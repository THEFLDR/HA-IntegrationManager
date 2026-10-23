"""Integration Manager runtime and supported Home Assistant lifecycle."""

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
    from .service import async_migrate_entity_ids, async_register_service

    options = dict(entry.options)
    options.setdefault("global_channel", options.get("channel", STABLE))
    options.setdefault("channel_mode", OVERRIDE if "channel" in options else MANAGED)
    if options != entry.options:
        hass.config_entries.async_update_entry(entry, options=options)
    service = async_register_service(hass, entry)
    migration_warnings = async_migrate_entity_ids(hass, entry)
    runtime = Manager(
        hass, entry, GitHubClient(async_get_clientsession(hass)), FileStore(hass.config.config_dir)
    )
    runtime.service_id = service.id
    await runtime.async_initialize()
    if migration_warnings:
        runtime.health.warnings["entity_ids"] = "; ".join(migration_warnings)
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
    from homeassistant.core import callback

    @callback
    def registry_changed(event):
        runtime.schedule_health()

    for event in ("entity_registry_updated", "device_registry_updated"):
        entry.async_on_unload(hass.bus.async_listen(event, registry_changed))
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    runtime.ready = True
    await runtime.health.async_refresh(filesystem=True)
    return True


async def _options_updated(hass, entry):
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    async_dispatcher_send(hass, f"{DOMAIN}_channel_changed")
    hass.data[DOMAIN].schedule_health()


async def async_unload_entry(hass, entry):
    from homeassistant.components import frontend

    runtime = hass.data[DOMAIN]
    if runtime.lock.locked():
        return False
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        runtime.ready = False
        if runtime.health_task and not runtime.health_task.done():
            await runtime.health_task
        frontend.async_remove_panel(hass, DOMAIN)
        hass.data.pop(DOMAIN, None)
        return True
    return False
