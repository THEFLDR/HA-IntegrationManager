import json
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant import config_entries
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components import integrationmanager as integration
from custom_components.integrationmanager.binary_sensor import async_setup_entry as setup_health
from custom_components.integrationmanager.catalog import CatalogEntry
from custom_components.integrationmanager.config_flow import ConfigFlow, OptionsFlow
from custom_components.integrationmanager.const import DOMAIN, RC, STABLE, VERSION
from custom_components.integrationmanager.files import FileStore, InstallError
from custom_components.integrationmanager.inventory import overview, scan_files
from custom_components.integrationmanager.sensor import ChannelSensor
from custom_components.integrationmanager.sensor import async_setup_entry as setup_sensor
from custom_components.integrationmanager.update import ManagerUpdate
from custom_components.integrationmanager.update import async_setup_entry as setup_update
from custom_components.integrationmanager.websocket import command


def entry(domain=DOMAIN):
    return config_entries.ConfigEntry(
        domain=domain,
        title="Integration Manager",
        data={},
        options={},
        source="user",
        version=1,
        minor_version=1,
        unique_id=domain,
        discovery_keys=MappingProxyType({}),
        subentries_data=[],
    )


def write_integration(root, domain, name="Friendly Native", version="0.1.0"):
    target = root / domain
    target.mkdir(parents=True)
    (target / "manifest.json").write_text(
        json.dumps({"domain": domain, "name": name, "version": version})
    )


async def test_registry_discovery_including_unlisted(hass, tmp_path, monkeypatch):
    store = FileStore(str(tmp_path))
    write_integration(store.root, "sample")
    write_integration(store.root, "unlisted", "Unlisted Integration")
    first, second = entry("sample"), entry("unlisted")
    monkeypatch.setattr(hass.config_entries, "async_entries", Mock(return_value=[first, second]))
    monkeypatch.setattr(
        hass.config_entries,
        "async_get_entry",
        lambda key: next((e for e in [first, second] if e.entry_id == key), None),
    )
    registry = er.async_get(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=first.entry_id, identifiers={("sample", "device")}
    )
    update = registry.async_get_or_create(
        "update",
        "sample",
        "native-update",
        config_entry=first,
        original_name="Software",
        device_id=device.id,
    )
    health = registry.async_get_or_create(
        "binary_sensor",
        "sample",
        "native-health",
        config_entry=first,
        original_device_class="problem",
        original_name="Health",
    )
    channel = registry.async_get_or_create(
        "sensor", "unlisted", "native-channel", config_entry=second, original_name="Release Channel"
    )
    registry.async_get_or_create(
        "sensor", "sample", "other", config_entry=first, original_name="Temperature"
    )
    hass.states.async_set(update.entity_id, "on", {"installed_version": "0.1.0"})
    hass.states.async_set(health.entity_id, "off")
    hass.states.async_set(
        channel.entity_id, "Managed", {"source": "managed", "effective_channel": RC}
    )
    result = await overview(
        hass, store, [CatalogEntry("Catalog Friendly", "THEFLDR/Repo", "sample")]
    )
    assert [item["name"] for item in result] == ["Catalog Friendly", "Unlisted Integration"]
    assert {e["entity_id"] for e in result[0]["entities"]} == {update.entity_id, health.entity_id}
    assert result[1]["entities"][0]["attributes"]["effective_channel"] == RC
    assert registry.async_get(update.entity_id).disabled is False
    assert registry.async_get(update.entity_id).hidden is False


def test_inventory_ignores_unsafe_and_bad_files(tmp_path):
    (tmp_path / "outside").mkdir()
    (tmp_path / "sample").symlink_to(tmp_path / "outside", target_is_directory=True)
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "manifest.json").write_text("invalid")
    assert scan_files(tmp_path) == {}


