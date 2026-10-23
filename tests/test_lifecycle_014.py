"""Native registry, update lifecycle, persistence, Repairs and health regressions."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from homeassistant.components import repairs as ha_repairs
from homeassistant.components.backup import CreateBackupEvent, CreateBackupState
from homeassistant.components.update import UpdateEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.integrationmanager import backup, repairs
from custom_components.integrationmanager.const import DOMAIN, VERSION
from custom_components.integrationmanager.files import InstallError
from custom_components.integrationmanager.github import GitHubError
from custom_components.integrationmanager.health import REQUIRED_FILES, check_files
from custom_components.integrationmanager.manager import Manager
from custom_components.integrationmanager.sensor import LastUpdateCheckSensor
from custom_components.integrationmanager.service import (
    ENTITY_DOMAINS,
    async_migrate_entity_ids,
    async_register_service,
)
from custom_components.integrationmanager.update import ManagerUpdate
from tests.test_ha import add_entry, entry
from tests.test_manager import prepare


@pytest_asyncio.fixture
async def monitored(hass, runtime, monkeypatch):
    current = entry()
    await add_entry(hass, current, monkeypatch)
    runtime.hass, runtime.entry = hass, current
    hass.data[DOMAIN] = runtime
    runtime.service_id = async_register_service(hass, current).id
    await runtime.async_initialize()
    registry = er.async_get(hass)
    for suffix, entity_domain in ENTITY_DOMAINS.items():
        entity = registry.async_get_or_create(
            entity_domain,
            DOMAIN,
            f"{current.entry_id}_{suffix}",
            config_entry=current,
            device_id=runtime.service_id,
            suggested_object_id=f"{DOMAIN}_{suffix}",
        )
        hass.states.async_set(
            entity.entity_id, "off" if suffix in {"health", "update"} else "Stable"
        )
    runtime.coordinator_task = asyncio.create_task(
        AsyncMock(return_value=SimpleNamespace(last_update_success=True))()
    )
    files = dict.fromkeys(REQUIRED_FILES, b"content")
    files["manifest.json"] = json.dumps({"domain": DOMAIN, "version": VERSION}).encode()
    runtime.store.install(DOMAIN, files)
    await runtime.release("THEFLDR/HA-IntegrationManager", DOMAIN)
    runtime.ready = True
    await runtime.health.async_refresh(filesystem=True)
    yield runtime
    runtime.ready = False
    if runtime.health_task:
        await runtime.health_task


@pytest.mark.parametrize("case", ["default", "custom", "collision", "live_collision"])
async def test_owned_default_id_migration_preserves_registry(hass, monkeypatch, case):
    current = entry()
    await add_entry(hass, current, monkeypatch)
    service = async_register_service(hass, current)
    registry = er.async_get(hass)
    for suffix, entity_domain in ENTITY_DOMAINS.items():
        old = f"owner_{suffix}" if case == "custom" else f"integration_manager_{suffix}"
        entity = registry.async_get_or_create(
            entity_domain,
            DOMAIN,
            f"{current.entry_id}_{suffix}",
            config_entry=current,
            device_id=service.id,
            suggested_object_id=old,
            disabled_by=er.RegistryEntryDisabler.USER,
        )
        registry.async_update_entity(
            entity.entity_id, name=f"Custom {suffix}", hidden_by=er.RegistryEntryHider.USER
        )
        before = registry.async_get(entity.entity_id)
        canonical = f"{entity_domain}.{DOMAIN}_{suffix}"
        if case == "collision":
            registry.async_get_or_create(
                entity_domain, "other", f"other_{suffix}", suggested_object_id=f"{DOMAIN}_{suffix}"
            )
        if case == "live_collision":
            hass.states.async_set(canonical, "on")
        warnings = async_migrate_entity_ids(hass, current)
        actual_id = registry.async_get_entity_id(entity_domain, DOMAIN, before.unique_id)
        assert actual_id == (canonical if case == "default" else before.entity_id)
        after = registry.async_get(actual_id)
        for field in (
            "id",
            "unique_id",
            "config_entry_id",
            "device_id",
            "name",
            "disabled_by",
            "hidden_by",
        ):
            assert getattr(after, field) == getattr(before, field)
        assert bool(warnings) == (case in {"collision", "live_collision"})
    ids = {e.entity_id for e in er.async_entries_for_config_entry(registry, current.entry_id)}
    async_migrate_entity_ids(hass, current)
    assert ids == {
        e.entity_id for e in er.async_entries_for_config_entry(registry, current.entry_id)
    }


async def test_manufacturer_explicitly_cleared(hass, monkeypatch):
    current = entry()
    await add_entry(hass, current, monkeypatch)
    devices = dr.async_get(hass)
    old = devices.async_get_or_create(
        config_entry_id=current.entry_id,
        identifiers={(DOMAIN, current.entry_id)},
        manufacturer="THEFLDR",
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    devices.async_update_device(old.id, name_by_user="My Manager")
    service = async_register_service(hass, current)
    assert service.id == old.id and service.manufacturer is None
    assert service.name_by_user == "My Manager"
    assert async_register_service(hass, current).manufacturer is None


async def test_last_success_timestamp_persists_across_reload_and_failure(monitored):
    runtime = monitored
    sensor = LastUpdateCheckSensor(runtime)
    original = sensor.native_value
    assert original.tzinfo is not None
    runtime.client.releases.side_effect = GitHubError("offline", transient=True)
    with pytest.raises(GitHubError):
        await runtime.release("THEFLDR/HA-IntegrationManager", DOMAIN)
    assert sensor.native_value == original
    reloaded = Manager(runtime.hass, runtime.entry, runtime.client, runtime.store)
    await reloaded.async_initialize()
    assert reloaded.last_update_check == original
    assert LastUpdateCheckSensor(reloaded).unique_id == sensor.unique_id
    runtime.client.releases.side_effect = None
    await runtime.release("THEFLDR/HA-IntegrationManager", DOMAIN)
    assert sensor.native_value >= original


async def test_pending_restart_survives_reload_and_clears_only_on_new_runtime(monitored):
    runtime = monitored
    runtime.pending_domains.add("sample")
    runtime._restart_notice()
    await runtime.async_save()
    assert ir.async_get(runtime.hass).async_get_issue(DOMAIN, repairs.ISSUE_ID).is_fixable
    reloaded = Manager(runtime.hass, runtime.entry, runtime.client, runtime.store)
    await reloaded.async_initialize()
    assert reloaded.restart_required and reloaded.pending_domains == {"sample"}
    runtime.hass.data.pop(f"{DOMAIN}_boot")
    restarted = Manager(runtime.hass, runtime.entry, runtime.client, runtime.store)
    await restarted.async_initialize()
    assert not restarted.restart_required
    assert ir.async_get(runtime.hass).async_get_issue(DOMAIN, repairs.ISSUE_ID) is None


async def test_self_update_issue_waits_for_expected_runtime(monitored, monkeypatch):
    runtime = monitored
    runtime.pending_manager_version = "0.2.0"
    runtime.pending_domains.add(DOMAIN)
    runtime._restart_notice()
    await runtime.async_save()
    runtime.hass.data.pop(f"{DOMAIN}_boot")
    old_runtime = Manager(runtime.hass, runtime.entry, runtime.client, runtime.store)
    await old_runtime.async_initialize()
    assert old_runtime.restart_required
    runtime.hass.data.pop(f"{DOMAIN}_boot")
    monkeypatch.setattr("custom_components.integrationmanager.manager.VERSION", "0.2.0")
    new_runtime = Manager(runtime.hass, runtime.entry, runtime.client, runtime.store)
    await new_runtime.async_initialize()
    assert not new_runtime.restart_required


async def test_health_operation_results_recover_from_storage(monitored):
    monitored.health.outcome("backup", "Requested backup failed")
    monitored.health.outcome("installation", "Installation blocked by backup")
    await monitored.async_save()
    reloaded = Manager(monitored.hass, monitored.entry, monitored.client, monitored.store)
    await reloaded.async_initialize()
    assert reloaded.health.checks["backup"] == "failed"
    assert reloaded.health.errors["installation"] == "Installation blocked by backup"
    reloaded.health.outcome("backup")
    reloaded.health.outcome("installation")
    await reloaded.async_save()
    recovered = Manager(monitored.hass, monitored.entry, monitored.client, monitored.store)
    await recovered.async_initialize()
    assert recovered.health.checks["backup"] == "ok"
    assert recovered.health.checks["installation"] == "ok"


@pytest.mark.parametrize("failure", [False, True, "locked"])
async def test_native_repair_confirmation_preserves_issue_until_restart(
    monitored, monkeypatch, failure
):
    runtime, hass = monitored, monitored.hass
    hass.http = SimpleNamespace(register_view=Mock())
    await ha_repairs.async_setup(hass, {})
    runtime.pending_domains.add("sample")
    runtime._restart_notice()
    restart = AsyncMock(
        side_effect=HomeAssistantError("restart unavailable") if failure is True else None
    )
    hass.services.async_register("homeassistant", "restart", restart)
    platforms = hass.data["repairs"].get("platforms")
    if platforms is None or isinstance(platforms, dict):
        hass.data["repairs"]["platforms"] = {DOMAIN: repairs}
    else:
        monkeypatch.setattr(platforms, "async_get_platform", AsyncMock(return_value=repairs))
    manager = ha_repairs.repairs_flow_manager(hass)
    form = await manager.async_init(
        DOMAIN, context={"issue_id": repairs.ISSUE_ID}, data={"issue_id": repairs.ISSUE_ID}
    )
    assert form["step_id"] == "confirm_restart"
    restart.assert_not_called()
    if failure == "locked":
        await runtime.lock.acquire()
    result = await manager.async_configure(form["flow_id"], {})
    if failure:
        assert result["type"] == "form"
        assert result["errors"]["base"] == (
            "operation_in_progress" if failure == "locked" else "restart_failed"
        )
    else:
        assert result["type"] == "abort" and result["reason"] == "restart_requested"
        restart.assert_awaited_once()
    assert ir.async_get(hass).async_get_issue(DOMAIN, repairs.ISSUE_ID)
    if runtime.lock.locked():
        runtime.lock.release()


@pytest.mark.parametrize(
    "outcome", ["success", "failed_event", "exception", "unconfirmed", "unsupported"]
)
async def test_optional_backup_blocks_files_until_confirmed(
    runtime, archive, release_payload, monkeypatch, outcome
):
    prepare(runtime, archive, release_payload, DOMAIN)
    runtime.store.install(DOMAIN, {"__init__.py": b"old"})
    original = (runtime.store.root / DOMAIN / "__init__.py").read_bytes()
    subscribers = []

    def subscribe(callback):
        subscribers.append(callback)
        return lambda: subscribers.remove(callback)

    async def create(**kwargs):
        assert (runtime.store.root / DOMAIN / "__init__.py").read_bytes() == original
        assert runtime.update_in_progress and runtime.update_phase == "backing_up"
        assert kwargs["include_homeassistant"] and kwargs["include_database"]
        if outcome == "exception":
            raise HomeAssistantError("disk full")
        if outcome != "unconfirmed":
            state = (
                CreateBackupState.COMPLETED if outcome == "success" else CreateBackupState.FAILED
            )
            subscribers[0](CreateBackupEvent(state=state, reason=None, stage=None))

    manager = SimpleNamespace(
        local_backup_agents={"local": Mock()},
        async_subscribe_events=subscribe,
        async_create_backup=AsyncMock(side_effect=create),
    )
    monkeypatch.setattr(
        backup,
        "async_get_manager",
        Mock(
            return_value=manager,
            side_effect=HomeAssistantError("unsupported") if outcome == "unsupported" else None,
        ),
    )
    entity = ManagerUpdate(SimpleNamespace(), runtime)
    entity.async_write_ha_state = Mock()
    assert (UpdateEntityFeature.BACKUP in entity.supported_features) == (outcome != "unsupported")
    assert UpdateEntityFeature.PROGRESS in entity.supported_features
    if outcome == "success":
        await entity.async_install(None, True)
        assert runtime.restart_required and runtime.health.checks["backup"] == "ok"
    else:
        with pytest.raises(HomeAssistantError):
            await entity.async_install(None, True)
        assert (runtime.store.root / DOMAIN / "__init__.py").read_bytes() == original
        assert not runtime.restart_required and runtime.health.checks["backup"] == "failed"
    assert not entity.in_progress and entity.update_percentage is None and not subscribers


async def test_backup_default_off_and_phase_progress_is_honest(
    runtime, archive, release_payload, monkeypatch
):
    prepare(runtime, archive, release_payload, DOMAIN)
    phases = []
    runtime.notify = lambda: phases.append((runtime.update_phase, runtime.update_in_progress))
    backup_call = AsyncMock()
    monkeypatch.setattr("custom_components.integrationmanager.manager.async_backup", backup_call)
    await runtime.self_update()
    backup_call.assert_not_called()
    assert runtime.health.checks["backup"] == "not_requested"
    assert {phase for phase, busy in phases if busy} >= {
        "checking_release",
        "downloading",
        "validating_archive",
        "installing_files",
    }
    assert phases[-1] == ("restart_required", False)


async def test_update_cancellation_waits_for_executor_and_retains_restart(
    runtime, archive, release_payload
):
    prepare(runtime, archive, release_payload, DOMAIN)
    started, finish = asyncio.Event(), asyncio.Event()
    original_executor = runtime.hass.async_add_executor_job

    async def executor(operation, *args):
        if operation == runtime.store.install:
            started.set()
            await finish.wait()
        return await original_executor(operation, *args)

    runtime.hass.async_add_executor_job = executor
    task = asyncio.create_task(runtime.self_update())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert runtime.lock.locked() and runtime.update_in_progress
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.restart_required and not runtime.update_in_progress
    assert runtime.update_phase == "restart_required"


async def test_backup_cancellation_never_changes_files(
    runtime, archive, release_payload, monkeypatch
):
    prepare(runtime, archive, release_payload, DOMAIN)
    runtime.store.install(DOMAIN, {"__init__.py": b"old"})
    started = asyncio.Event()

    async def waiting_backup(hass):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("custom_components.integrationmanager.manager.async_backup", waiting_backup)
    task = asyncio.create_task(runtime.self_update(backup=True))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not runtime.update_in_progress and runtime.update_phase == "cancelled"
    assert runtime.health.checks["backup"] == "failed"
    assert (runtime.store.root / DOMAIN / "__init__.py").read_bytes() == b"old"
    assert not runtime.restart_required and not runtime.lock.locked()


async def test_native_update_rollback_error_retains_progress_and_restart_guard(
    runtime, archive, release_payload, monkeypatch
):
    prepare(runtime, archive, release_payload, DOMAIN)
    monkeypatch.setattr(
        runtime.store,
        "install",
        Mock(side_effect=InstallError("rollback failed", files_changed=True)),
    )
    entity = ManagerUpdate(SimpleNamespace(), runtime)
    with pytest.raises(HomeAssistantError, match="rollback failed"):
        await entity.async_install(None, False)
    assert not entity.in_progress and entity.update_percentage is None
    assert runtime.health.checks["installation"] == "failed"
    assert runtime.restart_required and runtime.pending_domains == {DOMAIN}
    assert entity.extra_state_attributes["last_error"] == "rollback failed"


async def test_health_handles_cancelled_coordinator_task(monitored):
    task = asyncio.create_task(asyncio.sleep(10))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    monitored.coordinator_task = task
    await monitored.health.async_refresh()
    assert "update_subsystem" in monitored.health.failed_checks


@pytest.mark.parametrize("check", ["installation", "backup", "update_subsystem"])
async def test_health_operation_failures_and_recovery(monitored, check):
    health = monitored.health
    assert health.available and not health.failed_checks
    health.outcome(check, "operation failed; rollback completed")
    assert check in health.failed_checks and "rollback" in health.attributes["last_error"]
    health.outcome(check)
    assert not health.failed_checks
    assert health.attributes["last_error"] is None


async def test_health_connectivity_warning_persistent_failure_and_recovery(monitored):
    runtime, health = monitored, monitored.health
    health.release_result(GitHubError("offline", transient=True))
    assert not health.failed_checks and health.warnings["release_discovery"] == "offline"
    health.first_release_failure -= timedelta(hours=6)
    health.release_result(GitHubError("still offline", transient=True))
    assert "release_discovery" in health.failed_checks
    health.release_result()
    assert not health.failed_checks and not health.warnings
    runtime.last_update_check = None
    health.release_result(GitHubError("startup offline", transient=True))
    assert not health.available
    health.release_result(GitHubError("invalid artifact"))
    assert health.available and "release_discovery" in health.failed_checks


@pytest.mark.parametrize(
    "fault", ["configuration", "service", "association", "filesystem", "update"]
)
async def test_health_native_checks_and_recovery(monitored, fault):
    runtime, health, hass = monitored, monitored.health, monitored.hass
    registry, devices = er.async_get(hass), dr.async_get(hass)
    config = dict(runtime.entry.options)
    if fault == "configuration":
        hass.config_entries.async_update_entry(runtime.entry, options={"channel_mode": "invalid"})
        check = "configuration"
    elif fault == "service":
        devices.async_update_device(runtime.service_id, entry_type=None)
        check = "service_registry"
    elif fault == "association":
        registry.async_update_entity(f"sensor.{DOMAIN}_last_update_check", device_id=None)
        check = "service_registry"
    elif fault == "filesystem":
        (runtime.store.root / DOMAIN / "frontend/panel.js").unlink()
        check = "filesystem"
    else:
        hass.states.async_set(f"update.{DOMAIN}_update", "unavailable")
        check = "update_subsystem"
    await health.async_refresh(filesystem=True)
    assert check in health.failed_checks and health.attributes["last_check"]
    hass.config_entries.async_update_entry(runtime.entry, options=config)
    devices.async_update_device(runtime.service_id, entry_type=dr.DeviceEntryType.SERVICE)
    registry.async_update_entity(f"sensor.{DOMAIN}_last_update_check", device_id=runtime.service_id)
    (runtime.store.root / DOMAIN / "frontend/panel.js").write_text("panel")
    hass.states.async_set(f"update.{DOMAIN}_update", "off")
    await health.async_refresh(filesystem=True)
    assert not health.failed_checks
    runtime.restart_required = True
    assert health.available and not health.failed_checks


async def test_filesystem_bounded_identity_check(monitored):
    manifest = monitored.store.root / DOMAIN / "manifest.json"
    manifest.write_text("x" * 65537)
    with pytest.raises(ValueError, match="oversized"):
        check_files(monitored.store.root, False)
    manifest.write_text(json.dumps({"domain": DOMAIN, "version": "0.2.0"}))
    with pytest.raises(ValueError, match="active runtime"):
        check_files(monitored.store.root, False)
    check_files(monitored.store.root, True)
