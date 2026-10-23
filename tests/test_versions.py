import json
import subprocess
import sys
import zipfile
from unittest.mock import Mock

import pytest

from custom_components.integrationmanager.const import DOMAIN, MIN_HA_VERSION, RC, STABLE, VERSION
from custom_components.integrationmanager.files import InstallError, validate_archive
from custom_components.integrationmanager.releases import is_newer, select_release
from custom_components.integrationmanager.version import is_release_version
from scripts import release as publisher
from scripts.package import ROOT, build


@pytest.mark.parametrize("version", ["0.0.0", "0.1.1", "1.0.0", "10.20.300"])
def test_canonical_release_versions(version):
    assert is_release_version(version)


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        1,
        [],
        "",
        "v0.1.1",
        "V0.1.1",
        "0.1",
        "0.1.1.0",
        "0.1.1-rc.1",
        "0.1.1rc1",
        "0.1.1b1",
        "0.1.1.dev1",
        "0.1.1+local",
        "0.01.1",
        "01.0.0",
        "0.1.01",
        " 0.1.1",
        "0.1.1\n",
        "٠.١.١",
        "-1.0.0",
    ],
)
def test_noncanonical_release_versions(invalid):
    assert not is_release_version(invalid)


@pytest.mark.parametrize("invalid", ["v0.1.1", "0.1.1-rc.1", "0.1", "0.01.1", "0.1.1+local"])
def test_release_boundaries_reject_noncanonical_versions(
    invalid, tmp_path, archive, release_payload, monkeypatch
):
    api, gh = Mock(), Mock()
    monkeypatch.setattr(publisher, "api", api)
    monkeypatch.setattr(publisher, "gh", gh)
    for action in ("publish", "promote"):
        with pytest.raises(ValueError, match="X.Y.Z"):
            publisher.main(action, invalid, "a" * 40)
    api.assert_not_called()
    gh.assert_not_called()
    with pytest.raises(ValueError, match="tag"):
        build(tmp_path, invalid)
    for channel in (STABLE, RC):
        assert select_release([release_payload(invalid)], "sample", channel) is None
        assert select_release([release_payload(invalid, True)], "sample", channel) is None
    assert not is_newer(invalid, "0.1.0")
    assert not is_newer("1.0.0", invalid)
    with pytest.raises(InstallError):
        validate_archive(archive(version=invalid), "sample", invalid, MIN_HA_VERSION)


@pytest.mark.parametrize("location", ["requested", "manifest", "metadata"])
def test_archive_does_not_normalize_prefixed_versions(archive, location):
    invalid = "v0.2.0"
    extra = {}
    if location == "manifest":
        extra["custom_components/sample/manifest.json"] = json.dumps(
            {"domain": "sample", "version": invalid}
        )
    if location == "metadata":
        extra["release.json"] = json.dumps(
            {
                "domain": "sample",
                "version": invalid,
                "minimum_home_assistant_version": MIN_HA_VERSION,
            }
        )
    with pytest.raises(InstallError):
        validate_archive(
            archive(extra=extra),
            "sample",
            invalid if location == "requested" else "0.2.0",
            MIN_HA_VERSION,
        )


def test_version_is_identical_in_manifest_metadata_and_release_channels(tmp_path, release_payload):
    assert VERSION == "0.1.5"
    artifact = build(tmp_path, VERSION)
    with zipfile.ZipFile(artifact) as package:
        metadata = json.loads(package.read("release.json"))
        manifest = json.loads(package.read(f"custom_components/{DOMAIN}/manifest.json"))
    assert metadata["version"] == manifest["version"] == VERSION
    payload = release_payload(VERSION, True)
    candidate = select_release([payload], "sample", RC)
    assert candidate.tag == candidate.version == VERSION
    assert select_release([payload], "sample", STABLE) is None
    payload["prerelease"] = False
    assert select_release([payload], "sample", STABLE) == candidate


def test_packaging_rejects_empty_tag_and_noncanonical_manifest(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="tag"):
        build(tmp_path, "")
    from scripts import package

    source = tmp_path / f"custom_components/{DOMAIN}"
    source.mkdir(parents=True)
    (source / "manifest.json").write_text(json.dumps({"version": "v0.1.1"}))
    monkeypatch.setattr(package, "ROOT", tmp_path)
    monkeypatch.setattr(package, "VERSION", "v0.1.1")
    with pytest.raises(ValueError, match="versions differ"):
        build(tmp_path / "dist")


def test_release_cli_module_works_without_third_party_dependencies():
    result = subprocess.run(
        [sys.executable, "-S", "-m", "scripts.release", "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert "publish" in result.stdout and "promote" in result.stdout


@pytest.mark.parametrize("returned_tag", ["v0.1.1", "0.2.0"])
def test_publication_rejects_wrong_returned_tag(monkeypatch, returned_tag):
    monkeypatch.setenv("GITHUB_REPOSITORY", "THEFLDR/HA-IntegrationManager")
    monkeypatch.setattr(publisher, "verify_main_ci", Mock())
    monkeypatch.setattr(publisher, "published", Mock(return_value=False))
    monkeypatch.setattr(publisher, "gh", Mock())
    monkeypatch.setattr(
        publisher,
        "api",
        Mock(
            side_effect=[
                {"object": {"sha": "a" * 40}},
                {"object": {"sha": "a" * 40}},
                {"tag_name": returned_tag, "immutable": True, "prerelease": True},
            ]
        ),
    )
    with pytest.raises(ValueError, match="publication"):
        publisher.main("publish", VERSION, "a" * 40)


def test_promotion_rejects_wrong_original_tag_before_metadata_write(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "THEFLDR/HA-IntegrationManager")
    api = Mock(
        side_effect=[
            {"object": {"sha": "a" * 40}},
            {"tag_name": "v0.1.1", "immutable": True, "prerelease": True},
        ]
    )
    monkeypatch.setattr(publisher, "api", api)
    with pytest.raises(ValueError, match="Promotion requires"):
        publisher.main("promote", VERSION, "a" * 40)
    assert api.call_count == 2
