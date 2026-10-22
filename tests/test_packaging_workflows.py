import hashlib
import json
from unittest.mock import Mock

import pytest
import yaml

from custom_components.integrationmanager.const import DOMAIN
from custom_components.integrationmanager.files import validate_archive
from scripts import release as publisher
from scripts.package import ROOT, build


def test_package_deterministic_and_installable(tmp_path):
    artifact = build(tmp_path, "v0.1.0")
    first = artifact.read_bytes()
    assert build(tmp_path, "v0.1.0").read_bytes() == first
    files = validate_archive(first, DOMAIN, "0.1.0", "2025.12.5")
    assert files["frontend/icon.png"] == (ROOT / "approved-icon.png").read_bytes()
    assert "frontend/panel.js" in files
    assert all("__pycache__" not in f for f in files)
    assert (tmp_path / "SHA256SUMS").read_text().startswith(hashlib.sha256(first).hexdigest())
    with pytest.raises(ValueError, match="tag"):
        build(tmp_path, "v0.2.0")


def test_workflow_names_permissions_and_no_automatic_publication():
    workflows = {
        name: yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
        for name in ("ci.yml", "release.yml", "promotion.yml")
    }
    assert [w["name"] for w in workflows.values()] == [
        "CI - Validation",
        "Release - Publication",
        "Release - Promotion",
    ]
    assert [j["name"] for j in workflows["ci.yml"]["jobs"].values()] == ["CI - Validation"]
    assert "github/codeql-action" not in (ROOT / ".github/workflows/ci.yml").read_text()
    for name, data in workflows.items():
        assert data["permissions"] == {"contents": "read"}
        events = data.get("on", data.get(True))  # YAML 1.1 treats 'on' as boolean.
        assert "pull_request_target" not in events
        if name != "ci.yml":
            assert set(events) == {"workflow_dispatch"}
            assert any("${{ inputs.sha }}" in str(job) for job in data["jobs"].values())
    promotion = (ROOT / ".github/workflows/promotion.yml").read_text()
    assert "scripts.package" not in promotion
    assert "sandbox_tested" in promotion


def test_manifest_and_catalog():
    from custom_components.integrationmanager.catalog import parse_catalog

    manifest = json.loads((ROOT / f"custom_components/{DOMAIN}/manifest.json").read_text())
    assert manifest["domain"] == DOMAIN
    assert manifest["codeowners"] == ["@THEFLDR"]
    assert manifest["version"] == "0.1.0"
    assert parse_catalog(json.loads((ROOT / "repositories.json").read_text())) == []


def test_same_immutable_release_promotion(monkeypatch):
    before = {
        "id": 1,
        "tag_name": "v0.1.0",
        "immutable": True,
        "prerelease": True,
        "assets": [
            {"id": 42, "name": "integrationmanager.zip", "size": 10, "digest": "sha256:abc"}
        ],
    }
    after = {**before, "prerelease": False}
    responses = [
        {"object": {"sha": "a" * 40}},
        before,
        {"object": {"sha": "a" * 40, "type": "commit"}},
        {
            "workflow_runs": [
                {
                    "head_sha": "a" * 40,
                    "head_branch": "main",
                    "event": "push",
                    "conclusion": "success",
                }
            ]
        },
        after,
    ]
    api = Mock(side_effect=responses)
    monkeypatch.setenv("GITHUB_REPOSITORY", "THEFLDR/HA-IntegrationManager")
    monkeypatch.setattr(publisher, "api", api)
    publisher.main("promote", "v0.1.0", "a" * 40)
    assert api.call_args.args == (
        "repos/THEFLDR/HA-IntegrationManager/releases/1",
        "--method",
        "PATCH",
        "-F",
        "prerelease=false",
        "-f",
        "make_latest=true",
    )


def test_promotion_rejection_is_a_blocker(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "THEFLDR/HA-IntegrationManager")
    monkeypatch.setattr(
        publisher,
        "api",
        Mock(side_effect=[{"object": {"sha": "a" * 40}}, {"immutable": False, "prerelease": True}]),
    )
    with pytest.raises(ValueError, match="immutable"):
        publisher.main("promote", "v0.1.0", "a" * 40)


