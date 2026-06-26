"""Import API — 仅支持官方社区 URL 导入（Agent/Skill/Resource/MCP/Case）。

规则：
- 测试阶段允许任意 HTTPS URL
- 下载内容为 JSON（metadata）或 zip（含文件）
- zip 自动解压到 source/<type>/<name>/
"""
import io
import json
import zipfile
from pathlib import Path

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_session
from app.schemas.common import SuccessEnvelope
from app.services.agent_service import AgentService
from app.services.mcp_service import MCPService
from app.services.registry_service import RegistryService
from app.services.skill_service import SkillService

import_router = APIRouter(prefix="/import", tags=["import"])

TIMEOUT = 30


def _validate_url(url: str) -> str:
    if not url or not url.startswith("https://"):
        raise HTTPException(status_code=422, detail="导入地址必须为 HTTPS URL")
    return url.strip()


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _unzip_to(data: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest)


def _load_json(data: bytes) -> dict:
    try:
        return json.loads(data)
    except Exception:
        raise HTTPException(status_code=422, detail="社区链接返回内容不是合法 JSON")


# ── Agent 导入 ──────────────────────────────────────────────────────────

@import_router.post("/agent")
async def import_agent(
    community_url: str = Body(..., embed=True),
    db: Session = Depends(get_session),
):
    """从官方社区 URL 导入 Agent（JSON）。"""
    url = _validate_url(community_url)
    raw = await _download(url)
    data = _load_json(raw)

    from app.schemas.agent import AgentCreate
    create_data = AgentCreate(
        agent_type=data.get("agent_type", "expert"),
        category="expert",
        name=data.get("name", "Imported Agent"),
        responsibilities=data.get("responsibilities", ""),
        forbidden=data.get("forbidden", ""),
        custom_prompt=data.get("custom_prompt"),
        bound_skills=data.get("bound_skills"),
        bound_tools=data.get("bound_tools"),
        bound_mcps=data.get("bound_mcps"),
        origin="community_import",
    )
    svc = AgentService(db)
    agent = svc.create(create_data)
    return SuccessEnvelope(data=svc.to_response(agent), meta={"source_status": "real"})


# ── Skill 导入 ──────────────────────────────────────────────────────────

@import_router.post("/skill")
async def import_skill(
    community_url: str = Body(..., embed=True),
    db: Session = Depends(get_session),
):
    """从官方社区 URL 导入 Skill（zip 自动解压 或 JSON）。"""
    url = _validate_url(community_url)
    raw = await _download(url)

    svc = SkillService(db)

    if url.endswith(".zip") or raw[:2] == b"PK":
        # zip 包：解压后从 manifest.json 读取元数据
        tmp_dir = settings.source_path / "skills" / "community_import"
        _unzip_to(raw, tmp_dir)
        manifest_path = tmp_dir / "manifest.json"
        data = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        dest = settings.source_path / "skills" / data.get("category", "other") / data.get("name", "imported")
        if tmp_dir != dest:
            import shutil
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(tmp_dir), str(dest))
        directory_path = str(dest)
    else:
        data = _load_json(raw)
        directory_path = None

    from app.schemas.skill import SkillCreate
    create_data = SkillCreate(
        name=data.get("name", "Imported Skill"),
        series=data.get("series", "P"),
        category=data.get("category", "other"),
        description=data.get("description", ""),
        skill_source="community",
        directory_path=directory_path,
    )
    skill = svc.create(create_data)
    return SuccessEnvelope(data=svc.to_response(skill), meta={"source_status": "real"})


# ── Resource 导入 ────────────────────────────────────────────────────────

@import_router.post("/resource")
async def import_resource(
    community_url: str = Body(..., embed=True),
    db: Session = Depends(get_session),
):
    """从官方社区 URL 导入资源（zip 或 JSON）。"""
    url = _validate_url(community_url)
    raw = await _download(url)

    svc = RegistryService(db)

    if url.endswith(".zip") or raw[:2] == b"PK":
        tmp_dir = settings.source_path / "resources" / "community_import"
        _unzip_to(raw, tmp_dir)
        manifest_path = tmp_dir / "manifest.json"
        data = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        name = data.get("name", "imported")
        dest = settings.source_path / "resources" / name
        if tmp_dir != dest:
            import shutil
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(tmp_dir), str(dest))
        source_path_or_ref = str(dest)
    else:
        data = _load_json(raw)
        source_path_or_ref = None

    from app.schemas.registry import ResourceCreate
    create_data = ResourceCreate(
        resource_type=data.get("resource_type", "tool"),
        name=data.get("name", "Imported Resource"),
        description=data.get("description", ""),
        source_type="community",
        risk_level=data.get("risk_level", "L1"),
        status=data.get("status", "draft"),
        capabilities=data.get("capabilities"),
        type_metadata=data.get("type_metadata"),
        source_path_or_ref=source_path_or_ref,
    )
    entry = svc.create(create_data)
    return SuccessEnvelope(data=RegistryService.to_response(entry), meta={"source_status": "real"})


# ── MCP 导入 ─────────────────────────────────────────────────────────────

@import_router.post("/mcp")
async def import_mcp(
    community_url: str = Body(..., embed=True),
    db: Session = Depends(get_session),
):
    """从官方社区 URL 导入 MCP 配置（JSON）。"""
    url = _validate_url(community_url)
    raw = await _download(url)
    data = _load_json(raw)

    svc = MCPService(db)
    srv = svc.create({
        "name": data.get("name", "Imported MCP"),
        "description": data.get("description", ""),
        "transport": data.get("transport", "stdio"),
        "command": data.get("command"),
        "args": data.get("args"),
        "env_vars": data.get("env_vars"),
        "sse_url": data.get("sse_url"),
        "origin": "community_import",
    })
    from app.schemas.common import Meta
    return SuccessEnvelope(
        data=MCPService.to_response(srv),
        meta=Meta(source_status="real", capability_status="available"),
    )


# ── Case 导入 ────────────────────────────────────────────────────────────

@import_router.post("/case")
async def import_case(
    community_url: str = Body(..., embed=True),
    db: Session = Depends(get_session),
):
    """从官方社区 URL 导入案例（zip 或 JSON，resource_type=case）。"""
    url = _validate_url(community_url)
    raw = await _download(url)

    svc = RegistryService(db)

    if url.endswith(".zip") or raw[:2] == b"PK":
        tmp_dir = settings.source_path / "cases" / "community_import"
        _unzip_to(raw, tmp_dir)
        manifest_path = tmp_dir / "manifest.json"
        data = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        name = data.get("name", "imported")
        dest = settings.source_path / "cases" / name
        if tmp_dir != dest:
            import shutil
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(tmp_dir), str(dest))
        source_path_or_ref = str(dest)
    else:
        data = _load_json(raw)
        source_path_or_ref = None

    from app.schemas.registry import ResourceCreate
    create_data = ResourceCreate(
        resource_type="case",
        name=data.get("name", "Imported Case"),
        description=data.get("description", ""),
        source_type="community",
        risk_level=data.get("risk_level", "L0"),
        status=data.get("status", "draft"),
        capabilities=data.get("capabilities"),
        type_metadata=data.get("type_metadata"),
        source_path_or_ref=source_path_or_ref,
    )
    entry = svc.create(create_data)
    return SuccessEnvelope(data=RegistryService.to_response(entry), meta={"source_status": "real"})
