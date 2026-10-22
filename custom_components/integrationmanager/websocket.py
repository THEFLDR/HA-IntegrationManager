"""Authenticated admin commands for the small local management panel."""

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.exceptions import HomeAssistantError, Unauthorized

from .const import CHANNELS, DOMAIN
from .files import InstallError
from .github import GitHubError


def async_register_commands(hass):
    websocket_api.async_register_command(hass, command)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/command",
        vol.Required("action"): vol.In(
            ["overview", "catalog", "install", "remove", "cleanup", "channel"]
        ),
        vol.Optional("domain"): str,
        vol.Optional("confirmation"): str,
        vol.Optional("channel"): vol.In(CHANNELS),
    }
)
@websocket_api.async_response
async def command(hass, connection, msg):
    if connection.user is None or not connection.user.is_admin:
        raise Unauthorized
    runtime = hass.data.get(DOMAIN)
    if runtime is None:
        connection.send_error(msg["id"], "not_loaded", "Integration Manager is not loaded")
        return
    try:
        action = msg["action"]
        result = {"restart_required": runtime.restart_required}
        if action == "overview":
            result.update(integrations=await runtime.installed(), channel=runtime.channel)
        elif action == "catalog":
            result.update(integrations=await runtime.catalog_view())
        elif action in {"install", "remove"}:
            await getattr(runtime, action)(msg["domain"])
            result["restart_required"] = runtime.restart_required
        elif action == "cleanup":
            result["message"] = await runtime.cleanup(msg["domain"], msg.get("confirmation"))
        elif action == "channel":
            if runtime.lock.locked() or runtime.restart_required:
                raise InstallError(
                    "Finish the current operation and restart HA before changing channel"
                )
            hass.config_entries.async_update_entry(
                runtime.entry, options={**runtime.entry.options, "channel": msg["channel"]}
            )
        connection.send_result(msg["id"], result)
    except (InstallError, GitHubError, HomeAssistantError, ValueError, KeyError, OSError) as err:
        connection.send_error(msg["id"], "operation_failed", str(err))
