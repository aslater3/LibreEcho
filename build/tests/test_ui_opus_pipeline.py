#!/usr/bin/env python3
"""Regression for Product build 36843300462: missing locked Opus inputs."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location

ROOT = Path(__file__).parents[2]
PINS = {
    "libogg": ("1.3.5", "libogg-1.3.5.tar.gz", "https://downloads.xiph.org/releases/ogg/libogg-1.3.5.tar.gz", "0eb4b4b9420a0f51db142ba3f9c64b333f826532dc0f48c6410ae51f4799b664"),
    "opus": ("1.4", "opus-1.4.tar.gz", "https://downloads.xiph.org/releases/opus/opus-1.4.tar.gz", "c9b32b4253be5ae63d1ff16eea06b94b5f0f2951b7a02aceef58e3a3ce49c51f"),
    "opusfile": ("0.12", "opusfile-0.12.tar.gz", "https://github.com/xiph/opusfile/archive/refs/tags/v0.12.tar.gz", "a20a1dff1cdf0719d1e995112915e9966debf1470ee26bb31b2f510ccf00ef40"),
}

class OpusPipelineTests(unittest.TestCase):
    def test_public_inventory_and_staged_names_match_platform_lock(self):
        records = {r["name"]: r for r in json.loads((ROOT / "build/inputs/public-inputs.json").read_text())["inputs"]}
        spec = spec_from_file_location("fetcher", ROOT / "build/ci/fetch-public-deps.py")
        assert spec and spec.loader
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        module.load(ROOT / "build/inputs/public-inputs.json")
        for name, (_, filename, url, digest) in PINS.items():
            self.assertIn(name, records)
            self.assertEqual(records[name]["url"], url)
            self.assertEqual(records[name]["sha256"], digest)
            self.assertEqual(records[name]["license"], "BSD-3-Clause")
            self.assertEqual(records[name]["redistribution"], "cleared")
            self.assertEqual(module.NAMES.get(name, url.rsplit("/", 1)[-1]), filename)

    def test_workflow_supplies_archives_not_a_self_certified_prefix(self):
        workflow = (ROOT / ".github/workflows/build-release.yml").read_text()
        self.assertIn("LIBREECHO_OPUS_ARCHIVES_DIR: ${{ runner.temp }}/public-deps", workflow)
        self.assertNotIn("LIBREECHO_UI_OPUS_PREFIX_TRUSTED:", workflow)
        build = (ROOT / "build/build.sh").read_text()
        self.assertIn('LIBREECHO_OPUS_CC="${UI_CROSS}gcc"', build)
        self.assertIn('LIBREECHO_OPUS_AR="${UI_CROSS}ar"', build)
        self.assertNotIn("LIBREECHO_UI_OPUS_PREFIX_TRUSTED=1", build)

    def test_real_bundle_cache_key_changes_with_every_opus_input(self):
        build = (ROOT / "build/build.sh").read_text()
        start = build.index('ui_bundle_cache_key="$(component_cache_key ui-bundle')
        end = build.index('\nUI_BUNDLE_STAGE=', start)
        assignment = build[start:end]
        for entry in ('opus-builder=$TOOLS_DIR/ui/build_opus.sh', 'opus-lock=$TOOLS_DIR/ui/opus/SOURCE.lock'):
            self.assertIn(entry, assignment)
        for name, (_, filename, _, _) in PINS.items():
            label = "ogg" if name == "libogg" else name
            self.assertIn(label + '-source=$OPUS_ARCHIVES_DIR/' + filename, assignment)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            tools = work / "tools/ui/opus"
            tools.mkdir(parents=True)
            files = [tools / "SOURCE.lock", tools.parent / "build_opus.sh"]
            files += [work / filename for _, filename, _, _ in PINS.values()]
            files += [work / "bundle.sh", work / "cc"]
            for path in files:
                path.write_text("first\n")
            env = dict(os.environ, TOOLS_DIR=str(work / "tools"), OPUS_ARCHIVES_DIR=str(work), UI_BUILDER=str(work / "bundle.sh"), AUDIO_CC=str(work / "cc"), ui_commit="a"*40, ui_diff_sha="b"*64, UI_TOOLCHAIN_KEY="c"*64, mbedtls_cache_key="d"*64, CORE_TOOLCHAIN_KEY="e"*64, SERVICE_PROFILE="production")
            script = 'component_cache_key() { python3 "' + str(ROOT / "build/component-cache.py") + '" key --component "$@"; }\n' + assignment + '\nprintf "%s\\n" "$ui_bundle_cache_key"\n'
            def key():
                result = subprocess.run(["bash", "-euc", script], env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()
            first = key()
            for path in files[:5]:
                path.write_text("changed\n")
                self.assertNotEqual(first, key(), str(path))
                path.write_text("first\n")
                self.assertEqual(first, key())

    def test_shipped_stack_has_catalog_and_license_notice(self):
        catalog = {r["id"]: r for r in json.loads((ROOT / "release/components.json").read_text())["components"]}
        notice = (ROOT / "release/THIRD_PARTY_NOTICES.md").read_text()
        offer = (ROOT / "release/CORE-RUNTIME-SOURCE-OFFER.md").read_text()
        for name, (version, _, url, digest) in PINS.items():
            self.assertIn(name, catalog)
            record = catalog[name]
            self.assertEqual(record["version"], version)
            self.assertEqual(record["license"], "BSD-3-Clause")
            self.assertEqual(record["source_offer"], url)
            self.assertEqual(record["source_archive_sha256"], digest)
            self.assertEqual(record["distribution_scope"], "core-image")
            self.assertEqual(record["release_status"], "cleared")
            self.assertIn(name, notice)
            self.assertIn(url, offer)
            self.assertIn(digest, offer)

if __name__ == "__main__":
    unittest.main()
