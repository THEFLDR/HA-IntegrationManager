from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory

from custom_components import integrationmanager as integration
from custom_components.integrationmanager.channel import get_global_channel, resolve_channel
from custom_components.integrationmanager.config_flow import OptionsFlow
from custom_components.integrationmanager.const import DOMAIN, RC, STABLE
from custom_components.integrationmanager.sensor import ChannelModeSensor, ChannelSensor
from custom_components.integrationmanager.service import async_register_service
from custom_components.integrationmanager.websocket import command
from tests.test_ha import add_entry, entry


@pytest.mark.parametrize("mode", ["managed", "default", "override"])
@pytest.mark.parametrize("global_channel", [STABLE, RC])
@pytest.mark.parametrize("override", [STABLE, RC])
def test_independent_channel_modes(runtime, mode, global_channel, override):
    runtime.entry.options = {
        "channel_mode": mode,
        "global_channel": global_channel,
        "channel": override,
    }
    expected = global_channel if mode == "managed" else STABLE if mode == "default" else override
    assert runtime.channel == expected
    assert runtime.global_channel == get_global_channel(runtime.hass) == global_channel
    assert ChannelModeSensor(runtime).native_value == mode.title()
    assert ChannelModeSensor(runtime).entity_category == EntityCategory.DIAGNOSTIC
    assert ChannelSensor(runtime).extra_state_attributes == {"effective_channel": expected}
    assert ChannelModeSensor(runtime).extra_state_attributes is None
    runtime.entry.options["global_channel"] = RC if global_channel == STABLE else STABLE
    assert runtime.channel == (runtime.global_channel if mode == "managed" else expected)
    assert runtime.entry.options["channel"] == override


def test_explicit_modes_and_legacy_channel_interface(runtime):
    runtime.entry.options = {"global_channel": RC}
    assert resolve_channel(runtime.hass)["source"] == "managed"
    assert resolve_channel(runtime.hass, mode="default")["effective_channel"] == STABLE
    assert resolve_channel(runtime.hass, STABLE)["source"] == "override"
    for invalid in ("", "invented"):
        with pytest.raises(ValueError, match="mode"):
            resolve_channel(runtime.hass, mode=invalid)


@pytest.mark.parametrize("legacy", [{}, {"channel": STABLE}, {"channel": RC}])
async def test_legacy_configuration_migration(hass, monkeypatch, legacy):
    current = entry()
    object.__setattr__(current, "options", legacy)
    await add_entry(hass, current, monkeypatch)
    monkeypatch.setattr("homeassistant.helpers.aiohttp_client.async_get_clientsession", Mock())
    monkeypatch.setattr("homeassistant.components.panel_custom.async_register_panel", AsyncMock())
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", AsyncMock())
    assert await integration.async_setup_entry(hass, current)
    assert current.options["global_channel"] == legacy.get("channel", STABLE)
    assert current.options["channel_mode"] == ("override" if "channel" in legacy else "managed")
    assert current.options.get("channel") == legacy.get("channel")
    first = dict(current.options)
    assert await integration.async_setup_entry(hass, current)
    assert dict(current.options) == first


@pytest.mark.parametrize(
    "disabled_by", [er.RegistryEntryDisabler.USER, er.RegistryEntryDisabler.DEVICE]
)
async def test_legacy_service_upgrade_preserves_customizations(hass, monkeypatch, disabled_by):
    current = entry()
    await add_entry(hass, current, monkeypatch)
    registry, devices = er.async_get(hass), dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=current.entry_id, identifiers={(DOMAIN, current.entry_id)}
    )
    entity = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{current.entry_id}_release_channel",
        config_entry=current,
        device_id=device.id,
        original_name="Release Channel",
        suggested_object_id="existing_custom_channel",
    )
    registry.async_update_entity(
        entity.entity_id,
        name="Owner's channel",
        hidden_by=er.RegistryEntryHider.USER,
        disabled_by=disabled_by,
    )
    if disabled_by == er.RegistryEntryDisabler.DEVICE:
        devices.async_update_device(device.id, disabled_by=dr.DeviceEntryDisabler.USER)
    service = async_register_service(hass, current)
    assert async_register_service(hass, current).id == service.id == device.id
    await hass.async_block_till_done()
    assert devices.async_get(device.id).entry_type == dr.DeviceEntryType.SERVICE
    preserved = registry.async_get(entity.entity_id)
    assert preserved is not None and preserved.device_id == service.id
    assert preserved.unique_id == f"{current.entry_id}_release_channel"
    assert preserved.config_entry_id == current.entry_id
    assert preserved.name == "Owner's channel" and preserved.hidden_by == er.RegistryEntryHider.USER
    assert preserved.disabled_by == disabled_by


