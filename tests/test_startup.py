"""Exercise actual HA loading, config entries and native entity platforms."""

import socket
from unittest.mock import AsyncMock

import aiohttp
import pytest
from homeassistant import bootstrap, loader
from homeassistant.auth.const import GROUP_ID_ADMIN
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory

from custom_components import integrationmanager as integration
from custom_components.integrationmanager.const import DOMAIN, NAME, RC, STABLE


@pytest.mark.parametrize(
    "offline,legacy",
    [
        (False, None),
        (True, None),
        (False, "0.1.1"),
        (False, "0.1.2"),
        (False, "0.1.2-disabled"),
        (False, "0.1.3"),
        (False, "0.1.3-custom"),
    ],
)
async def test_actual_home_assistant_startup(tmp_path, monkeypatch, offline, legacy):
    from custom_components.integrationmanager.files import FileStore, validate_archive
    from custom_components.integrationmanager.github import GitHubClient, GitHubError
    from scripts.package import build

    legacy_entities = {}
    old_setup = integration.async_setup_entry

    async def upgrading_setup(hass, config_entry):
        if legacy and not legacy_entities:
            options = {"channel": RC}
            if legacy.startswith(("0.1.2", "0.1.3")):
                options.update(global_channel=STABLE, channel_mode="override")
            hass.config_entries.async_update_entry(config_entry, options=options)
            device = None
            if legacy in {"0.1.1", "0.1.3", "0.1.3-custom"}:
                device = dr.async_get(hass).async_get_or_create(
                    config_entry_id=config_entry.entry_id,
                    identifiers={(DOMAIN, config_entry.entry_id)},
                    name=NAME,
                    manufacturer="THEFLDR",
                    sw_version=legacy,
                    entry_type=dr.DeviceEntryType.SERVICE if legacy.startswith("0.1.3") else None,
                )
            for platform, suffix in [
                ("binary_sensor", "health"),
                ("sensor", "release_channel"),
                ("update", "update"),
            ] + (
                [("sensor", "release_channel_mode")]
                if legacy.startswith(("0.1.2", "0.1.3"))
                else []
            ):
                registered = er.async_get(hass).async_get_or_create(
                    platform,
                    DOMAIN,
                    f"{config_entry.entry_id}_{suffix}",
                    config_entry=config_entry,
                    device_id=device.id if device else None,
                    suggested_object_id=f"integration_manager_{suffix}"
                    if legacy == "0.1.3"
                    else f"owner_existing_{suffix}",
                    entity_category=EntityCategory.DIAGNOSTIC if suffix != "update" else None,
                    disabled_by=er.RegistryEntryDisabler.USER
                    if legacy == "0.1.2-disabled" and suffix == "release_channel_mode"
                    else None,
                )
                er.async_get(hass).async_update_entity(registered.entity_id, name=f"Owner {suffix}")
                legacy_entities[suffix] = registered.entity_id
        return await old_setup(hass, config_entry)

    monkeypatch.setattr(integration, "async_setup_entry", upgrading_setup)

    monkeypatch.setattr(
        GitHubClient,
        "releases",
        AsyncMock(
            return_value=[], side_effect=GitHubError("offline", transient=True) if offline else None
        ),
    )
    monkeypatch.setattr(GitHubClient, "catalog", AsyncMock(return_value=[]))
    resolver = aiohttp.ThreadedResolver()
    monkeypatch.setattr(
        "homeassistant.helpers.aiohttp_client._async_get_or_create_resolver", lambda hass: resolver
    )
    instance = HomeAssistant(str(tmp_path))
    package = build(tmp_path / "package").read_bytes()
    FileStore(str(tmp_path)).install(DOMAIN, validate_archive(package, DOMAIN, "0.1.4", HA_VERSION))
    instance.config.skip_pip = True
    loader.async_setup(instance)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    try:
        result = await bootstrap.async_from_config_dict(
            {
                "homeassistant": {
                    "name": "Integration test",
                    "time_zone": "UTC",
                    "country": "DE",
                    "latitude": 0,
                    "longitude": 0,
                    "elevation": 0,
                    "unit_system": "metric",
                },
                "http": {"server_host": "127.0.0.1", "server_port": port},
                DOMAIN: {},
            },
            instance,
        )
        assert result is instance
        assert DOMAIN in instance.config.components
        flow = await instance.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        created = await instance.config_entries.flow.async_configure(flow["flow_id"], {})
        assert created["type"] == "create_entry"
        assert created["title"] == NAME
        await instance.async_block_till_done()
        assert DOMAIN in instance.data
        assert any(state.domain == "update" for state in instance.states.async_all())
        health_id = er.async_get(instance).async_get_entity_id(
            "binary_sensor", DOMAIN, f"{created['result'].entry_id}_health"
        )
        health = instance.states.get(health_id)
        assert health.state == ("unavailable" if offline else "off")
        expected_channel = RC if legacy else STABLE
        channel = [
            state
            for state in instance.states.async_all()
            if "effective_channel" in state.attributes
        ]
        assert len(channel) == 1 and channel[0].state == (
            "Release Candidate" if legacy else "Stable"
        )
        assert channel[0].attributes["effective_channel"] == expected_channel
        assert "source" not in channel[0].attributes and "mode" not in channel[0].attributes
        config_entry = created["result"]
        services = dr.async_entries_for_config_entry(dr.async_get(instance), config_entry.entry_id)
        assert len(services) == 1
        service = services[0]
        assert service.entry_type == dr.DeviceEntryType.SERVICE
        assert service.identifiers == {(DOMAIN, config_entry.entry_id)}
        assert service.config_entry_id == config_entry.entry_id
        assert service.name == NAME and service.manufacturer is None
        assert service.sw_version == "0.1.4"
        registry = er.async_get(instance)
        registered = er.async_entries_for_config_entry(registry, config_entry.entry_id)
        assert len(registered) == 5
        assert all(item.device_id == service.id for item in registered)
        for item in registered:
            suffix = item.unique_id.removeprefix(f"{config_entry.entry_id}_")
            assert item.entity_category == (
                None
                if suffix == "health"
                else EntityCategory.CONFIG
                if suffix == "update"
                else EntityCategory.DIAGNOSTIC
            )
            if suffix in legacy_entities:
                assert item.entity_id == (
                    f"{item.domain}.{DOMAIN}_{suffix}"
                    if legacy == "0.1.3"
                    else legacy_entities[suffix]
                )
                assert item.name == f"Owner {suffix}"
            else:
                assert item.entity_id == f"{item.domain}.{DOMAIN}_{suffix}"
        mode_entry = next(
            item for item in registered if item.translation_key == "release_channel_mode"
        )
        if legacy == "0.1.2-disabled":
            assert mode_entry.disabled_by == er.RegistryEntryDisabler.USER
            assert instance.states.get(mode_entry.entity_id) is None
        else:
            assert instance.states.get(mode_entry.entity_id).state == (
                "Override" if legacy else "Managed"
            )
        if legacy and legacy.startswith(("0.1.2", "0.1.3")):
            assert dict(config_entry.options) == {
                "global_channel": STABLE,
                "channel_mode": "override",
                "channel": RC,
            }
        entity_ids = {item.entity_id for item in registered}
        original_options = dict(config_entry.options)
        assert await instance.config_entries.async_reload(config_entry.entry_id)
        await instance.async_block_till_done()
        assert config_entry.options == original_options
        assert {
            item.entity_id
            for item in er.async_entries_for_config_entry(registry, config_entry.entry_id)
        } == entity_ids
        assert [
            item.id
            for item in dr.async_entries_for_config_entry(
                dr.async_get(instance), config_entry.entry_id
            )
        ] == [service.id]
        await instance.async_start()
        if not offline and legacy is None:
            from homeassistant.setup import async_setup_component

            from custom_components.integrationmanager.backup import async_backup, backup_manager

            assert await async_setup_component(instance, "backup", {})
            await instance.async_block_till_done()
            assert backup_manager(instance) is not None
            await async_backup(instance)
            backups, errors = await backup_manager(instance).async_get_backups()
            assert backups and not errors
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/{DOMAIN}_static/panel.js") as response:
                assert response.status == 200
                assert "integrationmanager-panel" in await response.text()
            for admin in (True, False):
                user = await instance.auth.async_create_user(
                    "Test admin" if admin else "Test user",
                    group_ids=[GROUP_ID_ADMIN] if admin else [],
                )
                refresh = await instance.auth.async_create_refresh_token(
                    user, client_id="http://localhost/test"
                )
                access = instance.auth.async_create_access_token(refresh)
                for image in ("icon.png", "logo.png", "dark_icon@2x.png"):
                    async with session.get(
                        f"http://127.0.0.1:{port}/api/brands/integration/{DOMAIN}/{image}",
                        headers={"Authorization": f"Bearer {access}"},
                    ) as response:
                        assert response.status == 200
                        assert (
                            await response.read()
                            == (
                                tmp_path / "custom_components" / DOMAIN / "brand/icon.png"
                            ).read_bytes()
                        )
                async with session.ws_connect(f"http://127.0.0.1:{port}/api/websocket") as ws:
                    assert (await ws.receive_json())["type"] == "auth_required"
                    await ws.send_json({"type": "auth", "access_token": access})
                    assert (await ws.receive_json())["type"] == "auth_ok"
                    await ws.send_json({"id": 1, "type": f"{DOMAIN}/command", "action": "overview"})
                    response = await ws.receive_json()
                    assert response["success"] is admin
                    if admin:
                        assert (
                            response["result"]["integrations"][0]["name"] == "Integration Manager"
                        )
                    else:
                        assert response["error"]["code"] == "unauthorized"
                    await ws.send_json(
                        {"id": 2, "type": f"{DOMAIN}/command", "action": "check_updates"}
                    )
                    checked = await ws.receive_json()
                    assert checked["success"] is (admin and not offline)
                    if admin and not offline:
                        assert "up to date" in checked["result"]["message"]
                    elif admin:
                        assert "offline" in checked["error"]["message"]
                    if admin:
                        # These are the native frontend's service-detail prerequisites.
                        await ws.send_json({"id": 3, "type": "config/device_registry/list"})
                        devices_response = await ws.receive_json()
                        assert devices_response["success"]
                        native_services = [
                            item for item in devices_response["result"] if item["id"] == service.id
                        ]
                        assert (
                            len(native_services) == 1
                            and native_services[0]["entry_type"] == "service"
                        )
                        await ws.send_json({"id": 4, "type": "config/entity_registry/list"})
                        entities_response = await ws.receive_json()
                        assert entities_response["success"]
                        native_entities = [
                            item
                            for item in entities_response["result"]
                            if item["config_entry_id"] == config_entry.entry_id
                        ]
                        assert len(native_entities) == 5
                        assert all(item["device_id"] == service.id for item in native_entities)
        await instance.async_block_till_done(wait_background_tasks=True)
        assert await instance.config_entries.async_unload(created["result"].entry_id)
        assert DOMAIN not in instance.data
        await instance.config_entries.async_remove(config_entry.entry_id)
        await instance.async_block_till_done()
        assert (
            dr.async_entries_for_config_entry(dr.async_get(instance), config_entry.entry_id) == []
        )
        assert er.async_entries_for_config_entry(registry, config_entry.entry_id) == []
    finally:
        await instance.async_stop(force=True)
        await resolver.close()
