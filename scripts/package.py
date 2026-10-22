"""Build and validate the one deterministic installable release artifact."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from custom_components.integrationmanager.const import DOMAIN, MIN_HA_VERSION, VERSION
from custom_components.integrationmanager.files import validate_archive
from custom_components.integrationmanager.version import is_release_version

ROOT = Path(__file__).resolve().parents[1]


def build(output: Path, tag: str | None = None) -> Path:
    source = ROOT / "custom_components" / DOMAIN
    manifest = json.loads((source / "manifest.json").read_text())
    version = manifest["version"]
    if not is_release_version(version) or version != VERSION:
        raise ValueError("Runtime and manifest versions differ")
    if tag is not None and (not is_release_version(tag) or tag != version):
        raise ValueError("Release tag must be X.Y.Z and match the integration manifest version")
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"{DOMAIN}.zip"
    contents = {}
    for file in sorted(source.rglob("*")):
        if file.is_symlink():
            raise ValueError("Symlinks cannot be packaged")
        if file.is_file() and "__pycache__" not in file.parts and file.suffix != ".pyc":
            contents[f"custom_components/{DOMAIN}/{file.relative_to(source).as_posix()}"] = (
                file.read_bytes()
            )
    contents["release.json"] = json.dumps(
        {
            "domain": DOMAIN,
            "version": version,
            "minimum_home_assistant_version": MIN_HA_VERSION,
        },
        sort_keys=True,
    ).encode()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(contents.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    data = target.read_bytes()
    validate_archive(data, DOMAIN, version, MIN_HA_VERSION)
    approved_icon = (ROOT / "approved-icon.png").read_bytes()
    if any(
        contents[f"custom_components/{DOMAIN}/{name}"] != approved_icon
        for name in ("frontend/icon.png", "brand/icon.png")
    ):
        raise ValueError("Official icon must be reused unchanged")
    (output / "SHA256SUMS").write_text(f"{hashlib.sha256(data).hexdigest()}  {target.name}\n")
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag")
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    arguments = parser.parse_args()
    print(build(arguments.output, arguments.tag))
