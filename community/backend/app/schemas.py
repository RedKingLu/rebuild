"""Pydantic response schemas for the community service (R15-4-C3)."""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel


class StatusResponse(BaseModel):
    status: str = "ok"
    version: str
    resource_count: int
    model_count: int
    evaluation_count: int
    doc_count: int = 0


class ResourceCard(BaseModel):
    id: str
    resource_type: str
    name: str
    display_name: str = ""
    description: str = ""
    namespace: str = ""
    publisher: str = ""
    version: str = "1.0.0"
    tags: list[str] = []
    categories: list[str] = []
    license: str = ""
    source: str = "community"
    verified: bool = False
    deprecated: bool = False
    download_count: int = 0
    icon_url: str | None = None
    checksum_sha256: str = ""
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ResourceDetail(ResourceCard):
    readme: str = ""
    versions: list[str] = []
    files: list[dict] = []
    dependencies: list = []
    manifest_ref: str = ""
    created_at: datetime | None = None


class ResourceListResponse(BaseModel):
    # Open VSX-style paging contract (R15-3 Δ1)
    totalSize: int
    offset: int
    resources: list[ResourceCard]


class ManifestFile(BaseModel):
    name: str
    size: int
    sha256: str = ""


class ManifestResponse(BaseModel):
    resource_id: str
    version: str
    type: str
    files: list[ManifestFile] = []
    checksum_sha256: str = ""
    dependencies: list = []


# R16-B E3: 社区资源版本历史响应（仅当前版本有真实 checksum/files，历史版本用于追溯展示）
class CommunityResourceVersion(BaseModel):
    version: str
    manifest_ref: str = ""          # 指向 /resources/{id}/versions/{v}/manifest（当前版本唯一有效）
    deprecated: bool = False


class VersionListResponse(BaseModel):
    resource_id: str
    versions: list[CommunityResourceVersion]
    current_version: str
    note: str = ""                  # 诚实说明：如 "当前仅支持导入最新版本；历史版本记录用于追溯"


class NewsItem(BaseModel):
    id: str
    title: str
    summary: str = ""
    body: str = ""
    published_at: datetime | None = None
    url: str | None = None
    pinned: bool = False

    model_config = {"from_attributes": True}


class NewsResponse(BaseModel):
    news: list[NewsItem]


class CommunityDocItem(BaseModel):
    slug: str
    title: str
    summary: str = ""
    category: str = ""
    tags: list[str] = []
    version: str = "1.0.0"
    source: str = "community"
    updated_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class CommunityDocDetail(CommunityDocItem):
    body_markdown: str = ""


class CommunityDocListResponse(BaseModel):
    docs: list[CommunityDocItem]
    total: int


class ModelEntry(BaseModel):
    model_id: str
    display_name: str = ""
    provider_id: str = ""
    family: str = ""
    model_version: str = ""
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_modalities: list[str] = []
    output_modalities: list[str] = []
    capability_tags: list[str] = []
    task_tags: list[str] = []
    license: str | None = None
    official_icon_url: str | None = None
    official_url: str | None = None
    availability_status: str = "available"
    source: str = "community"
    updated_at: datetime | None = None

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class ModelListResponse(BaseModel):
    totalSize: int
    offset: int
    models: list[ModelEntry]


class EvaluationItem(BaseModel):
    eval_id: str
    model_id: str
    agent_type: str | None = None
    task_type: str | None = None
    scenario: str | None = None
    metric: str | None = None
    score: float | None = None
    success_rate: float | None = None
    cost_level: str | None = None
    latency_level: str | None = None
    sample_count: int | None = None
    eval_method: str | None = None
    eval_version: str | None = None
    source: str | None = None
    published_at: datetime | None = None
    limitations: str | None = None

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class EvaluationListResponse(BaseModel):
    evaluations: list[EvaluationItem]
