#!/usr/bin/env python3
"""Contract checks for the pinned ARM32 mbedTLS UI dependency.

HTTPS issue aslater3/LibreEcho-UI#250: the shipped Web UI advertised an HTTPS
toggle that could never listen because the production UI bundle was linked from
the stub TLS implementation.  Platform PR aslater3/LibreEcho-Platform#164 makes
the bundle builder require a pinned ARM32 Mbed TLS prefix and fail closed
without it.  These checks cover the Product half of that contract:

* the pinned public source archive and the pinned build interpreter;
* the ``mbedtls-arm32`` component (key, build, materialize, identity record);
* the UI bundle cache key binding, so a cached stub bundle cannot be reused;
* fail-closed behaviour of the shipped component block when any pinned input
  is missing, mismatched, or unavailable.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).parents[2]
BUILD = ROOT / "build/build.sh"
CACHE_TOOL = ROOT / "build/component-cache.py"
INVENTORY = ROOT / "build/inputs/public-inputs.json"
CATALOG = ROOT / "release/components.json"
WORKFLOW = ROOT / ".github/workflows/build-release.yml"
FETCHER = ROOT / "build/ci/fetch-public-deps.py"
WHEELHOUSE = ROOT / "build/inputs/reviewed/python-wheels"
REQUIREMENTS = WHEELHOUSE / "mbedtls-build-requirements.txt"
INTERPRETER_STEP = "Install the pinned mbedTLS build interpreter"

MBEDTLS_VERSION = "3.6.4"
# The exact archive pin recorded by the Platform mbedtls SOURCE.lock and by the
# Product public-input inventory.  Both halves of the fix must agree on it.
MBEDTLS_SOURCE_SHA256 = (
    "ec35b18a6c593cf98c3e30db8b98ff93e8940a8c4e690e66b41dfc011d678110"
)
MBEDTLS_SOURCE_URL = (
    "https://github.com/Mbed-TLS/mbedtls/releases/download/mbedtls-3.6.4/"
    "mbedtls-3.6.4.tar.bz2"
)
MBEDTLS_ARCHIVE_NAME = "mbedtls-3.6.4.tar.bz2"
BLOCK_START = "# --- mbedtls-arm32 component block"
BLOCK_END = "# --- end mbedtls-arm32 component block ---"
BUNDLE_KEY_START = 'ui_bundle_cache_key="$(component_cache_key ui-bundle'
POLICY = {
    "jinja2": "3.1.6",
    "MarkupSafe": "3.0.3",
    "jsonschema": "4.25.1",
    "attrs": "26.1.0",
    "jsonschema-specifications": "2025.9.1",
    "referencing": "0.37.0",
    "rpds-py": "2026.6.3",
    "typing-extensions": "4.16.0",
}


def load_module(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inventory_records() -> dict[str, dict]:
    data = json.loads(INVENTORY.read_text())
    return {item["name"]: item for item in data["inputs"]}


def catalog_components() -> dict[str, dict]:
    data = json.loads(CATALOG.read_text())
    return {item["id"]: item for item in data["components"]}


def extract_function(source: str, name: str) -> str:
    start = source.index(f"\n{name}() {{\n") + 1
    end = source.index("\n}\n", start) + 3
    return source[start:end]


def extract_assignment(source: str, name: str) -> str:
    for line in source.splitlines():
        if line.startswith(f"{name}="):
            return line + "\n"
    raise AssertionError(f"build/build.sh has no {name} assignment")


def extract_block(source: str) -> str:
    start = source.index(BLOCK_START)
    start = source.index("\n", start) + 1
    return source[start:source.index(BLOCK_END, start)]


def workflow_step_script(name: str) -> str:
    workflow = WORKFLOW.read_text()
    marker = f"      - name: {name}\n"
    assert marker in workflow, f"workflow step is missing: {name}"
    step = workflow.split(marker, 1)[1].split("\n      - ", 1)[0]
    return textwrap.dedent(step.split("        run: |\n", 1)[1])


class MbedtlsPublicInputTests(unittest.TestCase):
    def test_source_archive_pin_matches_the_platform_lock(self):
        record = inventory_records()["mbedtls"]
        self.assertEqual(record["kind"], "source-archive")
        self.assertEqual(record["redistribution"], "cleared")
        self.assertEqual(record["license"], "Apache-2.0")
        self.assertEqual(record["sha256"], MBEDTLS_SOURCE_SHA256)
        self.assertEqual(record["url"], MBEDTLS_SOURCE_URL)
        # The pipeline stages the archive under its upstream basename.
        self.assertEqual(record["url"].rsplit("/", 1)[-1], MBEDTLS_ARCHIVE_NAME)
        self.assertEqual(
            MBEDTLS_SOURCE_URL.rsplit("/", 1)[-1], MBEDTLS_ARCHIVE_NAME
        )
        # A cleared record makes the public fetcher download and digest it.
        module = load_module("fetch_public_deps", FETCHER)
        module.load(INVENTORY)
        self.assertEqual(
            {"mbedtls": MBEDTLS_ARCHIVE_NAME},
            {
                name: module.NAMES.get(name, MBEDTLS_SOURCE_URL.rsplit("/", 1)[-1])
                for name in ("mbedtls",)
            },
        )

    def test_workflow_stages_the_pinned_archive_and_build_interpreter(self):
        workflow = WORKFLOW.read_text()
        self.assertIn(
            "LIBREECHO_MBEDTLS_SOURCE_ARCHIVE: "
            "${{ runner.temp }}/public-deps/" + MBEDTLS_ARCHIVE_NAME,
            workflow,
        )
        self.assertIn(
            "LIBREECHO_MBEDTLS_BUILD_PYTHON: "
            "${{ runner.temp }}/mbedtls-build-venv/bin/python",
            workflow,
        )

    def test_catalog_and_source_offer_carry_license_and_provenance(self):
        component = catalog_components()["mbedtls"]
        record = inventory_records()["mbedtls"]
        self.assertEqual(component["version"], MBEDTLS_VERSION)
        self.assertEqual(component["license"], record["license"])
        self.assertEqual(component["release_status"], "cleared")
        self.assertEqual(component["distribution_scope"], "core-image")
        self.assertEqual(component["download_location"], record["url"])
        self.assertEqual(component["source_offer"], record["url"])
        self.assertEqual(component["source_archive_sha256"], record["sha256"])
        for evidence in ("release/THIRD_PARTY_NOTICES.md",
                         "release/CORE-RUNTIME-SOURCE-OFFER.md"):
            self.assertIn(evidence, component["evidence"])
        notices = (ROOT / "release/THIRD_PARTY_NOTICES.md").read_text()
        self.assertIn("Mbed TLS", notices)
        self.assertNotIn("Mbed TLS is not redistributed", notices)
        offer = (ROOT / "release/CORE-RUNTIME-SOURCE-OFFER.md").read_text()
        self.assertIn(MBEDTLS_SOURCE_URL, offer)
        self.assertIn(MBEDTLS_SOURCE_SHA256, offer)
        self.assertIn("Apache-2.0", offer)

    def test_build_records_source_provenance_in_the_run_metadata(self):
        build = BUILD.read_text()
        self.assertIn('install -m 0644 "$MBEDTLS_METADATA" "$RUN/mbedtls-source.json"', build)
        for entry in (
            "mbedtls_source_metadata=$RUN/mbedtls-source.json",
            "mbedtls_source_sha256=$mbedtls_archive_sha",
            "mbedtls_component_key=$mbedtls_cache_key",
        ):
            self.assertIn(entry, build)


class MbedtlsBuildInterpreterTests(unittest.TestCase):
    def test_reviewed_wheel_closure_pins_both_build_requirements(self):
        self.assertTrue(REQUIREMENTS.is_file(), REQUIREMENTS)
        text = REQUIREMENTS.read_text()
        for name, version in (("jinja2", "3.1.6"), ("jsonschema", "4.25.1")):
            self.assertIn(f"{name}=={version}", text)
        for name, version in POLICY.items():
            self.assertIn(f"{name}=={version}", text)
        self.assertEqual(text.count("--hash=sha256:"), len(POLICY))

    def test_vendored_wheels_match_the_inventory_digests(self):
        records = inventory_records()
        module = load_module("fetch_public_deps", FETCHER)
        module.load(INVENTORY)
        vendored = {
            name: record for name, record in records.items()
            if record["kind"] == "reviewed-vendored-input"
            and record["name"].startswith("mbedtls-build-")
        }
        self.assertEqual(len(vendored), len(POLICY) + 1)
        for name, record in vendored.items():
            self.assertEqual(record["redistribution"], "reviewed-vendored")
            relative = record["url"][len("vendored://"):]
            self.assertTrue(relative.startswith("reviewed/python-wheels/"), relative)
            source = ROOT / "build/inputs" / relative
            self.assertTrue(source.is_file(), source)
            self.assertFalse(source.is_symlink(), source)
            self.assertEqual(
                hashlib.sha256(source.read_bytes()).hexdigest(), record["sha256"]
            )
            self.assertEqual(module.NAMES[name], relative[len("reviewed/"):])
        requirements_record = vendored["mbedtls-build-requirements"]
        self.assertEqual(requirements_record["sha256"],
                         hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest())

    def test_replayed_reviewed_install_step_yields_the_pinned_interpreter(self):
        if sys.version_info[:2] != (3, 11):
            self.skipTest(
                "the reviewed wheel closure targets CPython 3.11, the "
                "interpreter the hosted build installs"
            )
        script = workflow_step_script(INTERPRETER_STEP)
        for guard in ("--no-index", "--require-hashes", "--find-links",
                      '--requirement "$wheelhouse/mbedtls-build-requirements.txt"'):
            self.assertIn(guard, script)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheelhouse = root / "public-deps/python-wheels"
            wheelhouse.mkdir(parents=True)
            for wheel in WHEELHOUSE.iterdir():
                if wheel.is_file():
                    (wheelhouse / wheel.name).write_bytes(wheel.read_bytes())
            environment = dict(
                os.environ,
                RUNNER_TEMP=str(root),
                PYTHONNOUSERSITE="1",
            )
            environment.pop("PYTHONPATH", None)
            # The workflow selects the pinned interpreter as `python`; replay the
            # step with the interpreter running this test so the step's CPython
            # pin and the reviewed cp311 wheels are exercised for real.
            shim = root / "interpreter-shim"
            shim.mkdir()
            for name in ("python", "python3"):
                (shim / name).symlink_to(sys.executable)
            environment["PATH"] = f"{shim}{os.pathsep}{environment['PATH']}"
            installed = subprocess.run(
                ["bash", "-euc", script], env=environment, cwd=ROOT,
                capture_output=True, text=True, timeout=600,
            )
            self.assertEqual(installed.returncode, 0,
                             installed.stdout + installed.stderr)
            interpreter = root / "mbedtls-build-venv/bin/python"
            self.assertTrue(interpreter.is_file(), interpreter)
            probe = subprocess.run(
                [str(interpreter), "-c",
                 "import importlib.metadata as m; import jinja2, jsonschema; "
                 "print(m.version('jinja2'), m.version('jsonschema'))"],
                env=environment, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(probe.returncode, 0, probe.stderr)
            self.assertEqual(probe.stdout.split(), ["3.1.6", "4.25.1"])
            # The same step without the reviewed closure must fail closed
            # rather than produce an interpreter that cannot satisfy the pins.
            for wheel in wheelhouse.iterdir():
                wheel.unlink()
            refused = subprocess.run(
                ["bash", "-euc", script], env=environment, cwd=ROOT,
                capture_output=True, text=True, timeout=600,
            )
            self.assertNotEqual(refused.returncode, 0)


class UiBundleCacheKeyTests(unittest.TestCase):
    """The UI bundle identity must change with the mbedTLS component."""

    def ui_bundle_key(self, work: Path, mbedtls_key: str | None) -> str:
        arguments = [
            "python3", str(CACHE_TOOL), "key", "--component", "ui-bundle",
            "--value", "payload_layout=bundle-relink-v1",
            "--value", "ui_head=" + "a" * 40,
            "--value", "ui_diff=" + "b" * 64,
            "--value", "ui_toolchain=" + "c" * 64,
            "--file", f"builder={work / 'builder.sh'}",
            "--file", f"ui-musl-gcc={work / 'cross-gcc'}",
            "--value", "cross_target=armhf",
            "--value", "musl_cc_target=armhf",
            "--value", "core-toolchain=" + "d" * 64,
            "--value", "service_profile=production",
        ]
        if mbedtls_key is not None:
            arguments += ["--value", f"ui_mbedtls={mbedtls_key}"]
        return subprocess.check_output(arguments, text=True).strip()

    def fixture(self, work: Path) -> None:
        for name in ("builder.sh", "cross-gcc"):
            path = work / name
            path.write_text("fixture\n")
            path.chmod(0o755)

    def test_build_binds_the_mbedtls_component_into_the_ui_bundle_key(self):
        build = BUILD.read_text()
        start = build.index(BUNDLE_KEY_START)
        end = build.index('--value "service_profile=$SERVICE_PROFILE")"', start)
        self.assertIn('--value "ui_mbedtls=$mbedtls_cache_key"', build[start:end])

    def test_bundle_key_changes_with_the_mbedtls_component(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            self.fixture(work)
            self.assertEqual(
                self.ui_bundle_key(work, "1" * 64),
                self.ui_bundle_key(work, "1" * 64),
            )
            for other in ("2" * 64, None):
                self.assertNotEqual(
                    self.ui_bundle_key(work, "1" * 64),
                    self.ui_bundle_key(work, other),
                )

    def test_cached_stub_bundle_cannot_satisfy_the_mbedtls_key(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            self.fixture(work)
            stub = work / "stub-bundle"
            (stub / "share/libreecho").mkdir(parents=True)
            (stub / "share/libreecho/ui-manifest.txt").write_text("stub tls\n")
            (stub / "relink").mkdir()
            (stub / "relink/tls.o").write_text("stub\n")
            cache = work / "cache"
            legacy = self.ui_bundle_key(work, None)
            current = self.ui_bundle_key(work, "3" * 64)
            self.assertNotEqual(legacy, current)
            subprocess.run(
                ["python3", str(CACHE_TOOL), "store", "--cache-root", str(cache),
                 "--component", "ui-bundle", "--key", legacy, "--source", str(stub)],
                check=True, capture_output=True, text=True, timeout=60,
            )
            miss = subprocess.run(
                ["python3", str(CACHE_TOOL), "restore", "--cache-root", str(cache),
                 "--component", "ui-bundle", "--key", current,
                 "--destination", str(work / "restored")],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(miss.returncode, 3, miss.stdout + miss.stderr)
            self.assertIn("MISS", miss.stdout)
            self.assertFalse((work / "restored").exists())
            subprocess.run(
                ["python3", str(CACHE_TOOL), "store", "--cache-root", str(cache),
                 "--component", "ui-bundle", "--key", current, "--source", str(stub)],
                check=True, capture_output=True, text=True, timeout=60,
            )
            hit = subprocess.run(
                ["python3", str(CACHE_TOOL), "restore", "--cache-root", str(cache),
                 "--component", "ui-bundle", "--key", current,
                 "--destination", str(work / "restored")],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(hit.returncode, 0, hit.stdout + hit.stderr)
            self.assertEqual(
                (work / "restored/share/libreecho/ui-manifest.txt").read_text(),
                "stub tls\n",
            )


BUILDER_TEMPLATE = """#!/usr/bin/env bash
set -euo pipefail
for argument in "$@"; do printf '%s\\n' "$argument" >>"$MBEDTLS_CALL_LOG"; done
archive=; output=
while (($#)); do
  case "$1" in
    --archive) archive=$2 ;;
    --output) output=$2 ;;
    --cc|--python|--jobs) ;;
    *) echo "ERROR: unexpected option: $1" >&2; exit 2 ;;
  esac
  shift 2
