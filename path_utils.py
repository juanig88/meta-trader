"""Filesystem helpers: resolve user-influenced paths safely under a project root."""

from __future__ import annotations

from pathlib import Path


def safe_under_root(name: str, *, root: Path, allowed_names: set[str] | None = None) -> Path:
    """
    Resolve `name` as a single path segment under `root`.

    - Rejects empty names, absolute paths, parent segments, and separators.
    - If `allowed_names` is set, `name` must be exactly one of those strings (typical: basenames).
    """
    s = (name or "").strip()
    if not s or s != Path(s).name:
        raise ValueError("invalid path name")
    if ".." in Path(s).parts:
        raise ValueError("invalid path name")
    if allowed_names is not None and s not in allowed_names:
        raise ValueError("path not allowed")
    root_r = root.resolve()
    out = (root_r / s).resolve()
    if root_r != out and root_r not in out.parents:
        raise ValueError("path escapes root")
    return out
