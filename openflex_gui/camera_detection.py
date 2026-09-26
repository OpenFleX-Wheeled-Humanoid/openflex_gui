from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence


_SERIAL_PATTERNS: Sequence[re.Pattern[str]] = (
    re.compile(r"(?:serial(?:\s+number)?|s/n)\s*[:#]\s*([A-Za-z0-9_-]{6,})", re.IGNORECASE),
    re.compile(r"^\s*(?:serial(?:\s+number)?|s/n)\s+([A-Za-z0-9_-]{6,})\s*$", re.IGNORECASE),
)

_SERIAL_VALUE = re.compile(r"^[A-Za-z0-9_-]{6,}$")


def parse_realsense_serials(output: str) -> list[str]:
    """Extract unique RealSense serial numbers while preserving device order."""
    serials: list[str] = []

    # `rs-enumerate-devices -s` normally prints a fixed-width table.  Parsing
    # by the serial-number column avoids interpreting the table header's
    # ``Firmware`` token as a device ID.
    lines = output.splitlines()
    for line_index, header in enumerate(lines):
        if not re.match(r"^\s*Device\s+Name\s+Serial\s+Number\b", header, re.IGNORECASE):
            continue
        serial_column = re.search(r"\bSerial\s+Number\b", header, re.IGNORECASE)
        if serial_column is None:
            continue
        serial_start = serial_column.start()
        firmware_column = re.search(r"\bFirmware\s+Version\b", header, re.IGNORECASE)
        serial_end = firmware_column.start() if firmware_column else None
        for row in lines[line_index + 1:]:
            if not row.strip():
                continue
            value = row[serial_start:serial_end].strip() if serial_end else row[serial_start:].split()[0]
            if _SERIAL_VALUE.fullmatch(value) and value.lower() not in {"serial", "number", "firmware", "version"}:
                if value not in serials:
                    serials.append(value)
        break

    for pattern in _SERIAL_PATTERNS:
        for match in pattern.finditer(output):
            serial = match.group(1).strip()
            if serial and serial not in serials:
                serials.append(serial)
    return serials


def enumerate_realsense_serials(timeout: float = 5.0) -> list[str]:
    """Return serials reported by the installed RealSense utility.

    A missing utility, timeout, or no connected device is represented by an
    empty list so the dialog can remain usable and offer a refresh action.
    """
    try:
        result = subprocess.run(
            ["rs-enumerate-devices", "-s"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_realsense_serials(f"{result.stdout}\n{result.stderr}")
