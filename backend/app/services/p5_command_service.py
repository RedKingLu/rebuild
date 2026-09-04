"""P5 全量真实构建/运行/测试/静态检查命令服务（R12-3-C5）。

落实 D-105① 用户裁决：P5 最小验证必须全量包含真实构建、运行/启动、测试、静态/语法检查。

两大服务：
  P5CommandDetectionService：识别/配置构建·运行/启动·测试·静态检查命令
    - 基于工作区文件（Makefile / pom.xml / package.json / go.mod / Cargo.toml 等）自动识别
    - 无法识别 → 返回 needs_user_input 提示（不伪造、不跳过）

  P5CommandExecutionService：经 ExecutionProvider 真实执行
    - 调用 get_execution_provider() → provider.execute()
    - 写入 Evidence / Trace / Audit
    - 命令失败 → validation_failed（不等于 completed）
    - L4/L5 高风险 → Gate 拦截

诚实原则（D-105①）：
  - 命令不可识别 → needs_user_input / evidence_gap（不标 completed）
  - 命令执行失败 → validation_failed（不等于 completed）
  - "无命令" ≠ 通过
  - Auto 模式不可绕过

本环节只新增命令服务 + 更新验证服务 + 更新 RealP5Handler + 单测。
**不创建 P5→P6 Gate**（C6 起），**不改前端**（C7 起）。
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.services.workspace_service import (
    workspace_path,
    resolve_default_remote_host_id,
    BindingStatus,
)
from app.services.execution_provider import get_execution_provider
from app.services.p5_validation_plan import P5SlotStatus

logger = logging.getLogger(__name__)


# ── DTOs ──────────────────────────────────────────────────────────────────

@dataclass
class P5DetectedCommands:
    """项目构建/运行/测试/静态检查命令识别结果。"""
    project_id: str
    # 识别到的命令（None = 不可识别）
    build_cmd: Optional[str] = None
    run_cmd: Optional[str] = None
    test_cmd: Optional[str] = None
    static_check_cmd: Optional[str] = None
    # 不可识别的原因 → needs_user_input 提示
    needs_user_input: list[dict] = field(default_factory=list)
    # 项目类型推断
    project_type: Optional[str] = None
    # 诚实状态
    all_identified: bool = False
    # R17.5-P5-R2 (GAP-P5-3, capability-first)：.NET SDK 探测 + 维度能力提示（非门禁）
    dotnet_sdk_present: Optional[bool] = None
    capability_notes: list[dict] = field(default_factory=list)
    # ── R19-1 G1（修 B1/B2/B3）─────────────────────────────────────────────
    # execution_channel：命令由哪个【执行通道】提供工具链。
    #   "host_sdk"            宿主已装 SDK（旧行为，保持不变）
    #   "toolchain_container" 宿主无 SDK 但工具链容器通道可用（官方 SDK 镜像已在本地）
    #   None                  两者皆无 → 上游诚实 needs_user_input / evidence_gap（不伪造）
    execution_channel: Optional[str] = None
    # 工具链探测事实（toolchain_resolver.probe_toolchain 原样带出：image_ref/digest/挂载/上限）
    toolchain: dict = field(default_factory=dict)
    # 多阶段命令（修 B2）：单条 `dotnet build` 在 restore 失败即中止 ⇒ 拆
    # `restore` + `build --no-restore` 两阶段，合并判定为同一个槽（Q-R19-1-5 建议 A）。
    #   slot_id → [{"stage": "restore", "command": "..."}, ...]
    slot_stages: dict = field(default_factory=dict)
    # 命令运行目录（宿主绝对路径）。None = 工作区根（旧行为）。
    # 修 B3：`output_code` 下无 `.sln`，在工作区根跑 `dotnet build` 会报 MSB1003 伪失败 ⇒
    # 以【构建根 + 显式项目路径】为输入。
    command_cwd: Optional[str] = None


@dataclass
class P5CommandResult:
    """单个命令执行结果。"""
    slot_id: str
    command: str
    executed: bool = False
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    elapsed_ms: int = 0
    blocked: bool = False
    risk_level: str = "L0"
    # 验证结果
    passed: bool = False  # exit_code == 0
    status: str = ""     # P5SlotStatus value
    failure_reason: Optional[str] = None
    # Gate 拦截
    gate_required: bool = False
    gate_reason: Optional[str] = None
    # ── R19-1 G1（修 B4：真实诊断必须能一路走到报告）───────────────────────
    # 结构化诊断明细（NU*/CS*/MSB*/NETSDK*，类别如实不合并 —— 见 build_diagnostics 红线）
    diagnostics: list = field(default_factory=list)
    diagnostics_summary: dict = field(default_factory=dict)
    # 多阶段（restore + build）逐阶段明细
    stages: list = field(default_factory=list)
    # 执行环境事实（provider / 镜像引用 + digest / 挂载清单 / 硬化档自述）
    execution: dict = field(default_factory=dict)
    # 环境不可用（Docker/镜像/SDK 缺失）≠ 构建失败 ⇒ 上游落诚实 evidence_gap
    toolchain_unavailable: bool = False


# ── 命令检测 ───────────────────────────────────────────────────────────────

def _toolchain_container_enabled() -> bool:
    """工具链容器通道开关（R19-1）。默认开；关掉即回到"诚实 needs_user_input"的旧行为。

    读取顺序：环境变量 `REBUILD_TOOLCHAIN_CONTAINER_ENABLED` > `Settings` 默认值。
    不硬编码端口/模型/endpoint；只是一个布尔 kill switch。
    """
    raw = os.environ.get("REBUILD_TOOLCHAIN_CONTAINER_ENABLED")
    if raw is not None:
        return raw.strip().lower() not in ("0", "false", "no", "off", "")
    try:
        from app.core.config import settings
        return bool(settings.toolchain_container_enabled)
    except Exception as e:
        logger.warning("读取 toolchain_container_enabled 失败，按启用处理：%s", e)
        return True


def _toolchain_cache_dir() -> Path:
    """包缓存目录（**不复用宿主 ~/.nuget**，避免带私有源凭据的宿主配置进容器）。"""
    raw = os.environ.get("REBUILD_TOOLCHAIN_CACHE_DIR")
    if not raw:
        try:
            from app.core.config import settings
            raw = settings.toolchain_cache_dir
        except Exception as e:
            logger.warning("读取 toolchain_cache_dir 失败，回退工作目录 .data：%s", e)
            raw = "./.data/toolchain-cache"
    return Path(raw).expanduser()


# 项目类型 → 命令映射（按检测优先级排序）
# 注意：不含 2>/dev/null（命中 /dev/ DENY → L5 阻断）和 || true（掩盖非零退出码）
# R12-4-06：去除 || true 与 2>/dev/null，使退出码真实反映成败
_PROJECT_COMMAND_RULES = [
    # (marker_files, project_type, build_cmd, run_cmd, test_cmd, static_check)
    (["pom.xml"], "java/maven", "mvn compile -q", "mvn exec:java", "mvn test -q", "mvn checkstyle:check -q"),
    (["build.gradle", "gradlew"], "java/gradle", "./gradlew compileJava -q", "", "./gradlew test -q", "./gradlew checkstyleMain -q"),
    (["package.json"], "node/npm", "npm run build", "node index.js", "npm test", "npx eslint ."),
    (["go.mod"], "go", "go build ./...", "go run .", "go test ./...", "go vet ./..."),
    (["Cargo.toml"], "rust", "cargo build", "cargo run", "cargo test", "cargo clippy -- -D warnings"),
    (["Makefile", "makefile"], "make", "make build || make all || make", "", "make test", ""),
    (["pyproject.toml", "setup.py", "requirements.txt"], "python", "python3 -m compileall -q .", "", "python3 -m pytest -x -q || python3 -m unittest discover -q", "python3 -m compileall -q ."),
    (["CMakeLists.txt"], "cpp/cmake", "cmake --build build || make", "", "ctest --test-dir build || make test", ""),
    # .NET / 信创迁移目标（R12-12 修复：非 echo 假 pass）
    ([".sln", ".csproj", ".vbproj"], "dotnet", "", "", "", ""),  # 本机通常无 msbuild，需 via Gate 申报
    (["web.config", "global.asax"], "dotnet/legacy", "", "", "", ""),  # 同上，不 echo→validated
]


def _match_markers(all_files: set[str], markers: list[str]) -> bool:
    """R12-12：匹配标记文件。

    支持两种语义：
    - 精确文件名（如 "pom.xml", "go.mod"）→ 直接 in all_files
    - 扩展名（如 ".sln", ".csproj"）→ 任一文件名 endswith 该扩展名
    """
    for m in markers:
        m_lower = m.lower()
        if m_lower.startswith("."):
            # 扩展名模式：.sln / .csproj / .vbproj
            if any(f.endswith(m_lower) for f in all_files):
                return True
        else:
            # 精确文件名模式
            if m_lower in all_files:
                return True
    return False


class P5CommandDetectionService:
    """识别项目构建/运行/测试/静态检查命令。R12-3-C5 新建。"""

    # ── R19-1 G1：工具链容器通道的命令模板（修 B2/B3）──────────────────────
    # 与既有 `_PROJECT_COMMAND_RULES` 同层级：命令模板属【工具链层】通用事实，
    # 而【样本实例值】（目标框架 / 镜像名 / 项目路径）一律来自 yaml 映射与项目文件解析，
    # Python 代码内不出现（AGENTS §10-26）。
    #   `{proj}`  相对构建根的项目路径（显式传入 → 杜绝 MSB1003 伪失败）
    #   `{build}` 容器内唯一写入位（来自 toolchain_images.yaml container_paths.build）
    #   `{cfg}`   平台自管的无凭据包源配置（写在 {build} 下）
    #   `{slug}`  项目路径派生的输出子目录名（避免多项目互相覆盖）
    _DOTNET_STAGE_TEMPLATES = {
        # restore 单独一阶段 → 捕获依赖类错误（NU*）
        "restore": ("dotnet restore {proj} --configfile {cfg} "
                    "-p:BaseIntermediateOutputPath={build}/obj/{slug}/"),
        # --no-restore 跳过 restore 直接进编译 → 捕获编译类错误（CS*）
        "build": ("dotnet build {proj} --no-restore -warnaserror "
                  "-p:BaseIntermediateOutputPath={build}/obj/{slug}/ "
                  "-p:BaseOutputPath={build}/bin/{slug}/"),
        "test": ("dotnet test {proj} --nologo "
                 "-p:BaseIntermediateOutputPath={build}/obj/{slug}/ "
                 "-p:BaseOutputPath={build}/bin/{slug}/"),
        "static_check": "dotnet format {proj} --verify-no-changes",
    }

    @staticmethod
    def _slug(rel_path: str) -> str:
        from pathlib import PurePosixPath
        p = PurePosixPath(rel_path.replace("\\", "/"))
        return str(p.with_suffix("")).replace("/", "__") or "proj"

    def _dotnet_container_commands(self, result: "P5DetectedCommands", probe: dict) -> None:
        """据真实探测事实生成工具链容器通道的分阶段命令（修 B1/B2/B3）。"""
        c_build = (probe.get("container_paths") or {}).get("build") or "/build"
        cfg_name = (probe.get("package_config") or {}).get("filename") or ""
        cfg_path = f"{c_build}/{cfg_name}" if cfg_name else ""
        # 有 .sln 时以解决方案为单一构建对象；否则对发现到的每个工程逐个执行（无 .sln 时
        # 在构建根跑裸 `dotnet build` 会报 MSB1003 —— 这正是 B3）。
        solutions = probe.get("solutions") or []
        projects = probe.get("projects") or []
        if solutions:
            targets = [{"path": solutions[0], "target_frameworks": [], "is_test": False}]
        else:
            targets = projects

        def render(stage: str, rel: str) -> str:
            return self._DOTNET_STAGE_TEMPLATES[stage].format(
                proj=rel, build=c_build, cfg=cfg_path, slug=self._slug(rel))

        # build 槽 = 全部 restore 阶段 + 全部 build 阶段（先收依赖错误，再收编译错误）
        build_stages = [{"stage": f"restore:{t['path']}", "command": render("restore", t["path"])}
                        for t in targets]
        build_stages += [{"stage": f"build:{t['path']}", "command": render("build", t["path"])}
                         for t in targets]
        result.slot_stages["build_verified"] = build_stages
        result.build_cmd = build_stages[-1]["command"] if build_stages else None

        # test 槽：只对真实标记为测试工程的项目执行（项目事实，非猜测）
        test_targets = [t for t in projects if t.get("is_test")] or (
            targets if solutions else [])
        if test_targets:
            test_stages = [{"stage": f"test:{t['path']}", "command": render("test", t["path"])}
                           for t in test_targets]
            result.slot_stages["tests_pass"] = test_stages
            result.test_cmd = test_stages[-1]["command"]

        static_stages = [{"stage": f"format:{t['path']}",
                          "command": render("static_check", t["path"])} for t in targets]
        if static_stages:
            result.slot_stages["static_check"] = static_stages
            result.static_check_cmd = static_stages[-1]["command"]

    def _resolve_dotnet_commands(self, result: "P5DetectedCommands",
                                 workspace: Optional[Path] = None) -> None:
        """.NET build/test/static 命令补全 —— 改为【执行通道能力感知】（R19-1 修 B1）。

        R17.5-P5-R2 的旧口径是"宿主有没有 `dotnet`"（`shutil.which`）：宿主无 SDK ⇒
        `build_cmd=None` ⇒ 上游落 `needs_user_input`。这与 R19-1-03 要求的**真实
        `validation_failed`** 直接冲突 —— 平台明明有可用的容器执行通道，却把"我这台机器没装
        SDK"表述成"需要用户输入"。

        新口径：`build_cmd` 的存在性 = **当前执行通道能否提供该工具链**：
          ① 宿主有 SDK           → 保持旧行为，命令一字不变（不改已验收的既有路径）
          ② 宿主无 SDK 但工具链容器通道可用（Docker 可达 + 官方 SDK 镜像已在本地 + 构建对象
             已发现 + 目标框架有映射）→ 生成容器内分阶段真实命令
          ③ 两者皆无             → 仍诚实 None（needs_user_input / evidence_gap），**不伪造**
        """
        if result.project_type not in ("dotnet", "dotnet/legacy"):
            return
        if shutil.which("dotnet"):
            # 真实命令（-warnaserror 让静态期告警反映到退出码；--verify-no-changes 不改盘）
            result.build_cmd = result.build_cmd or "dotnet build -warnaserror"
            result.test_cmd = result.test_cmd or "dotnet test --nologo"
            result.static_check_cmd = (result.static_check_cmd
                                       or "dotnet format --verify-no-changes")
            result.dotnet_sdk_present = True
            result.execution_channel = "host_sdk"
            return

        result.dotnet_sdk_present = False
        probe: dict = {}
        if _toolchain_container_enabled() and workspace is not None:
            try:
                from app.services.toolchain_resolver import probe_toolchain, DOTNET
                probe = probe_toolchain(workspace, DOTNET)
            except Exception as e:
                # 公理 3：探测异常发声，但绝不因此伪造命令可用。
                logger.warning("P5 工具链容器探测失败（诚实降级，非阻断）：%s", e, exc_info=True)
                probe = {"available": False, "reason": f"探测异常：{type(e).__name__}: {e}"}
        else:
            probe = {"available": False,
                     "reason": ("工具链容器通道未启用（REBUILD_TOOLCHAIN_CONTAINER_ENABLED=0）"
                                if workspace is not None else "工作区不可用")}
        result.toolchain = probe

        if probe.get("available"):
            result.execution_channel = "toolchain_container"
            result.command_cwd = probe.get("build_root") or None
            self._dotnet_container_commands(result, probe)
            result.capability_notes.append({
                "dimension": "dotnet_build",
                "capability_ready": True,
                "detail": (f"宿主无 dotnet SDK，改由工具链容器通道真实执行："
                           f"镜像 {probe.get('image_ref')}"
                           f"（digest {(probe.get('probe') or {}).get('image_digest') or '未知'}）；"
                           "构建输入以 ro 挂载、输出重定向到独立可写位。退出码与诊断均来自容器"
                           "真实输出，非伪造。"),
            })
        else:
            result.capability_notes.append({
                "dimension": "dotnet_build",
                "capability_ready": True,
                "detail": ("本地无 dotnet SDK，且工具链容器通道不可用："
                           f"{probe.get('reason') or '原因未知'}。"
                           ".NET build/test/static 能力已接线（工具链容器通道 + SDK-aware 命令补全 + "
                           "可绑定远程执行主机），待具备 SDK / 镜像 / 远程主机环境真验；"
                           "当前诚实 needs_user_input，非伪造通过。"),
            })

    def detect_commands(self, project_id: str) -> P5DetectedCommands:
        """扫描工作区，识别构建/运行/测试/静态检查命令。

        R12-4-01 修复：递归扫描工作区（含子目录），不再只看顶层。
        工作区根通常是目录（source/、output_code/…），构建标记嵌套在子目录中。
        """
        ws = workspace_path(project_id)
        result = P5DetectedCommands(project_id=project_id)

        if not ws.exists():
            result.needs_user_input.append({
                "slot": "workspace",
                "prompt": f"项目工作区 {project_id} 不存在",
            })
            return result

        # 递归收集所有文件名（不含 .rebuild 等元数据目录）
        try:
            all_files: set[str] = set()
            exclude_dirs = {'.rebuild', '.git', '__pycache__', 'node_modules', '.venv'}
            for root, dirs, files in __import__('os').walk(str(ws)):
                dirs[:] = [d for d in dirs if d not in exclude_dirs]
                for fname in files:
                    all_files.add(fname.lower())
        except Exception as e:
            logger.warning("P5 command detect: cannot walk workspace: %s", e, exc_info=True)
            result.needs_user_input.append({"slot": "workspace", "prompt": f"无法读取工作区：{e}"})
            return result

        # 匹配项目类型（R12-12：支持精确名 + 扩展名 endswith）
        matched = False
        for markers, ptype, build, run, test, static in _PROJECT_COMMAND_RULES:
            if _match_markers(all_files, markers):
                result.project_type = ptype
                result.build_cmd = build if build else None
                result.run_cmd = run if run else None
                result.test_cmd = test if test else None
                result.static_check_cmd = static if static else None
                matched = True
                break

        # R17.5-P5-R2 (GAP-P5-3)：.NET 命令补全。R19-1 改为【执行通道能力感知】（修 B1）：
        # 宿主 SDK 在 → 旧命令一字不变；宿主无 SDK 但工具链容器通道可用 → 容器内分阶段真实命令；
        # 两者皆无 → 仍诚实 None。
        self._resolve_dotnet_commands(result, workspace=ws)

        if not matched:
            result.needs_user_input.append({
                "slot": "project_type",
                "prompt": f"无法识别项目类型（工作区文件：{sorted(all_files)[:10]}）",
            })

        # 对未识别的槽位生成 needs_user_input 提示
        if not result.build_cmd:
            result.needs_user_input.append({"slot": "build",
                                            "prompt": "无法自动识别构建命令，请提供（如 make / mvn compile / go build）"})
        if not result.run_cmd:
            result.needs_user_input.append({"slot": "run",
                                            "prompt": "无法自动识别运行/启动命令，请提供"})
        if not result.test_cmd:
            result.needs_user_input.append({"slot": "test",
                                            "prompt": "无法自动识别测试命令，请提供（如 make test / pytest / mvn test）"})
        if not result.static_check_cmd:
            result.needs_user_input.append({"slot": "static_check",
                                            "prompt": "无法自动识别静态/语法检查命令，请提供"})

        result.all_identified = (result.build_cmd is not None and
                                 result.test_cmd is not None)
        return result


# ── 命令执行 ───────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class P5CommandExecutionService:
    """经 ExecutionProvider 真实执行 P5 验证命令。R12-3-C5 新建。

    R12-8 修复 B-P5-CONTAINER-CWD：ContainerExecutionProvider 无 cwd 参数且只读挂载，
    无法构建真实工作区。改用 WorkspaceLocalExecutionProvider（workspace_local 模式）：
    工作区本地执行 + DENY 子串安检 + 无 ALLOWED_COMMANDS 白名单 + 支持 cwd。
    """

    def __init__(self, tracer=None, auditor=None, mode: str | None = None):
        self.tracer = tracer
        self.auditor = auditor
        # 默认 workspace_local（R12-8 修复 B-P5-CONTAINER-CWD）
        self._mode = mode or os.environ.get("P5_EXECUTION_MODE", "workspace_local")
        # R19-1：同一轮验证的所有阶段共用一个构建目录（restore 写的资产文件要给 build 读）。
        self._build_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # ── R19-1：工具链容器通道的 provider 选择与选项组装 ─────────────────────

    def _toolchain_provider_options(self, project_id: str, commands: "P5DetectedCommands") -> dict:
        """据真实探测事实组装 provider 选项。挂载只 3 处，绝不含 docker.sock。"""
        probe = commands.toolchain or {}
        c_paths = probe.get("container_paths") or {}
        cache_leaf = (c_paths.get("package_cache") or "/cache").strip("/") or "cache"
        ws = workspace_path(project_id)
        return {
            "image_ref": probe.get("image_ref") or "",
            "image_digest": (probe.get("probe") or {}).get("image_digest") or "",
            "src_dir": probe.get("build_root") or str(ws),
            # 唯一写入位：与 output_code 分离，保持产物卫生（R19-3-01 的机制保障）
            "build_dir": str(ws / ".rebuild" / "build" / self._build_id),
            "package_cache_dir": str(_toolchain_cache_dir() / cache_leaf),
            "container_paths": c_paths,
            "container_env": probe.get("container_env") or {},
            "package_config": probe.get("package_config") or {},
            "limits": probe.get("limits") or {},
            "network_mode": probe.get("network_mode") or "none",
            "network_weakened": bool(probe.get("network_weakened")),
            "network_note": probe.get("network_note") or "",
        }

    def _resolve_provider(self, project_id: str, commands: "P5DetectedCommands | None"):
        """返回 (provider, cwd, stage_timeout_or_None)。异常向上抛，由调用侧诚实归因。"""
        env_mode = self._mode or os.environ.get("EXECUTION_MODE", "local")
        remote_host_id = resolve_default_remote_host_id(project_id)
        ws = workspace_path(project_id)

        # 远程绑定优先（R14-4 既有行为，不改）
        if env_mode == "remote" or remote_host_id:
            from app.core.database import get_session
            with get_session() as db:
                return (get_execution_provider(mode="remote", remote_host_id=remote_host_id,
                                               db=db),
                        str(ws), None)

        # 工具链容器通道：仅当检测层实测确认可用时启用（宿主无 SDK 的那一档）
        if commands is not None and commands.execution_channel == "toolchain_container":
            opts = self._toolchain_provider_options(project_id, commands)
            provider = get_execution_provider(mode="toolchain_container", provider_options=opts)
            limits = (commands.toolchain or {}).get("limits") or {}
            stage_timeout = int(limits.get("timeout_s") or 0) or None
            return provider, (commands.command_cwd or str(ws)), stage_timeout

        return get_execution_provider(mode=env_mode), str(ws), None

    def execute_slot_command(self, project_id: str, slot_id: str, command: str,
                              timeout: int = 120,
                              commands: "P5DetectedCommands | None" = None) -> P5CommandResult:
        """执行单个槽位命令，返回执行结果 + 验证状态。

        R14-4: 当 Workspace 绑定了默认 remote 环境时，自动分派到
        get_execution_provider(mode="remote", remote_host_id, db)。
        R19-1: 当检测层实测确认工具链容器通道可用时，分派到 toolchain_container 档。
        """
        result = P5CommandResult(slot_id=slot_id, command=command)
        env_mode = self._mode or os.environ.get("EXECUTION_MODE", "local")

        # R14-4: resolve default remote host for this workspace (G3 fix).
        remote_host_id = resolve_default_remote_host_id(project_id)

        try:
            provider, cwd, stage_timeout = self._resolve_provider(project_id, commands)
        except Exception as e:
            result.failure_reason = f"ExecutionProvider 初始化失败：{e}"
            result.status = "validation_failed"
            return result

        eff_timeout = stage_timeout or timeout
        start = time.monotonic()
        try:
            exec_result = self._run_in_loop(provider.execute(
                command, language="bash", timeout=eff_timeout, cwd=cwd),
                wait_timeout=eff_timeout + 60)
        except Exception as e:
            result.elapsed_ms = int((time.monotonic() - start) * 1000)
            result.failure_reason = f"命令执行异常：{type(e).__name__}: {e}"
            result.status = "validation_failed"
            return result

        elapsed = int((time.monotonic() - start) * 1000)
        result.executed = True
        result.elapsed_ms = elapsed
        result.exit_code = exec_result.get("exit_code", -1)
        result.stdout = exec_result.get("stdout", "")[:5000]  # cap
        result.stderr = exec_result.get("stderr", "")[:5000]
        result.blocked = exec_result.get("blocked", False)
        result.risk_level = exec_result.get("risk_level", "L0")
        # R19-1（修 B4）：从真实输出抽结构化诊断 + 记执行环境事实（镜像引用/digest/挂载/硬化档）。
        _attach_diagnostics(result, exec_result)

        # R14-4 WP4: persist RemoteInvocation when routed to a remote provider.
        if (env_mode == "remote" or remote_host_id) and exec_result.get("provider") == "remote_ssh":
            try:
                from app.core.database import get_session
                from app.services.invocation_service import record_invocation
                from app.services.workspace_service import get_environment_block
                _env_blk = get_environment_block(project_id)
                _binding_id = _env_blk.get("default_binding_id")
                _digest = __import__("hashlib").sha256(command.encode()).hexdigest()[:16]
                # R14-6 (B-R14-INV-REF-1): write a real audit + trace entry for the
                # remote execution and back-link them onto the invocation, so
                # audit_ref / trace_ref are no longer dead columns. Done before the
                # L4/L5 early-return below so high-risk invocations are linked too.
                _audit_ref = None
                if self.auditor:
                    _a = self.auditor.write(
                        "remote_invocation",
                        risk_level=result.risk_level,
                        action=f"p5_remote_exec_{slot_id}",
                        decision="blocked" if result.blocked else "executed",
                        reason=f"P5 {slot_id} remote_ssh exit={result.exit_code}",
                        project_id=project_id, stage="p5",
                        transition_mode="real",
                        command_digest=_digest,
                    )
                    _audit_ref = _a.get("audit_id") if isinstance(_a, dict) else getattr(_a, "audit_id", None)
                _trace_ref = None
                if self.tracer:
                    _t = self.tracer.write(
                        "evidence_event",
                        action=f"p5_remote_invocation_{slot_id}",
                        summary=(f"P5 {slot_id} remote_ssh: exit={result.exit_code}, "
                                 f"{elapsed}ms, risk={result.risk_level}"),
                        project_id=project_id, stage="p5",
                        transition_mode="real",
                        extras={"slot_id": slot_id, "command_digest": _digest,
                                "exit_code": result.exit_code, "risk_level": result.risk_level},
                    )
                    _trace_ref = _t.get("trace_id") if isinstance(_t, dict) else getattr(_t, "trace_id", None)
                with get_session() as _db:
                    _inv = record_invocation(
                        db=_db,
                        workspace_id=project_id,
                        binding_id=_binding_id,
                        provider="remote_ssh",
                        command_digest=_digest,
                        exit_code=result.exit_code,
                        risk_level=result.risk_level,
                        elapsed_ms=elapsed,
                        stdout=result.stdout,
                        stderr=result.stderr,
                        trigger="p_stage",
                        audit_ref=_audit_ref,
                        trace_ref=_trace_ref,
                    )
                    result.invocation_id = _inv.invocation_id
                    result.audit_ref = _inv.audit_ref
            except Exception as e:
                logger.warning("RemoteInvocation record failed (non-fatal): %s", e)

        # L4/L5 高风险 → Gate 拦截
        if result.risk_level in ("L4", "L5"):
            result.gate_required = True
            result.gate_reason = f"命令风险级别 {result.risk_level}（{command}）"
            result.status = "needs_user_input"
            return result

        # R19-1：环境不可用（Docker 守护/镜像/SDK 缺失）≠ 构建失败 ⇒ 诚实 evidence_gap，
        # 绝不伪造 available/validated（§10-20 / D-097 / R19-1-04）。
        if result.toolchain_unavailable:
            result.passed = False
            result.status = P5SlotStatus.EVIDENCE_GAP
            result.failure_reason = (result.stderr or "工具链执行通道不可用")[:1000]
            return result

        # 验证判定
        result.passed = (result.exit_code == 0 and not result.blocked)
        result.status = "validated" if result.passed else "validation_failed"
        if not result.passed and not result.failure_reason:
            result.failure_reason = (f"命令退出码 {result.exit_code}"
                                     + (f"，被安全策略拦截" if result.blocked else ""))

        # Trace 写入
        if self.tracer:
            self.tracer.write(
                "evidence_event",
                action=f"p5_execute_{slot_id}",
                summary=(f"P5 {slot_id}: {'通过' if result.passed else '失败'} "
                         f"(exit={result.exit_code}, {elapsed}ms, risk={result.risk_level})"),
                project_id=project_id,
                stage="p5",
                transition_mode="real",
                extras={"slot_id": slot_id, "command": command, "passed": result.passed,
                        "exit_code": result.exit_code, "elapsed_ms": elapsed,
                        "risk_level": result.risk_level, "blocked": result.blocked,
                        "diagnostics_summary": result.diagnostics_summary},
            )
        return result

    def execute_slot_stages(self, project_id: str, slot_id: str, stages: list,
                            commands: "P5DetectedCommands", timeout: int = 120) -> P5CommandResult:
        """R19-1（修 B2）：多阶段命令合并为**同一个槽**的单一判定（Q-R19-1-5 方案 A）。

        为什么要拆阶段：单条 `dotnet build` 在 restore 失败（NU1101）时即中止，编译根本不发生
        ⇒ 永远拿不到 CS 类明细。拆成 `restore`（收依赖类错误）+ `build --no-restore`
        （跳过 restore 直接编译，收编译类错误）后，**同一份报告里可同时容纳两类诊断**。
        阶段【不早停】：restore 失败也继续跑 build，否则等于回到单条命令的老问题。

        判定：任一阶段退出码非零 → `validation_failed`；任一阶段报环境不可用 → `evidence_gap`。
        阶段明细进 `stages[]`，诊断跨阶段汇总（类别如实、不合并 —— 见 build_diagnostics 红线）。
        """
        merged = P5CommandResult(slot_id=slot_id, command="")
        stage_details: list[dict] = []
        all_stdout: list[str] = []
        all_stderr: list[str] = []
        first_nonzero: Optional[int] = None
        unavailable = False

        for st in stages:
            cmd = st.get("command") or ""
            one = self.execute_slot_command(project_id, f"{slot_id}:{st.get('stage')}",
                                            cmd, timeout, commands=commands)
            merged.elapsed_ms += one.elapsed_ms
            merged.executed = merged.executed or one.executed
            merged.risk_level = one.risk_level or merged.risk_level
            merged.blocked = merged.blocked or one.blocked
            if one.gate_required and not merged.gate_required:
                merged.gate_required = True
                merged.gate_reason = one.gate_reason
            if one.execution and not merged.execution:
                merged.execution = one.execution
            if one.toolchain_unavailable:
                unavailable = True
            if one.exit_code not in (0, None) and first_nonzero is None:
                first_nonzero = one.exit_code
            all_stdout.append(f"$ {cmd}\n{one.stdout}")
            all_stderr.append(one.stderr)
            merged.diagnostics.extend(one.diagnostics)
            stage_details.append({
                "stage": st.get("stage"),
                "command": cmd,
                "exit_code": one.exit_code,
                "elapsed_ms": one.elapsed_ms,
                "status": one.status,
                "diagnostics_summary": one.diagnostics_summary,
                "stdout_tail": one.stdout,
                "stderr_tail": one.stderr,
            })

        from app.services.build_diagnostics import summarize_diagnostics, tail
        # 跨阶段去重（同一条错误可能在 restore 与 build 两阶段各出现一次）
        seen: set = set()
        deduped = []
        for d in merged.diagnostics:
            key = (d["code"], d.get("file"), d.get("line"), d.get("column"), d.get("message"))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(d)
        merged.diagnostics = deduped
        merged.diagnostics_summary = summarize_diagnostics(deduped)
        merged.stages = stage_details
        # 展示用摘要。**刻意不用 `&&` 拼接** —— 阶段是逐个独立执行且【不早停】的
        # （restore 失败也继续跑 build），写成 `&&` 会误导成 shell 短路语义。
        # 真实逐条命令与各自退出码见 stages[]。
        merged.command = (f"{len(stages)} 阶段："
                          + "；".join(st.get("command") or "" for st in stages))
        merged.stdout = tail("\n".join(all_stdout))
        merged.stderr = tail("\n".join(all_stderr))
        merged.exit_code = first_nonzero if first_nonzero is not None else 0

        if merged.gate_required:
            merged.status = P5SlotStatus.NEEDS_USER_INPUT
            return merged
        if unavailable:
            merged.passed = False
            merged.status = P5SlotStatus.EVIDENCE_GAP
            merged.failure_reason = (merged.stderr or "工具链执行通道不可用")[:1000]
            return merged
        merged.passed = (merged.exit_code == 0 and not merged.blocked)
        merged.status = "validated" if merged.passed else "validation_failed"
        if not merged.passed:
            codes = ", ".join(sorted(merged.diagnostics_summary.get("by_code", {}))) or "无可解析诊断码"
            merged.failure_reason = (f"{len(stages)} 阶段构建：退出码 {merged.exit_code}；"
                                     f"诊断码 {codes}")
        if self.tracer:
            self.tracer.write(
                "evidence_event", action=f"p5_execute_{slot_id}",
                summary=(f"P5 {slot_id}: {'通过' if merged.passed else '失败'} "
                         f"({len(stages)} 阶段, exit={merged.exit_code}, {merged.elapsed_ms}ms)"),
                project_id=project_id, stage="p5", transition_mode="real",
                extras={"slot_id": slot_id, "stages": [s["stage"] for s in stage_details],
                        "exit_code": merged.exit_code,
                        "diagnostics_summary": merged.diagnostics_summary},
            )
        return merged

    def execute_all_conditional(self, project_id: str,
                                 commands: P5DetectedCommands,
                                 timeout: int = 120) -> list:
        """执行全部有条件必需槽位命令。

        R12-14：命令-less 工程的 run_verified 置 NOT_APPLICABLE（库类工程无入口命令），
        避免因缺少 run 命令永久阻塞 P5 完成。
        其余不可识别槽位 → needs_user_input。
        R19-1：槽位若有多阶段命令（restore + build）→ 走 execute_slot_stages 合并判定。
        """
        results = []
        slot_cmd_pairs = [
            ("build_verified", commands.build_cmd),
            ("run_verified", commands.run_cmd),
            ("tests_pass", commands.test_cmd),
            ("static_check", commands.static_check_cmd),
        ]
        for slot_id, cmd in slot_cmd_pairs:
            stages = (commands.slot_stages or {}).get(slot_id)
            if stages:
                results.append(self.execute_slot_stages(project_id, slot_id, stages,
                                                        commands, timeout))
            elif cmd is None:
                if slot_id == "run_verified":
                    # 库类工程无入口命令 → NOT_APPLICABLE（经 Audit 记录）
                    results.append(P5CommandResult(
                        slot_id=slot_id, command="",
                        status=P5SlotStatus.NOT_APPLICABLE,
                        failure_reason="库类工程无入口命令，不适用运行验证",
                    ))
                    if self.auditor:
                        self.auditor.write(
                            "risk_acceptance", action="p5_run_not_applicable",
                            decision="accepted", risk_level="L1",
                            project_id=project_id, stage="p5",
                            reason="库类工程无入口命令，run_verified 不适用",
                        )
                else:
                    # R19-1-04 诚实降级的口径区分：
                    #   ① 工具链已识别、但【环境不具备】（Docker 守护不可达 / SDK 镜像不在本地 /
                    #      目标框架无映射）→ `evidence_gap` + 具体原因（这是"能力已接线、待环境
                    #      真验"，属 capability-first 词表，绝不是 available/validated）。
                    #   ② 连项目类型/命令都识别不出来 → 仍 `needs_user_input`（旧行为不变）。
                    # 两者都不放宽门禁：`can_mark_completed` 对 evidence_gap 仍要求用户 Gate
                    # 明确接受风险才算过，未接受一律阻塞。
                    probe = commands.toolchain or {}
                    if probe and not probe.get("available"):
                        reason = probe.get("reason") or "工具链执行环境不具备"
                        results.append(P5CommandResult(
                            slot_id=slot_id, command="",
                            status=P5SlotStatus.EVIDENCE_GAP,
                            failure_reason=f"工具链执行环境不具备：{reason}",
                            execution={"provider": "", "execution_mode": "",
                                       "toolchain_probe": probe.get("probe") or {},
                                       "image_ref": probe.get("image_ref") or ""},
                            toolchain_unavailable=True,
                        ))
                    else:
                        results.append(P5CommandResult(
                            slot_id=slot_id, command="",
                            status=P5SlotStatus.NEEDS_USER_INPUT,
                            failure_reason="命令不可识别（项目类型未知）",
                        ))
            else:
                results.append(self.execute_slot_command(project_id, slot_id, cmd, timeout,
                                                         commands=commands))
        return results


# ── helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _run_in_loop(coro, wait_timeout: int = 180):
        """Run an async coroutine to completion (handles nested loop contexts)."""
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            # Nested loop (e.g. StageLoop): use a fresh thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                # R19-1：容器构建（restore 冷缓存 + 多阶段）可能远超旧的 180s 硬上限 ⇒
                # 由调用侧按阶段超时给出等待上限；否则 TimeoutError 会被归因成"命令执行异常"
                # 的伪失败。
                return future.result(timeout=wait_timeout)
        return asyncio.run(coro)


def _attach_diagnostics(result: "P5CommandResult", exec_result: dict) -> None:
    """R19-1（修 B4）：把真实输出抽成结构化诊断 + 记执行环境事实。

    这里是"真实错误文本能不能走到报告"的关键接点：旧链路只保留 500 字符尾巴且
    `slot_to_dict` 不序列化 details ⇒ NU1101/CS 明细根本到不了 p5_validation_report.json。
    """
    from app.services.build_diagnostics import (
        parse_diagnostics, summarize_diagnostics, tail,
    )
    stdout_raw = exec_result.get("stdout", "") or ""
    stderr_raw = exec_result.get("stderr", "") or ""
    result.diagnostics = parse_diagnostics(stdout_raw, stderr_raw)
    result.diagnostics_summary = summarize_diagnostics(result.diagnostics)
    result.stdout = tail(stdout_raw)
    result.stderr = tail(stderr_raw)
    result.toolchain_unavailable = bool(exec_result.get("toolchain_unavailable"))
    execution = {
        "provider": exec_result.get("provider", ""),
        "execution_mode": exec_result.get("execution_mode", ""),
        "risk_level": exec_result.get("risk_level", ""),
    }
    for key in ("image_ref", "image_digest", "container_workdir", "mounts", "hardening",
                "cwd_mismatch"):
        if key in exec_result:
            execution[key] = exec_result[key]
    result.execution = execution
