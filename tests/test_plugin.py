from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

import pytest
from agent.plugin_composition import (
    COMMANDS,
    CommandResult,
    CompositionRoot,
    Context,
    PluginRuntime,
)
from agent.plugins.manager import PluginManager
from agent.plugins.composable import ComposablePlugin
from plugins.commands import plugin as commands_module
from plugins.commands.plugin import apply as apply_commands
from tests.fixtures.plugin_workspace import initialize_plugin_workspace
from bus.event_bus import EventBus

import plugin as plugin_module
from plugin import (
    Config,
    _format_reply,
    _qqbot_config_path,
)


@pytest.mark.asyncio
async def test_v3_apply_registers_chatid_command(tmp_path: Path) -> None:
    root = CompositionRoot("setup-helper-v3")
    await root.mount(apply_commands, name="commands")
    commands = root.context.require(COMMANDS)

    async def mount_plugin(ctx: Context) -> None:
        await plugin_module.apply(ctx)

    _ = await root.mount(
        mount_plugin,
        name="setup_helper",
        inject=plugin_module.inject,
        runtime=PluginRuntime(
            plugin_id="setup_helper",
            generation_id="test-generation",
            plugin_dir=Path(plugin_module.__file__).resolve().parent,
            data_dir=tmp_path / "plugin-data",
            workspace=tmp_path / "workspace",
            config={},
        ),
    )
    registry = commands.freeze()
    assert registry.descriptors[0].name == "chatid"
    assert registry.descriptors[0].aliases == ("myid",)
    execution = await registry.execute(
        "/myid@my_bot",
        session_key="telegram:123",
        channel="telegram",
        chat_id="123",
        sender="hua",
    )
    assert execution is not None
    assert execution.result == CommandResult(
        "success",
        _format_reply("123", session_id="telegram:123"),
    )
    await root.dispose()
    assert root.receipt().effects == ()


@pytest.mark.asyncio
async def test_manager_updates_exact_command_catalog_in_current_root(
    tmp_path: Path,
) -> None:
    sources = tmp_path / "plugins"
    current = sources / "setup_helper"
    core = Path(commands_module.__file__).parents[1]
    shutil.copytree(core / "commands", sources / "commands")
    shutil.copytree(Path(plugin_module.__file__).parent, current,
                    ignore=shutil.ignore_patterns(".git", ".akashic-core", ".plugin-contracts", "__pycache__"))
    workspace = tmp_path / "workspace"
    initialize_plugin_workspace(workspace)
    manager = PluginManager(plugin_dirs=[sources], event_bus=EventBus(), workspace=workspace)
    try:
        await manager.load_all()
        root = manager.live_root
        assert root is not None
        registry = root.context.require(COMMANDS).freeze()
        first = await registry.execute("/chatid", session_key="telegram:stable",
                                       channel="telegram", chat_id="stable", sender="hua")
        assert first is not None and "stable" in first.result.text
        source = current / "plugin.py"
        source.write_text(source.read_text().replace('version = "3.0.0"', 'version = "3.0.1"'))
        results = await manager.reconcile_changed()
        assert len(results) == 1
        assert manager.live_root is root
        generation = manager.generation("setup_helper")
        assert generation is not None and isinstance(generation.instance, ComposablePlugin)
        assert generation.instance.version == "3.0.1"
        registry = root.context.require(COMMANDS).freeze()
        assert len(registry.descriptors) == 1
        second = await registry.execute("/myid", session_key="qqbot:new",
                                        channel="qqbot", chat_id="c2c:new", sender="hua")
        assert second is not None
        assert 'allow_from = ["new"]' in second.result.text
        from plugins.wake.api import Config as WakeConfig
        snippet = second.result.text.split("```toml\n", 1)[1].split("```", 1)[0]
        delivery = WakeConfig.model_validate(tomllib.loads(snippet)).delivery
        assert delivery is not None
        assert (delivery.channel, delivery.recipient, delivery.session_id) == ("qqbot", "c2c:new", "qqbot:new")
    finally:
        await manager.terminate_all()
    assert root.topology_view().listeners == ()
    assert root.receipt().effects == ()


def test_qqbot_reply_contains_allow_from_hint() -> None:
    config_path = Path("/plugin-data/qqbot-custom/config.local.toml")
    reply = _format_reply(
        "c2c:abc",
        channel="qqbot",
        qqbot_config_path=config_path,
        session_id="qqbot:abc",
    )
    assert 'allow_from = ["abc"]' in reply
    assert str(config_path) in reply


def test_qqbot_data_dir_comes_from_explicit_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("QQBOT_DATA_DIR", raising=False)
    config = Config(qqbot_data_dir=str(tmp_path))
    assert _qqbot_config_path(config) == tmp_path / "config.local.toml"


def test_qqbot_data_dir_environment_has_priority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured = tmp_path / "configured"
    overridden = tmp_path / "overridden"
    monkeypatch.setenv("QQBOT_DATA_DIR", f"  {overridden}  ")
    config = Config(qqbot_data_dir=str(configured))
    assert _qqbot_config_path(config) == overridden / "config.local.toml"


def test_missing_qqbot_data_dir_does_not_guess_marketplace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QQBOT_DATA_DIR", "   ")
    config = Config(qqbot_data_dir="   ")
    assert _qqbot_config_path(config) is None
    reply = _format_reply(
        "c2c:abc", channel="qqbot", qqbot_config_path=None, session_id="qqbot:abc"
    )
    assert "QQBOT_DATA_DIR" in reply
    assert "qqbot-github" not in reply
