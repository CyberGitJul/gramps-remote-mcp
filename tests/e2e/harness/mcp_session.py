"""Raw JSON-RPC stdio driver for the shipped MCP container (plan D21).

Deliberately raw rather than the `mcp` client library: the suite must observe what the
wire actually carries — `structuredContent` presence, `nextCursor`, and the fact that a
gated-off tool answers with a *successful* result carrying `isError: true` (D20). A
client library normalises exactly those details away.

It speaks the protocol over a process it is *handed* and never starts one. Choosing the
image, wiring mounts and env, and getting the container removed again is `mcp_container.py`;
keeping the two apart is what lets the framing be tested against a plain pipe (see
`test_00_mcp_session.py`) instead of only against a live container.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

from .docker_util import ProbeError

# How often the read loop looks up from the queue to ask whether the server is still alive,
# and how long it then waits for the pipe to drain before calling the answer lost.
DEATH_CHECK_S = 0.25
DRAIN_S = 2.0


@dataclass
class ToolResult:
    """One `tools/call` reply, in the shape the assertions will use."""

    is_error: bool
    content: list[dict[str, Any]]
    structured: Any | None
    raw: dict[str, Any]

    @property
    def text(self) -> str:
        return "\n".join(
            part.get("text", "") for part in self.content if part.get("type") == "text"
        )


class McpSession:
    """One stdio session over an already-running server process."""

    PROTOCOL_VERSION = "2025-06-18"

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        self._proc = proc
        self._next_id = 0
        self._lines: queue.Queue[str] = queue.Queue()
        self.stderr: list[str] = []
        self._stdout_pump = self._pump(self._proc.stdout, self._lines.put)
        self._pump(self._proc.stderr, self.stderr.append)

    def _pump(self, stream: Any, sink: Any) -> threading.Thread:
        thread = threading.Thread(target=lambda: [sink(line) for line in stream], daemon=True)
        thread.start()
        return thread

    def _request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 60.0
    ) -> dict[str, Any]:
        self._next_id += 1
        msg_id = self._next_id
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._write(payload)

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError(
                    f"{method} timed out after {timeout}s; stderr={''.join(self.stderr)[-400:]}"
                )
            try:
                line = self._lines.get(timeout=min(remaining, DEATH_CHECK_S))
            except queue.Empty:
                self._raise_if_dead(method)
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == msg_id:
                return msg

    def _raise_if_dead(self, method: str) -> None:
        """A server that exited is not a server that is slow, and must not read as a timeout.

        This is the shape of the failure that shipped twice: a module missing from the image's
        `COPY` list makes the process die on import, and every call against it then waited out
        the full timeout and reported "timed out" — which points at the network, at the
        instance, at anything but the image. Waiting 60 s to say the wrong thing is worse than
        waiting 60 s.

        The exit is not enough on its own: a server may answer and *then* exit, and that answer
        is legitimate. So the stdout pump is joined first — once the process is gone and the
        pipe has drained, whatever is in the queue is everything that will ever arrive — and a
        non-empty queue hands the decision back to the read loop.
        """
        code = self._proc.poll()
        if code is None:
            return
        self._stdout_pump.join(timeout=DRAIN_S)
        if not self._lines.empty():
            return
        raise ProbeError(
            f"the server process exited with code {code} before answering {method}; "
            f"stderr={''.join(self.stderr)[-400:]}"
        )

    def _write(self, payload: dict[str, Any]) -> None:
        if self._proc.stdin is None:  # pragma: no cover - Popen always gives us a pipe here
            raise ProbeError("stdin is closed")
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()

    def initialize(self) -> dict[str, Any]:
        reply = self._request(
            "initialize",
            {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "gramps-e2e-probe", "version": "0"},
            },
        )
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return reply

    def list_tools(self, cursor: str | None = None) -> dict[str, Any]:
        return self._request("tools/list", {"cursor": cursor} if cursor else {})["result"]

    def call(
        self, tool: str, arguments: dict[str, Any] | None = None, *, timeout: float = 60.0
    ) -> ToolResult:
        reply = self._request(
            "tools/call", {"name": tool, "arguments": arguments or {}}, timeout=timeout
        )
        if "error" in reply:
            raise ProbeError(f"{tool} answered with a JSON-RPC error: {reply['error']}")
        result = reply["result"]
        return ToolResult(
            is_error=bool(result.get("isError")),
            content=result.get("content", []),
            structured=result.get("structuredContent"),
            raw=result,
        )

    def close(self) -> None:
        try:
            if self._proc.stdin is not None:
                self._proc.stdin.close()
            self._proc.wait(timeout=20)
        except (subprocess.TimeoutExpired, OSError):  # pragma: no cover - container already gone
            self._proc.kill()
