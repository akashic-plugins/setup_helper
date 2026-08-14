from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, cast

from pydantic import BaseModel

from agent.lifecycle.types import BeforeTurnCtx, TurnState
from agent.plugin_composition import (
    COMMANDS,
    CommandDefinition,
    CommandInvocation,
    CommandResult,
    Context,
)
from agent.plugins import Plugin


class SetupHelperConfig(BaseModel):
    qqbot_data_dir: str = ""


api_version = 3
name = "setup_helper"
version = "1.0.0"
desc = "快速查询当前会话 chat_id，用于配置 proactive"
Config = SetupHelperConfig
inject = (COMMANDS,)


class _BeforeTurnFrame(Protocol):
    input: TurnState
    slots: dict[str, object]


async def apply(ctx: Context, config: SetupHelperConfig) -> None:
    """Register the chat identity command against the Core command seam."""

    # 1. Resolve plugin-owned presentation configuration once per generation.
    qqbot_config_path = _qqbot_config_path(config)

    # 2. Core owns admission; this plugin owns the command behavior and text.
    async def handle(invocation: CommandInvocation) -> CommandResult:
        chat_id = invocation.chat_id or "（未知）"
        return CommandResult(
            "success",
            _format_reply(
                chat_id,
                channel=invocation.channel,
                qqbot_config_path=qqbot_config_path,
            ),
        )

    await ctx.require(COMMANDS).register(
        ctx,
        CommandDefinition(
            name="chatid",
            description="查看我的 chat_id（配置 proactive 用）",
            aliases=("myid",),
            handler=handle,
        ),
    )


class ChatIdCommandModule:
    slot = "setup_helper.chatid"
    requires = ("before_turn.acquire_session", "session:session")
    produces = ("session:ctx",)

    def __init__(self, qqbot_config_path: Path | None) -> None:
        self._qqbot_config_path = qqbot_config_path

    async def run(self, frame: object) -> object:
        typed_frame = cast(_BeforeTurnFrame, frame)
        if "session:ctx" in typed_frame.slots:
            return frame
        state = typed_frame.input
        if _normalize_command(state.msg.content) not in {"/chatid", "/myid"}:
            return frame
        chat_id = state.msg.chat_id or "（未知）"
        reply = _format_reply(
            chat_id,
            channel=state.msg.channel,
            qqbot_config_path=self._qqbot_config_path,
        )
        typed_frame.slots["session:ctx"] = _abort_ctx(state, reply)
        return frame


class SetupHelper(Plugin):
    api_version = 2
    name = "setup_helper"
    version = "1.0.0"
    desc = "快速查询当前会话 chat_id，用于配置 proactive"
    ConfigModel = SetupHelperConfig

    def telegram_bot_commands(self) -> list[tuple[str, str]]:
        return [("chatid", "查看我的 chat_id（配置 proactive 用）")]

    def before_turn_modules(self) -> list[object]:
        config = cast(SetupHelperConfig, self.context.config)
        qqbot_config_path = _qqbot_config_path(config)
        return cast("list[object]", [ChatIdCommandModule(qqbot_config_path)])


def _normalize_command(content: str) -> str:
    parts = (content or "").strip().split(maxsplit=1)
    if not parts:
        return ""
    head = parts[0].lower()
    if "@" in head:
        head = head.split("@", 1)[0]
    return head


def _format_reply(
    chat_id: str,
    channel: str = "telegram",
    qqbot_config_path: Path | None = None,
) -> str:
    lines = [
        f"你的 chat_id 是：`{chat_id}`",
        "",
        "将它填入 config.toml 即可开启主动推送：",
        "",
        "```toml",
        "[proactive]",
        "enabled = true",
        "",
        "[proactive.target]",
        f'channel = "{channel}"',
        f'chat_id = "{chat_id}"',
        "```",
    ]
    if channel == "qqbot":
        # user_openid 也需要加入 allow_from 白名单
        raw_openid = chat_id.removeprefix("c2c:")
        lines += [
            "",
            "同时确认 allow_from 已包含你的 user_openid：",
            "",
            "```toml",
            (
                str(qqbot_config_path)
                if qqbot_config_path is not None
                else "请先设置 QQBOT_DATA_DIR，或在 setup_helper 插件配置中填写 qqbot_data_dir"
            ),
            f'allow_from = ["{raw_openid}"]',
            "```",
        ]
    return "\n".join(lines)


def _qqbot_config_path(config: SetupHelperConfig) -> Path | None:
    raw = os.environ.get("QQBOT_DATA_DIR", "").strip()
    if not raw:
        raw = config.qqbot_data_dir.strip()
    if not raw:
        return None
    return Path(raw).expanduser() / "config.local.toml"


def _abort_ctx(state: TurnState, reply: str) -> BeforeTurnCtx:
    return BeforeTurnCtx(
        session_key=state.session_key,
        channel=state.msg.channel,
        chat_id=state.msg.chat_id,
        content=state.msg.content,
        timestamp=state.msg.timestamp,
        skill_names=[],
        retrieved_memory_block="",
        retrieval_trace_raw=None,
        history_messages=(),
        abort=True,
        abort_reply=reply,
    )
