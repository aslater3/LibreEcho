#!/usr/bin/env python3
"""0.13.15 protected OTA-v2 signer entry point.

The 0.13.14 signer implementation is retained byte-for-byte in the adjacent
module.  This release entry point changes only the exact candidate release
identity; all handoff, provenance, dependency, key-anchor, and Platform signer
checks execute in that retained implementation.
"""
from __future__ import annotations

import sign_ota_candidate_0_13_14 as _impl

EXPECTED_RELEASE = "0.13.15"
_impl.EXPECTED_RELEASE = EXPECTED_RELEASE

from sign_ota_candidate_0_13_14 import *  # noqa: E402,F401,F403


if __name__ == "__main__":
    raise SystemExit(_impl.main())
