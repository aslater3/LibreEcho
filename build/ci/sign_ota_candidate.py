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

# Default arguments are bound when the retained module is imported.  Merely
# changing _impl.EXPECTED_RELEASE would therefore leave validate_handoff()'s
# no-argument default pinned to 0.13.14, causing internal create/sign paths to
# reject a valid 0.13.15 handoff before the real contract checks run.  Adapt
# that one entry point so all implicit calls pass this release explicitly while
# keeping the preserved 0.13.14 implementation itself byte-for-byte unchanged.
_validate_handoff_0_13_14 = _impl.validate_handoff


def _validate_handoff_for_release(path, expected_release: str = EXPECTED_RELEASE):
    return _validate_handoff_0_13_14(path, expected_release)


_impl.validate_handoff = _validate_handoff_for_release

# Re-export the complete implementation API, including deliberately tested
# private verification helpers. A normal star import would omit `_...` names
# and accidentally reduce the host-side regression surface.
for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)
EXPECTED_RELEASE = "0.13.15"


if __name__ == "__main__":
    raise SystemExit(_impl.main())
