"""In-memory store for chat_id (session) and response_id mapping."""

import uuid
from typing import Any

# chat_id (our session id) -> ACP session_id (UUID string). We use chat_id as session_id for ACP.
# response_id -> optional chat_id for deletion mapping
_response_to_chat: dict[str, str] = {}
_chat_to_responses: dict[str, set[str]] = {}  # chat_id -> set of response_ids


def register_response(response_id: str, chat_id: str) -> None:
    """Record that this response_id belongs to chat_id."""
    _response_to_chat[response_id] = chat_id
    _chat_to_responses.setdefault(chat_id, set()).add(response_id)


def delete_response(response_id: str) -> bool:
    """Remove response_id from store. Returns True if it existed."""
    chat_id = _response_to_chat.pop(response_id, None)
    if chat_id and chat_id in _chat_to_responses:
        _chat_to_responses[chat_id].discard(response_id)
        if not _chat_to_responses[chat_id]:
            del _chat_to_responses[chat_id]
    return chat_id is not None


def delete_session(chat_id: str) -> bool:
    """Remove all response_ids for this chat_id. Returns True if session existed."""
    response_ids = _chat_to_responses.pop(chat_id, None)
    if response_ids:
        for rid in response_ids:
            _response_to_chat.pop(rid, None)
        return True
    return False


def chat_id_or_new(chat_id: str | None) -> str:
    """Return chat_id if provided, otherwise a new UUID string (ACP session_id)."""
    return chat_id if chat_id else str(uuid.uuid4())
