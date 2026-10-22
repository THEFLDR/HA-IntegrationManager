import io
import os
import shutil
import stat
import zipfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from custom_components.integrationmanager.files import FileStore, InstallError, validate_archive


def test_valid_archive(archive):
    files = validate_archive(archive(), "sample", "0.2.0", "2025.12.5")
    assert set(files) == {"manifest.json", "__init__.py"}


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "custom_components/sample/../bad",
        "custom_components/sample/a\\b",
        "custom_components/sample/a:b",
        "custom_components/other/manifest.json",
        ".storage/data",
        "custom_components/sample//empty",
        "custom_components/sample/./dot",
    ],
)
def test_path_attacks(archive, name):
    with pytest.raises(InstallError):
        validate_archive(archive(extra={name: "attack"}), "sample", "0.2.0", "2025.12.5")


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFBLK])
def test_special_files_rejected(archive, mode):
    stream = io.BytesIO(archive())
    with zipfile.ZipFile(stream, "a") as output:
        member = zipfile.ZipInfo("custom_components/sample/link")
        member.external_attr = (mode | 0o777) << 16
        output.writestr(member, "../../secrets.yaml")
    with pytest.raises(InstallError):
        validate_archive(stream.getvalue(), "sample", "0.2.0", "2025.12.5")


@pytest.mark.parametrize(
    "extra",
    [
        {"custom_components/sample/MANIFEST.JSON": "{}"},
        {"release.json": "[]"},
        {"release.json": "broken"},
        {"custom_components/sample/manifest.json": "[]"},
    ],
)
def test_invalid_metadata(archive, extra):
    with pytest.raises(InstallError):
        validate_archive(archive(extra=extra), "sample", "0.2.0", "2025.12.5")


def test_identity_compatibility_and_corruption(archive):
    for data, domain, version, ha in [
        (archive(), "other", "0.2.0", "2025.12.5"),
        (archive(), "sample", "0.3.0", "2025.12.5"),
        (archive(), "sample", "0.2.0", "2025.1.0"),
        (b"bad zip", "sample", "0.2.0", "2025.12.5"),
        (archive(), "../bad", "0.2.0", "2025.12.5"),
    ]:
        with pytest.raises(InstallError):
            validate_archive(data, domain, version, ha)


def test_archive_bounds(archive, monkeypatch):
    import custom_components.integrationmanager.files as module

    for name, limit in [("MAX_ARCHIVE", 2), ("MAX_FILES", 1), ("MAX_UNPACKED", 2)]:
        with monkeypatch.context() as context:
            context.setattr(module, name, limit)
            with pytest.raises(InstallError):
                validate_archive(archive(), "sample", "0.2.0", "2025.12.5")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("custom_components/sample/bomb", b"0" * 1_000_000)
    with pytest.raises(InstallError, match="compression"):
        validate_archive(stream.getvalue(), "sample", "0.2.0", "2025.12.5")


def test_duplicate_and_empty_archive(archive):
    stream = io.BytesIO(archive())
    with pytest.warns(UserWarning), zipfile.ZipFile(stream, "a") as output:
        output.writestr("release.json", "{}")
    with pytest.raises(InstallError):
        validate_archive(stream.getvalue(), "sample", "0.2.0", "2025.12.5")
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w"):
        pass
    with pytest.raises(InstallError):
        validate_archive(empty.getvalue(), "sample", "0.2.0", "2025.12.5")


def test_install_remove_preserves_data(tmp_path):
    store = FileStore(str(tmp_path))
    (tmp_path / ".storage").mkdir()
    shared = tmp_path / ".storage" / "core.config_entries"
    shared.write_text("preserved")
    store.install("sample", {"__init__.py": b"old"})
    with pytest.raises(InstallError, match="Already installed"):
        store.install("sample", {"__init__.py": b"new"})
    store.install("sample", {"__init__.py": b"new"}, True)
    assert (store.root / "sample" / "__init__.py").read_bytes() == b"new"
    store.remove("sample")
    assert not (store.root / "sample").exists()
    assert shared.read_text() == "preserved"
    with pytest.raises(InstallError, match="not installed"):
        store.remove("sample")


def test_partial_install_rolls_back(tmp_path, monkeypatch):
    store = FileStore(str(tmp_path))
    store.install("sample", {"__init__.py": b"old"})
    replace = os.replace

    def fail_prepared(source, destination):
        if Path(source).name == "prepared":
            raise OSError("disk error")
        replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_prepared)
    with pytest.raises(InstallError):
        store.install("sample", {"__init__.py": b"new"}, True)
    assert (store.root / "sample" / "__init__.py").read_bytes() == b"old"


def test_staging_error_preserves_old_files(tmp_path, monkeypatch):
    store = FileStore(str(tmp_path))
    store.install("sample", {"file": b"old"})
    monkeypatch.setattr(
        Path, "write_bytes", lambda *args: (_ for _ in ()).throw(OSError("full disk"))
    )
    with pytest.raises(InstallError):
        store.install("sample", {"file": b"new"}, True)
    assert (store.root / "sample" / "file").read_bytes() == b"old"


