from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

from agent.plugin_composition import (
    COMMANDS,
    CommandDefinition,
    CommandInvocation,
    CommandResult,
    Context,
)


class SetupHelperConfig(BaseModel):
    qqbot_data_dir: str = ""


api_version = 3
name = "setup_helper"
version = "1.0.0"
desc = "快速查询当前会话 chat_id，用于配置 proactive"
Config = SetupHelperConfig
inject = (COMMANDS,)


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