done
[[ -n "$archive" && -n "$output" ]] || {{ echo "ERROR: missing option" >&2; exit 2; }}
{failure}mkdir -p "$output/lib" "$output/include/mbedtls"
for archive_name in libmbedcrypto.a libmbedx509.a libmbedtls.a; do
  printf 'fixture object\\n' >"$output/lib/$archive_name"
done
printf 'fixture license\\n' >"$output/LICENSE"
printf '{{"name":"mbedtls","version":"{version}"}}\\n' >"$output/mbedtls-source.json"
printf 'mbedtls_version={version}\\n'
"""


class MbedtlsComponentBlockTests(unittest.TestCase):
    """Replay the shipped component block with the shipped cache helpers."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.source = BUILD.read_text()
        self.tools = self.work / "tools"
        (self.tools / "mbedtls").mkdir(parents=True)
        self.archive = self.work / MBEDTLS_ARCHIVE_NAME
        self.archive.write_bytes(b"pinned mbedtls source archive fixture\n")
        self.lock = self.tools / "mbedtls/SOURCE.lock"
        self.write_lock(self.archive)
        self.builder = self.tools / "mbedtls/build_mbedtls.sh"
        self.write_builder(self.builder)
        self.cross = self.work / "cross/arm-linux-gnueabihf-"
        (self.work / "cross").mkdir()
        for suffix in ("gcc", "ar"):
            tool = Path(f"{self.cross}{suffix}")
            tool.write_text("fixture\n")
            tool.chmod(0o755)
        self.interpreter = self.work / "mbedtls-build-venv/bin/python"
        self.interpreter.parent.mkdir(parents=True)
        self.interpreter.write_text("fixture\n")
        self.interpreter.chmod(0o755)
        self.core_key = "9" * 64

    def write_lock(self, archive: Path, *, version: str = MBEDTLS_VERSION) -> None:
        self.lock.write_text(json.dumps({
            "name": "mbedtls",
            "version": version,
            "license": "Apache-2.0",
            "source_url": MBEDTLS_SOURCE_URL,
            "source_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "target": "arm-linux-gnueabihf-static",
            "build_requirements": {"python3": ">=3.8", "jinja2": "3.1.6",
                                   "jsonschema": "4.25.1"},
        }, indent=2, sort_keys=True) + "\n")

    def write_builder(self, path: Path, *, failing: bool = False) -> None:
        path.write_text(BUILDER_TEMPLATE.format(
            version=MBEDTLS_VERSION,
            failure='echo "ERROR: mbedtls build failed" >&2\nexit 1\n' if failing else "",
        ))
        path.chmod(0o755)

    def ui_toolchain_key(self) -> str:
        """A stand-in UI toolchain identity for the fixture cross tools."""
        return subprocess.check_output(
            ["python3", str(CACHE_TOOL), "key", "--component", "ui-armhf-toolchain",
             "--value", "target=arm-linux-gnueabihf",
             "--file", f"gcc={self.cross}gcc", "--file", f"ar={self.cross}ar",
             "--value", f"core-toolchain={self.core_key}"],
            text=True,
        ).strip()

    def harness(self, run: Path, cache: Path, tools: Path | None = None) -> str:
        functions = "".join(
            extract_function(self.source, name)
            for name in (
                "component_cache_key", "record_component_identity",
                "component_cache_restore", "component_cache_store",
                "component_materialize",
            )
        )
        assignments = "".join(
            extract_assignment(self.source, name)
            for name in (
                "MBEDTLS_SOURCE_ARCHIVE", "MBEDTLS_BUILD_PYTHON",
                "MBEDTLS_BUILDER", "MBEDTLS_SOURCE_LOCK",
            )
        )
        preamble = textwrap.dedent(f"""\
            set -euo pipefail
            export LC_ALL=C
            CACHE_TOOL={shlex.quote(str(CACHE_TOOL))}
            COMPONENT_CACHE_ROOT={shlex.quote(str(cache))}
            REUSE_COMPONENT_CACHE=1
            COMPONENTS_MANIFEST={shlex.quote(str(run / 'components.json'))}
            COMPONENT_TIMING_FILE={shlex.quote(str(run / 'component-timing.log'))}
            COMPONENT_IDENTITY_FILE={shlex.quote(str(run / 'component-identities.log'))}
            JOBS=1
            RUN={shlex.quote(str(run))}
            TOOLS_DIR={shlex.quote(str(tools if tools is not None else self.tools))}
            UI_CROSS={shlex.quote(str(self.cross))}
            UI_TOOLCHAIN_KEY={shlex.quote(self.ui_toolchain_key())}
            CORE_TOOLCHAIN_KEY={self.core_key}
            mkdir -p "$RUN"
            declare -A COMPONENT_STARTED_MS=()
            """)
        return preamble + functions + assignments + extract_block(self.source)

    def run_block(self, run: Path, cache: Path, *, env: dict | None = None,
                  unset: tuple[str, ...] = (), tools: Path | None = None):
        environment = dict(
            os.environ,
            LIBREECHO_MBEDTLS_SOURCE_ARCHIVE=str(self.archive),
            LIBREECHO_MBEDTLS_BUILD_PYTHON=str(self.interpreter),
            MBEDTLS_CALL_LOG=str(self.work / "builder-calls.log"),
        )
        if env:
            environment.update(env)
        for variable in unset:
            environment.pop(variable, None)
        return subprocess.run(
            ["bash", "-euc", self.harness(run, cache, tools)],
            env=environment, capture_output=True, text=True, timeout=120,
        )

    def test_block_builds_materialises_and_records_the_component(self):
        run, cache = self.work / "run", self.work / "cache"
        result = self.run_block(run, cache)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.work / "builder-calls.log").read_text().split("\n")
        self.assertIn("--archive", calls)
        self.assertIn(str(self.archive), calls)
        self.assertIn(f"{self.cross}gcc", calls)
        self.assertIn(str(self.interpreter), calls)
        self.assertIn("--jobs", calls)
        # The run-local prefix is the materialised component, not a cache path.
        prefix = run / "components/mbedtls-arm32"
        for archive_name in ("libmbedcrypto.a", "libmbedx509.a", "libmbedtls.a"):
            self.assertTrue((prefix / "lib" / archive_name).is_file(), archive_name)
        self.assertTrue((prefix / "mbedtls-source.json").is_file())
        self.assertTrue((run / "mbedtls-source.json").is_file())
        key = subprocess.check_output(
            ["python3", str(CACHE_TOOL), "key", "--component", "mbedtls-arm32",
             "--tree", f"platform-mbedtls={self.tools / 'mbedtls'}",
             "--file", f"builder={self.builder}",
             "--file", f"source-lock={self.lock}",
             "--file", f"source-archive={self.archive}",
             "--file", f"cross-gcc={self.cross}gcc",
             "--file", f"cross-ar={self.cross}ar",
             "--value", f"version={MBEDTLS_VERSION}",
             "--value", f"source_sha256={json.loads(self.lock.read_text())['source_sha256']}",
             "--value", "target=arm-linux-gnueabihf-static",
             "--value", "ui-toolchain=" + self.ui_toolchain_key(),
             "--value", f"core-toolchain={self.core_key}"],
            text=True).strip()
        identities = (run / "component-identities.log").read_text()
        self.assertIn(f"identity=mbedtls-arm32 sha256={key}", identities)
        manifest = json.loads((run / "components.json").read_text())
        recorded = [item for item in manifest["components"]
                    if item["name"] == "mbedtls-arm32"]
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["status"], "rebuilt")
        self.assertEqual(recorded[0]["root"], str(prefix))
        self.assertIn(f"mbedtls_component_key={key}", result.stdout)
        self.assertIn("mbedtls_version=" + MBEDTLS_VERSION, result.stdout)

    def test_second_identical_run_restores_the_cached_component(self):
        cache = self.work / "cache"
        first = self.run_block(self.work / "run-one", cache)
        self.assertEqual(first.returncode, 0, first.stderr)
        reuse_log = self.work / "reuse-calls.log"
        second = self.run_block(
            self.work / "run-two", cache,
            env={"MBEDTLS_CALL_LOG": str(reuse_log)},
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("component_cache_hit=mbedtls-arm32", second.stdout)
        self.assertFalse(reuse_log.exists(), "cached component was rebuilt")
        self.assertTrue(
            (self.work / "run-two/components/mbedtls-arm32/lib/libmbedtls.a").is_file()
        )

    def test_component_key_changes_with_the_source_archive(self):
        first = self.run_block(self.work / "run-one", self.work / "cache-one")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.archive.write_bytes(b"a different mbedtls source archive fixture\n")
        self.write_lock(self.archive)
        second = self.run_block(self.work / "run-two", self.work / "cache-two")
        self.assertEqual(second.returncode, 0, second.stderr)
        key = "mbedtls_component_key="
        self.assertNotEqual(
            first.stdout[first.stdout.index(key):],
            second.stdout[second.stdout.index(key):],
        )

    def test_block_fails_closed_without_any_pinned_input(self):
        (self.work / "archive-link").symlink_to(self.archive)
        empty_tools = self.work / "empty-tools"
        (empty_tools / "mbedtls").mkdir(parents=True)
        absent = str(self.work / "absent")
        cases = {
            "interpreter_unset": (
                {}, ("LIBREECHO_MBEDTLS_BUILD_PYTHON",), None,
                "LIBREECHO_MBEDTLS_BUILD_PYTHON",
            ),
            "archive_unset": (
                {}, ("LIBREECHO_MBEDTLS_SOURCE_ARCHIVE",), None,
                "LIBREECHO_MBEDTLS_SOURCE_ARCHIVE",
            ),
            "interpreter_missing": (
                {"LIBREECHO_MBEDTLS_BUILD_PYTHON": f"{absent}-python"}, (), None,
                "pinned mbedTLS build interpreter is unavailable",
            ),
            "interpreter_not_executable": (
                {"LIBREECHO_MBEDTLS_BUILD_PYTHON": str(self.lock)}, (), None,
                "pinned mbedTLS build interpreter is unavailable",
            ),
            "archive_missing": (
                {"LIBREECHO_MBEDTLS_SOURCE_ARCHIVE": f"{absent}.tar.bz2"}, (), None,
                "missing pinned mbedTLS source archive",
            ),
            "archive_is_symlink": (
                {"LIBREECHO_MBEDTLS_SOURCE_ARCHIVE": str(self.work / "archive-link")},
                (), None, "missing pinned mbedTLS source archive",
            ),
            "lock_missing": (
                {}, (), empty_tools,
                "missing pinned mbedTLS source lock",
            ),
            "builder_missing": (
                {}, (), empty_tools,
                "mbedTLS builder is missing or not executable",
            ),
        }
        for name, (environment, unset, tools, expected) in cases.items():
            with self.subTest(case=name):
                run = self.work / f"run-{name}"
                result = self.run_block(
                    run, self.work / f"cache-{name}",
                    env=environment, unset=unset, tools=tools,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(expected, result.stderr)
                self.assertFalse((run / "components/mbedtls-arm32").exists())
                self.assertFalse((run / "mbedtls-source.json").exists())

    def test_block_fails_closed_on_a_lock_mismatch(self):
        self.lock.write_text(
            self.lock.read_text().replace(
                hashlib.sha256(self.archive.read_bytes()).hexdigest(), "0" * 64
            )
        )
        result = self.run_block(self.work / "run", self.work / "cache")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match the pinned source lock", result.stderr)

    def test_failed_build_is_never_cached_or_materialised(self):
        self.write_builder(self.builder, failing=True)
        run, cache = self.work / "run", self.work / "cache"
        result = self.run_block(run, cache)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mbedtls build failed", result.stderr)
        self.assertFalse((run / "components/mbedtls-arm32").exists())
        component_cache = cache / "mbedtls-arm32"
        entries = [] if not component_cache.exists() else [
            entry.name for entry in component_cache.iterdir()
            if len(entry.name) == 64
        ]
        self.assertEqual(entries, [])


class UiBuilderInvocationTests(unittest.TestCase):
    def test_ui_builder_receives_the_materialised_mbedtls_prefix(self):
        build = BUILD.read_text()
        start = build.index('if ! component_cache_restore ui-bundle')
        end = build.index('| tee "$RUN/ui-build.log"', start)
        invocation = build[start:end]
        self.assertIn('LIBREECHO_UI_MBEDTLS_ROOT="$MBEDTLS_OUTPUT"', invocation)
        self.assertIn('"$UI_BUILDER" "$UI_SOURCE"', invocation)
        self.assertIn(
            'MBEDTLS_OUTPUT="$RUN/components/mbedtls-arm32"',
            build[:build.index(BUNDLE_KEY_START)],
        )


if __name__ == "__main__":
    unittest.main()
