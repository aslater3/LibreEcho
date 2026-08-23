#!/usr/bin/env python3
from pathlib import Path
import re
import unittest
ROOT=Path(__file__).parents[1]
W=(ROOT/'.github/workflows/build-release.yml').read_text()
TOOLCHAIN=(ROOT/'build/ci/build-public-toolchain.sh').read_text()
CACHE_PIN='0400d5f644dc74513175e3cd8d07132dd4860809'
class Tests(unittest.TestCase):
 def test_hosted_only(self):
  self.assertNotIn('self-hosted',W); self.assertNotIn('Vaultwarden',W)
  self.assertIn('ports.ubuntu.com/ubuntu-ports', W)
  self.assertIn('apt-get download', W)
  self.assertIn('dpkg-deb -x', W)
  self.assertIn(f'actions/cache/restore@{CACHE_PIN}', W)
  self.assertIn(f'actions/cache/save@{CACHE_PIN}', W)
  self.assertIn('LIBREECHO_COMPONENT_CACHE_ROOT', W)
  self.assertIn('LIBREECHO_REUSE_COMPONENT_CACHE: "1"', W)
  self.assertIn('restore-keys:', W)
  self.assertIn('include-hidden-files: true', W)
  self.assertIn('Build public neural dependencies', W)
  self.assertNotIn('public-neural-deps-', W)
  for variable in (
    'LIBREECHO_WAKE_ORT_SOURCE', 'LIBREECHO_ORT_BUILD',
    'LIBREECHO_ORT_PREFIX', 'LIBREECHO_WAKE_FLATBUFFERS_PYTHON',
    'LIBREECHO_SHERPA_SOURCE', 'LIBREECHO_SHERPA_PREFIX',
    'LIBREECHO_SPEEX_PREFIX',
  ):
   self.assertIn(variable, W)
  self.assertIn('build/ci/build-public-neural-deps.sh', W)
  self.assertIn('LD_LIBRARY_PATH: ${{ runner.temp }}/armhf-root/usr/lib/x86_64-linux-gnu', W)
  self.assertIn("build/ci/build-public-neural-deps.sh', 'build/inputs/public-inputs.json'", W)
  self.assertNotIn('out/CURRENT', W)
  self.assertIn('runs-on: ubuntu-24.04',W)
 def test_cache_layers(self):
  # Every expensive, input-stable stage is content-keyed so unchanged inputs
  # restore instead of rebuilding. Keys are input fingerprints: any input
  # change (inventory, builder script, compiler bytes, kernel SHA, defconfig)
  # changes the key and forces a rebuild under the new key.
  for key in (
    'libreecho-public-deps-v1-',
    'libreecho-musl-toolchain-v1-',
    'libreecho-neural-deps-v1-',
    'libreecho-components-v6-',
    'libreecho-kernel-out-v1-',
  ):
   self.assertIn(key, W)
  # The neural cache key binds the exact compiler bytes and the ARMHF root
  # package digest (any package added/removed/updated invalidates it).
  self.assertIn('steps.armhf-id.outputs.armhf_cc_sha256', W)
  self.assertIn('steps.armhf-pkg.outputs.armhf_root_digest', W)
  self.assertIn('needs.prepare-public-inputs.outputs.armhf_root_digest', W)
  self.assertIn('linux-libc-dev-armhf-cross', W)
  # The kernel cache key binds kernel SHA, compiler bytes, and defconfig.
  self.assertIn('needs.resolve-and-preflight.outputs.linux_sha', W)
  self.assertIn('mt8163_arm32_defconfig', W)
  # Component cache is saved even when the build fails (entries are stored
  # atomically per component); the kernel object tree is saved only after a
  # complete successful build.
  self.assertIn('if: always() && steps.restore-components.outputs.cache-hit', W)
  self.assertIn('if: success() && steps.restore-kernel.outputs.cache-hit', W)
  # The AirPlay sysroot is staged in build-image as a full ARMHF glibc
  # dependency closure (apt --download-only with dependency resolution),
  # and the deps cache layer excludes it (derived, not fetched).
  self.assertIn('Stage public ARMHF AirPlay sysroot', W)
  self.assertIn('install --download-only --reinstall -y', W)
  self.assertIn('-o APT::Install-Recommends=false', W)
  self.assertIn('AirPlay sysroot closure is incomplete', W)
  self.assertIn('libsodium-dev libgcrypt20-dev libc6-dev-armhf-cross linux-libc-dev-armhf-cross', W)
  # Hosted-runner substitutions for host-only defaults: AirPlay C++ compiler
  # and ALSA runtime data come from the staged ARMHF root, not /usr.
  self.assertIn('LIBREECHO_AIRPLAY_CXX: ${{ runner.temp }}/armhf-root/usr/bin/arm-linux-gnueabihf-g++', W)
  self.assertIn('LIBREECHO_AIRPLAY_ALSA_DATA: ${{ runner.temp }}/armhf-root/usr/share/alsa', W)
  # The UI daemons (and AirPlay's CROSS_PREFIX) build with the staged ARMHF
  # glibc cross, not the musl toolchain: the musl driver has no default
  # include path in the staged layout, so bare invocations from the UI
  # Makefile cannot find stdint.h/time.h there.
  self.assertIn('LIBREECHO_UI_CROSS: ${{ runner.temp }}/armhf-root/usr/bin/arm-linux-gnueabihf-', W)
  self.assertNotIn('LIBREECHO_UI_CROSS: ${{ runner.temp }}/toolchain/usr/bin/armv7-alpine-linux-musleabihf-', W)
  # Kernel UAPI headers are exported (linux/, asm/, asm-generic/) from the
  # locked kernel source; component builders link against the exported tree,
  # not the raw source root.
  self.assertIn('Export Linux UAPI headers', W)
  self.assertIn('headers_install INSTALL_HDR_PATH="$RUNNER_TEMP/kernel-uapi"', W)
  self.assertIn('LIBREECHO_ADBD_KERNEL_HEADERS: ${{ runner.temp }}/kernel-uapi/include', W)
  # qemu-arm-static is staged from a digest-pinned deb so the wpa/connectivity
  # runtime contract checks can execute built ARM binaries on the runner.
  self.assertIn('Stage qemu-arm-static for runtime contract checks', W)
  self.assertIn('qemu-user-static_8.2.2+ds-0ubuntu1.18_amd64.deb', W)
  self.assertIn('5bb397f66063efa349f6fd5cb3b68cd96f29edd0994e4ba5115cf0859a716bf0', W)
  # The DTB contract verifier requires fdtget; stage the digest-pinned dtc
  # and libfdt debs into host-bin (already on PATH).
  self.assertIn('Stage device-tree tools for the DTB contract', W)
  self.assertIn('device-tree-compiler_1.7.0-2build1_amd64.deb', W)
  self.assertIn('libfdt1_1.7.0-2build1_amd64.deb', W)
  self.assertIn('b2c1e8c86f18b6bda26408f92bfb9ec1a1e40bfdc41f1034600ccd68e82d2ed7', W)
  self.assertIn('274d20dfab9d6b216b5de85446a93f6ce5b2cd82c847b8dfdc508577f76eb96a', W)
  self.assertNotIn('LIBREECHO_ADBD_KERNEL_HEADERS: ${{ github.workspace }}/sources/linux', W)
  self.assertIn('libasound2-data', W)
  # Exec bits are restored across the full staged closure: toolchain
  # libexec (cc1), ARMHF usr/bin and usr/sbin (avahi/dbus daemons), and
  # host-tools/bin (plistutil), because download-artifact strips them.
  self.assertIn('find "$RUNNER_TEMP/toolchain/libexec" "$RUNNER_TEMP/toolchain/usr/libexec" -type f -exec chmod 0755 {} +', W)
  self.assertIn('find "$RUNNER_TEMP/armhf-root/usr/libexec/gcc-cross/arm-linux-gnueabihf/13" -type f -exec chmod 0755 {} +', W)
  # The staged ARMHF binutils are dynamically linked against -armhf libs
  # inside the extracted root; the runner host lacks them, so the build
  # step must export LD_LIBRARY_PATH (host-bin too, for libfdt.so.1).
  self.assertIn('LD_LIBRARY_PATH: ${{ runner.temp }}/armhf-root/usr/lib/x86_64-linux-gnu:${{ runner.temp }}/host-bin', W)
  self.assertIn('"$RUNNER_TEMP/armhf-root/usr/sbin"', W)
  self.assertIn('find "$RUNNER_TEMP/public-deps/host-tools/bin" -type f -exec chmod 0755 {} +', W)
  # Host build tools missing from the ubuntu-24.04 runner image are staged
  # digest-pinned into host-bin: xxd (AirPlay) and mksquashfs/unsquashfs
  # (feature payloads), plus the mksquashfs runtime libraries.
  self.assertIn('Stage host build tools (xxd, mksquashfs)', W)
  self.assertIn('6e78203acd7886ee1b91e1e80f673d02e6dc3b55b04e64ebcd6bedc42b9d16bc', W)
  self.assertIn('87fae263846bab255d4a51ad9fc623685497ad830db60758dde39589c9fdadcb', W)
  self.assertIn('e0d13be155013138b8db4cfe68212b866080af661c78302c2eab0d2f9d0d454e', W)
  self.assertIn('!${{ runner.temp }}/public-deps/airplay-sysroot', W)
  self.assertIn('!${{ runner.temp }}/public-deps/neural', W)
  # Restored inputs are skipped; only cold paths rebuild and re-save.
  self.assertIn("if: steps.restore-deps.outputs.cache-hit != 'true'", W)
  self.assertIn("if: steps.restore-toolchain.outputs.cache-hit != 'true'", W)
  self.assertIn("if: steps.restore-neural.outputs.cache-hit != 'true'", W)
  # Cold-path saves are gated on success so a failed fetch/build never
  # stores partial output under the real key.
  self.assertIn("if: success() && steps.restore-deps.outputs.cache-hit", W)
  self.assertIn("if: success() && steps.restore-toolchain.outputs.cache-hit", W)
  self.assertIn("if: success() && steps.restore-neural.outputs.cache-hit", W)
 def test_triggers_and_jobs(self):
  self.assertIn('branches: [main]',W); self.assertIn('branches: [main, release/0.13.8]',W); self.assertIn('workflow_dispatch:',W); self.assertIn('version:',W)
  self.assertIn('prepare-public-inputs:',W); self.assertIn('publish-dev:',W); self.assertIn('publish-production:',W)
 def test_boundaries(self):
  # Concurrency is scoped per event and ref (independent PR lanes), and
  # running builds are never cancelled.
  self.assertIn('github.head_ref || github.ref',W); self.assertIn('cancel-in-progress: false',W)
  self.assertNotIn('queue: max',W)
  self.assertIn('build/ci/build-public-release.sh',W); self.assertIn('fetch-public-deps.py',W)
  self.assertIn('GNU_SITE = https://ftp.gnu.org/gnu', TOOLCHAIN)
  self.assertIn('MUSL_SITE = https://www.musl-libc.org/releases', TOOLCHAIN)
  self.assertIn('curl -4 -L --fail --retry 5 --retry-all-errors', TOOLCHAIN)
  self.assertIn('curl is required for public toolchain downloads', TOOLCHAIN)
  # GCC resolves cc1 via ../libexec relative to the driver; the usr/bin
  # compiler copies need the libexec tree staged beside them.
  self.assertIn('cp -a "$OUT/libexec" "$OUT/usr/libexec"', TOOLCHAIN)
  # The Alpine musl SONAME patch is staged into musl-cross-make's patches/
  # and bound into the toolchain cache key.
  self.assertIn('patches/musl-1.2.6/50-alpine-soname.diff', TOOLCHAIN)
  self.assertIn('build/inputs/musl-alpine-soname.diff', W)
  self.assertIn('"$RUNNER_TEMP/toolchain/usr/libexec"', W)
  # GCC also needs <prefix>/lib/gcc/<target>/<ver> (internal includes,
  # libgcc) and <prefix>/arm-linux-musleabihf/bin (target binutils)
  # beside the usr/bin drivers; missing them yields stdc-predef.h and
  # host-'as' failures.
  self.assertIn('cp -a "$OUT/lib/gcc" "$OUT/usr/lib/gcc"', TOOLCHAIN)
  self.assertIn('cp -a "$OUT/arm-linux-musleabihf/bin" "$OUT/usr/arm-linux-musleabihf/bin"', TOOLCHAIN)
  self.assertIn('"$RUNNER_TEMP/toolchain/usr/arm-linux-musleabihf/bin"', W)
  # Hygiene flags shrink the toolchain, but only the deterministic
  # post-install strip pass keeps volatile build paths out of libgcc's
  # DWARF (component contracts reject binaries carrying /home/ paths).
  self.assertIn('COMMON_CONFIG += CFLAGS="-g0 -Os -ffile-prefix-map=', TOOLCHAIN)
  self.assertIn('=/usr/src/musl-cross-make', TOOLCHAIN)
  self.assertIn('objcopy" --strip-debug "$archive"', TOOLCHAIN)
  self.assertIn('ranlib" "$archive"', TOOLCHAIN)
  self.assertIn('--strip-debug "$OUT/arm-linux-musleabihf/lib/libc.so"', TOOLCHAIN)
  for package in ('gcc-13-arm-linux-gnueabihf-base', 'gcc-13-cross-base',
                  'cpp-13-arm-linux-gnueabihf', 'libgcc-13-dev-armhf-cross',
                  'libgcc-s1-armhf-cross', 'libstdc++6-armhf-cross',
                  'libstdc++-13-dev-armhf-cross', 'libc6-armhf-cross'):
   self.assertIn(package, W)
  for action in re.findall(r'uses:\s*([^\s]+)',W): self.assertRegex(action,r'@[0-9a-f]{40}$')
if __name__=='__main__': unittest.main()
