"""Single source for the pinned community Amonet ZIPs used by the one-shot installer.

The release producers read release/amonet-pins.json and embed the same values in
tools/libreecho-install.py (AMONET_PINS). A test asserts the two never drift.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIN_FILE = ROOT / "release" / "amonet-pins.json"


def load_pins() -> dict:
    return json.loads(PIN_FILE.read_text(encoding="utf-8"))


def amonet_record(target: str) -> dict:
    """Manifest record for one target: archive identity only, no hash/version pin for the payload."""
    pin = load_pins()["targets"][target]
    return {
        "archive": pin["archive"],
        "archive_sha256": pin["archive_sha256"],
        "archive_size": pin["archive_size"],
    }
