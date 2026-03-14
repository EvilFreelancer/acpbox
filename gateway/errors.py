"""Map ACP errors to OpenAI-style error responses."""

from fastapi import status

from gateway.schemas import OpenAIErrorBody


def acp_code_to_http_status(code: str) -> int:
    """Map ACP error code to HTTP status code."""
    match code:
        case "invalid_input":
            return status.HTTP_400_BAD_REQUEST
        case "not_found":
            return status.HTTP_404_NOT_FOUND
        case "server_error" | _:
            return status.HTTP_500_INTERNAL_SERVER_ERROR


def openai_error_body(message: str, code: str = "server_error") -> dict:
    """Build OpenAI-style error body."""
    return OpenAIErrorBody(
        code=code,
        message=message,
        type="invalid_request_error" if code == "invalid_input" else "api_error",
    ).model_dump(exclude_none=True)
