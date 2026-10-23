"""Automatic and recovery publication guards, without any real release writes."""

import hashlib
import json
import subprocess
from unittest.mock import Mock

import pytest

from scripts import release

REPO = release.REPOSITORY
SHA = "a" * 40
OLD = "b" * 40
TAG = "0.1.5"
WORKFLOW = {"id": 5, "name": "Validation", "path": ".github/workflows/ci.yml"}


def run(**changes):
    return {
        **WORKFLOW,
        "id": 10,
        "workflow_id": 5,
        "repository": {"full_name": REPO},
        "head_repository": {"full_name": REPO},
        "head_sha": SHA,
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        **changes,
    }


def artifact(data=b"tested artifact"):
    checksum = f"{hashlib.sha256(data).hexdigest()}  integrationmanager.zip\n"
    assets = [
        {
            "id": 20,
            "name": "integrationmanager.zip",
            "size": len(data),
            "digest": f"sha256:{hashlib.sha256(data).hexdigest()}",
        },
        {
            "id": 21,
            "name": "SHA256SUMS",
            "size": len(checksum),
            "digest": f"sha256:{hashlib.sha256(checksum.encode()).hexdigest()}",
        },
    ]
    return {
        "id": 1,
        "tag_name": TAG,
        "immutable": True,
        "draft": False,
        "prerelease": True,
        "assets": assets,
    }, checksum


@pytest.fixture
def event_env(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"action": "completed", "workflow_run": run()}))
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_run")
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("GITHUB_ACTOR", "THEFLDR")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setattr(release.subprocess, "check_output", Mock(return_value=SHA + "\n"))
    monkeypatch.setattr(release, "published", Mock(return_value=False))
    monkeypatch.setattr(
        release, "api", Mock(side_effect=[{"object": {"sha": SHA}}, WORKFLOW, run()])
    )
    return event, output


def test_auto_uses_verified_run_sha_and_derives_version(event_env, monkeypatch):
    _, output = event_env
    monkeypatch.setenv("GITHUB_SHA", OLD)  # workflow_run's default SHA must never be used.
    release.prepare()
    assert output.read_text() == f"tag={TAG}\nsha={SHA}\npublish=true\n"
    assert release.api.call_args.args == (f"repos/{REPO}/actions/runs/10",)
    release.published.assert_called_once_with(REPO, TAG, SHA)


@pytest.mark.parametrize(
    "changes",
    [
        {"event": "pull_request"},
        {"head_branch": "CODEX/0.1.5"},
        {"conclusion": "failure"},
        {"conclusion": "cancelled"},
        {"status": "in_progress"},
        {"name": "Impostor"},
        {"path": ".github/workflows/other.yml"},
        {"repository": {"full_name": "fork/repo"}},
        {"head_repository": {"full_name": "fork/repo"}},
        {"head_sha": "main"},
    ],
)
def test_rejects_untrusted_upstream_event(event_env, changes):
    event, output = event_env
    event.write_text(json.dumps({"action": "completed", "workflow_run": run(**changes)}))
    with pytest.raises(ValueError):
        release.prepare()
    assert not output.exists()
    release.api.assert_not_called()
    release.published.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"workflow_id": 99},
        {"event": "pull_request"},
        {"head_sha": OLD},
        {"conclusion": "failure"},
        {"name": "Other"},
        {"head_repository": {"full_name": "fork/repo"}},
        {"path": ".github/workflows/other.yml"},
        {"status": "in_progress"},
    ],
)
def test_api_run_must_independently_confirm_upstream(event_env, monkeypatch, changes):
    monkeypatch.setattr(
        release, "api", Mock(side_effect=[{"object": {"sha": SHA}}, WORKFLOW, run(**changes)])
    )
    with pytest.raises(ValueError, match="exact merged main SHA"):
        release.prepare()
    release.published.assert_not_called()


def test_workflow_identity_must_match_registered_ci(event_env, monkeypatch):
    monkeypatch.setattr(
        release, "api", Mock(side_effect=[{"object": {"sha": SHA}}, {**WORKFLOW, "name": "Other"}])
    )
    with pytest.raises(ValueError, match="identity"):
        release.prepare()


@pytest.mark.parametrize(
    "reason",
    ["main advanced", "wrong checkout", "wrong repository", "unsupported event", "not completed"],
)
def test_prepare_rejects_unexpected_context(event_env, monkeypatch, reason):
    event, output = event_env
    if reason == "main advanced":
        monkeypatch.setattr(release, "api", Mock(return_value={"object": {"sha": OLD}}))
    elif reason == "wrong checkout":
        release.subprocess.check_output.return_value = OLD
    elif reason == "wrong repository":
        monkeypatch.setenv("GITHUB_REPOSITORY", "fork/repo")
    elif reason == "unsupported event":
        monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    else:
        event.write_text(json.dumps({"action": "requested", "workflow_run": run()}))
    with pytest.raises(ValueError):
        release.prepare()
    assert not output.exists()
    release.published.assert_not_called()


