from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin import (
    ChatIdCommandModule,
    SetupHelperConfig,
    _format_reply,
    _qqbot_config_path,
)


@pytest.mark.asyncio
async def test_chatid_command_aborts_turn() -> None:
    state = SimpleNamespace(
        session_key="telegram:1",
        msg=SimpleNamespace(
            content="/chatid",
            channel="telegram",
            chat_id="123",
            timestamp=datetime.now(),
        ),
    )
    frame = SimpleNamespace(input=state, slots={"session:session": object()})
    module = ChatIdCommandModule(Path("/plugin-data/qqbot-custom/config.local.toml"))
    await module.run(frame)
    ctx = frame.slots["session:ctx"]
    assert ctx.abort is True
    assert "123" in ctx.abort_reply


def test_qqbot_reply_contains_allow_from_hint() -> None:
    config_path = Path("/plugin-data/qqbot-custom/config.local.toml")
    reply = _format_reply("c2c:abc", channel="qqbot", qqbot_config_path=config_path)
    assert 'allow_from = ["abc"]' in reply
    assert str(config_path) in reply


def test_qqbot_data_dir_comes_from_explicit_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("QQBOT_DATA_DIR", raising=False)
    config = SetupHelperConfig(qqbot_data_dir=str(tmp_path))
    assert _qqbot_config_path(config) == tmp_path / "config.local.toml"


def test_qqbot_data_dir_environment_has_priority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured = tmp_path / "configured"
    overridden = tmp_path / "overridden"
    monkeypatch.setenv("QQBOT_DATA_DIR", f"  {overridden}  ")
    config = SetupHelperConfig(qqbot_data_dir=str(configured))
    assert _qqbot_config_path(config) == overridden / "config.local.toml"


def test_missing_qqbot_data_dir_does_not_guess_marketplace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QQBOT_DATA_DIR", "   ")
    config = SetupHelperConfig(qqbot_data_dir="   ")
    assert _qqbot_config_path(config) is None
    reply = _format_reply("c2c:abc", channel="qqbot", qqbot_config_path=None)
    assert "QQBOT_DATA_DIR" in reply
    assert "qqbot-github" not in reply