@pytest.mark.parametrize(
    "tag, sha", [("main", "a" * 40), ("v0.1.0", "main"), ("v0.1.0; malicious", "a" * 40)]
)
def test_release_rejects_ambiguous_inputs(tag, sha):
    with pytest.raises(ValueError):
        publisher.main("publish", tag, sha)


def test_publication_exact_sha_and_existing_release(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "THEFLDR/HA-IntegrationManager")
    monkeypatch.setattr(publisher, "api", Mock(return_value={"object": {"sha": "b" * 40}}))
    with pytest.raises(ValueError, match="main HEAD"):
        publisher.main("publish", "v0.1.0", "a" * 40)
    monkeypatch.setattr(
        publisher,
        "api",
        Mock(side_effect=[{"object": {"sha": "a" * 40}}, [{"tag_name": "v0.1.0"}]]),
    )
    with pytest.raises(ValueError, match="already exists"):
        publisher.main("publish", "v0.1.0", "a" * 40)


def test_verify_release_digest():
    data = b"artifact"
    publisher.verify_asset(
        {
            "assets": [
                {
                    "name": "integrationmanager.zip",
                    "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
                }
            ]
        },
        data,
    )
    with pytest.raises(ValueError, match="digest"):
        publisher.verify_asset({"assets": []}, data)


def test_publication_success_is_draft_then_immutable_prerelease(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dist").mkdir()
    data = b"tested artifact"
    (tmp_path / "dist/integrationmanager.zip").write_bytes(data)
    release = {
        "immutable": True,
        "prerelease": True,
        "assets": [
            {
                "name": "integrationmanager.zip",
                "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
            }
        ],
    }
    monkeypatch.setenv("GITHUB_REPOSITORY", "THEFLDR/HA-IntegrationManager")
    monkeypatch.setattr(
        publisher, "api", Mock(side_effect=[{"object": {"sha": "a" * 40}}, [], release])
    )
    verify = Mock()
    monkeypatch.setattr(publisher, "verify_main_ci", verify)
    monkeypatch.setattr(publisher, "tag_target", Mock(return_value=None))
    gh = Mock()
    monkeypatch.setattr(publisher, "gh", gh)
    publisher.main("publish", "v0.1.0", "a" * 40)
    verify.assert_called_once_with("THEFLDR/HA-IntegrationManager", "a" * 40)
    assert "--draft" in gh.call_args_list[0].args
    assert "--prerelease" in gh.call_args_list[0].args
    assert gh.call_args_list[1].args[:3] == ("release", "edit", "v0.1.0")
    assert "--draft=false" in gh.call_args_list[1].args


@pytest.mark.parametrize(
    "runs",
    [
        [],
        [
            {
                "head_sha": "a" * 40,
                "head_branch": "CODEX/0.1.0",
                "event": "pull_request",
                "conclusion": "success",
            }
        ],
        [{"head_sha": "a" * 40, "head_branch": "main", "event": "push", "conclusion": "failure"}],
    ],
)
def test_release_requires_successful_exact_main_ci(monkeypatch, runs):
    monkeypatch.setattr(publisher, "api", Mock(return_value={"workflow_runs": runs}))
    with pytest.raises(ValueError, match="exact merged main SHA"):
        publisher.verify_main_ci("THEFLDR/HA-IntegrationManager", "a" * 40)


def test_changed_promotion_artifacts_rejected(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "THEFLDR/HA-IntegrationManager")
    before = {
        "id": 1,
        "tag_name": "v0.1.0",
        "immutable": True,
        "prerelease": True,
        "assets": [{"id": 42, "name": "integrationmanager.zip", "size": 2, "digest": "sha256:old"}],
    }
    after = {
        **before,
        "prerelease": False,
        "assets": [{"id": 43, "name": "integrationmanager.zip", "size": 2, "digest": "sha256:new"}],
    }
    monkeypatch.setattr(
        publisher, "api", Mock(side_effect=[{"object": {"sha": "a" * 40}}, before, after])
    )
    monkeypatch.setattr(publisher, "tag_target", Mock(return_value="a" * 40))
    monkeypatch.setattr(publisher, "verify_main_ci", Mock())
    with pytest.raises(ValueError, match="assets changed"):
        publisher.main("promote", "v0.1.0", "a" * 40)
