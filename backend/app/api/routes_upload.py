"""Upload API — zip 文件上传创建 Skill / Resource，以及 source 目录扫描入库。"""
import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.schemas.common import SuccessEnvelope
from app.services.registry_service import RegistryService
from app.services.skill_service import SkillService

upload_router = APIRouter(prefix="/upload", tags=["upload"])

VALID_SKILL_CATS = {"common", "p0", "p1", "p2", "p3", "p4", "p5", "p6", "other"}


def _unzip(data: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest)


# ── Skill zip 上传 ─────────────────────────────────────────────────────

@upload_router.post("/skill")
async def upload_skill(
    name: str = Form(...),
    category: str = Form(...),
    series: str = Form(default="P"),
    description: str = Form(default=""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """创建 Skill：提供名称、分类（9项），上传 zip 文件。后台自动解压到 source/skills/<category>/<name>/。"""
    if category not in VALID_SKILL_CATS:
        raise HTTPException(status_code=422, detail=f"category 须为: {', '.join(sorted(VALID_SKILL_CATS))}")
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=422, detail="请上传 .zip 文件")

    raw = await file.read()
    dest = settings.source_path / "skills" / category / name
    if dest.exists():
        raise HTTPException(status_code=409, detail=f"Skill '{name}' 在分类 '{category}' 下已存在")
    _unzip(raw, dest)

    from app.schemas.skill import SkillCreate
    svc = SkillService(db)
    create_data = SkillCreate(
        name=name,
        series=series.upper() if series.upper() in ("R", "P") else "P",
        category=category,
        description=description,
        skill_source="user_upload",
        directory_path=str(dest),
    )
    skill = svc.create(create_data)
    return SuccessEnvelope(data=svc.to_response(skill), meta={"source_status": "real"})


# ── Resource zip 上传 ──────────────────────────────────────────────────

@upload_router.post("/resource")
async def upload_resource(
    name: str = Form(...),
    resource_type: str = Form(...),
    description: str = Form(default=""),
    risk_level: str = Form(default="L1"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """创建其他资源：提供名称、类型，上传 zip 文件。后台自动解压到 source/resources/<name>/。"""
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=422, detail="请上传 .zip 文件")

    raw = await file.read()
    dest = settings.source_path / "resources" / name
    if dest.exists():
        raise HTTPException(status_code=409, detail=f"资源 '{name}' 已存在")
    _unzip(raw, dest)

    from app.schemas.registry import ResourceCreate
    svc = RegistryService(db)
    create_data = ResourceCreate(
        resource_type=resource_type,
        name=name,
        description=description,
        source_type="user_provided",
        risk_level=risk_level,
        status="draft",
        source_path_or_ref=str(dest),
    )
    entry = svc.create(create_data)
    return SuccessEnvelope(data=RegistryService.to_response(entry), meta={"source_status": "real"})


# ── source/skills 目录扫描入库 ────────────────────────────────────────

@upload_router.post("/skill/scan")
async def scan_skills(db: Session = Depends(get_db)):
    """扫描 source/skills/<category>/<skill_name>/ 目录，将未入库的 Skill 批量注册。
    SKILL.md 第一行作为 name，目录名作为 fallback。
    """
    svc = SkillService(db)
    base = settings.source_path / "skills"
    imported = []
    skipped = []

    for cat_dir in sorted(base.iterdir()):
        if not cat_dir.is_dir():
            continue
        category = cat_dir.name
        if category not in VALID_SKILL_CATS:
            continue
        for skill_dir in sorted(cat_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            # 从 SKILL.md 第一行提取 name（去掉 # 前缀）
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                first_line = skill_md.read_text(encoding="utf-8").splitlines()[0]
                name = first_line.lstrip("#").strip() or skill_dir.name
            else:
                name = skill_dir.name

            # 检查是否已入库（按 directory_path）
            existing, _ = svc.list_all(limit=1000, offset=0)
            already = any(s.directory_path == str(skill_dir) for s in existing)
            if already:
                skipped.append(name)
                continue

            from app.schemas.skill import SkillCreate
            create_data = SkillCreate(
                name=name,
                series="P",
                category=category,
                description="",
                skill_source="platform",
                directory_path=str(skill_dir),
            )
            skill = svc.create(create_data)
            imported.append(svc.to_response(skill))

    return SuccessEnvelope(
        data={"imported": len(imported), "skipped": len(skipped), "skills": imported},
        meta={"source_status": "real"},
    )
