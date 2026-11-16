"""Link-aware native payload accounting and an optional duplicate-content audit.

Run ``python -m native_build.artifact_size PATH --duplicates`` against a frozen
runtime or platform bundle. Sizes describe file payloads, not compressed downloads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
from collections import defaultdict
from pathlib import Path
from typing import Any

SIZE_ACCOUNTING = "regular-file-bytes-excluding-symlinks"


def _files(root: Path):
    """Yield regular file entries without traversing or dereferencing symlinks."""
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        if stat.S_ISREG(metadata.st_mode):
            yield path, metadata


def tree_size(root: Path) -> int:
    """Count each regular file path once; file/directory symlinks contribute zero."""
    return sum(metadata.st_size for _path, metadata in _files(root))


def size_breakdown(root: Path) -> dict[str, int]:
    """Group runtime files by package, with top-level resources kept separate."""
    groups: dict[str, int] = defaultdict(int)
    for path, metadata in _files(root):
        relative = path.relative_to(root)
        depth = relative.parts.index("_internal") + 2 if "_internal" in relative.parts else 1
        groups[Path(*relative.parts[:depth]).as_posix()] += metadata.st_size
    return dict(sorted(groups.items()))


def write_sized_inventory(path: Path, inventory: dict[str, Any]) -> Path:
    """Write a runtime inventory whose payload total includes the inventory itself."""
    inventory["size_accounting"] = SIZE_ACCOUNTING
    for _attempt in range(10):
        path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        total = tree_size(path.parent)
        breakdown = size_breakdown(path.parent)
        if (
            inventory.get("runtime_size_bytes") == total
            and inventory.get("size_breakdown_bytes") == breakdown
        ):
            return path
        inventory["runtime_size_bytes"] = total
        inventory["size_breakdown_bytes"] = breakdown
    raise RuntimeError("Runtime size inventory did not stabilize")


def audit_size(root: Path, *, duplicates: bool = False) -> dict[str, Any]:
    """Report payload, unique inode sizes, largest files and optional large duplicates.

    Duplicate hashing reads only groups of same-sized files of at least 1 MiB.
    Hard links and symlinks do not count as redundant copies in that report.
    """
    files = list(_files(root))
    seen: set[tuple[int, int]] = set()
    unique = []
    for path, metadata in files:
        identity = (metadata.st_dev, metadata.st_ino)
        if identity not in seen:
            seen.add(identity)
            unique.append((path, metadata))
    links = [path for path in root.rglob("*") if path.is_symlink()]
    result = {
        "size_accounting": SIZE_ACCOUNTING,
        "regular_file_bytes": sum(item.st_size for _path, item in files),
        "unique_file_bytes": sum(item.st_size for _path, item in unique),
        "symlink_bytes": sum(path.lstat().st_size for path in links),
        "file_count": len(files),
        "symlink_count": len(links),
        "size_breakdown_bytes": size_breakdown(root),
        "largest_files": [
            {"path": path.relative_to(root).as_posix(), "size_bytes": metadata.st_size}
            for path, metadata in sorted(files, key=lambda item: item[1].st_size, reverse=True)[:20]
        ],
    }
    if all(hasattr(item, "st_blocks") for _path, item in unique):
        result["allocated_file_bytes"] = sum(item.st_blocks * 512 for _path, item in unique)
    if duplicates:
        by_size = defaultdict(list)
        for path, metadata in unique:
            if metadata.st_size >= 1024 * 1024:
                by_size[metadata.st_size].append(path)
        groups = []
        for size, paths in sorted(by_size.items(), reverse=True):
            if len(paths) < 2:
                continue
            by_hash = defaultdict(list)
            for path in paths:
                with path.open("rb") as source:
                    digest = hashlib.file_digest(source, "sha256").hexdigest()
                by_hash[digest].append(path.relative_to(root).as_posix())
            for digest, matches in sorted(by_hash.items()):
                if len(matches) > 1:
                    groups.append(
                        {
                            "sha256": digest,
                            "paths": matches,
                            "redundant_bytes": size * (len(matches) - 1),
                        }
                    )
        result["duplicate_files"] = groups
        result["duplicate_bytes"] = sum(group["redundant_bytes"] for group in groups)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--duplicates", action="store_true")
    arguments = parser.parse_args()
    if not arguments.root.is_dir():
        parser.error("root must be a runtime or bundle directory")
    print(json.dumps(audit_size(arguments.root, duplicates=arguments.duplicates), indent=2))


if __name__ == "__main__":
    main()
