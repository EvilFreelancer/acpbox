"""Pydantic schemas for OpenAI-style and internal request/response bodies."""

from typing import Any, Literal

from pydantic import BaseModel, Field


# ----- OpenAI request/response (minimal) -----


class ChatCompletionMessagePart(BaseModel):
    type: Literal["text", "image_url"] = "text"
    text: str | None = None
    image_url: dict[str, str] | None = None


class ChatCompletionMessage(BaseModel):
    role: Literal["system", "user", "assistant", "developer"]
    content: str | list[ChatCompletionMessagePart] | None = None


class CreateChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatCompletionMessage]
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    metadata: dict[str, Any] | None = None


class ChatCompletionChoiceMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str | None = None


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatCompletionChoiceMessage
    finish_reason: Literal["stop", "length", "content_filter"] = "stop"


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class CreateChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: Usage | None = None


# ----- OpenAI Responses API (stateful) -----


class ResponseInputItem(BaseModel):
    role: str | None = None
    content: list[dict[str, Any]] | str | None = None


class CreateResponseRequest(BaseModel):
    model: str
    input: str | list[ResponseInputItem]
    instructions: str | None = None
    chat_id: str | None = None  # Extension: session id for stateful continuity
    metadata: dict[str, Any] | None = None


class ResponseOutputMessageContent(BaseModel):
    type: Literal["output_text"] = "output_text"
    text: str
    annotations: list[Any] = Field(default_factory=list)


class ResponseOutputMessage(BaseModel):
    type: Literal["message"] = "message"
    id: str | None = None
    status: Literal["completed"] = "completed"
    role: Literal["assistant"] = "assistant"
    content: list[ResponseOutputMessageContent] = Field(default_factory=list)


class ResponseUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class CreateResponseBody(BaseModel):
    id: str  # response_id
    object: Literal["response"] = "response"
    created_at: int
    status: Literal["completed"] = "completed"
    model: str
    output: list[ResponseOutputMessage] = Field(default_factory=list)
    usage: ResponseUsage | None = None
    chat_id: str | None = None  # Extension: return session id for next request


class DeletedResponse(BaseModel):
    id: str
    object: Literal["response"] = "response"
    deleted: Literal[True] = True


# ----- OpenAI Models API -----


class ModelObject(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str = "acp"


class ListModelsResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[ModelObject]


# ----- OpenAI error -----


class OpenAIErrorBody(BaseModel):
    code: str | None = None
    message: str
    type: str | None = None
