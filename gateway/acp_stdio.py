"""
ACP client over stdio: spawn agent subprocess, send JSON-RPC via stdin, read from stdout.

One process per "session" or per request; no HTTP. See docs/agent-client-protocol/docs/protocol/transports.mdx.
"""

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class AcpStdioError(Exception):
    """Raised when ACP stdio handshake or call fails."""


async def _write_message(stream: asyncio.StreamWriter, obj: dict[str, Any]) -> None:
    """Send one JSON-RPC message (newline-delimited, no embedded newlines)."""
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    stream.write(line.encode("utf-8"))
    await stream.drain()


async def _read_message(stream: asyncio.StreamReader) -> dict[str, Any] | None:
    """Read one newline-delimited JSON-RPC message. Returns None on EOF."""
    line = await stream.readline()
    if not line:
        return None
    return json.loads(line.decode("utf-8").strip())


async def _request(
    writer: asyncio.StreamWriter,
    reader: asyncio.StreamReader,
    msg_id: int,
    method: str,
    params: dict[str, Any] | None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Send JSON-RPC request and wait for response with same id. Raises on error or timeout."""
    req = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
    await _write_message(writer, req)
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise AcpStdioError(f"Timeout waiting for response to {method}")
        try:
            msg = await asyncio.wait_for(_read_message(reader), timeout=min(remaining, 60.0))
        except asyncio.TimeoutError:
            continue
        if msg is None:
            raise AcpStdioError(f"EOF while waiting for response to {method}")
        if "id" in msg and msg["id"] == msg_id:
            if "error" in msg:
                err = msg["error"]
                raise AcpStdioError(
                    err.get("message", "Unknown error") or f"code={err.get('code')}"
                )
            return msg.get("result") or {}
        # Not our response (e.g. notification); caller should handle in read loop
        if "method" in msg:
            logger.debug("Unexpected notification while waiting for response: %s", msg.get("method"))
        continue


async def run_single_turn(
    command: list[str],
    env: dict[str, str],
    cwd: str | None,
    prompt_blocks: list[dict[str, Any]],
    *,
    request_timeout: float = 300.0,
) -> tuple[str, str]:
    """
    Spawn ACP agent, perform initialize -> session/new -> session/prompt, collect agent text
    from session/update (agent_message_chunk), return (aggregated_text, stop_reason).
    """
    env_full = dict(os.environ) | env
    work_dir = cwd or os.getcwd()
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env_full,
        cwd=work_dir,
    )
    assert proc.stdin and proc.stdout
    stdin_writer: asyncio.StreamWriter = proc.stdin
    stdout_reader: asyncio.StreamReader = proc.stdout

    collected_text: list[str] = []
    stop_reason = "end_turn"
    next_id = 0

    try:
        # initialize
        init_result = await _request(
            stdin_writer,
            stdout_reader,
            next_id,
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {"name": "acp-openapi-gateway", "version": "0.1.0"},
            },
            timeout=request_timeout,
        )
        next_id += 1
        logger.debug("initialize result: %s", init_result)

        # session/new
        new_result = await _request(
            stdin_writer,
            stdout_reader,
            next_id,
            "session/new",
            {"cwd": work_dir, "mcpServers": []},
            timeout=request_timeout,
        )
        next_id += 1
        session_id = new_result.get("sessionId")
        if not session_id:
            raise AcpStdioError("session/new did not return sessionId")

        # session/prompt: we send the request then read until we get the response for this id
        prompt_id = next_id
        req = {
            "jsonrpc": "2.0",
            "id": prompt_id,
            "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": prompt_blocks},
        }
        await _write_message(stdin_writer, req)

        # Read messages until we get session/prompt response (same id)
        while True:
            msg = await asyncio.wait_for(_read_message(stdout_reader), timeout=request_timeout)
            if msg is None:
                raise AcpStdioError("EOF before session/prompt response")
            if "id" in msg and msg["id"] == prompt_id:
                if "error" in msg:
                    err = msg["error"]
                    raise AcpStdioError(
                        err.get("message", "Unknown error") or f"code={err.get('code')}"
                    )
                result = msg.get("result") or {}
                stop_reason = result.get("stopReason", "end_turn")
                break
            if msg.get("method") == "session/update":
                params = msg.get("params") or {}
                update = params.get("update") or {}
                if update.get("sessionUpdate") == "agent_message_chunk":
                    content = update.get("content") or {}
                    if isinstance(content, dict) and content.get("type") == "text":
                        collected_text.append(content.get("text") or "")
                # session/request_permission: auto-allow for gateway
                continue
            if msg.get("method") == "session/request_permission":
                # Reply with allow_once so agent can continue
                perm_id = msg.get("id")
                if perm_id is not None:
                    reply = {
                        "jsonrpc": "2.0",
                        "id": perm_id,
                        "result": {"outcome": "allow_once"},
                    }
                    await _write_message(stdin_writer, reply)
                continue
            logger.debug("ACP message: %s", msg.get("method") or msg)

        return ("".join(collected_text), stop_reason)
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        if proc.stderr:
            try:
                stderr = await proc.stderr.read()
                if stderr:
                    logger.debug("ACP stderr: %s", stderr.decode(errors="replace"))
            except Exception:
                pass
