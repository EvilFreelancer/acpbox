"""OpenAI /v1/models from config (no ACP process)."""

import time

from fastapi import APIRouter, HTTPException, Request

from gateway.errors import openai_error_body
from gateway.schemas import ListModelsResponse, ModelObject

router = APIRouter(prefix="/v1/models", tags=["Models"])


@router.get("", response_model=ListModelsResponse)
async def list_models(request: Request) -> ListModelsResponse:
    """GET /v1/models returns list from config.acp.models (no agent spawn)."""
    config = request.app.state.config
    models_list = config.acp.models
    created = int(time.time())
    return ListModelsResponse(
        data=[
            ModelObject(id=mid, created=created, owned_by="acp")
            for mid in models_list
        ],
    )


@router.get("/{model_id}", response_model=ModelObject)
async def get_model(model_id: str, request: Request) -> ModelObject:
    """GET /v1/models/{model_id} returns model if in config list."""
    config = request.app.state.config
    models_list = config.acp.models
    if model_id not in models_list:
        raise HTTPException(
            status_code=404,
            detail=openai_error_body("The model does not exist.", "not_found"),
        )
    return ModelObject(
        id=model_id,
        created=int(time.time()),
        owned_by="acp",
    )
