"""One instance, with independent global and Manager release-channel options."""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import CHANNEL_MODES, CHANNELS, DOMAIN, MANAGED, NAME, OVERRIDE, STABLE


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title=NAME, data={})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow()


class OptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            options = {**self.config_entry.options, **user_input}
            if user_input["channel_mode"] != OVERRIDE:
                if "channel" in self.config_entry.options:
                    options["channel"] = self.config_entry.options["channel"]
                else:
                    options.pop("channel", None)
            return self.async_create_entry(data=options)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "global_channel",
                        default=self.config_entry.options.get(
                            "global_channel", self.config_entry.options.get("channel", STABLE)
                        ),
                    ): vol.In(CHANNELS),
                    vol.Required(
                        "channel_mode",
                        default=self.config_entry.options.get(
                            "channel_mode",
                            OVERRIDE if "channel" in self.config_entry.options else MANAGED,
                        ),
                    ): vol.In(CHANNEL_MODES),
                    vol.Required(
                        "channel", default=self.config_entry.options.get("channel", STABLE)
                    ): vol.In(CHANNELS),
                }
            ),
        )
