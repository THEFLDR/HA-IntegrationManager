"""Durable native self-update outcomes, including real HA Store reloads."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from homeassistant.exceptions import HomeAssistantError

from custom_components.integrationmanager.const import DOMAIN, VERSION
from custom_components.integrationmanager.files import InstallError
from custom_components.integrationmanager.github import GitHubError
from custom_components.integrationmanager.manager import Manager
from custom_components.integrationmanager.update import ManagerUpdate
from tests.test_manager import prepare


@pytest_asyncio.fixture
async def persisted(hass, runtime, archive, release_payload):
    runtime.hass = hass
    hass.data[DOMAIN] = runtime
    await runtime.async_initialize()
    prepare(runtime, archive, release_payload, DOMAIN)
    return runtime


async def reload(runtime, *, restart=False):
    if restart:
        runtime.hass.data.pop(f"{DOMAIN}_boot")
    result = Manager(runtime.hass, runtime.entry, runtime.client, runtime.store)
    await result.async_initialize()
    return result


async def test_native_update_phases_and_persistent_restart_result(persisted):
    runtime = persisted
    phases = []
    runtime.notify = lambda: phases.append(dict(runtime.self_update_status))
    entity = ManagerUpdate(SimpleNamespace(), runtime)
    entity.async_write_ha_state = Mock()
    await entity.async_install("0.2.0", False)
    active = [state["phase"] for state in phases if state["in_progress"]]
    assert list(dict.fromkeys(active)) == [
        "checking_release",
        "downloading",
        "validating_archive",
        "installing_files",
        "restart_required",
    ]
    assert not entity.in_progress and entity.update_percentage is None
    result = entity.extra_state_attributes["update_status"]
    assert result == {
        "phase": "restart_required",
        "step": "installing_files",
        "target_version": "0.2.0",
        "running_version": VERSION,
        "backup_requested": False,
        "files_installed": True,
        "error": None,
        "in_progress": False,
        "restart_required": True,
    }
    restored = await reload(runtime)
    assert restored.self_update_status == result
    assert "in_progress" not in (await restored.state_store.async_load())["update_status"]


async def test_completed_after_matching_restart_is_durable(persisted, monkeypatch):
    await persisted.self_update()
    monkeypatch.setattr("custom_components.integrationmanager.manager.VERSION", "0.2.0")
    running = await reload(persisted, restart=True)
    assert running.self_update_status["phase"] == "completed"
    assert running.self_update_status["running_version"] == "0.2.0"
    assert running.update_target_version == "0.2.0"
    assert not running.restart_required and not running.pending_manager_version
    assert not running.update_in_progress and running.update_error is None
    assert (await reload(running, restart=True)).update_phase == "completed"


async def test_restart_with_old_runtime_warns_instead_of_claiming_success(persisted):
    await persisted.self_update()
    mismatch = await reload(persisted, restart=True)
    assert mismatch.restart_required and DOMAIN in mismatch.pending_domains
    assert mismatch.update_phase == "restart_mismatch"
    assert f"running {VERSION}" in mismatch.update_error
    assert "0.2.0" in mismatch.update_error
    assert not mismatch.update_in_progress


@pytest.mark.parametrize(
    "failure", ["download", "archive", "backup", "files", "partial", "unexpected"]
)
async def test_failure_is_visible_after_reload_without_false_install_success(
    persisted, failure, monkeypatch
):
    runtime = persisted
    if failure == "download":
        runtime.client.download.side_effect = GitHubError("Download offline")
    elif failure == "archive":
        runtime.client.download.return_value = b"Invalid archive"
    elif failure == "backup":
        monkeypatch.setattr(
            "custom_components.integrationmanager.manager.async_backup",
            AsyncMock(side_effect=InstallError("Backup failed")),
        )
    elif failure == "unexpected":
        runtime.client.download.side_effect = RuntimeError("Unexpected download failure")
    else:
        monkeypatch.setattr(
            runtime.store,
            "install",
            Mock(
                side_effect=InstallError(
                    "File replacement failed", files_changed=failure == "partial"
                )
            ),
        )
    with pytest.raises((InstallError, HomeAssistantError, GitHubError, RuntimeError)):
        await runtime.self_update(backup=failure == "backup")
    assert runtime.update_phase == "failed" and runtime.update_error
    assert not runtime.update_in_progress and not runtime.update_files_installed
    assert (DOMAIN in runtime.pending_domains) == (failure == "partial")
    restored = await reload(runtime)
    assert restored.self_update_status == runtime.self_update_status
    if failure == "partial":
        restarted = await reload(runtime, restart=True)
        assert restarted.update_phase == "restart_mismatch"
        assert "File replacement failed" in restarted.update_error
        assert restarted.restart_required
    else:
        assert not restored.pending_manager_version


async def test_cancelled_backup_stays_cancelled_after_restart(persisted, monkeypatch):
    started = asyncio.Event()

    async def backup(hass):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("custom_components.integrationmanager.manager.async_backup", backup)
    task = asyncio.create_task(persisted.self_update(backup=True))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    cancelled = await reload(persisted, restart=True)
    assert cancelled.update_phase == "cancelled" and "cancelled" in cancelled.update_error
    assert not cancelled.update_files_installed and not cancelled.restart_required
    assert not cancelled.update_in_progress


async def test_cancellation_waits_for_files_and_persists_actual_outcome(persisted):
    started, finish = asyncio.Event(), asyncio.Event()
    executor = persisted.hass.async_add_executor_job

    async def blocked(operation, *args):
        if operation == persisted.store.install:
            started.set()
            await finish.wait()
        return await executor(operation, *args)

    persisted.hass.async_add_executor_job = blocked
    task = asyncio.create_task(persisted.self_update())
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert persisted.lock.locked() and persisted.update_in_progress
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    result = await reload(persisted)
    assert result.update_files_installed and result.restart_required
    assert result.update_phase == "restart_required" and "cancelled" in result.update_error
    assert not result.update_in_progress


async def test_backup_stage_is_only_added_when_requested(persisted, monkeypatch):
    stages = []
    persisted.notify = lambda: stages.append(persisted.update_phase)
    backup = AsyncMock()
    monkeypatch.setattr("custom_components.integrationmanager.manager.async_backup", backup)
    await persisted.self_update(backup=True)
    backup.assert_awaited_once()
    assert "backing_up" in stages
    assert persisted.self_update_status["backup_requested"] is True


async def test_interrupted_operation_warns_on_new_boot(persisted):
    persisted.update_in_progress = True
    await persisted.async_phase("downloading")
    restarted = await reload(persisted, restart=True)
    assert restarted.update_phase == "interrupted"
    assert not restarted.update_in_progress and not restarted.update_files_installed
    assert "interrupted" in restarted.update_error


async def test_fresh_install_and_other_integration_restart_are_not_self_update(persisted):
    result = persisted.self_update_status
    assert result["phase"] is None and result["target_version"] is None
    persisted.pending_domains.add("other")
    persisted.restart_required = True
    await persisted.async_save()
    restored = await reload(persisted)
    assert restored.restart_required
    assert restored.self_update_status["phase"] is None
    assert not restored.self_update_status["restart_required"]


@pytest.mark.parametrize("failed", [False, True])
async def test_legacy_pending_status_migrates_without_overwriting_settings(persisted, failed):
    saved = await persisted.state_store.async_load()
    saved.pop("update_status")
    saved.update(pending_manager_version="0.2.0", pending_domains=[DOMAIN], restart_required=True)
    saved["operation_results"]["installation"] = {
        "state": "failed" if failed else "ok",
        "error": "Previous install failed" if failed else None,
    }
    await persisted.state_store.async_save(saved)
    restored = await reload(persisted)
    assert restored.update_target_version == "0.2.0"
    assert restored.update_phase == ("failed" if failed else "restart_required")
    assert restored.update_files_installed is not failed
    assert restored.entry.options == persisted.entry.options


async def test_partial_failure_never_becomes_success_just_because_version_matches(
    persisted, monkeypatch
):
    monkeypatch.setattr(
        persisted.store,
        "install",
        Mock(side_effect=InstallError("Rollback failed", files_changed=True)),
    )
    with pytest.raises(InstallError):
        await persisted.self_update()
    monkeypatch.setattr("custom_components.integrationmanager.manager.VERSION", "0.2.0")
    restarted = await reload(persisted, restart=True)
    assert restarted.update_phase == "failed"
    assert restarted.update_error == "Rollback failed"
    assert not restarted.update_files_installed


async def test_later_matching_restart_resolves_an_earlier_mismatch(persisted, monkeypatch):
    await persisted.self_update()
    mismatch = await reload(persisted, restart=True)
    assert mismatch.update_phase == "restart_mismatch"
    monkeypatch.setattr("custom_components.integrationmanager.manager.VERSION", "0.2.0")
    completed = await reload(mismatch, restart=True)
    assert completed.update_phase == "completed" and completed.update_error is None
    assert not completed.restart_required


async def test_partial_failure_survives_mismatch_then_matching_version(persisted, monkeypatch):
    monkeypatch.setattr(
        persisted.store,
        "install",
        Mock(side_effect=InstallError("Recovery needed", files_changed=True)),
    )
    with pytest.raises(InstallError):
        await persisted.self_update()
    mismatch = await reload(persisted, restart=True)
    monkeypatch.setattr("custom_components.integrationmanager.manager.VERSION", "0.2.0")
    failed = await reload(mismatch, restart=True)
    assert failed.update_phase == "failed"
    assert "incomplete" in failed.update_error and "Recovery needed" in failed.update_error
    assert not failed.update_files_installed


async def test_store_failure_still_ends_native_active_progress(persisted, monkeypatch):
    monkeypatch.setattr(
        persisted.state_store, "async_save", AsyncMock(side_effect=OSError("Status storage full"))
    )
    with pytest.raises(OSError, match="storage full"):
        await persisted.self_update()
    assert not persisted.update_in_progress and persisted.update_phase == "failed"
    assert persisted.update_error == "Status storage full"
    persisted.client.download.assert_not_called()
