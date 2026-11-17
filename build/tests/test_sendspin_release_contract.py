#!/usr/bin/env python3
"""Release wiring of the Sendspin player into the AirPlay 2 payload (build.sh).

Source contract only: the real build, packaging and payload checks run in
Platform (sendspin/build_sendspind.sh, sendspin/test_packaging.py) and in the
release image build itself.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
BUILD = (ROOT / "build/build.sh").read_text(encoding="utf-8")


class SendspinReleaseContract(unittest.TestCase):
    def section(self):
        start = BUILD.index('echo "=== building the static ARM32 Sendspin player')
        end = BUILD.index('airplay_payload_sha="$(sha256sum "$AIRPLAY_PAYLOAD"')
        return BUILD[start:end]

    def test_player_is_built_before_the_airplay_payload_is_packaged(self):
        self.assertLess(BUILD.index('echo "=== building the static ARM32 Sendspin player'),
                        BUILD.index('echo "=== packaging AirPlay 2 as an external feature payload ==="'))
        self.assertLess(BUILD.index('echo "=== building packaged ARM32 AirPlay 2 payload ==="'),
                        BUILD.index('echo "=== building the static ARM32 Sendspin player'))

    def test_built_from_the_pinned_inputs_with_the_ui_cross_toolchain(self):
        section = self.section()
        self.assertIn('SENDSPIN_BUILDER="$TOOLS_DIR/sendspin/build_sendspind.sh"', section)
        self.assertIn('CROSS_PREFIX="$UI_CROSS" "$SENDSPIN_BUILDER" "$UI_SOURCE" "$INPUTS" "$SENDSPIN_OUTPUT"', section)
        self.assertIn('[[ -d "$INPUTS/sendspin-sdk" ]]', section)
        self.assertIn('echo "sendspind_sha256=$sendspind_sha"', section)

    def test_release_builds_must_ship_the_player(self):
        section = self.section()
        branch = re.search(r'elif \[\[ "\$PUBLIC_RELEASE_MODE" == 1 \]\]; then\n(.*?)\n(?:else|fi)', section, re.S)
        self.assertIsNotNone(branch, "release mode has no explicit Sendspin branch")
        self.assertIn("exit 1", branch.group(1))

    def test_packager_receives_the_player(self):
        section = self.section()
        self.assertRegex(section, r'LIBREECHO_AIRPLAY_SENDSPIN="\$SENDSPIN_OUTPUT" \\\n\s+"\$AIRPLAY_FEATURE_PACKAGER"')


if __name__ == "__main__":
    unittest.main()
