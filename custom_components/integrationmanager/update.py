"""Native Update entity for Manager only; other integrations own their updates."""

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME, REPOSITORY, VERSION
from .coordinator import async_get_coordinator
from .files import InstallError
from .github import GitHubError
from .sensor import device_info


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = hass.data[DOMAIN]
    coordinator = await async_get_coordinator(hass, entry, runtime)
    async_add_entities([ManagerUpdate(coordinator, runtime)])


class ManagerUpdate(CoordinatorEntity, UpdateEntity):
    _attr_has_entity_name = True
    _attr_name = "Update"
    _attr_installed_version = VERSION
    _attr_title = NAME
    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.RELEASE_NOTES
    _attr_entity_registry_enabled_default = True
    _attr_entity_registry_visible_default = True

    def __init__(self, coordinator, runtime):
        super().__init__(coordinator)
        self.runtime = runtime
        self._attr_unique_id = f"{runtime.entry.entry_id}_update"
        self._attr_device_info = device_info(runtime.entry)

    @property
    def entity_picture(self):
        return f"/{DOMAIN}_static/icon.png"

    @property
    def latest_version(self):
        return self.coordinator.data.version if self.coordinator.data else VERSION

    @property
    def release_url(self):
        return (
            f"https://github.com/{REPOSITORY}/releases/tag/{self.coordinator.data.tag}"
            if self.coordinator.data
            else None
        )

    @property
    def extra_state_attributes(self):
        return {"restart_required": self.runtime.restart_required}

    async def async_release_notes(self):
        return self.coordinator.data.notes if self.coordinator.data else "No release available"

    async def async_install(self, version, backup, **kwargs):
        try:
            await self.runtime.self_update(version)
        except (InstallError, GitHubError, OSError, ValueError) as err:
            raise HomeAssistantError(str(err)) from err
        self.async_write_ha_state()
