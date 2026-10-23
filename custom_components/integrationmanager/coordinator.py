"""One coordinator for Manager's own release availability and health."""

import logging
from datetime import timedelta

from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, NAME, REPOSITORY
from .github import GitHubError


async def async_get_coordinator(hass, entry, runtime):
    async def create():
        async def fetch():
            try:
                return await runtime.release(REPOSITORY, DOMAIN)
            except (GitHubError, ValueError) as err:
                raise UpdateFailed(str(err)) from err

        coordinator = DataUpdateCoordinator(
            hass,
            logging.getLogger(__name__),
            name=NAME,
            update_method=fetch,
            update_interval=timedelta(hours=6),
            config_entry=entry,
        )
        entry.async_on_unload(
            coordinator.async_add_listener(lambda: runtime.schedule_health(filesystem=True))
        )
        # Offline startup keeps the local dashboard and native entities present.
        await coordinator.async_refresh()
        entry.async_on_unload(
            async_dispatcher_connect(
                hass, f"{DOMAIN}_channel_changed", coordinator.async_request_refresh
            )
        )
        return coordinator

    if runtime.coordinator_task is None:
        runtime.coordinator_task = hass.async_create_task(create())
    return await runtime.coordinator_task
