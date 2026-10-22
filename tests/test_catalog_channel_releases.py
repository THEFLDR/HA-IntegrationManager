from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.integrationmanager.catalog import parse_catalog, validate_repository
from custom_components.integrationmanager.channel import get_global_channel, resolve_channel
from custom_components.integrationmanager.const import DOMAIN, RC, STABLE
from custom_components.integrationmanager.releases import is_newer, select_release

ENTRY = {"name": "Friendly Integration", "domain": "sample", "repository": "THEFLDR/HA-Sample"}


def test_catalog_mapping():
    result = parse_catalog([ENTRY])[0]
    assert result.name == "Friendly Integration"
    assert result.repository == "THEFLDR/HA-Sample"
    assert result.domain == "sample"
    assert parse_catalog([]) == []


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        "https://evil",
        [None],
        [{}],
        [{**ENTRY, "extra": 1}],
        [{**ENTRY, "name": ""}],
        [{**ENTRY, "name": " bad "}],
        [{**ENTRY, "name": "Bad\nName"}],
        [{**ENTRY, "name": 3}],
        [{**ENTRY, "domain": "../escape"}],
        [{**ENTRY, "domain": "Bad-domain"}],
        [{**ENTRY, "domain": DOMAIN}],
        [{**ENTRY, "repository": "THEFLDR/HA-IntegrationManager"}],
        [ENTRY, ENTRY],
        [ENTRY, {**ENTRY, "domain": "other"}],
        [ENTRY, {**ENTRY, "repository": "THEFLDR/Other"}],
        [ENTRY] * 501,
    ],
)
def test_bad_catalog(payload):
    with pytest.raises(ValueError):
        parse_catalog(payload)


@pytest.mark.parametrize(
    "repo",
    [
        "other/repo",
        "thefldr/Repo",
        "THEFLDR/../x",
        "https://github.com/THEFLDR/repo",
        "THEFLDR/.",
        "THEFLDR/..",
        "THEFLDR/r?x",
        "THEFLDR/a\\b",
    ],
)
def test_repository_restriction(repo):
    with pytest.raises(ValueError):
        validate_repository(repo)


@pytest.mark.parametrize("global_channel", [STABLE, RC])
@pytest.mark.parametrize("override", [None, STABLE, RC])
def test_channel_lifecycle(global_channel, override):
    hass = SimpleNamespace(data={})
    intent = override
    assert get_global_channel(hass) is None
    assert resolve_channel(hass, intent)["effective_channel"] == (override or STABLE)
    hass.data[DOMAIN] = SimpleNamespace(entry=SimpleNamespace(options={"channel": global_channel}))
    result = resolve_channel(hass, intent)
    assert result["effective_channel"] == (override or global_channel)
    assert result["source"] == ("override" if override else "managed")
    assert result["state"] == (
        {STABLE: "Stable", RC: "Release Candidate"}[override] if override else "Managed"
    )
    del hass.data[DOMAIN]
    assert resolve_channel(hass, intent)["effective_channel"] == (override or STABLE)
    assert resolve_channel(hass, intent)["source"] == ("override" if override else "default")
    assert intent == override
    # Restoring Manager reuses its option without overwriting explicit intent.
    hass.data[DOMAIN] = SimpleNamespace(entry=SimpleNamespace(options={"channel": global_channel}))
    assert resolve_channel(hass, intent) == result


def test_invalid_channel():
    with pytest.raises(ValueError):
        resolve_channel(SimpleNamespace(data={}), "beta")
    with pytest.raises(ValueError):
        select_release([], "sample", "beta")


def test_release_filtering(release_payload):
    items = [
        release_payload("v0.1.0"),
        release_payload("v0.2.0", True),
        release_payload("v0.3.0-rc.1", True),
        release_payload("v9.0.0-beta.1", True),
        release_payload("v9.0.0-alpha.1", True),
        release_payload("v8.0.0", draft=True),
    ]
    assert select_release(items, "sample", STABLE).version == "0.1.0"
    assert select_release(items, "sample", RC).version == "0.3.0rc1"
    assert select_release(items[:2], "sample", RC).version == "0.2.0"
    assert select_release([], "sample", STABLE) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"immutable": False},
        {"tag_name": "main"},
        {"tag_name": "v1.0.0-dev1"},
        {"assets": []},
        {"assets": [{"name": "other.zip"}]},
        {"assets": [{"name": "sample.zip", "digest": "bad", "size": 1, "id": 1}]},
    ],
)
def test_unusable_releases(release_payload, changes):
    assert select_release([release_payload(**changes)], "sample", STABLE) is None


def test_asset_validation(release_payload):
    payload = release_payload()
    for key, value in [("size", 0), ("size", 10**9), ("id", -1), ("digest", None), ("id", True)]:
        changed = deepcopy(payload)
        changed["assets"][0][key] = value
        assert select_release([changed], "sample", STABLE) is None
    payload["assets"].append(deepcopy(payload["assets"][0]))
    assert select_release([payload], "sample", STABLE) is None


@pytest.mark.parametrize(
    ("candidate", "installed", "expected"),
    [
        ("0.2.0", "0.1.0", True),
        ("0.1.0", "0.1.0", False),
        ("0.1.0", "0.2.0", False),
        ("0.2.0rc1", "0.1.0", True),
        ("bad", "0.1.0", False),
    ],
)
def test_version_compare(candidate, installed, expected):
    assert is_newer(candidate, installed) is expected
