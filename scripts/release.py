"""Validated-main RC publication and same-artifact immutable release promotion."""

import argparse
import ast
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


REPOSITORY = "THEFLDR/HA-IntegrationManager"
ROOT = Path(__file__).resolve().parents[1]


def valid_run(run, sha, workflow_id):
    return (
        run.get("workflow_id") == workflow_id
        and run.get("name") == "Validation"
        and run.get("path") == ".github/workflows/ci.yml"
        and run.get("repository", {}).get("full_name") == REPOSITORY
        and run.get("head_repository", {}).get("full_name") == REPOSITORY
        and run.get("head_sha") == sha
        and run.get("head_branch") == "main"
        and run.get("event") == "push"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
    )


def verify_main_ci(repo, sha, run_id=None):
    workflow = api(f"repos/{repo}/actions/workflows/ci.yml")
    if workflow.get("name") != "Validation" or workflow.get("path") != ".github/workflows/ci.yml":
        raise ValueError("Unexpected Validation workflow identity")
    if run_id is not None:
        runs = [api(f"repos/{repo}/actions/runs/{run_id}")]
    else:
        runs = api(
            f"repos/{repo}/actions/workflows/ci.yml/runs?head_sha={sha}&status=completed&per_page=100"
        )["workflow_runs"]
    if not any(valid_run(run, sha, workflow["id"]) for run in runs):
        raise ValueError("Successful Validation on the exact merged main SHA is required")


def release_version():
    source = ROOT / "custom_components/integrationmanager"
    version = json.loads((source / "manifest.json").read_text())["version"]
    assignments = [
        node.value
        for node in ast.parse((source / "const.py").read_text()).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets)
    ]
    if (
        not is_release_version(version)
        or len(assignments) != 1
        or ast.literal_eval(assignments[0]) != version
    ):
        raise ValueError("Runtime and manifest versions must agree in bare X.Y.Z format")
    return version


def prepare():
    """Resolve both event paths before installing tools or building any assets."""
    repo = os.environ["GITHUB_REPOSITORY"]
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    if repo != REPOSITORY:
        raise ValueError("Only the owner repository can publish")
    run_id = None
    if os.environ["GITHUB_EVENT_NAME"] == "workflow_run":
        run = event["workflow_run"]
        sha = run["head_sha"]
        run_id = run["id"]
        if event.get("action") != "completed" or not valid_run(run, sha, run["workflow_id"]):
            raise ValueError(
                "Only a successful owner-repository push-to-main Validation may publish"
            )
    elif os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch":
        if os.environ["GITHUB_ACTOR"] != "THEFLDR" or os.environ["GITHUB_REF"] != "refs/heads/main":
            raise ValueError("Manual recovery requires the owner on main")
        sha = event["inputs"]["sha"]
    else:
        raise ValueError("Unsupported publication event")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("An exact full merged SHA is required")
    checkout = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()  # noqa: S603, S607
    if checkout != sha:
        raise ValueError("Checkout must match the exact upstream validated SHA")
    tag = release_version()
    if run_id is None and event["inputs"]["tag"] != tag:
        raise ValueError("Manual release tag must match the manifest version")
    if api(f"repos/{repo}/git/ref/heads/main")["object"]["sha"] != sha:
        raise ValueError("Publication requires current main HEAD; rerun Validation on main")
    verify_main_ci(repo, sha, run_id)
    publish = not published(repo, tag, sha)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        output.write(f"tag={tag}\nsha={sha}\npublish={str(publish).lower()}\n")
    print(
        f"{tag} at {sha}: {'ready to package and publish RC' if publish else 'already published; no-op'}"
    )


