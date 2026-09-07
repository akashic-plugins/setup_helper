from __future__ import annotations

import json
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

api_version = 3
name = "setup_helper"
version = "3.0.0"
desc = "快速查询当前会话 chat_id，用于配置 Wake 送达"
inject = (COMMANDS,)


class Config(BaseModel):
    qqbot_data_dir: str = ""


async def apply(ctx: Context, config: Config) -> None:
    """登记查询当前渠道身份的 slash command。"""

    # 1. 配置只决定 QQBot 提示中的目标路径，不持有渠道或 Session。
    qqbot_config_path = _qqbot_config_path(config)

    async def handle_chat_id(invocation: CommandInvocation) -> CommandResult:
        chat_id = invocation.chat_id or "（未知）"
        return CommandResult(
            "success",
            _format_reply(
                chat_id,
                channel=invocation.channel,
                session_id=invocation.session_key,
                qqbot_config_path=qqbot_config_path,
            ),
        )

    # 2. Command Registry 统一拥有描述、别名、执行和 generation cleanup。
    await ctx.require(COMMANDS).register(
        ctx,
        CommandDefinition(
            name="chatid",
            description="查看我的 chat_id（配置 Wake 送达用）",
            aliases=("myid",),
            handler=handle_chat_id,
        ),
    )


def _format_reply(
    chat_id: str,
    channel: str = "telegram",
    qqbot_config_path: Path | None = None,
    *,
    session_id: str,
) -> str:
    lines = [
        f"你的 chat_id 是：`{chat_id}`",
        "",
        "在 <workspace>/plugin-data/wake-builtin/config.local.toml 中设置主动消息的送达目标：",
        "",
        "```toml",
        "[delivery]",
        f"channel = {json.dumps(channel, ensure_ascii=False)}",
        f"recipient = {json.dumps(chat_id, ensure_ascii=False)}",
        f"session_id = {json.dumps(session_id, ensure_ascii=False)}",
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
            f"allow_from = [{json.dumps(raw_openid, ensure_ascii=False)}]",
            "```",
        ]
    return "\n".join(lines)


def _qqbot_config_path(config: Config) -> Path | None:
    raw = os.environ.get("QQBOT_DATA_DIR", "").strip()
    if not raw:
        raw = config.qqbot_data_dir.strip()
    if not raw:
        return None
    return Path(raw).expanduser() / "config.local.toml"