@pytest.mark.parametrize(
    "actor, ref, tag, sha",
    [
        ("THEFLDR", "refs/heads/main", TAG, SHA),
        ("other", "refs/heads/main", TAG, SHA),
        ("THEFLDR", "refs/heads/CODEX/0.1.5", TAG, SHA),
        ("THEFLDR", "refs/heads/main", "0.1.4", SHA),
        ("THEFLDR", "refs/heads/main", TAG, "main"),
    ],
)
def test_owner_only_manual_recovery(event_env, monkeypatch, actor, ref, tag, sha):
    event, output = event_env
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_ACTOR", actor)
    monkeypatch.setenv("GITHUB_REF", ref)
    event.write_text(json.dumps({"inputs": {"tag": tag, "sha": sha}}))
    monkeypatch.setattr(
        release,
        "api",
        Mock(side_effect=[{"object": {"sha": SHA}}, WORKFLOW, {"workflow_runs": [run()]}]),
    )
    if actor == "THEFLDR" and ref == "refs/heads/main" and tag == TAG and sha == SHA:
        release.prepare()
        assert "publish=true" in output.read_text()
    else:
        with pytest.raises(ValueError):
            release.prepare()
        assert not output.exists()


@pytest.mark.parametrize("manifest, runtime", [(TAG, "0.1.4"), ("v0.1.5", "v0.1.5"), (TAG, None)])
def test_prepare_rejects_mismatched_or_invalid_versions(
    event_env, tmp_path, monkeypatch, manifest, runtime
):
    source = tmp_path / "custom_components/integrationmanager"
    source.mkdir(parents=True)
    (source / "manifest.json").write_text(json.dumps({"version": manifest}))
    (source / "const.py").write_text(
        f"VERSION = {runtime!r}\n" if runtime else "NAME = 'Manager'\n"
    )
    monkeypatch.setattr(release, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="versions must agree"):
        release.prepare()
    release.api.assert_not_called()


def test_repeated_prepare_skips_package_and_publication(event_env):
    _, output = event_env
    release.published.return_value = True
    release.prepare()
    assert "publish=false" in output.read_text()


@pytest.fixture
def existing(monkeypatch):
    before, checksum = artifact()
    monkeypatch.setattr(release, "gh", Mock(side_effect=[json.dumps(before), checksum]))
    monkeypatch.setattr(release, "tag_target", Mock(return_value=SHA))
    return before, checksum


@pytest.mark.parametrize("stable", [False, True])
def test_intact_release_is_noop_even_after_promotion(existing, monkeypatch, stable):
    before, checksum = existing
    before["prerelease"] = not stable
    release.gh.side_effect = [json.dumps(before), checksum]
    assert release.published(REPO, TAG, SHA)
    assert all(call.args[0] == "api" for call in release.gh.call_args_list)


def test_paginated_draft_is_not_overlooked(existing):
    before, _ = existing
    before["draft"] = True
    release.gh.side_effect = [json.dumps(before)]
    with pytest.raises(ValueError, match="draft, partial"):
        release.published(REPO, TAG, SHA)
    assert "--paginate" in release.gh.call_args.args


@pytest.mark.parametrize(
    "change",
    [
        "draft",
        "mutable",
        "missing tag",
        "missing checksum",
        "bad digest",
        "zero size",
        "bad checksum",
        "missing asset",
        "duplicate release",
    ],
)
def test_existing_partial_or_conflicting_releases_fail(existing, change):
    before, checksum = existing
    releases = [before]
    if change == "draft":
        before["draft"] = True
    elif change == "mutable":
        before["immutable"] = False
    elif change == "missing tag":
        release.tag_target.return_value = None
    elif change in ("missing checksum", "missing asset"):
        before["assets"].pop(1 if change == "missing checksum" else 0)
    elif change == "bad digest":
        before["assets"][0]["digest"] = "sha256:bad"
    elif change == "zero size":
        before["assets"][0]["size"] = 0
    elif change == "bad checksum":
        checksum = "unrelated checksum"
    elif change == "duplicate release":
        releases.append(before)
    release.gh.side_effect = ["\n".join(json.dumps(item) for item in releases), checksum]
    with pytest.raises(ValueError):
        release.published(REPO, TAG, SHA)


@pytest.mark.parametrize(
    "files, status, accepted",
    [
        ([{"filename": "README.md"}, {"filename": "docs/guide.txt"}], "ahead", True),
        ([], "ahead", True),
        (
            [{"filename": "README.md", "previous_filename": "custom_components/old.py"}],
            "ahead",
            False,
        ),
        ([{"filename": "custom_components/integrationmanager/update.py"}], "ahead", False),
        ([{"filename": "README.md"}], "diverged", False),
        ([{"filename": "README.md"}] * 300, "ahead", False),
        (None, "ahead", False),
    ],
)
def test_documentation_only_main_reuses_published_version(
    existing, monkeypatch, files, status, accepted
):
    release.tag_target.return_value = OLD
    monkeypatch.setattr(release, "api", Mock(return_value={"status": status, "files": files}))
    verify = Mock()
    monkeypatch.setattr(release, "verify_main_ci", verify)
    if accepted:
        assert release.published(REPO, TAG, SHA)
        verify.assert_called_once_with(REPO, OLD)
    else:
        with pytest.raises(ValueError, match="conflicts"):
            release.published(REPO, TAG, SHA)
        verify.assert_not_called()