def test_symlink_destinations(tmp_path):
    store = FileStore(str(tmp_path))
    outside = tmp_path / "outside"
    outside.mkdir()
    store.root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(InstallError):
        store.destination("sample")
    store.root.unlink()
    store.root.mkdir()
    (store.root / "sample").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InstallError):
        store.destination("sample")
    with pytest.raises(InstallError):
        store.destination("../escape")


@pytest.mark.parametrize("failure", ["prepare", "write", "backup", "activate"])
def test_install_failures_restore_old_files_and_clean_staging(tmp_path, monkeypatch, failure):
    store = FileStore(str(tmp_path))
    store.install("sample", {"file": b"old"})
    mkdir, write, replace = Path.mkdir, Path.write_bytes, os.replace

    def fail_mkdir(path, *args, **kwargs):
        if failure == "prepare" and path.name == "prepared":
            raise OSError("cannot prepare")
        return mkdir(path, *args, **kwargs)

    def fail_write(path, *args, **kwargs):
        if failure == "write":
            raise OSError("cannot write")
        return write(path, *args, **kwargs)

    def fail_replace(source, destination):
        if (failure == "backup" and Path(source).name == "sample") or (
            failure == "activate" and Path(source).name == "prepared"
        ):
            raise OSError("cannot move")
        return replace(source, destination)

    monkeypatch.setattr(Path, "mkdir", fail_mkdir)
    monkeypatch.setattr(Path, "write_bytes", fail_write)
    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(InstallError) as raised:
        store.install("sample", {"file": b"new"}, True)
    assert not raised.value.files_changed
    assert (store.root / "sample/file").read_bytes() == b"old"
    assert list(store.root.iterdir()) == [store.root / "sample"]


@pytest.mark.parametrize("destination_exists", [False, True])
def test_rollback_failure_always_preserves_backup(tmp_path, monkeypatch, destination_exists):
    store = FileStore(str(tmp_path))
    store.install("sample", {"file": b"old"})
    replace = os.replace

    def fail_activate_and_rollback(source, destination):
        if Path(source).name == "prepared":
            if destination_exists:
                Path(destination).mkdir()
                (Path(destination) / "unexpected").write_bytes(b"external file")
            raise OSError("activation failed")
        if Path(source).name == "previous":
            raise OSError("rollback failed")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_activate_and_rollback)
    with pytest.raises(InstallError, match="rollback failed") as raised:
        store.install("sample", {"file": b"new"}, True)
    stage = next(store.root.glob(".integrationmanager-*"))
    assert (stage / "previous/file").read_bytes() == b"old"
    assert str(stage / "previous") in str(raised.value)
    assert raised.value.files_changed


@pytest.mark.parametrize("operation", ["install", "remove"])
def test_cleanup_failure_reports_recovery_path(tmp_path, monkeypatch, operation):
    store = FileStore(str(tmp_path))
    store.install("sample", {"file": b"old", "other": b"remaining"})

    def partial_delete(stage):
        (stage / ("sample" if operation == "remove" else "previous") / "file").unlink()
        raise OSError("permission denied")

    monkeypatch.setattr(shutil, "rmtree", partial_delete)
    with pytest.raises(InstallError) as raised:
        if operation == "remove":
            store.remove("sample")
        else:
            store.install("sample", {"file": b"new"}, True)
    stage = next(store.root.glob(".integrationmanager-*"))
    assert str(stage) in str(raised.value)
    assert raised.value.files_changed
    assert (stage / ("sample" if operation == "remove" else "previous") / "other").exists()


def test_remove_move_and_cleanup_failure_are_reported(tmp_path, monkeypatch):
    store = FileStore(str(tmp_path))
    store.install("sample", {"file": b"old"})
    monkeypatch.setattr(os, "replace", Mock(side_effect=OSError("move failed")))
    with pytest.raises(InstallError, match="Unable to remove"):
        store.remove("sample")
    assert list(store.root.iterdir()) == [store.root / "sample"]
    monkeypatch.setattr(Path, "rmdir", Mock(side_effect=OSError("cleanup failed")))
    with pytest.raises(InstallError, match="staging cleanup failed") as raised:
        store.remove("sample")
    assert str(next(store.root.glob(".integrationmanager-*"))) in str(raised.value)
    assert (store.root / "sample/file").read_bytes() == b"old"


def test_failed_staging_cleanup_does_not_hide_install_failure(tmp_path, monkeypatch):
    store = FileStore(str(tmp_path))
    store.install("sample", {"file": b"old"})
    monkeypatch.setattr(Path, "write_bytes", Mock(side_effect=OSError("disk full")))
    monkeypatch.setattr(shutil, "rmtree", Mock(side_effect=OSError("permission denied")))
    with pytest.raises(InstallError, match="disk full.*staging cleanup failed") as raised:
        store.install("sample", {"file": b"new"}, True)
    assert not raised.value.files_changed
    assert (store.root / "sample/file").read_bytes() == b"old"
    assert str(next(store.root.glob(".integrationmanager-*"))) in str(raised.value)
