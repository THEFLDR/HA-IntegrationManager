import base64
import hashlib
import json
import subprocess
import zipfile
from unittest.mock import Mock

import pytest
import yaml

from custom_components.integrationmanager.const import DOMAIN, MIN_HA_VERSION
from custom_components.integrationmanager.files import validate_archive
from scripts import release as publisher
from scripts.package import ROOT, build


def test_package_deterministic_and_installable(tmp_path):
    artifact = build(tmp_path, "0.1.6")
    first = artifact.read_bytes()
    assert build(tmp_path, "0.1.6").read_bytes() == first
    assert MIN_HA_VERSION == "2025.12.0"
    files = validate_archive(first, DOMAIN, "0.1.6", MIN_HA_VERSION)
    with zipfile.ZipFile(artifact) as archive:
        assert (
            json.loads(archive.read("release.json"))["minimum_home_assistant_version"]
            == MIN_HA_VERSION
        )
    assert files["frontend/icon.png"] == (ROOT / "approved-icon.png").read_bytes()
    assert files["brand/icon.png"] == (ROOT / "approved-icon.png").read_bytes()
    assert "frontend/panel.js" in files
    assert all("__pycache__" not in f for f in files)
    assert (tmp_path / "SHA256SUMS").read_text().startswith(hashlib.sha256(first).hexdigest())
    with pytest.raises(ValueError, match="tag"):
        build(tmp_path, "0.2.0")


def test_svg_embeds_only_the_exact_approved_png():
    from defusedxml.ElementTree import fromstring

    # Parse the owner-supplied asset without executing or modifying its content.
    svg = fromstring(
        (ROOT / "custom_components/integrationmanager/frontend/approved-icon.svg").read_bytes()
    )
    assert svg.tag == "{http://www.w3.org/2000/svg}svg"
    assert svg.attrib["viewBox"] == "0 0 512 512"
    assert [element.tag for element in svg] == [
        "{http://www.w3.org/2000/svg}title",
        "{http://www.w3.org/2000/svg}image",
    ]
    image = svg[1]
    assert {key: value for key, value in image.attrib.items() if key != "href"} == {
        "x": "0",
        "y": "0",
        "width": "512",
        "height": "512",
    }
    assert image.attrib["href"].startswith("data:image/png;base64,")
    assert (
        base64.b64decode(image.attrib["href"].split(",", 1)[1], validate=True)
        == (ROOT / "approved-icon.png").read_bytes()
    )


def test_workflow_names_permissions_and_publication_guards():
    workflows = {
        name: yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
        for name in ("ci.yml", "release.yml", "promotion.yml")
    }
    assert [w["name"] for w in workflows.values()] == ["Validation", "Publication", "Promotion"]
    assert {
        name: {job_id: job["name"] for job_id, job in workflow["jobs"].items()}
        for name, workflow in workflows.items()
    } == {
        "ci.yml": {"validation": "Validation"},
        "release.yml": {"authorize": "Publication authorization", "publish": "Publication"},
        "promotion.yml": {"authorize": "Owner initiation", "promote": "Promotion"},
    }
    assert "github/codeql-action" not in (ROOT / ".github/workflows/ci.yml").read_text()
    for name, data in workflows.items():
        assert data["permissions"] == {"contents": "read"}
        events = data.get("on", data.get(True))  # YAML 1.1 treats 'on' as boolean.
        assert "pull_request_target" not in events
        if name != "ci.yml":
            assert set(events) == (
                {"workflow_dispatch", "workflow_run"}
                if name == "release.yml"
                else {"workflow_dispatch"}
            )
            assert "inputs.sha" in str(data)
            assert "X.Y.Z" in events["workflow_dispatch"]["inputs"]["tag"]["description"]
            assert "0.1.1" in events["workflow_dispatch"]["inputs"]["tag"]["description"]
            assert "python -m scripts.release " in str(data)
    publication = workflows["release.yml"]
    events = publication.get("on", publication.get(True))
    assert events["workflow_run"] == {
        "workflows": ["Validation"],
        "types": ["completed"],
        "branches": ["main"],
    }
    assert publication["concurrency"] == {
        "group": "integrationmanager-release",
        "cancel-in-progress": False,
    }
    job = publication["jobs"]["publish"]
    assert job["permissions"] == {"contents": "write", "actions": "read"}
    assert job["needs"] == "authorize"
    guard = publication["jobs"]["authorize"]["if"]
    for required in [
        "github.repository == 'THEFLDR/HA-IntegrationManager'",
        "github.actor == 'THEFLDR'",
        "github.ref == 'refs/heads/main'",
        "workflow_run.event == 'push'",
        "workflow_run.conclusion == 'success'",
        "workflow_run.head_branch == 'main'",
        "workflow_run.name == 'Validation'",
        "workflow_run.path == '.github/workflows/ci.yml'",
        "workflow_run.repository.full_name",
        "workflow_run.head_repository.full_name",
    ]:
        assert required in guard
    steps = job["steps"]
    assert steps[0]["with"]["ref"] == "${{ needs.authorize.outputs.sha }}"
    assert steps[0]["with"]["persist-credentials"] is False
    assert "github.sha" not in str(publication)
    assert "python -m scripts.release prepare" in str(steps)
    assert "pytest" not in str(steps) and "npm test" not in str(steps)
    for step in steps:
        if (
            "scripts.package" in step.get("run", "")
            or "scripts.release publish" in step.get("run", "")
            or "pip install" in step.get("run", "")
        ):
            assert step["if"] == "steps.candidate.outputs.publish == 'true'"
    assert workflows["promotion.yml"]["concurrency"] == publication["concurrency"]
    promotion = (ROOT / ".github/workflows/promotion.yml").read_text()
    assert "scripts.package" not in promotion
    assert "sandbox_tested" in promotion


