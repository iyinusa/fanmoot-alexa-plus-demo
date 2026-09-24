"""W1-10: the evidence capture writes only what is true."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from app.agent import models
from app.settings import Settings
from scripts.capture_evidence import capture
from tests.fake_mcp import RunningServer
from tests.scripted import ScriptedLlm, say


async def test_refuses_a_chain_that_is_not_bedrock(tmp_path: Path) -> None:
    assert await capture(["gemini/gemini-3.1-flash-lite"], tmp_path) == 1
    assert list(tmp_path.iterdir()) == []


async def test_refuses_without_aws_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models, "_aws_chain_has_credentials", lambda: False)

    assert await capture(["bedrock/us.amazon.nova-pro-v1:0"], tmp_path) == 1
    assert list(tmp_path.iterdir()) == []


async def test_writes_the_transcript_and_the_summary(
    tmp_path: Path, configure: Callable[..., Settings], mcp_server: RunningServer
) -> None:
    configure(mcp_url=mcp_server.url, aws_access_key_id="AKIA", aws_secret_access_key="s")
    llm = ScriptedLlm.of(*[say(f"Answer {n}.") for n in range(1, 6)], model="nova")

    code = await capture(
        ["bedrock/us.amazon.nova-pro-v1:0"], tmp_path, llm_factory=lambda _r, _s: llm
    )

    assert code == 0
    summary = next(tmp_path.glob("bedrock-run-*.md")).read_text()
    assert "`bedrock/us.amazon.nova-pro-v1:0`" in summary
    assert "`2025-11-25`" in summary
    assert "Who are the gunners?" in summary
    transcript = next(tmp_path.glob("bedrock-run-*.jsonl")).read_text()
    assert transcript.count('"type": "turn.end"') == 5
