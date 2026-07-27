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


# ── 命令检测 ───────────────────────────────────────────────────────────────

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

    def _resolve_dotnet_commands(self, result: "P5DetectedCommands") -> None:
        """R17.5-P5-R2 (GAP-P5-3, capability-first)：.NET build/test/static 命令 SDK-aware 补全。

        R12-12 因"本机通常无 msbuild"故意留空（防 echo 假 pass）。capability-first：探测 dotnet
        CLI——【有 SDK】则填真实命令（dotnet build/test/format，经 ExecutionProvider 产真实退出码
        事实，非 echo 伪造）；【无 SDK】保持 None → 上游诚实 needs_user_input（阻断，不伪造通过），
        并记 capability_ready 提示（能力已接线，待 SDK / 远程执行主机环境真验）。
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
        else:
            result.dotnet_sdk_present = False
            result.capability_notes.append({
                "dimension": "dotnet_build",
                "capability_ready": True,
                "detail": ("本地无 dotnet SDK：.NET build/test/static 能力已接线（SDK-aware 命令补全 + "
                           "可绑定远程执行主机），待具备 SDK / 远程主机环境真验；当前诚实 "
                           "needs_user_input，非伪造通过。"),
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

        # R17.5-P5-R2 (GAP-P5-3)：.NET 命令 SDK-aware 补全（SDK 在则真实命令；不在则诚实待环境真验）
        self._resolve_dotnet_commands(result)

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

    def execute_slot_command(self, project_id: str, slot_id: str, command: str,
                              timeout: int = 120) -> P5CommandResult:
        """执行单个槽位命令，返回执行结果 + 验证状态。

        R14-4: 当 Workspace 绑定了默认 remote 环境时，自动分派到
        get_execution_provider(mode="remote", remote_host_id, db)。
        """
        result = P5CommandResult(slot_id=slot_id, command=command)
        env_mode = self._mode or os.environ.get("EXECUTION_MODE", "local")

        # R14-4: resolve default remote host for this workspace (G3 fix).
        remote_host_id = resolve_default_remote_host_id(project_id)

        try:
            if env_mode == "remote" or remote_host_id:
                # Workspace bound to a remote host → route to it.
                # remote_host_id may be None if binding resolves but DB lookup fails,
                # in which case the factory raises ValueError (caught below).
                from app.core.database import get_session
                with get_session() as db:
                    provider = get_execution_provider(
                        mode="remote", remote_host_id=remote_host_id, db=db,
                    )
            else:
                provider = get_execution_provider(mode=env_mode)
        except Exception as e:
            result.failure_reason = f"ExecutionProvider 初始化失败：{e}"
            result.status = "validation_failed"
            return result

        ws = workspace_path(project_id)
        start = time.monotonic()
        try:
            exec_result = self._run_in_loop(provider.execute(
                command, language="bash", timeout=timeout, cwd=str(ws)))
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
                        "risk_level": result.risk_level, "blocked": result.blocked},
            )
        return result

    def execute_all_conditional(self, project_id: str,
                                 commands: P5DetectedCommands,
                                 timeout: int = 120) -> list:
        """执行全部有条件必需槽位命令。

        R12-14：命令-less 工程的 run_verified 置 NOT_APPLICABLE（库类工程无入口命令），
        避免因缺少 run 命令永久阻塞 P5 完成。
        其余不可识别槽位 → needs_user_input。
        """
        results = []
        slot_cmd_pairs = [
            ("build_verified", commands.build_cmd),
            ("run_verified", commands.run_cmd),
            ("tests_pass", commands.test_cmd),
            ("static_check", commands.static_check_cmd),
        ]
        for slot_id, cmd in slot_cmd_pairs:
            if cmd is None:
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
                    # 其余不可识别 → needs_user_input
                    results.append(P5CommandResult(
                        slot_id=slot_id, command="",
                        status=P5SlotStatus.NEEDS_USER_INPUT,
                        failure_reason="命令不可识别（项目类型未知）",
                    ))
            else:
                results.append(self.execute_slot_command(project_id, slot_id, cmd, timeout))
        return results


# ── helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _run_in_loop(coro):
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
                return future.result(timeout=180)
        return asyncio.run(coro)
