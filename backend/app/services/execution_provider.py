"""Execution provider seam — selects HOW code/commands run in the platform.

D-076 (2026-06-26): Code execution and AI coding agents are strictly separate paths.
  - ExecutionProvider handles subprocess / container execution ONLY.
  - AI coding agent invocation goes through CodingAgentAdapter (coding_agent_adapter.py).
  - ExecutionProvider must NEVER depend on OpenCode CLI availability.

R8: ContainerExecutionProvider wired via docker-py (Plan A, Q-R8-01 confirmed).
R9-5-6: RemoteSSHExecutionProvider added (D-093). Three-mode factory.

Modes (env `EXECUTION_MODE`, default "local"):
  - "local"     → LocalSubprocessExecutionProvider: host subprocess behind deny-list.
  - "container" → ContainerExecutionProvider: disposable, network-isolated container.
                  NEVER silently downgrades to host execution.
  - "remote"    → RemoteSSHExecutionProvider: paramiko SSH + SFTP (D-093).

R19-1 (G1 容器真构建)：新增 "toolchain_container" → ToolchainContainerExecutionProvider。
  与 ContainerExecutionProvider **并列而非替换**：后者的 untrusted_snippet 硬化基线
  （network=none / read_only / cap_drop=ALL / 256m / uid 10002 / 纯 Python 沙箱镜像）
  **一字不改**；新档用厂商官方 SDK 镜像跑真实项目构建，仅 3 处最小挂载，出网为
  **如实标注的弱化项**（经用户批准的 C2 变更，Q-R19-1-1）。绝不挂 docker.sock。

R21：子进程生命周期原语（起/等/超时终止/收尾）已抽到 `subprocess_runner.py` ——
  `git_service.run_git_command` 有同一个孤儿进程缺陷，两处必须共用**一份**清理实现
  （`B-R20-REDACT-THREE-IMPLS` 的教训），同时让本文件回到 800 行上限内。
  本文件保留的是**安全策略**（DENY 名单 / 白名单 / 环境清洗 / 风险分级）与 provider 接线。
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

from app.services.subprocess_runner import run_subprocess_command

logger = logging.getLogger("rebuild.execution_provider")


# ── Security boundaries (D-076: owned by ExecutionProvider, not opencode_adapter) ─

DENY_SUBSTRINGS = [
    "rm -rf", "rm -r", "sudo", "curl | sh", "wget | sh",
    "/dev/", "/proc/", "/sys/", "~/.ssh", "~/.gnupg",
    ".env", "id_rsa", "id_ed25519", "id_ecdsa",
    "/etc/passwd", "/etc/shadow", "/root/",
]

# R18-1 P1-06（D-033 缺口）：子串匹配无法覆盖变体，实测漏报 3 条——fork bomb
# `:(){ :|:&;}:;`、`chmod 777 /`、`chown -R root /`。改为"编译正则优先 + 子串兜底"。
# 吸收自 rebuild-archive/V10/原始代码/backend/app/sandbox/gvisor.py:28-41 的 DENY_LIST_COMMANDS
# 思路（正则化），但【只取本轮实测漏报的三条并适配收窄】，不整体复制：
#   - chmod：V10 原式 `chmod\s+.*777\s+/` 会把 `chmod 777 /srv/app/main.py` 也升为 L5，
#     与既有 L4 语义（test_r956_execution）冲突且属过度拦截 → 收窄为仅锚定根目录 `/`。
#   - 未吸收 V10 的 DENY_LIST_PATTERNS（`(root|etc|proc|sys|dev)` 宽匹配 / `credentials`）：
#     它会误杀 `npm run dev` 之类合法命令，属行为退化，见施工记录"吸收记录"。
DENY_REGEXES: list[re.Pattern] = [
    # fork bomb 及其变体：:(){ :|:&;}:;
    re.compile(r":\(\)\s*\{.*:\|:&.*\};*:", re.IGNORECASE),
    # 根目录整体放权：chmod [-R] 777 /（仅根，具体文件仍按 L4 处理）
    re.compile(r"\bchmod\s+(?:-\S+\s+)*777\s+/(?=[\s;&|]|$)", re.IGNORECASE),
    # 递归把绝对路径改归 root：chown -R root /...
    re.compile(r"\bchown\s+(?:-\S+\s+)*-R\s+(?:-\S+\s+)*root(?::\S+)?\s+/", re.IGNORECASE),
]

# R18-1 P1-01：环境变量【值】中的内嵌凭据 scheme://user[:password]@host
# （`[^/\s]+@` 贪婪到首个 `/` 之前的最后一个 `@`，避免口令自带 `@` 时残留片段）
_URL_CREDENTIAL_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*://)[^/\s]+@")

ALLOWED_COMMANDS = ["python3", "python", "echo", "cat", "ls", "pwd", "which"]

# Commands that are high-risk but not in DENY_SUBSTRINGS (L4 soft-block)
_L4_PATTERNS = [
    "chmod", "chown", "chgrp", "crontab", "iptables", "ip6tables",
    "nftables", "nc -l", "ncat -l", "netcat -l", "mkfs", "fdisk",
    "dd if=", "kill -9", "killall", "systemctl", "service ",
    "useradd", "userdel", "passwd", "adduser",
]


def _strip_url_credentials(value: str) -> str:
    """R18-1 P1-01：剥离 URL 值中内嵌的 user[:password]@，保留 scheme/host/port/path。

    `postgresql://user:口令@localhost:5432/db` → `postgresql://localhost:5432/db`
    非 URL 的普通值原样返回（不误伤）。纯正则实现，不引 urlparse 异常分支
    （避免为 parse 失败写静默 except，公理3）。
    """
    return _URL_CREDENTIAL_RE.sub(r"\1", value, count=1)


def _match_deny_regex(code: str) -> str | None:
    """R18-1 P1-06：编译正则 DENY 检查（覆盖子串匹配漏报的变体）。命中返回模式串。"""
    for pat in DENY_REGEXES:
        if pat.search(code):
            return pat.pattern
    return None


def _check_dangerous(code: str) -> str | None:
    # R18-1 P1-06：正则检查优先于子串检查（子串漏报 fork bomb / chmod 777 / / chown -R root /）
    hit = _match_deny_regex(code)
    if hit:
        return f"Blocked dangerous pattern: {hit}"
    code_lower = code.lower()
    for pattern in DENY_SUBSTRINGS:
        if pattern.lower() in code_lower:
            return f"Blocked dangerous pattern: {pattern}"
    return None


def _classify_risk(code: str, language: str = "exec") -> str:
    """Classify execution risk level L0-L5 (R9-5-6 T8 / D-093).

    Returns the risk level string. Does NOT block — caller decides based on mode.
    """
    code_lower = code.lower()

    # L5: hard deny patterns (DENY_REGEXES 正则 + DENY_SUBSTRINGS 子串)
    if _match_deny_regex(code):
        return "L5"
    for p in DENY_SUBSTRINGS:
        if p.lower() in code_lower:
            return "L5"

    # L4: system-modification patterns
    for p in _L4_PATTERNS:
        if p.lower() in code_lower:
            return "L4"

    if language in ("python", "python3"):
        # Python: flag subprocess/os.system calls as L3
        if any(x in code_lower for x in ("subprocess", "os.system", "os.popen", "exec(")):
            return "L3"
        return "L1"

    # Shell/bash
    first_word = (code.strip().split() or [""])[0].split("/")[-1].lower()
    if first_word in {c.lower() for c in ALLOWED_COMMANDS}:
        return "L1"
    # Unknown command
    return "L3"


def _clean_env() -> dict:
    env = os.environ.copy()
    for key in list(env.keys()):
        if any(s in key.upper() for s in ["KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"]):
            del env[key]
        else:
            # R18-1 P1-01：名不敏感的变量，其【值】里仍可能内嵌凭据
            # （DATABASE_URL=postgresql://user:口令@host 会原样传给子进程）→ 剥离 user:pass。
            env[key] = _strip_url_credentials(env[key])
    # WP-E (B-R17.2-P5-PATH): inherit the host PATH so installed toolchains
    # (sdkman java, nvm node, dotnet, go, mvn, npm …) are visible to P5 build/verify
    # commands. Previously PATH was pinned to /usr/local/bin:/usr/bin:/bin, which hid
    #信创 toolchains → mvn/npm/go returned "command not found" (127) → false
    # validation_failed. PATH is not a secret; sensitive KEY/TOKEN/SECRET/PASSWORD/
    # CREDENTIAL vars are still stripped above. Standard system dirs are guaranteed to
    # be present even if the host PATH is unusual/empty.
    host_path = os.environ.get("PATH", "")
    std_dirs = ["/usr/local/bin", "/usr/bin", "/bin"]
    parts = [p for p in host_path.split(os.pathsep) if p]
    for d in std_dirs:
        if d not in parts:
            parts.append(d)
    env["PATH"] = os.pathsep.join(parts)
    return env


class ExecutionProvider(Protocol):
    """Contract every execution backend implements (R9-5-6 T3: aligned signature)."""

    name: str

    async def execute(self, code: str, language: str = "python",
                      timeout: int = 30, model: str | None = None,
                      cwd: str | None = None) -> dict:
        """Run code/command and return the standard ExecutionResult dict.

        Result keys: exit_code, stdout, stderr, elapsed_ms, provider,
        execution_mode, fallback, blocked, risk_level, audited.
        Never raises for ordinary execution failures.
        """
        ...


class LocalSubprocessExecutionProvider:
    """Host-subprocess execution behind the deny-list (current default).

    D-076: Does NOT depend on OpenCode CLI. Subprocess is always available
    regardless of whether any AI coding agent is installed.
    """

    name = "local_subprocess"

    async def execute(self, code: str, language: str = "python",
                      timeout: int = 30, model: str | None = None,
                      cwd: str | None = None) -> dict:
        import time
        start = time.monotonic()

        denial = _check_dangerous(code)
        if denial:
            return {
                "exit_code": 1, "stdout": "", "stderr": denial,
                "elapsed_ms": 0, "provider": "security_block",
                "execution_mode": "local", "fallback": True, "blocked": True,
                "risk_level": "L5", "audited": False,
                # D-034（R18-1 P1-02）：L5 不再静默 block —— 上报 gate_required，
                # 由调用侧（stage_handlers）创建可裁决的用户 Gate。
                "gate_required": True,
                # R21 循环卫生②：timed_out/signal 独立于 exit_code 真实上报——安检拒绝
                # 不是超时，也没有进程可归因信号。
                "timed_out": False, "signal": None,
            }

        risk = _classify_risk(code, language)
        result = await _run_subprocess(code, language, timeout, cwd=cwd)
        result["elapsed_ms"] = int((time.monotonic() - start) * 1000)
        result["execution_mode"] = "local"
        result["risk_level"] = risk
        result["audited"] = False
        return result


async def _run_subprocess(code: str, language: str, timeout: int,
                         cwd: str | None = None,
                         enforce_whitelist: bool = True) -> dict:
    """Execute via python3 -c or bash -c in a clean environment.

    R9-3A: Accepts optional cwd to bind execution to project workspace (WP-A1.1).
    R12-10: enforce_whitelist=False allows platform-internal P5 commands
    (mvn/go/npm/cargo/make/cmake) while keeping DENY_SUBSTRINGS L5 check.
    R21: 超时/异常/取消三条退出路径都**真正终止子进程并等它停稳**，不再留下孤儿进程。
    该清理机制已抽到 `subprocess_runner.run_subprocess_command`（与
    `git_service.run_git_command` 共用同一份实现，避免第二份副本悄悄漂移）；本函数
    只保留**安全策略**：语言 → argv 映射、ALLOWED_COMMANDS 白名单、`_clean_env`
    环境清洗，以及 provider/fallback/blocked 这几个既有返回字段。
    """
    clean_env = _clean_env()

    if language in ("python", "python3"):
        cmd = ["python3", "-c", code]
    elif language in ("bash", "sh", "shell"):
        if enforce_whitelist:
            first_word = (code.strip().split() or [""])[0].split("/")[-1]
            if first_word and first_word not in ALLOWED_COMMANDS:
                return {
                    "exit_code": 1, "stdout": "",
                    "stderr": f"命令不在允许列表中: {first_word}",
                    "provider": "local_subprocess", "fallback": False, "blocked": True,
                    "timed_out": False, "signal": None,
                }
        cmd = ["bash", "-c", code]
    else:
        cmd = ["python3", "-c", code]

    try:
        outcome = await run_subprocess_command(cmd, timeout=timeout, cwd=cwd, env=clean_env)
    except Exception as e:
        # 起不来（命令不存在 / cwd 不存在 …）：没有进程需要清理。
        # 注：CancelledError 在 py3.8+ 继承 BaseException，不会被这里吞掉 ——
        # 取消语义由 run_subprocess_command 处理（SIGKILL 后继续向上传播）。
        return {
            "exit_code": -1, "stdout": "", "stderr": str(e),
            "provider": "local_subprocess", "fallback": False, "blocked": False,
            "timed_out": False, "signal": None,
        }

    # R21 循环卫生②：timed_out / exit_code / signal 三个事实各自独立上报，不嵌套
    # 依赖——signal 从 POSIX returncode 约定（负数 -N = 被信号 N 终止）派生，不猜测；
    # 正常退出（returncode>=0）则 signal 诚实报 None（没有信号可归因）。
    return {
        "exit_code": outcome.exit_code,
        "stdout": outcome.stdout,
        "stderr": outcome.stderr,
        "provider": "local_subprocess",
        "fallback": False,
        "blocked": False,
        "timed_out": outcome.timed_out,
        "signal": outcome.signal,
    }


class ContainerExecutionProvider:
    """R8: Execute inside a disposable isolated container via docker-py.

    Uses the execution-sandbox image built in deploy/execution-sandbox/.
    Container runs with: no network, read-only root, non-root, cap_drop ALL.
    NEVER silently downgrades to host execution.
    """

    name = "container_sandbox"

    async def execute(self, code: str, language: str = "python",
                      timeout: int = 30, model: str | None = None,
                      cwd: str | None = None) -> dict:
        """R19-1（Q-R19-1-4 用户批准）：补 `cwd` 形参以对齐 `ExecutionProvider` Protocol。

        旧签名无 `cwd`，而 Protocol（:166-168）与调用点 `p5_command_service.execute_slot_command`
        （传 `cwd=str(ws)`）都要求它 ⇒ 一旦 `P5_EXECUTION_MODE=container`，会抛 TypeError 并被
        调用侧捕获成 `validation_failed`，把"能力未接线"表现成"构建失败"（伪失败，违反验收
        §0.3 第 3 条方向性纪律）。记为 `B-P5-CONTAINER-CWD`。

        本档是面向**不可信代码片段**的一次性强隔离沙箱：代码写入临时目录并以 **ro** 挂到
        `/workspace`，工作目录固定在容器内，宿主 `cwd` 在此档**无意义**（挂宿主工作区会破坏
        隔离基线）。故此处**接受并如实忽略** `cwd`，只消除签名不一致，
        **不改本档任何硬化参数**（network=none / read_only / cap_drop / 256m / uid 10002）。
        需要真实工作区构建请用 `toolchain_container` 档（ToolchainContainerExecutionProvider）。
        """
        import time
        start = time.time()

        if cwd:
            logger.debug(
                "ContainerExecutionProvider 忽略 cwd=%s：不可信片段档不挂宿主工作区（隔离基线），"
                "真实项目构建请用 toolchain_container 档。", cwd)

        denial = _check_dangerous(code)
        if denial:
            return {
                "exit_code": 1, "stdout": "", "stderr": denial,
                "elapsed_ms": 0, "provider": "security_block",
                "execution_mode": "container", "fallback": False, "blocked": True,
                "risk_level": "L5", "audited": False,
                "gate_required": True,   # D-034（R18-1 P1-02）：L5 须可裁决，不静默 block
                "timed_out": False, "signal": None,
            }

        # Write code to a temp file that will be mounted into the container.
        # The sandbox runs as non-root uid 10002 which does not own this host
        # tempdir, so make the dir traversable and the file world-readable.
        tmpdir = tempfile.mkdtemp(prefix="rb-exec-")
        code_file = Path(tmpdir) / ("code.py" if language in ("python", "python3") else "script.sh")
        code_file.write_text(code, encoding="utf-8")
        os.chmod(tmpdir, 0o755)
        os.chmod(code_file, 0o644)

        is_python = language in ("python", "python3")
        cmd = ["python3", "/workspace/code.py"] if is_python else ["bash", "/workspace/script.sh"]

        # R21 循环卫生②：timed_out/signal 独立于 exit_code 上报，不嵌套依赖。
        timed_out = False
        signal_num = None
        try:
            import docker
            client = docker.from_env()
            container = client.containers.run(
                image="rebuild-execution-sandbox:local",
                command=cmd,
                volumes={tmpdir: {"bind": "/workspace", "mode": "ro"}},
                network_mode="none",
                read_only=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                mem_limit="256m",
                pids_limit=64,
                user="10002",
                detach=True,
                # NOTE: no auto-remove here — we must wait() + read logs() first;
                # the finally block force-removes. remove=True races the log read (409).
            )
            try:
                result = container.wait(timeout=timeout)
                exit_code = result.get("StatusCode", -1)
                stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")[-8000:]
                stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")[-4000:]
            except Exception:
                timed_out = True
                container.kill()
                signal_num = 9  # docker kill 默认发 SIGKILL——kill() 未抛异常即真实发生
                exit_code = -1
                stdout = ""
                stderr = f"Container execution timed out after {timeout}s"
            finally:
                try:
                    container.remove(force=True)
                except Exception:
                    # advisory：best-effort 清理容器；执行结果已在上方捕获，清理失败不影响返回值。
                    logger.debug("容器清理 remove 失败（best-effort）", exc_info=True)
        except Exception as e:
            exit_code = -1
            stdout = ""
            stderr = f"Container execution error: {e}. Is docker running and rebuild-execution-sandbox:local built?"
        finally:
            import shutil
            try:
                shutil.rmtree(tmpdir)
            except Exception:
                # advisory：best-effort 清理临时目录；清理失败不影响执行结果，仅可能残留临时文件。
                logger.debug("临时目录清理 rmtree 失败（best-effort）tmpdir=%s", tmpdir, exc_info=True)

        elapsed = int((time.time() - start) * 1000)
        risk = _classify_risk(code, language)
        return {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_ms": elapsed,
            "provider": "container_sandbox",
            "execution_mode": "container",
            "fallback": False,
            "blocked": False,
            "risk_level": risk,
            "audited": False,
            "timed_out": timed_out,
            "signal": signal_num,
        }


class ToolchainContainerExecutionProvider:
    """R19-1 G1：`toolchain_build` 硬化档 —— 项目工具链构建专用一次性容器。

    与 `ContainerExecutionProvider`（`untrusted_snippet` 档）**并列而非替换**：后者面向不可信
    代码片段、镜像为纯 Python 沙箱、无网络、无写入位，架构上跑不了真实项目构建；本档面向
    **平台内部可信的项目构建命令**，用厂商官方 SDK 镜像（引用来自
    `backend/app/config/toolchain_images.yaml`，由项目事实映射得出，代码内不出现框架/镜像字面量）。

    挂载（最小必要，R19-1-06）——只 3 处：
      src            → ro   构建输入（构建不得篡改平台产物，也不往 output_code 落 obj/bin）
      build          → rw   **唯一写入位**：HOME / CLI 状态 / 中间产物 / 输出 / 包源配置
      package_cache  → rw   包缓存，跨轮复用

    明确**不挂**：`/var/run/docker.sock`（挂它等于把宿主 Docker 控制权交给容器；本档架构上
    不需要——Docker API 由宿主侧 backend 调用，容器内只跑构建命令）、`source/`（D-099）、
    仓库根 / `.env*`、`~/.ssh`、`~/.nuget`（宿主包源配置可能带私有源凭据 ⇒ 用平台自管无凭据
    配置）、其他项目工作区。

    硬化项（保留/弱化，逐项如实标注，见 `hardening` 返回字段）：
      保留：`cap_drop=ALL`、`no-new-privileges`、根 `read_only=True`（+ tmpfs /tmp）、
            资源上限（mem/pids/cpu，来自 yaml）、**非 root**（显式传宿主 uid:gid）、
            一次性容器（用完 force remove）、不发布任何端口（不触碰 §10-19 端口标准）、
            DENY 安检与风险分级先行、`_clean_env` 不外传宿主环境。
      **弱化（如实标注，不假称已白名单化）**：网络。`restore` 必须访问包源 ⇒ 本档
            `network_mode` 由 yaml 给出（非 `none`）。docker 原生无法做精细 egress 白名单。
            属经用户批准的 **C2 变更**（Q-R19-1-1 resolved，2026-09-04）：现有强隔离档一字不动、
            弱化只发生在本新增档、且本档**仅用于工具链构建**。
    """

    name = "toolchain_container"

    def __init__(self, *, image_ref: str, src_dir: str, build_dir: str,
                 package_cache_dir: str,
                 container_paths: dict | None = None,
                 container_env: dict | None = None,
                 package_config: dict | None = None,
                 limits: dict | None = None,
                 network_mode: str = "none",
                 network_weakened: bool = False,
                 network_note: str = "",
                 image_digest: str = ""):
        if not image_ref:
            raise ValueError("ToolchainContainerExecutionProvider 需要 image_ref（不猜镜像）")
        if not src_dir:
            raise ValueError("ToolchainContainerExecutionProvider 需要 src_dir（构建输入）")
        self.image_ref = image_ref
        self.image_digest = image_digest
        self.src_dir = str(Path(src_dir).resolve())
        self.build_dir = str(Path(build_dir).resolve())
        self.package_cache_dir = str(Path(package_cache_dir).resolve())
        paths = container_paths or {}
        self.c_src = paths.get("src") or "/src"
        self.c_build = paths.get("build") or "/build"
        self.c_cache = paths.get("package_cache") or "/cache"
        self.container_env = dict(container_env or {})
        self.package_config = dict(package_config or {})
        self.limits = dict(limits or {})
        self.network_mode = network_mode or "none"
        self.network_weakened = bool(network_weakened)
        self.network_note = network_note or ""

    # ── 宿主侧准备（唯一写入位 + 无凭据包源配置）──────────────────────────
    def _prepare_host_dirs(self) -> None:
        """创建 rw 挂载目录。失败必须抛（公理 3：不静默吞、不降级）。"""
        home = self.container_env.get("HOME", "")
        Path(self.build_dir).mkdir(parents=True, exist_ok=True)
        Path(self.package_cache_dir).mkdir(parents=True, exist_ok=True)
        if home.startswith(self.c_build + "/"):
            # 容器内 HOME 落在 build 挂载内 → 宿主侧先建好，避免 read_only 根下无法创建
            (Path(self.build_dir) / home[len(self.c_build) + 1:]).mkdir(parents=True, exist_ok=True)
        filename = self.package_config.get("filename")
        content = self.package_config.get("content")
        if filename and content:
            (Path(self.build_dir) / filename).write_text(content, encoding="utf-8")

    def volumes(self) -> dict:
        """挂载清单（写入证据，供 R19-1-06 逐条核对最小性）。"""
        return {
            self.src_dir: {"bind": self.c_src, "mode": "ro"},
            self.build_dir: {"bind": self.c_build, "mode": "rw"},
            self.package_cache_dir: {"bind": self.c_cache, "mode": "rw"},
        }

    def hardening(self) -> dict:
        """硬化档如实自述：保留了什么、弱化了什么（禁止美化）。"""
        return {
            "profile": "toolchain_build",
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "read_only_rootfs": True,
            "tmpfs": ["/tmp"],
            "run_as_non_root": True,
            "user": f"{os.getuid()}:{os.getgid()}",
            "mem_limit": self.limits.get("mem_limit"),
            "pids_limit": self.limits.get("pids_limit"),
            "cpu_count": self.limits.get("cpu_count"),
            "published_ports": [],
            "docker_socket_mounted": False,
            "network_mode": self.network_mode,
            "network_weakened": self.network_weakened,
            "network_note": self.network_note,
            "weakened_items": (["network"] if self.network_weakened else []),
        }

    def _execution_meta(self) -> dict:
        return {
            "provider": self.name,
            "execution_mode": self.name,
            "image_ref": self.image_ref,
            "image_digest": self.image_digest,
            "container_workdir": self.c_src,
            "mounts": [{"host": h, "container": v["bind"], "mode": v["mode"]}
                       for h, v in self.volumes().items()],
            "hardening": self.hardening(),
        }

    async def execute(self, code: str, language: str = "bash",
                      timeout: int = 30, model: str | None = None,
                      cwd: str | None = None) -> dict:
        """在一次性构建容器内执行命令。

        `cwd` 语义：命令的运行目录。本档把 `src_dir` 以 ro 挂到容器内 `container_paths.src`
        并把它设为 `working_dir`，因此命令中的相对路径以构建根为基准。调用侧传入的宿主
        `cwd` 若与 `src_dir` 不一致会被如实记录（不静默改语义）。
        """
        import time
        start = time.time()
        meta = self._execution_meta()

        # ① DENY 安检先行（与既有档一致，不因换通道而放宽）
        denial = _check_dangerous(code)
        if denial:
            return {
                "exit_code": 1, "stdout": "", "stderr": denial,
                "elapsed_ms": 0, "provider": "security_block",
                "execution_mode": self.name, "fallback": False, "blocked": True,
                "risk_level": "L5", "audited": False,
                "gate_required": True,   # D-034：L5 须可裁决，不静默 block
                "timed_out": False, "signal": None,
                **{k: v for k, v in meta.items() if k not in ("provider", "execution_mode")},
            }

        risk = _classify_risk(code, language)
        cwd_mismatch = bool(cwd) and str(Path(cwd).resolve()) != self.src_dir
        if cwd_mismatch:
            logger.info("toolchain_container：调用侧 cwd=%s 与挂载构建根 %s 不一致，"
                        "以构建根为容器工作目录（已记入证据）", cwd, self.src_dir)

        try:
            self._prepare_host_dirs()
        except Exception as e:
            # 公理 3：准备失败必须发声，且诚实报为不可用，不伪造构建结论。
            logger.warning("toolchain_container 宿主目录准备失败：%s", e, exc_info=True)
            return {
                "exit_code": -1, "stdout": "",
                "stderr": f"构建工作目录准备失败：{type(e).__name__}: {e}",
                "elapsed_ms": int((time.time() - start) * 1000),
                "fallback": False, "blocked": False, "risk_level": risk, "audited": False,
                "toolchain_unavailable": True, "timed_out": False, "signal": None, **meta,
            }

        limits = self.limits
        cpu_count = limits.get("cpu_count")
        run_kwargs = dict(
            image=self.image_ref,
            command=["bash", "-lc", code],
            volumes=self.volumes(),
            working_dir=self.c_src,
            environment=self.container_env,
            network_mode=self.network_mode,
            read_only=True,
            tmpfs={"/tmp": "rw,size=256m"},
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit=limits.get("mem_limit") or "2g",
            pids_limit=int(limits.get("pids_limit") or 512),
            user=f"{os.getuid()}:{os.getgid()}",
            detach=True,
        )
        if cpu_count:
            run_kwargs["nano_cpus"] = int(float(cpu_count) * 1_000_000_000)

        unavailable = False
        # R21 循环卫生②：timed_out/signal 独立于 exit_code 上报，不嵌套依赖。
        timed_out = False
        signal_num = None
        try:
            import docker
            client = docker.from_env()
            container = client.containers.run(**run_kwargs)
            try:
                result = container.wait(timeout=timeout)
                exit_code = result.get("StatusCode", -1)
                stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
                stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
            except Exception:
                logger.warning("toolchain_container 执行超时/中断（%ss），已 kill 容器", timeout,
                               exc_info=True)
                timed_out = True
                try:
                    container.kill()
                    signal_num = 9  # docker kill 默认发 SIGKILL——kill() 未抛异常即真实发生
                except Exception:
                    logger.debug("容器 kill 失败（best-effort）", exc_info=True)
                exit_code = -1
                stdout = ""
                stderr = f"构建容器执行超时（{timeout}s）"
            finally:
                try:
                    container.remove(force=True)   # 一次性容器
                except Exception:
                    logger.debug("容器清理 remove 失败（best-effort）", exc_info=True)
        except Exception as e:
            # 环境不可用（Docker 守护 / 镜像 / SDK）≠ 构建失败。标 toolchain_unavailable，
            # 由上游落诚实 evidence_gap，**绝不**伪造 available/validated（§10-20 / D-097）。
            logger.warning("toolchain_container 不可用：%s", e, exc_info=True)
            unavailable = True
            exit_code = -1
            stdout = ""
            stderr = f"工具链构建容器不可用：{type(e).__name__}: {e}"

        return {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_ms": int((time.time() - start) * 1000),
            "fallback": False,
            "blocked": False,
            "risk_level": risk,
            "audited": False,
            "toolchain_unavailable": unavailable,
            "cwd_mismatch": cwd_mismatch,
            "timed_out": timed_out,
            "signal": signal_num,
            **meta,
        }


class WorkspaceLocalExecutionProvider:
    """R12-8: Workspace-bound local execution for P5 verification commands.

    Unlike LocalSubprocessExecutionProvider (which enforces ALLOWED_COMMANDS
    whitelist suitable only for tiny utility snippets), this provider runs
    build/test/verify commands directly in the project workspace with full
    toolchain access (mvn, npm, go, cargo, make, cmake, node, python3, etc).

    Security model (D-088② / D-076):
      - Still checks DENY_SUBSTRINGS (L5 hard block: sudo, curl|sh, rm -rf …).
      - No ALLOWED_COMMANDS whitelist — P5 is platform-internal (trusted),
        not user-supplied untrusted code.
      - cwd is always the project workspace root (no escape).
      - Risk is classified and audited, not silently elevated.

    This solves B-P5-CONTAINER-CWD: ContainerExecutionProvider lacks cwd and
    read-only-only mounts, which makes it architecturally unable to run real
    project builds. For P5 trusted workspace execution, local with DENY check
    + workspace-bound cwd is the correct seam.
    """

    name = "workspace_local"

    async def execute(self, code: str, language: str = "bash",
                      timeout: int = 30, model: str | None = None,
                      cwd: str | None = None) -> dict:
        import time
        start = time.time()

        denial = _check_dangerous(code)
        if denial:
            return {
                "exit_code": 1, "stdout": "", "stderr": denial,
                "elapsed_ms": 0, "provider": "workspace_local_security_block",
                "execution_mode": "workspace_local", "fallback": True, "blocked": True,
                "risk_level": "L5", "audited": False,
                "gate_required": True,   # D-034（R18-1 P1-02）：L5 须可裁决，不静默 block
                "timed_out": False, "signal": None,
            }

        risk = _classify_risk(code, language)
        result = await _run_subprocess(code, language, timeout, cwd=cwd,
                                        enforce_whitelist=False)
        result["elapsed_ms"] = int((time.time() - start) * 1000)
        result["execution_mode"] = "workspace_local"
        result["risk_level"] = risk
        result["audited"] = False
        return result


def get_execution_provider(
    mode: str | None = None,
    remote_host_id: str | None = None,
    db=None,
    *,
    trust_on_first_use: bool = False,
    provider_options: dict | None = None,
) -> "ExecutionProvider":
    """Return the execution provider for the given mode.

    Priority: explicit mode arg > EXECUTION_MODE env var > "local".
    Modes:
      - "local"               → LocalSubprocessExecutionProvider (ALLOWED_COMMANDS whitelist)
      - "workspace_local"     → WorkspaceLocalExecutionProvider (workspace-bound, no whitelist, DENY check)
      - "container"           → ContainerExecutionProvider (untrusted_snippet 档：无网络/只读/256m)
      - "toolchain_container" → ToolchainContainerExecutionProvider (R19-1 toolchain_build 档：
                                官方 SDK 镜像 + 最小 3 处挂载 + 出网为如实标注的弱化项)
      - "remote"              → RemoteSSHExecutionProvider (paramiko SSH + SFTP)

    trust_on_first_use: for the remote mode, accept unknown host keys on first
    connection and persist the fingerprint (TOFU, D-093).  Default False =
    reject unknown hosts.

    provider_options: toolchain_container 档所需的镜像引用 / 挂载路径 / 上限等（由
    `toolchain_resolver.probe_toolchain` 的真实探测结果给出）。缺必填项时**显式抛
    ValueError**（不猜镜像、不静默降级）。
    """
    resolved_mode = (mode or os.environ.get("EXECUTION_MODE") or "local").strip().lower()
    if resolved_mode == "container":
        return ContainerExecutionProvider()
    if resolved_mode == "toolchain_container":
        opts = provider_options or {}
        try:
            return ToolchainContainerExecutionProvider(**opts)
        except TypeError as e:
            raise ValueError(
                f"get_execution_provider(mode='toolchain_container') 的 provider_options 不合法：{e}"
            ) from e
    if resolved_mode == "workspace_local":
        return WorkspaceLocalExecutionProvider()
    if resolved_mode == "remote":
        if remote_host_id is None or db is None:
            raise ValueError(
                "get_execution_provider(mode='remote') requires remote_host_id and db"
            )
        from app.services.remote_executor import RemoteSSHExecutionProvider
        from app.services.remote_service import RemoteService
        host = RemoteService(db).get(remote_host_id)
        if host is None:
            raise ValueError(f"RemoteHost not found: {remote_host_id}")
        return RemoteSSHExecutionProvider(host, db, trust_on_first_use=trust_on_first_use)
    return LocalSubprocessExecutionProvider()
