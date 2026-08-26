#!/usr/bin/env python3
from pathlib import Path
import re
import unittest
ROOT=Path(__file__).parents[1]
W=(ROOT/'.github/workflows/build-release.yml').read_text()
PUBLISH=(ROOT/'.github/workflows/publish-release.yml').read_text()
PUBLIC_WRAPPER=(ROOT/'build/ci/build-public-release.sh').read_text()
STABLE_PUBLISHER=(ROOT/'build/ci/publish-stable-release.sh').read_text()
TOOLCHAIN=(ROOT/'build/ci/build-public-toolchain.sh').read_text()
B=(ROOT/'build/build.sh').read_text()
NEURAL=(ROOT/'build/ci/build-public-neural-deps.sh').read_text()
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
  # A cold kernel output generates defconfig automatically; a restored cache
  # must not pass --defconfig, which invalidates the object tree and rebuilds
  # the entire kernel despite a cache hit.
  public_release = (ROOT/'build/ci/build-public-release.sh').read_text()
  self.assertNotIn('build.sh" --defconfig', public_release)
  self.assertIn('Normalize Linux source mtimes for kernel cache reuse', W)
  self.assertIn('git -C sources/linux ls-files -z', W)
  self.assertIn("grep -qxF '# CONFIG_LIBREECHO_DEV_RECOVERY_MARKER is not set'", B)
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
  self.assertIn('libsodium-dev libgcrypt20-dev zlib1g-dev', W)
  # zlib1g-dev is required transitively: FFmpeg's static archives inject
  # -lz into shairport-sync's final link; assert the dev side is present.
  self.assertIn('"$sysroot/usr/lib/arm-linux-gnueabihf/libz.a"', W)
  self.assertIn('"$sysroot/usr/lib/arm-linux-gnueabihf/libz.so"', W)
  self.assertIn('"$sysroot/usr/include/zlib.h"', W)
  # The AirPlay sysroot must NOT seed the cross-layout glibc packages:
  # they install a second glibc under usr/arm-linux-gnueabihf/ that
  # conflicts with the native armhf layout the builders link against.
  self.assertIn('Do not seed libc6-dev-armhf-cross', W)
  self.assertIn('linux-libc-dev-armhf-cross here: they install a second glibc', W)
  # Noble is usrmerge: libc's dev linker script references /lib/arm-linux-
  # gnueabihf/libc.so.6; with --sysroot ld resolves it inside the sysroot,
  # which needs the usrmerge symlinks dpkg-deb -x never creates. A dynamic
  # link probe fails closed before AirPlay consumes the sysroot.
  self.assertIn('ln -sfn "usr/$merged" "$sysroot/$merged"', W)
  # alsa-topology-conf ships real /lib/firmware; the usrmerge handling must
  # merge real top-level dirs into usr/ (not fail closed on them).
  self.assertIn('cp -a "$sysroot/$merged/." "$sysroot/usr/$merged/"', W)
  self.assertIn('"$sysroot/usr/lib/firmware/skl_hda_dsp_generic-tplg.bin"', W)
  self.assertIn('airplay-sysroot-probe', W)
  # The sysroot must never contain a cross-layout glibc tree (it would
  # shadow the native armhf glibc and reintroduce bare /usr/arm-linux-
  # gnueabihf GROUP paths that only resolve behind the hosted anchor).
  self.assertIn('ERROR: airplay sysroot contains a cross-layout tree: usr/arm-linux-gnueabihf', W)
  # The probes must reproduce the consumers' exact link lines (-L into the
  # sysroot's native lib dir); a bare probe without -L falls through to
  # armhf-root's cross libc.so GROUP paths and fails before the anchor.
  self.assertIn('-L"$sysroot/usr/lib/arm-linux-gnueabihf"', W)
  self.assertIn('ELF 32-bit LSB pie executable, ARM', W)
  self.assertIn('ELF 32-bit LSB executable, ARM', W)
  # Hosted-runner substitutions for host-only defaults: AirPlay C++ compiler
  # comes from the staged ARMHF root, and ALSA runtime data plus its Debian
  # copyright records come from the staged sysroot (which carries
  # alsa-ucm-conf / alsa-topology-conf, Recommends skipped under
  # Install-Recommends=false).
  self.assertIn('LIBREECHO_AIRPLAY_CXX: ${{ runner.temp }}/armhf-root/usr/bin/arm-linux-gnueabihf-g++', W)
  self.assertIn('LIBREECHO_AIRPLAY_ALSA_DATA: ${{ runner.temp }}/public-deps/airplay-sysroot/usr/share/alsa', W)
  self.assertIn('LIBREECHO_AIRPLAY_ALSA_DATA_COPYRIGHT: ${{ runner.temp }}/public-deps/airplay-sysroot/usr/share/doc/libasound2-data/copyright', W)
  self.assertIn('LIBREECHO_AIRPLAY_ALSA_UCM_COPYRIGHT: ${{ runner.temp }}/public-deps/airplay-sysroot/usr/share/doc/alsa-ucm-conf/copyright', W)
  self.assertIn('zlib1g-dev alsa-ucm-conf alsa-topology-conf', W)
  self.assertIn('"$sysroot/usr/share/alsa/ucm2"', W)
  self.assertIn('"$sysroot/usr/share/doc/alsa-ucm-conf/copyright"', W)
  # The UI daemons (and AirPlay's CROSS_PREFIX) build with the staged ARMHF
  # glibc cross, not the musl toolchain: the musl driver has no default
  # include path in the staged layout, so bare invocations from the UI
  # Makefile cannot find stdint.h/time.h there.
  self.assertIn('LIBREECHO_UI_CROSS: ${{ runner.temp }}/armhf-root/usr/bin/arm-linux-gnueabihf-', W)
  self.assertNotIn('LIBREECHO_UI_CROSS: ${{ runner.temp }}/toolchain/usr/bin/armv7-alpine-linux-musleabihf-', W)
  # TTS voices are derived at build time from the pinned upstream Piper
  # voices (rhasspy/piper-voices rev ea046e84): upstream blob + reviewed
  # metadata_props. The derived hashes must equal the contract in
  # tts-voice-metadata.json (same as package_feature.sh), and each voice
  # gets its own env var — never both pointing at one file.
  self.assertIn('Derive reviewed TTS voice models', W)
  self.assertIn('python3 build/ci/derive-tts-model.py --deps-root "$RUNNER_TEMP/public-deps"', W)
  self.assertIn('LIBREECHO_TTS_NORTHERN_MALE_MODEL: ${{ runner.temp }}/public-deps/northern-male.derived.onnx', W)
  self.assertIn('LIBREECHO_TTS_FEMALE_MODEL: ${{ runner.temp }}/public-deps/southern-female.derived.onnx', W)
  self.assertIn('LIBREECHO_TTS_TOKENS: ${{ runner.temp }}/public-deps/tts-tokens.txt', W)
  self.assertNotIn('LIBREECHO_TTS_FEMALE_MODEL: ${{ runner.temp }}/public-deps/northern-male', W)
  # Flite ships as source only; the neural dependency builder compiles the
  # static ARM32 archives into the neural cache and build.sh stages them
  # into the source tree at the path the UI Makefile links from.
  self.assertIn('LIBREECHO_FLITE_SOURCE: ${{ runner.temp }}/public-deps/flite-source', W)
  self.assertIn('LIBREECHO_FLITE_ROOT: ${{ runner.temp }}/public-deps/neural/flite-root', W)
  self.assertIn('FLITE_BUILT_ROOT="${LIBREECHO_FLITE_ROOT:?ERROR: set LIBREECHO_FLITE_ROOT explicitly}"', B)
  self.assertIn('libflite_cmu_us_slt.a', NEURAL)
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
  self.assertIn('Stage host build tools (xxd, mksquashfs, cpio)', W)
  self.assertIn('6e78203acd7886ee1b91e1e80f673d02e6dc3b55b04e64ebcd6bedc42b9d16bc', W)
  self.assertIn('87fae263846bab255d4a51ad9fc623685497ad830db60758dde39589c9fdadcb', W)
  self.assertIn('e0d13be155013138b8db4cfe68212b866080af661c78302c2eab0d2f9d0d454e', W)
  self.assertIn('b3c7bb97baf1a5dabe0c672ebdc94724bdcd7251790152cfa4314efda5696817', W)
  # The glibc dev linker scripts bake absolute /usr/arm-linux-gnueabihf
  # paths; anchor the staged closure there so configure link probes work.
  self.assertIn('Anchor ARMHF sysroot at its packaged absolute paths', W)
  self.assertIn('sudo ln -sfT "$RUNNER_TEMP/armhf-root/usr/arm-linux-gnueabihf" /usr/arm-linux-gnueabihf', W)
  self.assertIn('armhf-anchor-probe', W)
  # Autotools cross builds (wakeword SpeexDSP configure) discover the
  # compiler from PATH; the shim exposes only prefixed cross tools so the
  # host binutils are never shadowed.
  self.assertIn('Expose ARMHF cross tools on PATH', W)
  self.assertIn('armhf-cross-shim', W)
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
 def test_vendored_ca_contract(self):
  # The assistant feature packager rejects any CA bundle except the reviewed
  # 2026-06-01 bundle (digest c0c940a0...). The hosted pipeline must vendor
  # those exact bytes in the repository and fail closed on their digest,
  # never copying the runner's live system bundle (which drifts with runner
  # image updates).
  import hashlib, json
  inv = json.loads((ROOT/'build/inputs/public-inputs.json').read_text())
  ca = [e for e in inv['inputs'] if e['name']=='ca-certificates'][0]
  notice = [e for e in inv['inputs'] if e['name']=='ca-certificates-notice'][0]
  self.assertEqual(ca['kind'], 'reviewed-vendored-input')
  self.assertEqual(ca['sha256'], 'c0c940a0e30d859783f7f130868d8082e79936ff0b41a0b1098ac7f98909263b')
  self.assertEqual(ca['url'], 'vendored://reviewed/ca-certificates-20260601.crt')
  self.assertEqual(notice['sha256'], 'e85e1bcad3a915dc7e6f41412bc5bdeba275cadd817896ea0451f2140a93967c')
  # Vendored bytes exist and match the pinned digests exactly.
  for rec in (ca, notice):
    src = ROOT/'build/inputs'/rec['url'][len('vendored://'):]
    self.assertTrue(src.is_file(), src)
    self.assertEqual(hashlib.sha256(src.read_bytes()).hexdigest(), rec['sha256'])
  # Fetcher has no remaining runner-copy path.
  fpd = (ROOT/'build/ci/fetch-public-deps.py').read_text()
  self.assertIn('reviewed-vendored-input', fpd)
  self.assertIn('stage_vendored', fpd)
  self.assertNotIn('RUNNER_SOURCES', fpd)
  self.assertNotIn('/etc/ssl/certs/ca-certificates.crt', fpd)
 def test_vendored_connectivity_contract(self):
  # The connectivity helpers carry a byte-exact recovery-image contract
  # (CONNECTIVITY_HELPERS in build_recovery_image.py). They were built by
  # the dedicated lane's Alpine 15.2.0 chroot compiler and cannot be
  # reproduced by the hosted toolchain, so the public pipeline vendors the
  # reviewed bytes and stages them fail-closed instead of rebuilding.
  import hashlib
  vendored_dir = ROOT/'build/inputs/reviewed/connectivity'
  contract = {
    'wmt_configure': (25744, '2a57272037a34519e9f6f5dd64ab5a16ad304c81535c4aa7f15a8afae34aadb1'),
    'wmt_responder': (21648, '46170ddc1d1ddf21a85ec16df129aac47a258a439bc9e6ed061d1e5942aa48eb'),
    'wmt_bt_on': (21648, '985320b270149cd27bc59d7f34d0da829817f225a4e712037633517c843cc745'),
    'wmt_stock_compat': (21648, '7e3afe31b706029ebf6e271f5cda6e3880cfc5b184abb052a190662759708c87'),
    'wmt_launcher': (21648, '65cb5c0c49bb61aec657c114cf67269e398bf41ff7b70a4abb8eb0ec36ff2c99'),
  }
  for name, (size, sha) in contract.items():
    src = vendored_dir/name
    self.assertTrue(src.is_file(), src)
    data = src.read_bytes()
    self.assertEqual(len(data), size)
    self.assertEqual(hashlib.sha256(data).hexdigest(), sha)
  self.assertTrue((vendored_dir/'connectivity-source.json').is_file())
  self.assertIn('stage-reviewed-connectivity.py', B)
  self.assertIn('--vendored "$PIPELINE/inputs/reviewed/connectivity"', B)
  self.assertIn('--tools-dir "$TOOLS_DIR"', B)
  self.assertIn('--tree "reviewed-connectivity=$PIPELINE/inputs/reviewed/connectivity"', B)
  # The vendored path must not invoke the musl toolchain rebuild anymore.
  self.assertNotIn('build_connectivity_helpers.sh', B)
 def test_reviewed_signing_dependencies_are_downloaded_before_install(self):
  build_image = W.index('  build-image:')
  download = W.index('name: public-deps-${{ needs.resolve-and-preflight.outputs.source_set_id }}', build_image)
  install = W.index('Install reviewed OTA signing dependency closure', build_image)
  self.assertLess(download, install)

 def test_triggers_and_jobs(self):
  self.assertIn("branches: [main, 'release/**']",W); self.assertIn("cron: '17 3 * * *'",W); self.assertIn('workflow_dispatch:',W); self.assertIn('update_channel:',W); self.assertIn('release_version:',W); self.assertIn('signing_mode:',W)
  self.assertIn("SIGNING_MODE: ${{ github.event_name == 'pull_request' && 'github' || 'local' }}", W)
  self.assertIn("'dev-release'", W)
  self.assertEqual(W.count("- '**/*.md'"), 2)
  self.assertEqual(W.count("- 'docs/**'"), 2)
  self.assertIn('prepare-public-inputs:',W); self.assertNotIn('publish-dev:',W); self.assertNotIn('publish-production:',W)
  self.assertIn('name: libreecho-${{ steps.dev-build-name.outputs.value }}',W)
  self.assertIn('path: ${{ runner.temp }}/libreecho-build/out/runs/*',W)
  self.assertIn('release-source-commits.txt', W)
  self.assertIn('ref="${GITHUB_REF_NAME#release/}"',W)
  self.assertIn('PRODUCT_SHA: ${{ needs.resolve-and-preflight.outputs.product_sha }}',W)
  self.assertIn('"${PRODUCT_SHA:0:7}"',W)
  self.assertNotIn('"${GITHUB_SHA:0:7}"',W)
  self.assertIn('LIBREECHO_UPDATE_CHANNEL: ${{ needs.resolve-and-preflight.outputs.channel }}',W)
  self.assertIn("workflows: ['Hosted LibreEcho build and release']", PUBLISH)
  self.assertIn("github.event.workflow_run.event == 'push'", PUBLISH)
  self.assertIn("github.event.workflow_run.head_branch == 'main'", PUBLISH)
  self.assertIn('prepare-dev-release.py', PUBLISH)
  self.assertIn('make_latest=false', PUBLISH)
  self.assertIn('CC-BY-NC-SA-4.0 (noncommercial and ShareAlike)', PUBLISH)
  self.assertIn('TTS voice assets remain CC-BY-SA-4.0', PUBLISH)
  self.assertIn('-F draft=true', PUBLISH)
  self.assertIn('-F draft=false', PUBLISH)
  self.assertIn('dev_release=ALREADY_PUBLISHED', PUBLISH)
  self.assertIn('radar-puffin-nightly-', PUBLISH)
  self.assertIn('nightly_retention=DELETED', PUBLISH)
  self.assertIn('group: hosted-dev-release-${{ github.event.workflow_run.id }}', PUBLISH)
  self.assertIn('cancel-in-progress: false', PUBLISH)
  self.assertIn('-f ref="refs/tags/$tag" -f sha="$HEAD_SHA"', PUBLISH)
  self.assertIn("'.object.type == \"commit\" and .object.sha == $sha'", PUBLISH)
  self.assertIn('expected_asset_count=14', PUBLISH)
  self.assertIn('expected_asset_count=15', PUBLISH)
  self.assertIn('expected_asset_count=19', PUBLISH)
  self.assertIn('prepare-stable-release.py', PUBLISH)
  self.assertIn('publish-stable:', PUBLISH)
  self.assertIn("github.event.workflow_run.event == 'workflow_dispatch'", PUBLISH)
  self.assertNotIn('aslater3/LibreEcho-Build', PUBLISH)
  self.assertIn('component_ref="release/$RELEASE_VERSION"', W)
  self.assertIn('coordinated component ref is missing', W)
  self.assertIn('Install reviewed OTA signing dependency closure', W)
  self.assertIn('--no-index', W)
  self.assertIn('--find-links "$wheelhouse"', W)
  self.assertIn('--require-hashes', W)
  self.assertIn('--requirement "$wheelhouse/requirements.txt"', W)
  self.assertIn('(cd "$deps" && sha256sum -c SHA256SUMS)', W)
  self.assertNotIn('pip install --disable-pip-version-check PyNaCl==1.5.0', W)
  self.assertIn('Verify stable UI version marker', W)
  self.assertIn('UI VERSION=', W)
  self.assertIn('"$GITHUB_BASE_REF" == release/*', W)
  self.assertIn('component_ref="$GITHUB_BASE_REF"', W)
 def test_release_lanes_and_ota_boundaries(self):
  self.assertIn('LIBREECHO_OTA_SIGNING_MODE', W)
  self.assertIn('LIBREECHO_OTA_SIGNING_KEY_HEX', W)
  self.assertIn('LIBREECHO_SSH_ROOT_PASSWORD_HASH', W)
  self.assertIn('LIBREECHO_SSH_ENABLED', W)
  self.assertIn('Materialize protected SSH root password hash', W)
  self.assertIn('dropbear_sha256', (ROOT/'build/ci/prepare-dev-release.py').read_text())
  self.assertIn('stable-release', W)
  self.assertIn('local) ;;', PUBLIC_WRAPPER)
  self.assertIn('stable releases require local OTA signing', PUBLIC_WRAPPER)
  self.assertIn('libreecho-${RELEASE_TAG}.ota.tar', STABLE_PUBLISHER)
  self.assertIn('prerelease=false', STABLE_PUBLISHER)
  self.assertIn('make_latest=true', STABLE_PUBLISHER)
  self.assertNotIn('LibreEcho-Build', PUBLISH)
  self.assertIn('generate-release-notes.py', PUBLISH)
  self.assertIn('Cross-repository included changes', (ROOT/'build/ci/generate-release-notes.py').read_text())
  self.assertIn('CC-BY-NC-SA-4.0', (ROOT/'build/ci/generate-release-notes.py').read_text())
  self.assertIn('No local release command', (ROOT/'build/README.md').read_text())

 def test_nightly_release_tag_is_accepted(self):
  installer = (ROOT/'tools/libreecho-install.py').read_text()
  self.assertIn('nightly-[0-9a-f-]+', installer)
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
