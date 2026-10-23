"""Bounded, event-driven checks for Manager itself; never a monitoring loop."""

import json
from datetime import timedelta

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import CHANNEL_MODES, CHANNELS, DOMAIN, OVERRIDE, VERSION
from .service import ENTITY_DOMAINS

REQUIRED_FILES = (
    "__init__.py",
    "manifest.json",
    "config_flow.py",
    "sensor.py",
    "binary_sensor.py",
    "update.py",
    "coordinator.py",
    "service.py",
    "manager.py",
    "health.py",
    "repairs.py",
    "backup.py",
    "files.py",
    "github.py",
    "channel.py",
    "const.py",
    "version.py",
    "releases.py",
    "catalog.py",
    "inventory.py",
    "frontend/panel.js",
    "frontend/icon.png",
    "brand/icon.png",
)


def check_files(root, pending):
    """Read only required files, off the event loop, with bounded reads."""
    for name in REQUIRED_FILES:
        with (root / DOMAIN / name).open("rb") as handle:
            data = handle.read(65537 if name == "manifest.json" else 1)
        if not data:
            raise ValueError(f"Required file is empty: {name}")
        if name == "manifest.json":
            if len(data) > 65536:
                raise ValueError("Manifest is oversized")
            manifest = json.loads(data)
            if manifest.get("domain") != DOMAIN or (
                not pending and manifest.get("version") != VERSION
            ):
                raise ValueError("Installed manifest does not match the active runtime")


class Health:
    def __init__(self, runtime):
        self.runtime = runtime
        self.checks = dict.fromkeys(
            (
                "release_discovery",
                "update_subsystem",
                "service_registry",
                "configuration",
                "filesystem",
            ),
            "unverified",
        )
        self.checks.update(installation="not_run", backup="not_requested")
        self.errors = {}
        self.warnings = {}
        self.last_check = None
        self.first_release_failure = None

    def outcome(self, check, error=None):
        self.checks[check] = "failed" if error else "ok"
        if error:
            self.errors[check] = str(error)
        else:
            self.errors.pop(check, None)
        self.runtime.notify()

    def release_result(self, error=None):
        if error is None:
            self.first_release_failure = None
            self.warnings.pop("release_discovery", None)
            self.outcome("release_discovery")
            return
        now = dt_util.utcnow()
        self.first_release_failure = self.first_release_failure or now
        if getattr(error, "transient", False) and now - self.first_release_failure < timedelta(
            hours=6
        ):
            self.checks["release_discovery"] = (
                "warning" if self.runtime.last_update_check else "unverified"
            )
            self.warnings["release_discovery"] = str(error)
            self.errors.pop("release_discovery", None)
        else:
            self.outcome("release_discovery", error)
            self.warnings.pop("release_discovery", None)

    @property
    def failed_checks(self):
        return [name for name, state in self.checks.items() if state == "failed"]

    @property
    def available(self):
        return bool(self.failed_checks) or "unverified" not in self.checks.values()

    @property
    def attributes(self):
        return {
            "checks": dict(self.checks),
            "failed_checks": self.failed_checks,
            "warnings": dict(self.warnings),
            "last_check": self.last_check,
            "last_error": "; ".join(self.errors.values()) or None,
        }

    async def async_refresh(self, *, filesystem=False):
        runtime, hass = self.runtime, self.runtime.hass
        if not runtime.ready:
            return
        try:
            if runtime.global_channel not in CHANNELS or runtime.channel_mode not in CHANNEL_MODES:
                raise ValueError("Invalid global channel or Manager mode")
            if (
                "channel" in runtime.entry.options
                and runtime.entry.options["channel"] not in CHANNELS
            ):
                raise ValueError("Invalid override channel")
            if runtime.channel_mode == OVERRIDE and "channel" not in runtime.entry.options:
                raise ValueError("Override mode requires a channel")
            if runtime.channel not in CHANNELS:
                raise ValueError("Invalid effective channel")
        except (ValueError, KeyError) as err:
            self.outcome("configuration", err)
        else:
            self.outcome("configuration")
        devices, entities = dr.async_get(hass), er.async_get(hass)
        service = devices.async_get(runtime.service_id)
        owner = getattr(service, "config_entry_id", None)
        owners = {owner} if owner else getattr(service, "config_entries", set())
        if (
            not service
            or service.entry_type != dr.DeviceEntryType.SERVICE
            or owners != {runtime.entry.entry_id}
        ):
            self.outcome("service_registry", "Service missing, wrong entry type or ownership")
        else:
            self.outcome("service_registry")
        for suffix, entity_domain in ENTITY_DOMAINS.items():
            entity_id = entities.async_get_entity_id(
                entity_domain, DOMAIN, f"{runtime.entry.entry_id}_{suffix}"
            )
            entity = entities.async_get(entity_id) if entity_id else None
            if (
                not entity
                or entity.config_entry_id != runtime.entry.entry_id
                or entity.device_id != runtime.service_id
            ):
                self.outcome("service_registry", f"Missing service association: {suffix}")
                break
        coordinator = None
        task = runtime.coordinator_task
        if task and task.done() and not task.cancelled():
            try:
                coordinator = task.result()
            except Exception:
                # A failed coordinator task is a failed check, not a crashed monitor.
                coordinator = None
        update_id = entities.async_get_entity_id(
            "update", DOMAIN, f"{runtime.entry.entry_id}_update"
        )
        update = entities.async_get(update_id) if update_id else None
        if coordinator is None or not update:
            self.outcome("update_subsystem", "Update coordinator or entity is missing")
        elif runtime.update_error:
            self.outcome("update_subsystem", runtime.update_error)
        elif update.disabled or not coordinator.last_update_success:
            self.checks["update_subsystem"] = "warning"
            self.errors.pop("update_subsystem", None)
            self.warnings["update_subsystem"] = "Update entity disabled or release data unavailable"
        elif not (state := hass.states.get(update_id)) or state.state in {"unknown", "unavailable"}:
            self.outcome("update_subsystem", "Native update entity is unavailable")
        else:
            self.outcome("update_subsystem")
            self.warnings.pop("update_subsystem", None)
        if filesystem and not runtime.lock.locked():
            try:
                await hass.async_add_executor_job(
                    check_files, runtime.store.root, DOMAIN in runtime.pending_domains
                )
            except (OSError, ValueError, TypeError) as err:
                self.outcome("filesystem", err)
            else:
                self.outcome("filesystem")
        self.last_check = dt_util.utcnow().isoformat()
        runtime.notify()
