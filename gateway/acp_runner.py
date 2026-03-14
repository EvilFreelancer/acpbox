"""Start ACP server as subprocess, wait for /ping, shutdown on exit."""

import asyncio
import logging
import os
import signal
import time
from typing import NoReturn

import httpx

logger = logging.getLogger(__name__)


class AcpRunnerError(Exception):
    """Raised when ACP process fails to start or respond to ping."""


class AcpRunner:
    """Manages ACP server subprocess: start, wait for ready (/ping), terminate on shutdown."""

    def __init__(
        self,
        command: list[str],
        env: dict[str, str],
        base_url: str,
        startup_timeout_seconds: int,
    ) -> None:
        self.command = command
        self.env = dict(os.environ) | env
        self.base_url = base_url.rstrip("/")
        self.startup_timeout_seconds = startup_timeout_seconds
        self._process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        """Start the ACP process and wait until GET {base_url}/ping returns 200."""
        if self._process is not None:
            return
        logger.info("Starting ACP server: %s", self.command)
        self._process = await asyncio.create_subprocess_exec(
            *self.command,
            env=self.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await self._wait_for_ping()

    async def _wait_for_ping(self) -> None:
        """Poll GET {base_url}/ping until 200 or timeout."""
        ping_url = f"{self.base_url}/ping"
        deadline = time.monotonic() + self.startup_timeout_seconds
        async with httpx.AsyncClient(timeout=2.0) as client:
            while time.monotonic() < deadline:
                if self._process and self._process.returncode is not None:
                    raise AcpRunnerError(
                        f"ACP process exited with code {self._process.returncode} before /ping succeeded"
                    )
                try:
                    r = await client.get(ping_url)
                    if r.status_code == 200:
                        logger.info("ACP server is ready at %s", self.base_url)
                        return
                except Exception as e:
                    logger.debug("Ping failed: %s", e)
                await asyncio.sleep(0.5)
        raise AcpRunnerError(
            f"ACP server did not respond to GET {ping_url} within {self.startup_timeout_seconds}s"
        )

    async def stop(self) -> None:
        """Terminate the ACP process (SIGTERM, then SIGKILL after short wait)."""
        if self._process is None:
            return
        p = self._process
        self._process = None
        if p.returncode is not None:
            return
        logger.info("Stopping ACP server (PID %s)", p.pid)
        p.terminate()
        try:
            await asyncio.wait_for(p.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("ACP process did not exit after SIGTERM, sending SIGKILL")
            p.kill()
            await p.wait()
        if p.stdout:
            p.stdout.close()

    def _log_stdout(self) -> None:
        """Read and log remaining stdout from the process (non-blocking)."""
        if self._process is None or not self._process.stdout:
            return
        try:
            while True:
                line = self._process.stdout.readline()
                if not line:
                    break
                logger.debug("ACP stdout: %s", line.decode(errors="replace").strip())
        except Exception:
            pass


def run_stdout_logger(process: asyncio.subprocess.Process) -> asyncio.Task[None]:
    """Start a background task that logs ACP stdout line by line."""

    async def _log() -> None:
        if not process.stdout:
            return
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                logger.debug("ACP: %s", line.decode(errors="replace").strip())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("ACP stdout reader: %s", e)

    return asyncio.create_task(_log())
