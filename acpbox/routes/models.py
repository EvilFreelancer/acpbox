"""OpenAI /v1/models from ACP agent modes (session/new availableModes)."""

import logging
import time

from fastapi import APIRouter, HTTPException, Request

from acpbox.acp_stdio import AcpStdioError
from acpbox.errors import openai_error_body
from acpbox.schemas import ListModelsResponse, ModelObject

router = APIRouter(prefix="/v1/models", tags=["Models"])
logger = logging.getLogger(__name__)


@router.get("", response_model=ListModelsResponse)
async def list_models(request: Request) -> ListModelsResponse:
    """GET /v1/models returns agent modes (e.g. plan, build) from session/new availableModes."""
    runner = request.app.state.runner
    try:
        models_list = await runner.get_agent_models()
    except AcpStdioError as e:
        logger.warning("Failed to get models from ACP agent: %s", e)
        raise HTTPException(
            status_code=503,
            detail=openai_error_body(str(e), "server_error"),
        ) from e
    created = int(time.time())
    return ListModelsResponse(
        data=[
            ModelObject(id=mid, created=created, owned_by="acp")
            for mid in models_list
        ],
    )


@router.get("/{model_id}", response_model=ModelObject)
async def get_model(model_id: str, request: Request) -> ModelObject:
    """GET /v1/models/{model_id} returns model if in agent modes."""
    runner = request.app.state.runner
    try:
        models_list = await runner.get_agent_models()
    except AcpStdioError as e:
        logger.warning("Failed to get models from ACP agent: %s", e)
        raise HTTPException(
            status_code=503,
            detail=openai_error_body(str(e), "server_error"),
        ) from e
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
