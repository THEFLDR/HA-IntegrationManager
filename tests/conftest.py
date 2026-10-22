import io
import json
import zipfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from homeassistant import loader
from homeassistant.config_entries import ConfigEntries
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.integrationmanager.const import DOMAIN


def pytest_report_header():
    return f"Home Assistant Core under test: {HA_VERSION}"


@pytest.fixture
def archive():
    def make(domain="sample", version="0.2.0", extra=None, minimum="2025.12.0"):
        files = {
            f"custom_components/{domain}/manifest.json": json.dumps(
                {"domain": domain, "version": version, "name": "Friendly Name"}
            ),
            f"custom_components/{domain}/__init__.py": "# working integration\n",
            "release.json": json.dumps(
                {"domain": domain, "version": version, "minimum_home_assistant_version": minimum}
            ),
        }
        files.update(extra or {})
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as output:
            for name, value in files.items():
                output.writestr(name, value)
        return stream.getvalue()

    return make


@pytest.fixture
def release_payload():
    def make(tag="0.2.0", prerelease=False, **changes):
        return {
            "tag_name": tag,
            "prerelease": prerelease,
            "draft": False,
            "immutable": True,
            "assets": [
                {"name": "sample.zip", "id": 123, "digest": "sha256:" + "a" * 64, "size": 512}
            ],
            **changes,
        }

    return make


@pytest_asyncio.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    loader.async_setup(instance)
    instance.config_entries = ConfigEntries(instance, {})
    await instance.config_entries.async_initialize()
    dr.async_setup(instance)
    await dr.async_load(instance)
    await er.async_load(instance)
    yield instance
    await instance.async_stop(force=True)


@pytest.fixture
def runtime(tmp_path):
    from custom_components.integrationmanager.files import FileStore
    from custom_components.integrationmanager.manager import Manager

    async def executor(function, *args):
        return function(*args)

    hass = SimpleNamespace(
        data={},
        async_add_executor_job=executor,
        config_entries=SimpleNamespace(
            async_entries=Mock(return_value=[]),
            async_remove=AsyncMock(return_value={"require_restart": False}),
        ),
    )
    entry = SimpleNamespace(options={}, entry_id="manager")
    client = SimpleNamespace(
        catalog=AsyncMock(return_value=[]),
        releases=AsyncMock(return_value=[]),
        download=AsyncMock(),
        invalidate_releases=Mock(),
    )
    manager = Manager(hass, entry, client, FileStore(str(tmp_path)))
    hass.data[DOMAIN] = manager
    return manager
