"""Optional native HA backups, including the Supervisor reader/writer on HA OS."""

from homeassistant.components.backup import (
    CreateBackupEvent,
    CreateBackupState,
    async_get_manager,
)
from homeassistant.exceptions import HomeAssistantError

from .files import InstallError


def backup_manager(hass):
    try:
        manager = async_get_manager(hass)
    except HomeAssistantError:
        return None
    if not manager.local_backup_agents or not callable(manager.async_create_backup):
        return None
    return manager


async def async_backup(hass):
    manager = backup_manager(hass)
    if manager is None:
        raise InstallError("Home Assistant backup is unavailable; no files were changed")
    outcome = None

    def event_received(event):
        nonlocal outcome
        if isinstance(event, CreateBackupEvent) and event.state != CreateBackupState.IN_PROGRESS:
            outcome = event.state

    unsubscribe = manager.async_subscribe_events(event_received)
    try:
        # Same local destination as HA's native manual backup. The manager chooses
        # Core or Supervisor backup implementation for the current installation.
        await manager.async_create_backup(
            agent_ids=[next(iter(manager.local_backup_agents))],
            include_addons=None,
            include_all_addons=False,
            include_database=True,
            include_folders=None,
            include_homeassistant=True,
            name="Before Integration Manager update",
            password=None,
        )
        if outcome != CreateBackupState.COMPLETED:
            raise InstallError(
                "Home Assistant did not confirm a successful backup; no files were changed"
            )
    except Exception as err:
        raise InstallError(f"Home Assistant backup failed; no files were changed: {err}") from err
    finally:
        unsubscribe()
