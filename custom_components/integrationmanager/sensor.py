"""Manager's effective release channel and independent channel mode."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory

from .const import CHANNEL_MODES, CHANNELS, DOMAIN, NAME


async def async_setup_entry(hass, entry, async_add_entities):
    runtime = hass.data[DOMAIN]
    async_add_entities([ChannelSensor(runtime), ChannelModeSensor(runtime)])


class ChannelSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = f"{NAME} Release Channel"
    _attr_translation_key = "release_channel"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True
    _attr_entity_registry_visible_default = True

    def __init__(self, runtime):
        self.runtime = runtime
        self._attr_unique_id = f"{runtime.entry.entry_id}_release_channel"

    @property
    def suggested_object_id(self):
        return f"{DOMAIN}_release_channel"

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{DOMAIN}_channel_changed", self.async_write_ha_state
            )
        )

    @property
    def native_value(self):
        return CHANNELS[self.runtime.channel]

    @property
    def extra_state_attributes(self):
        return {"effective_channel": self.runtime.channel}


class ChannelModeSensor(ChannelSensor):
    _attr_name = f"{NAME} Release Channel Mode"
    _attr_translation_key = "release_channel_mode"

    def __init__(self, runtime):
        super().__init__(runtime)
        self._attr_unique_id = f"{runtime.entry.entry_id}_release_channel_mode"

    @property
    def suggested_object_id(self):
        return f"{DOMAIN}_release_channel_mode"

    @property
    def native_value(self):
        return CHANNEL_MODES[self.runtime.channel_mode]

    @property
    def extra_state_attributes(self):
        return None
