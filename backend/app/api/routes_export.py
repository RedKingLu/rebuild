"""Export API — 将 Skill / Agent / Resource 打包为 zip 触发下载。"""
import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.agent_service import AgentService
from app.services.registry_service import RegistryService
from app.services.skill_service import SkillService

export_router = APIRouter(prefix="/export", tags=["export"])


def _zip_dir(path: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in path.rglob("*"):
            if fp.is_file():
                zf.write(fp, fp.relative_to(path))
    buf.seek(0)
    return buf.read()


def _zip_dict(data: dict, filename: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, json.dumps(data, ensure_ascii=False, indent=2, default=str))
    buf.seek(0)
    return buf.read()


def _stream(data: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Skill 导出 ─────────────────────────────────────────────────────────

@export_router.get("/skill/{skill_id}")
def export_skill(skill_id: str, db: Session = Depends(get_db)):
    svc = SkillService(db)
    skill = svc.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    safe_name = skill.name.replace("/", "_").replace(" ", "_")
    filename = f"{safe_name}.zip"

    if skill.directory_path:
        d = Path(skill.directory_path)
        if d.exists() and d.is_dir():
            return _stream(_zip_dir(d), filename)

    # 无目录时将 metadata 打包
    data = SkillService.to_response(skill)
    return _stream(_zip_dict(data, "manifest.json"), filename)


# ── Agent 导出 ────────────────────────────────────────────────────────

@export_router.get("/agent/{agent_id}")
def export_agent(agent_id: str, db: Session = Depends(get_db)):
    svc = AgentService(db)
    agent = svc.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    safe_name = agent.name.replace("/", "_").replace(" ", "_")
    filename = f"{safe_name}.zip"
    data = svc.to_response(agent)
    return _stream(_zip_dict(data, "agent.json"), filename)


# ── Resource 导出 ─────────────────────────────────────────────────────

@export_router.get("/resource/{resource_id}")
def export_resource(resource_id: str, db: Session = Depends(get_db)):
    svc = RegistryService(db)
    entry = svc.get(resource_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Resource not found")

    safe_name = entry.name.replace("/", "_").replace(" ", "_")
    filename = f"{safe_name}.zip"

    if entry.source_path_or_ref:
        d = Path(entry.source_path_or_ref)
        if d.exists() and d.is_dir():
            return _stream(_zip_dir(d), filename)

    data = RegistryService.to_response(entry)
    return _stream(_zip_dict(data, "manifest.json"), filename)
