"""Exercise actual HA loading, config entries and native entity platforms."""

import socket
from unittest.mock import AsyncMock

import aiohttp
import pytest
from homeassistant import bootstrap, loader
from homeassistant.auth.const import GROUP_ID_ADMIN
from homeassistant.core import HomeAssistant

from custom_components.integrationmanager.const import DOMAIN


@pytest.mark.parametrize("offline", [False, True])
async def test_actual_home_assistant_startup(tmp_path, monkeypatch, offline):
    from custom_components.integrationmanager.files import FileStore, validate_archive
    from custom_components.integrationmanager.github import GitHubClient, GitHubError
    from scripts.package import build

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
    FileStore(str(tmp_path)).install(
        DOMAIN, validate_archive(package, DOMAIN, "0.1.0", "2025.12.5")
    )
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
        await instance.async_block_till_done()
        assert DOMAIN in instance.data
        assert any(state.domain == "update" for state in instance.states.async_all())
        health = [
            state
            for state in instance.states.async_all()
            if state.attributes.get("scope") == "Integration Manager release discovery"
        ]
        assert len(health) == 1 and health[0].state == ("on" if offline else "off")
        channel = [
            state
            for state in instance.states.async_all()
            if state.attributes.get("effective_channel") == "stable"
        ]
        assert len(channel) == 1 and channel[0].state == "Stable"
        await instance.async_start()
        port = instance.http.site._server.sockets[0].getsockname()[1]
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
        await instance.async_block_till_done(wait_background_tasks=True)
        assert await instance.config_entries.async_unload(created["result"].entry_id)
        assert DOMAIN not in instance.data
    finally:
        await instance.async_stop(force=True)
        await resolver.close()
