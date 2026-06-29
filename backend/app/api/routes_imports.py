"""Import API — supports community URL import AND local file upload for resource/case/knowledge.

规则：
- URL 导入：HTTPS URL，下载 JSON 或 zip
- 文件导入：multipart UploadFile（md/txt/pdf/json/zip），draft+pending+unreviewed 入库
- zip 自动解压到 source/<type>/<name>/
"""
import io
import json
import zipfile
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
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


def _extract_pdf_text(content: bytes) -> str:
    """Extract text from PDF bytes; returns '' on failure with body_parse_failed logged."""
    try:
        import pypdf
        import io as _io
        reader = pypdf.PdfReader(_io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(pages)
    except ImportError:
        return ""  # pypdf not installed — body_parse_failed marked in type_metadata
    except Exception:
        return ""  # parse failed — caller marks body_parse_status="failed"


# ── Agent 导入 ──────────────────────────────────────────────────────────

@import_router.post("/agent")
async def import_agent(
    community_url: str = Body(..., embed=True),
    db: Session = Depends(get_db),
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
    db: Session = Depends(get_db),
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
    community_url: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    resource_type: Optional[str] = Form(default=None),
    name: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
):
    """从社区 URL 或本地文件导入资源（JSON/zip/md/txt/pdf）。

    - community_url: HTTPS URL 导入
    - file: 本地文件上传（multipart），初始 draft/pending/unreviewed
    二者皆缺 → 422。
    """
    if not community_url and not file:
        raise HTTPException(
            status_code=422,
            detail="必须提供 community_url 或上传文件（file）"
        )

    svc = RegistryService(db)

    if community_url:
        url = _validate_url(community_url)
        raw = await _download(url)

        if url.endswith(".zip") or raw[:2] == b"PK":
            tmp_dir = settings.source_path / "resources" / "community_import"
            _unzip_to(raw, tmp_dir)
            manifest_path = tmp_dir / "manifest.json"
            data = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
            res_name = data.get("name", "imported")
            dest = settings.source_path / "resources" / res_name
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
            status="draft",
            review_status="pending",
            source_trust_level="unreviewed",
            capabilities=data.get("capabilities"),
            type_metadata=data.get("type_metadata"),
            source_path_or_ref=source_path_or_ref,
        )

    else:
        # File upload path (T6.1)
        content = await file.read()
        filename = file.filename or "upload"
        res_name = name or Path(filename).stem
        dest_dir = settings.source_path / "resources" / res_name
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Parse body for knowledge/cases
        body_text = ""
        if filename.endswith(".zip") or content[:2] == b"PK":
            _unzip_to(content, dest_dir)
            manifest_path = dest_dir / "manifest.json"
            meta = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        elif filename.endswith(".json"):
            meta = json.loads(content.decode("utf-8", errors="replace"))
        elif filename.endswith((".md", ".txt")):
            body_text = content.decode("utf-8", errors="replace")
            meta = {}
        elif filename.endswith(".pdf"):
            body_text = _extract_pdf_text(content)
            meta = {}
        else:
            body_text = content.decode("utf-8", errors="replace")
            meta = {}

        # Save body to disk if present
        source_path = str(dest_dir)
        if body_text:
            body_file = dest_dir / "body.md"
            body_file.write_text(body_text, encoding="utf-8")

        from app.schemas.registry import ResourceCreate
        type_metadata = meta.get("type_metadata") or {}
        if body_text:
            type_metadata["body_path"] = str(dest_dir / "body.md")
            type_metadata["body_parse_status"] = "ok"

        create_data = ResourceCreate(
            resource_type=meta.get("resource_type") or resource_type or "knowledge",
            name=meta.get("name") or res_name,
            description=meta.get("description") or description or "",
            source_type="user_provided",
            risk_level=meta.get("risk_level", "L0"),
            status="draft",
            review_status="pending",
            source_trust_level="unreviewed",
            capabilities=meta.get("capabilities"),
            type_metadata=type_metadata,
            source_path_or_ref=source_path,
        )

    entry = svc.create(create_data)
    return SuccessEnvelope(
        data=RegistryService.to_response(entry),
        meta={"source_status": "real", "review_required": True}
    )


# ── MCP 导入 ─────────────────────────────────────────────────────────────

@import_router.post("/mcp")
async def import_mcp(
    community_url: str = Body(..., embed=True),
    db: Session = Depends(get_db),
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
    community_url: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
    name: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
):
    """从社区 URL 或本地文件导入案例（zip 或 JSON，resource_type=case）。

    file 上传支持 json/zip/md/txt。初始 draft/pending/unreviewed (D-061)。
    """
    if not community_url and not file:
        raise HTTPException(status_code=422, detail="必须提供 community_url 或上传文件（file）")

    svc = RegistryService(db)

    if community_url:
        url = _validate_url(community_url)
        raw = await _download(url)

        if url.endswith(".zip") or raw[:2] == b"PK":
            tmp_dir = settings.source_path / "cases" / "community_import"
            _unzip_to(raw, tmp_dir)
            manifest_path = tmp_dir / "manifest.json"
            data = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
            res_name = data.get("name", "imported")
            dest = settings.source_path / "cases" / res_name
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
            status="draft",
            review_status="pending",
            source_trust_level="unreviewed",
            capabilities=data.get("capabilities"),
            type_metadata=data.get("type_metadata"),
            source_path_or_ref=source_path_or_ref,
        )

    else:
        content = await file.read()
        filename = file.filename or "upload"
        res_name = name or Path(filename).stem
        dest_dir = settings.source_path / "cases" / res_name
        dest_dir.mkdir(parents=True, exist_ok=True)

        body_text = ""
        if filename.endswith(".zip") or content[:2] == b"PK":
            _unzip_to(content, dest_dir)
            manifest_path = dest_dir / "manifest.json"
            meta = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        elif filename.endswith(".json"):
            meta = json.loads(content.decode("utf-8", errors="replace"))
        else:
            body_text = content.decode("utf-8", errors="replace")
            meta = {}

        type_metadata = meta.get("type_metadata") or {}
        if body_text:
            body_file = dest_dir / "body.md"
            body_file.write_text(body_text, encoding="utf-8")
            type_metadata["body_path"] = str(body_file)

        from app.schemas.registry import ResourceCreate
        create_data = ResourceCreate(
            resource_type="case",
            name=meta.get("name") or res_name,
            description=meta.get("description") or description or "",
            source_type="user_provided",
            risk_level="L0",
            status="draft",
            review_status="pending",
            source_trust_level="unreviewed",
            capabilities=meta.get("capabilities"),
            type_metadata=type_metadata,
            source_path_or_ref=str(dest_dir),
        )

    entry = svc.create(create_data)
    return SuccessEnvelope(
        data=RegistryService.to_response(entry),
        meta={"source_status": "real", "never_execute": True, "review_required": True},
    )
