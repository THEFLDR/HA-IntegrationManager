"""Native service registry entry, using the same mechanism as Google Translate TTS."""

from collections.abc import Mapping

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, NAME, OWNER, VERSION


def service_info(entry):
    return dr.DeviceInfo(
        entry_type=dr.DeviceEntryType.SERVICE,
        identifiers={(DOMAIN, entry.entry_id)},
        name=NAME,
        manufacturer=OWNER,
        model=NAME,
        sw_version=VERSION,
    )


def async_register_service(hass, entry):
    """Reuse the legacy identity and associate even disabled 0.1.2 entities."""
    devices, entities = dr.async_get(hass), er.async_get(hass)
    info = service_info(entry)
    if lookup := getattr(devices, "async_get_device_by_identifier", None):
        existing = lookup((DOMAIN, entry.entry_id), entry.entry_id)
    else:  # Supported HA versions before config-entry-scoped device lookups.
        existing = devices.async_get_device(identifiers=info["identifiers"])
    if existing:
        owner = getattr(existing, "config_entry_id", None)
        owners = {owner} if owner is not None else existing.config_entries
        associated = er.async_entries_for_device(
            entities, existing.id, include_disabled_entities=True
        )
        all_devices = devices.devices
        if isinstance(all_devices, Mapping):
            all_devices = all_devices.values()
        children = getattr(dr, "async_entries_for_parent_device", None)
        if (
            existing.identifiers != info["identifiers"]
            or owners != {entry.entry_id}
            or any(e.config_entry_id != entry.entry_id or e.platform != DOMAIN for e in associated)
            or (children and children(devices, existing.id))
            or any(other.via_device_id == existing.id for other in all_devices)
        ):
            raise HomeAssistantError(
                "Cannot register Integration Manager service: shared registry entry"
            )
    unique_ids = {
        f"{entry.entry_id}_{suffix}"
        for suffix in ("health", "update", "release_channel", "release_channel_mode")
    }
    owned = [
        entity
        for entity in er.async_entries_for_config_entry(entities, entry.entry_id)
        if entity.platform == DOMAIN and entity.unique_id in unique_ids
    ]
    if any(e.device_id and (not existing or e.device_id != existing.id) for e in owned):
        raise HomeAssistantError(
            "Cannot register Integration Manager service: entity has another owner"
        )
    # Updating in place preserves registry IDs, user names, areas and disabled states.
    service = devices.async_get_or_create(config_entry_id=entry.entry_id, **info)
    for entity in owned:
        if entity.device_id != service.id:
            entities.async_update_entity(entity.entity_id, device_id=service.id)
    return service
