"""Unit tests for gateway.session_store."""

import pytest

from gateway import session_store


def test_chat_id_or_new_returns_given():
    assert session_store.chat_id_or_new("existing-id") == "existing-id"


def test_chat_id_or_new_returns_new_uuid_when_none():
    out = session_store.chat_id_or_new(None)
    assert out is not None
    assert len(out) == 36
    assert out.count("-") == 4


def test_register_and_delete_response():
    # Use a fresh store by re-importing or assume tests run in isolation
    session_store.register_response("resp_1", "chat_1")
    session_store.register_response("resp_2", "chat_1")
    assert session_store.delete_response("resp_1") is True
    assert session_store.delete_response("resp_1") is False
    assert session_store.delete_response("resp_2") is True


def test_delete_session_removes_all_responses():
    session_store.register_response("r1", "sess_1")
    session_store.register_response("r2", "sess_1")
    assert session_store.delete_session("sess_1") is True
    assert session_store.delete_response("r1") is False
    assert session_store.delete_response("r2") is False
    assert session_store.delete_session("sess_1") is False


def test_delete_session_nonexistent():
    assert session_store.delete_session("nonexistent") is False
