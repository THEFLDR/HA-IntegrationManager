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


@pytest.mark.parametrize("offline,legacy", [(False, False), (True, False), (False, True)])
async def test_actual_home_assistant_startup(tmp_path, monkeypatch, offline, legacy):
    from custom_components.integrationmanager.files import FileStore, validate_archive
    from custom_components.integrationmanager.github import GitHubClient, GitHubError
    from scripts.package import build

    legacy_entities = {}
    old_setup = integration.async_setup_entry

    async def upgrading_setup(hass, config_entry):
        if legacy:
            hass.config_entries.async_update_entry(config_entry, options={"channel": RC})
            device = dr.async_get(hass).async_get_or_create(
                config_entry_id=config_entry.entry_id,
                identifiers={(DOMAIN, config_entry.entry_id)},
                name=NAME,
                manufacturer="THEFLDR",
                sw_version="0.1.1",
            )
            for platform, suffix in [
                ("binary_sensor", "health"),
                ("sensor", "release_channel"),
                ("update", "update"),
            ]:
                registered = er.async_get(hass).async_get_or_create(
                    platform,
                    DOMAIN,
                    f"{config_entry.entry_id}_{suffix}",
                    config_entry=config_entry,
                    device_id=device.id,
                    suggested_object_id=f"owner_existing_{suffix}",
                    entity_category=EntityCategory.DIAGNOSTIC if suffix != "update" else None,
                )
                er.async_get(hass).async_update_entity(registered.entity_id, name=f"Owner {suffix}")
                legacy_entities[suffix] = registered.entity_id
        return await old_setup(hass, config_entry)

    monkeypatch.setattr(integration, "async_setup_entry", upgrading_setup)

    monkeypatch.setattr(
        GitHubClient,
        "releases",
        AsyncMock(return_value=[], side_effect=GitHubError("offline") if offline else None),
    )
    monkeypatch.setattr(GitHubClient, "catalog", AsyncMock(return_value=[]))
    resolver = aiohttp.ThreadedResolver()
    monkeypatch.setattr(
        "homeassistant.helpers.aiohttp_client._async_get_or_create_resolver", lambda hass: resolver
    )
    instance = HomeAssistant(str(tmp_path))
    package = build(tmp_path / "package").read_bytes()
    FileStore(str(tmp_path)).install(DOMAIN, validate_archive(package, DOMAIN, "0.1.2", HA_VERSION))
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
        health = [
            state
            for state in instance.states.async_all()
            if state.attributes.get("scope") == "Integration Manager release discovery"
        ]
        assert len(health) == 1 and health[0].state == ("on" if offline else "off")
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
        assert (
            dr.async_entries_for_config_entry(dr.async_get(instance), config_entry.entry_id) == []
        )
        registry = er.async_get(instance)
        registered = er.async_entries_for_config_entry(registry, config_entry.entry_id)
        assert len(registered) == 4
        assert all(item.device_id is None for item in registered)
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
                assert item.entity_id == legacy_entities[suffix]
                assert item.name == f"Owner {suffix}"
            else:
                assert item.entity_id == f"{item.domain}.{DOMAIN}_{suffix}"
        mode_entry = next(
            item for item in registered if item.translation_key == "release_channel_mode"
        )
        assert instance.states.get(mode_entry.entity_id).state == (
            "Override" if legacy else "Managed"
        )
        await instance.async_start()
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
        await instance.async_block_till_done(wait_background_tasks=True)
        assert await instance.config_entries.async_unload(created["result"].entry_id)
        assert DOMAIN not in instance.data
    finally:
        await instance.async_stop(force=True)
        await resolver.close()
