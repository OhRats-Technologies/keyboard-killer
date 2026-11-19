"""Minimal fake ACP agent used by contract tests.

Usage: fake_acp_agent.py <event-log.jsonl> [--auth]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

LOG = Path(sys.argv[1]) if len(sys.argv) > 1 else None
AUTH = "--auth" in sys.argv


def log(event: str, **fields: object) -> None:
    if LOG is None:
        return
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": event, **fields}) + "\n")


def send(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def read_message() -> dict:
    line = sys.stdin.readline()
    if not line:
        raise SystemExit(0)
    return json.loads(line)


def main() -> int:
    while True:
        message = read_message()
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            log(
                "initialize",
                client_capabilities=message.get("params", {}).get("clientCapabilities"),
                client_info=message.get("params", {}).get("clientInfo"),
            )
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": 1,
                        "agentCapabilities": {"promptCapabilities": {"image": True}},
                        "agentInfo": {"name": "fake-acp", "version": "0.1"},
                        "authMethods": [] if not AUTH else [{"id": "fake-login", "type": "agent"}],
                    },
                }
            )
        elif method == "session/new":
            cwd = str(message.get("params", {}).get("cwd", ""))
            contents = sorted(os.listdir(cwd)) if cwd and Path(cwd).is_dir() else ["<missing>"]
            log("session_new", cwd=cwd, contents=contents)
            if AUTH:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32000, "message": "authentication required"},
                    }
                )
                continue
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"sessionId": "fake-session"},
                }
            )
        elif method == "session/prompt":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 900,
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": "fake-session",
                        "toolCall": {"toolCallId": "t1", "title": "Run a command"},
                        "options": [
                            {"optionId": "allow-once", "name": "Allow", "kind": "allow_once"},
                            {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"},
                        ],
                    },
                }
            )
            response = read_message()
            log("permission_response", payload=response)
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 901,
                    "method": "fs/read_text_file",
                    "params": {"sessionId": "fake-session", "path": "/etc/passwd"},
                }
            )
            response = read_message()
            log("fs_response", payload=response)
            prompt = message.get("params", {}).get("prompt", [])
            log("prompt", content=prompt)
            for chunk in ("Click ", "send."):
                send(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": "fake-session",
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": chunk},
                            },
                        },
                    }
                )
            send({"jsonrpc": "2.0", "id": request_id, "result": {"stopReason": "end_turn"}})
        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"unknown method {method}"},
                }
            )


if __name__ == "__main__":
    raise SystemExit(main())
