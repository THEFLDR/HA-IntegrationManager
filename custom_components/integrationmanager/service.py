"""Native service registry entry, using the same mechanism as Google Translate TTS."""

import logging
from collections.abc import Mapping

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, NAME, VERSION

_LOGGER = logging.getLogger(__name__)
ENTITY_DOMAINS = {
    "health": "binary_sensor",
    "update": "update",
    "release_channel": "sensor",
    "release_channel_mode": "sensor",
    "last_update_check": "sensor",
}


def service_info(entry):
    return dr.DeviceInfo(
        entry_type=dr.DeviceEntryType.SERVICE,
        identifiers={(DOMAIN, entry.entry_id)},
        name=NAME,
        manufacturer=None,
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
    unique_ids = {f"{entry.entry_id}_{suffix}" for suffix in ENTITY_DOMAINS}
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


def async_migrate_entity_ids(hass, entry):
    """Only exact historical defaults qualify; custom IDs and collisions stay put."""
    registry = er.async_get(hass)
    warnings = []
    for suffix, entity_domain in ENTITY_DOMAINS.items():
        entity_id = registry.async_get_entity_id(
            entity_domain, DOMAIN, f"{entry.entry_id}_{suffix}"
        )
        entity = registry.async_get(entity_id) if entity_id else None
        canonical = f"{entity_domain}.{DOMAIN}_{suffix}"
        if not entity or entity.config_entry_id != entry.entry_id:
            continue
        if entity.entity_id != f"{entity_domain}.integration_manager_{suffix}":
            continue
        if registry.async_get(canonical) or hass.states.get(canonical):
            warnings.append(f"Cannot migrate {entity_id}: {canonical} is in use")
        else:
            registry.async_update_entity(entity_id, new_entity_id=canonical)
            _LOGGER.warning(
                "Renamed default entity %s to %s; update references in automations and dashboards",
                entity_id,
                canonical,
            )
    for warning in warnings:
        _LOGGER.warning(warning)
    return warnings
