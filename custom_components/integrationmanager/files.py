"""Bounded archive validation and recoverable filesystem transactions."""

import io
import json
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from packaging.version import Version

from .catalog import DOMAIN_PATTERN
from .const import MAX_ARCHIVE, MAX_FILES, MAX_UNPACKED


class InstallError(Exception):
    """An artifact or filesystem operation was rejected."""


def validate_archive(data: bytes, domain: str, version: str, ha_version: str) -> dict:
    """Validate everything in memory before writing anything to disk."""
    if not DOMAIN_PATTERN.fullmatch(domain) or len(data) > MAX_ARCHIVE:
        raise InstallError("Invalid domain or oversized archive")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if not members or len(members) > MAX_FILES:
                raise InstallError("Archive file count exceeds limit")
            files, total, seen = {}, 0, set()
            prefix = f"custom_components/{domain}/"
            for member in members:
                name = member.filename
                path = PurePosixPath(name)
                mode = member.external_attr >> 16
                if (
                    "\\" in name
                    or "\x00" in name
                    or path.is_absolute()
                    or any(p in ("", ".", "..") for p in name.rstrip("/").split("/"))
                    or ":" in name
                    or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or member.flag_bits & 1
                    or name.casefold() in seen
                ):
                    raise InstallError("Unsafe archive path, type, encryption or duplicate")
                seen.add(name.casefold())
                if member.is_dir():
                    if name.rstrip("/") not in {
                        "custom_components",
                        f"custom_components/{domain}",
                    } and not name.startswith(prefix):
                        raise InstallError("Archive contains unrelated directories")
                    continue
                if name != "release.json" and not name.startswith(prefix):
                    raise InstallError("Archive contains unrelated files")
                total += member.file_size
                if total > MAX_UNPACKED or member.file_size > MAX_ARCHIVE:
                    raise InstallError("Expanded archive exceeds size limits")
                if member.file_size / max(member.compress_size, 1) > 200:
                    raise InstallError("Suspicious archive compression ratio")
                files[name] = archive.read(member)
            manifest = json.loads(files[f"{prefix}manifest.json"])
            metadata = json.loads(files["release.json"])
            if (
                manifest.get("domain") != domain
                or Version(manifest["version"]) != Version(version)
                or metadata["domain"] != domain
                or Version(metadata["version"]) != Version(version)
                or Version(ha_version) < Version(metadata["minimum_home_assistant_version"])
                or f"{prefix}__init__.py" not in files
            ):
                raise InstallError(
                    "Artifact identity, version or Home Assistant compatibility mismatch"
                )
            return {
                name[len(prefix) :]: value
                for name, value in files.items()
                if name.startswith(prefix)
            }
    except (
        KeyError,
        TypeError,
        ValueError,
        AttributeError,
        zipfile.BadZipFile,
        RuntimeError,
        OSError,
    ) as err:
        raise InstallError("Invalid integration release archive") from err


class FileStore:
    def __init__(self, config_dir: str):
        self.config_dir = Path(config_dir)
        self.root = self.config_dir / "custom_components"

    def destination(self, domain: str) -> Path:
        if not DOMAIN_PATTERN.fullmatch(domain):
            raise InstallError("Invalid integration domain")
        if self.root.is_symlink() or self.root.resolve().parent != self.config_dir.resolve():
            raise InstallError("Custom integration directory must stay inside HA configuration")
        dest = self.root / domain
        if dest.is_symlink():
            raise InstallError("Refusing a symlink destination")
        if dest.exists() and (not dest.is_dir() or any(p.is_symlink() for p in dest.rglob("*"))):
            raise InstallError("Refusing an unsafe integration directory")
        return dest

    def install(self, domain: str, files: dict, replace: bool = False):
        dest = self.destination(domain)
        if dest.exists() and not replace:
            raise InstallError("Already installed; use the integration's native Update entity")
        self.root.mkdir(exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix=".integrationmanager-", dir=self.root))
        backup = stage / "previous"
        prepared = stage / "prepared"
        prepared.mkdir()
        try:
            for name, content in files.items():
                path = prepared / name
                if path.resolve().is_relative_to(prepared.resolve()) is False:
                    raise InstallError("Unsafe staged destination")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            if dest.exists():
                os.replace(dest, backup)
            try:
                os.replace(prepared, dest)
            except OSError:
                if backup.exists():
                    os.replace(backup, dest)
                raise
        except OSError as err:
            # Keep a recoverable backup if rollback itself failed.
            raise InstallError(f"Installation failed; inspect {stage.name} for recovery") from err
        finally:
            if not backup.exists() or dest.exists():
                shutil.rmtree(stage, ignore_errors=True)

    def remove(self, domain: str):
        dest = self.destination(domain)
        if not dest.exists():
            raise InstallError("Integration files are not installed")
        stage = Path(tempfile.mkdtemp(prefix=".integrationmanager-remove-", dir=self.root))
        try:
            os.replace(dest, stage / domain)
        except OSError as err:
            stage.rmdir()
            raise InstallError("Unable to remove integration files") from err
        # Only files in the validated integration directory, never .storage or data.
        shutil.rmtree(stage, ignore_errors=True)
