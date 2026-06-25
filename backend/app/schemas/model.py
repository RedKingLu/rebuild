"""Model domain schemas — placeholder for R5 ModelGateway."""

from typing import Optional
from pydantic import BaseModel, Field


class ModelProviderResponse(BaseModel):
    provider_id: str = ""
    name: str = ""
    api_format: str = "openai"
    source_status: str = "not_connected"
    capability_status: str = "future"


class ModelProfileResponse(BaseModel):
    profile_id: str = ""
    provider_id: str = ""
    model: str = ""
    display_name: str = ""
    supports_streaming: bool = True
    source_status: str = "not_connected"
    capability_status: str = "future"


class ModelBindingResponse(BaseModel):
    binding_id: str = ""
    project_id: str = ""
    profile_id: str = ""
    source_status: str = "not_connected"
