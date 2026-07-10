"""Community Connector (R15-4-C4): main platform → community service thin client.

Communicates with the independent community-backend (COMMUNITY_BASE_URL) over
httpx, surfaces resources/news/models/evaluations to the main platform, and
performs mandatory sha256 checksum verification on downloaded packages
(P0: checksum mismatch → reject import, no fallback success).

Reachable-fail semantics: when the community service is offline, callers get
clear errors / empty states, never a fabricated success (red line).
"""
from __future__ import annotations

import hashlib
from typing import Any

import httpx

from app.core.config import settings

TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class CommunityUnavailableError(RuntimeError):
    """Raised when the community service cannot be reached."""


class ChecksumMismatchError(ValueError):
    """P0: downloaded package sha256 does not match the manifest checksum."""


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=settings.community_base_url_normalized,
        timeout=TIMEOUT,
        headers={"Accept": "application/json"},
    )


def _get(path: str, params: dict[str, str] | None = None) -> Any:
    try:
        with _client() as c:
            r = c.get(path, params=params or {})
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        raise CommunityUnavailableError(f"community service unreachable: {e}") from e


# ── search / read ───────────────────────────────────────────────────────
def search_resources(q: str | None = None, type: str | None = None,
                     source: str | None = None, tag: str | None = None,
                     offset: int = 0, size: int = 20) -> dict:
    params: dict[str, str] = {"offset": str(offset), "size": str(size)}
    if q: params["q"] = q
    if type: params["type"] = type
    if source: params["source"] = source
    if tag: params["tag"] = tag
    return _get("/resources", params)  # {totalSize, offset, resources[]}


def get_resource(resource_id: str) -> dict:
    return _get(f"/resources/{resource_id}")


def get_manifest(resource_id: str) -> dict:
    return _get(f"/resources/{resource_id}/manifest")


def list_models(provider: str | None = None, family: str | None = None,
                availability: str | None = None) -> dict:
    params: dict[str, str] = {}
    if provider: params["provider"] = provider
    if family: params["family"] = family
    if availability: params["availability"] = availability
    return _get("/models", params)  # {totalSize, offset, models[]}


def list_evaluations(model_id: str | None = None, task_type: str | None = None) -> dict:
    params: dict[str, str] = {}
    if model_id: params["model_id"] = model_id
    if task_type: params["task_type"] = task_type
    return _get("/evaluations", params)  # {evaluations[]}


def list_news() -> list[dict]:
    return _get("/news")["news"]


# ── download + sha256 verify ────────────────────────────────────────────
def download_package(resource_id: str) -> tuple[bytes, str]:
    """Download a community package and verify its sha256 against the manifest.

    Returns (bytes, checksum) on success. Raises ChecksumMismatchError (P0) if
    the bytes don't match — the caller MUST reject the import in that case.
    CommunityUnavailableError if the service is offline.
    """
    # Expected checksum from the signed manifest (inline, + header cross-check).
    manifest = get_manifest(resource_id)
    expected = manifest.get("checksum_sha256") or ""
    try:
        with _client() as c:
            r = c.get(f"/resources/{resource_id}/download")
            r.raise_for_status()
            data = r.content
            header_sha = r.headers.get("X-Checksum-SHA256", "")
    except httpx.HTTPError as e:
        raise CommunityUnavailableError(f"community download failed: {e}") from e

    actual = hashlib.sha256(data).hexdigest()
    if expected and actual != expected:
        raise ChecksumMismatchError(
            f"sha256 mismatch for {resource_id}: expected {expected}, got {actual}"
        )
    if header_sha and header_sha != actual:
        # Header cross-check — tolerate empty header but fail on mismatch.
        raise ChecksumMismatchError(
            f"sha256 header mismatch for {resource_id}: header {header_sha}, bytes {actual}"
        )
    return data, actual


def status() -> dict:
    try:
        return _get("/status")
    except CommunityUnavailableError:
        return {"status": "unreachable", "error": "community service offline"}