@pytest.mark.parametrize("reason", ["foreign_entity", "different_identity", "dependent_device"])
async def test_cleanup_keeps_uncertain_or_shared_devices(hass, reason, monkeypatch):
    current = entry()
    await add_entry(hass, current, monkeypatch)
    devices, entities = dr.async_get(hass), er.async_get(hass)
    identifiers = (
        {(DOMAIN, current.entry_id)} if reason != "different_identity" else {(DOMAIN, "other")}
    )
    device = devices.async_get_or_create(config_entry_id=current.entry_id, identifiers=identifiers)
    if reason == "foreign_entity":
        foreign = entry("other")
        await add_entry(hass, foreign, monkeypatch)
        entities.async_get_or_create(
            "sensor", "other", "foreign", config_entry=foreign, device_id=device.id
        )
    elif reason == "dependent_device":
        devices.async_get_or_create(
            config_entry_id=current.entry_id,
            identifiers={(DOMAIN, "child")},
            via_device_id=device.id,
        )
    if reason == "different_identity":
        service = async_register_service(hass, current)
        assert service.id != device.id and service.entry_type == dr.DeviceEntryType.SERVICE
    else:
        with pytest.raises(HomeAssistantError, match="shared registry entry"):
            async_register_service(hass, current)
    await hass.async_block_till_done()
    assert devices.async_get(device.id) is not None


async def test_old_ha_shared_device_is_never_removed(hass, monkeypatch):
    current = entry()
    device = SimpleNamespace(
        id="shared",
        identifiers={(DOMAIN, current.entry_id)},
        config_entries={current.entry_id, "another"},
    )
    monkeypatch.setattr(
        dr.async_get(hass), "async_get_device_by_identifier", Mock(return_value=device)
    )
    remove = Mock()
    monkeypatch.setattr(dr.async_get(hass), "async_remove_device", remove)
    with pytest.raises(HomeAssistantError, match="shared registry entry"):
        async_register_service(hass, current)
    remove.assert_not_called()


@pytest.mark.parametrize("disabled", [False, True])
async def test_upgrade_012_service_registration_is_idempotent(hass, monkeypatch, disabled):
    current = entry()
    object.__setattr__(
        current, "options", {"global_channel": RC, "channel_mode": "override", "channel": STABLE}
    )
    await add_entry(hass, current, monkeypatch)
    registry, devices = er.async_get(hass), dr.async_get(hass)
    before = {}
    for platform, suffix in [
        ("binary_sensor", "health"),
        ("update", "update"),
        ("sensor", "release_channel"),
        ("sensor", "release_channel_mode"),
    ]:
        entity = registry.async_get_or_create(
            platform,
            DOMAIN,
            f"{current.entry_id}_{suffix}",
            config_entry=current,
            suggested_object_id=f"owner_{suffix}",
            disabled_by=er.RegistryEntryDisabler.USER if disabled else None,
        )
        registry.async_update_entity(
            entity.entity_id, name=f"Custom {suffix}", hidden_by=er.RegistryEntryHider.USER
        )
        before[entity.entity_id] = registry.async_get(entity.entity_id)
    service = async_register_service(hass, current)
    devices.async_update_device(service.id, name_by_user="My service")
    again = async_register_service(hass, current)
    assert again.id == service.id and again.name_by_user == "My service"
    assert again.entry_type == dr.DeviceEntryType.SERVICE
    assert again.manufacturer is None and again.name == "Integration Manager"
    assert len(dr.async_entries_for_config_entry(devices, current.entry_id)) == 1
    for entity_id, old in before.items():
        upgraded = registry.async_get(entity_id)
        assert upgraded.device_id == service.id
        for field in ("unique_id", "config_entry_id", "name", "hidden_by", "disabled_by"):
            assert getattr(upgraded, field) == getattr(old, field)
    assert current.options == {"global_channel": RC, "channel_mode": "override", "channel": STABLE}


