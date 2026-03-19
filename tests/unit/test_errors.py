"""Unit tests for acpbox.errors (ACP code -> HTTP status, OpenAI error body)."""

import pytest
from fastapi import status

from acpbox.errors import acp_code_to_http_status, openai_error_body


def test_acp_code_to_http_status_invalid_input():
    assert acp_code_to_http_status("invalid_input") == status.HTTP_400_BAD_REQUEST


def test_acp_code_to_http_status_not_found():
    assert acp_code_to_http_status("not_found") == status.HTTP_404_NOT_FOUND


def test_acp_code_to_http_status_server_error():
    assert acp_code_to_http_status("server_error") == status.HTTP_500_INTERNAL_SERVER_ERROR


def test_acp_code_to_http_status_unknown_defaults_to_500():
    assert acp_code_to_http_status("unknown_code") == status.HTTP_500_INTERNAL_SERVER_ERROR


def test_openai_error_body():
    body = openai_error_body("Something failed", "server_error")
    assert body["message"] == "Something failed"
    assert body["code"] == "server_error"
    assert "type" in body


def test_openai_error_body_invalid_input_type():
    body = openai_error_body("Bad input", "invalid_input")
    assert body["code"] == "invalid_input"
    assert body["type"] == "invalid_request_error"
