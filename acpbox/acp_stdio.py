"""
ACP client over stdio: one long-lived agent process per worker (uvicorn worker).

Each worker holds one AcpRunner that keeps a single ACP subprocess; all requests
in that worker reuse it (session/new + session/prompt per request). With 8 workers
you get 8 ACP binary instances. See docs/agent-client-protocol/docs/protocol/transports.mdx.
"""

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from collections.abc import AsyncGenerator
from typing import Any, Literal, Sequence

TurnStreamEvent = (
    tuple[Literal["text"], str]
    | tuple[Literal["done"], str]
    | tuple[Literal["session"], dict[str, Any]]
)

from acp.schema import TextContentBlock

logger = logging.getLogger(__name__)

# Default asyncio subprocess StreamReader limit is 64 KiB; ACP JSON-RPC lines (e.g. large
# tool lists from opencode) can exceed that. Pass limit= to create_subprocess_exec and use
# readuntil so one line may grow up to _MAX_JSON_LINE bytes.
_MAX_JSON_LINE = 32 * 1024 * 1024


class AcpStdioError(Exception):
    """Raised when ACP stdio handshake or call fails."""


async def _write_message(stream: asyncio.StreamWriter, obj: dict[str, Any]) -> None:
    """Send one JSON-RPC message (newline-delimited, no embedded newlines)."""
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    stream.write(line.encode("utf-8"))
    await stream.drain()


async def _read_message(stream: asyncio.StreamReader) -> dict[str, Any] | None:
    """Read one newline-delimited JSON-RPC message. Returns None on EOF."""
    try:
        line = await stream.readuntil(b"\n")
    except asyncio.IncompleteReadError as e:
        if not e.partial:
            return None
        line = e.partial
    except asyncio.LimitOverrunError as e:
        raise AcpStdioError(
            f"ACP JSON-RPC line exceeds {_MAX_JSON_LINE} bytes; increase _MAX_JSON_LINE in acp_stdio.py"
        ) from e
    if not line.strip():
        return None
    return json.loads(line.decode("utf-8").strip())


# Heuristic for stderr_pipe_split: route these to process stderr, rest to stdout.
_STDERR_LINE_ERROR_HINT = re.compile(
    r"(?i)(error|exception|traceback|fatal|panic|failed\b|errno|warn(ing)?\b)"
)


async def _drain_stderr(
    stream: asyncio.StreamReader,
    *,
    pipe_split: bool = False,
) -> None:
    """
    Read child stderr until EOF. Required when stderr=PIPE (otherwise the pipe buffer fills).

    Forwards lines to the gateway process streams so Docker shows them on stdout/stderr:
    - pipe_split False: all lines to sys.stderr
    - pipe_split True: error-like lines to sys.stderr, others to sys.stdout
    """
    try:
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if not text:
                continue
            if pipe_split:
                if _STDERR_LINE_ERROR_HINT.search(text):
                    sys.stderr.write(text + "\n")
                    sys.stderr.flush()
                else:
                    sys.stdout.write(text + "\n")
                    sys.stdout.flush()
            else:
                sys.stderr.write(text + "\n")
                sys.stderr.flush()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug("ACP stderr drain stopped: %s", e)


def _subprocess_stderr_arg(mode: str) -> int | None:
    """Map config stderr mode to asyncio subprocess stderr argument."""
    if mode == "inherit":
        return None
    if mode == "devnull":
        return asyncio.subprocess.DEVNULL
    if mode == "pipe":
        return asyncio.subprocess.PIPE
    raise ValueError(f"unknown ACP stderr mode: {mode!r}")


def _resolved_workspace_dir(workspace: str) -> str:
    """Absolute path for ACP session/new cwd."""
    return str(Path(workspace).expanduser().resolve())


def _serialize_prompt_blocks(blocks: Sequence[TextContentBlock | dict[str, Any]]) -> list[dict[str, Any]]:
    """Serialize ContentBlock models or plain dicts to JSON-serializable dicts."""
    serialized: list[dict[str, Any]] = []
    for b in blocks:
        if isinstance(b, TextContentBlock):
            serialized.append(b.model_dump(mode="json"))
        else:
            serialized.append(b)
    return serialized


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
        if "method" in msg:
            logger.debug("Unexpected notification while waiting for response: %s", msg.get("method"))
        continue