async def test_lifecycle(hass, monkeypatch):
    monkeypatch.setattr("homeassistant.helpers.aiohttp_client.async_get_clientsession", Mock())
    current = entry()
    panel = AsyncMock()
    monkeypatch.setattr("homeassistant.components.panel_custom.async_register_panel", panel)
    remove = Mock()
    monkeypatch.setattr("homeassistant.components.frontend.async_remove_panel", remove)
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", AsyncMock())
    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True))
    assert await integration.async_setup_entry(hass, current)
    assert hass.data[DOMAIN].channel == STABLE
    assert panel.call_args.kwargs["require_admin"] is True
    async with hass.data[DOMAIN].lock:
        assert await integration.async_unload_entry(hass, current) is False
    assert await integration.async_unload_entry(hass, current)
    assert DOMAIN not in hass.data
    remove.assert_called_once_with(hass, DOMAIN)


async def test_setup_failure_cleans_panel_and_runtime(hass, monkeypatch):
    monkeypatch.setattr("homeassistant.helpers.aiohttp_client.async_get_clientsession", Mock())
    monkeypatch.setattr(
        "homeassistant.components.panel_custom.async_register_panel",
        AsyncMock(side_effect=ValueError("bad panel")),
    )
    monkeypatch.setattr("homeassistant.components.frontend.async_remove_panel", Mock())
    with pytest.raises(ValueError):
        await integration.async_setup_entry(hass, entry())
    assert DOMAIN not in hass.data


async def test_static_setup(hass, monkeypatch):
    http = SimpleNamespace(async_register_static_paths=AsyncMock())
    hass.http = http
    register = Mock()
    monkeypatch.setattr("homeassistant.components.websocket_api.async_register_command", register)
    assert await integration.async_setup(hass, {})
    register.assert_called_once()
    assert http.async_register_static_paths.call_args.args[0][0].url_path == f"/{DOMAIN}_static"


async def test_native_entities_and_update_errors(hass, runtime):
    hass.data[DOMAIN] = runtime
    runtime.release = AsyncMock(return_value=None)
    add = Mock()
    runtime.entry.async_on_unload = Mock()
    await setup_sensor(hass, runtime.entry, add)
    sensor = add.call_args.args[0][0]
    assert isinstance(sensor, ChannelSensor)
    assert sensor.native_value == "Stable"
    assert sensor.extra_state_attributes == {"source": "default", "effective_channel": STABLE}
    runtime.entry.options["channel"] = RC
    assert sensor.native_value == "Release Candidate"
    assert sensor.extra_state_attributes == {"source": "override", "effective_channel": RC}
    runtime.entry.options["channel"] = STABLE
    assert sensor.extra_state_attributes == {"source": "override", "effective_channel": STABLE}
    runtime.entry.options["channel"] = RC
    await setup_update(hass, runtime.entry, add)
    entity = add.call_args.args[0][0]
    assert isinstance(entity, ManagerUpdate)
    assert entity.installed_version == entity.latest_version == VERSION
    assert entity.entity_picture == f"/{DOMAIN}_static/icon.png"
    assert await entity.async_release_notes() == "No release available"
    runtime.self_update = AsyncMock(side_effect=InstallError("download error"))
    with pytest.raises(HomeAssistantError, match="download error"):
        await entity.async_install(None, False)
    entity.coordinator.data = SimpleNamespace(version="0.2.0", notes="Release notes", tag="0.2.0")
    assert entity.latest_version == "0.2.0"
    assert entity.release_url.endswith("0.2.0")
    assert await entity.async_release_notes() == "Release notes"
    runtime.self_update = AsyncMock()
    entity.async_write_ha_state = Mock()
    await entity.async_install("0.2.0", False)
    runtime.self_update.assert_awaited_once_with("0.2.0")
    await setup_health(hass, runtime.entry, add)
    health = add.call_args.args[0][0]
    assert health.coordinator is entity.coordinator
    assert health.available and not health.is_on
    health.coordinator.last_update_success = False
    health.coordinator.last_exception = ValueError("offline")
    assert health.is_on
    assert health.extra_state_attributes["error"] == "offline"


async def test_restart_notification_is_manual(hass, runtime):
    from homeassistant.components import persistent_notification

    await persistent_notification.async_setup(hass, {})
    runtime.hass = hass
    runtime._restart_notice()
    assert runtime.restart_required
    # Only a notification, no restart service invoked.
    assert not hass.services.has_service("homeassistant", "restart")