async def test_service_registration_rejects_entities_on_unrelated_device(hass, monkeypatch):
    current = entry()
    await add_entry(hass, current, monkeypatch)
    devices, registry = dr.async_get(hass), er.async_get(hass)
    other = devices.async_get_or_create(
        config_entry_id=current.entry_id, identifiers={(DOMAIN, "other")}
    )
    entity = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{current.entry_id}_release_channel",
        config_entry=current,
        device_id=other.id,
    )
    with pytest.raises(HomeAssistantError, match="another owner"):
        async_register_service(hass, current)
    assert registry.async_get(entity.entity_id).device_id == other.id
    assert len(dr.async_entries_for_config_entry(devices, current.entry_id)) == 1


async def test_service_registration_supports_older_registry_api(hass, monkeypatch):
    current = entry()
    await add_entry(hass, current, monkeypatch)
    devices = dr.async_get(hass)
    monkeypatch.setattr(devices, "async_get_device_by_identifier", None)
    legacy_lookup = Mock(return_value=None)
    monkeypatch.setattr(devices, "async_get_device", legacy_lookup)
    service = async_register_service(hass, current)
    legacy_lookup.assert_called_once_with(identifiers={(DOMAIN, current.entry_id)})
    assert service.entry_type == dr.DeviceEntryType.SERVICE


async def test_dashboard_channel_controls_preserve_explicit_intent(hass, runtime, monkeypatch):
    current = entry()
    object.__setattr__(
        current, "options", {"global_channel": STABLE, "channel_mode": "override", "channel": RC}
    )
    await add_entry(hass, current, monkeypatch)
    runtime.entry, runtime.hass = current, hass
    hass.data[DOMAIN] = runtime
    connection = SimpleNamespace(
        user=SimpleNamespace(is_admin=True), send_result=Mock(), send_error=Mock()
    )
    handle = command.__wrapped__
    await handle(hass, connection, {"id": 1, "action": "channel", "channel": RC})
    assert current.options["global_channel"] == RC and current.options["channel"] == RC
    for mode, expected in [("default", STABLE), ("managed", RC), ("override", RC)]:
        await handle(
            hass, connection, {"id": 2, "action": "manager_channel", "mode": mode, "channel": RC}
        )
        assert runtime.channel_mode == mode and runtime.channel == expected
        assert current.options["channel"] == RC and current.options["global_channel"] == RC
    await handle(hass, connection, {"id": 3, "action": "channel", "channel": STABLE})
    assert runtime.channel == RC and current.options["channel"] == RC
    runtime.installed = AsyncMock(return_value=[])
    await handle(hass, connection, {"id": 4, "action": "overview"})
    overview = connection.send_result.call_args.args[1]
    assert overview["channel"] == STABLE and overview["manager_channel"] == RC
    assert overview["manager_channel_mode"] == "override"
    connection.send_error.assert_not_called()


@pytest.mark.parametrize("mode", ["managed", "default"])
async def test_native_options_preserve_dormant_override(hass, monkeypatch, mode):
    current = entry()
    object.__setattr__(
        current, "options", {"global_channel": STABLE, "channel_mode": "override", "channel": RC}
    )
    options = OptionsFlow()
    options.hass, options.handler = hass, current.entry_id
    monkeypatch.setattr(hass.config_entries, "async_get_known_entry", Mock(return_value=current))
    result = await options.async_step_init(
        {"global_channel": RC, "channel_mode": mode, "channel": STABLE}
    )
    assert result["data"] == {"global_channel": RC, "channel_mode": mode, "channel": RC}


async def test_options_before_first_upgrade_load_preserve_legacy_defaults(hass, monkeypatch):
    current = entry()
    object.__setattr__(current, "options", {"channel": RC})
    options = OptionsFlow()
    options.hass, options.handler = hass, current.entry_id
    monkeypatch.setattr(hass.config_entries, "async_get_known_entry", Mock(return_value=current))
    form = await options.async_step_init()
    assert form["data_schema"]({}) == {
        "global_channel": RC,
        "channel_mode": "override",
        "channel": RC,
    }
