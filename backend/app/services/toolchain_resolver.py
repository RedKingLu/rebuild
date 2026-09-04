"""工具链容器构建解析器（R19-1 G1 · 确定性采集层）。

职责（全部是【确定性采集】，无 LLM 推理——AGENTS §2.3 第 2 条）：
  ① 读 `backend/app/config/toolchain_images.yaml`（框架→镜像映射的唯一事实源）
  ② 从工作区发现构建根与构建对象（`.sln` / `.csproj`）并抽取 `<TargetFramework>`
  ③ 目标框架 → 官方 SDK 镜像引用；**无映射则不猜**，返回 None + 原因串
  ④ 三态探测：Docker 守护可达性 / 镜像是否已在本地（含 RepoDigest）/ 具备

红线：
  - 本模块**不出现**任何具体目标框架或镜像名字面量（AGENTS §10-26：样本实例值不得
    硬编码进平台代码）——一律从 yaml 读。
  - 不可得时给**诚实原因串**供上游落 `evidence_gap`，绝不伪造 available（D-097 / §10-20）。
  - **只读探测，不拉镜像**：拉取写宿主镜像库属 L4，由 `deploy/toolchain-images/pull.sh`
    显式前置执行（Q-R19-1-2 裁决 C：Gate 只作用于「镜像获取」，不作用于「构建执行」）。
  - 不碰 `/var/run/docker.sock` 挂载：Docker API 由宿主侧 backend 进程调用，容器内只跑构建。

关联：产物/草稿/R19-1-容器构建方案.md §2.1-2.4、Q-R19-1-2 / Q-R19-1-6
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("rebuild.toolchain_resolver")

# 默认工具链键（yaml `toolchains` 下的键名，不是框架字面量）
DOTNET = "dotnet"

# 构建对象发现时跳过的目录（构建中间产物 / 元数据，非构建输入）
_SKIP_DIRS = {".rebuild", ".git", "obj", "bin", "node_modules", "__pycache__", ".venv"}

# 工作区内优先作为构建根的子目录（平台产物约定，非样本实例值）
_BUILD_ROOT_CANDIDATES = ("output_code",)

_TFM_RE = re.compile(r"<TargetFrameworks?>([^<]+)</TargetFrameworks?>", re.IGNORECASE)


def config_path() -> Path:
    """映射表路径（可经 REBUILD_TOOLCHAIN_IMAGES_CONFIG 覆盖，便于测试与部署）。"""
    override = os.environ.get("REBUILD_TOOLCHAIN_IMAGES_CONFIG")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "config" / "toolchain_images.yaml"


def load_toolchain_config(path: Optional[Path] = None) -> dict:
    """读映射表。缺失/不可解析 → 返回空 dict 并告警（公理 3：发声，不静默）。"""
    fp = path or config_path()
    try:
        import yaml
        with open(fp, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        if not isinstance(cfg, dict):
            logger.warning("工具链映射表格式非 mapping：%s", fp)
            return {}
        return cfg
    except FileNotFoundError:
        logger.warning("工具链映射表不存在：%s（工具链容器通道将诚实不可用）", fp)
        return {}
    except Exception as e:
        logger.warning("工具链映射表读取失败：%s（%s）", fp, e, exc_info=True)
        return {}


def toolchain_section(cfg: dict, toolchain: str = DOTNET) -> dict:
    return ((cfg.get("toolchains") or {}).get(toolchain) or {})


def registries_allowed(cfg: dict) -> list[str]:
    return list(cfg.get("registries_allowed") or [])


def image_is_allowed(cfg: dict, image_ref: str) -> tuple[bool, str]:
    """双重白名单：host 在 registries_allowed 内，且引用在映射表中出现过。

    绝不接受来自项目内容或 LLM 输出的镜像名（否则等于任意镜像执行）。
    """
    if not image_ref:
        return False, "镜像引用为空"
    host = image_ref.split("/", 1)[0]
    allowed_hosts = registries_allowed(cfg)
    if host not in allowed_hosts:
        return False, f"registry '{host}' 不在白名单 {allowed_hosts}"
    known = set()
    for section in (cfg.get("toolchains") or {}).values():
        known.update((section.get("target_framework_map") or {}).values())
        if section.get("default_image"):
            known.add(section["default_image"])
    if image_ref not in known:
        return False, "镜像引用未在 toolchain_images.yaml 中登记"
    return True, "ok"


# ── ① 构建根 / 构建对象发现（修 B3：显式项目路径，杜绝 MSB1003 伪失败）─────────

def discover_build_root(workspace: Path, toolchain: str = DOTNET,
                        cfg: Optional[dict] = None) -> Optional[Path]:
    """在工作区内定位构建根：优先平台产物目录，其次工作区自身。

    返回 None = 工作区内找不到该工具链的构建对象。
    """
    cfg = cfg if cfg is not None else load_toolchain_config()
    section = toolchain_section(cfg, toolchain)
    globs = list(section.get("project_globs") or []) + list(section.get("solution_globs") or [])
    if not globs:
        return None
    for cand in list(_BUILD_ROOT_CANDIDATES) + [""]:
        root = (workspace / cand) if cand else workspace
        if not root.is_dir():
            continue
        if _find_matching(root, globs, limit=1):
            return root
    return None


def _find_matching(root: Path, globs: list[str], limit: int = 0) -> list[Path]:
    """递归匹配（跳过 obj/bin 等中间产物目录）。limit>0 时早停。"""
    import fnmatch
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            if any(fnmatch.fnmatch(fname, g) for g in globs):
                found.append(Path(dirpath) / fname)
                if limit and len(found) >= limit:
                    return found
    return found


def extract_target_frameworks_from_text(text: str) -> list[str]:
    """从工程文件文本抽取目标框架（支持 TargetFramework / TargetFrameworks 分号列表）。"""
    out: list[str] = []
    for m in _TFM_RE.finditer(text or ""):
        for part in m.group(1).split(";"):
            tfm = part.strip()
            if tfm and tfm not in out:
                out.append(tfm)
    return out


def extract_target_frameworks(project_file: Path) -> list[str]:
    """从工程文件抽取目标框架。读取失败 → 空列表 + 告警（不猜）。"""
    try:
        text = project_file.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("工程文件读取失败：%s（%s）", project_file, e)
        return []
    return extract_target_frameworks_from_text(text)


def discover_projects(build_root: Path, toolchain: str = DOTNET,
                      cfg: Optional[dict] = None) -> dict:
    """发现构建对象 + 目标框架。返回相对 build_root 的路径（容器内可直接复用）。"""
    cfg = cfg if cfg is not None else load_toolchain_config()
    section = toolchain_section(cfg, toolchain)
    sol_globs = list(section.get("solution_globs") or [])
    proj_globs = list(section.get("project_globs") or [])
    test_markers = [m.lower() for m in (section.get("test_project_markers") or [])]

    solutions = [str(p.relative_to(build_root)) for p in _find_matching(build_root, sol_globs)]
    projects = []
    frameworks: list[str] = []
    for p in sorted(_find_matching(build_root, proj_globs)):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("工程文件读取失败：%s（%s）", p, e)
            text = ""
        tfms = extract_target_frameworks_from_text(text)
        low = text.lower()
        projects.append({
            "path": str(p.relative_to(build_root)),
            "target_frameworks": tfms,
            "is_test": any(m in low for m in test_markers),
        })
        for t in tfms:
            if t not in frameworks:
                frameworks.append(t)
    return {"build_root": str(build_root), "solutions": sorted(solutions),
            "projects": projects, "target_frameworks": sorted(frameworks)}


# ── ② 框架 → 镜像映射（无映射不猜）────────────────────────────────────────────

def resolve_image(frameworks: list[str], toolchain: str = DOTNET,
                  cfg: Optional[dict] = None) -> tuple[Optional[str], str]:
    """目标框架 → 镜像引用。返回 (image_ref | None, reason)。

    多个不同框架 / 无映射 → **不猜**，返回 None + 原因（上游落 evidence_gap）。
    """
    cfg = cfg if cfg is not None else load_toolchain_config()
    section = toolchain_section(cfg, toolchain)
    fmap = section.get("target_framework_map") or {}
    if not fmap:
        return None, f"toolchain_images.yaml 未配置 '{toolchain}' 的 target_framework_map"
    if not frameworks:
        return None, "未能从工程文件抽取到目标框架（不猜测镜像）"
    mapped = {fmap.get(f) for f in frameworks}
    if None in mapped:
        missing = [f for f in frameworks if f not in fmap]
        return None, f"未映射的目标框架：{missing}（请在 toolchain_images.yaml 增加映射）"
    if len(mapped) > 1:
        return None, (f"多个目标框架映射到不同镜像 {sorted(m for m in mapped if m)}，"
                      "单容器无法同时满足（不猜测）")
    image = mapped.pop()
    ok, why = image_is_allowed(cfg, image or "")
    if not ok:
        return None, f"镜像引用被白名单拒绝：{why}"
    return image, "ok"


# ── ③ Docker / 镜像三态探测（只读，不拉取）───────────────────────────────────

def probe_docker() -> dict:
    """Docker 守护是否可达（只读）。不可达 → reachable=False + 原因，绝不伪造。"""
    try:
        import docker  # noqa: PLC0415 — 惰性导入：无 docker SDK 时不拖垮 P5
    except Exception as e:
        return {"reachable": False, "reason": f"docker SDK 不可用：{type(e).__name__}",
                "docker": "sdk_missing"}
    try:
        client = docker.from_env()
        ver = client.version() or {}
        return {"reachable": True, "docker": "reachable",
                "server_version": ver.get("Version", ""), "reason": "ok"}
    except Exception as e:
        # 公理 3：发声但不阻断；上游据此落诚实 evidence_gap。
        logger.warning("Docker 守护不可达（工具链容器通道将诚实不可用）：%s", e)
        return {"reachable": False, "docker": "unreachable",
                "reason": f"Docker 守护不可达：{type(e).__name__}: {e}"}


def probe_image(image_ref: str) -> dict:
    """镜像是否已在本地 + RepoDigest（滚动 tag 会漂移，digest 才可复现，Q-R19-1-6）。"""
    if not image_ref:
        return {"present": False, "image": "unspecified", "digest": ""}
    try:
        import docker
        from docker.errors import ImageNotFound
    except Exception as e:
        return {"present": False, "image": "sdk_missing", "digest": "",
                "reason": f"docker SDK 不可用：{type(e).__name__}"}
    try:
        client = docker.from_env()
        img = client.images.get(image_ref)
        digests = getattr(img, "attrs", {}).get("RepoDigests") or []
        return {"present": True, "image": "local", "digest": digests[0] if digests else "",
                "image_id": getattr(img, "id", "") or "", "reason": "ok"}
    except ImageNotFound:
        return {"present": False, "image": "unavailable", "digest": "",
                "reason": (f"镜像 {image_ref} 不在本地。预热命令："
                           f"bash deploy/toolchain-images/pull.sh {image_ref}")}
    except Exception as e:
        logger.warning("镜像探测失败 %s：%s", image_ref, e)
        return {"present": False, "image": "probe_failed", "digest": "",
                "reason": f"镜像探测失败：{type(e).__name__}: {e}"}


def probe_toolchain(workspace: Path, toolchain: str = DOTNET,
                    cfg: Optional[dict] = None) -> dict:
    """工具链容器通道三态探测（R19-1-04 诚实降级的单一判据来源）。

    返回 dict：
      available      bool  —— 仅当 Docker 可达 + 镜像已在本地 + 构建对象已发现 才为 True
      reason         str   —— 不可用的诚实原因（供 evidence_gap 文案）
      probe          dict  —— 确定性探测明细（docker / image / digest / frameworks）
      image_ref      str   —— 解析到的镜像引用（未解析出则 ""）
      build_root     str   —— 宿主侧构建根绝对路径
      projects       list  —— 构建对象（相对 build_root）
      container_paths / limits —— 来自 yaml，供 provider 使用

    **任何一态都不产 available 之外的乐观结论**（§10-20 / D-097）。
    """
    cfg = cfg if cfg is not None else load_toolchain_config()
    section = toolchain_section(cfg, toolchain)
    out: dict = {
        "toolchain": toolchain,
        "available": False,
        "reason": "",
        "probe": {},
        "image_ref": "",
        "build_root": "",
        "projects": [],
        "solutions": [],
        "target_frameworks": [],
        "container_paths": dict(section.get("container_paths") or {}),
        "limits": dict(section.get("limits") or {}),
        "container_env": dict(section.get("container_env") or {}),
        "package_config": dict(section.get("package_config") or {}),
        "network_mode": section.get("network_mode") or "none",
        "network_weakened": bool(section.get("network_weakened", False)),
        "network_note": section.get("network_note") or "",
        "allow_runtime_pull": bool(cfg.get("allow_runtime_pull", False)),
    }
    if not section:
        out["reason"] = f"toolchain_images.yaml 未配置工具链 '{toolchain}'"
        out["probe"] = {"config": "missing"}
        return out

    build_root = discover_build_root(workspace, toolchain, cfg)
    if build_root is None:
        out["reason"] = "工作区内未发现该工具链的构建对象（.sln/.csproj）"
        out["probe"] = {"build_objects": "absent"}
        return out
    found = discover_projects(build_root, toolchain, cfg)
    out.update({"build_root": found["build_root"], "projects": found["projects"],
                "solutions": found["solutions"],
                "target_frameworks": found["target_frameworks"]})

    image, why = resolve_image(found["target_frameworks"], toolchain, cfg)
    out["probe"]["target_frameworks"] = found["target_frameworks"]
    if image is None:
        out["reason"] = why
        out["probe"]["image"] = "unmapped"
        return out
    out["image_ref"] = image

    dock = probe_docker()
    out["probe"]["docker"] = dock.get("docker")
    if dock.get("server_version"):
        out["probe"]["docker_server_version"] = dock["server_version"]
    if not dock.get("reachable"):
        out["reason"] = dock.get("reason", "Docker 守护不可达")
        return out

    img = probe_image(image)
    out["probe"]["image"] = img.get("image")
    out["probe"]["image_digest"] = img.get("digest", "")
    if not img.get("present"):
        out["reason"] = img.get("reason", f"镜像 {image} 不可得")
        return out

    out["available"] = True
    out["reason"] = "ok"
    return out
