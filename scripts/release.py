"""Owner-triggered publication and same-artifact immutable release promotion."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from custom_components.integrationmanager.version import is_release_version


def gh(*arguments):
    executable = shutil.which("gh")
    if executable is None:
        raise RuntimeError("GitHub CLI is required")
    return subprocess.check_output(  # noqa: S603
        [executable, *arguments], text=True, stderr=subprocess.PIPE
    )


def api(path, *arguments):
    return json.loads(gh("api", path, *arguments))


def verify_main_ci(repo, sha):
    result = api(
        f"repos/{repo}/actions/workflows/ci.yml/runs?head_sha={sha}&status=completed&per_page=100"
    )
    if not any(
        run.get("head_sha") == sha
        and run.get("head_branch") == "main"
        and run.get("event") == "push"
        and run.get("conclusion") == "success"
        for run in result["workflow_runs"]
    ):
        raise ValueError("Successful CI on the exact merged main SHA is required")


def tag_target(repo, tag, missing_ok=False):
    try:
        ref = api(f"repos/{repo}/git/ref/tags/{tag}")["object"]
    except subprocess.CalledProcessError as err:
        # gh can emit no stdout on API errors. Only an explicit HTTP 404 is
        # absence; authentication, transport and server failures must abort.
        if missing_ok and re.search(r"\(HTTP 404\)", err.stderr or ""):
            return None
        raise
    if ref["type"] == "tag":
        ref = api(f"repos/{repo}/git/tags/{ref['sha']}")["object"]
    if ref["type"] != "commit":
        raise ValueError("Release tag must resolve to a commit")
    return ref["sha"]


def main(action, tag, sha):
    if not is_release_version(tag) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("An exact X.Y.Z tag and full merged SHA are required")
    repo = os.environ["GITHUB_REPOSITORY"]
    if repo != "THEFLDR/HA-IntegrationManager":
        raise ValueError("Only the owner repository can publish")
    head = api(f"repos/{repo}/git/ref/heads/main")["object"]["sha"]
    if head != sha:
        if action == "publish" or api(f"repos/{repo}/compare/{sha}...{head}")["status"] != "ahead":
            raise ValueError(
                "The selected SHA must be merged into main (current main HEAD for publication)"
            )
    if action == "publish":
        # Create a draft, attach artifacts, then publish once. Immutability is
        # applied by GitHub on publication, so assets are never replaced later.
        releases = api(f"repos/{repo}/releases?per_page=100")
        if any(r["tag_name"] == tag for r in releases):
            raise ValueError("Release already exists; never replace or rebuild it")
        verify_main_ci(repo, sha)
        existing_target = tag_target(repo, tag, missing_ok=True)
        if existing_target is not None and existing_target != sha:
            raise ValueError("Existing release tag does not match the validated merged SHA")
        gh(
            "release",
            "create",
            tag,
            "dist/integrationmanager.zip",
            "dist/SHA256SUMS",
            "--repo",
            repo,
            "--target",
            sha,
            "--title",
            tag,
            "--notes",
            "Release Candidate. Requires Home Assistant 2025.12 or later. Restart HA manually after installation.",
            "--draft",
            "--prerelease",
        )
        gh("release", "edit", tag, "--repo", repo, "--draft=false", "--prerelease")
        release = api(f"repos/{repo}/releases/tags/{tag}")
        if (
            release.get("tag_name") != tag
            or not release.get("immutable")
            or not release["prerelease"]
        ):
            raise ValueError(
                "GitHub did not confirm immutable prerelease publication; owner action required"
            )
        verify_asset(release, Path("dist/integrationmanager.zip").read_bytes())
    elif action == "promote":
        before = api(f"repos/{repo}/releases/tags/{tag}")
        if before.get("tag_name") != tag or not before.get("immutable") or not before["prerelease"]:
            raise ValueError("Promotion requires an existing immutable prerelease")
        if tag_target(repo, tag) != sha:
            raise ValueError("Release tag does not point to the validated merged SHA")
        verify_main_ci(repo, sha)
        # PATCH only release metadata. A GitHub refusal is a blocker, not a
        # reason to delete/recreate a release or touch its immutable assets.
        after = api(
            f"repos/{repo}/releases/{before['id']}",
            "--method",
            "PATCH",
            "-F",
            "prerelease=false",
            "-f",
            "make_latest=true",
        )
        if (
            after["id"] != before["id"]
            or after["tag_name"] != tag
            or after["prerelease"]
            or not after.get("immutable")
        ):
            raise ValueError("GitHub did not confirm same-release immutable promotion")

        def identity(r):
            return sorted((a["id"], a["name"], a["size"], a.get("digest")) for a in r["assets"])

        if identity(before) != identity(after):
            raise ValueError("Release assets changed unexpectedly")


def verify_asset(release, data):
    assets = [a for a in release["assets"] if a["name"] == "integrationmanager.zip"]
    if len(assets) != 1 or assets[0].get("digest") != f"sha256:{hashlib.sha256(data).hexdigest()}":
        raise ValueError("GitHub artifact digest does not match the tested package")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["publish", "promote"])
    parser.add_argument("tag")
    parser.add_argument("sha")
    args = parser.parse_args()
    main(args.action, args.tag, args.sha)