def published(repo, tag, sha):
    """Only an intact matching immutable release permits a no-op."""
    # Include drafts: a failed publication must be recovered explicitly by the
    # owner, never overwritten. Paginate so old duplicate tags cannot be missed.
    releases = gh("api", "--paginate", "--jq", ".[] | @json", f"repos/{repo}/releases?per_page=100")
    releases = [json.loads(line) for line in releases.splitlines()]
    matches = [item for item in releases if item.get("tag_name") == tag]
    target = tag_target(repo, tag, missing_ok=True)
    if not matches:
        if target is not None:
            raise ValueError("Tag exists without a published release; owner recovery required")
        return False
    if len(matches) != 1:
        raise ValueError("Conflicting releases for this version; owner recovery required")
    release = matches[0]
    if (
        release.get("draft") is not False
        or release.get("immutable") is not True
        or not isinstance(release.get("prerelease"), bool)
        or target is None
    ):
        raise ValueError("Existing release is draft, partial or mutable; owner recovery required")
    if target != sha:
        comparison = api(f"repos/{repo}/compare/{target}...{sha}")
        files = comparison.get("files")
        # A doc-only main push keeps the already released version. Accept only
        # a proven ancestor with a complete, documentation-only diff. Code
        # changes without a version bump need an explicit owner correction.
        if (
            comparison.get("status") != "ahead"
            or not isinstance(files, list)
            or len(files) >= 300
            or any(
                not documentation(file["filename"])
                or ("previous_filename" in file and not documentation(file["previous_filename"]))
                for file in files
            )
        ):
            raise ValueError("Existing release tag conflicts with the validated SHA/version")
        verify_main_ci(repo, target)
    assets = release.get("assets", [])
    if (
        len(assets) != 2
        or {asset["name"] for asset in assets} != {"integrationmanager.zip", "SHA256SUMS"}
        or any(
            not re.fullmatch(r"sha256:[0-9a-f]{64}", asset.get("digest", ""))
            or asset.get("size", 0) <= 0
            for asset in assets
        )
    ):
        raise ValueError(
            "Existing release has incomplete artifacts/digests; owner recovery required"
        )
    package = next(asset for asset in assets if asset["name"] == "integrationmanager.zip")
    checksum = next(asset for asset in assets if asset["name"] == "SHA256SUMS")
    data = gh(
        "api",
        f"repos/{repo}/releases/assets/{checksum['id']}",
        "-H",
        "Accept: application/octet-stream",
    )
    expected = f"{package['digest'].removeprefix('sha256:')}  integrationmanager.zip\n"
    if (
        data != expected
        or len(data.encode()) != checksum["size"]
        or f"sha256:{hashlib.sha256(data.encode()).hexdigest()}" != checksum["digest"]
    ):
        raise ValueError("Existing release checksum does not match immutable artifact digests")
    print(f"Release {tag} already published; preserving release and assets unchanged")
    return True


def documentation(path):
    return path.endswith(".md") or path.startswith(("docs/", ".github/ISSUE_TEMPLATE/"))


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
        verify_main_ci(repo, sha)
        if published(repo, tag, sha):
            return
        # Publication may have waited behind another run or API requests.
        # Fail if main advanced since the first check, before creating a tag.
        if api(f"repos/{repo}/git/ref/heads/main")["object"]["sha"] != sha:
            raise ValueError("Publication requires current main HEAD; rerun Validation on main")
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
            "Release Candidate. Requires Home Assistant 2025.12 or later. Restart Home Assistant after installation, manually or through Repairs (Fix issue, then confirm).",
            "--draft",
            "--prerelease",
        )
        gh("release", "edit", tag, "--repo", repo, "--draft=false", "--prerelease")
        release = api(f"repos/{repo}/releases/tags/{tag}")
        if (
            release.get("tag_name") != tag
            or not release.get("immutable")
            or release.get("prerelease") is not True
            or release.get("draft") is not False
        ):
            raise ValueError(
                "GitHub did not confirm immutable prerelease publication; owner action required"
            )
        if tag_target(repo, tag) != sha:
            raise ValueError("Published release tag does not match the validated SHA")
        package_data = Path("dist/integrationmanager.zip").read_bytes()
        verify_asset(release, package_data)
        checksums = [a for a in release["assets"] if a["name"] == "SHA256SUMS"]
        checksum_data = Path("dist/SHA256SUMS").read_bytes()
        if (
            checksum_data
            != f"{hashlib.sha256(package_data).hexdigest()}  integrationmanager.zip\n".encode()
            or len(release["assets"]) != 2
            or len(checksums) != 1
            or checksums[0].get("digest") != f"sha256:{hashlib.sha256(checksum_data).hexdigest()}"
            or checksums[0].get("size") != len(checksum_data)
        ):
            raise ValueError("Published checksum does not match the tested package")
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
    if (
        len(assets) != 1
        or assets[0].get("digest") != f"sha256:{hashlib.sha256(data).hexdigest()}"
        or assets[0].get("size") != len(data)
    ):
        raise ValueError("GitHub artifact digest does not match the tested package")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "publish", "promote"])
    parser.add_argument("tag", nargs="?")
    parser.add_argument("sha", nargs="?")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    else:
        if args.tag is None or args.sha is None:
            parser.error("publish/promote require tag and SHA")
        main(args.action, args.tag, args.sha)
