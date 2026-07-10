"""Knowledge package zip importer (R15-4-C7).

Imports a knowledge zip (manifest.json + multiple .md docs) used to migrate the
old static Docs/Help content into the Knowledge registry. Each .md doc becomes a
Knowledge-type ResourceEntry with its Markdown body stored on disk (body_path).

Endpoint:
  POST /api/knowledge/import-package  (multipart: file=<zip>)
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.schemas.common import SuccessEnvelope
from app.schemas.registry import ResourceResponse
from app.services.registry_service import RegistryService

knowledge_router = APIRouter(prefix="/knowledge", tags=["knowledge"])

DOC_EXTS = (".md", ".txt")


def _unzip_to(data: bytes, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest)


@knowledge_router.post("/import-package", response_model=SuccessEnvelope)
async def import_knowledge_package(
    file: UploadFile = File(...),
    scope: str = Form("platform"),          # "platform" | "user"
    source: str = Form("official"),         # official knowledge package
    db: Session = Depends(get_db),
):
    """Import a knowledge zip package (manifest.json + .md docs).

    Creates one Knowledge ResourceEntry per document. The zip is unpacked under
    source/knowledge/<package-name>/; each doc's Markdown body is stored on disk
    and referenced via type_metadata.body_path.
    """
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=422, detail="知识包必须是 .zip 文件")

    content = await file.read()
    if content[:2] != b"PK":
        raise HTTPException(status_code=422, detail="无效的 zip 文件")

    # Read manifest for package metadata
    try:
        manifest = zipfile.ZipFile(io.BytesIO(content)).read("manifest.json")
        meta = json.loads(manifest.decode("utf-8"))
    except Exception:
        meta = {}

    pkg_name = meta.get("name") or Path(file.filename).stem
    pkg_version = meta.get("version", "1.0.0")
    pkg_description = meta.get("description", "")
    doc_files = meta.get("files") or []  # optional ordered list

    svc = RegistryService(db)
    base_dir = settings.source_path / "knowledge" / pkg_name
    _unzip_to(content, base_dir)

    # Discover document files: honor manifest.files order, else scan the zip.
    discovered: list[str] = []
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for n in zf.namelist():
            p = Path(n)
            if p.name.startswith(".") or p.name.startswith("__"):
                continue
            if p.suffix.lower() in DOC_EXTS and p.name != "manifest.json":
                discovered.append(str(p))

    ordered: list[str] = []
    for f in doc_files:
        if f in discovered:
            ordered.append(f)
    for f in discovered:
        if f not in ordered:
            ordered.append(f)

    if not ordered:
        raise HTTPException(status_code=422, detail="知识包中未找到任何 .md/.txt 文档")

    created = []
    for rel in ordered:
        p = base_dir / rel
        if not p.exists():
            continue
        body_text = p.read_text(encoding="utf-8", errors="replace")
        doc_name = p.stem.replace("-", " ").replace("_", " ").title() or p.stem
        type_metadata = {
            "body_path": str(p),
            "body_parse_status": "ok",
            "knowledge_package": pkg_name,
            "package_version": pkg_version,
            "source_filename": rel,
        }
        from app.models.resource_entry import ResourceEntry, ResourceStatus, SourceType, TrustLevel, RiskLevel
        entry = ResourceEntry(
            name=doc_name,
            resource_type="knowledge",
            description=pkg_description or doc_name,
            version=pkg_version,
            source_type=SourceType.internal_current if scope == "platform" else SourceType.user_provided,
            source_trust_level=TrustLevel.read_only_reference,
            source_path_or_ref=str(base_dir),
            risk_level=RiskLevel.L0,
            status=ResourceStatus.active,
            enabled=True,
            capabilities={"tags": meta.get("tags") or [], "categories": meta.get("categories") or []},
            type_metadata=type_metadata,
        )
        db.add(entry)
        db.flush()
        created.append(entry)

    db.commit()
    for e in created:
        db.refresh(e)

    return SuccessEnvelope(
        data={
            "package": pkg_name,
            "version": pkg_version,
            "imported_count": len(created),
            "resources": [ResourceResponse(**svc.to_response(e)).model_dump() for e in created],
        },
        meta={"source_status": "real", "scope": scope, "base_dir": str(base_dir)},
    )


@knowledge_router.get("/packages")
def list_packages(db: Session = Depends(get_db)):
    """List knowledge resources (grouped by knowledge_package in frontend)."""
    svc = RegistryService(db)
    from app.models.resource_entry import ResourceEntry
    from sqlalchemy import select
    stmt = select(ResourceEntry).where(ResourceEntry.resource_type == "knowledge")
    rows = db.execute(stmt).scalars().all()
    return SuccessEnvelope(
        data={"total": len(rows),
               "resources": [ResourceResponse(**svc.to_response(e)).model_dump() for e in rows]},
        meta={"source_status": "real"},
    )
