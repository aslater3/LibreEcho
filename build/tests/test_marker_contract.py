#!/usr/bin/env python3
"""Regression tests for the amonet v2.0.0 marker contract (Platform #195)."""

from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "build/check_marker_contract.sh"
SYSMAP_DEV = (
    "c0000000 t marker_thread\n"
    "c0000100 t echo_fastboot_marker_init\n"
    "c0000200 t __initcall_echo_fastboot_marker_init2\n"
)
SAFE_DRIVER = (
    '#define MISC_PATH\t"/dev/mmcblk0p8"\n'
    "#define WRITE_RETRIES 5\n"
    "if (written != (ssize_t)len) {}\n"
    "vfs_read(f, buf, len, &pos);\n"
)
SAFE_INIT = "IMAGE_PROFILE=$($BB cat /etc/libreecho/image-profile)\n"
SAFE_UPDATE = "USERDATA_DEVICE=/dev/mmcblk0p16\n"


class MarkerContractTests(unittest.TestCase):
    def run_contract(self, profile="development", driver=SAFE_DRIVER,
                     init=SAFE_INIT, update=SAFE_UPDATE, sysmap=SYSMAP_DEV):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kernel = root / "kernel"
            tooling = root / "tooling"
            (kernel / "drivers/misc/mediatek").mkdir(parents=True)
            (kernel / "drivers/misc/mediatek/echo_fastboot_marker.c").write_text(driver)
            initramfs = tooling / "tools/mt8163-arm32/initramfs"
            initramfs.mkdir(parents=True)
            (initramfs / "libreecho-init").write_text(init)
            (initramfs / "libreecho-update").write_text(update)
            (root / "System.map").write_text(sysmap)
            return subprocess.run(
                ["bash", str(CONTRACT), str(root / "System.map"), profile,
                 str(kernel), str(tooling)],
                capture_output=True, text=True, timeout=30,
            )

    def test_safe_development_sources_pass(self):
        result = self.run_contract()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("expdb=untouched", result.stdout)

    def test_safe_ota_sources_pass_without_marker_symbols(self):
        result = self.run_contract(profile="ota", sysmap="c0000000 T start_kernel\n")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_kernel_expdb_marker_is_rejected(self):
        driver = SAFE_DRIVER + '#define EXPDB_PATH "/dev/mmcblk0p7"\n#define MARKER "FASTBOOT_PLEASE"\n'
        result = self.run_contract(driver=driver)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Platform #195", result.stderr)

    def test_initramfs_expdb_write_is_rejected_in_every_profile(self):
        init = SAFE_INIT + 'printf FASTBOOT_PLEASE > "$EXPDB"\n'
        for profile, sysmap in (("development", SYSMAP_DEV), ("ota", "")):
            with self.subTest(profile=profile):
                result = self.run_contract(profile=profile, init=init, sysmap=sysmap)
                self.assertNotEqual(result.returncode, 0)

    def test_updater_expdb_clear_is_rejected(self):
        update = SAFE_UPDATE + "EXPDB_DEVICE=/dev/mmcblk0p7\n"
        result = self.run_contract(update=update)
        self.assertNotEqual(result.returncode, 0)

    def test_bcb_reset_must_still_target_misc(self):
        result = self.run_contract(driver=SAFE_DRIVER.replace("mmcblk0p8", "mmcblk0p9"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("misc", result.stderr)

    def test_ota_rejects_development_marker_symbols(self):
        result = self.run_contract(profile="ota")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
