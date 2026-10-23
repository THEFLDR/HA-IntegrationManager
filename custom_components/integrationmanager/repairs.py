"""Confirmation-only native restart repair, following HACS's Repairs platform."""

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

ISSUE_ID = "restart_required"


def async_restart_issue(hass, required):
    if required:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_ID,
            is_fixable=True,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_ID,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_ID)


class RestartRequiredFixFlow(RepairsFlow):
    async def async_step_init(self, user_input=None):
        return await self.async_step_confirm_restart()

    async def async_step_confirm_restart(self, user_input=None):
        runtime = self.hass.data.get(DOMAIN)
        if runtime is None or not runtime.restart_required:
            return self.async_abort(reason="no_restart_required")
        errors = {}
        if user_input is not None:
            if runtime.lock.locked():
                errors["base"] = "operation_in_progress"
            else:
                try:
                    async with runtime.lock:
                        await runtime.async_save()
                        await self.hass.services.async_call(
                            "homeassistant", "restart", blocking=True
                        )
                except (HomeAssistantError, OSError):
                    errors["base"] = "restart_failed"
                else:
                    # CREATE_ENTRY would prematurely delete the issue. An abort
                    # leaves it until startup confirms the new runtime is active.
                    return self.async_abort(reason="restart_requested")
        return self.async_show_form(
            step_id="confirm_restart",
            data_schema=vol.Schema({}),
            errors=errors,
        )


async def async_create_fix_flow(hass, issue_id, data):
    if issue_id == ISSUE_ID:
        return RestartRequiredFixFlow()
    return None
