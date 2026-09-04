"""R19-1 G1 容器真构建 —— 定向测试。

覆盖验收项：
  R19-1-02  宿主无 SDK + Docker 可用 → 按项目事实解析官方 SDK 镜像并在隔离环境执行
  R19-1-03  两阶段命令（restore + build --no-restore）+ 显式项目路径 + 诊断能到报告
  R19-1-04  **反向诚实**：Docker 不可用 / 镜像不在本地 / 框架无映射 → evidence_gap，
            绝不出现 available / validated（注入 fake docker client 抛 DockerException）
  R19-1-06  安全边界：只 3 处挂载、src 为 ro、**绝不挂 docker.sock**、非 root、
            cap_drop=ALL / no-new-privileges / read_only / 资源上限 / 不发布端口
  B1~B5     五处断点的回归护栏（含 slot_to_dict 必须序列化 details）

红线护栏（Q-R19-1-8）：MSB / NETSDK **不得**被分类成 compile 冒充 CS 类明细。
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services import build_diagnostics as bd
from app.services import toolchain_resolver as tr


# ── 真实容器输出样本（2026-09-04 实测捕获自 mcr.microsoft.com/dotnet/sdk:8.0）──────
# 不是编造的字符串：NU1101 来自 MicroOA output_code 的真实 restore；CS0103/CS0246 来自
# 同一镜像内一次真实编译（诊断抽取的正向对照）。
REAL_NU1101 = (
    "/src/MicroOA.Web/MicroOA.Web.csproj : error NU1101: Unable to find package "
    "Microsoft.EntityFrameworkCore.Dm. No packages exist with this id in source(s): nuget.org"
)
REAL_CS = (
    "/build/cs-positive-control/Broken.cs(3,30): error CS0103: The name 'Missing' does not "
    "exist in the current context [/build/cs-positive-control/proj.csproj]\n"
    "/build/cs-positive-control/Broken.cs(3,47): error CS0246: The type or namespace name "
    "'NotAType' could not be found (are you missing a using directive or an assembly "
    "reference?) [/build/cs-positive-control/proj.csproj]"
)
REAL_MSB = ("MSBUILD : error MSB1003: Specify a project or solution file. The current working "
            "directory does not contain a project or solution file.")
REAL_NETSDK = (
    "/usr/share/dotnet/sdk/8.0.424/Sdks/Microsoft.NET.Sdk/targets/"
    "Microsoft.PackageDependencyResolution.targets(266,5): error NETSDK1004: Assets file "
    "'/x/project.assets.json' not found. Run a NuGet package restore [/x/p.csproj]"
)


# ── (a) 诊断抽取：真实文本 → 结构化，且**类别如实不合并** ─────────────────────

class TestBuildDiagnostics:

    def test_parses_real_nu1101_as_dependency(self):
        diags = bd.parse_diagnostics(REAL_NU1101)
        assert len(diags) == 1
        d = diags[0]
        assert d["code"] == "NU1101"
        assert d["category"] == "dependency"
        assert d["severity"] == "error"
        assert d["file"].endswith("MicroOA.Web.csproj")
        assert "Microsoft.EntityFrameworkCore.Dm" in d["message"]

    def test_parses_real_cs_errors_with_line_and_column(self):
        diags = bd.parse_diagnostics(REAL_CS)
        codes = [d["code"] for d in diags]
        assert codes == ["CS0103", "CS0246"]
        assert all(d["category"] == "compile" for d in diags)
        assert diags[0]["line"] == 3 and diags[0]["column"] == 30
        assert diags[0]["file"].endswith("Broken.cs")

    def test_msb_and_netsdk_are_never_classified_as_compile(self):
        """Q-R19-1-8 红线：不得把 MSB/NETSDK 当 CS 类明细交差。"""
        diags = bd.parse_diagnostics(REAL_MSB, REAL_NETSDK)
        cats = {d["code"]: d["category"] for d in diags}
        assert cats["MSB1003"] == "msbuild"
        assert cats["NETSDK1004"] == "sdk"
        assert "compile" not in cats.values()

    def test_summary_separates_dependency_from_compile(self):
        summary = bd.summarize_diagnostics(bd.parse_diagnostics(REAL_NU1101, REAL_MSB))
        assert summary["by_category"]["dependency"] == 1
        assert summary["by_category"]["msbuild"] == 1
        # 「只有依赖类、没有编译类」是可机器判定的诚实事实，不给凑数留空间
        assert summary["by_category"].get("compile", 0) == 0
        assert summary["by_code"]["NU1101"] == 1
        assert summary["error_count"] == 2

    def test_duplicate_lines_deduped(self):
        """MSBuild 会在正文与摘要各打印一次同一错误。"""
        diags = bd.parse_diagnostics(REAL_NU1101 + "\n" + REAL_NU1101)
        assert len(diags) == 1

    def test_non_diagnostic_lines_ignored(self):
        assert bd.parse_diagnostics("  Determining projects to restore...\nBuild FAILED.") == []

    def test_tail_keeps_far_more_than_old_500_chars(self):
        """B4：旧实现只留 500 字符尾巴，NU1101/CS 文本根本到不了报告。"""
        assert bd.TAIL_LIMIT >= 4000
        text = "x" * 10000 + REAL_NU1101
        assert "NU1101" in bd.tail(text)

    def test_tail_redacts_secrets(self):
        # 合成夹具：**运行时拼装**，避免真实的 Key 形状字符串以字面量出现在源码里
        # （历史密钥扫描会把这类夹具当命中，虽为假阳但制造噪音，AGENTS §8）。
        fake = "sk-" + ("a" * 16) + "0123456789"
        out = bd.tail(f"api_key: {fake}")
        assert fake not in out
        assert "[REDACTED]" in out


# ── (b) 镜像映射：项目事实驱动、无映射不猜、双重白名单 ─────────────────────────

def _csproj(tmp_path: Path, rel: str, tfm: str = "net8.0", extra: str = "") -> Path:
    fp = tmp_path / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(
        f"<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup>"
        f"<TargetFramework>{tfm}</TargetFramework>{extra}"
        f"</PropertyGroup></Project>", encoding="utf-8")
    return fp


class TestToolchainResolver:

    def test_shipped_config_loads_and_has_registry_allowlist(self):
        cfg = tr.load_toolchain_config()
        assert cfg, "backend/app/config/toolchain_images.yaml 必须存在且可解析"
        assert tr.registries_allowed(cfg), "必须配置 registry 白名单"
        section = tr.toolchain_section(cfg)
        assert section.get("target_framework_map"), "必须配置框架→镜像映射"
        assert section.get("default_image") is None, "无映射时必须不猜（default_image=null）"

    def test_resolves_image_from_project_declared_framework(self):
        cfg = tr.load_toolchain_config()
        fmap = tr.toolchain_section(cfg)["target_framework_map"]
        tfm = sorted(fmap)[0]
        image, why = tr.resolve_image([tfm], cfg=cfg)
        assert image == fmap[tfm] and why == "ok"

    def test_unmapped_framework_returns_none_not_a_guess(self):
        image, why = tr.resolve_image(["netX.Y-not-mapped"])
        assert image is None
        assert "未映射" in why

    def test_no_framework_extracted_returns_none(self):
        image, why = tr.resolve_image([])
        assert image is None and "不猜" in why

    def test_multiple_conflicting_frameworks_returns_none(self):
        cfg = tr.load_toolchain_config()
        fmap = tr.toolchain_section(cfg)["target_framework_map"]
        two = sorted(fmap)[:2]
        if len({fmap[t] for t in two}) < 2:
            pytest.skip("映射表中前两个框架指向同一镜像，无冲突可测")
        image, why = tr.resolve_image(two, cfg=cfg)
        assert image is None and "多个目标框架" in why

    def test_image_outside_registry_allowlist_is_rejected(self):
        cfg = tr.load_toolchain_config()
        ok, why = tr.image_is_allowed(cfg, "evil.example.com/whatever:latest")
        assert ok is False and "白名单" in why

    def test_image_not_registered_in_config_is_rejected(self):
        """绝不接受来自项目内容或 LLM 输出的镜像名（否则等于任意镜像执行）。"""
        cfg = tr.load_toolchain_config()
        host = tr.registries_allowed(cfg)[0]
        ok, why = tr.image_is_allowed(cfg, f"{host}/attacker/backdoor:latest")
        assert ok is False and "未在" in why

    def test_discovers_projects_frameworks_and_test_flag(self, tmp_path):
        root = tmp_path / "output_code"
        _csproj(tmp_path, "output_code/App/App.csproj")
        _csproj(tmp_path, "output_code/App.Tests/App.Tests.csproj",
                extra="<IsTestProject>true</IsTestProject>")
        found = tr.discover_projects(root)
        paths = {p["path"]: p for p in found["projects"]}
        assert set(paths) == {"App/App.csproj", "App.Tests/App.Tests.csproj"}
        assert paths["App.Tests/App.Tests.csproj"]["is_test"] is True
        assert paths["App/App.csproj"]["is_test"] is False
        assert found["target_frameworks"] == ["net8.0"]

    def test_build_root_prefers_output_code_and_skips_obj(self, tmp_path):
        _csproj(tmp_path, "output_code/App/App.csproj")
        (tmp_path / "output_code" / "App" / "obj").mkdir(parents=True, exist_ok=True)
        _csproj(tmp_path, "output_code/App/obj/Stale.csproj")
        root = tr.discover_build_root(tmp_path)
        assert root == tmp_path / "output_code"
        found = tr.discover_projects(root)
        assert all("obj/" not in p["path"] for p in found["projects"])


# ── (c) R19-1-04 反向诚实：不可用路径① —— 注入 fake docker client ──────────────

class TestHonestDegradationUnavailablePaths:

    def test_probe_docker_unreachable_when_client_raises(self):
        import docker
        from docker.errors import DockerException
        with patch.object(docker, "from_env", side_effect=DockerException("no daemon")):
            out = tr.probe_docker()
        assert out["reachable"] is False
        assert out["docker"] == "unreachable"
        assert "Docker 守护不可达" in out["reason"]

    def test_probe_toolchain_reports_docker_unreachable_not_available(self, tmp_path):
        import docker
        from docker.errors import DockerException
        _csproj(tmp_path, "output_code/App/App.csproj")
        with patch.object(docker, "from_env", side_effect=DockerException("no daemon")):
            out = tr.probe_toolchain(tmp_path)
        assert out["available"] is False
        assert out["probe"]["docker"] == "unreachable"
        # 反伪造：任何一态都不得出现 available/validated
        assert "available" not in str(out["probe"].values()).replace("unavailable", "")

    def test_probe_image_absent_gives_prewarm_hint_not_available(self, tmp_path):
        _csproj(tmp_path, "output_code/App/App.csproj")
        with patch.object(tr, "probe_docker",
                          return_value={"reachable": True, "docker": "reachable"}), \
             patch.object(tr, "probe_image",
                          return_value={"present": False, "image": "unavailable", "digest": "",
                                        "reason": "镜像 X 不在本地。预热命令：pull.sh X"}):
            out = tr.probe_toolchain(tmp_path)
        assert out["available"] is False
        assert out["probe"]["image"] == "unavailable"
        assert "预热" in out["reason"]

    def test_capability_dotnet_build_is_evidence_gap_when_docker_unreachable(self, tmp_path):
        from app.services.p5_capability_service import (
            P5CapabilityService, CAP_EVIDENCE_GAP,
        )
        svc = P5CapabilityService()
        with patch("app.services.p5_capability_service.shutil.which", return_value=None), \
             patch.object(svc, "_probe_toolchain_container",
                          return_value={"available": False, "probe": {"docker": "unreachable"},
                                        "reason": "Docker 守护不可达：DockerException"}):
            cap = svc.probe_dotnet_build("proj")
        assert cap.status == CAP_EVIDENCE_GAP
        assert cap.environment_available is False
        assert cap.capability_ready is True          # 能力已接线
        assert cap.status != "available"

    def test_capability_dotnet_build_available_via_container_channel(self, tmp_path):
        from app.services.p5_capability_service import P5CapabilityService, CAP_AVAILABLE
        svc = P5CapabilityService()
        with patch("app.services.p5_capability_service.shutil.which", return_value=None), \
             patch.object(svc, "_probe_toolchain_container",
                          return_value={"available": True, "image_ref": "reg/x:1",
                                        "probe": {"image_digest": "reg/x@sha256:deadbeef"},
                                        "reason": "ok"}):
            cap = svc.probe_dotnet_build("proj")
        assert cap.status == CAP_AVAILABLE
        assert cap.environment_available is True
        assert cap.probe["dotnet_sdk"] == "absent"   # 事实如实：宿主确实没有 SDK
        assert "弱化项" in cap.note                   # 网络弱化必须如实标注

    def test_provider_reports_unavailable_not_build_failure(self):
        """环境不可用 ≠ 构建失败：必须置 toolchain_unavailable，供上游落 evidence_gap。"""
        import asyncio
        import docker
        from docker.errors import DockerException
        from app.services.execution_provider import ToolchainContainerExecutionProvider
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = ToolchainContainerExecutionProvider(
                image_ref="reg/x:1", src_dir=td, build_dir=f"{td}/b",
                package_cache_dir=f"{td}/c", container_paths={"src": "/src", "build": "/build",
                                                              "package_cache": "/nuget"})
            with patch.object(docker, "from_env", side_effect=DockerException("no daemon")):
                out = asyncio.run(p.execute("dotnet restore App/App.csproj"))
        assert out["toolchain_unavailable"] is True
        assert out["exit_code"] == -1
        assert out["blocked"] is False
        assert "不可用" in out["stderr"]

    def test_slot_falls_to_evidence_gap_when_env_missing(self, tmp_path):
        """B1 的反向：环境不具备时是诚实 evidence_gap，不是 validated。"""
        from app.services.p5_command_service import (
            P5CommandExecutionService, P5DetectedCommands,
        )
        cmds = P5DetectedCommands(project_id="p", project_type="dotnet",
                                  toolchain={"available": False,
                                             "reason": "Docker 守护不可达",
                                             "probe": {"docker": "unreachable"}})
        with patch("app.services.p5_command_service.workspace_path", return_value=tmp_path), \
             patch("app.services.p5_command_service.resolve_default_remote_host_id",
                   return_value=None):
            results = P5CommandExecutionService().execute_all_conditional("p", cmds)
        by_slot = {r.slot_id: r for r in results}
        assert by_slot["build_verified"].status == "evidence_gap"
        assert by_slot["build_verified"].toolchain_unavailable is True
        assert "Docker 守护不可达" in by_slot["build_verified"].failure_reason
        for r in results:
            assert r.status not in ("validated", "available")


# ── (d) R19-1-06 安全边界：最小挂载 / 非 root / 硬化项如实自述 ─────────────────

class TestSecurityBoundary:

    def _provider(self, td: str):
        from app.services.execution_provider import ToolchainContainerExecutionProvider
        return ToolchainContainerExecutionProvider(
            image_ref="reg/x:1", src_dir=td, build_dir=f"{td}/b", package_cache_dir=f"{td}/c",
            container_paths={"src": "/src", "build": "/build", "package_cache": "/nuget"},
            limits={"mem_limit": "2g", "pids_limit": 512, "cpu_count": 2},
            network_mode="bridge", network_weakened=True,
            network_note="构建需访问包源 ⇒ 出网为弱化项，未做 egress 白名单")

    def test_exactly_three_mounts_and_src_is_readonly(self, tmp_path):
        vols = self._provider(str(tmp_path)).volumes()
        assert len(vols) == 3, "只允许 3 处挂载（src ro / build rw / package_cache rw）"
        modes = {v["bind"]: v["mode"] for v in vols.values()}
        assert modes["/src"] == "ro", "构建输入必须 ro（不篡改平台产物、不落 obj/bin）"
        assert modes["/build"] == "rw" and modes["/nuget"] == "rw"

    def test_never_mounts_docker_socket_or_credential_paths(self, tmp_path):
        vols = self._provider(str(tmp_path)).volumes()
        joined = " ".join(list(vols.keys()) + [v["bind"] for v in vols.values()])
        for forbidden in ("docker.sock", "/.ssh", "/.gnupg", "/.nuget", "/.env", "/etc", "/proc",
                          "/sys", "/dev"):
            assert forbidden not in joined, f"绝不挂载 {forbidden}"

    def test_hardening_keeps_everything_except_network_and_says_so(self, tmp_path):
        h = self._provider(str(tmp_path)).hardening()
        assert h["profile"] == "toolchain_build"
        assert h["cap_drop"] == ["ALL"]
        assert h["security_opt"] == ["no-new-privileges:true"]
        assert h["read_only_rootfs"] is True
        assert h["run_as_non_root"] is True
        assert not h["user"].startswith("0:"), "容器内必须非 root"
        assert h["mem_limit"] and h["pids_limit"] and h["cpu_count"]
        assert h["published_ports"] == [], "不发布任何端口（不触碰端口标准 §10-19）"
        assert h["docker_socket_mounted"] is False
        # 弱化项必须如实标注，且**只有**网络一项
        assert h["weakened_items"] == ["network"]
        assert h["network_weakened"] is True
        assert "白名单" in h["network_note"], "必须如实说明未做 egress 白名单"

    def test_deny_list_still_applies_in_new_channel(self, tmp_path):
        """新通道不得因为"平台内部可信"而放宽 L5 安检。"""
        import asyncio
        out = asyncio.run(self._provider(str(tmp_path)).execute("sudo rm -rf /"))
        assert out["blocked"] is True
        assert out["risk_level"] == "L5"
        assert out["gate_required"] is True          # D-034：L5 须可裁决，不静默 block

    def test_untrusted_snippet_hardening_baseline_untouched(self):
        """既有强隔离档【一字不改】（Q-R19-1-1 化解口径①）。"""
        src = Path(__file__).resolve().parents[1] / "app/services/execution_provider.py"
        text = src.read_text(encoding="utf-8")
        body = text[text.index("class ContainerExecutionProvider"):
                    text.index("class ToolchainContainerExecutionProvider")]
        for token in ('network_mode="none"', "read_only=True", 'cap_drop=["ALL"]',
                      '"no-new-privileges:true"', 'mem_limit="256m"', "pids_limit=64",
                      'user="10002"', 'image="rebuild-execution-sandbox:local"'):
            assert token in body, f"untrusted_snippet 档的硬化基线被改动：{token}"

    def test_compose_docker_sock_mount_stays_commented(self):
        compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
        for line in compose.read_text(encoding="utf-8").splitlines():
            if "/var/run/docker.sock" in line:
                assert line.lstrip().startswith("#"), (
                    "docker-compose.yml 中的 docker.sock 挂载必须保持注释、不得启用")

    def test_no_sample_framework_or_image_literals_in_python(self):
        """AGENTS §10-26：样本实例值不得硬编码进平台代码（一律来自 yaml / 项目文件）。

        用 tokenize 只检查【代码取值】：注释与字符串/文档串里出现属说明性文字，不是取值。
        """
        import io
        import tokenize
        app = Path(__file__).resolve().parents[1] / "app"
        pat = re.compile(r"net\d+\.\d+|mcr\.microsoft\.com")
        offenders = []
        for py in app.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            if not pat.search(text):
                continue
            try:
                toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
            except (tokenize.TokenError, IndentationError, SyntaxError):
                continue
            for tok in toks:
                if tok.type in (tokenize.COMMENT, tokenize.STRING):
                    continue
                if pat.search(tok.string):
                    offenders.append(f"{py.name}:{tok.start[0]}: {tok.string[:80]}")
        assert not offenders, "Python 代码内不得出现目标框架/镜像字面量：\n" + "\n".join(offenders)


# ── (e) B1/B2/B3：通道感知 + 两阶段 + 显式项目路径 ────────────────────────────

class TestChannelAwareCommandGeneration:

    def _probe(self, tmp_path):
        return {
            "available": True,
            "image_ref": "reg/sdk:x",
            "build_root": str(tmp_path / "output_code"),
            "solutions": [],
            "projects": [
                {"path": "App/App.csproj", "target_frameworks": ["nX"], "is_test": False},
                {"path": "App.Tests/App.Tests.csproj", "target_frameworks": ["nX"], "is_test": True},
            ],
            "container_paths": {"src": "/src", "build": "/build", "package_cache": "/nuget"},
            "package_config": {"filename": "NuGet.Config", "content": "<configuration/>"},
            "limits": {"timeout_s": 900},
            "probe": {"image_digest": "reg/sdk@sha256:abc"},
        }

    def _detect(self, tmp_path, probe):
        from app.services.p5_command_service import P5CommandDetectionService
        _csproj(tmp_path, "output_code/App/App.csproj")
        with patch("app.services.p5_command_service.workspace_path", return_value=tmp_path), \
             patch("app.services.p5_command_service.shutil.which", return_value=None), \
             patch("app.services.toolchain_resolver.probe_toolchain", return_value=probe):
            return P5CommandDetectionService().detect_commands("p")

    def test_b1_build_cmd_no_longer_requires_host_sdk(self, tmp_path):
        """B1：宿主无 SDK 时，build_cmd 不再恒 None（否则必落 needs_user_input）。"""
        res = self._detect(tmp_path, self._probe(tmp_path))
        assert res.dotnet_sdk_present is False       # 宿主确实没 SDK（事实如实）
        assert res.execution_channel == "toolchain_container"
        assert res.build_cmd is not None
        assert not any(n["slot"] == "build" for n in res.needs_user_input)

    def test_b2_build_split_into_restore_and_no_restore_build(self, tmp_path):
        """B2：单条 dotnet build 在 restore 失败即中止 ⇒ 必须拆两阶段。"""
        res = self._detect(tmp_path, self._probe(tmp_path))
        stages = res.slot_stages["build_verified"]
        names = [s["stage"] for s in stages]
        assert any(n.startswith("restore:") for n in names)
        assert any(n.startswith("build:") for n in names)
        # restore 阶段全部排在 build 阶段之前（先收依赖错误，再收编译错误）
        assert max(i for i, n in enumerate(names) if n.startswith("restore:")) < \
               min(i for i, n in enumerate(names) if n.startswith("build:"))
        build_cmds = [s["command"] for s in stages if s["stage"].startswith("build:")]
        assert all("--no-restore" in c for c in build_cmds)

    def test_b3_every_command_carries_explicit_project_path(self, tmp_path):
        """B3：构建根下无 .sln，裸命令会报 MSB1003 伪失败 ⇒ 必须显式传项目路径。"""
        res = self._detect(tmp_path, self._probe(tmp_path))
        for slot_stages in res.slot_stages.values():
            for st in slot_stages:
                assert ".csproj" in st["command"] or ".sln" in st["command"], st["command"]
        assert res.command_cwd == str(tmp_path / "output_code")

    def test_outputs_redirected_off_the_readonly_source_tree(self, tmp_path):
        """ro 挂载 + 输出重定向：构建不往 output_code 落 obj/bin（R19-3-01 机制保障）。"""
        res = self._detect(tmp_path, self._probe(tmp_path))
        for st in res.slot_stages["build_verified"]:
            if st["stage"].startswith("build:"):
                assert "-p:BaseIntermediateOutputPath=/build/" in st["command"]
                assert "-p:BaseOutputPath=/build/" in st["command"]

    def test_test_stage_only_for_real_test_projects(self, tmp_path):
        res = self._detect(tmp_path, self._probe(tmp_path))
        test_stages = res.slot_stages.get("tests_pass") or []
        assert len(test_stages) == 1
        assert "App.Tests" in test_stages[0]["command"]

    def test_host_sdk_path_behaviour_unchanged(self, tmp_path):
        """G-8 无行为回退：宿主有 SDK 时命令与既有基线一字不差。"""
        from app.services.p5_command_service import P5CommandDetectionService
        _csproj(tmp_path, "output_code/App/App.csproj")
        with patch("app.services.p5_command_service.workspace_path", return_value=tmp_path), \
             patch("app.services.p5_command_service.shutil.which", return_value="/usr/bin/dotnet"):
            res = P5CommandDetectionService().detect_commands("p")
        assert res.dotnet_sdk_present is True
        assert res.execution_channel == "host_sdk"
        assert res.build_cmd == "dotnet build -warnaserror"
        assert res.test_cmd == "dotnet test --nologo"
        assert res.static_check_cmd == "dotnet format --verify-no-changes"
        assert res.slot_stages == {}

    def test_kill_switch_disables_container_channel(self, tmp_path, monkeypatch):
        from app.services.p5_command_service import P5CommandDetectionService
        monkeypatch.setenv("REBUILD_TOOLCHAIN_CONTAINER_ENABLED", "0")
        _csproj(tmp_path, "output_code/App/App.csproj")
        with patch("app.services.p5_command_service.workspace_path", return_value=tmp_path), \
             patch("app.services.p5_command_service.shutil.which", return_value=None):
            res = P5CommandDetectionService().detect_commands("p")
        assert res.execution_channel is None
        assert res.build_cmd is None
        assert res.toolchain.get("available") is False


# ── (f) B4/B5：诊断与 gate_reason 必须真的走到报告 ────────────────────────────

class TestReportSerialization:

    def test_b4_slot_to_dict_serializes_details(self):
        """B4（最致命）：旧 slot_to_dict 不序列化 details ⇒ NU1101/CS 到不了报告。"""
        from app.services.p5_validation_plan import P5ValidationSlot, slot_to_dict
        slot = P5ValidationSlot(slot_id="build_verified", slot_type="conditional",
                                status="validation_failed")
        slot.details = {
            "stdout_tail": REAL_NU1101,
            "diagnostics": bd.parse_diagnostics(REAL_NU1101),
            "diagnostics_summary": bd.summarize_diagnostics(bd.parse_diagnostics(REAL_NU1101)),
            "execution": {"image_ref": "reg/x:1", "image_digest": "reg/x@sha256:abc"},
            "elapsed_ms": 1234,
        }
        d = slot_to_dict(slot)
        assert "details" in d
        assert d["details"]["diagnostics"][0]["code"] == "NU1101"
        # stdout_summary/stderr_summary 从"永远为 null 的死字段"变成真实回填
        assert "NU1101" in d["stdout_summary"]
        assert d["duration_ms"] == 1234

    def test_b4_report_json_contains_real_error_code(self):
        import json
        from app.services.p5_validation_plan import (
            create_p5_validation_plan, slot_to_dict, plan_to_dict,
        )
        plan = create_p5_validation_plan("p", "r")
        slot = plan.get_slot("build_verified")
        slot.details = {"stdout_tail": REAL_NU1101,
                        "diagnostics": bd.parse_diagnostics(REAL_NU1101)}
        blob = json.dumps(plan_to_dict(plan), ensure_ascii=False)
        assert "NU1101" in blob
        assert slot_to_dict(slot)["details"]["diagnostics"]

    def test_b5_gate_reason_and_diagnostics_flow_into_conditional_details(self):
        """B5：前端 StagePageP5 早已渲染 gate_reason，后端此前从未提供。"""
        src = Path(__file__).resolve().parents[1] / "app/graph/stage_handlers.py"
        text = src.read_text(encoding="utf-8")
        block = text[text.index('conditional_details.append({'):]
        block = block[:block.index("\n            })")]
        for key in ('"gate_reason"', '"diagnostics"', '"diagnostics_summary"', '"stages"',
                    '"execution"', '"stdout_tail"', '"stderr_tail"', '"evidence_refs"'):
            assert key in block, f"conditional_details 缺字段 {key}（B4/B5 未修全）"

    def test_verification_service_passes_diagnostics_through(self, tmp_path):
        from app.services.p5_command_service import P5CommandResult, P5DetectedCommands
        from app.services.p5_verification_service import P5VerificationService
        er = P5CommandResult(slot_id="build_verified", command="dotnet restore App/App.csproj",
                             executed=True, exit_code=1, status="validation_failed",
                             stdout=REAL_NU1101, failure_reason="4 阶段构建：退出码 1")
        er.diagnostics = bd.parse_diagnostics(REAL_NU1101)
        er.diagnostics_summary = bd.summarize_diagnostics(er.diagnostics)
        er.execution = {"image_ref": "reg/x:1", "image_digest": "reg/x@sha256:abc"}
        er.stages = [{"stage": "restore:App/App.csproj", "exit_code": 1}]
        with patch("app.services.p5_command_service.P5CommandDetectionService.detect_commands",
                   return_value=P5DetectedCommands(project_id="p", project_type="dotnet")), \
             patch("app.services.p5_command_service.P5CommandExecutionService."
                   "execute_all_conditional", return_value=[er]), \
             patch.object(P5VerificationService, "_persist_build_log", return_value=None):
            results = P5VerificationService()._noop_guard() if False else \
                P5VerificationService().verify_conditional_slots("p")
        d = results[0].details
        assert d["diagnostics"][0]["code"] == "NU1101"
        assert d["diagnostics_summary"]["by_category"]["dependency"] == 1
        assert d["execution"]["image_digest"].endswith("abc")
        assert d["stages"]
        assert "NU1101" in d["stdout_tail"]


# ── (g) Protocol 一致性：B-P5-CONTAINER-CWD ───────────────────────────────────

class TestProviderProtocol:

    def test_container_provider_accepts_cwd_without_typeerror(self):
        """Q-R19-1-4：旧签名缺 cwd ⇒ P5_EXECUTION_MODE=container 会 TypeError → 伪失败。"""
        import asyncio
        import inspect
        import docker
        from docker.errors import DockerException
        from app.services.execution_provider import ContainerExecutionProvider
        sig = inspect.signature(ContainerExecutionProvider.execute)
        assert "cwd" in sig.parameters
        with patch.object(docker, "from_env", side_effect=DockerException("no daemon")):
            out = asyncio.run(ContainerExecutionProvider().execute(
                "echo hi", language="bash", cwd="/tmp"))
        assert out["exit_code"] == -1                # 环境不可用，但不是 TypeError 伪失败
        assert "TypeError" not in out["stderr"]

    def test_factory_returns_toolchain_provider_and_rejects_bad_options(self, tmp_path):
        from app.services.execution_provider import (
            get_execution_provider, ToolchainContainerExecutionProvider,
        )
        p = get_execution_provider(mode="toolchain_container", provider_options={
            "image_ref": "reg/x:1", "src_dir": str(tmp_path),
            "build_dir": str(tmp_path / "b"), "package_cache_dir": str(tmp_path / "c")})
        assert isinstance(p, ToolchainContainerExecutionProvider)
        assert p.name == "toolchain_container"
        with pytest.raises(ValueError):
            get_execution_provider(mode="toolchain_container", provider_options={"bogus": 1})
        with pytest.raises(ValueError):
            get_execution_provider(mode="toolchain_container",
                                   provider_options={"image_ref": "", "src_dir": "/x",
                                                     "build_dir": "/x", "package_cache_dir": "/x"})

    def test_existing_modes_unchanged(self):
        from app.services.execution_provider import (
            get_execution_provider, LocalSubprocessExecutionProvider,
            WorkspaceLocalExecutionProvider, ContainerExecutionProvider,
        )
        assert isinstance(get_execution_provider(mode="local"), LocalSubprocessExecutionProvider)
        assert isinstance(get_execution_provider(mode="workspace_local"),
                          WorkspaceLocalExecutionProvider)
        assert isinstance(get_execution_provider(mode="container"), ContainerExecutionProvider)


# ── (h) 预热脚本与离线说明存在（R19-1-02 / R19-1-04 的运维前置件）──────────────

class TestPrewarmAssets:

    def test_pull_script_and_readme_exist(self):
        deploy = Path(__file__).resolve().parents[2] / "deploy" / "toolchain-images"
        assert (deploy / "pull.sh").exists()
        assert (deploy / "README.md").exists()

    def test_pull_script_refuses_unregistered_images(self):
        script = (Path(__file__).resolve().parents[2] / "deploy" / "toolchain-images"
                  / "pull.sh").read_text(encoding="utf-8")
        assert "is_known" in script and "host_allowed" in script
        # 预热脚本绝不挂载/改权限 docker.sock（只允许 docker CLI 调用宿主守护）
        assert "docker.sock:" not in script
        assert "-v /var/run/docker.sock" not in script
        assert "chmod" not in script and "systemctl" not in script