class AcpRunner:
    """
    One ACP agent process per worker. Started in lifespan, reused for all requests in this worker.
    Use a lock so only one request at a time uses the process (stdio is single-stream).
    """

    def __init__(
        self,
        command: list[str],
        env: dict[str, str],
        workspace: str,
        *,
        stderr_mode: Literal["inherit", "pipe", "devnull"] = "inherit",
        stderr_pipe_split: bool = False,
    ) -> None:
        self._command = command
        self._env = dict(os.environ) | env
        self._workspace_dir = _resolved_workspace_dir(workspace)
        self._stderr_mode = stderr_mode
        self._stderr_pipe_split = stderr_pipe_split
        self._proc: asyncio.subprocess.Process | None = None
        self._stdin_writer: asyncio.StreamWriter | None = None
        self._stdout_reader: asyncio.StreamReader | None = None
        self._next_id = 0
        self._initialized = False
        self._lock = asyncio.Lock()
        self._stderr_drain_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Spawn the ACP process and run initialize."""
        if self._proc is not None:
            return
        logger.info(
            "Starting ACP agent (one per worker): %s (stderr=%s)",
            self._command,
            self._stderr_mode,
        )
        stderr_arg = _subprocess_stderr_arg(self._stderr_mode)
        self._proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr_arg,
            env=self._env,
            cwd=self._workspace_dir,
            limit=_MAX_JSON_LINE,
        )
        assert self._proc.stdin and self._proc.stdout
        self._stdin_writer = self._proc.stdin
        self._stdout_reader = self._proc.stdout
        if self._stderr_mode == "pipe" and self._proc.stderr is not None:
            self._stderr_drain_task = asyncio.create_task(
                _drain_stderr(self._proc.stderr, pipe_split=self._stderr_pipe_split)
            )
        await self._do_initialize()

    async def _do_initialize(self) -> None:
        """Send initialize and set _initialized."""
        if self._initialized or not self._stdin_writer or not self._stdout_reader:
            return
        await _request(
            self._stdin_writer,
            self._stdout_reader,
            self._next_id,
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {"name": "acpbox", "version": "0.1.0"},
            },
            timeout=30.0,
        )
        self._next_id += 1
        self._initialized = True
        logger.debug("ACP agent initialized")

    async def get_agent_models(self) -> list[str]:
        """Return model ids from session/new modes.availableModes. Serialized with _lock."""
        async with self._lock:
            return await self._get_agent_models_unsafe()

    async def _get_agent_models_unsafe(self) -> list[str]:
        if not self._stdin_writer or not self._stdout_reader:
            raise AcpStdioError("ACP process not started")
        await self._do_initialize()
        new_result = await _request(
            self._stdin_writer,
            self._stdout_reader,
            self._next_id,
            "session/new",
            {"cwd": self._workspace_dir, "mcpServers": []},
            timeout=30.0,
        )
        self._next_id += 1
        agent_name: str | None = None
        modes = new_result.get("modes")
        if isinstance(modes, dict):
            available = modes.get("availableModes")
            if isinstance(available, list) and len(available) > 0:
                ids = []
                for m in available:
                    if isinstance(m, dict) and m.get("id"):
                        ids.append(str(m["id"]))
                if ids:
                    return ids
        return [agent_name or "default"]

    async def run_turn(
        self,
        prompt_blocks: Sequence[TextContentBlock | dict[str, Any]],
        mode_id: str | None = None,
        request_timeout: float = 300.0,
    ) -> tuple[str, str, list[dict[str, Any]]]:
        """Run one turn: session/new -> [session/set_mode] -> session/prompt. Serialized with _lock."""
        async with self._lock:
            return await self._run_turn_unsafe(prompt_blocks, mode_id, request_timeout)

    async def _run_turn_unsafe(
        self,
        prompt_blocks: Sequence[TextContentBlock | dict[str, Any]],
        mode_id: str | None,
        request_timeout: float,
    ) -> tuple[str, str, list[dict[str, Any]]]:
        if not self._stdin_writer or not self._stdout_reader:
            raise AcpStdioError("ACP process not started")
        await self._do_initialize()
        prompt_payload = _serialize_prompt_blocks(prompt_blocks)
        new_result = await _request(
            self._stdin_writer,
            self._stdout_reader,
            self._next_id,
            "session/new",
            {"cwd": self._workspace_dir, "mcpServers": []},
            timeout=request_timeout,
        )
        self._next_id += 1
        session_id = new_result.get("sessionId")
        if not session_id:
            raise AcpStdioError("session/new did not return sessionId")
        if mode_id:
            await _request(
                self._stdin_writer,
                self._stdout_reader,
                self._next_id,
                "session/set_mode",
                {"sessionId": session_id, "modeId": mode_id},
                timeout=request_timeout,
            )
            self._next_id += 1
        prompt_id = self._next_id
        req = {
            "jsonrpc": "2.0",
            "id": prompt_id,
            "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": prompt_payload},
        }
        await _write_message(self._stdin_writer, req)
        collected_text: list[str] = []
        session_updates: list[dict[str, Any]] = []
        stop_reason = "end_turn"
        while True:
            msg = await asyncio.wait_for(_read_message(self._stdout_reader), timeout=request_timeout)
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
                if not isinstance(update, dict):
                    continue
                sut = update.get("sessionUpdate")
                if sut == "agent_message_chunk":
                    content = update.get("content") or {}
                    if isinstance(content, dict) and content.get("type") == "text":
                        collected_text.append(content.get("text") or "")
                    continue
                if sut:
                    session_updates.append(
                        {
                            "sessionId": params.get("sessionId"),
                            "update": update,
                        },
                    )
                continue
            if msg.get("method") == "session/request_permission":
                perm_id = msg.get("id")
                if perm_id is not None:
                    reply = {"jsonrpc": "2.0", "id": perm_id, "result": {"outcome": "allow_once"}}
                    await _write_message(self._stdin_writer, reply)
                continue
            logger.debug("ACP message: %s", msg.get("method") or msg)
        self._next_id += 1
        return ("".join(collected_text), stop_reason, session_updates)

    async def run_turn_stream(
        self,
        prompt_blocks: Sequence[TextContentBlock | dict[str, Any]],
        mode_id: str | None = None,
        request_timeout: float = 300.0,
    ) -> AsyncGenerator[TurnStreamEvent, None]:
        """
        Same as run_turn but yield:
        - (\"text\", fragment) per agent_message_chunk (text),
        - (\"session\", {\"sessionId\", \"update\"}) for other session/update kinds (tool_call, tool_call_update, etc.),
        - (\"done\", stop_reason) at end.
        """
        async with self._lock:
            async for ev in self._run_turn_stream_unsafe(prompt_blocks, mode_id, request_timeout):
                yield ev

    async def _run_turn_stream_unsafe(
        self,
        prompt_blocks: Sequence[TextContentBlock | dict[str, Any]],
        mode_id: str | None,
        request_timeout: float,
    ) -> AsyncGenerator[TurnStreamEvent, None]:
        if not self._stdin_writer or not self._stdout_reader:
            raise AcpStdioError("ACP process not started")
        await self._do_initialize()
        prompt_payload = _serialize_prompt_blocks(prompt_blocks)
        new_result = await _request(
            self._stdin_writer,
            self._stdout_reader,
            self._next_id,
            "session/new",
            {"cwd": self._workspace_dir, "mcpServers": []},
            timeout=request_timeout,
        )
        self._next_id += 1
        session_id = new_result.get("sessionId")
        if not session_id:
            raise AcpStdioError("session/new did not return sessionId")
        if mode_id:
            await _request(
                self._stdin_writer,
                self._stdout_reader,
                self._next_id,
                "session/set_mode",
                {"sessionId": session_id, "modeId": mode_id},
                timeout=request_timeout,
            )
            self._next_id += 1
        prompt_id = self._next_id
        req = {
            "jsonrpc": "2.0",
            "id": prompt_id,
            "method": "session/prompt",
            "params": {"sessionId": session_id, "prompt": prompt_payload},
        }
        await _write_message(self._stdin_writer, req)
        stop_reason = "end_turn"
        while True:
            msg = await asyncio.wait_for(_read_message(self._stdout_reader), timeout=request_timeout)
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
                if not isinstance(update, dict):
                    continue
                sut = update.get("sessionUpdate")
                if sut == "agent_message_chunk":
                    content = update.get("content") or {}
                    if isinstance(content, dict) and content.get("type") == "text":
                        piece = content.get("text") or ""
                        yield ("text", piece)
                    continue
                if sut:
                    yield (
                        "session",
                        {
                            "sessionId": params.get("sessionId"),
                            "update": update,
                        },
                    )
                continue
            if msg.get("method") == "session/request_permission":
                perm_id = msg.get("id")
                if perm_id is not None:
                    reply = {"jsonrpc": "2.0", "id": perm_id, "result": {"outcome": "allow_once"}}
                    await _write_message(self._stdin_writer, reply)
                continue
            logger.debug("ACP message: %s", msg.get("method") or msg)
        self._next_id += 1
        yield ("done", str(stop_reason))

    async def stop(self) -> None:
        """Terminate the ACP process."""
        if self._proc is None:
            return
        p = self._proc
        stderr_task = self._stderr_drain_task
        self._stderr_drain_task = None
        self._proc = None
        self._stdin_writer = None
        self._stdout_reader = None
        self._initialized = False
        if p.returncode is not None:
            if stderr_task and not stderr_task.done():
                stderr_task.cancel()
                try:
                    await stderr_task
                except asyncio.CancelledError:
                    pass
            return
        logger.info("Stopping ACP agent (PID %s)", p.pid)
        p.terminate()
        try:
            await asyncio.wait_for(p.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            p.kill()
            await p.wait()
        if stderr_task:
            try:
                await asyncio.wait_for(stderr_task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                stderr_task.cancel()
                try:
                    await stderr_task
                except asyncio.CancelledError:
                    pass


# Standalone helpers for tests or one-off use (spawn per call)
async def get_agent_models(
    command: list[str],
    env: dict[str, str],
    workspace: str,
    *,
    timeout: float = 30.0,
    stderr_mode: Literal["inherit", "pipe", "devnull"] = "inherit",
    stderr_pipe_split: bool = False,
) -> list[str]:
    """
    Spawn ACP agent, call initialize and session/new, return list of model ids.
    Used when no per-worker runner exists (e.g. tests with mock runner).
    """
    env_full = dict(os.environ) | env
    work_dir = _resolved_workspace_dir(workspace)
    stderr_arg = _subprocess_stderr_arg(stderr_mode)
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr_arg,
        env=env_full,
        cwd=work_dir,
        limit=_MAX_JSON_LINE,
    )
    assert proc.stdin and proc.stdout
    stderr_task: asyncio.Task[None] | None = None
    if stderr_mode == "pipe" and proc.stderr is not None:
        stderr_task = asyncio.create_task(
            _drain_stderr(proc.stderr, pipe_split=stderr_pipe_split)
        )
    try:
        init_result = await _request(
            proc.stdin,
            proc.stdout,
            0,
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {},
                "clientInfo": {"name": "acpbox", "version": "0.1.0"},
            },
            timeout=timeout,
        )
        agent_name: str | None = None
        if isinstance(init_result.get("agentInfo"), dict):
            agent_name = init_result["agentInfo"].get("name")
        new_result = await _request(
            proc.stdin,
            proc.stdout,
            1,
            "session/new",
            {"cwd": work_dir, "mcpServers": []},
            timeout=timeout,
        )
        modes = new_result.get("modes")
        if isinstance(modes, dict):
            available = modes.get("availableModes")
            if isinstance(available, list) and len(available) > 0:
                ids = [str(m["id"]) for m in available if isinstance(m, dict) and m.get("id")]
                if ids:
                    return ids
        return [agent_name or "default"]
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        if stderr_task:
            try:
                await asyncio.wait_for(stderr_task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                stderr_task.cancel()
                try:
                    await stderr_task
                except asyncio.CancelledError:
                    pass


async def run_single_turn(
    command: list[str],
    env: dict[str, str],
    workspace: str,
    prompt_blocks: list[dict[str, Any]],
    *,
    mode_id: str | None = None,
    request_timeout: float = 300.0,
) -> tuple[str, str, list[dict[str, Any]]]:
    """
    Spawn ACP agent, run one turn, then terminate. Used when no per-worker runner exists.
    """
    runner = AcpRunner(command, env, workspace)
    await runner.start()
    try:
        return await runner.run_turn(prompt_blocks, mode_id, request_timeout)
    finally:
        await runner.stop()