async def test_config_flow_and_single_instance(hass):
    flow = ConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    assert (await flow.async_step_user())["type"] == "form"
    result = await flow.async_step_user({})
    assert result["type"] == "create_entry"
    assert result["data"] == {}


async def test_options(hass, monkeypatch):
    options = OptionsFlow()
    options.hass = hass
    # HA's options handler binds config_entry through handler context.
    current = entry()
    options.handler = current.entry_id
    monkeypatch.setattr(hass.config_entries, "async_get_known_entry", Mock(return_value=current))
    form = await options.async_step_init()
    assert form["type"] == "form"
    assert form["data_schema"]({"channel": RC}) == {"channel": RC}
    result = await options.async_step_init({"channel": RC})
    assert result["data"] == {"channel": RC}


async def test_websocket_admin_and_errors(hass, runtime):
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=True), send_result=Mock(), send_error=Mock()
    )
    handle = command.__wrapped__
    await handle(hass, connection, {"id": 1, "action": "overview"})
    connection.send_error.assert_called_with(1, "not_loaded", "Integration Manager is not loaded")
    hass.data[DOMAIN] = runtime
    runtime.installed = AsyncMock(return_value=[])
    await handle(hass, connection, {"id": 2, "action": "overview"})
    assert connection.send_result.call_args.args[1]["channel"] == STABLE
    runtime.install = AsyncMock(side_effect=InstallError("bad artifact"))
    await handle(hass, connection, {"id": 3, "action": "install", "domain": "sample"})
    connection.send_error.assert_called_with(3, "operation_failed", "bad artifact")
    connection.user = None
    with pytest.raises(Unauthorized):
        await handle(hass, connection, {"id": 4, "action": "remove", "domain": "sample"})


async def test_websocket_commands(hass, runtime, monkeypatch):
    hass.data[DOMAIN] = runtime
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=True), send_result=Mock(), send_error=Mock()
    )
    handle = command.__wrapped__
    for action in ["catalog", "remove", "cleanup"]:
        method = "catalog_view" if action == "catalog" else action
        setattr(runtime, method, AsyncMock(return_value=[]))
        await handle(
            hass,
            connection,
            {
                "id": 1,
                "action": action,
                "domain": "sample",
                "confirmation": "REMOVE sample CONFIGURATION",
            },
        )
        getattr(runtime, method).assert_awaited_once()
    update = Mock()
    monkeypatch.setattr(hass.config_entries, "async_update_entry", update)
    await handle(hass, connection, {"id": 2, "action": "channel", "channel": RC})
    assert update.call_args.kwargs["options"] == {"channel": RC}
    runtime.restart_required = True
    await handle(hass, connection, {"id": 3, "action": "channel", "channel": RC})
    assert connection.send_error.call_args.args[1] == "operation_failed"


async def test_websocket_cleanup_reports_updated_restart_state(hass, runtime):
    hass.data[DOMAIN] = runtime
    runtime.installed = AsyncMock(return_value=[{"domain": "sample"}])
    runtime.hass.config_entries.async_entries.return_value = [SimpleNamespace(entry_id="owned")]
    runtime.hass.config_entries.async_remove.return_value = {"require_restart": True}
    runtime._restart_notice = Mock(
        side_effect=lambda **kw: setattr(runtime, "restart_required", True)
    )
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=True), send_result=Mock(), send_error=Mock()
    )
    await command.__wrapped__(
        hass,
        connection,
        {
            "id": 1,
            "action": "cleanup",
            "domain": "sample",
            "confirmation": "REMOVE sample CONFIGURATION",
        },
    )
    connection.send_error.assert_not_called()
    result = connection.send_result.call_args.args[1]
    assert result["restart_required"] is True
    assert "Restart Home Assistant" in result["message"]
    connection.send_result.reset_mock()
    runtime.hass.config_entries.async_remove.return_value = False
    await command.__wrapped__(
        hass,
        connection,
        {
            "id": 2,
            "action": "cleanup",
            "domain": "sample",
            "confirmation": "REMOVE sample CONFIGURATION",
        },
    )
    connection.send_result.assert_not_called()
    assert "cleanup incomplete" in connection.send_error.call_args.args[2]
