"""Discover installed integrations and associate existing HA entities."""

import json

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_custom_components

from .catalog import DOMAIN_PATTERN
from .const import DOMAIN, NAME


def scan_files(root):
    result = {}
    if not root.is_dir() or root.is_symlink():
        return result
    for directory in root.iterdir():
        if (
            not DOMAIN_PATTERN.fullmatch(directory.name)
            or directory.is_symlink()
            or not directory.is_dir()
        ):
            continue
        try:
            manifest_path = directory / "manifest.json"
            if manifest_path.is_symlink() or manifest_path.stat().st_size > 65536:
                continue
            manifest = json.loads(manifest_path.read_bytes())
            if manifest.get("domain") == directory.name:
                name, version = manifest.get("name"), manifest.get("version")
                result[directory.name] = {
                    "name": name
                    if isinstance(name, str) and name.strip()
                    else directory.name.replace("_", " ").title(),
                    "version": version if isinstance(version, str) else None,
                    "files_present": True,
                }
        except (OSError, ValueError, AttributeError):
            continue
    return result


async def overview(hass, store, catalog):
    installed = await hass.async_add_executor_job(scan_files, store.root)
    known_custom = await async_get_custom_components(hass)
    entries = hass.config_entries.async_entries()
    registry, devices = er.async_get(hass), dr.async_get(hass)
    names = {entry.domain: entry.name for entry in catalog}
    names[DOMAIN] = NAME
    for entry in entries:
        # Configured custom integrations whose files have been removed remain visible.
        if entry.domain not in installed and (store.root / entry.domain).exists():
            continue
        if entry.domain in installed or entry.domain in names or entry.domain in known_custom:
            installed.setdefault(
                entry.domain, {"name": entry.title, "version": None, "files_present": False}
            )
    entry_domains = {entry.entry_id: entry.domain for entry in entries if entry.domain in installed}
    destinations = {domain: set() for domain in installed}
    for entry in entries:
        if entry.domain in destinations:
            destinations[entry.domain].update(
                device.id for device in dr.async_entries_for_config_entry(devices, entry.entry_id)
            )
    for domain, item in installed.items():
        targets = destinations[domain]
        item.update(
            domain=domain,
            name=names.get(domain, item["name"]),
            entities=[],
            device_id=next(iter(targets)) if len(targets) == 1 else None,
        )
    for entity in registry.entities.values():
        domains = (
            {entry_domains[entity.config_entry_id]}
            if entity.config_entry_id in entry_domains
            else set()
        )
        if (
            not entity.config_entry_id
            and entity.device_id
            and (device := devices.async_get(entity.device_id))
        ):
            owner = getattr(device, "config_entry_id", None)
            owners = {owner} if owner else device.config_entries
            domains.update(entry_domains[e] for e in owners if e in entry_domains)
        # Include only existing native entities. Never create or enable/disable another integration's entities.
        kind = entity.domain
        label = (entity.original_name or entity.name or "").casefold()
        if entity.translation_key == "last_update_check":
            entity_kind = "last_update_check"
        elif kind == "update":
            entity_kind = "update"
        elif entity.translation_key == "release_channel_mode" or label == "release channel mode":
            entity_kind = "release_channel_mode"
        elif entity.translation_key == "release_channel" or label == "release channel":
            entity_kind = "release_channel"
        elif (
            entity.translation_key == "health"
            or label == "health"
            or (kind == "binary_sensor" and entity.original_device_class == "problem")
        ):
            entity_kind = "health"
        else:
            continue
        state = hass.states.get(entity.entity_id)
        for domain in domains:
            installed[domain]["entities"].append(
                {
                    "entity_id": entity.entity_id,
                    "kind": entity_kind,
                    "name": entity.name or entity.original_name or entity.entity_id,
                    "state": state.state if state else "unavailable",
                    "attributes": dict(state.attributes) if state else {},
                    "disabled": entity.disabled,
                    "hidden": entity.hidden,
                }
            )
    return sorted(installed.values(), key=lambda item: item["name"].casefold())