def test_orphan_tag_is_not_silently_republished(monkeypatch):
    monkeypatch.setattr(release, "gh", Mock(return_value=""))
    monkeypatch.setattr(release, "tag_target", Mock(return_value=SHA))
    with pytest.raises(ValueError, match="without a published release"):
        release.published(REPO, TAG, SHA)


def test_new_version_requires_no_tag_or_release(monkeypatch):
    monkeypatch.setattr(release, "gh", Mock(return_value=""))
    monkeypatch.setattr(release, "tag_target", Mock(return_value=None))
    assert not release.published(REPO, TAG, SHA)


def test_initial_publication_missing_tag_with_empty_error_stdout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dist").mkdir()
    data = b"tested artifact"
    published, checksum = artifact(data)
    (tmp_path / "dist/integrationmanager.zip").write_bytes(data)
    (tmp_path / "dist/SHA256SUMS").write_text(checksum)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setattr(release, "verify_main_ci", Mock())
    missing = subprocess.CalledProcessError(
        1, ["gh", "api"], output="", stderr="gh: Not Found (HTTP 404)\n"
    )
    gh = Mock(
        side_effect=[
            json.dumps({"object": {"sha": SHA}}),
            "",
            missing,
            json.dumps({"object": {"sha": SHA}}),
            "",
            "",
            json.dumps(published),
            json.dumps({"object": {"sha": SHA, "type": "commit"}}),
        ]
    )
    monkeypatch.setattr(release, "gh", gh)
    release.main("publish", TAG, SHA)
    create, edit = gh.call_args_list[4:6]
    assert create.args[:3] == ("release", "create", TAG)
    assert "--draft" in create.args and "--prerelease" in create.args
    assert create.args[create.args.index("--target") + 1] == SHA
    assert "Repairs (Fix issue, then confirm)" in create.args[create.args.index("--notes") + 1]
    assert edit.args == ("release", "edit", TAG, "--repo", REPO, "--draft=false", "--prerelease")


def test_publish_rechecks_state_after_preparation_for_race(existing, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setattr(release, "api", Mock(return_value={"object": {"sha": SHA}}))
    monkeypatch.setattr(release, "verify_main_ci", Mock())
    release.main("publish", TAG, SHA)
    assert all(call.args[0] == "api" for call in release.gh.call_args_list)


def test_main_advance_between_prepare_and_publish_fails(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setattr(release, "api", Mock(return_value={"object": {"sha": OLD}}))
    write = Mock()
    monkeypatch.setattr(release, "gh", write)
    with pytest.raises(ValueError, match="main HEAD"):
        release.main("publish", TAG, SHA)
    write.assert_not_called()


@pytest.mark.parametrize(
    "change",
    ["wrong tag target", "wrong package digest", "wrong checksum digest", "wrong checksum content"],
)
def test_post_publication_verification_fails_closed(tmp_path, monkeypatch, change):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dist").mkdir()
    data = b"tested artifact"
    published, checksum = artifact(data)
    (tmp_path / "dist/integrationmanager.zip").write_bytes(data)
    (tmp_path / "dist/SHA256SUMS").write_text(
        checksum if change != "wrong checksum content" else "bad"
    )
    if change == "wrong package digest":
        published["assets"][0]["digest"] = "sha256:bad"
    if change == "wrong checksum digest":
        published["assets"][1]["digest"] = "sha256:bad"
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setattr(
        release,
        "api",
        Mock(side_effect=[{"object": {"sha": SHA}}, {"object": {"sha": SHA}}, published]),
    )
    monkeypatch.setattr(release, "published", Mock(return_value=False))
    monkeypatch.setattr(release, "verify_main_ci", Mock())
    monkeypatch.setattr(
        release, "tag_target", Mock(return_value=OLD if change == "wrong tag target" else SHA)
    )
    monkeypatch.setattr(release, "gh", Mock())
    with pytest.raises(ValueError):
        release.main("publish", TAG, SHA)


def test_verify_package_digest_and_size():
    data = b"tested artifact"
    published, _ = artifact(data)
    release.verify_asset(published, data)
    for field, value in [("digest", "sha256:bad"), ("size", 1)]:
        broken, _ = artifact(data)
        broken["assets"][0][field] = value
        with pytest.raises(ValueError, match="digest"):
            release.verify_asset(broken, data)


def test_main_advance_during_preflight_never_creates_tag(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setattr(
        release, "api", Mock(side_effect=[{"object": {"sha": SHA}}, {"object": {"sha": OLD}}])
    )
    monkeypatch.setattr(release, "verify_main_ci", Mock())
    monkeypatch.setattr(release, "published", Mock(return_value=False))
    write = Mock()
    monkeypatch.setattr(release, "gh", write)
    with pytest.raises(ValueError, match="current main HEAD"):
        release.main("publish", TAG, SHA)
    write.assert_not_called()
