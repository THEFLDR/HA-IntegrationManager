import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.integrationmanager.catalog import CatalogEntry
from custom_components.integrationmanager.const import DOMAIN, VERSION
from custom_components.integrationmanager.files import InstallError
from custom_components.integrationmanager.github import GitHubError


def prepare(runtime, archive, release_payload, domain="sample", version="0.2.0"):
    data = archive(domain, version)
    payload = release_payload(version)
    payload["assets"] = [
        {
            "name": f"{domain}.zip",
            "id": 42,
            "size": len(data),
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        }
    ]
    runtime.client.catalog.return_value = [
        CatalogEntry("Friendly Name", "THEFLDR/HA-Sample", "sample")
    ]
    runtime.client.releases.return_value = [payload]
    runtime.client.download.return_value = data
    runtime._restart_notice = Mock(side_effect=lambda: setattr(runtime, "restart_required", True))


async def test_install_success_and_restart(runtime, archive, release_payload):
    prepare(runtime, archive, release_payload)
    await runtime.install("sample")
    assert (runtime.store.root / "sample" / "manifest.json").exists()
    assert runtime.restart_required
    assert runtime.pending_domains == {"sample"}
    with pytest.raises(InstallError, match="Restart"):
        await runtime.install("sample")
    with pytest.raises(InstallError, match="Restart"):
        await runtime.remove("sample")


@pytest.mark.parametrize("domain", [DOMAIN, "unknown"])
async def test_install_forbidden_domains(runtime, domain):
    with pytest.raises(InstallError):
        await runtime.install(domain)
    runtime.client.download.assert_not_called()


async def test_no_release(runtime):
    runtime.client.catalog.return_value = [CatalogEntry("Friendly Name", "THEFLDR/Repo", "sample")]
    with pytest.raises(InstallError, match="Friendly Name"):
        await runtime.install("sample")


@pytest.mark.parametrize("operation", ["install", "self_update"])
@pytest.mark.parametrize("failure", ["download", "archive", "partial"])
async def test_install_failure_does_not_request_restart(
    runtime, archive, release_payload, operation, failure, monkeypatch
):
    domain = DOMAIN if operation == "self_update" else "sample"
    prepare(runtime, archive, release_payload, domain)
    if failure == "download":
        runtime.client.download.side_effect = GitHubError("offline")
    elif failure == "archive":
        runtime.client.download.return_value = b"bad archive"
    else:
        monkeypatch.setattr(runtime.store, "install", Mock(side_effect=InstallError("disk full")))
    with pytest.raises((InstallError, GitHubError)):
        if operation == "self_update":
            await runtime.self_update()
        else:
            await runtime.install("sample")
    assert not runtime.restart_required
    assert not runtime.pending_domains


async def test_native_self_update(runtime, archive, release_payload):
    prepare(runtime, archive, release_payload, DOMAIN)
    runtime.store.install(DOMAIN, {"__init__.py": b"old"})
    await runtime.self_update("0.2.0")
    assert (runtime.store.root / DOMAIN / "manifest.json").exists()
    assert runtime.restart_required
    assert DOMAIN in runtime.pending_domains


async def test_self_update_from_0_1_0_to_0_1_1(runtime, archive, release_payload, monkeypatch):
    import json

    monkeypatch.setattr("custom_components.integrationmanager.manager.VERSION", "0.1.0")
    prepare(runtime, archive, release_payload, DOMAIN, "0.1.1")
    runtime.store.install(DOMAIN, {"__init__.py": b"old"})
    await runtime.self_update("0.1.1")
    manifest = json.loads((runtime.store.root / DOMAIN / "manifest.json").read_text())
    assert manifest["version"] == "0.1.1"
    assert runtime.restart_required and DOMAIN in runtime.pending_domains


async def test_self_update_rejects_prefixed_requested_version(runtime, archive, release_payload):
    prepare(runtime, archive, release_payload, DOMAIN)
    with pytest.raises(InstallError, match="Requested version"):
        await runtime.self_update("v0.2.0")
    runtime.client.download.assert_not_called()


