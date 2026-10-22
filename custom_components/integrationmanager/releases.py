"""Public tagged release selection and immutable artifact identities."""

import re
from dataclasses import dataclass

from packaging.version import InvalidVersion, Version

from .const import MAX_ARCHIVE, RC, STABLE

TAG = re.compile(r"v?\d+\.\d+\.\d+(?:(?:-?rc\.?)([1-9]\d*))?\Z", re.I)


@dataclass(frozen=True)
class Release:
    tag: str
    version: str
    asset_id: int
    digest: str
    size: int
    url: str
    notes: str


def select_release(items: list, domain: str, channel: str) -> Release | None:
    """Stable excludes prereleases; RC accepts final and recognized RC tags.

    A final version marked GitHub prerelease is the initial RC publication,
    allowing promotion of that same immutable tag and asset to Stable.
    """
    if channel not in (STABLE, RC):
        raise ValueError("Invalid release channel")
    candidates = []
    for item in items:
        if (
            not isinstance(item, dict)
            or item.get("draft") is not False
            or item.get("immutable") is not True
            or type(item.get("prerelease")) is not bool
        ):
            continue
        tag = item.get("tag_name", "")
        if not isinstance(tag, str) or len(tag) > 100 or not TAG.fullmatch(tag):
            continue
        version = Version(tag)
        if channel == STABLE and (item.get("prerelease") or version.is_prerelease):
            continue
        raw_assets = item.get("assets", [])
        if not isinstance(raw_assets, list):
            continue
        assets = [a for a in raw_assets if isinstance(a, dict) and a.get("name") == f"{domain}.zip"]
        if len(assets) != 1:
            continue
        asset = assets[0]
        digest, size, asset_id = (
            asset.get("digest", ""),
            asset.get("size", 0),
            asset.get("id", 0),
        )
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            or type(size) is not int
            or not 0 < size <= MAX_ARCHIVE
            or type(asset_id) is not int
            or asset_id <= 0
        ):
            continue
        candidates.append(
            Release(
                tag,
                str(version),
                asset_id,
                digest.removeprefix("sha256:"),
                size,
                "",  # A repository/tag URL is constructed locally, never trusted from JSON.
                str(item.get("body") or "")[:20000],
            )
        )
    return max(candidates, key=lambda r: Version(r.version), default=None)


def is_newer(candidate: str, installed: str) -> bool:
    try:
        return Version(candidate) > Version(installed)
    except InvalidVersion:
        return False
