#!/usr/bin/env python3
"""Deployment-orchestration source identity contracts."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
SOURCE_STATE_SCRIPTS = (
    "build.sh",
    "status.sh",
    "publish-external-candidate.sh",
    "flash.sh",
)


class DeploymentSourceStateTests(unittest.TestCase):
    def test_public_release_wifi_profile_is_credential_free(self) -> None:
        build = (ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertIn('IMAGE_WIFI_CONFIG="$WIFI_CONFIG"', build)
        self.assertIn('if [[ "$PUBLIC_RELEASE_MODE" == 1 ]]; then', build)
        self.assertIn('prepare-public-wifi-config.sh', build)
        self.assertIn('--wifi-config "$IMAGE_WIFI_CONFIG"', build)

        with tempfile.TemporaryDirectory(prefix="libreecho-public-wifi-") as name:
            output = Path(name) / "public-wpa_supplicant.conf"
            result = subprocess.run(
                [str(ROOT / "prepare-public-wifi-config.sh"), str(output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            profile = output.read_text(encoding="utf-8")
            self.assertNotRegex(profile, r"(?m)^\\s*(ssid|psk)\\s*=")
            self.assertIn("update_config=1", profile)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_ci_generated_roots_are_excluded_from_build_source_state(self) -> None:
        for relative in (
            "sources/product/.git",
            "sources/platform/.git",
            "sources/linux/.git",
            "sources/ui/.git",
            "ci-artifacts/manifest.json",
        ):
            with self.subTest(path=relative):
                result = subprocess.run(
                    ["git", "check-ignore", "--no-index", "--quiet", "--", relative],
                    cwd=ROOT,
                    check=False,
                )
                self.assertEqual(result.returncode, 0)

    def test_all_source_state_hashers_use_binary_full_index_diff(self) -> None:
        required = 'git -C "$repository" diff --binary --full-index HEAD'
        obsolete = 'git -C "$repository" diff --binary HEAD'
        for name in SOURCE_STATE_SCRIPTS:
            with self.subTest(script=name):
                source = (ROOT / name).read_text(encoding="utf-8")
                self.assertIn(required, source)
                self.assertNotIn(obsolete, source)

    def test_component_cache_is_explicit_content_addressed_and_fail_closed(self) -> None:
        build = (ROOT / "build.sh").read_text(encoding="utf-8")
        cache = (ROOT / "component-cache.py").read_text(encoding="utf-8")
        wrapper = (ROOT / "ci/build-community-noncommercial.sh").read_text(encoding="utf-8")
        component = (ROOT / "ci/build-component.sh").read_text(encoding="utf-8")
        workflow = (ROOT / ".github/workflows/component-iteration.yml").read_text(encoding="utf-8")
        for expected in (
            'COMPONENT_CACHE_ROOT="${LIBREECHO_COMPONENT_CACHE_ROOT:-$BUILD_ROOT/component-cache}"',
            'REUSE_COMPONENT_CACHE="${LIBREECHO_REUSE_COMPONENT_CACHE:-0}"',
            'component_cache_key()',
            'component_cache_restore()',
            'component_cache_store()',
            '--tree "platform-airplay=$TOOLS_DIR/airplay"',
            'component_cache_store airplay',
            'component_cache_store busybox',
            'component_cache_store musl',
            'component_cache_store ui-bundle',
            'component_cache_store assistant-curl',
            'assistant_curl_cache_key=',
            'ui_bundle_cache_key=',
            # AirPlay cache payload must carry the relink object tree too, so
            # a cache hit still satisfies the corresponding-source offer.
            '"$AIRPLAY_STAGE"',
            '"$RUN/components/airplay"',
            'AirPlay cache payload is incomplete',
            # Codex P1: version the payload layout so legacy single-directory
            # entries on a runner cannot satisfy the new layout.
            '--value "payload_layout=v2"',
        ):
            self.assertIn(expected, build)
        for expected in (
            '"schema": SCHEMA',
            'actual = _manifest_fd(payload_fd, component, key)',
            'refusing to overwrite existing restore destination',
            'def _copy_tree_fd(source_fd: int, destination_fd: int)',
            'src_dir_fd=',
            'dst_dir_fd=',
            'def _key_file_digest(path: Path)',
            'f"symlink\\0mode={mode:o}\\0content=',
            # Git metadata changes on every checkout and is never a build
            # input; the tree digest must skip it or keys never reproduce.
            'tree-digest-v6',
            'if entry.name == ".git":',
        ):
            self.assertIn(expected, cache)
        self.assertIn('export JOBS=2', wrapper)
        self.assertNotIn('"$RUNS" "$COMPONENT_CACHE_ROOT"', build)
        self.assertNotIn('export JOBS="${JOBS:-2}"', wrapper)
        self.assertIn('FFmpeg static install', wrapper)
        # Codex P1: case-folding must stay limited to permission/no-space
        # phrases; a whole-allowlist -i leaks lowercase private source lines.
        self.assertIn("grep -iE '(^|[[:space:]])(permission denied|No space left)'", wrapper)
        self.assertNotIn("grep -iE '(^|[[:space:]])(ERROR", wrapper)
        self.assertIn('LIBREECHO_COMPONENT_CACHE_ROOT', wrapper)
        self.assertIn('LIBREECHO_REUSE_COMPONENT_CACHE', wrapper)
        self.assertIn('$LIBREECHO_CI_STATE_ROOT/component-cache', wrapper)
        # Pipeline v2: persistent incremental kernel output, stamped only after
        # a successful build, rebuilt from defconfig when the kernel lock moves.
        self.assertIn('kernel_out="$LIBREECHO_CI_STATE_ROOT/kernel-out"', wrapper)
        self.assertIn('kernel_stamp="$kernel_out/.libreecho-kernel-digest"', wrapper)
        self.assertIn('defconfig_flag="--defconfig"', wrapper)
        self.assertIn('printf \'%s\' "$kernel_head" >"$kernel_stamp"', wrapper)
        for expected in (
            'component="${1:?usage: build-component.sh <assistant-curl|airplay>}"',
            'assistant-curl|airplay) ;;',
            'component-cache.py',
            'component assistant-curl',
            'component=airplay',
            'LIBREECHO_ASSISTANT_RELINK_OUTPUT=',
            'cache=stored',
            'component cache restore failed for $name',
            '--component airplay',
            '--value "payload_layout=v2"',
        ):
            self.assertIn(expected, component)
        for expected in (
            'workflow_dispatch:',
            'assistant-curl',
            'airplay',
            'self-hosted, linux, x64, libreecho-image-builder',
            'ci/build-component.sh',
            'Verify locked Platform identity',
        ):
            self.assertIn(expected, workflow)

        source = (ROOT / "publish-external-candidate.sh").read_text(encoding="utf-8")
        self.assertIn('OUT="${LIBREECHO_DEPLOY_OUT:-$PIPELINE/out}"', source)
        self.assertIn('LIBREECHO_CURRENT="$CURRENT" "$PIPELINE/status.sh"', source)

    def test_status_and_flash_can_target_authoritative_output(self) -> None:
        for name in ("status.sh", "flash.sh"):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn('OUT="${LIBREECHO_DEPLOY_OUT:-$PIPELINE/out}"', source)

    def test_status_and_flash_pass_required_runtime_identities(self) -> None:
        required_reads = (
            'feature_policy="$(get_current feature_policy)"',
            'busybox_sha="$(get_current busybox_sha256)"',
            'musl_loader_sha="$(get_current musl_loader_sha256)"',
            'update_channel="$(get_current update_channel)"',
        )
        required_args = (
            '--expected-feature-policy "$feature_policy"',
            '--expected-busybox-sha256 "$busybox_sha"',
            '--expected-musl-loader-sha256 "$musl_loader_sha"',
            '--expected-update-channel "$update_channel"',
        )
        for name in ("status.sh", "flash.sh"):
            source = (ROOT / name).read_text(encoding="utf-8")
            with self.subTest(script=name):
                for expected in required_reads + required_args:
                    self.assertIn(expected, source)

    def test_status_does_not_require_retired_input_binaries(self) -> None:
        source = (ROOT / "status.sh").read_text(encoding="utf-8")
        self.assertNotIn("sha256sum -c SHA256SUMS", source)
        self.assertNotIn('INPUTS="$PIPELINE/inputs"', source)

    def test_adbd_requires_explicit_exported_uapi_headers(self) -> None:
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertIn(
            'ADBD_KERNEL_HEADERS="${LIBREECHO_ADBD_KERNEL_HEADERS:?ERROR:',
            source,
        )
        self.assertNotIn(
            'LIBREECHO_ADBD_KERNEL_HEADERS:-/usr/arm-linux-gnueabihf/include',
            source,
        )

    def test_wpa_builder_receives_the_exported_uapi_headers(self) -> None:
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertIn(
            '"$TOOLS_DIR/wpa-supplicant/build_wpa_supplicant.sh"',
            source,
        )
        self.assertIn('--kernel-headers "$ADBD_KERNEL_HEADERS"', source)

    def test_wireless_tools_builder_is_offline_and_source_metadata_is_packaged(self) -> None:
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertIn(
            'WIRELESS_TOOLS_SOURCE_ARCHIVE="${LIBREECHO_WIRELESS_TOOLS_SOURCE_ARCHIVE:?ERROR:',
            source,
        )
        self.assertIn('--archive "$WIRELESS_TOOLS_SOURCE_ARCHIVE"', source)
        self.assertIn('--output "$WIRELESS_TOOLS_OUTPUT"', source)
        self.assertIn('--native-root "$OTA_MUSL_NATIVE_ROOT"', source)
        self.assertIn('--iwconfig-source-metadata "$RUN/wireless-tools-source.json"', source)
        self.assertIn('WIRELESS_REGDB_SOURCE_ARCHIVE="${LIBREECHO_WIRELESS_REGDB_SOURCE_ARCHIVE:?ERROR:', source)
        self.assertIn('wireless-regdb/build_wireless_regdb.sh', source)
        self.assertIn('wireless-regdb-source.json', source)
        self.assertIn('LIBSODIUM_SOURCE_ARCHIVE="${LIBREECHO_LIBSODIUM_SOURCE_ARCHIVE:?ERROR:', source)
        self.assertIn('ota/build_libsodium.sh', source)
        self.assertIn('libsodium-source.json', source)
        self.assertIn('install -m 0644 "$WIRELESS_TOOLS_OUTPUT/wireless-tools-source.json"', source)
        self.assertNotIn(
            'LIBREECHO_PIPELINE_ROOT="$BUILD_ROOT" \\\n  "$NETWORK_TOOLS_BUILDER"',
            source,
        )

    def test_image_builder_receives_wpa_source_metadata(self) -> None:
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertIn('--wpa-source-metadata "$RUN/wpa-supplicant-source.json"', source)

    def test_tracked_public_inputs_contain_no_private_workspace_paths(self) -> None:
        result = subprocess.run(
            ["git", "ls-files", "inputs"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        for relative in result.stdout.splitlines():
            path = ROOT / relative
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            with self.subTest(path=relative):
                self.assertNotIn("mt8163-arm32-wifi-candidate", text)
                self.assertNotIn("/home/andy/", text)

    def test_configure_does_not_reference_retired_workspace(self) -> None:
        source = (ROOT / "configure.sh").read_text(encoding="utf-8")
        self.assertNotIn("mt8163-arm32-wifi-candidate", source)
        self.assertNotIn("/home/andy/workspaces/libreecho", source)

    def test_gold_build_requires_explicit_sources_and_has_no_host_paths(self) -> None:
        source = (ROOT / "build-gold.sh").read_text(encoding="utf-8")
        self.assertNotIn("/home/andy/", source)
        for variable in (
            "LIBREECHO_KERNEL_SRC", "LIBREECHO_TOOLING_SRC", "LIBREECHO_UI_SRC",
            "LIBREECHO_TTS_NORTHERN_MALE_MODEL", "LIBREECHO_WAKE_ORT_SOURCE",
            "LIBREECHO_STT_MODEL_ROOT",
        ):
            self.assertIn(f'${{{variable}:?', source)

    def test_external_publisher_preserves_ota_service_profile(self) -> None:
        source = (ROOT / "publish-external-candidate.sh").read_text(encoding="utf-8")
        self.assertIn('--service-profile "$service_profile"', source)
        self.assertIn("values.get('service_profile') != expected_service", source)

    def test_external_publisher_preserves_generated_runtime_identities(self) -> None:
        source = (ROOT / "publish-external-candidate.sh").read_text(encoding="utf-8")
        copy_loop = source.split("for key in ", 1)[1].split("; do", 1)[0]
        self.assertIn('copy_current_key "$key"', source)
        for key in ("busybox_sha256", "musl_loader_sha256"):
            with self.subTest(key=key):
                self.assertIn(key, copy_loop)

    def test_public_inputs_contain_no_retired_opaque_runtime_binaries(self) -> None:
        retired = (
            "busybox-arm32",
            "ld-musl-armhf.so.1",
            "connectivity-helpers/wmt_configure",
            "connectivity-helpers/wmt_responder",
            "connectivity-helpers/wmt_bt_on",
            "connectivity-helpers/wmt_stock_compat",
            "connectivity-helpers/wmt_launcher",
            "connectivity-helpers/wpa_supplicant",
            "stock-root-v184/sbin/adbd",
        )
        for relative in retired:
            self.assertFalse((ROOT / "inputs" / relative).exists(), relative)
        checksums = (ROOT / "inputs/SHA256SUMS").read_text(encoding="utf-8")
        for relative in retired:
            self.assertNotIn(f"  {relative}\n", checksums)

    def test_public_inputs_contain_no_private_connectivity_inspection_traces(self) -> None:
        for relative in (
            "connectivity-helpers/bt-no-gate.out",
            "connectivity-helpers/bt-null.out",
            "connectivity-helpers/bt-null.strace",
            "connectivity-helpers/patch-inspection.out",
            "connectivity-helpers/responder-negative.out",
        ):
            self.assertFalse((ROOT / "inputs" / relative).exists(), relative)

    def test_assistant_daemon_is_explicitly_static_linked(self) -> None:
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        start = source.index("=== building static ARM32 streamed assistant daemon ===")
        end = source.index('AGENT_DAEMON="$UI_SOURCE/build/libreecho-agentd"', start)
        invocation = source[start:end]
        self.assertIn("LDFLAGS=-static", invocation)
        self.assertIn("build/libreecho-agentd", invocation)

    def test_feature_builds_use_explicit_dependency_roots(self) -> None:
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        for variable in (
            "LIBREECHO_SHERPA_SOURCE",
            "LIBREECHO_SHERPA_PREFIX",
            "LIBREECHO_ORT_BUILD",
            "LIBREECHO_ORT_PREFIX",
            "LIBREECHO_WAKE_FLATBUFFERS_PYTHON",
            "LIBREECHO_ESPEAK_SOURCE",
            "LIBREECHO_FLITE_SOURCE",
            "LIBREECHO_SPEEX_PREFIX",
        ):
            self.assertIn(f'${{{variable}:?ERROR:', source)
        for make_variable in (
            "SHERPA_PREFIX", "ORT_BUILD", "ORT_PREFIX", "RE2_ARCHIVE",
            "ESPEAK_SRC", "FLITE_SRC",
            "SPEEX_PREFIX", "ARM_SPEEX_PREFIX",
        ):
            self.assertIn(f'{make_variable}="$', source)
        self.assertNotIn("$(HOME)/workspace", source)
        self.assertIn(
            'LIBREECHO_WAKE_FLATBUFFERS_PYTHON="$WAKE_FLATBUFFERS_PYTHON"',
            source,
        )
        self.assertIn(
            'LIBREECHO_WAKE_RE2_ARCHIVE="$ORT_PREFIX/lib/libre2.a"', source
        )
        self.assertEqual(
            source.count('  RE2_ARCHIVE="$ORT_PREFIX/lib/libre2.a" \\\n'), 2
        )

    def test_source_offers_are_required_and_recorded_for_full_features(self) -> None:
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertIn(
            'SOURCE_OFFER_INPUTS="${LIBREECHO_SOURCE_OFFER_INPUTS:?ERROR:',
            source,
        )
        self.assertIn('ASSEMBLE_SOURCE_OFFERS="$PIPELINE/assemble-release-source-offers.sh"', source)
        self.assertIn('"$ASSEMBLE_SOURCE_OFFERS"', source)
        assembler = (ROOT / "assemble-release-source-offers.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('CORE_RUNTIME_SYSROOT=', source)
        self.assertIn('CORE_GCC_LIBDIR=', source)
        self.assertIn(
            'append_relink core_args "$RUN/components/ui-bundle/relink" ui',
            assembler,
        )
        self.assertIn(
            'append_relink core_args "$CORE_RUNTIME_SYSROOT/lib" glibc-runtime',
            assembler,
        )
        self.assertIn(
            'append_relink core_args "$CORE_GCC_LIBDIR" gcc-runtime', assembler
        )
        self.assertIn('append_relink stt_args "$ORT_BUILD" onnxruntime-build', assembler)
        self.assertIn('append_relink tts_args "$ORT_BUILD" onnxruntime-build', assembler)
        for relink_root in (
            "SHERPA_PREFIX", "ORT_PREFIX", "FLITE_SOURCE", "SPEEX_PREFIX",
            "WAKE_ORT_BUILD", "WAKE_SPEEX_PREFIX",
        ):
            self.assertIn(f'append_relink ', assembler)
            self.assertIn(f'"${relink_root}"', assembler)
        for component in (
            "core-runtime-closure", "airplay-payload", "stt-payload",
            "tts-payload", "wakeword-payload", "assistant-payload",
        ):
            self.assertIn(f'{component}.source-offer.tar.gz', source)
        self.assertIn("source_offer_manifest_sha256", source)

    def test_relink_outputs_are_requested_from_ephemeral_builders(self) -> None:
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertIn('LIBREECHO_AIRPLAY_RELINK_OUTPUT=', source)
        self.assertIn('LIBREECHO_ASSISTANT_RELINK_OUTPUT=', source)
        self.assertIn(
            'LIBREECHO_WAKE_RELINK_OUTPUT="$WAKE_BUILD_ROOT/wakeword/relink"', source
        )
        assembler = (ROOT / "assemble-release-source-offers.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'append_relink wake_relink '
            '"$RUN/components/wake-runtime/wakeword/relink" ui-wakeword',
            assembler,
        )

    def test_redistributable_policy_excludes_wakeword_only(self) -> None:
        build = (ROOT / "build.sh").read_text(encoding="utf-8")
        userdata = (ROOT / "prepare_userdata_tree.sh").read_text(encoding="utf-8")
        assembler = (ROOT / "assemble-release-source-offers.sh").read_text(encoding="utf-8")
        ota = (ROOT / "ota.sh").read_text(encoding="utf-8")
        publisher = (ROOT / "publish-external-candidate.sh").read_text(encoding="utf-8")
        self.assertIn("exclude|preserve|redistributable", build)
        self.assertIn("FEATURES_ENABLED=1", build)
        self.assertIn("WAKEWORD_ENABLED=0", build)
        self.assertIn('if [[ "$WAKEWORD_ENABLED" == 1 ]]', build)
        self.assertIn('"$FEATURE_POLICY"', assembler)
        self.assertIn('required=("airplay","assistant","stt","tts")', userdata)
        self.assertIn("expected_policy", ota)
        self.assertIn("expected_policy", publisher)

    def test_community_noncommercial_policy_includes_wakeword_everywhere(self) -> None:
        build = (ROOT / "build.sh").read_text(encoding="utf-8")
        userdata = (ROOT / "prepare_userdata_tree.sh").read_text(encoding="utf-8")
        assembler = (ROOT / "assemble-release-source-offers.sh").read_text(encoding="utf-8")
        ota = (ROOT / "ota.sh").read_text(encoding="utf-8")
        publisher = (ROOT / "publish-external-candidate.sh").read_text(encoding="utf-8")
        self.assertIn(
            "community-noncommercial) policy_token=community-nc; "
            "FEATURES_ENABLED=1; WAKEWORD_ENABLED=1; "
            "release_scope=community-noncommercial",
            build,
        )
        self.assertIn('--release-scope "$release_scope"', build)
        self.assertIn("preserve|community-noncommercial", assembler)
        self.assertIn('policy in ("preserve", "community-noncommercial")', userdata)
        self.assertIn("preserve|community-noncommercial", ota)
        self.assertIn("preserve|community-noncommercial", publisher)

    def test_run_id_policy_token_fits_signed_ota_version(self) -> None:
        build = (ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertIn("redistributable) policy_token=redistrib", build)
        self.assertIn("community-noncommercial) policy_token=community-nc", build)
        self.assertIn("-${policy_token}-ssh", build)

    def test_candidate_records_build_orchestrator_identity(self) -> None:
        build = (ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertIn("build_git_head=$build_head", build)
        self.assertIn("build_git_diff_sha256=$build_diffsha", build)
        self.assertIn('git -C "$PIPELINE" rev-parse HEAD', build)

    def test_ci_candidate_exports_portable_component_provenance(self) -> None:
        build = (ROOT / "build.sh").read_text(encoding="utf-8")
        wrapper = (ROOT / "ci/build-community-noncommercial.sh").read_text(encoding="utf-8")
        self.assertIn("provenance=$RUN/provenance.txt", build)
        self.assertIn("provenance_sha256=$provenance_sha", build)
        self.assertIn('values["components_manifest"] = "base/components.json"', wrapper)
        self.assertIn('values["provenance"] = "base/provenance.txt"', wrapper)
        self.assertIn('"component_timing": ("component-timing.log"', wrapper)
        self.assertIn('"component_identities": ("component-identities.log"', wrapper)
        self.assertIn("component_timing_sha256=$component_timing_sha", build)
        self.assertIn("component_identities_sha256=$component_identities_sha", build)
        self.assertIn('item["root"] = f"EXTERNAL_RUN_COMPONENT/{item[\'name\']}"', wrapper)
        self.assertNotIn(
            "boot.ramdisk.cpio.gz boot-envelope.bin manifest.json verify.log CURRENT.candidate",
            wrapper,
        )

    def test_ci_candidate_portable_provenance_rewriter_executes(self) -> None:
        wrapper = (ROOT / "ci/build-community-noncommercial.sh").read_text(encoding="utf-8")
        marker = (
            'python3 - "$run/CURRENT.candidate" "$artifacts/CURRENT.candidate" '
            '"$artifacts" <<\'PY\'\n'
        )
        script = wrapper.split(marker, 1)[1].split("\nPY\n", 1)[0]
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            run = root / "run"
            artifacts = root / "artifacts"
            (run / "features").mkdir(parents=True)
            artifacts.mkdir()
            for relative in (
                "audio_probe", "tinyplay", "tinycap", "tinymix", "iwconfig",
                "airplay-audio-contract.log",
                "features/airplay2.squashfs", "features/airplay2.manifest.json",
                "features/tts.squashfs", "features/tts.manifest.json",
                "features/wakeword.squashfs", "features/wakeword.manifest.json",
                "features/stt.squashfs", "features/stt.manifest.json",
                "features/assistant.squashfs", "features/assistant.manifest.json",
            ):
                path = run / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative)
            ui_manifest = root / "ui-manifest.txt"
            ui_manifest.write_text("ui")
            components = run / "components.json"
            components.write_text(json.dumps({
                "schema": "libreecho-components-v1",
                "components": [{
                    "name": "demo", "key": "a" * 64, "status": "hit",
                    "root": str(run / "components/demo"), "root_mode": 0o755,
                    "outputs": [],
                }],
            }))
            timing = run / "component-timing.log"
            timing.write_text("component_timing component=demo status=hit duration_ms=1\n")
            identities = run / "component-identities.log"
            identities.write_text(f"identity=core-toolchain sha256={'1' * 64}\n")
            provenance = run / "provenance.txt"
            provenance.write_text(
                f"schema=1\ncomponents_manifest={components}\n"
                f"components_manifest_sha256={'0' * 64}\n"
                f"build_source={root / 'checkout/build'}\n"
                f"kernel_source={root / 'checkout/linux'}\n"
                f"ui_source={root / 'ui-source'}\nboot_image={run / 'boot.img'}\n"
            )
            candidate = run / "CURRENT.candidate"
            fields = {
                "schema": "1",
                "components_manifest": str(components),
                "components_manifest_sha256": "0" * 64,
                "component_timing": str(timing),
                "component_timing_sha256": hashlib.sha256(timing.read_bytes()).hexdigest(),
                "component_identities": str(identities),
                "component_identities_sha256": hashlib.sha256(identities.read_bytes()).hexdigest(),
                "build_source": str(root / "checkout/build"),
                "kernel_source": str(root / "checkout/linux"),
                "provenance": str(provenance),
                "provenance_sha256": "0" * 64,
                "ui_manifest": str(ui_manifest),
                "ui_source": str(root / "ui-source"),
            }
            candidate.write_text("".join(f"{key}={value}\n" for key, value in fields.items()))
            output = artifacts / "CURRENT.candidate"
            result = subprocess.run(
                [sys.executable, "-", str(candidate), str(output), str(artifacts)],
                input=script, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            portable = dict(
                line.split("=", 1) for line in output.read_text().splitlines() if "=" in line
            )
            self.assertEqual(portable["components_manifest"], "base/components.json")
            self.assertEqual(portable["provenance"], "base/provenance.txt")
            self.assertEqual(portable["component_timing"], "base/component-timing.log")
            self.assertEqual(portable["component_identities"], "base/component-identities.log")
            self.assertEqual(portable["build_source"], "EXTERNAL_BUILD_INPUT")
            self.assertEqual(portable["kernel_source"], "EXTERNAL_BUILD_INPUT")
            portable_components = artifacts / portable["components_manifest"]
            portable_provenance = artifacts / portable["provenance"]
            self.assertEqual(
                portable["components_manifest_sha256"],
                hashlib.sha256(portable_components.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                portable["provenance_sha256"],
                hashlib.sha256(portable_provenance.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                json.loads(portable_components.read_text())["components"][0]["root"],
                "EXTERNAL_RUN_COMPONENT/demo",
            )
            self.assertNotIn(str(run), portable_provenance.read_text())
            self.assertNotIn(str(root), output.read_text())
            self.assertNotIn(str(root), portable_provenance.read_text())

    def test_audio_tools_use_exported_uapi_not_kernel_source(self) -> None:
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertIn('--kernel-headers "$ADBD_KERNEL_HEADERS"', source)
        self.assertIn('-I"$ADBD_KERNEL_HEADERS"', source)
        self.assertNotIn('--kernel-headers "$KERNEL_SRC"', source)

    def test_network_metadata_is_captured_after_clean_root_builds(self) -> None:
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertLess(
            source.index('"$NETWORK_TOOLS_BUILDER"'),
            source.index(
                'install -m 0644 "$WIRELESS_TOOLS_OUTPUT/wireless-tools-source.json"'
            ),
        )
        self.assertIn(
            'install -m 0644 "$WIRELESS_TOOLS_OUTPUT/wireless-tools-COPYING"',
            source,
        )
        self.assertLess(
            source.index('"$REGDB_BUILDER" --archive'),
            source.index(
                'install -m 0644 "$WIRELESS_REGDB_OUTPUT/wireless-regdb-source.json"'
            ),
        )

    def test_ui_builder_receives_exact_cross_and_musl_roots(self) -> None:
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertIn('LIBREECHO_UI_CROSS_COMPILE="$UI_CROSS"', source)
        self.assertIn(
            'LIBREECHO_UI_MUSL_NATIVE_ROOT="$OTA_MUSL_NATIVE_ROOT"', source
        )
        self.assertIn('LIBREECHO_UI_MUSL_SYSROOT="$OTA_MUSL_SYSROOT"', source)

    def test_device_actions_require_explicit_serials(self) -> None:
        adb_scripts = ("ota.sh", "stage_feature_payload.sh")
        fastboot_scripts = ("clear_expdb.sh", "format_userdata.sh")
        private_serial_prefix = "G2A0" + "RF"
        for name in adb_scripts:
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn('${ADB_SERIAL:?ERROR: set ADB_SERIAL explicitly}', source)
            self.assertNotIn(private_serial_prefix, source)
        for name in fastboot_scripts:
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn(
                '${FASTBOOT_SERIAL:?ERROR: set FASTBOOT_SERIAL explicitly}', source
            )
            self.assertNotIn(private_serial_prefix, source)

    def test_local_ota_signing_binds_bundle_to_build_manifest(self) -> None:
        build = (ROOT / "build.sh").read_text(encoding="utf-8")
        publisher = (ROOT / "publish-external-candidate.sh").read_text(encoding="utf-8")
        self.assertIn('--build-manifest "$RUN/manifest.json"', build)
        self.assertIn('--build-manifest "$RUN/manifest.json"', publisher)
        # The locally signed OTA bundle consumes $RUN/boot.img; the publisher
        # must materialize the verified boot image into the immutable run.
        self.assertIn('cp -- "$BOOT" "$RUN/boot.img"', publisher)
        # CURRENT.candidate is parsed as key=value; no heredoc line may carry a
        # leading space or status.sh/flash.sh cannot resolve the audio tools.
        tail_block = publisher.split("for key in busybox_sha256", 1)[1]
        candidate_block = tail_block.split("cat <<EOF", 1)[1].split("EOF", 1)[0]
        self.assertIn("audio_probe=$RUN/audio_probe", candidate_block)
        for line in candidate_block.splitlines():
            if line.strip():
                self.assertFalse(
                    line.startswith(" "),
                    f"CURRENT.candidate line carries a leading space: {line!r}",
                )

    def test_upstream_watch_inventory_and_workflow(self) -> None:
        import json as _json
        inventory_path = ROOT / "ci" / "pinned-components.json"
        inventory = _json.loads(inventory_path.read_text(encoding="utf-8"))
        components = inventory["components"]
        self.assertGreaterEqual(len(components), 16)
        names = {c["name"] for c in components}
        for required in (
            "curl", "ffmpeg", "shairport-sync", "nqptp", "tinyalsa",
            "busybox", "musl", "wpa_supplicant", "libsodium", "speexdsp",
            "dropbear", "openwakeword-alexa-model",
        ):
            self.assertIn(required, names)
        for component in components:
            self.assertIn("name", component)
            self.assertIn("pinned_version", component)
            self.assertIn("archive", component)
            self.assertIn("license", component)
            self.assertIn("watch", component)
            backend = component["watch"].get("backend")
            self.assertIn(backend, ("anitya", "github", "github-commit", "none"))
            if backend in ("anitya",):
                self.assertIn("anitya_project", component["watch"])
            if backend in ("github", "github-commit"):
                self.assertIn("github_repo", component["watch"])
        # Repo-relative archives must actually exist so the inventory cannot
        # drift from what the build pins.
        for component in components:
            archive = component["archive"]
            if archive.startswith("inputs/"):
                self.assertTrue(
                    (ROOT / archive).is_file(),
                    f"inventory archive missing: {archive}",
                )
        checker = (ROOT / "ci" / "check-upstream-updates.py").read_text(encoding="utf-8")
        self.assertIn("Never modifies any", checker.replace("\n", " ").replace("pin:", "pin:"))
        self.assertNotIn("write_text", checker)
        workflow = (ROOT / ".github/workflows/upstream-watch.yml").read_text(encoding="utf-8")
        self.assertIn("issues: write", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("ci/check-upstream-updates.py", workflow)
        # Watch-only: must never push, merge, or touch self-hosted builders.
        for forbidden in ("git push", "self-hosted", "libreecho-image-builder"):
            self.assertNotIn(forbidden, workflow)

    def test_ci_image_job_is_main_only_unsigned_and_bounded(self) -> None:
        workflow = (ROOT / ".github/workflows/image-ci.yml").read_text(
            encoding="utf-8"
        )
        wrapper = (ROOT / "ci/build-community-noncommercial.sh").read_text(
            encoding="utf-8"
        )
        build = (ROOT / "build.sh").read_text(encoding="utf-8")
        ota = (ROOT / "ota.sh").read_text(encoding="utf-8")
        status = (ROOT / "status.sh").read_text(encoding="utf-8")
        publisher = (ROOT / "publish-external-candidate.sh").read_text(encoding="utf-8")
        self.assertIn("needs: [source_checks, private_source_checks]", workflow)
        self.assertIn("private_source_checks:", workflow)
        self.assertGreaterEqual(
            workflow.count("environment: community-noncommercial-build"), 2
        )
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("github.event_name == 'schedule'", workflow)
        self.assertLess(
            workflow.index("github.ref == 'refs/heads/main'"),
            workflow.index("github.event_name == 'schedule'"),
        )
        # Pipeline v2: the expensive self-hosted lanes are release gates.  They
        # must never run on every push to main; push events keep hosted checks only.
        self.assertNotIn("github.event_name == 'push'", workflow)
        # Concurrency must be event-scoped: one shared group lets a dispatch
        # run supersede (and GitHub-cancel) the fast push-triggered checks.
        self.assertIn(
            "group: build-ci-${{ github.workflow }}-${{ github.ref }}-${{ github.event_name }}",
            workflow,
        )
        self.assertNotIn("pull_request_target", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertGreaterEqual(workflow.count("./ci/verify-source-lock.sh"), 3)
        self.assertIn("./ci/verify-source-lock.sh --public-only", workflow)
        self.assertIn("./ci/verify-source-lock.sh --ui-only", workflow)
        self.assertGreaterEqual(
            workflow.count("ssh-key: ${{ secrets.LIBREECHO_UI_DEPLOY_KEY }}"), 2
        )
        self.assertIn(
            "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            workflow,
        )
        self.assertIn("if: always()", workflow)
        for source_path in (
            "sources/product",
            "sources/platform",
            "sources/linux",
            "sources/ui",
        ):
            self.assertGreaterEqual(workflow.count(f"path: {source_path}"), 2)
        source_lock = (ROOT / "ci/source-lock.env").read_text(encoding="utf-8")
        locked_sources = dict(
            line.split("=", 1)
            for line in source_lock.splitlines()
            if "=" in line and not line.startswith("#")
        )
        self.assertEqual(
            set(locked_sources),
            {"PRODUCT_SHA", "PLATFORM_SHA", "LINUX_SHA", "UI_SHA"},
        )
        for name, commit in locked_sources.items():
            with self.subTest(source=name):
                self.assertRegex(commit, r"^[0-9a-f]{40}$")
        self.assertIn('update_channel="${LIBREECHO_UPDATE_CHANNEL:-dev}"', wrapper)
        self.assertIn('LIBREECHO_ORT_PREFIX="$deps/onnxruntime-arm32/install"', wrapper)
        self.assertNotIn('LIBREECHO_ORT_PREFIX="$deps/onnxruntime-arm32/install/lib"', wrapper)
        self.assertIn('stable|dev) ;;', wrapper)
        self.assertIn('export LIBREECHO_UPDATE_CHANNEL="$update_channel"', wrapper)
        self.assertIn('--update-channel "$update_channel"', wrapper)
        self.assertIn(
            "LIBREECHO_UPDATE_CHANNEL: ${{ inputs.update_channel || 'dev' }}",
            workflow,
        )
        self.assertNotIn(
            'run: LIBREECHO_UPDATE_CHANNEL=${{ inputs.update_channel',
            workflow,
        )
        self.assertIn('host_tools="$GITHUB_WORKSPACE/inputs/host-tools/bin"', wrapper)
        self.assertIn('host_tool_lib="$GITHUB_WORKSPACE/inputs/host-tools/lib"', wrapper)
        self.assertIn("16e17cb12bdc38ca009ecce61192925fa32fc9142ca62ee9d40861d625479242", wrapper)
        self.assertIn("find . -type f ! -name SHA256SUMS", wrapper)
        self.assertIn('values["ui_source"] = "EXTERNAL_UI_SOURCE"', wrapper)
        self.assertIn('"features/airplay2.squashfs"', wrapper)
        self.assertIn("LIBREECHO_OTA_SIGNING_MODE=github", wrapper)
        self.assertIn("unset LIBREECHO_OTA_SIGNING_KEY OTA_SIGNING_KEY_HEX", wrapper)
        self.assertIn("--update-channel", build)
        self.assertIn("update_channel=$UPDATE_CHANNEL", build)
        self.assertIn("update_channel", ota)
        self.assertIn("update_channel", status)
        self.assertIn("update_channel", publisher)
        self.assertIn('"$artifacts/ci-failure.log"', wrapper)
        self.assertIn("tail -c 131072", wrapper)
        self.assertIn('find "$state" -type f -name \'*.log\' -print0', wrapper)
        self.assertIn("Component builders write their own logs", wrapper)
        self.assertIn("ninja: build stopped", wrapper)
        self.assertIn("No space left", wrapper)
        self.assertNotIn('for log in configure.log build.log', wrapper)
        self.assertIn("no-sanitized-diagnostics-matched", wrapper)
        self.assertIn("<private-ui-source>", wrapper)
        for release_path in (
            '"$artifacts/release"',
            '"$artifacts/kernel-output"',
            'boot.ramdisk.cpio.gz',
            'boot-envelope.bin',
            'libreecho-radar-puffin.dtb',
            'features/$feature.squashfs',
            'features/$feature.manifest.json',
        ):
            self.assertIn(release_path, wrapper)
        self.assertIn("--no-publish", wrapper)
        self.assertNotIn("build-gold.sh", wrapper)
        self.assertNotIn("ota-signing-key.hex", wrapper)
        for forbidden in (
            '"$run/provenance.txt"',
            '"$run"/features/',
            '"$run"/*.ota.tar',
            '"$state/build.log" "$artifacts/',
        ):
            self.assertNotIn(forbidden, wrapper)

    def test_ci_channel_mapping_keeps_schedule_on_dev_and_dispatch_explicit(self) -> None:
        workflow = (ROOT / ".github/workflows/image-ci.yml").read_text(encoding="utf-8")
        wrapper = (ROOT / "ci/build-community-noncommercial.sh").read_text(encoding="utf-8")
        build_step = workflow.split(
            "      - name: Build and verify immutable no-publish candidate\n", 1
        )[1].split("      - name: Stage deterministic public release assets\n", 1)[0]

        # The workflow expression is evaluated by Actions: dispatch passes its
        # choice, while schedule has no input and therefore selects dev.
        self.assertIn(
            "update_channel:\n"
            "        description: OTA channel for this release\n"
            "        required: true\n"
            "        default: stable\n"
            "        type: choice\n"
            "        options: [stable, dev]",
            workflow,
        )
        self.assertIn(
            "LIBREECHO_UPDATE_CHANNEL: ${{ inputs.update_channel || 'dev' }}",
            build_step,
        )
        self.assertIn("run: ./ci/build-community-noncommercial.sh", build_step)
        self.assertNotIn("${{", build_step.split("run:", 1)[1])

        # The wrapper is the final validation boundary even if the environment
        # is supplied outside Actions; only stable/dev reach build.sh.
        self.assertIn('update_channel="${LIBREECHO_UPDATE_CHANNEL:-dev}"', wrapper)
        self.assertIn('case "$update_channel" in', wrapper)
        self.assertIn("stable|dev) ;;", wrapper)
        self.assertIn('export LIBREECHO_UPDATE_CHANNEL="$update_channel"', wrapper)
        self.assertIn('--update-channel "$update_channel"', wrapper)
        self.assertIn("ERROR: unsupported OTA update channel", wrapper)

    def test_pinned_plistutil_has_a_hash_bound_runtime_closure(self) -> None:
        build = (ROOT / "build.sh").read_text(encoding="utf-8")
        wrapper = (ROOT / "ci/build-community-noncommercial.sh").read_text(
            encoding="utf-8"
        )
        component = (ROOT / "ci/build-component.sh").read_text(encoding="utf-8")
        manifest_path = ROOT / "inputs/host-tools/manifest.json"
        runtime = ROOT / "inputs/host-tools/lib/libplist-2.0.so.4"
        copyright_path = ROOT / "inputs/host-tools/share/libplist/copyright"
        license_path = ROOT / "inputs/host-tools/share/libplist/COPYING.LESSER"
        source_dir = ROOT / "inputs/host-tools/source"
        source_hashes = {
            "libplist_2.3.0.orig.tar.bz2":
                "4e8580d3f39d3dfa13cefab1a13f39ea85c4b0202e9305c5c8f63818182cac61",
            "libplist_2.3.0-1~exp2build2.debian.tar.xz":
                "32822ad066b08869544933faf1e53e2b351a4a6ee506428ae405da07f300bf93",
            "libplist_2.3.0-1~exp2build2.dsc":
                "9a91a02870e95a521a552050d032927f5009dd2e7c840da21057ce56b23433a9",
        }

        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], 1)
        self.assertEqual(manifest["package_version"], "2.3.0-1~exp2build2")
        self.assertEqual(
            manifest["plistutil_sha256"],
            "16e17cb12bdc38ca009ecce61192925fa32fc9142ca62ee9d40861d625479242",
        )
        self.assertEqual(
            manifest["libplist_sha256"],
            "4177ee2d671eacd7b1a952702ef7e14a479acede369396b2a532de1f142491a4",
        )
        self.assertEqual(manifest["license"], "LGPL-2.1-or-later")
        self.assertTrue(runtime.is_file())
        self.assertFalse(runtime.is_symlink())
        self.assertEqual(
            hashlib.sha256(runtime.read_bytes()).hexdigest(),
            manifest["libplist_sha256"],
        )
        self.assertTrue(copyright_path.is_file())
        self.assertTrue(license_path.is_file())
        license_text = license_path.read_text(encoding="utf-8")
        self.assertIn("GNU LESSER GENERAL PUBLIC LICENSE", license_text)
        self.assertIn("Version 2.1, February 1999", license_text)
        self.assertEqual(manifest["source_files"], source_hashes)
        for name, expected in source_hashes.items():
            source_file = source_dir / name
            self.assertTrue(source_file.is_file())
            self.assertEqual(hashlib.sha256(source_file.read_bytes()).hexdigest(), expected)

        env = dict(**__import__("os").environ)
        env["LD_LIBRARY_PATH"] = str(runtime.parent)
        trace = subprocess.run(
            ["ldd", str(ROOT / "inputs/host-tools/bin/plistutil")],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        self.assertIn(f"libplist-2.0.so.4 => {runtime}", trace)

        build_gold = (ROOT / "build-gold.sh").read_text(encoding="utf-8")
        self.assertNotIn("LIBREECHO_AIRPLAY_HOST_BIN", build)
        self.assertNotIn("LIBREECHO_AIRPLAY_HOST_LIB", build)
        self.assertNotIn("LIBREECHO_INPUTS_ROOT", build)
        self.assertNotIn("LIBREECHO_AIRPLAY_HOST_BIN", build_gold)
        self.assertNotIn("LIBREECHO_AIRPLAY_HOST_LIB", build_gold)
        self.assertNotIn("export LIBREECHO_AIRPLAY_HOST_BIN", wrapper)
        self.assertNotIn("export LIBREECHO_AIRPLAY_HOST_LIB", wrapper)
        self.assertIn('AIRPLAY_HOST_BIN="$INPUTS/host-tools/bin"', build)
        self.assertIn('AIRPLAY_HOST_LIB="$INPUTS/host-tools/lib"', build)
        self.assertIn("host-tools/bin/plistutil", build)
        self.assertIn("host-tools/lib/libplist-2.0.so.4", build)
        self.assertIn('--tree "host-lib=$AIRPLAY_HOST_LIB"', build)
        self.assertIn("LD_LIBRARY_PATH=$AIRPLAY_HOST_LIB", build)
        allowlist_probe = "-mindepth 1 -maxdepth 1 -printf '%f\\n'"
        self.assertGreaterEqual(build.count(allowlist_probe), 2)
        self.assertGreaterEqual(wrapper.count(allowlist_probe), 2)
        self.assertGreaterEqual(component.count(allowlist_probe), 2)
        self.assertLess(
            build.index(allowlist_probe),
            build.index('"$AIRPLAY_PLISTUTIL" --help'),
        )
        self.assertLess(
            build.index('verify_pinned_input "$pinned_input"'),
            build.index('"$AIRPLAY_PLISTUTIL" --help'),
        )
        self.assertLess(
            component.index('sha256sum "$host_lib/libplist-2.0.so.4"'),
            component.index('"$host_bin/plistutil" --help'),
        )
        self.assertIn(
            "4177ee2d671eacd7b1a952702ef7e14a479acede369396b2a532de1f142491a4",
            wrapper,
        )

    def test_direct_musl_compiles_load_native_toolchain_libraries(self) -> None:
        source = (ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertIn(
            'env LD_LIBRARY_PATH="$OTA_MUSL_NATIVE_ROOT/usr/lib:'
            '$OTA_MUSL_NATIVE_ROOT/lib"', source
        )
        self.assertIn('"$AUDIO_CC" --sysroot="$OTA_MUSL_SYSROOT"', source)
        self.assertIn('-Os -ffunction-sections -fdata-sections -static -no-pie', source)


if __name__ == "__main__":
    unittest.main()
