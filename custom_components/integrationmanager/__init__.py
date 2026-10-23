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
    from .service import async_register_service

    options = dict(entry.options)
    options.setdefault("global_channel", options.get("channel", STABLE))
    options.setdefault("channel_mode", OVERRIDE if "channel" in options else MANAGED)
    if options != entry.options:
        hass.config_entries.async_update_entry(entry, options=options)
    async_register_service(hass, entry)
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