@pytest.mark.parametrize("mode", ["managed", "default", "override"])
@pytest.mark.parametrize("global_channel", ["stable", "release_candidate"])
@pytest.mark.parametrize("override", ["stable", "release_candidate"])
async def test_self_update_uses_effective_channel(
    runtime, archive, release_payload, mode, global_channel, override
):
    import json

    runtime.entry.options = {
        "channel_mode": mode,
        "global_channel": global_channel,
        "channel": override,
    }
    prepare(runtime, archive, release_payload, DOMAIN, "0.2.0")
    stable = runtime.client.releases.return_value[0]
    prepare(runtime, archive, release_payload, DOMAIN, "0.3.0")
    candidate = runtime.client.releases.return_value[0]
    candidate["prerelease"] = True
    runtime.client.releases.return_value = [stable, candidate]
    effective = global_channel if mode == "managed" else "stable" if mode == "default" else override
    selected = "0.3.0" if effective == "release_candidate" else "0.2.0"
    runtime.client.download.return_value = archive(DOMAIN, selected)
    await runtime.self_update(selected)
    assert runtime.client.download.call_args.args[2].version == selected
    assert (
        json.loads((runtime.store.root / DOMAIN / "manifest.json").read_text())["version"]
        == selected
    )


@pytest.mark.parametrize("outcome", ["current", "available", "failure"])
async def test_dashboard_check_reuses_coordinator(hass, runtime, archive, release_payload, outcome):
    from datetime import timedelta

    from custom_components.integrationmanager.coordinator import async_get_coordinator

    runtime.hass = hass
    hass.data[DOMAIN] = runtime
    runtime.entry.async_on_unload = Mock()
    coordinator = await async_get_coordinator(hass, runtime.entry, runtime)
    assert coordinator.update_interval == timedelta(hours=6)
    initial_calls = runtime.client.releases.call_count
    if outcome == "available":
        prepare(runtime, archive, release_payload, DOMAIN)
    elif outcome == "failure":
        runtime.client.releases.side_effect = GitHubError("offline")
    if outcome == "failure":
        with pytest.raises(InstallError, match="Update check failed.*offline"):
            await runtime.check_for_updates()
        assert not coordinator.last_update_success
    else:
        message = await runtime.check_for_updates()
        assert ("Update available: 0.2.0" if outcome == "available" else "up to date") in message
        assert coordinator.last_update_success
    assert await async_get_coordinator(hass, runtime.entry, runtime) is coordinator
    assert coordinator.update_interval == timedelta(hours=6)
    assert runtime.client.releases.call_count == initial_calls + 1
    runtime.client.invalidate_releases.assert_called_once()


async def test_catalog_installation_uses_global_channel(runtime, release_payload):
    runtime.entry.options = {
        "global_channel": "release_candidate",
        "channel_mode": "override",
        "channel": "stable",
    }
    runtime.client.releases.return_value = [release_payload("0.3.0", prerelease=True)]
    assert (await runtime.release("THEFLDR/HA-Sample", "sample")).version == "0.3.0"


async def test_no_update_and_unmatched_request(runtime, archive, release_payload):
    with pytest.raises(InstallError, match="No newer"):
        await runtime.self_update()
    prepare(runtime, archive, release_payload, DOMAIN, VERSION)
    with pytest.raises(InstallError, match="No newer"):
        await runtime.self_update()
    prepare(runtime, archive, release_payload, DOMAIN)
    with pytest.raises(InstallError, match="Requested version"):
        await runtime.self_update("9.0.0")


async def test_remove_preserves_configuration(runtime):
    runtime.store.install("sample", {"__init__.py": b"integration"})
    runtime._restart_notice = Mock()
    await runtime.remove("sample")
    assert not (runtime.store.root / "sample").exists()
    runtime.hass.config_entries.async_remove.assert_not_called()
    runtime._restart_notice.assert_called_once()
    with pytest.raises(InstallError, match="self-uninstallation"):
        await runtime.remove(DOMAIN)


async def test_separate_cleanup(runtime):
    runtime.installed = AsyncMock(return_value=[{"domain": "sample"}])
    runtime.hass.config_entries.async_entries.return_value = [SimpleNamespace(entry_id="owned")]
    with pytest.raises(InstallError, match="confirmation"):
        await runtime.cleanup("sample", "yes")
    with pytest.raises(InstallError):
        await runtime.cleanup(DOMAIN, f"REMOVE {DOMAIN} CONFIGURATION")
    with pytest.raises(InstallError, match="ownership"):
        await runtime.cleanup("unknown", "REMOVE unknown CONFIGURATION")
    message = await runtime.cleanup("sample", "REMOVE sample CONFIGURATION")
    runtime.hass.config_entries.async_remove.assert_awaited_once_with("owned")
    assert "uncertain" in message


