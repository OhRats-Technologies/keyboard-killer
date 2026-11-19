"""Contract tests for the ACP adapter using a fake stdio agent."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from keyboard_killer.models.providers.acp import AcpClient, AcpProvider
from keyboard_killer.models.providers.acp_registry import AcpAgentSpec
from keyboard_killer.models.providers.base import ProviderState

FAKE_AGENT = Path(__file__).parent.parent / "fixtures" / "fake_acp_agent.py"


def _spec(log: Path, *extra: str) -> AcpAgentSpec:
    return AcpAgentSpec(
        id="fake",
        name="Fake Agent",
        description="test",
        command=(sys.executable, str(FAKE_AGENT), str(log), *extra),
        source="test",
        installed=True,
    )


def _events(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def test_acp_roundtrip_rejects_permissions_and_filesystem(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    empty = tmp_path / "empty-workdir"
    provider = AcpProvider(_spec(log), cwd=empty)
    try:
        status = provider.status()
        assert status.state is ProviderState.READY
        assert provider.capabilities.image_input is True
        reply = provider.complete("You are terse.", "Click send")
        assert reply == "Click send."
    finally:
        provider.close()

    events = _events(log)
    by_name = {event["event"]: event for event in events}
    assert by_name["initialize"]["client_capabilities"] == {}
    assert by_name["session_new"]["contents"] == []
    assert by_name["session_new"]["cwd"] == str(empty)
    permission = by_name["permission_response"]["payload"]
    assert permission["result"]["outcome"]["optionId"] == "reject-once"
    fs_response = by_name["fs_response"]["payload"]
    assert fs_response["error"]["code"] == -32601
    prompt = by_name["prompt"]["content"]
    assert prompt[0]["type"] == "text"
    assert "Click send" in prompt[0]["text"]


def test_acp_auth_required_is_reported(tmp_path: Path) -> None:
    log = tmp_path / "events.jsonl"
    provider = AcpProvider(_spec(log, "--auth"), cwd=tmp_path / "empty")
    try:
        status = provider.status()
        assert status.state is ProviderState.NEEDS_AUTH
        assert "authentication" in status.detail.lower()
    finally:
        provider.close()


def test_acp_missing_command_is_not_installed(tmp_path: Path) -> None:
    spec = AcpAgentSpec(
        id="missing",
        name="Missing",
        command=("definitely-not-an-acp-agent-xyz",),
        installed=False,
    )
    status = AcpProvider(spec).status()
    assert status.state is ProviderState.NOT_INSTALLED


def test_acp_client_refuses_unknown_agent_requests(tmp_path: Path) -> None:
    client = AcpClient([sys.executable, str(FAKE_AGENT)], cwd=tmp_path / "empty")
    try:
        client.initialize()
        client.new_session()
        assert client.prompt("hello") == "Click send."
    finally:
        client.close()