def test_manifest_and_catalog():
    from custom_components.integrationmanager.catalog import parse_catalog

    manifest = json.loads((ROOT / f"custom_components/{DOMAIN}/manifest.json").read_text())
    assert manifest["domain"] == DOMAIN
    assert manifest["codeowners"] == ["@THEFLDR"]
    assert manifest["version"] == "0.1.6"
    assert parse_catalog(json.loads((ROOT / "repositories.json").read_text())) == []


def test_same_immutable_release_promotion(monkeypatch):
    before = {
        "id": 1,
        "tag_name": "0.1.1",
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
        {"id": 5, "name": "Validation", "path": ".github/workflows/ci.yml"},
        {
            "workflow_runs": [
                {
                    "workflow_id": 5,
                    "name": "Validation",
                    "path": ".github/workflows/ci.yml",
                    "repository": {"full_name": "THEFLDR/HA-IntegrationManager"},
                    "head_repository": {"full_name": "THEFLDR/HA-IntegrationManager"},
                    "status": "completed",
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
    publisher.main("promote", "0.1.1", "a" * 40)
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
        publisher.main("promote", "0.1.1", "a" * 40)


@pytest.mark.parametrize(
    "tag, sha", [("main", "a" * 40), ("0.1.1", "main"), ("0.1.1; malicious", "a" * 40)]
)
def test_release_rejects_ambiguous_inputs(tag, sha):
    with pytest.raises(ValueError):
        publisher.main("publish", tag, sha)


@pytest.mark.parametrize(
    "stderr", ["gh: Forbidden (HTTP 403)", "gh: server error (HTTP 500)", "transport failed"]
)
def test_tag_lookup_never_treats_other_errors_as_absence(monkeypatch, stderr):
    error = subprocess.CalledProcessError(1, ["gh", "api"], output="", stderr=stderr)
    monkeypatch.setattr(publisher, "gh", Mock(side_effect=error))
    with pytest.raises(subprocess.CalledProcessError) as raised:
        publisher.tag_target("THEFLDR/HA-IntegrationManager", "0.1.1", missing_ok=True)
    assert raised.value is error


def test_missing_tag_still_fails_promotion_and_cli_captures_stderr(monkeypatch):
    monkeypatch.setattr(publisher.shutil, "which", Mock(return_value="/usr/bin/gh"))
    run = Mock(
        side_effect=subprocess.CalledProcessError(
            1, ["gh", "api"], output="", stderr="gh: Not Found (HTTP 404)"
        )
    )
    monkeypatch.setattr(publisher.subprocess, "check_output", run)
    with pytest.raises(subprocess.CalledProcessError):
        publisher.tag_target("THEFLDR/HA-IntegrationManager", "0.1.1")
    assert run.call_args.kwargs["stderr"] == subprocess.PIPE


@pytest.mark.parametrize(
    "runs",
    [
        [],
        [
            {
                "head_sha": "a" * 40,
                "head_branch": "CODEX/0.1.1",
                "event": "pull_request",
                "conclusion": "success",
            }
        ],
        [{"head_sha": "a" * 40, "head_branch": "main", "event": "push", "conclusion": "failure"}],
    ],
)
def test_release_requires_successful_exact_main_ci(monkeypatch, runs):
    monkeypatch.setattr(
        publisher,
        "api",
        Mock(
            side_effect=[
                {"id": 5, "name": "Validation", "path": ".github/workflows/ci.yml"},
                {"workflow_runs": runs},
            ]
        ),
    )
    with pytest.raises(ValueError, match="exact merged main SHA"):
        publisher.verify_main_ci("THEFLDR/HA-IntegrationManager", "a" * 40)


def test_changed_promotion_artifacts_rejected(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "THEFLDR/HA-IntegrationManager")
    before = {
        "id": 1,
        "tag_name": "0.1.1",
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
        publisher.main("promote", "0.1.1", "a" * 40)


def test_sidebar_trace_is_a_labeled_vector_candidate_outside_the_installable_package(tmp_path):
    from defusedxml.ElementTree import fromstring

    svg = fromstring((ROOT / "previews/0.1.6-sidebar-icon-candidate.svg").read_bytes())
    namespace = "{http://www.w3.org/2000/svg}"
    assert [element.tag for element in svg] == [
        f"{namespace}title",
        f"{namespace}desc",
        f"{namespace}path",
    ]
    assert "Unapproved" in svg[0].text
    assert hashlib.sha256((ROOT / "approved-icon.png").read_bytes()).hexdigest() in svg[1].text
    assert svg.attrib["viewBox"] == "0 0 512 512"
    assert svg[2].attrib["fill"] == "currentColor"
    assert svg[2].attrib["d"].startswith("M") and "C" in svg[2].attrib["d"]
    assert "data:" not in svg[2].attrib["d"]
    with zipfile.ZipFile(build(tmp_path)) as archive:
        assert not any("candidate" in name or "previews" in name for name in archive.namelist())
