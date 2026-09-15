from __future__ import annotations

import os
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
from plugins.commands.registry import PluginCommands
from agent.plugins.manager import PluginManager
from agent.plugins.selection import PluginSelection
from agent.plugins.artifacts import ArtifactPointer, write_pointers
from agent.plugins.manifest import write_plugin_manifest
from bus.event_bus import EventBus

AGENT_ROOT = Path(os.environ["AKASHIC_AGENT_ROOT"])

import plugin as plugin_module
from plugin import (
    Config,
    _format_reply,
    _qqbot_config_path,
)


@pytest.mark.asyncio
async def test_v3_apply_registers_chatid_command(tmp_path: Path) -> None:
    root = CompositionRoot("setup-helper-v3")
    commands = PluginCommands(root.context)
    _ = await root.context.provide(COMMANDS, commands)

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
            config=dict(Config().model_dump()),
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
async def test_manager_rebuilds_exact_command_catalog_on_candidate_publish(
    tmp_path: Path,
) -> None:
    plugin_base = tmp_path / "home" / "cache" / "lab" / "setup_helper"
    stable_root = plugin_base / ".artifacts" / "3.0.0-stable"
    latest_root = plugin_base / ".artifacts" / "3.0.1-latest"
    stable_root.mkdir(parents=True)
    latest_root.mkdir(parents=True)
    source = Path(plugin_module.__file__).resolve()
    for artifact in (stable_root, latest_root):
        shutil.copy2(source, artifact / "plugin.py")
    (latest_root / "plugin.py").write_text(
        (latest_root / "plugin.py")
        .read_text(encoding="utf-8")
        .replace(
            'version = "3.0.0"',
            'version = "3.0.1"',
        ),
        encoding="utf-8",
    )
    stable_pointer = ArtifactPointer(".artifacts/3.0.0-stable")
    latest_pointer = ArtifactPointer(".artifacts/3.0.1-latest")
    write_pointers(plugin_base, stable=stable_pointer, latest=stable_pointer)
    write_plugin_manifest(
        {"setup_helper@lab": True},
        plugins_home=tmp_path / "home",
    )
    manager = PluginManager(
        plugin_dirs=[AGENT_ROOT / "plugins" / "commands"],
        event_bus=EventBus(),
        workspace=tmp_path / "workspace",
        installed_cache_root=tmp_path / "home" / "cache",
    )
    PluginSelection(tmp_path / "workspace").initialize()

    async def _switch_endpoints(
        old: tuple[tuple[str, str], ...],
        new: tuple[tuple[str, str], ...],
    ) -> None:
        _ = (old, new)

    manager.bind_endpoint_switcher(_switch_endpoints)
    await manager.load_all()
    stable = manager.current_snapshot
    assert stable is not None and stable.composition_root is not None
    stable_commands = stable.composition_root.context.require(COMMANDS).freeze()
    first = await stable_commands.execute(
        "/chatid",
        session_key="telegram:stable",
        channel="telegram",
        chat_id="stable",
        sender="hua",
    )
    assert first is not None and "stable" in first.result.text

    write_pointers(plugin_base, stable=stable_pointer, latest=latest_pointer)
    result = (await manager.reconcile_changed())[0]
    candidate = manager.ready_candidate
    assert result["publication_state"] == "latest_ready"
    assert candidate is not None and candidate.validation_workspace is not None
    validation_root = candidate.validation_workspace.parent
    result = await manager.switch_ready("setup_helper@lab")

    assert result["publication_state"] == "promoted"
    current = manager.current_snapshot
    assert current is not None and current.composition_root is not None
    second = await current.composition_root.context.require(
        COMMANDS
    ).freeze().execute(
        "/myid",
        session_key="qqbot:new",
        channel="qqbot",
        chat_id="c2c:new",
        sender="hua",
    )
    assert second is not None
    assert 'allow_from = ["new"]' in second.result.text
    from plugins.wake.api import Config as WakeConfig

    snippet = second.result.text.split("```toml\n", 1)[1].split("```", 1)[0]
    delivery = WakeConfig.model_validate(tomllib.loads(snippet)).delivery
    assert delivery is not None
    assert (delivery.channel, delivery.recipient, delivery.session_id) == (
        "qqbot",
        "c2c:new",
        "qqbot:new",
    )
    # 验证 workspace 由独立 cleanup 事务回收，不在 promote 的即时范围内。
    assert validation_root.is_relative_to(tmp_path / "workspace" / "runtime" / "plugin-validation")
    formal_root = current.composition_root
    await manager.terminate_all()
    assert formal_root.topology_view().listeners == ()
    assert formal_root.receipt().effects == ()


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
