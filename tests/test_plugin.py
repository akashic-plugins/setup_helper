from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from plugin import ChatIdCommandModule, _format_reply


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
    await ChatIdCommandModule().run(frame)
    ctx = frame.slots["session:ctx"]
    assert ctx.abort is True
    assert "123" in ctx.abort_reply


def test_qqbot_reply_contains_allow_from_hint() -> None:
    reply = _format_reply("c2c:abc", channel="qqbot")
    assert 'allow_from = ["abc"]' in reply
