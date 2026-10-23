"""Application operations shared by HA UI and the native self-update entity."""

import asyncio
from dataclasses import asdict
from uuid import uuid4

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .backup import async_backup, backup_manager
from .channel import resolve_channel
from .const import DOMAIN, MANAGED, REPOSITORY, STABLE, VERSION
from .coordinator import async_get_coordinator
from .files import InstallError, validate_archive
from .github import GitHubError
from .health import Health
from .inventory import overview
from .releases import is_newer, select_release
from .repairs import async_restart_issue


class Manager:
    def __init__(self, hass, entry, client, store):
        self.hass, self.entry, self.client, self.store = hass, entry, client, store
        self.lock = asyncio.Lock()
        self.restart_required = False
        self.pending_domains = set()
        self._catalog = []
        self.coordinator_task = None
        self.ready = False
        self.service_id = None
        self.last_update_check = None
        self.state_store = None
        self.pending_manager_version = None
        self.update_in_progress = False
        self.update_phase = None
        self.update_error = None
        self.health = Health(self)
        self.health_task = None
        self.health_dirty = False
        self.health_files_pending = False

    async def async_initialize(self):
        self.boot_id = self.hass.data.setdefault(f"{DOMAIN}_boot", uuid4().hex)
        self.state_store = Store(self.hass, 1, f"{DOMAIN}.runtime")
        saved = await self.state_store.async_load() or {}
        if raw := saved.get("last_update_check"):
            parsed = dt_util.parse_datetime(raw)
            if parsed and parsed.tzinfo:
                self.last_update_check = parsed
        for check in ("installation", "backup"):
            result = saved.get("operation_results", {}).get(check, {})
            if result.get("state") in {"ok", "failed", "not_run", "not_requested"}:
                self.health.checks[check] = result["state"]
                if result["state"] == "failed":
                    self.health.errors[check] = str(
                        result.get("error") or "Previous operation failed"
                    )
        self.pending_manager_version = saved.get("pending_manager_version")
        if saved.get("boot_id") == self.boot_id:
            self.pending_domains = set(saved.get("pending_domains", []))
            self.restart_required = bool(self.pending_domains) or bool(
                saved.get("restart_required")
            )
        elif self.pending_manager_version and self.pending_manager_version != VERSION:
            self.pending_domains = {DOMAIN}
            self.restart_required = True
        else:
            self.pending_manager_version = None
        async_restart_issue(self.hass, self.restart_required)
        await self.async_save()

    async def async_save(self):
        if self.state_store is not None:
            await self.state_store.async_save(
                {
                    "last_update_check": self.last_update_check.isoformat()
                    if self.last_update_check
                    else None,
                    "boot_id": self.boot_id,
                    "pending_domains": sorted(self.pending_domains),
                    "pending_manager_version": self.pending_manager_version,
                    "restart_required": self.restart_required,
                    "operation_results": {
                        check: {
                            "state": self.health.checks[check],
                            "error": self.health.errors.get(check),
                        }
                        for check in ("installation", "backup")
                    },
                }
            )

    def notify(self):
        if hasattr(self.hass, "bus"):
            async_dispatcher_send(self.hass, f"{DOMAIN}_runtime_changed")

    def schedule_health(self, *, filesystem=False):
        if not self.ready:
            return
        self.health_dirty = True
        self.health_files_pending |= filesystem
        if self.health_task is not None and not self.health_task.done():
            return

        async def refresh():
            await asyncio.sleep(0)
            while self.ready and self.health_dirty:
                self.health_dirty = False
                check_files = self.health_files_pending
                self.health_files_pending = False
                await self.health.async_refresh(filesystem=check_files)

        self.health_task = self.hass.async_create_task(refresh())

    @property
    def backup_available(self):
        return backup_manager(self.hass) is not None

    def phase(self, phase):
        self.update_phase = phase
        self.notify()

    @property
    def channel(self):
        return resolve_channel(self.hass, self.entry.options.get("channel"), self.channel_mode)[
            "effective_channel"
        ]

    @property
    def global_channel(self):
        return self.entry.options.get("global_channel", self.entry.options.get("channel", STABLE))

    @property
    def channel_mode(self):
        return self.entry.options.get("channel_mode", MANAGED)

    async def catalog(self):
        self._catalog = await self.client.catalog()
        return self._catalog

    async def installed(self):
        # Local discovery never depends on a live network request.
        return await overview(self.hass, self.store, self._catalog)

    async def catalog_view(self):
        catalog = await self.catalog()
        installed = {i["domain"]: i for i in await self.installed()}
        return [
            {
                **asdict(e),
                "installed": installed.get(e.domain, {}).get("files_present", False),
                "version": installed.get(e.domain, {}).get("version"),
            }
            for e in catalog
        ]

    async def release(self, repository, domain):
        try:
            channel = self.channel if domain == DOMAIN else self.global_channel
            release = select_release(await self.client.releases(repository), domain, channel)
        except (GitHubError, ValueError) as err:
            if domain == DOMAIN:
                self.health.release_result(err)
                self.notify()
            raise
        if domain == DOMAIN:
            self.last_update_check = dt_util.utcnow()
            self.health.release_result()
            await self.async_save()
            self.notify()
        return release

    async def check_for_updates(self):
        """Refresh the existing native-entity coordinator on dashboard demand."""
        async with self.lock:
            coordinator = await async_get_coordinator(self.hass, self.entry, self)
            self.client.invalidate_releases(REPOSITORY)
            await coordinator.async_refresh()
            if not coordinator.last_update_success:
                raise InstallError(f"Update check failed: {coordinator.last_exception}")
            if coordinator.data and is_newer(coordinator.data.version, VERSION):
                return f"Update available: {coordinator.data.version}."
            return "Update check complete. Integration Manager is up to date."

    async def install(self, domain: str):
        if domain == DOMAIN:
            raise InstallError("Use Integration Manager's native Update entity")
        async with self.lock:
            entry = next((e for e in await self.catalog() if e.domain == domain), None)
            if entry is None:
                raise InstallError("Integration is not in the current approved catalog")
            release = await self.release(entry.repository, domain)
            if release is None:
                raise InstallError(
                    f"No compatible immutable release for {entry.name} on this channel"
                )
            await self._install(entry.repository, domain, release, False)

    async def self_update(self, requested_version=None, *, backup=False):
        async with self.lock:
            if DOMAIN in self.pending_domains:
                raise InstallError("Restart Home Assistant before changing this integration again")
            self.update_in_progress = True
            self.update_error = None
            self.phase("checking_release")
            try:
                release = await self.release(REPOSITORY, DOMAIN)
                if release is None or not is_newer(release.version, VERSION):
                    raise InstallError("No newer compatible release is available")
                if requested_version is not None and requested_version != release.version:
                    raise InstallError(
                        "Requested version does not match the selected channel release"
                    )
                await self._install(REPOSITORY, DOMAIN, release, True, backup=backup)
            except asyncio.CancelledError:
                self.phase("restart_required" if DOMAIN in self.pending_domains else "cancelled")
                raise
            except (InstallError, GitHubError, OSError, ValueError) as err:
                self.update_error = str(err)
                self.phase("failed")
                raise
            finally:
                self.update_in_progress = False
                self.notify()
                self.schedule_health(filesystem=True)

    async def _install(self, repository, domain, release, replace, *, backup=False):
        if domain in self.pending_domains:
            raise InstallError("Restart Home Assistant before changing this integration again")
        try:
            if self.update_in_progress:
                self.phase("downloading")
            data = await self.client.download(repository, domain, release)
            if self.update_in_progress:
                self.phase("validating_archive")
            files = await self.hass.async_add_executor_job(
                validate_archive, data, domain, release.version, HA_VERSION
            )
            if backup:
                self.phase("backing_up")
                try:
                    await async_backup(self.hass)
                except asyncio.CancelledError:
                    self.health.outcome(
                        "backup", "Requested backup was cancelled; no installation started"
                    )
                    raise
                except (InstallError, HomeAssistantError, OSError) as err:
                    self.health.outcome("backup", err)
                    raise
                else:
                    self.health.outcome("backup")
            if self.update_in_progress:
                self.phase("installing_files")
            if domain == DOMAIN:
                self.pending_manager_version = release.version
            await self._change_files(domain, self.store.install, domain, files, replace)
        except (InstallError, GitHubError, OSError, ValueError) as err:
            self.health.outcome("installation", err)
            if domain == DOMAIN and domain not in self.pending_domains:
                self.pending_manager_version = None
            raise
        else:
            self.health.outcome("installation")
            if self.update_in_progress:
                self.phase("restart_required")
        finally:
            await self.async_save()
            self.schedule_health(filesystem=True)

    async def _change_files(self, domain, operation, *args):
        # Executor work continues after its caller is cancelled. Keep the lock
        # until it has completed and still report the restart requirement.
        job = asyncio.ensure_future(self._run_file_change(domain, operation, *args))
        try:
            await asyncio.shield(job)
        except asyncio.CancelledError:
            await job
            raise

    async def _run_file_change(self, domain, operation, *args):
        try:
            await self.hass.async_add_executor_job(operation, *args)
        except (InstallError, OSError) as err:
            self.health.outcome("installation", err)
            self.schedule_health(filesystem=True)
            if getattr(err, "files_changed", False):
                self.pending_domains.add(domain)
                self._restart_notice()
                await self.async_save()
            raise
        self.pending_domains.add(domain)
        self._restart_notice()
        self.health.outcome("installation")
        await self.async_save()
        self.schedule_health(filesystem=True)

    def _restart_notice(self, *, configuration_removed=False):
        from homeassistant.components import persistent_notification

        self.restart_required = True
        async_restart_issue(self.hass, True)
        persistent_notification.async_create(
            self.hass,
            (
                "HA configuration entries were removed. Restart Home Assistant manually "
                "to finish unloading the integration."
                if configuration_removed
                else "Integration files changed. Restart Home Assistant manually to apply them. "
                "Configuration and data have been preserved."
            ),
            "Integration Manager: restart required",
            f"{DOMAIN}_restart",
        )

    async def remove(self, domain):
        if domain == DOMAIN:
            raise InstallError("Manager self-uninstallation is not supported")
        async with self.lock:
            if domain in self.pending_domains:
                raise InstallError("Restart Home Assistant before changing this integration again")
            await self._change_files(domain, self.store.remove, domain)

    async def cleanup(self, domain, confirmation):
        if domain == DOMAIN or confirmation != f"REMOVE {domain} CONFIGURATION":
            raise InstallError("Separate explicit configuration cleanup confirmation is required")
        async with self.lock:
            installed = {i["domain"] for i in await self.installed()}
            if domain not in installed:
                raise InstallError("Cannot establish integration ownership")
            entries = self.hass.config_entries.async_entries(domain)
            if not entries:
                raise InstallError(
                    "No integration-owned HA configuration entries; no data was deleted"
                )
            removed = 0
            restart = False
            for entry in entries:
                try:
                    result = await self.hass.config_entries.async_remove(entry.entry_id)
                    if (
                        not isinstance(result, dict)
                        or type(result.get("require_restart")) is not bool
                        or result.get("error")
                    ):
                        raise InstallError("HA did not confirm configuration entry removal")
                except (HomeAssistantError, OSError, ValueError, KeyError, InstallError) as err:
                    raise InstallError(
                        f"Configuration cleanup incomplete: {removed}/{len(entries)} entries "
                        f"confirmed removed; failed at {entry.entry_id}: {err}. "
                        "Inspect HA configuration before retrying."
                    ) from err
                removed += 1
                if result["require_restart"]:
                    restart = True
                    self._restart_notice(configuration_removed=True)
                    await self.async_save()
            await self.async_save()
            return (
                "HA configuration entries removed. "
                + ("Restart Home Assistant manually to finish unloading. " if restart else "")
                + "Shared files, YAML, backups and uncertain data were not deleted. "
                "Integration removal hooks control their own data cleanup."
            )
