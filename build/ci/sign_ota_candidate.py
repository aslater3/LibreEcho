#!/usr/bin/env python3
"""0.13.15 protected OTA-v2 signer entry point.

The 0.13.14 signer implementation is retained byte-for-byte in the adjacent
module. This release entry point changes only the exact candidate release
identity; all handoff, provenance, dependency, key-anchor, and Platform signer
checks execute in that retained implementation.
"""
from __future__ import annotations

import sign_ota_candidate_0_13_14 as _impl

EXPECTED_RELEASE = "0.13.15"
_impl.EXPECTED_RELEASE = EXPECTED_RELEASE

# Re-export the complete implementation API, including deliberately tested
# private verification helpers. A normal star import would omit `_...` names
# and accidentally reduce the host-side regression surface.
for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)
EXPECTED_RELEASE = "0.13.15"


if __name__ == "__main__":
    raise SystemExit(_impl.main())