async def test_cleanup_no_entries_and_restart(runtime):
    runtime.installed = AsyncMock(return_value=[{"domain": "sample"}])
    with pytest.raises(InstallError, match="No integration-owned"):
        await runtime.cleanup("sample", "REMOVE sample CONFIGURATION")
    runtime.hass.config_entries.async_entries.return_value = [SimpleNamespace(entry_id="owned")]
    runtime.hass.config_entries.async_remove.return_value = {"require_restart": True}
    runtime._restart_notice = Mock()
    message = await runtime.cleanup("sample", "REMOVE sample CONFIGURATION")
    runtime._restart_notice.assert_called_once_with(configuration_removed=True)
    assert "Restart Home Assistant" in message


@pytest.mark.parametrize("result", [None, False, {}, {"error": "failed", "require_restart": False}])
async def test_cleanup_does_not_claim_success_without_ha_confirmation(runtime, result):
    runtime.installed = AsyncMock(return_value=[{"domain": "sample"}])
    runtime.hass.config_entries.async_entries.return_value = [SimpleNamespace(entry_id="owned")]
    runtime.hass.config_entries.async_remove.return_value = result
    with pytest.raises(InstallError, match="cleanup incomplete: 0/1.*owned"):
        await runtime.cleanup("sample", "REMOVE sample CONFIGURATION")


async def test_partial_cleanup_reports_progress_and_preserves_restart(runtime):
    runtime.installed = AsyncMock(return_value=[{"domain": "sample"}])
    runtime.hass.config_entries.async_entries.return_value = [
        SimpleNamespace(entry_id="first"),
        SimpleNamespace(entry_id="second"),
    ]
    runtime.hass.config_entries.async_remove.side_effect = [
        {"require_restart": True},
        OSError("removal hook failed"),
    ]
    runtime._restart_notice = Mock(
        side_effect=lambda **kw: setattr(runtime, "restart_required", True)
    )
    with pytest.raises(InstallError, match="cleanup incomplete: 1/2.*second.*removal hook failed"):
        await runtime.cleanup("sample", "REMOVE sample CONFIGURATION")
    assert runtime.restart_required
    assert not runtime.lock.locked()


@pytest.mark.parametrize("changed", [False, True])
async def test_failed_file_change_reports_failure_and_tracks_changed_files(runtime, changed):
    runtime.store.remove = Mock(side_effect=InstallError("inspect staging", files_changed=changed))
    runtime._restart_notice = Mock(side_effect=lambda: setattr(runtime, "restart_required", True))
    with pytest.raises(InstallError, match="inspect staging"):
        await runtime.remove("sample")
    assert runtime.restart_required is changed
    assert runtime.pending_domains == ({"sample"} if changed else set())
    assert not runtime.lock.locked()


async def test_cleanup_notification_does_not_claim_configuration_preserved(
    hass, runtime, monkeypatch
):
    runtime.hass = hass
    notify = Mock()
    monkeypatch.setattr("homeassistant.components.persistent_notification.async_create", notify)
    runtime._restart_notice(configuration_removed=True)
    assert runtime.restart_required
    assert "configuration entries were removed" in notify.call_args.args[1]
    assert "preserved" not in notify.call_args.args[1]


async def test_dashboard_survives_catalog_failure(runtime, monkeypatch):
    runtime.client.catalog.side_effect = GitHubError("offline")
    overview = AsyncMock(return_value=[{"name": "Unlisted Integration"}])
    monkeypatch.setattr("custom_components.integrationmanager.manager.overview", overview)
    assert (await runtime.installed())[0]["name"] == "Unlisted Integration"
    assert overview.call_args.args[-1] == []


async def test_catalog_view_uses_names_and_status(runtime):
    runtime.client.catalog.return_value = [
        CatalogEntry("Friendly Name", "THEFLDR/HA-Sample", "sample")
    ]
    runtime.installed = AsyncMock(
        return_value=[{"domain": "sample", "files_present": True, "version": "1.2.3"}]
    )
    assert await runtime.catalog_view() == [
        {
            "name": "Friendly Name",
            "repository": "THEFLDR/HA-Sample",
            "domain": "sample",
            "installed": True,
            "version": "1.2.3",
        }
    ]


async def test_cancellation_keeps_transaction_locked(runtime):
    started, finish = asyncio.Event(), asyncio.Event()
    runtime.store.install("sample", {"__init__.py": b"old"})
    runtime._restart_notice = Mock(side_effect=lambda: setattr(runtime, "restart_required", True))

    async def executor(operation, *args):
        started.set()
        await finish.wait()
        return operation(*args)

    runtime.hass.async_add_executor_job = executor
    task = asyncio.create_task(runtime.remove("sample"))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert runtime.lock.locked()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (runtime.store.root / "sample").exists()
    assert runtime.restart_required
    assert runtime.pending_domains == {"sample"}
    assert not runtime.lock.locked()
