"""Application operations shared by HA UI and the native self-update entity."""

import asyncio
from dataclasses import asdict

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, REPOSITORY, STABLE, VERSION
from .files import InstallError, validate_archive
from .inventory import overview
from .releases import is_newer, select_release


class Manager:
    def __init__(self, hass, entry, client, store):
        self.hass, self.entry, self.client, self.store = hass, entry, client, store
        self.lock = asyncio.Lock()
        self.restart_required = False
        self.pending_domains = set()
        self._catalog = []
        self.coordinator_task = None

    @property
    def channel(self):
        return self.entry.options.get("channel", STABLE)

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
        return select_release(await self.client.releases(repository), domain, self.channel)

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

    async def self_update(self, requested_version=None):
        async with self.lock:
            release = await self.release(REPOSITORY, DOMAIN)
            if release is None or not is_newer(release.version, VERSION):
                raise InstallError("No newer compatible release is available")
            if requested_version is not None and requested_version != release.version:
                raise InstallError("Requested version does not match the selected channel release")
            await self._install(REPOSITORY, DOMAIN, release, True)

    async def _install(self, repository, domain, release, replace):
        if domain in self.pending_domains:
            raise InstallError("Restart Home Assistant before changing this integration again")
        data = await self.client.download(repository, domain, release)
        files = await self.hass.async_add_executor_job(
            validate_archive, data, domain, release.version, HA_VERSION
        )
        await self._change_files(domain, self.store.install, domain, files, replace)

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
        except InstallError as err:
            if err.files_changed:
                self.pending_domains.add(domain)
                self._restart_notice()
            raise
        self.pending_domains.add(domain)
        self._restart_notice()

    def _restart_notice(self, *, configuration_removed=False):
        from homeassistant.components import persistent_notification

        self.restart_required = True
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
            return (
                "HA configuration entries removed. "
                + ("Restart Home Assistant manually to finish unloading. " if restart else "")
                + "Shared files, YAML, backups and uncertain data were not deleted. "
                "Integration removal hooks control their own data cleanup."
            )
