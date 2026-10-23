"""Manager's effective release channel and independent channel mode."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory

from .const import CHANNEL_MODES, CHANNELS, DOMAIN
from .service import service_info


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = hass.data[DOMAIN]
    async_add_entities(
        [ChannelSensor(runtime), ChannelModeSensor(runtime), LastUpdateCheckSensor(runtime)]
    )


class ChannelSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Release Channel"
    _attr_translation_key = "release_channel"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True
    _attr_entity_registry_visible_default = True

    def __init__(self, runtime):
        self.runtime = runtime
        self.entity_id = f"sensor.{DOMAIN}_release_channel"
        self._attr_unique_id = f"{runtime.entry.entry_id}_release_channel"
        self._attr_device_info = service_info(runtime.entry)

    @property
    def suggested_object_id(self):
        return "release_channel"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        for signal in ("channel_changed", "runtime_changed"):
            self.async_on_remove(
                async_dispatcher_connect(self.hass, f"{DOMAIN}_{signal}", self.async_write_ha_state)
            )

    @property
    def native_value(self):
        try:
            return CHANNELS[self.runtime.channel]
        except (ValueError, KeyError):
            return None

    @property
    def extra_state_attributes(self):
        try:
            return {"effective_channel": self.runtime.channel}
        except (ValueError, KeyError):
            return {"effective_channel": None}


class ChannelModeSensor(ChannelSensor):
    _attr_name = "Release Channel Mode"
    _attr_translation_key = "release_channel_mode"

    def __init__(self, runtime):
        super().__init__(runtime)
        self.entity_id = f"sensor.{DOMAIN}_release_channel_mode"
        self._attr_unique_id = f"{runtime.entry.entry_id}_release_channel_mode"

    @property
    def suggested_object_id(self):
        return "release_channel_mode"

    @property
    def native_value(self):
        return CHANNEL_MODES.get(self.runtime.channel_mode)

    @property
    def extra_state_attributes(self):
        return None


class LastUpdateCheckSensor(ChannelSensor):
    _attr_name = "Last Update Check"
    _attr_translation_key = "last_update_check"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, runtime):
        super().__init__(runtime)
        self.entity_id = f"sensor.{DOMAIN}_last_update_check"
        self._attr_unique_id = f"{runtime.entry.entry_id}_last_update_check"

    @property
    def native_value(self):
        return self.runtime.last_update_check

    @property
    def extra_state_attributes(self):
        return None
