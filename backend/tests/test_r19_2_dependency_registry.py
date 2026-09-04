"""R19-2 G2 依赖真实性校验 —— 定向测试。

覆盖验收项（交接/当前/V26.2-验收标准.md §3.2）：
  R19-2-01  三类 registry（NuGet/npm/Maven）存在性 + 版本可解析性
  R19-2-02  真跑锚点：臆造包 Microsoft.EntityFrameworkCore.Dm → package_not_found
            （真跑验证见 test_r19_2_live_p4_dependency_check.py，本文件为定向单测，
            httpx.MockTransport 零真实网络）
  R19-2-03  不越界：不改任何依赖清单一个字节
  R19-2-04  诚实降级：429/401/超时/DNS ≠ package_not_found；indeterminate 不缓存
  R19-2-05  产物 + Evidence 可被 P5/验收 Agent 读取（run_id 过滤不吞掉、artifacts 显式追加）

红线断言（本方案第一红线）：429 ≠ package_not_found；404 从不重试（attempts==1）。
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest import mock

import httpx
import pytest

from app.services import dependency_registry_service as dep_svc
from app.services import manifest_parsers as mp


@contextlib.contextmanager
def _patched_async_client(transport: httpx.BaseTransport):
    """把 httpx.AsyncClient() 无参构造重定向到给定 transport（零真实网络）。

    捕获打补丁前的真实类，避免 lambda 内再次调用 httpx.AsyncClient 时递归自指。
    """
    real_cls = httpx.AsyncClient
    with mock.patch("httpx.AsyncClient", lambda *a, **k: real_cls(transport=transport)):
        yield


@pytest.fixture(autouse=True)
def _clear_cache():
    dep_svc.clear_cache()
    yield
    dep_svc.clear_cache()


# ════════════════════════════════════════════════════════════════════════════
# 1.1 / 1.2  manifest_parsers —— 清单解析正例 + 负例（纯函数，无网络）
# ════════════════════════════════════════════════════════════════════════════

class TestManifestParsersPositive:

    def test_csproj_packagereference_with_line_numbers(self, tmp_path: Path):
        out_code = tmp_path / "output_code" / "MicroOA.Web"
        out_code.mkdir(parents=True)
        csproj = out_code / "MicroOA.Web.csproj"
        csproj.write_text(
            '<Project Sdk="Microsoft.NET.Sdk.Web">\n'
            "  <PropertyGroup>\n"
            "    <TargetFramework>net8.0</TargetFramework>\n"
            "  </PropertyGroup>\n"
            "  <ItemGroup>\n"
            '    <PackageReference Include="Microsoft.EntityFrameworkCore" Version="8.0.0" />\n'
            '    <PackageReference Include="Microsoft.EntityFrameworkCore.Dm" Version="8.0.0" />\n'
            "  </ItemGroup>\n"
            "</Project>\n", encoding="utf-8")
        result = mp.parse_manifest(csproj, tmp_path / "output_code")
        assert result["parse_status"] == "ok"
        assert result["ecosystem"] == "nuget"
        assert result["format"] == "packagereference"
        assert result["coordinate_count"] == 2
        dm = next(c for c in result["coordinates"] if c["id"] == "Microsoft.EntityFrameworkCore.Dm")
        assert dm["version"] == "8.0.0"
        assert dm["line"] == 7  # 实测锚点坐标随行号变化而变化，此处按本用例文本行号断言

    def test_packages_config_old_style(self, tmp_path: Path):
        out_code = tmp_path / "output_code"
        out_code.mkdir(parents=True)
        pc = out_code / "packages.config"
        pc.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<packages>\n"
            '  <package id="Newtonsoft.Json" version="13.0.3" targetFramework="net472" />\n'
            "</packages>\n", encoding="utf-8")
        result = mp.parse_manifest(pc, out_code)
        assert result["parse_status"] == "ok"
        assert result["ecosystem"] == "nuget"
        assert result["format"] == "packages_config"
        assert result["coordinates"][0]["id"] == "Newtonsoft.Json"
        assert result["coordinates"][0]["version"] == "13.0.3"

    def test_package_json_all_dependency_fields(self, tmp_path: Path):
        out_code = tmp_path / "output_code"
        out_code.mkdir(parents=True)
        pj = out_code / "package.json"
        pj.write_text(json.dumps({
            "dependencies": {"express": "4.18.2"},
            "devDependencies": {"jest": "^29.0.0"},
            "peerDependencies": {"react": "^18.0.0"},
            "optionalDependencies": {"fsevents": "~2.3.2"},
        }), encoding="utf-8")
        result = mp.parse_manifest(pj, out_code)
        assert result["parse_status"] == "ok"
        assert result["ecosystem"] == "npm"
        assert result["coordinate_count"] == 4
        ids = {c["id"] for c in result["coordinates"]}
        assert ids == {"express", "jest", "react", "fsevents"}

    def test_pom_xml_dependencies(self, tmp_path: Path):
        out_code = tmp_path / "output_code"
        out_code.mkdir(parents=True)
        pom = out_code / "pom.xml"
        pom.write_text(
            '<?xml version="1.0"?>\n<project>\n  <dependencies>\n'
            "    <dependency>\n      <groupId>org.springframework</groupId>\n"
            "      <artifactId>spring-core</artifactId>\n      <version>6.1.0</version>\n"
            "    </dependency>\n  </dependencies>\n</project>\n", encoding="utf-8")
        result = mp.parse_manifest(pom, out_code)
        assert result["parse_status"] == "ok"
        assert result["ecosystem"] == "maven"
        assert result["coordinates"][0]["id"] == "org.springframework:spring-core"
        assert result["coordinates"][0]["version"] == "6.1.0"

    def test_discover_manifests_excludes_rebuild_and_build_dirs(self, tmp_path: Path):
        out_code = tmp_path / "output_code"
        (out_code / "App").mkdir(parents=True)
        (out_code / "App" / "App.csproj").write_text("<Project/>", encoding="utf-8")
        (out_code / ".rebuild" / "build").mkdir(parents=True)
        (out_code / ".rebuild" / "build" / "ghost.csproj").write_text("<Project/>", encoding="utf-8")
        (out_code / "App" / "obj").mkdir(parents=True)
        (out_code / "App" / "obj" / "leftover.csproj").write_text("<Project/>", encoding="utf-8")
        found = mp.discover_manifests(out_code)
        assert len(found) == 1
        assert found[0].name == "App.csproj"


class TestManifestParsersNegative:

    def test_packages_config_llm_prose_not_xml_is_unparseable(self, tmp_path: Path):
        """Q-R19-2-4 实测样本：output_code/MicroOA.Web/packages.config 内容是 LLM 旁白
        文本、不是 XML。必须不抛异常、不静默跳过——诚实返回 manifest_unparseable。"""
        out_code = tmp_path / "output_code"
        out_code.mkdir(parents=True)
        pc = out_code / "packages.config"
        pc.write_text(
            "脚手架已完整存在，无需重复创建。以下是 `output_code/MicroOA.Web/` 的完整"
            "工程结构确认：**工程根文件：** MicroOA.Web.csproj — .NET 8 SDK Web 项目",
            encoding="utf-8")
        result = mp.parse_manifest(pc, out_code)
        assert result["parse_status"] == "manifest_unparseable"
        assert result["coordinate_count"] == 0
        assert result["coordinates"] == []
        assert result["error"]
        # sha256/bytes 仍应给出（不是完全空产出——文件真实存在过）
        assert result["sha256"]
        assert result["bytes"] > 0

    def test_package_json_not_json_is_unparseable(self, tmp_path: Path):
        out_code = tmp_path / "output_code"
        out_code.mkdir(parents=True)
        pj = out_code / "package.json"
        pj.write_text("this is not json at all {{{", encoding="utf-8")
        result = mp.parse_manifest(pj, out_code)
        assert result["parse_status"] == "manifest_unparseable"

    def test_pom_xml_not_json_but_broken_xml_is_unparseable(self, tmp_path: Path):
        out_code = tmp_path / "output_code"
        out_code.mkdir(parents=True)
        pom = out_code / "pom.xml"
        pom.write_text("<project><dependencies><dependency>不闭合", encoding="utf-8")
        result = mp.parse_manifest(pom, out_code)
        assert result["parse_status"] == "manifest_unparseable"

    def test_parse_all_manifests_never_raises_on_mixed_valid_and_broken(self, tmp_path: Path):
        out_code = tmp_path / "output_code"
        (out_code / "A").mkdir(parents=True)
        (out_code / "A" / "A.csproj").write_text(
            '<Project><ItemGroup><PackageReference Include="X" Version="1.0.0" />'
            "</ItemGroup></Project>", encoding="utf-8")
        (out_code / "A" / "packages.config").write_text("not xml prose", encoding="utf-8")
        results = mp.parse_all_manifests(out_code)
        statuses = {r["parse_status"] for r in results}
        assert statuses == {"ok", "manifest_unparseable"}


# ════════════════════════════════════════════════════════════════════════════
# 私有源信号采集（§3.8.2）
# ════════════════════════════════════════════════════════════════════════════

class TestPrivateSourceSignals:

    def test_nuget_config_declares_private_source(self, tmp_path: Path):
        out_code = tmp_path / "output_code"
        out_code.mkdir(parents=True)
        (out_code / "NuGet.Config").write_text(
            '<?xml version="1.0"?>\n<configuration>\n  <packageSources>\n'
            '    <add key="nexus" value="https://nexus.example.internal/nuget/v3/index.json" />\n'
            "  </packageSources>\n</configuration>\n", encoding="utf-8")
        signals = mp.discover_private_source_signals(tmp_path)
        assert len(signals) == 1
        assert signals[0]["declared_hosts"] == ["nexus.example.internal"]

    def test_rebuild_dir_nuget_config_excluded(self, tmp_path: Path):
        """实测事实：MicroOA 唯一的 NuGet.Config 在 .rebuild/build/**（平台探针产物），
        按排除规则不计入私有源信号——否则 R19-2-02 锚点会被静默降级为 indeterminate。"""
        rebuild_dir = tmp_path / "output_code" / ".rebuild" / "build" / "probe"
        rebuild_dir.mkdir(parents=True)
        (rebuild_dir / "NuGet.Config").write_text(
            '<configuration><packageSources><add key="x" '
            'value="https://internal.example.com/v3/index.json" /></packageSources>'
            "</configuration>", encoding="utf-8")
        signals = mp.discover_private_source_signals(tmp_path)
        assert signals == []

    def test_nuget_org_only_source_is_not_a_private_signal(self, tmp_path: Path):
        out_code = tmp_path / "output_code"
        out_code.mkdir(parents=True)
        (out_code / "NuGet.Config").write_text(
            '<configuration><packageSources><add key="nuget.org" '
            'value="https://api.nuget.org/v3/index.json" /></packageSources></configuration>',
            encoding="utf-8")
        assert mp.discover_private_source_signals(tmp_path) == []

    def test_npmrc_registry_declaration(self, tmp_path: Path):
        out_code = tmp_path / "output_code"
        out_code.mkdir(parents=True)
        (out_code / ".npmrc").write_text(
            "registry=https://npm.internal.example.com/\n", encoding="utf-8")
        signals = mp.discover_private_source_signals(tmp_path)
        assert signals[0]["declared_hosts"] == ["npm.internal.example.com"]


# ════════════════════════════════════════════════════════════════════════════
# 1.3 / 1.4 / 1.5 / 1.7 / 1.8  dependency_registry_service —— HTTP 判据表
# （httpx.MockTransport，零真实网络）
# ════════════════════════════════════════════════════════════════════════════

def _coord(ecosystem: str, pkg_id: str, version: str) -> dict:
    return {"id": pkg_id, "version": version, "ecosystem": ecosystem,
           "manifest_ref": "output_code/test.csproj", "manifest_line": 1}


def _nuget_index_body(versions: list[str]) -> bytes:
    return json.dumps({
        "version": "3.0.0",
        "resources": [{"@id": "https://api.nuget.org/v3-flatcontainer/",
                       "@type": "PackageBaseAddress/3.0.0"}],
    }).encode() if versions is None else json.dumps({"versions": versions}).encode()


class TestNuGetJudgment:

    async def test_resolvable_real_package_exact_version(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v3/index.json":
                return httpx.Response(200, json={"resources": [
                    {"@id": "https://api.nuget.org/v3-flatcontainer/",
                     "@type": "PackageBaseAddress/3.0.0"}]})
            if "microsoft.entityframeworkcore/index.json" in request.url.path:
                return httpx.Response(200, json={"versions": ["7.0.0", "8.0.0", "8.0.1"]})
            return httpx.Response(404)
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_nuget(
                client, _coord("nuget", "Microsoft.EntityFrameworkCore", "8.0.0"), budget)
        assert r["conclusion"] == "resolvable"
        assert r["attempts"] == 1

    async def test_version_normalization_8_0_0_0_matches_8_0_0(self):
        """实测事实：NuGet versions[] 含 '8.0.0' 但不含 '8.0.0.0'；csproj 里 8.0.0.0 是合法
        MSBuild 写法。不归一化会把真实包误判为 version_not_found。"""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v3/index.json":
                return httpx.Response(200, json={"resources": [
                    {"@id": "https://api.nuget.org/v3-flatcontainer/",
                     "@type": "PackageBaseAddress/3.0.0"}]})
            return httpx.Response(200, json={"versions": ["8.0.0"]})
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_nuget(
                client, _coord("nuget", "Microsoft.EntityFrameworkCore", "8.0.0.0"), budget)
        assert r["conclusion"] == "resolvable"

    async def test_package_not_found_404_is_the_r19_2_02_anchor_shape(self):
        """R19-2-02 锚点形状：臆造包 404 → package_not_found（本用例零真实网络，
        真跑锚点见 test_r19_2_live_p4_dependency_check.py）。"""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v3/index.json":
                return httpx.Response(200, json={"resources": [
                    {"@id": "https://api.nuget.org/v3-flatcontainer/",
                     "@type": "PackageBaseAddress/3.0.0"}]})
            return httpx.Response(404, content=b'<Error><Code>BlobNotFound</Code></Error>')
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_nuget(
                client, _coord("nuget", "Microsoft.EntityFrameworkCore.Dm", "8.0.0"), budget)
        assert r["conclusion"] == "package_not_found"
        assert r["http_status"] == 404
        assert r["attempts"] == 1  # 404 从不重试

    async def test_version_not_found_when_package_exists(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v3/index.json":
                return httpx.Response(200, json={"resources": [
                    {"@id": "https://api.nuget.org/v3-flatcontainer/",
                     "@type": "PackageBaseAddress/3.0.0"}]})
            return httpx.Response(200, json={"versions": ["8.0.0"]})
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_nuget(
                client, _coord("nuget", "Microsoft.EntityFrameworkCore", "99.99.99"), budget)
        assert r["conclusion"] == "version_not_found"

    async def test_msbuild_property_version_is_indeterminate_not_guessed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v3/index.json":
                return httpx.Response(200, json={"resources": [
                    {"@id": "https://api.nuget.org/v3-flatcontainer/",
                     "@type": "PackageBaseAddress/3.0.0"}]})
            return httpx.Response(200, json={"versions": ["8.0.0"]})
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_nuget(
                client, _coord("nuget", "Microsoft.EntityFrameworkCore", "$(EfVersion)"), budget)
        assert r["conclusion"] == "indeterminate"
        assert r["reason_code"] == "version_indeterminate"


class TestHttpSemanticsRedLines:
    """核心红线：404 ≠ 401 ≠ 429 ≠ 超时——语义完全不同，绝不可混淆。"""

    async def test_429_is_never_package_not_found_first_red_line(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v3/index.json":
                return httpx.Response(200, json={"resources": [
                    {"@id": "https://api.nuget.org/v3-flatcontainer/",
                     "@type": "PackageBaseAddress/3.0.0"}]})
            calls["n"] += 1
            return httpx.Response(429, headers={"Retry-After": "0"})
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_nuget(
                client, _coord("nuget", "Microsoft.EntityFrameworkCore", "8.0.0"), budget)
        assert r["conclusion"] == "indeterminate"
        assert r["reason_code"] == "rate_limited"
        assert r["conclusion"] != "package_not_found"
        assert calls["n"] >= 2  # 确认真的重试了

    async def test_401_is_never_package_not_found(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v3/index.json":
                return httpx.Response(200, json={"resources": [
                    {"@id": "https://api.nuget.org/v3-flatcontainer/",
                     "@type": "PackageBaseAddress/3.0.0"}]})
            return httpx.Response(401)
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_nuget(
                client, _coord("nuget", "Microsoft.EntityFrameworkCore", "8.0.0"), budget)
        assert r["conclusion"] == "indeterminate"
        assert r["reason_code"] == "auth_required"

    async def test_retry_after_over_20s_does_not_wait_judges_rate_limited(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v3/index.json":
                return httpx.Response(200, json={"resources": [
                    {"@id": "https://api.nuget.org/v3-flatcontainer/",
                     "@type": "PackageBaseAddress/3.0.0"}]})
            return httpx.Response(429, headers={"Retry-After": "30"})
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            import time
            t0 = time.monotonic()
            r = await dep_svc._check_nuget(
                client, _coord("nuget", "Microsoft.EntityFrameworkCore", "8.0.0"), budget)
            elapsed = time.monotonic() - t0
        assert r["reason_code"] == "rate_limited"
        assert elapsed < 5  # 没有真的等 30s

    async def test_5xx_is_registry_error_not_package_not_found(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v3/index.json":
                return httpx.Response(200, json={"resources": [
                    {"@id": "https://api.nuget.org/v3-flatcontainer/",
                     "@type": "PackageBaseAddress/3.0.0"}]})
            return httpx.Response(503)
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_nuget(
                client, _coord("nuget", "Microsoft.EntityFrameworkCore", "8.0.0"), budget)
        assert r["reason_code"] == "registry_error"

    async def test_network_timeout_reason_code(self):
        def handler(request: httpx.Request):
            raise httpx.TimeoutException("boom")
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_nuget(
                client, _coord("nuget", "Microsoft.EntityFrameworkCore", "8.0.0"), budget)
        assert r["conclusion"] == "indeterminate"
        assert r["reason_code"] == "network_timeout"

    async def test_network_unavailable_reason_code(self):
        def handler(request: httpx.Request):
            raise httpx.ConnectError("dns failed")
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_nuget(
                client, _coord("nuget", "Microsoft.EntityFrameworkCore", "8.0.0"), budget)
        assert r["reason_code"] == "network_unavailable"

    async def test_cross_host_redirect_is_unexpected_redirect_not_followed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v3/index.json":
                return httpx.Response(200, json={"resources": [
                    {"@id": "https://api.nuget.org/v3-flatcontainer/",
                     "@type": "PackageBaseAddress/3.0.0"}]})
            return httpx.Response(302, headers={"Location": "https://evil.example.com/steal"})
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_nuget(
                client, _coord("nuget", "Microsoft.EntityFrameworkCore", "8.0.0"), budget)
        assert r["reason_code"] == "unexpected_redirect"

    async def test_same_host_redirect_is_followed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v3/index.json":
                return httpx.Response(200, json={"resources": [
                    {"@id": "https://api.nuget.org/v3-flatcontainer/",
                     "@type": "PackageBaseAddress/3.0.0"}]})
            if request.url.path.endswith("/index.json") and "moved" not in str(request.url):
                return httpx.Response(302, headers={"Location":
                    "https://api.nuget.org/v3-flatcontainer/moved/index.json"})
            return httpx.Response(200, json={"versions": ["8.0.0"]})
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_nuget(
                client, _coord("nuget", "Microsoft.EntityFrameworkCore", "8.0.0"), budget)
        assert r["conclusion"] == "resolvable"

    async def test_budget_exhausted_is_honest_not_default_pass(self):
        def handler(request: httpx.Request):
            raise httpx.ConnectError("down")
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(0)  # 已耗尽
            r = await dep_svc._check_nuget(
                client, _coord("nuget", "Microsoft.EntityFrameworkCore", "8.0.0"), budget)
        assert r["conclusion"] == "indeterminate"
        assert r["reason_code"] == "budget_exhausted"


class TestNpmDoubleShaped404:
    """实测事实：npm 404 有两种响应体形状——包不存在是对象 {"error":"Not found"}，
    版本不存在是【裸字符串】"version not found: X"。json.loads 后必须先判类型，
    否则 dict.get 对字符串抛 AttributeError，会被错误吞成 indeterminate。"""

    async def test_package_not_found_object_shaped_404(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "Not found"})
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_npm(client, _coord("npm", "left-pad-nonexistent-xyz", "1.0.0"),
                                         budget)
        assert r["conclusion"] == "package_not_found"

    async def test_version_not_found_bare_string_shaped_404(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/express":
                return httpx.Response(200, json={"name": "express", "versions": {"4.18.2": {}}})
            # 版本端点：裸字符串 404 body（不是对象！）
            return httpx.Response(404, content=b'"version not found: 99.99.99"')
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_npm(client, _coord("npm", "express", "99.99.99"), budget)
        assert r["conclusion"] == "version_not_found"  # 未抛 AttributeError

    async def test_npm_resolvable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/express":
                return httpx.Response(200, json={"name": "express"})
            return httpx.Response(200, json={"version": "4.18.2"})
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_npm(client, _coord("npm", "express", "4.18.2"), budget)
        assert r["conclusion"] == "resolvable"

    async def test_npm_semver_range_is_indeterminate_per_q_r19_2_1(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"name": "express"})
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_npm(client, _coord("npm", "express", "^4.18.0"), budget)
        assert r["conclusion"] == "indeterminate"
        assert r["reason_code"] == "version_indeterminate"


class TestMavenJudgment:

    async def test_maven_resolvable_via_head_gav_pom(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("maven-metadata.xml"):
                return httpx.Response(200, content=(
                    b"<metadata><versioning><versions>"
                    b"<version>6.1.0</version></versions></versioning></metadata>"))
            return httpx.Response(200, content=b"")  # HEAD GAV pom
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_maven(
                client, _coord("maven", "org.springframework:spring-core", "6.1.0"), budget)
        assert r["conclusion"] == "resolvable"

    async def test_maven_ga_not_found(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_maven(
                client, _coord("maven", "com.example:nonexistent", "1.0.0"), budget)
        assert r["conclusion"] == "package_not_found"

    async def test_maven_bom_property_version_is_indeterminate(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=(
                b"<metadata><versioning><versions>"
                b"<version>6.1.0</version></versions></versioning></metadata>"))
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r = await dep_svc._check_maven(
                client, _coord("maven", "org.springframework:spring-core", "${spring.version}"),
                budget)
        assert r["conclusion"] == "indeterminate"
        assert r["reason_code"] == "version_indeterminate"


# ════════════════════════════════════════════════════════════════════════════
# 1.6  缓存：确定结论命中缓存；indeterminate 不缓存
# ════════════════════════════════════════════════════════════════════════════

class TestCaching:

    async def test_resolvable_conclusion_is_cached(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if request.url.path == "/v3/index.json":
                return httpx.Response(200, json={"resources": [
                    {"@id": "https://api.nuget.org/v3-flatcontainer/",
                     "@type": "PackageBaseAddress/3.0.0"}]})
            return httpx.Response(200, json={"versions": ["8.0.0"]})
        transport = httpx.MockTransport(handler)
        coord = _coord("nuget", "Microsoft.EntityFrameworkCore", "8.0.0")
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r1 = await dep_svc.check_coordinate(client, coord, budget)
            calls_after_first = calls["n"]
            r2 = await dep_svc.check_coordinate(client, coord, budget)
        assert r1["conclusion"] == r2["conclusion"] == "resolvable"
        assert r2["cache"] == "hit"
        assert calls["n"] == calls_after_first  # 第二次未发起新请求

    async def test_indeterminate_is_never_cached_so_next_query_really_retries(self):
        """陷阱：查完即缓存会把一次限流固化 15 分钟、持续误报。indeterminate 必须
        每次都重新真查（不被"上次限流"污染这次的真实结论）。"""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v3/index.json":
                return httpx.Response(200, json={"resources": [
                    {"@id": "https://api.nuget.org/v3-flatcontainer/",
                     "@type": "PackageBaseAddress/3.0.0"}]})
            calls["n"] += 1
            if calls["n"] <= 3:  # 第一次查询：反复 429 直到重试耗尽
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, json={"versions": ["8.0.0"]})  # 第二次查询：恢复正常
        transport = httpx.MockTransport(handler)
        coord = _coord("nuget", "Microsoft.EntityFrameworkCore", "8.0.0")
        async with httpx.AsyncClient(transport=transport) as client:
            budget = dep_svc._Budget(30)
            r1 = await dep_svc.check_coordinate(client, coord, budget)
            r2 = await dep_svc.check_coordinate(client, coord, budget)
        assert r1["conclusion"] == "indeterminate"
        assert r1["reason_code"] == "rate_limited"
        assert r2["conclusion"] == "resolvable"  # 第二次真的重新查询，未被第一次污染
        assert r2["cache"] == "miss"


# ════════════════════════════════════════════════════════════════════════════
# 1.10  私有源信号 → 结论降级
# ════════════════════════════════════════════════════════════════════════════

class TestPrivateSourceDowngrade:

    async def test_package_not_found_downgrades_to_indeterminate_when_private_source_declared(
            self, tmp_path: Path):
        out_code = tmp_path / "output_code"
        out_code.mkdir(parents=True)
        (out_code / "App.csproj").write_text(
            '<Project><ItemGroup><PackageReference Include="Internal.Only.Pkg" '
            'Version="1.0.0" /></ItemGroup></Project>', encoding="utf-8")
        (out_code / "NuGet.Config").write_text(
            '<configuration><packageSources><add key="nexus" '
            'value="https://nexus.internal/v3/index.json" /></packageSources></configuration>',
            encoding="utf-8")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v3/index.json":
                return httpx.Response(200, json={"resources": [
                    {"@id": "https://api.nuget.org/v3-flatcontainer/",
                     "@type": "PackageBaseAddress/3.0.0"}]})
            return httpx.Response(404)
        transport = httpx.MockTransport(handler)

        with _patched_async_client(transport):
            doc = await dep_svc.run_dependency_check("test-proj", tmp_path, run_id="run-x")
        coord = doc["coordinates"][0]
        assert coord["conclusion"] == "indeterminate"
        assert coord["reason_code"] == "private_source_possible"
        assert doc["private_source_signals"]


# ════════════════════════════════════════════════════════════════════════════
# 1.11 / 1.13  不越界 + 零产物场景仍产依赖校验产物
# ════════════════════════════════════════════════════════════════════════════

class TestBoundaryAndZeroArtifactScenario:

    async def test_run_dependency_check_never_mutates_manifests(self, tmp_path: Path, monkeypatch):
        out_code = tmp_path / "output_code"
        out_code.mkdir(parents=True)
        csproj = out_code / "App.csproj"
        original = ('<Project><ItemGroup><PackageReference Include="Ghost.Pkg.Xyz" '
                    'Version="1.0.0" /></ItemGroup></Project>')
        csproj.write_text(original, encoding="utf-8")
        import hashlib
        before_sha = hashlib.sha256(csproj.read_bytes()).hexdigest()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v3/index.json":
                return httpx.Response(200, json={"resources": [
                    {"@id": "https://api.nuget.org/v3-flatcontainer/",
                     "@type": "PackageBaseAddress/3.0.0"}]})
            return httpx.Response(404)
        transport = httpx.MockTransport(handler)
        with _patched_async_client(transport):
            await dep_svc.run_dependency_check("test-proj", tmp_path, run_id="run-x")
        after_sha = hashlib.sha256(csproj.read_bytes()).hexdigest()
        assert before_sha == after_sha

    async def test_disabled_via_config_produces_not_checked_without_network(
            self, tmp_path: Path, monkeypatch):
        out_code = tmp_path / "output_code"
        out_code.mkdir(parents=True)
        (out_code / "App.csproj").write_text(
            '<Project><ItemGroup><PackageReference Include="X" Version="1.0.0" />'
            "</ItemGroup></Project>", encoding="utf-8")
        from app.core.config import settings as app_settings
        # Settings 是 frozen pydantic 模型，不能就地 setattr；改为 monkeypatch
        # dependency_registry_service 模块内的 settings 名（模块内按名字查找属性）。
        patched_settings = app_settings.model_copy(update={"dependency_check_enabled": False})
        monkeypatch.setattr(dep_svc, "settings", patched_settings)
        called = {"n": 0}

        class _ExplodingTransport(httpx.BaseTransport):
            def handle_request(self, request):
                called["n"] += 1
                raise AssertionError("不应发起任何网络请求")
        with _patched_async_client(_ExplodingTransport()):
            doc = await dep_svc.run_dependency_check("test-proj", tmp_path, run_id="run-x")
        assert called["n"] == 0
        assert doc["coordinates"][0]["conclusion"] == "not_checked"
        assert doc["coordinates"][0]["reason_code"] == "skipped_by_config"


# ════════════════════════════════════════════════════════════════════════════
# 序列化陷阱自查（§3.10）：Evidence extra 带 run_id；产物字段无白名单裁剪
# ════════════════════════════════════════════════════════════════════════════

class TestSerializationTrapsAvoidance:

    def test_evidence_write_carries_run_id_survives_d111_filter(self, tmp_path, monkeypatch):
        """陷阱 B：D-111 幽灵过滤要求 extra.run_id == 本次 run_id，否则被静默剔除。"""
        from app.services.aet_service import AETService
        monkeypatch.setattr("app.services.aet_service.workspace_path", lambda pid: tmp_path)
        (tmp_path / "evidence").mkdir(parents=True, exist_ok=True)
        svc = AETService(None)
        ev = svc.write_evidence(
            "proj-x", evidence_id="ev-p4-depcheck-run1234", evidence_type="dependency_resolution",
            status="candidate", source="p4", stage="p4", claim="test",
            extra={"run_id": "run-1234abcd", "artifact_ref": "artifacts/p4/p4_dependency_check.json"})
        assert ev["run_id"] == "run-1234abcd"
        listed = svc.list_evidence("proj-x", stage="p4")
        filtered = [e for e in listed if e.get("run_id") == "run-1234abcd"]
        assert len(filtered) == 1
        assert filtered[0]["evidence_id"] == "ev-p4-depcheck-run1234"

    async def test_dependency_check_document_has_no_whitelist_drop(self, tmp_path: Path):
        """陷阱 D 的反面验证：本方案不复用 DimensionCapability.to_dict() 的 9 键白名单——
        自建 dict 应保留全部自定义字段（counts/coordinates/private_source_signals/
        boundary_note/independence_note），不经过任何白名单裁剪。"""
        out_code = tmp_path / "output_code"
        out_code.mkdir(parents=True)
        (out_code / "App.csproj").write_text(
            '<Project><ItemGroup><PackageReference Include="Ghost" Version="1.0.0" />'
            "</ItemGroup></Project>", encoding="utf-8")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v3/index.json":
                return httpx.Response(200, json={"resources": [
                    {"@id": "https://api.nuget.org/v3-flatcontainer/",
                     "@type": "PackageBaseAddress/3.0.0"}]})
            return httpx.Response(404)
        transport = httpx.MockTransport(handler)
        with _patched_async_client(transport):
            doc = await dep_svc.run_dependency_check("test-proj", tmp_path, run_id="run-x")
        for key in ("counts", "coordinates", "private_source_signals", "boundary_note",
                   "independence_note", "manifests", "evidence_gaps", "run_id", "status"):
            assert key in doc, f"缺失字段 {key}（疑似被白名单裁剪）"
        assert doc["run_id"] == "run-x"
        assert doc["independence_note"]

    def test_p4_and_p5_have_no_cross_import(self):
        """§0.1 红线：P4 的 registry 结论不得读 P5 结论推断；不复用 build_diagnostics.py。

        只检查真实 import 语句与代码内的模块引用（排除中文说明注释/docstring 里提到
        "不 import p5_*" 这类否定性陈述本身，那不构成真实依赖）。
        """
        import ast
        import inspect
        from app.services import dependency_registry_service as svc_mod
        src = inspect.getsource(svc_mod)
        tree = ast.parse(src)
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)
        assert not any("p5" in name for name in imported_names), imported_names
        assert not any("build_diagnostics" in name for name in imported_names), imported_names


# ════════════════════════════════════════════════════════════════════════════
# stage_handlers.py 接线：RealP4Handler.execute() 全链路（artifacts/evidence_refs
# 显式追加 + p4_execution_summary.json 的 dependency_check 指针 + unresolved_scope
# 只读追加）。dependency_registry_service.run_dependency_check 打桩（零真实网络），
# 只验证 stage_handlers 侧的接线是否正确——网络判据表已在上面各类中单独覆盖。
# ════════════════════════════════════════════════════════════════════════════

class TestStageHandlersWiring:

    @staticmethod
    def _mk_ws(pid, sources):
        from app.services import workspace_service
        ws = workspace_service.init_workspace(pid)
        for rel, content in sources.items():
            p = ws / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        return ws

    @staticmethod
    def _mk_graph(pid, node_specs, edges=None):
        from app.core.database import get_session
        from app.models.task_graph import TaskGraph, TaskNode
        db = get_session()
        try:
            tg = TaskGraph(project_id=pid, stage="p3", stage_plan_ref="sp-r19-2",
                           title="r19-2 graph", graph_status="draft", edges=edges or [], version=1)
            db.add(tg); db.flush()
            for spec in node_specs:
                nid = spec[0]
                db.add(TaskNode(task_graph_id=tg.task_graph_id, project_id=pid, stage="p3",
                                node_id=nid, node_type="execution", title=f"exec {nid}",
                                input_refs=spec[1], risk_level="L2"))
            db.commit()
            return tg.task_graph_id
        finally:
            db.close()

    class _StubGateway:
        def __init__(self, content):
            self.content = content

        async def call(self, **kwargs):
            return {"status": "completed", "content": self.content, "model_id": "stub"}

    def _handler(self, gateway):
        from app.graph.stage_handlers import RealP4Handler
        from app.core.trace_writer import TraceWriter
        from app.core.audit_writer import AuditWriter
        return RealP4Handler(tracer=TraceWriter(), auditor=AuditWriter(), gateway=gateway)

    @staticmethod
    def _fake_dep_doc(run_id: str) -> dict:
        return {
            "artifact_type": "p4_dependency_check", "kind": "p4_dependency_check",
            "stage": "p4", "run_id": run_id, "generated_at": "2026-09-04T00:00:00+00:00",
            "schema": "p4_dependency_check_v1", "status": "partial_evidence_gap",
            "counts": {"manifests": 1, "coordinates": 1, "resolvable": 0,
                      "unresolvable": 1, "indeterminate": 0, "not_checked": 0},
            "registries": [{"ecosystem": "nuget", "host": "api.nuget.org",
                            "reachable": True, "note": ""}],
            "manifests": [{"path": "output_code/App.csproj", "ecosystem": "nuget",
                          "format": "packagereference", "parse_status": "ok",
                          "coordinate_count": 1, "sha256": "x", "bytes": 10}],
            "coordinates": [{
                "ecosystem": "nuget", "id": "Ghost.Fake.Pkg", "id_normalized": "ghost.fake.pkg",
                "requested_version": "9.9.9", "manifest_ref": "output_code/App.csproj",
                "manifest_line": 1, "conclusion": "package_not_found", "reason_code": None,
                "registry_host": "api.nuget.org",
                "endpoint": "https://api.nuget.org/v3-flatcontainer/ghost.fake.pkg/index.json",
                "http_status": 404, "attempts": 1, "elapsed_ms": 10,
                "available_versions_sample": [], "available_versions_total": 0,
                "cache": "miss", "checked_at": "2026-09-04T00:00:00+00:00",
                "statement": "在 nuget.org 公共源上不存在该包（HTTP 404）：Ghost.Fake.Pkg。",
            }],
            "evidence_gaps": [], "private_source_signals": [],
            "boundary_note": "平台只负责尽早暴露依赖缺陷，不承诺替用户完成修复（R19-2-03）。",
            "independence_note": "本结论来自本次真实 registry HTTP 响应，未读取任何 P5 结论。",
        }

    async def test_execute_appends_dep_check_ref_and_evidence_and_summary_pointer(
            self, monkeypatch, tmp_path):
        pid = "proj-r19-2-wiring"
        self._mk_ws(pid, {"source/App.csproj": "placeholder"})
        self._mk_graph(pid, [("n1", ["source/App.csproj"])])
        bad_csproj = ('<Project><ItemGroup><PackageReference Include="Ghost.Fake.Pkg" '
                     'Version="9.9.9" /></ItemGroup></Project>')

        async def _fake_run_dependency_check(project_id, workspace_dir, run_id=""):
            return self._fake_dep_doc(run_id)

        monkeypatch.setattr(
            "app.services.dependency_registry_service.run_dependency_check",
            _fake_run_dependency_check)

        res = await self._handler(self._StubGateway(bad_csproj)).execute(
            {"project_id": pid, "run_id": "r-wiring-1"})

        assert res["status"] == "completed"
        assert "artifacts/p4/p4_dependency_check.json" in res["artifacts"]
        assert "ev-p4-depcheck-r-wiring" in "".join(res["evidence_refs"])

        from app.services import workspace_service
        ws = workspace_service.workspace_path(pid)
        dep_doc_on_disk = json.loads((ws / "artifacts/p4/p4_dependency_check.json")
                                     .read_text(encoding="utf-8"))
        assert dep_doc_on_disk["counts"]["unresolvable"] == 1

        summary = json.loads((ws / "artifacts/p4/p4_execution_summary.json")
                             .read_text(encoding="utf-8"))
        assert "dependency_check" in summary
        assert summary["dependency_check"]["unresolvable"] == 1
        assert any("Ghost.Fake.Pkg" in s for s in summary["unresolved_scope"])

        from app.services.stage_package import read_stage_package
        pkg = read_stage_package(pid, "p4")
        dep_products = [p for p in pkg["products"] if p["type"] == "dependency_check"]
        assert len(dep_products) == 1
        assert dep_products[0]["file"] == "p4_dependency_check.json"

    async def test_execute_survives_dependency_check_exception(self, monkeypatch):
        """依赖校验异常不得影响 P4 本身的完成判定（advisory 降级，红线：暴露≠拦截）。"""
        pid = "proj-r19-2-wiring-fail"
        self._mk_ws(pid, {"source/App.csproj": "placeholder"})
        self._mk_graph(pid, [("n1", ["source/App.csproj"])])

        async def _boom(*a, **k):
            raise RuntimeError("simulated network catastrophe")

        monkeypatch.setattr(
            "app.services.dependency_registry_service.run_dependency_check", _boom)

        res = await self._handler(self._StubGateway("// ok\n")).execute(
            {"project_id": pid, "run_id": "r-wiring-2"})
        assert res["status"] == "completed"
        assert "artifacts/p4/p4_dependency_check.json" not in res["artifacts"]
