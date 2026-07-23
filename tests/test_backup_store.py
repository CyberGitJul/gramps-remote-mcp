import os
import re

import pytest

import backup_store


def test_resolve_export_path_default_is_timestamped(tmp_path):
    path = backup_store.resolve_export_path(str(tmp_path))
    assert os.path.dirname(path) == os.path.realpath(str(tmp_path))
    assert re.match(r"gramps-export-\d{8}-\d{6}\.gramps$", os.path.basename(path))


def test_resolve_export_path_custom_filename(tmp_path):
    path = backup_store.resolve_export_path(str(tmp_path), "my-backup.gramps")
    assert path == os.path.realpath(os.path.join(str(tmp_path), "my-backup.gramps"))


def test_resolve_export_path_rejects_traversal(tmp_path):
    with pytest.raises(ValueError):
        backup_store.resolve_export_path(str(tmp_path), "../evil.gramps")


def test_resolve_import_path_rejects_absolute_escape(tmp_path):
    with pytest.raises(ValueError):
        backup_store.resolve_import_path(str(tmp_path), "/etc/passwd")


def test_resolve_import_path_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_store.resolve_import_path(str(tmp_path), "nope.gramps")


def test_resolve_import_path_accepts_valid(tmp_path):
    p = tmp_path / "good.gramps"
    p.write_bytes(b"x")
    path = backup_store.resolve_import_path(str(tmp_path), "good.gramps")
    assert path == os.path.realpath(str(p))


def test_read_write_roundtrip(tmp_path):
    path = os.path.join(str(tmp_path), "x.gramps")
    backup_store.write_bytes(path, b"GRAMPSDATA")
    assert backup_store.read_bytes(path) == b"GRAMPSDATA"
