"""Health of Manager's own release discovery, never of other integrations."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
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
        self.runtime = runtime
        self.entity_id = f"binary_sensor.{DOMAIN}_health"
        self._attr_unique_id = f"{runtime.entry.entry_id}_health"
        self._attr_device_info = service_info(runtime.entry)

    @property
    def suggested_object_id(self):
        return "health"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{DOMAIN}_runtime_changed", self.async_write_ha_state
            )
        )

    @property
    def available(self):
        return self.runtime.ready and self.runtime.health.available

    @property
    def is_on(self):
        return bool(self.runtime.health.failed_checks)

    @property
    def extra_state_attributes(self):
        return {"scope": "Integration Manager health", **self.runtime.health.attributes}
