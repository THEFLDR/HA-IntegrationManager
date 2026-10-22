"""Exact project release versions, shared by runtime and release tooling."""

import re

_RELEASE_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


def is_release_version(value: object) -> bool:
    """Accept only X.Y.Z; GitHub metadata carries prerelease status."""
    return (
        isinstance(value, str)
        and len(value) <= 100
        and _RELEASE_VERSION.fullmatch(value) is not None
    )
