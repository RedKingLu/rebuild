"""R19-2 G2 依赖真实性校验 —— 清单解析层（纯函数，无网络，易测）。

职责边界（严格，见 产物/草稿/R19-2-依赖真实性校验-三步法.md §3.11）：
  · 本模块只【读】 output_code/ 下的依赖清单文件并解析成结构化坐标列表。
  · 不发起任何网络请求（网络查询在 dependency_registry_service.py）。
  · 不修改、不删除任何清单文件一个字节（R19-2-03 硬红线）。
  · 解析失败【绝不抛异常、绝不静默跳过】——返回 parse_status="manifest_unparseable"，
    上浮为 evidence_gap（Q-R19-2-4：实测 MicroOA 的 packages.config 内容是 LLM 旁白文本、
    不是 XML，是这条红线的真实负路径样本，已固化为单测用例）。

支持的清单格式（对应 R19-2-01）：
  nuget: *.csproj / *.props（<PackageReference Include= Version=>）
  nuget（旧式）: packages.config（<package id= version=>）
  npm:   package.json（dependencies/devDependencies/peerDependencies/optionalDependencies）
  maven: pom.xml（<dependency><groupId><artifactId><version>）
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

# 平台自管的公共源白名单（不接受项目内容/LLM 输出指定的 registry 主机，防 SSRF 面）。
PUBLIC_REGISTRY_HOSTS = frozenset({
    "api.nuget.org", "nuget.org",
    "registry.npmjs.org",
    "repo1.maven.org", "repo.maven.apache.org", "search.maven.org",
})

_SKIP_DIR_NAMES = frozenset({".git", "node_modules", "obj", "bin", "__pycache__", ".rebuild"})
_MANIFEST_PATTERNS = ("*.csproj", "*.props", "packages.config", "package.json", "pom.xml")


def _assign_line_numbers(raw_text: str, coords: list[dict], needles: list[str]) -> None:
    """按文档顺序把坐标匹配回源码行号（供报告引用 ``manifest_line``，如 R19-2-02 锚点
    ``MicroOA.Web.csproj:17``）。

    注：``xml.etree.ElementTree.XMLParser`` 在本机 CPython 上是 C 加速实现
    （``_elementtree``），不会调用被子类覆写的 ``_start``/``_end`` 钩子，无法拿到
    expat 的 ``CurrentLineNumber``——改用文本行扫描+游标（按文档顺序单调前移，
    避免重复内容的坐标被错配到同一行）。``needles`` 与 ``coords`` 按下标一一对应。
    """
    lines = raw_text.splitlines()
    cursor = 0
    for coord, needle in zip(coords, needles):
        line_no = None
        if needle:
            for i in range(cursor, len(lines)):
                if needle in lines[i]:
                    line_no = i + 1
                    cursor = i + 1
                    break
        coord["line"] = line_no


def _tag(el: ET.Element) -> str:
    """去掉 XML 命名空间前缀，只取本地标签名。"""
    return el.tag.split("}")[-1]


def discover_manifests(output_code_dir: Path) -> list[Path]:
    """只读扫描 output_code/ 下受支持的依赖清单文件。排除 .rebuild/ 等平台自产目录
    与 obj/bin/node_modules 构建产物目录（不是依赖清单，扫描会产生噪音）。"""
    if not output_code_dir.exists():
        return []
    found: set[Path] = set()
    for pattern in _MANIFEST_PATTERNS:
        for p in output_code_dir.rglob(pattern):
            if p.is_file() and not any(part in _SKIP_DIR_NAMES for part in p.parts):
                found.add(p)
    return sorted(found)


def _ecosystem_and_format(name_lower: str) -> tuple[str, str]:
    if name_lower.endswith(".csproj") or name_lower.endswith(".props"):
        return "nuget", "packagereference"
    if name_lower == "packages.config":
        return "nuget", "packages_config"
    if name_lower == "package.json":
        return "npm", "package_json"
    if name_lower == "pom.xml":
        return "maven", "pom"
    return "unknown", "unknown"


def _unparseable(rel: str, ecosystem: str, fmt: str, error: str, *,
                 sha256: str = "", size: int = 0) -> dict:
    return {
        "path": rel, "ecosystem": ecosystem, "format": fmt,
        "parse_status": "manifest_unparseable", "error": error[:500],
        "coordinate_count": 0, "coordinates": [],
        "sha256": sha256, "bytes": size,
    }


def _parse_packagereference(root: ET.Element, raw_text: str) -> list[dict]:
    coords = []
    needles = []
    for el in root.iter():
        if _tag(el) != "PackageReference":
            continue
        attr_name = "Include" if el.get("Include") else ("Update" if el.get("Update") else None)
        pkg_id = el.get(attr_name) if attr_name else None
        if not pkg_id:
            continue
        version = el.get("Version")
        if not version:
            for child in el:
                if _tag(child) == "Version" and child.text:
                    version = child.text.strip()
                    break
        coords.append({"id": pkg_id, "version": (version or "").strip()})
        needles.append(f'{attr_name}="{pkg_id}"')
    _assign_line_numbers(raw_text, coords, needles)
    return coords


def _parse_packages_config(root: ET.Element, raw_text: str) -> list[dict]:
    coords = []
    needles = []
    for el in root.iter():
        if _tag(el) != "package":
            continue
        pkg_id = el.get("id")
        if not pkg_id:
            continue
        coords.append({"id": pkg_id, "version": (el.get("version") or "").strip()})
        needles.append(f'id="{pkg_id}"')
    _assign_line_numbers(raw_text, coords, needles)
    return coords


def _parse_pom_dependencies(root: ET.Element, raw_text: str) -> list[dict]:
    coords = []
    needles = []
    for dep in root.iter():
        if _tag(dep) != "dependency":
            continue
        gid = artid = ver = None
        for child in dep:
            ctag = _tag(child)
            if ctag == "groupId":
                gid = (child.text or "").strip()
            elif ctag == "artifactId":
                artid = (child.text or "").strip()
            elif ctag == "version":
                ver = (child.text or "").strip()
        if gid and artid:
            coords.append({"id": f"{gid}:{artid}", "version": ver or ""})
            needles.append(f"<artifactId>{artid}</artifactId>")
    _assign_line_numbers(raw_text, coords, needles)
    return coords


def _parse_package_json(raw: str) -> list[dict]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("package.json 顶层结构不是 JSON 对象")
    coords: list[dict] = []
    for field in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        deps = data.get(field)
        if not isinstance(deps, dict):
            continue
        for name, version in deps.items():
            coords.append({"id": str(name), "version": str(version), "line": None})
    return coords


def parse_manifest(path: Path, rel_to: Path) -> dict:
    """解析单个清单文件。永不抛异常——解析失败返回 parse_status='manifest_unparseable'
    （不静默跳过、不修不删该文件，Q-R19-2-4）。"""
    try:
        rel = path.relative_to(rel_to).as_posix()
    except ValueError:
        rel = str(path)
    ecosystem, fmt = _ecosystem_and_format(path.name.lower())
    try:
        raw_bytes = path.read_bytes()
    except Exception as e:
        return _unparseable(rel, ecosystem, fmt, f"读取失败: {e}")
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    size = len(raw_bytes)
    try:
        raw_text = raw_bytes.decode("utf-8")
    except Exception as e:
        return _unparseable(rel, ecosystem, fmt, f"非 UTF-8 编码: {e}", sha256=sha256, size=size)

    try:
        if fmt == "package_json":
            coords = _parse_package_json(raw_text)
        elif fmt == "packagereference":
            coords = _parse_packagereference(ET.fromstring(raw_text), raw_text)
        elif fmt == "packages_config":
            coords = _parse_packages_config(ET.fromstring(raw_text), raw_text)
        elif fmt == "pom":
            coords = _parse_pom_dependencies(ET.fromstring(raw_text), raw_text)
        else:
            return _unparseable(rel, ecosystem, fmt, "未识别的清单格式", sha256=sha256, size=size)
    except Exception as e:
        # ET.ParseError（非 XML/坏 XML）、json.JSONDecodeError（非 JSON）等一律归此。
        # 实测样本：output_code/MicroOA.Web/packages.config 内容是 LLM 旁白文本，
        # ET.ParseError 会在此被捕获（Q-R19-2-4）。
        return _unparseable(rel, ecosystem, fmt, f"{type(e).__name__}: {e}", sha256=sha256, size=size)

    return {
        "path": rel, "ecosystem": ecosystem, "format": fmt,
        "parse_status": "ok", "error": "",
        "coordinate_count": len(coords), "coordinates": coords,
        "sha256": sha256, "bytes": size,
    }


def parse_all_manifests(output_code_dir: Path) -> list[dict]:
    """扫描并解析 output_code/ 下全部受支持清单，返回 parse_manifest() 结果列表。

    每条结果的 ``path`` 相对于 ``output_code_dir`` 本身（不含 "output_code/" 前缀）——
    调用方（dependency_registry_service）统一负责拼上该前缀，避免两处各拼一次导致
    "output_code/output_code/..." 重复前缀（实测曾踩过的坑）。
    """
    return [parse_manifest(p, output_code_dir) for p in discover_manifests(output_code_dir)]


# ─────────────────────────────────────────────────────────────────────────────
# 私有源信号采集（§3.8.2）——只读存在性 + 主机名，绝不读取凭据值。
# ─────────────────────────────────────────────────────────────────────────────

def _host_of(url: str) -> str:
    try:
        return (urlparse(url.strip()).hostname or "").lower()
    except Exception:
        return ""


def _xml_urls_under_tags(root: ET.Element, tags: tuple[str, ...]) -> list[str]:
    urls = []
    for el in root.iter():
        if _tag(el) in tags:
            for child in el:
                if _tag(child) == "url" and child.text:
                    urls.append(child.text.strip())
    return urls


def _nuget_config_hosts(path: Path) -> list[str]:
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []
    hosts = []
    for el in root.iter():
        if _tag(el) == "add" and el.get("value"):
            h = _host_of(el.get("value"))
            if h and h not in PUBLIC_REGISTRY_HOSTS:
                hosts.append(h)
    return sorted(set(hosts))


def _npmrc_hosts(path: Path) -> list[str]:
    hosts = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key == "registry" or key.endswith(":registry"):
            h = _host_of(val.strip())
            if h and h not in PUBLIC_REGISTRY_HOSTS:
                hosts.append(h)
    return sorted(set(hosts))


def _maven_xml_hosts(path: Path, tags: tuple[str, ...]) -> list[str]:
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []
    hosts = [h for u in _xml_urls_under_tags(root, tags)
             for h in [_host_of(u)] if h and h not in PUBLIC_REGISTRY_HOSTS]
    return sorted(set(hosts))


def discover_private_source_signals(workspace_dir: Path) -> list[dict]:
    """在 output_code/ 与 source/ 下只读扫描私有源声明（NuGet.Config / .npmrc /
    settings.xml / pom.xml 的 <repositories>/<mirrors>）。只提取【文件存在】+【主机名】
    两项事实，绝不提取任何 password/token/auth 字段的值（连是否存在都不写）。

    .rebuild/ 下的平台自产文件（如探测/构建期临时 NuGet.Config）不计入——那是平台自己
    写的，不是用户的私有源声明（实测 MicroOA 惟一 NuGet.Config 在 .rebuild/build/**，
    按此规则排除）。
    """
    signals: list[dict] = []
    for scan_root_name in ("output_code", "source"):
        root_dir = workspace_dir / scan_root_name
        if not root_dir.exists():
            continue
        for p in root_dir.rglob("*"):
            if not p.is_file() or ".rebuild" in p.parts:
                continue
            name = p.name.lower()
            hosts: list[str] = []
            ecosystem = ""
            if name == "nuget.config":
                hosts, ecosystem = _nuget_config_hosts(p), "nuget"
            elif name == ".npmrc":
                hosts, ecosystem = _npmrc_hosts(p), "npm"
            elif name == "settings.xml":
                hosts, ecosystem = _maven_xml_hosts(p, ("mirror", "repository")), "maven"
            elif name == "pom.xml":
                hosts, ecosystem = _maven_xml_hosts(p, ("repository",)), "maven"
            if hosts:
                signals.append({
                    "path": str(p.relative_to(workspace_dir)),
                    "ecosystem": ecosystem,
                    "declared_hosts": hosts,
                    "note": "只读源主机名；平台不访问私有源、不读凭据",
                })
    return signals
