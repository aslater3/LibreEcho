#!/usr/bin/env python3
"""Ensure the public Product installer mirror is complete and immutable."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "tools" / "libreecho-install.py"
CHECKSUM = ROOT / "tools" / "libreecho-install.py.sha256"


class InstallerPublicationTests(unittest.TestCase):
    def test_checked_in_installer_matches_its_sha256_sidecar(self) -> None:
        self.assertTrue(INSTALLER.is_file())
        self.assertFalse(INSTALLER.is_symlink())
        self.assertTrue(CHECKSUM.is_file())
        self.assertFalse(CHECKSUM.is_symlink())
        expected = CHECKSUM.read_text(encoding="ascii").strip().split()
        self.assertEqual(len(expected), 2)
        self.assertEqual(expected[1], "libreecho-install.py")
        self.assertEqual(len(expected[0]), 64)
        self.assertEqual(expected[0], hashlib.sha256(INSTALLER.read_bytes()).hexdigest())

    def test_installer_accepts_build_release_checksum_inventory(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("radar-puffin-build-", source)
        self.assertIn("def download_release", source)
        self.assertIn("def download_amonet", source)
        self.assertIn("release_dir = download_release", source)

    def test_installer_starts_with_readable_libreecho_banner(self) -> None:
        import contextlib
        import io

        spec = importlib.util.spec_from_file_location("installer_under_test", INSTALLER)
        assert spec is not None and spec.loader is not None
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)

        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "installer.log"
            result = subprocess.run(
                [
                    sys.executable, str(INSTALLER), "status",
                    "--state-root", str(Path(temporary) / "state"),
                    "--install-id", "banner-test",
                    "--log-file", str(log),
                ],
                text=True, capture_output=True, check=True,
            )
            self.assertIn("LibreEcho initial installer", result.stdout)
            expected_wordmark = (
                "#      #####  ####   ####   #####  #####   ####  #   #   ###\n"
                "#        #    #   #  #   #  #      #      #      #   #  #   #\n"
                "#        #    ####   ####   ####   ####   #      #####  #   #\n"
                "#        #    #   #  #  #   #      #      #      #   #  #   #\n"
                "#####  #####  ####   #   #  #####  #####   ####  #   #   ###"
            )
            self.assertIn(expected_wordmark, result.stdout)
            log_text = log.read_text(encoding="utf-8")
            self.assertIn("LibreEcho initial installer", log_text)
            self.assertNotIn("\x1b[", log_text)

        prompt_output = io.StringIO()
        with contextlib.redirect_stdout(prompt_output):
            installer.print_brom_action_prompt()
        prompt = prompt_output.getvalue()
        self.assertIn("ACTION REQUIRED - ENTER BROM MODE", prompt)
        self.assertIn("Connect the USB data pins: D+, D-, and GND.", prompt)
        self.assertIn("Do not apply power yet.", prompt)

        console = io.StringIO()
        logfile = io.StringIO()
        tee = installer._Tee(console, logfile)
        installer._COLOUR_ENABLED = True
        tee.write("\033[92mcolour\033[0m\n")
        installer._COLOUR_ENABLED = False
        self.assertIn("colour", console.getvalue())
        self.assertNotIn("\x1b[", logfile.getvalue())
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("def collect_failure_evidence", source)
        self.assertIn("libreecho-installer-evidence.tar.gz", source)
        self.assertIn("fastboot", source)
        self.assertIn("adb", source)

    def test_run_one_shot_wrapper_is_packaged_in_complete_release(self) -> None:
        source = (ROOT / "build/ci/prepare-dev-release.py").read_text(encoding="utf-8")
        self.assertIn("run-one-shot.sh", source)

    def test_product_readme_links_the_one_shot_install_guide(self) -> None:
        product_readme = (ROOT / "README.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs/install/README.md").read_text(encoding="utf-8")
        self.assertIn(
            "[Echo 2nd Gen one-shot installation guide](docs/install/README.md)",
            product_readme,
        )
        self.assertIn("TAG=latest", product_readme)
        self.assertIn(
            './run-one-shot.sh "$TAG" --fastboot-serial auto --slots both --execute-hardware',
            product_readme,
        )
        for marker in (
            "TAG=latest",
            "https://raw.githubusercontent.com/aslater3/LibreEcho/main/tools/run-one-shot.sh",
            './run-one-shot.sh "$TAG" --fastboot-serial auto --slots both --execute-hardware',
            "stages the feature payloads",
            "http://libreecho.local:8080/",
        ):
            self.assertIn(marker, guide)
        release_notes = (ROOT / "release/radar-puffin-v0.13.10.md").read_text(encoding="utf-8")
        self.assertIn(
            "https://github.com/aslater3/LibreEcho/blob/radar-puffin-v0.13.10/docs/install/README.md",
            release_notes,
        )
        self.assertIn("publication metadata remains `PREPARED_NOT_FLASHED`", release_notes)
        self.assertNotIn("exact coordinated 0.13.10 candidate", release_notes)

    def test_tools_readme_documents_self_download_and_full_flow(self) -> None:
        readme = (ROOT / "tools" / "README.md").read_text(encoding="utf-8")
        for marker in (
            "release checksum inventory",
            "download and verify pinned Amonet",
            "./run-one-shot.sh \"$TAG\"",
            "--execute-hardware",
            "initial-install.tar",
            "stage and verify all five feature payloads",
            "adb",
            "fastboot",
            "android-sdk-libsparse-utils",
            "--install-host-deps` can install only",
            "does **not** install `adb` or `fastboot`",
            "command -v adb fastboot mke2fs img2simg",
        ):
            self.assertIn(marker, readme)

    def test_run_one_shot_bootstrap_is_shell_and_checksum_gated(self) -> None:
        wrapper = ROOT / "tools" / "run-one-shot.sh"
        self.assertTrue(wrapper.is_file())
        self.assertFalse(wrapper.is_symlink())
        source = wrapper.read_text(encoding="utf-8")
        self.assertIn("SHA256SUMS", source)
        self.assertIn("sha256sum -c", source)
        self.assertNotIn("exec python3", source)
        self.assertIn("if python3", source)
        self.assertIn("exit \"$status\"", source)

    def test_run_one_shot_defaults_latest_and_resolves_an_immutable_tag(self) -> None:
        tag = "radar-puffin-v1.2.3"
        prefix = f"libreecho-{tag}"
        installer = b"#!/usr/bin/env python3\n"
        digest = hashlib.sha256(installer).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bindir = root / "bin"
            tmpdir = root / "tmp"
            argv_log = root / "installer-argv"
            bindir.mkdir()
            tmpdir.mkdir()
            (bindir / "curl").write_text(
                "#!/bin/sh\n"
                "out=\n"
                "url=\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  if [ \"$1\" = -o ]; then out=$2; shift 2; continue; fi\n"
                "  url=$1; shift\n"
                "done\n"
                "case \"$url\" in\n"
                f"  */releases/latest) printf '%s\\n' '{{\"draft\":false,\"prerelease\":false,\"tag_name\":\"{tag}\"}}' ;;\n"
                f"  *SHA256SUMS) printf '%s  %s\\n' '{digest}' '{prefix}-installer.py' >\"$out\" ;;\n"
                "  *-installer.py) printf '#!/usr/bin/env python3\\n' >\"$out\" ;;\n"
                "  *) exit 9 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            (bindir / "python3").write_text(
                "#!/bin/sh\n"
                f"if [ \"${{1:-}}\" = -c ]; then exec {sys.executable} \"$@\"; fi\n"
                f"printf '%s\\n' \"$@\" > {argv_log}\n"
                "exit 23\n",
                encoding="utf-8",
            )
            for tool in (bindir / "curl", bindir / "python3"):
                tool.chmod(0o755)
            env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", TMPDIR=str(tmpdir))
            result = subprocess.run(
                ["bash", str(ROOT / "tools/run-one-shot.sh"), "latest",
                 "--fastboot-serial", "auto", "--slots", "both", "--execute-hardware"],
                text=True, capture_output=True, env=env,
            )
            self.assertEqual(result.returncode, 23, result.stderr)
            self.assertIn(f"Resolved latest stable release: {tag}", result.stdout)
            argv = argv_log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(argv[1:4], ["one-shot", "--release-tag", tag])
            self.assertEqual(
                argv[4:],
                ["--fastboot-serial", "auto", "--slots", "both", "--execute-hardware"],
            )
            self.assertEqual(list(tmpdir.iterdir()), [])

    def test_run_one_shot_cleans_download_directory_after_installer_returns(self) -> None:
        tag = "radar-puffin-v1.2.3"
        prefix = f"libreecho-{tag}"
        installer = b"#!/usr/bin/env python3\n"
        digest = hashlib.sha256(installer).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bindir = root / "bin"
            tmpdir = root / "tmp"
            bindir.mkdir()
            tmpdir.mkdir()
            (bindir / "curl").write_text(
                "#!/bin/sh\n"
                "while [ \"$#\" -gt 0 ]; do\n"
                "  if [ \"$1\" = -o ]; then out=$2; shift 2; continue; fi\n"
                "  shift\n"
                "done\n"
                "case \"$out\" in\n"
                f"  *SHA256SUMS) printf '%s  %s\\n' '{digest}' '{prefix}-installer.py' >\"$out\" ;;\n"
                "  *) printf '#!/usr/bin/env python3\\n' >\"$out\" ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            (bindir / "python3").write_text("#!/bin/sh\nexit 23\n", encoding="utf-8")
            for tool in (bindir / "curl", bindir / "python3"):
                tool.chmod(0o755)
            env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", TMPDIR=str(tmpdir))
            result = subprocess.run(
                ["bash", str(ROOT / "tools/run-one-shot.sh"), tag],
                text=True, capture_output=True, env=env,
            )
            self.assertEqual(result.returncode, 23, result.stderr)
            self.assertEqual(list(tmpdir.iterdir()), [])

    def test_installer_accepts_stable_build_manifest_asset(self) -> None:
        spec = importlib.util.spec_from_file_location("libreecho_install", INSTALLER)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tag = "radar-puffin-v0.13.9"
        prefix = f"libreecho-{tag}"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release = root / "release"
            cache = root / "cache"
            release.mkdir()
            files = {
                f"{prefix}-boot.img": b"boot",
                f"{prefix}-ota-public-key.hex": b"a" * 64 + b"\n",
                f"{prefix}-release-notes.md": b"notes\\n",
                f"{prefix}-installer.py": b"#!/usr/bin/env python3\\n",
                f"{prefix}.ota.tar": b"signed ota",
                "libreecho-radar-puffin-stable.ota.tar": b"signed ota",
                f"{prefix}-build.json": b"{}\\n",
                f"{prefix}-run-one-shot.sh": b"#!/usr/bin/env bash\\n",
            }
            manifest = {
                "schema": "libreecho-initial-install-v1",
                "release": tag,
                "board": "radar_puffin",
                "soc": "mt8163",
                "image_profile": "ota",
                "service_profile": "production",
                "boot": {"name": f"{prefix}-boot.img", "size": len(files[f"{prefix}-boot.img"]), "sha256": hashlib.sha256(files[f"{prefix}-boot.img"]).hexdigest()},
                "ota_public_key": {"name": f"{prefix}-ota-public-key.hex", "size": len(files[f"{prefix}-ota-public-key.hex"]), "sha256": hashlib.sha256(files[f"{prefix}-ota-public-key.hex"]).hexdigest()},
                "features": [],
                "amonet": {"repository": "https://github.com/example/amonet", "tag": "v1", "commit": "a" * 40},
            }
            bundle = release / f"{prefix}-initial-install.tar"
            with tarfile.open(bundle, "w") as archive:
                info = tarfile.TarInfo("manifest.json")
                data = json.dumps(manifest).encode()
                info.size = len(data)
                archive.addfile(info, __import__("io").BytesIO(data))
                for name in (f"{prefix}-boot.img", f"{prefix}-ota-public-key.hex"):
                    info = tarfile.TarInfo(name)
                    info.size = len(files[name])
                    archive.addfile(info, __import__("io").BytesIO(files[name]))
            files[bundle.name] = bundle.read_bytes()
            for name, data in files.items():
                if name != bundle.name:
                    (release / name).write_bytes(data)
            sums = release / f"{prefix}-SHA256SUMS"
            sums.write_text("".join(f"{hashlib.sha256((release / name).read_bytes()).hexdigest()}  {name}\n" for name in sorted(files)), encoding="ascii")
            prepared, _ = module._prepare(release, cache, tag)
            self.assertEqual(prepared["release"], tag)

    def test_stable_release_publishes_checksum_covered_wrapper(self) -> None:
        source = (ROOT / "build/ci/prepare-stable-release.py").read_text(encoding="utf-8")
        self.assertIn('"tools/run-one-shot.sh"', source)
        self.assertIn('"run-one-shot.sh"', source)

    def test_install_guide_documents_initial_forward_and_safe_reassembly(self) -> None:
        guide = (ROOT / "docs/install/README.md").read_text(encoding="utf-8")
        self.assertIn("http://127.0.0.1:18080/setup.html", guide)
        self.assertIn("power before reconnecting any flex cables", guide.lower())
        self.assertIn("adb wait-for-device", guide)
        self.assertIn("Start the installer now", guide)
        self.assertLess(guide.index("Start the installer now"),
                        guide.index("## 5. Complete the installer transaction"))
        self.assertGreater(guide.index("./run-one-shot.sh \"$TAG\""),
                           guide.index("## 4. Enter BROM mode"))

    def test_install_guide_uses_copyable_public_wrapper_syntax(self) -> None:
        guide = (ROOT / "docs/install/README.md").read_text(encoding="utf-8")
        code_blocks = re.findall(r"```(?:sh|bash)\n(.*?)\n```", guide, re.S)
        self.assertTrue(code_blocks)
        for block in code_blocks:
            for line in block.splitlines():
                self.assertNotRegex(line, r"\\\\\\s*$",
                                     f"doubled shell continuation: {line!r}")
        self.assertIn("./run-one-shot.sh \"$TAG\"", guide)
        self.assertNotIn("./run-one-shot.sh latest", guide)
        self.assertNotIn("radar-puffin-v0.13.9", guide)
        self.assertNotIn("gh release list", guide)
        self.assertIn("public GitHub download URLs", guide)

    def test_continuation_validates_the_requested_release_tag(self) -> None:
        source = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("state[\"release\"] != release_tag", source)
        self.assertIn("continuation release tag does not match saved state", source)
        self.assertTrue(source.startswith("#!/usr/bin/env python3\n"))
        self.assertNotIn("/home/andy", source)
        self.assertNotIn("/media/andy", source)
        self.assertNotIn("PRIVATE", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
