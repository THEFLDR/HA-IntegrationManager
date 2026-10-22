"""Manager's own native global Release Channel sensor."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory

from .const import CHANNELS, DOMAIN, NAME, VERSION


def device_info(entry):
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=NAME,
        manufacturer="THEFLDR",
        sw_version=VERSION,
    )


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ChannelSensor(hass.data[DOMAIN])])


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
        self._attr_unique_id = f"{runtime.entry.entry_id}_release_channel"
        self._attr_device_info = device_info(runtime.entry)

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
        return {"effective_channel": self.runtime.channel, "source": "global"}
