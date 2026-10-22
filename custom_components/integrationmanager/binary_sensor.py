"""Health of Manager's own release discovery, never of other integrations."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import async_get_coordinator
from .service import service_info


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = hass.data[DOMAIN]
    coordinator = await async_get_coordinator(hass, entry, runtime)
    async_add_entities([ManagerHealth(coordinator, runtime)])


class ManagerHealth(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Health"
    _attr_translation_key = "health"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_registry_enabled_default = True
    _attr_entity_registry_visible_default = True
    _attr_available = True

    def __init__(self, coordinator, runtime):
        super().__init__(coordinator)
        self._attr_unique_id = f"{runtime.entry.entry_id}_health"
        self._attr_device_info = service_info(runtime.entry)

    @property
    def suggested_object_id(self):
        return "health"

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        return not self.coordinator.last_update_success

    @property
    def extra_state_attributes(self):
        return {
            "scope": "Integration Manager release discovery",
            "error": str(self.coordinator.last_exception)
            if self.coordinator.last_exception
            else None,
        }
