"""Integration Manager runtime and supported Home Assistant lifecycle."""

from collections.abc import Mapping
from pathlib import Path

from .const import DOMAIN, MANAGED, NAME, OVERRIDE, STABLE, VERSION

PLATFORMS = ["binary_sensor", "sensor", "update"]


async def async_setup(hass, config):
    from homeassistant.components.http import StaticPathConfig

    from .websocket import async_register_commands

    async_register_commands(hass)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(f"/{DOMAIN}_static", str(Path(__file__).parent / "frontend"), False)]
    )
    return True


async def async_setup_entry(hass, entry):
    from homeassistant.components import frontend, panel_custom
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from .files import FileStore
    from .github import GitHubClient
    from .manager import Manager

    options = dict(entry.options)
    options.setdefault("global_channel", options.get("channel", STABLE))
    options.setdefault("channel_mode", OVERRIDE if "channel" in options else MANAGED)
    if options != entry.options:
        hass.config_entries.async_update_entry(entry, options=options)
    _async_cleanup_legacy_device(hass, entry)
    runtime = Manager(
        hass, entry, GitHubClient(async_get_clientsession(hass)), FileStore(hass.config.config_dir)
    )
    hass.data[DOMAIN] = runtime
    try:
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=DOMAIN,
            webcomponent_name="integrationmanager-panel",
            sidebar_title=NAME,
            sidebar_icon="mdi:puzzle",
            module_url=f"/{DOMAIN}_static/panel.js?v={VERSION}",
            require_admin=True,
            config_panel_domain=DOMAIN,
        )
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        frontend.async_remove_panel(hass, DOMAIN)
        hass.data.pop(DOMAIN, None)
        raise
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


def _async_cleanup_legacy_device(hass, entry):
    """Detach entities before removing only Manager's exclusively owned old device."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    devices, entities = dr.async_get(hass), er.async_get(hass)
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        if device.identifiers != {(DOMAIN, entry.entry_id)}:
            continue
        # Older supported HA versions allowed a device to have multiple owners.
        owner = getattr(device, "config_entry_id", None)
        owners = {owner} if owner is not None else device.config_entries
        if owners != {entry.entry_id}:
            continue
        associated = er.async_entries_for_device(
            entities, device.id, include_disabled_entities=True
        )
        if any(e.config_entry_id != entry.entry_id or e.platform != DOMAIN for e in associated):
            continue
        # A device with children is no longer the standalone device we created.
        child_entries = getattr(dr, "async_entries_for_parent_device", None)
        if child_entries and child_entries(devices, device.id):
            continue
        all_devices = devices.devices
        if isinstance(all_devices, Mapping):
            all_devices = all_devices.values()
        if any(other.via_device_id == device.id for other in all_devices):
            continue
        for entity in associated:
            disabled_by = entity.disabled_by
            if disabled_by == er.RegistryEntryDisabler.DEVICE:
                # Keep the entity disabled after the disabling device is gone.
                disabled_by = er.RegistryEntryDisabler.USER
            entities.async_update_entity(
                entity.entity_id,
                device_id=None,
                area_id=entity.area_id or device.area_id,
                disabled_by=disabled_by,
            )
        devices.async_remove_device(device.id)


async def _options_updated(hass, entry):
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    async_dispatcher_send(hass, f"{DOMAIN}_channel_changed")


async def async_unload_entry(hass, entry):
    from homeassistant.components import frontend

    runtime = hass.data[DOMAIN]
    if runtime.lock.locked():
        return False
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        frontend.async_remove_panel(hass, DOMAIN)
        hass.data.pop(DOMAIN, None)
        return True
    return False
