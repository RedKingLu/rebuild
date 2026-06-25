"""Model, Resource, and Integration placeholder API routes.

R4: All endpoints return not_connected / future status.
No real model calls, resource lookups, or integration actions.
"""

from fastapi import APIRouter

from app.dependencies import get_services
from app.schemas.common import SuccessEnvelope, Meta
from app.schemas.model import ModelProviderResponse, ModelProfileResponse, ModelBindingResponse
from app.schemas.resource import ResourceResponse, ResourceRegistryResponse
from app.schemas.integration import IntegrationResponse, GitStatusResponse, SourceImportRequest, SourceImportResponse


# === Model routes ===
model_router = APIRouter(prefix="/model", tags=["models"])


@model_router.get("/providers")
async def list_providers():
    get_services().trace_writer.write("state_change", action="list_providers", summary="Model providers (placeholder)")
    return SuccessEnvelope(
        data={"providers": []},
        meta=Meta(source_status="not_connected", capability_status="future",
                   not_connected_reason="ModelGateway planned for R5"),
    )


@model_router.get("/profiles")
async def list_profiles():
    return SuccessEnvelope(
        data={"profiles": []},
        meta=Meta(source_status="not_connected", capability_status="future"),
    )


@model_router.get("/projects/{project_id}/binding")
async def get_model_binding(project_id: str):
    return SuccessEnvelope(
        data=ModelBindingResponse(),
        meta=Meta(source_status="not_connected", capability_status="future"),
    )


# === Resource routes ===
resource_router = APIRouter(prefix="/resources", tags=["resources"])


@resource_router.get("")
async def list_resources():
    get_services().trace_writer.write("state_change", action="list_resources", summary="Resources (placeholder)")
    return SuccessEnvelope(
        data=ResourceRegistryResponse(),
        meta=Meta(source_status="not_connected", capability_status="future",
                   not_connected_reason="Resource registry planned for R6"),
    )


@resource_router.get("/registry")
async def get_registry():
    return SuccessEnvelope(
        data=ResourceRegistryResponse(),
        meta=Meta(source_status="not_connected", capability_status="future"),
    )


# === Integration routes ===
integration_router = APIRouter(prefix="/projects/{project_id}/integrations", tags=["integrations"])


@integration_router.get("")
async def list_integrations(project_id: str):
    get_services().trace_writer.write("state_change", action="list_integrations",
                                      summary="Integrations (placeholder)", project_id=project_id)
    return SuccessEnvelope(
        data={"integrations": []},
        meta=Meta(source_status="not_connected", capability_status="future",
                   not_connected_reason="Integration support planned for R7"),
    )


@integration_router.get("/git/status")
async def git_status(project_id: str):
    return SuccessEnvelope(
        data=GitStatusResponse(),
        meta=Meta(source_status="not_connected", capability_status="future"),
    )


@integration_router.post("/source/import")
async def source_import(project_id: str, req: SourceImportRequest):
    return SuccessEnvelope(
        data=SourceImportResponse(),
        meta=Meta(source_status="not_connected", capability_status="future",
                   not_connected_reason="Source import planned for R7"),
    )
