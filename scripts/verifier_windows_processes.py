"""Read the process identity fields required by the Windows runtime verifier."""

from __future__ import annotations

import json
import subprocess

_INVENTORY_SCRIPT = (
    "$ErrorActionPreference = 'Stop'; "
    "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
    "Get-CimInstance Win32_Process -Property ProcessId,ParentProcessId,CommandLine | "
    "Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
)


def _query(timeout: int) -> str:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _INVENTORY_SCRIPT],
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=timeout,
    )
    return result.stdout


def windows_process_rows() -> list[tuple[int, int, str]]:
    """Get process IDs, parent IDs and command lines with one bounded retry."""
    try:
        output = _query(timeout=15)
    except subprocess.TimeoutExpired:
        # A slow CI runner can stall the first CIM query. Require a complete
        # snapshot from the retry before making any process identity assertions.
        output = _query(timeout=30)

    try:
        raw = json.loads(output.lstrip("\ufeff"))
    except json.JSONDecodeError as error:
        raise RuntimeError("could not decode Windows process inventory") from error
    records = raw if isinstance(raw, list) else [raw]
    return [
        (record["ProcessId"], record["ParentProcessId"], record["CommandLine"])
        for record in records
        if isinstance(record, dict)
        and isinstance(record.get("ProcessId"), int)
        and isinstance(record.get("ParentProcessId"), int)
        and isinstance(record.get("CommandLine"), str)
    ]
