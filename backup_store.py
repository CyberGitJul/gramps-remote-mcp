import os
from datetime import datetime


def _safe_join(backup_dir, filename):
    """Resolve `filename` inside `backup_dir`, refusing any path escape."""
    if not filename or "\x00" in filename:
        raise ValueError(f"Invalid filename: {filename!r}")
    base = os.path.realpath(backup_dir)
    candidate = os.path.realpath(os.path.join(base, filename))
    if candidate != base and not candidate.startswith(base + os.sep):
        raise ValueError(f"Path escapes backup directory: {filename!r}")
    return candidate


def resolve_export_path(backup_dir, filename=None, extension="gramps"):
    """Absolute path for an export inside backup_dir. Default name is timestamped."""
    if filename is None:
        filename = f"gramps-export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.{extension}"
    return _safe_join(backup_dir, filename)


def resolve_import_path(backup_dir, filename):
    """Absolute path for an existing import file inside backup_dir."""
    path = _safe_join(backup_dir, filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Import file not found in backup directory: {filename!r}")
    return path


def write_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()
