"""R19-2 G2 依赖真实性校验 —— registry 只读查询层（有网络、有缓存、有重试）。

职责边界（严格，见 产物/草稿/R19-2-依赖真实性校验-三步法.md §3）：
  · 只发起只读 GET/HEAD 请求（不下载包体，只取 manifest/index，L0 风险）。
  · 只查平台自管的公共源白名单（api.nuget.org / registry.npmjs.org / repo1.maven.org），
    不接受项目内容或 LLM 输出指定的 registry 主机（防 SSRF 面）。
  · 绝不读取宿主 ~/.nuget / ~/.npmrc / ~/.m2/settings.xml 或任何凭据值（AGENTS §8/§12.1）。
  · 不改任何依赖清单、不推荐替代包、不参与门禁（R19-2-03：只暴露不修复，暴露≠拦截）。
  · 只产事实 + 结论码（resolvable/package_not_found/version_not_found/indeterminate/
    not_checked），不产 pass/通过 结论。completed 判定不受本服务影响。
  · 结论码与 reason_code 词表严格遵循方案③ §3.5：404≠401≠429≠超时，语义不可混淆——
    429（限流）被误判为 404 会把真实存在的包报成不存在，是本方案第一红线。
  · P4 不得读 P5 结论、不复用 build_diagnostics.py（输入介质不同，无共享代码面）。

本模块不修改任何依赖清单文件；纯只读网络层，与 manifest_parsers.py（纯函数解析层）
关注点分离（有网络 vs 无网络，便于分别单测）。
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from app.core.config import settings
from app.services import manifest_parsers

logger = logging.getLogger("rebuild.dependency_registry_service")

NUGET_HOST = "api.nuget.org"
NPM_HOST = "registry.npmjs.org"
MAVEN_HOST = "repo1.maven.org"
_ALLOWED_HOSTS = frozenset({NUGET_HOST, NPM_HOST, MAVEN_HOST})

# 计入"不可解析依赖"的结论码（R19-2-02 锚点）。
_UNRESOLVABLE_CONCLUSIONS = frozenset({"package_not_found", "version_not_found"})
# 只缓存确定结论（②吸收 web_fetch.py:262 只在成功路径缓存的形状）。
_CACHEABLE_CONCLUSIONS = frozenset({"resolvable", "package_not_found", "version_not_found"})

# ── 进程内缓存（②吸收 web_fetch.py:84/87/100 的形状：dict + monotonic 到期戳） ──────
_CACHE: dict[tuple, tuple[dict, float]] = {}


def clear_cache() -> None:
    """测试钩子：清空进程内缓存，避免用例互相污染（②吸收 web_fetch.py:104 _cache_clear）。"""
    _CACHE.clear()


def _cache_get(key: tuple) -> dict | None:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    value, expire_at = entry
    if time.monotonic() >= expire_at:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: tuple, value: dict) -> None:
    _CACHE[key] = (dict(value), time.monotonic() + settings.dependency_check_cache_ttl_s)


class _Budget:
    """整轮依赖校验的墙钟总预算（默认 60s，env 可调）。超预算未查完的坐标诚实
    indeterminate(budget_exhausted)，不默认通过（AGENTS §10-21 异常必须发声）。"""

    def __init__(self, seconds: float):
        self._deadline = time.monotonic() + max(0.0, seconds)

    def remaining(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def exhausted(self) -> bool:
        return self.remaining() <= 0


_HOST_SEMAPHORES: dict[str, asyncio.Semaphore] = {}


def _semaphore_for(host: str) -> asyncio.Semaphore:
    sem = _HOST_SEMAPHORES.get(host)
    if sem is None:
        sem = asyncio.Semaphore(4)
        _HOST_SEMAPHORES[host] = sem
    return sem


# ── 单次请求 + 重试/退避（②吸收 model_gateway.py:252/255/263 的形状） ──────────────

def _retry_after_seconds(resp: httpx.Response) -> float | None:
    val = resp.headers.get("retry-after")
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        return None


async def _sleep_backoff(attempt: int, budget: _Budget, override: float | None = None) -> None:
    delay = override if override is not None else (1.5 * (2 ** attempt) + random.uniform(0, 0.5))
    delay = min(delay, budget.remaining())
    if delay > 0:
        await asyncio.sleep(delay)


async def _request_once(client: httpx.AsyncClient, method: str, url: str,
                        timeout: float) -> tuple[httpx.Response | None, str | None]:
    """单次请求，跟随同主机重定向（上限 3 跳）；跨主机重定向不跟随（unexpected_redirect）。"""
    cur = url
    for _ in range(4):
        resp = await client.request(method, cur, timeout=timeout, follow_redirects=False)
        if resp.status_code not in (301, 302, 303, 307, 308):
            return resp, None
        loc = resp.headers.get("location", "")
        if not loc:
            return None, "malformed_response"
        host = (urlparse(loc).hostname or "").lower()
        if host and host not in _ALLOWED_HOSTS:
            return None, "unexpected_redirect"
        cur = loc
    return None, "unexpected_redirect"


async def _fetch(client: httpx.AsyncClient, method: str, url: str,
                 budget: _Budget) -> tuple[httpx.Response | None, str | None, int]:
    """带重试的请求。返回 (response|None, reason_code|None, attempts)。

    404 从不重试（确定事实，重试只白费预算并逼近限流）。429/5xx/超时按指数退避+抖动重试，
    最多 dependency_check_max_retries 次。Retry-After > 20s 不干等，直接判 rate_limited
    （P4 收尾链路有墙钟预算，与 V10 单请求语境不同——这是③在②形状之上的适配，非吸收）。
    """
    max_retries = settings.dependency_check_max_retries
    timeout = settings.dependency_check_timeout_s
    attempt = 0
    while True:
        if budget.exhausted():
            return None, "budget_exhausted", attempt + 1
        try:
            resp, reason = await _request_once(client, method, url, timeout)
        except httpx.TimeoutException:
            resp, reason = None, "network_timeout"
        except httpx.ConnectError:
            resp, reason = None, "network_unavailable"
        except httpx.HTTPError:
            resp, reason = None, "network_unavailable"

        if resp is None:
            if reason in ("unexpected_redirect", "malformed_response"):
                return None, reason, attempt + 1
            # network_timeout / network_unavailable：按退避重试
            if attempt >= max_retries:
                return None, reason, attempt + 1
            await _sleep_backoff(attempt, budget)
            attempt += 1
            continue

        if resp.status_code == 404:
            return resp, None, attempt + 1  # 404 从不重试
        if resp.status_code in (401, 403):
            return None, "auth_required", attempt + 1
        if resp.status_code == 429:
            if attempt >= max_retries:
                return None, "rate_limited", attempt + 1
            wait_s = _retry_after_seconds(resp)
            if wait_s is not None and wait_s > 20:
                return None, "rate_limited", attempt + 1
            await _sleep_backoff(attempt, budget, override=wait_s)
            attempt += 1
            continue
        if 500 <= resp.status_code < 600:
            if attempt >= max_retries:
                return None, "registry_error", attempt + 1
            await _sleep_backoff(attempt, budget)
            attempt += 1
            continue
        if resp.status_code == 200:
            return resp, None, attempt + 1
        return None, "registry_error", attempt + 1


# ── NuGet ──────────────────────────────────────────────────────────────────

_NUGET_BASE_URL_CACHE_KEY = ("_nuget_base_address", "", "")


def _normalize_nuget_version(v: str) -> str:
    """去除 4 段版本号中末位为 0 的第 4 段（8.0.0.0 → 8.0.0，csproj 合法写法但 NuGet
    versions[] 不含该形式）；预发布后缀保留原样（语义上是不同版本，不可丢）。"""
    v = (v or "").strip()
    core, sep, pre = v.partition("-")
    parts = core.split(".")
    if len(parts) == 4 and parts[3] == "0":
        core = ".".join(parts[:3])
    return f"{core}{sep}{pre}" if sep else core


def _nuget_version_is_indeterminate(v: str) -> bool:
    """MSBuild 属性表达式（$(Ver) 等）无法静态求解 → version_indeterminate（Q-R19-2-1）。"""
    return "$(" in (v or "") or not (v or "").strip()


async def _nuget_base_address(client: httpx.AsyncClient, budget: _Budget) -> tuple[str | None, str | None]:
    """从服务索引动态发现 PackageBaseAddress/3.0.0（不硬编码），带 TTL 缓存。"""
    cached = _cache_get(_NUGET_BASE_URL_CACHE_KEY)
    if cached is not None:
        return cached.get("base_url"), None
    resp, reason, _ = await _fetch(client, "GET", f"https://{NUGET_HOST}/v3/index.json", budget)
    if resp is None:
        return None, reason
    try:
        data = resp.json()
    except Exception:
        return None, "malformed_response"
    base_url = None
    for res in data.get("resources", []) or []:
        rtype = str(res.get("@type", ""))
        if rtype.startswith("PackageBaseAddress/3.0.0"):
            base_url = res.get("@id")
            break
    if not base_url:
        return None, "malformed_response"
    base_url = base_url.rstrip("/")
    _cache_set(_NUGET_BASE_URL_CACHE_KEY, {"base_url": base_url})
    return base_url, None


async def _check_nuget(client: httpx.AsyncClient, coord: dict, budget: _Budget) -> dict:
    pkg_id = coord["id"]
    id_lower = pkg_id.lower()
    requested_version = coord.get("version", "")
    base_url, reason = await _nuget_base_address(client, budget)
    if base_url is None:
        return _indeterminate_result(coord, "nuget", NUGET_HOST, "", reason or "registry_error",
                                     attempts=1, elapsed_ms=0, http_status=None)

    endpoint = f"{base_url}/{id_lower}/index.json"
    t0 = time.monotonic()
    resp, reason, attempts = await _fetch(client, "GET", endpoint, budget)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    if resp is None:
        return _indeterminate_result(coord, "nuget", NUGET_HOST, endpoint, reason,
                                     attempts=attempts, elapsed_ms=elapsed_ms, http_status=None)
    if resp.status_code == 404:
        return _make_result(coord, "nuget", NUGET_HOST, endpoint, "package_not_found", None,
                            http_status=404, attempts=attempts, elapsed_ms=elapsed_ms,
                            statement=f"在 nuget.org 公共源上不存在该包（HTTP 404）：{pkg_id}。"
                                      "平台只暴露不修复。")
    try:
        data = resp.json()
        versions = list(data.get("versions") or [])
        if not isinstance(versions, list):
            raise ValueError("versions 字段非列表")
    except Exception:
        return _indeterminate_result(coord, "nuget", NUGET_HOST, endpoint, "malformed_response",
                                     attempts=attempts, elapsed_ms=elapsed_ms, http_status=resp.status_code)

    sample = versions[:10]
    if _nuget_version_is_indeterminate(requested_version):
        return _make_result(coord, "nuget", NUGET_HOST, endpoint, "indeterminate",
                            "version_indeterminate", http_status=resp.status_code,
                            attempts=attempts, elapsed_ms=elapsed_ms,
                            versions_sample=sample, versions_total=len(versions),
                            statement=f"包 {pkg_id} 在 nuget.org 存在，但版本表达式 "
                                      f"'{requested_version}' 无法静态求解（未做版本范围求解）。")
    norm_requested = _normalize_nuget_version(requested_version)
    norm_available = {_normalize_nuget_version(v) for v in versions}
    if norm_requested in norm_available:
        return _make_result(coord, "nuget", NUGET_HOST, endpoint, "resolvable", None,
                            http_status=resp.status_code, attempts=attempts, elapsed_ms=elapsed_ms,
                            versions_sample=sample, versions_total=len(versions),
                            statement=f"包 {pkg_id}@{requested_version} 在 nuget.org 可解析。")
    return _make_result(coord, "nuget", NUGET_HOST, endpoint, "version_not_found", None,
                        http_status=resp.status_code, attempts=attempts, elapsed_ms=elapsed_ms,
                        versions_sample=sample, versions_total=len(versions),
                        statement=f"包 {pkg_id} 在 nuget.org 上存在，但请求版本 "
                                  f"{requested_version} 不在版本表内。")


# ── npm ────────────────────────────────────────────────────────────────────

_NPM_INSTALL_ACCEPT = "application/vnd.npm.install-v1+json"
_NPM_EXACT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([-+].*)?$")


def _npm_encode_name(name: str) -> str:
    if name.startswith("@") and "/" in name:
        scope, _, rest = name.partition("/")
        return f"{quote(scope, safe='')}%2f{quote(rest, safe='')}"
    return quote(name, safe="")


def _npm_version_is_exact(v: str) -> bool:
    v = (v or "").strip()
    if not v:
        return False
    if v.lower() in ("latest", "*", "next"):
        return False
    if any(v.startswith(p) for p in ("workspace:", "git+", "git:", "http:", "https:", "file:", "npm:")):
        return False
    if any(c in v for c in "^~*<>| "):
        return False
    return bool(_NPM_EXACT_VERSION_RE.match(v))


def _npm_error_message(body_text: str) -> str:
    """安全解析 npm 404 body：包不存在是 JSON 对象 {"error":"Not found"}，
    版本不存在是【裸 JSON 字符串】"version not found: X"——json.loads 后必须先判类型
    再取键，否则 dict.get 会对字符串抛 AttributeError（实测事实，见方案③ §3.4.1）。"""
    try:
        import json as _json
        parsed = _json.loads(body_text)
    except Exception:
        return ""
    if isinstance(parsed, dict):
        return str(parsed.get("error", ""))
    if isinstance(parsed, str):
        return parsed
    return ""


async def _check_npm(client: httpx.AsyncClient, coord: dict, budget: _Budget) -> dict:
    name = coord["id"]
    requested_version = coord.get("version", "")
    name_enc = _npm_encode_name(name)
    root_url = f"https://{NPM_HOST}/{name_enc}"

    t0 = time.monotonic()
    resp, reason, attempts = await _fetch(client, "GET", root_url, budget)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if resp is None:
        return _indeterminate_result(coord, "npm", NPM_HOST, root_url, reason,
                                     attempts=attempts, elapsed_ms=elapsed_ms, http_status=None)
    if resp.status_code == 404:
        msg = _npm_error_message(resp.text)
        return _make_result(coord, "npm", NPM_HOST, root_url, "package_not_found", None,
                            http_status=404, attempts=attempts, elapsed_ms=elapsed_ms,
                            statement=f"在 registry.npmjs.org 公共源上不存在该包（HTTP 404"
                                      f"{': ' + msg if msg else ''}）：{name}。平台只暴露不修复。")

    if not _npm_version_is_exact(requested_version):
        return _make_result(coord, "npm", NPM_HOST, root_url, "indeterminate",
                            "version_indeterminate", http_status=resp.status_code,
                            attempts=attempts, elapsed_ms=elapsed_ms,
                            statement=f"包 {name} 在 registry.npmjs.org 存在，但版本表达式 "
                                      f"'{requested_version}' 无法静态求解（未做 semver 范围求解）。")

    version_url = f"{root_url}/{quote(requested_version, safe='')}"
    t1 = time.monotonic()
    resp2, reason2, attempts2 = await _fetch(client, "GET", version_url, budget)
    elapsed_ms2 = int((time.monotonic() - t1) * 1000)
    if resp2 is None:
        return _indeterminate_result(coord, "npm", NPM_HOST, version_url, reason2,
                                     attempts=attempts + attempts2, elapsed_ms=elapsed_ms + elapsed_ms2,
                                     http_status=None)
    if resp2.status_code == 404:
        msg = _npm_error_message(resp2.text)
        return _make_result(coord, "npm", NPM_HOST, version_url, "version_not_found", None,
                            http_status=404, attempts=attempts + attempts2,
                            elapsed_ms=elapsed_ms + elapsed_ms2,
                            statement=f"包 {name} 在 registry.npmjs.org 上存在，但请求版本 "
                                      f"{requested_version} 不存在（HTTP 404"
                                      f"{': ' + msg if msg else ''}）。")
    return _make_result(coord, "npm", NPM_HOST, version_url, "resolvable", None,
                        http_status=resp2.status_code, attempts=attempts + attempts2,
                        elapsed_ms=elapsed_ms + elapsed_ms2,
                        statement=f"包 {name}@{requested_version} 在 registry.npmjs.org 可解析。")


# ── Maven ──────────────────────────────────────────────────────────────────

def _maven_version_is_indeterminate(v: str) -> bool:
    v = (v or "").strip()
    return not v or v.startswith("${") or "SNAPSHOT" in v.upper()


async def _check_maven(client: httpx.AsyncClient, coord: dict, budget: _Budget) -> dict:
    gid, _, artid = coord["id"].partition(":")
    requested_version = coord.get("version", "")
    group_path = gid.replace(".", "/")
    metadata_url = f"https://{MAVEN_HOST}/maven2/{group_path}/{artid}/maven-metadata.xml"

    t0 = time.monotonic()
    resp, reason, attempts = await _fetch(client, "GET", metadata_url, budget)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if resp is None:
        return _indeterminate_result(coord, "maven", MAVEN_HOST, metadata_url, reason,
                                     attempts=attempts, elapsed_ms=elapsed_ms, http_status=None)
    if resp.status_code == 404:
        return _make_result(coord, "maven", MAVEN_HOST, metadata_url, "package_not_found", None,
                            http_status=404, attempts=attempts, elapsed_ms=elapsed_ms,
                            statement=f"在 Maven Central 上不存在该 GA（HTTP 404）："
                                      f"{coord['id']}。平台只暴露不修复。")
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.text)
        versions = [v.text.strip() for v in root.iter() if v.tag.split("}")[-1] == "version" and v.text]
    except Exception:
        return _indeterminate_result(coord, "maven", MAVEN_HOST, metadata_url, "malformed_response",
                                     attempts=attempts, elapsed_ms=elapsed_ms, http_status=resp.status_code)

    sample = versions[:10]
    if _maven_version_is_indeterminate(requested_version):
        return _make_result(coord, "maven", MAVEN_HOST, metadata_url, "indeterminate",
                            "version_indeterminate", http_status=resp.status_code,
                            attempts=attempts, elapsed_ms=elapsed_ms,
                            versions_sample=sample, versions_total=len(versions),
                            statement=f"GA {coord['id']} 在 Maven Central 存在，但版本 "
                                      f"'{requested_version}' 为属性/BOM/SNAPSHOT 表达式，"
                                      "无法静态求解（未做版本范围求解）。")

    pom_url = f"https://{MAVEN_HOST}/maven2/{group_path}/{artid}/{requested_version}/{artid}-{requested_version}.pom"
    t1 = time.monotonic()
    resp2, reason2, attempts2 = await _fetch(client, "HEAD", pom_url, budget)
    elapsed_ms2 = int((time.monotonic() - t1) * 1000)
    if resp2 is None:
        return _indeterminate_result(coord, "maven", MAVEN_HOST, pom_url, reason2,
                                     attempts=attempts + attempts2, elapsed_ms=elapsed_ms + elapsed_ms2,
                                     http_status=None, versions_sample=sample, versions_total=len(versions))
    if resp2.status_code == 404:
        return _make_result(coord, "maven", MAVEN_HOST, pom_url, "version_not_found", None,
                            http_status=404, attempts=attempts + attempts2,
                            elapsed_ms=elapsed_ms + elapsed_ms2, versions_sample=sample,
                            versions_total=len(versions),
                            statement=f"GA {coord['id']} 在 Maven Central 上存在，但请求版本 "
                                      f"{requested_version} 不存在（HEAD GAV pom 404）。")
    return _make_result(coord, "maven", MAVEN_HOST, pom_url, "resolvable", None,
                        http_status=resp2.status_code, attempts=attempts + attempts2,
                        elapsed_ms=elapsed_ms + elapsed_ms2, versions_sample=sample,
                        versions_total=len(versions),
                        statement=f"GAV {coord['id']}:{requested_version} 在 Maven Central 可解析。")


# ── 结果构造 ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_result(coord: dict, ecosystem: str, host: str, endpoint: str, conclusion: str,
                 reason_code: str | None, *, http_status: int | None, attempts: int,
                 elapsed_ms: int, statement: str, versions_sample: list | None = None,
                 versions_total: int = 0, cache: str = "miss") -> dict:
    id_normalized = coord["id"].lower() if ecosystem == "nuget" else coord["id"]
    return {
        "ecosystem": ecosystem,
        "id": coord["id"],
        "id_normalized": id_normalized,
        "requested_version": coord.get("version", ""),
        "manifest_ref": coord.get("manifest_ref"),
        "manifest_line": coord.get("manifest_line"),
        "conclusion": conclusion,
        "reason_code": reason_code,
        "registry_host": host,
        "endpoint": endpoint,
        "http_status": http_status,
        "attempts": attempts,
        "elapsed_ms": elapsed_ms,
        "available_versions_sample": versions_sample or [],
        "available_versions_total": versions_total,
        "cache": cache,
        "checked_at": _now_iso(),
        "statement": statement,
    }


def _indeterminate_result(coord: dict, ecosystem: str, host: str, endpoint: str,
                          reason_code: str, *, attempts: int, elapsed_ms: int,
                          http_status: int | None, versions_sample: list | None = None,
                          versions_total: int = 0) -> dict:
    _REASON_TEXT = {
        "auth_required": "该公共源返回 401/403（需凭据）——公共源正常不应要求凭据，"
                          "判据基础已失效，不可当作不存在",
        "rate_limited": "该公共源限流（429），重试已耗尽——绝不可当作不存在（第一红线）",
        "registry_error": "该公共源返回 5xx 服务端错误，与坐标是否存在无关",
        "network_timeout": "网络请求超时，无任何事实，不得因查不到就推不存在",
        "network_unavailable": "网络不可达（DNS 失败/连接被拒），无任何事实",
        "unexpected_redirect": "被重定向到非白名单主机，响应内容不再是权威 registry 的答复",
        "malformed_response": "HTTP 200 但响应体无法解析，200 只证明服务器答了、不证明答的是要的事实",
        "budget_exhausted": "整轮依赖校验墙钟预算耗尽，本坐标本次未查完，不默认通过",
    }.get(reason_code, f"不可判：{reason_code}")
    return _make_result(coord, ecosystem, host, endpoint, "indeterminate", reason_code,
                        http_status=http_status, attempts=attempts, elapsed_ms=elapsed_ms,
                        statement=f"{coord['id']}@{coord.get('version', '')}：{_REASON_TEXT}。",
                        versions_sample=versions_sample, versions_total=versions_total)


_CHECKERS = {"nuget": _check_nuget, "npm": _check_npm, "maven": _check_maven}


async def check_coordinate(client: httpx.AsyncClient, coord: dict, budget: _Budget) -> dict:
    """查询单个依赖坐标（先查缓存；只有确定结论才缓存写回）。"""
    ecosystem = coord["ecosystem"]
    checker = _CHECKERS.get(ecosystem)
    if checker is None:
        return _make_result(coord, ecosystem, "", "", "not_checked", "ecosystem_unsupported",
                            http_status=None, attempts=0, elapsed_ms=0,
                            statement=f"生态 {ecosystem} 当前不支持只读校验。")

    cache_key = (ecosystem,
                 coord["id"].lower() if ecosystem == "nuget" else coord["id"],
                 coord.get("version", ""))
    cached = _cache_get(cache_key)
    if cached is not None:
        result = dict(cached)
        result["cache"] = "hit"
        result["checked_at"] = _now_iso()
        return result

    async with _semaphore_for(_host_for_ecosystem(ecosystem)):
        result = await checker(client, coord, budget)

    if result["conclusion"] in _CACHEABLE_CONCLUSIONS:
        _cache_set(cache_key, result)
    return result


def _host_for_ecosystem(ecosystem: str) -> str:
    return {"nuget": NUGET_HOST, "npm": NPM_HOST, "maven": MAVEN_HOST}.get(ecosystem, "")


# ── 顶层入口：产出 p4_dependency_check.json 形状的完整文档 ──────────────────────

async def run_dependency_check(project_id: str, workspace_dir: Path,
                               run_id: str = "") -> dict | None:
    """扫描 output_code/ 全部依赖清单 → 逐坐标查询三类 registry → 产出完整文档
    （方案③ §3.9.1 字段形状，dict-first，不经任何白名单 to_dict）。

    output_code/ 下【一个受支持的依赖清单文件都没有】时返回 ``None``（不产噪音产物/
    Evidence——非 .NET/npm/Maven 项目或纯测试夹具场景下，每次 P4 都写一份空文档没有
    信息量）。清单存在但坐标为空（如空的 packages.config）仍照常产出真实（空）结果。

    纯只读：不改任何依赖清单一个字节；不参与门禁；不产 pass/通过 结论。
    与 P5 的取证链路完全独立（本函数不 import 任何 p5_* 模块、不读 p5_validation_report.json）。
    """
    output_code_dir = workspace_dir / "output_code"
    manifests = manifest_parsers.parse_all_manifests(output_code_dir)
    if not manifests:
        return None
    private_signals = manifest_parsers.discover_private_source_signals(workspace_dir)
    private_hosts_present = bool(private_signals)

    coordinates: list[dict] = []
    for m in manifests:
        for c in m.get("coordinates", []):
            coordinates.append({**c, "ecosystem": m["ecosystem"], "manifest_ref":
                                f"output_code/{m['path']}", "manifest_line": c.get("line")})

    evidence_gaps: list[dict] = []
    for m in manifests:
        if m["parse_status"] != "ok":
            evidence_gaps.append({
                "gap_id": f"dep_manifest_unparseable_{Path(m['path']).name}",
                "evidence_type": "dependency_resolution",
                "description": f"清单文件无法解析（manifest_unparseable）：output_code/{m['path']} "
                                f"—— {m.get('error', '')}",
                "blocking": False,
            })

    registries_status = []
    if not settings.dependency_check_enabled:
        results = [_make_result(c, c["ecosystem"], _host_for_ecosystem(c["ecosystem"]), "",
                                "not_checked", "skipped_by_config", http_status=None,
                                attempts=0, elapsed_ms=0,
                                statement="依赖真实性校验已被配置关闭（DEPENDENCY_CHECK_ENABLED=False），"
                                          "本次未查询任何 registry。")
                  for c in coordinates]
    else:
        budget = _Budget(settings.dependency_check_budget_s)
        async with httpx.AsyncClient() as client:
            tasks = [check_coordinate(client, c, budget) for c in coordinates]
            results = await asyncio.gather(*tasks) if tasks else []
            for ecosystem, host in (("nuget", NUGET_HOST), ("npm", NPM_HOST), ("maven", MAVEN_HOST)):
                if any(c["ecosystem"] == ecosystem for c in coordinates):
                    registries_status.append({
                        "ecosystem": ecosystem, "host": host,
                        "reachable": any(r["ecosystem"] == ecosystem and r["http_status"] is not None
                                        for r in results),
                        "note": "",
                    })

    # 私有源结论降级（§3.8.2，双向规则：不可判必须发声，不得静默通过）。
    if private_hosts_present:
        for r in results:
            if r["conclusion"] == "package_not_found":
                signal_hosts = [h for s in private_signals if s["ecosystem"] == r["ecosystem"]
                               for h in s["declared_hosts"]]
                if signal_hosts:
                    r["conclusion"] = "indeterminate"
                    r["reason_code"] = "private_source_possible"
                    r["statement"] = (f"在 {r['registry_host']} 上不存在；但项目声明了私有源 "
                                      f"{signal_hosts[0]}，平台不访问私有源、不读凭据 ⇒ "
                                      "无法判定该包是否存在。")

    for r in results:
        if r["conclusion"] == "indeterminate":
            evidence_gaps.append({
                "gap_id": f"dep_indeterminate_{r['ecosystem']}_{r['id_normalized']}",
                "evidence_type": "dependency_resolution",
                "description": f"{r['id']}@{r['requested_version']}：{r['statement']}",
                "blocking": False,
            })

    counts = {
        "manifests": len(manifests),
        "coordinates": len(coordinates),
        "resolvable": sum(1 for r in results if r["conclusion"] == "resolvable"),
        "unresolvable": sum(1 for r in results if r["conclusion"] in _UNRESOLVABLE_CONCLUSIONS),
        "indeterminate": sum(1 for r in results if r["conclusion"] == "indeterminate"),
        "not_checked": sum(1 for r in results if r["conclusion"] == "not_checked"),
    }
    status = "completed" if counts["indeterminate"] == 0 and not evidence_gaps else (
        "evidence_gap" if counts["resolvable"] == 0 and counts["unresolvable"] == 0
        else "partial_evidence_gap")

    return {
        "artifact_type": "p4_dependency_check",
        "kind": "p4_dependency_check",
        "stage": "p4",
        "run_id": run_id,
        "generated_at": _now_iso(),
        "schema": "p4_dependency_check_v1",
        "status": status,
        "counts": counts,
        "registries": registries_status,
        "manifests": [{"path": f"output_code/{m['path']}", "ecosystem": m["ecosystem"],
                       "format": m["format"], "parse_status": m["parse_status"],
                       "coordinate_count": m["coordinate_count"],
                       "sha256": m.get("sha256", ""), "bytes": m.get("bytes", 0)}
                      for m in manifests],
        "coordinates": results,
        "evidence_gaps": evidence_gaps,
        "private_source_signals": private_signals,
        "boundary_note": ("平台只负责尽早暴露依赖缺陷，不承诺替用户完成修复（R19-2-03）："
                          "未修改任何依赖清单、未降级版本、未推荐替代包。"),
        "independence_note": ("本结论来自本次真实 registry HTTP 响应，未读取任何 P5 结论"
                              "（R19-2 与 R19-1-03 取证链路独立）。"),
    }
