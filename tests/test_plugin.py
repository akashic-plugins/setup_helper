from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

import plugin as plugin_module
from agent.context import ContextBuilder
from agent.core.passive_turn import (
    ContextStore,
    PassiveTurnDeps,
    PassiveTurnPipeline,
    Reasoner,
)
from agent.looping.ports import SessionServices
from agent.plugin_composition import (
    COMMANDS,
    CommandDescriptor,
    CompositionRoot,
    Context,
    PluginCommands,
    PluginRuntime,
)
from agent.plugins.composable import ComposablePlugin
from agent.plugins.manager import PluginManager
from agent.plugins.snapshot import bind_runtime_snapshot, reset_runtime_snapshot
from agent.tools.registry import ToolRegistry
from agent.turns.outbound import OutboundPort
from bus.event_bus import EventBus
from bus.events import InboundMessage, TurnDisposition
from plugin import (
    ChatIdCommandModule,
    SetupHelperConfig,
    apply,
    inject,
    _format_reply,
    _qqbot_config_path,
)

PLUGIN_ROOT = Path(__file__).parents[1]


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "channel", "chat_id"),
    [
        ("/chatid", "telegram", "123"),
        ("/myid", "mobile", "device-1"),
        ("  /CHATID@AkashicBot ignored  ", "qqbot", "c2c:abc"),
        ("/chatid", "telegram", ""),
        ("/unknown", "telegram", "123"),
    ],
)
async def test_v3_command_matches_v2_short_circuit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: str,
    channel: str,
    chat_id: str,
) -> None:
    monkeypatch.delenv("QQBOT_DATA_DIR", raising=False)
    config = SetupHelperConfig(qqbot_data_dir=str(tmp_path / "qqbot"))
    config_path = _qqbot_config_path(config)

    legacy_state = SimpleNamespace(
        session_key=f"{channel}:{chat_id}",
        msg=SimpleNamespace(
            content=content,
            channel=channel,
            chat_id=chat_id,
            timestamp=datetime.now(),
        ),
    )
    legacy_frame = SimpleNamespace(
        input=legacy_state,
        slots={"session:session": object()},
    )
    await ChatIdCommandModule(config_path).run(legacy_frame)
    legacy_reply = (
        legacy_frame.slots["session:ctx"].abort_reply
        if "session:ctx" in legacy_frame.slots
        else None
    )

    _ = ComposablePlugin.from_module(plugin_module)
    root = CompositionRoot("setup-helper-parity")
    commands = PluginCommands()
    _ = await root.context.provide(COMMANDS, commands)

    async def mount(ctx: Context) -> None:
        await apply(ctx, config)

    _ = await root.mount(
        mount,
        name="setup_helper",
        inject=inject,
        runtime=PluginRuntime(
            plugin_id="setup_helper",
            plugin_dir=PLUGIN_ROOT,
            data_dir=tmp_path / "plugin-data",
            workspace=tmp_path / "workspace",
            config=config,
        ),
    )
    registry = commands.freeze()
    execution = await registry.execute(
        content,
        session_key=f"{channel}:{chat_id}",
        channel=channel,
        chat_id=chat_id,
        sender="hua",
    )
    candidate_reply = execution.result.text if execution is not None else None

    assert candidate_reply == legacy_reply
    assert registry.descriptors == (
        CommandDescriptor(
            name="chatid",
            description="查看我的 chat_id（配置 proactive 用）",
        ),
    )
    assert root.receipt().writes == ()
    assert root.receipt().external_effects == ()

    await root.dispose()

    assert root.receipt().services == ()
    assert root.receipt().effects == ()


@pytest.mark.asyncio
async def test_v3_command_loads_and_bypasses_session_and_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("QQBOT_DATA_DIR", raising=False)
    plugin_home = tmp_path / "plugins"
    _ = shutil.copytree(
        PLUGIN_ROOT,
        plugin_home / "setup_helper",
        ignore=shutil.ignore_patterns(
            ".akashic-core",
            ".git",
            ".pytest_cache",
            "__pycache__",
        ),
    )
    manager = PluginManager(
        plugin_dirs=[plugin_home],
        event_bus=EventBus(),
        tool_registry=ToolRegistry(),
        workspace=tmp_path / "workspace",
        installed_cache_root=tmp_path / "plugin-home" / "cache",
    )
    await manager.load_all()

    generation = manager.generation("setup_helper")
    snapshot = manager.current_snapshot
    assert generation is not None and snapshot is not None
    assert isinstance(generation.instance, ComposablePlugin)
    assert snapshot.command_registry is not None
    assert manager.telegram_bot_commands == [
        ("chatid", "查看我的 chat_id（配置 proactive 用）")
    ]
    assert manager.mobile_bot_commands == []

    session_manager = SimpleNamespace(
        get_or_create=MagicMock(),
        peek_next_message_id=MagicMock(),
        append_messages=AsyncMock(),
    )
    context_store = SimpleNamespace(prepare=AsyncMock())
    reasoner = SimpleNamespace(run_turn=AsyncMock())
    outbound_port = SimpleNamespace(dispatch=AsyncMock())
    pipeline = PassiveTurnPipeline(
        PassiveTurnDeps(
            session=cast(
                SessionServices,
                cast(
                    object,
                    SimpleNamespace(session_manager=session_manager, presence=None),
                ),
            ),
            context_store=cast(ContextStore, cast(object, context_store)),
            context=cast(ContextBuilder, cast(object, SimpleNamespace())),
            tools=cast(ToolRegistry, cast(object, SimpleNamespace())),
            reasoner=cast(Reasoner, cast(object, reasoner)),
            outbound_port=cast(OutboundPort, cast(object, outbound_port)),
        )
    )
    lease = manager.snapshot_store.lease()
    token = bind_runtime_snapshot(lease)
    try:
        outbound = await pipeline.run(
            InboundMessage(
                channel="telegram",
                sender="hua",
                chat_id="123",
                content="/myid@AkashicBot",
            ),
            "telegram:123",
        )
    finally:
        reset_runtime_snapshot(token)
        await lease.release()

    assert outbound.content == _format_reply("123", channel="telegram")
    assert outbound.turn_disposition is TurnDisposition.SHORT_CIRCUITED
    session_manager.get_or_create.assert_not_called()
    session_manager.peek_next_message_id.assert_not_called()
    session_manager.append_messages.assert_not_awaited()
    context_store.prepare.assert_not_awaited()
    reasoner.run_turn.assert_not_awaited()
    outbound_port.dispatch.assert_awaited_once()

    root = snapshot.composition_root
    assert root is not None
    await manager.terminate_all()
    assert root.receipt().services == ()
    assert root.receipt().effects == ()
