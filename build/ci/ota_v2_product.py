"""Product-side checks for the Platform reboot-bound OTA v2 contract."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from importlib import util as importlib_util
from pathlib import Path
from typing import Any

from nacl.exceptions import BadSignatureError

FEATURES = ("airplay2", "tts", "wakeword", "stt", "assistant")
SERVICE_PROFILES = frozenset({"diagnostic", "production"})
FEATURE_POLICIES = frozenset({"exclude", "preserve", "redistributable", "community-noncommercial"})
CANONICAL_PARSER_RELATIVE = "tools/mt8163-arm32/ota/feature_manifest.py"
CANONICAL_PARSER_SCHEMA = "libreecho-ota-v2-feature-manifest-v1"
DAEMONS = {
    "airplay2": "usr/local/sbin/libreecho-audio-engine",
    "tts": "usr/local/sbin/libreecho-ttsd",
    "wakeword": "usr/local/sbin/libreecho-waked",
    "stt": "usr/local/sbin/libreecho-sttd",
    "assistant": "usr/local/sbin/libreecho-agentd",
}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
COMMIT40 = re.compile(r"[0-9a-f]{40}\Z")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
BASE_FIELDS = {
    "feature_id", "action", "activation", "base_payload_sha256", "base_manifest_sha256",
    "daemon_path", "daemon_sha256", "release", "source_commit",
}
ASSET_FIELDS = {"asset", "size", "sha256", "manifest_asset", "manifest_size", "manifest_sha256"}


class ContractError(ValueError):
    """A Product artifact is incompatible with Platform's OTA v2 contract."""


def fail(message: str) -> None:
    raise ContractError(message)


def digest(path: Path) -> tuple[str, int]:
    if path.is_symlink() or not path.is_file():
        fail(f"artifact is not a regular file: {path}")
    value = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
            size += len(block)
    return value.hexdigest(), size


def load_json(path: Path, label: str) -> Any:
    if path.is_symlink() or not path.is_file():
        fail(f"{label} is unavailable")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"{label} is malformed: {exc}")


def _hash(value: Any, label: str) -> None:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        fail(f"invalid {label}")


def _size(value: Any, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < (1 << 63):
        fail(f"invalid {label}")


def validate_profile_policy(service_profile: Any, feature_policy: Any) -> None:
    if service_profile not in SERVICE_PROFILES:
        fail("invalid service profile")
    if feature_policy not in FEATURE_POLICIES:
        fail("invalid feature policy")
    if feature_policy == "exclude" and service_profile != "diagnostic":
        fail("feature exclusion requires the diagnostic service profile")
    if feature_policy in {"redistributable", "community-noncommercial"} and service_profile != "production":
        fail(f"{feature_policy} feature policy requires the production service profile")


def expected_public_key_sha256(public_key: Path, expected: str | None) -> str:
    if not isinstance(expected, str) or HEX64.fullmatch(expected) is None:
        fail("trusted OTA public-key digest anchor is missing or malformed")
    actual, _ = digest(public_key)
    if actual != expected:
        fail("OTA public key does not match the trusted digest anchor")
    return actual


def validate_file_records(files: Any, label: str) -> dict[str, Any]:
    """Validate the file identities carried by a feature manifest."""
    if not isinstance(files, dict):
        fail(f"{label} lacks file records")
    for name, record in files.items():
        if (not isinstance(name, str) or not name or name.startswith("/")
                or "//" in name or "/../" in f"/{name}/"
                or not isinstance(record, dict)):
            fail(f"{label} contains an unsafe file record")
        _hash(record.get("sha256"), f"{label} file hash")
    return files


def _asset_name(value: Any, label: str, suffix: str, prefix: str) -> None:
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z", value)
            or "/" in value or "\\" in value or value.startswith(".")
            or not value.endswith(suffix) or value != prefix + suffix):
        fail(f"invalid {label} identity")


def validate_record(record: Any, release: str, source_commit: str) -> None:
    if not isinstance(record, dict):
        fail("feature record must be an object")
    if set(record) - (BASE_FIELDS | ASSET_FIELDS):
        fail("feature record contains unsupported fields")
    if set(record) != BASE_FIELDS and set(record) != BASE_FIELDS | ASSET_FIELDS:
        fail("feature record fields do not match action")
    feature = record.get("feature_id")
    action = record.get("action")
    if feature not in FEATURES:
        fail("invalid feature id")
    if action not in {"preserve", "runtime", "replace"}:
        fail(f"unsupported feature action: {action}")
    if record.get("activation") != "reboot":
        fail("v2 requires reboot activation")
    _hash(record.get("base_payload_sha256"), "base payload hash")
    _hash(record.get("base_manifest_sha256"), "base manifest hash")
    if record.get("daemon_path") != DAEMONS[feature]:
        fail("invalid daemon path")
    _hash(record.get("daemon_sha256"), "daemon hash")
    if record.get("release") != release or not VERSION.fullmatch(str(record.get("release", ""))):
        fail("feature release does not match the requested release")
    if record.get("source_commit") != source_commit or COMMIT40.fullmatch(str(record.get("source_commit", ""))) is None:
        fail("feature source commit does not match the candidate")
    if action == "preserve":
        if set(record) != BASE_FIELDS:
            fail("preserve feature contains asset fields")
        return
    suffix = ".runtime.squashfs" if action == "runtime" else ".payload.squashfs"
    manifest_suffix = ".runtime-manifest.json" if action == "runtime" else ".manifest.json"
    prefix = f"libreecho-radar-puffin-{release}-{feature}"
    _asset_name(record.get("asset"), "payload asset", suffix, prefix)
    _asset_name(record.get("manifest_asset"), "feature manifest asset", manifest_suffix, prefix)
    _size(record.get("size"), "payload size")
    _size(record.get("manifest_size"), "manifest size")
    _hash(record.get("sha256"), "payload hash")
    _hash(record.get("manifest_sha256"), "manifest hash")


def validate_plan(plan: Any, release: str, source_commit: str) -> list[dict[str, Any]]:
    if (not isinstance(plan, dict)
            or set(plan) != {"schema", "transaction_type", "activation", "release", "source_commit", "features"}
            or plan.get("schema") != "libreecho-product-feature-plan-v1"
            or plan.get("transaction_type") != "system"
            or plan.get("activation") != "reboot"):
        fail("OTA v2 plan must be a reboot-bound system transaction")
    if plan.get("release") != release or plan.get("source_commit") != source_commit:
        fail("OTA v2 plan identity does not match the candidate")
    records = plan.get("features")
    if not isinstance(records, list) or [r.get("feature_id") for r in records if isinstance(r, dict)] != list(FEATURES):
        fail("feature-only or incomplete OTA v2 plan is not supported")
    for record in records:
        validate_record(record, release, source_commit)
    return records


def validate_inventory(inventory: Any, records: list[dict[str, Any]], release: str) -> list[dict[str, Any]]:
    if (not isinstance(inventory, dict)
            or set(inventory) != {"schema", "transaction_type", "activation", "release", "source_commit", "assets"}
            or inventory.get("schema") != "libreecho-product-feature-assets-v1"
            or inventory.get("transaction_type") != "system"
            or inventory.get("activation") != "reboot"):
        fail("asset inventory is not a reboot-bound system inventory")
    if (not records or inventory.get("release") != release
            or inventory.get("source_commit") != records[0].get("source_commit")):
        fail("asset inventory identity mismatch")
    expected: list[tuple[str, str, str, str, int, str]] = []
    for record in records:
        if record["action"] != "preserve":
            expected.extend([
                (record["feature_id"], record["action"], "payload", record["asset"], record["size"], record["sha256"]),
                (record["feature_id"], record["action"], "manifest", record["manifest_asset"], record["manifest_size"], record["manifest_sha256"]),
            ])
    expected.sort(key=lambda item: item[3])
    assets = inventory.get("assets")
    if not isinstance(assets, list) or len(assets) != len(expected):
        fail("asset inventory is incomplete")
    seen: set[str] = set()
    for item, (feature, action, kind, expected_name, expected_size, expected_hash) in zip(assets, expected):
        if not isinstance(item, dict) or set(item) != {"feature_id", "action", "kind", "name", "size", "sha256"}:
            fail("asset inventory record is malformed")
        if (item["feature_id"], item["action"], item["kind"], item["name"], item["size"], item["sha256"]) != (
                feature, action, kind, expected_name, expected_size, expected_hash):
            fail("asset inventory does not match the signed feature plan")
        if item["name"] in seen:
            fail("asset inventory contains duplicate asset")
        seen.add(item["name"])
        _size(item["size"], "inventory size")
        _hash(item["sha256"], "inventory hash")
    return assets


def load_feature_contract(run: Path, candidate: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any], Path, list[dict[str, Any]]]:
    """Load and verify the immutable v2 plan, inventory, and staged assets."""
    if candidate.get("ota_format") != "v2":
        fail("candidate is not an OTA v2 build")
    release = candidate.get("ota_release", "")
    source_commit = candidate.get("ui_commit", "")
    if not isinstance(release, str) or not VERSION.fullmatch(release):
        fail("candidate has an invalid OTA v2 release")

    def artifact_path(field: str, basename: str) -> Path:
        value = candidate.get(field, "")
        if not isinstance(value, str) or Path(value).name != basename:
            fail(f"candidate has an invalid {field}")
        path = run / basename
        if path.is_symlink() or not path.is_file():
            fail(f"candidate is missing {basename}")
        return path

    plan_path = artifact_path("feature_plan", "feature-plan.json")
    inventory_path = artifact_path("feature_asset_inventory", "feature-assets.json")
    asset_dir_value = candidate.get("feature_asset_dir", "")
    if not isinstance(asset_dir_value, str) or Path(asset_dir_value).name != "ota-assets":
        fail("candidate has an invalid feature_asset_dir")
    asset_dir = run / "ota-assets"
    if asset_dir.is_symlink() or (asset_dir.exists() and not asset_dir.is_dir()):
        fail("candidate has an unsafe ota-assets path")

    plan = load_json(plan_path, "feature plan")
    records = validate_plan(plan, release, source_commit)
    inventory = load_json(inventory_path, "feature asset inventory")
    assets = validate_inventory(inventory, records, release)
    expected_names = {item["name"] for item in assets}
    # Artifact transport omits empty directories. Only an independently
    # validated empty plan/inventory may omit this directory.
    if not asset_dir.exists() and expected_names:
        fail("candidate is missing ota-assets")
    entries = list(asset_dir.iterdir()) if asset_dir.exists() else []
    if any(entry.is_symlink() or not entry.is_file() for entry in entries):
        fail("feature asset directory contains an unsafe entry")
    if {entry.name for entry in entries} != expected_names:
        fail("feature asset directory does not match the signed inventory")
    for item in assets:
        path = asset_dir / item["name"]
        actual_hash, actual_size = digest(path)
        if (actual_hash, actual_size) != (item["sha256"], item["size"]):
            fail(f"feature asset identity mismatch: {item['name']}")
    return plan, inventory, asset_dir, assets


def _canonical_parser() -> Any | None:
    """Load Platform's canonical parser when the pinned source is available.

    Product release preparation also runs from a Product-only checkout, so the
    complete adapter below remains the portable verifier.  When the coordinated
    Platform source is present, run its parser first and then apply Product's
    exact-five/binding adapter; this prevents the two sides from silently
    drifting while keeping the v1 bridge independent of Platform.
    """
    roots = []
    configured = __import__("os").environ.get("LIBREECHO_PLATFORM_SOURCE", "")
    if configured:
        roots.append(Path(configured))
    roots.append(Path(__file__).resolve().parents[3] / "platform")
    for root in roots:
        parser_path = root / CANONICAL_PARSER_RELATIVE
        if parser_path.is_symlink() or not parser_path.is_file():
            continue
        spec = importlib_util.spec_from_file_location("libreecho_canonical_feature_manifest", parser_path)
        if spec is None or spec.loader is None:
            continue
        module = importlib_util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except (OSError, ImportError, ValueError):
            continue
        if getattr(module, "parse_manifest", None) is not None:
            return module
    return None


def _parse_complete_v2_manifest(raw: bytes) -> dict[str, Any]:
    """Parse the signed wire format and enforce the complete v2 contract."""
    parser = _canonical_parser()
    if parser is not None:
        try:
            parser.parse_manifest(raw)
        except Exception as exc:  # canonical parser errors are contract errors
            fail(f"canonical v2 parser rejected manifest: {exc}")
    if not isinstance(raw, bytes) or len(raw) > 64 * 1024 or not raw.endswith(b"\n"):
        fail("OTA v2 manifest is malformed")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        fail(f"OTA v2 manifest is not ASCII: {exc}")
    values: dict[str, str] = {}
    for line in text[:-1].split("\n"):
        if not line or line.count("=") != 1:
            fail("OTA v2 manifest has a malformed line")
        key, value = line.split("=", 1)
        if (re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is None
                or not value or key in values):
            fail("OTA v2 manifest has duplicate or unknown fields")
        values[key] = value

    top = {
        "format", "manifest_version", "board", "soc", "architecture", "image_profile",
        "transaction_type", "transaction_id", "version", "update_channel", "service_profile",
        "feature_policy", "minimum_updater_schema", "feature_asset_base", "commit_policy",
        "feature_ids", "boot_filename", "boot_size", "boot_sha256",
    }
    if values.get("feature_ids") != ",".join(FEATURES):
        fail("OTA v2 manifest must contain all five features in canonical order")
    expected_keys = set(top)
    for feature in FEATURES:
        prefix = f"feature_{feature}_"
        expected_keys.update(prefix + field for field in (BASE_FIELDS - {"feature_id"}) | ASSET_FIELDS)
        # Asset fields are required only for runtime/replace; the action is
        # parsed first and the exact record shape is checked below.
    if not top.issubset(values):
        fail("OTA v2 manifest schema is incomplete")
    unknown = set(values) - expected_keys
    if unknown:
        fail(f"OTA v2 manifest contains unsupported fields: {sorted(unknown)}")

    int_keys = {"manifest_version", "minimum_updater_schema", "boot_size", "size", "manifest_size"}
    typed: dict[str, Any] = dict(values)
    for key in int_keys:
        if key in typed:
            try:
                typed[key] = int(typed[key])
            except ValueError as exc:
                fail(f"OTA v2 manifest has invalid {key}: {exc}")
    records: list[dict[str, Any]] = []
    for feature in FEATURES:
        prefix = f"feature_{feature}_"
        record: dict[str, Any] = {"feature_id": feature}
        for key, value in typed.items():
            if key.startswith(prefix):
                field = key.removeprefix(prefix)
                if field in {"size", "manifest_size"}:
                    try:
                        value = int(value)
                    except ValueError as exc:
                        fail(f"OTA v2 manifest has invalid {field}: {exc}")
                record[field] = value
        if set(record) - BASE_FIELDS - ASSET_FIELDS:
            fail(f"OTA v2 feature record contains unsupported fields: {feature}")
        validate_record(record, str(typed["version"]), str(record.get("source_commit", "")))
        records.append(record)
    result = {key: typed[key] for key in top}
    result["features"] = records
    if (result["format"] != "libreecho-ota-v2" or result["manifest_version"] != 1
            or result["minimum_updater_schema"] != 2 or result["board"] != "radar_puffin"
            or result["soc"] != "mt8163" or result["architecture"] != "armv7"
            or result["image_profile"] != "ota" or result["transaction_type"] != "system"
            or result["update_channel"] not in {"dev", "stable"}
            or result["feature_asset_base"] != "github-release-channel"
            or result["commit_policy"] != "after-slot-confirm"
            or result["boot_filename"] != "boot.img" or result["boot_size"] != 16 * 1024 * 1024):
        fail("OTA v2 manifest identity or policy is invalid")
    validate_profile_policy(result["service_profile"], result["feature_policy"])
    _hash(result["boot_sha256"], "boot hash")
    if not isinstance(result["transaction_id"], str) or re.fullmatch(r"txn-[0-9a-f]{24}", result["transaction_id"]) is None:
        fail("OTA v2 transaction id is malformed")

    # Recreate the canonical line order.  This catches reordered fields and
    # prevents a signed alternate serialization from becoming a second format.
    ordered: list[tuple[str, Any]] = [(key, result[key]) for key in (
        "format", "manifest_version", "board", "soc", "architecture", "image_profile",
        "transaction_type", "transaction_id", "version", "update_channel", "service_profile",
        "feature_policy", "minimum_updater_schema", "feature_asset_base", "commit_policy",
    )]
    ordered.extend((key, result[key]) for key in ("boot_filename", "boot_size", "boot_sha256"))
    ordered.append(("feature_ids", ",".join(FEATURES)))
    for record in records:
        prefix = f"feature_{record['feature_id']}_"
        for field in ("action", "activation", "base_payload_sha256", "base_manifest_sha256", "daemon_path", "daemon_sha256", "release", "source_commit"):
            ordered.append((prefix + field, record[field]))
        if record["action"] != "preserve":
            for field in ("asset", "size", "sha256", "manifest_asset", "manifest_size", "manifest_sha256"):
                ordered.append((prefix + field, record[field]))
    canonical = "".join(f"{key}={value}\n" for key, value in ordered).encode("ascii")
    if canonical != raw:
        fail("OTA v2 manifest is not canonical")
    return result


def _bind_control_to_feature_contract(
    manifest: dict[str, Any],
    plan: dict[str, Any],
    inventory: dict[str, Any],
    asset_dir: Path,
    expected_channel: str,
    boot_path: Path | None = None,
) -> None:
    records = validate_plan(plan, str(manifest["version"]), str(plan.get("source_commit", "")))
    assets = validate_inventory(inventory, records, str(manifest["version"]))
    transaction_material = (
        str(manifest["version"])
        + str(manifest["boot_sha256"])
        + json.dumps(records, sort_keys=True, separators=(",", ":"))
    ).encode("utf-8")
    expected_transaction = "txn-" + hashlib.sha256(transaction_material).hexdigest()[:24]
    if (manifest["transaction_type"] != plan["transaction_type"]
            or manifest["version"] != plan["release"]
            or manifest["update_channel"] != expected_channel
            or inventory["transaction_type"] != manifest["transaction_type"]
            or manifest["transaction_id"] != expected_transaction):
        fail("signed control transaction, version, or channel does not match the feature contract")
    if manifest["features"] != records:
        fail("signed control feature records do not match feature-plan.json")
    expected_assets = []
    for record in records:
        if record["action"] != "preserve":
            expected_assets.extend([
                {"feature_id": record["feature_id"], "action": record["action"], "kind": "payload", "name": record["asset"], "size": record["size"], "sha256": record["sha256"]},
                {"feature_id": record["feature_id"], "action": record["action"], "kind": "manifest", "name": record["manifest_asset"], "size": record["manifest_size"], "sha256": record["manifest_sha256"]},
            ])
    expected_assets.sort(key=lambda item: item["name"])
    if assets != expected_assets:
        fail("signed control feature records do not match feature-assets.json")
    if (asset_dir.is_symlink()
            or (asset_dir.exists() and not asset_dir.is_dir())
            or (assets and not asset_dir.is_dir())):
        fail("feature asset directory is unavailable")
    for item in assets:
        actual_hash, actual_size = digest(asset_dir / item["name"])
        if (actual_hash, actual_size) != (item["sha256"], item["size"]):
            fail(f"signed control asset identity mismatch: {item['name']}")
    if boot_path is not None:
        boot_hash, boot_size = digest(boot_path)
        if (boot_size, boot_hash) != (manifest["boot_size"], manifest["boot_sha256"]):
            fail("signed control boot identity does not match the published boot image")


def validate_control_tar(
    path: Path,
    public_key: Path,
    expected_format: str,
    release: str,
    *,
    feature_plan: dict[str, Any] | None = None,
    feature_inventory: dict[str, Any] | None = None,
    feature_asset_dir: Path | None = None,
    expected_channel: str | None = None,
    boot_path: Path | None = None,
    expected_key_sha256: str | None = None,
) -> None:
    raw = b""
    if path.is_symlink() or not path.is_file():
        fail("OTA control bundle is unavailable")
    try:
        with tarfile.open(path, "r:") as archive:
            members = archive.getmembers()
            if [member.name for member in members] != ["manifest", "manifest.sig", "boot.img"]:
                fail("OTA control bundle has an incompatible member set")
            if any(not member.isfile() or member.issym() or member.islnk() for member in members):
                fail("OTA control bundle contains a non-regular member")
            raw = archive.extractfile("manifest").read()  # type: ignore[union-attr]
            signature = archive.extractfile("manifest.sig").read()  # type: ignore[union-attr]
            boot = archive.extractfile("boot.img").read()  # type: ignore[union-attr]
    except (OSError, tarfile.TarError, AttributeError) as exc:
        fail(f"OTA control bundle is malformed: {exc}")
    if expected_format not in {"v1", "v2"}:
        fail("unsupported OTA format")
    if not boot.startswith(b"ANDROID!") or len(boot) != 16 * 1024 * 1024:
        fail("OTA bundle boot image is not an exact 16 MiB Android image")
    if not signature.endswith(b"\n") or not re.fullmatch(rb"[0-9a-f]{128}\n", signature):
        fail("OTA signature is malformed")
    try:
        lines = raw.decode("ascii").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        fail(f"OTA manifest is not ASCII: {exc}")
    if not lines or any(not line.endswith("\n") or line.count("=") != 1 for line in lines):
        fail("OTA manifest is malformed")
    fields: dict[str, str] = {}
    for line in lines:
        key, value = line[:-1].split("=", 1)
        if key in fields or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) or not value:
            fail("OTA manifest has duplicate or malformed fields")
        fields[key] = value
    required = {
        "format", "manifest_version", "board", "soc", "architecture",
        "version", "boot_filename", "boot_size", "boot_sha256",
        "image_profile", "service_profile", "update_channel",
    }
    if not required.issubset(fields):
        fail("OTA manifest schema is incomplete")
    if fields["format"] != f"libreecho-ota-{expected_format}":
        fail("OTA bundle format does not match the requested bridge")
    if fields["manifest_version"] not in {"1", "2"}:
        fail("OTA manifest version is unsupported")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+~-]{0,95}", fields["version"]) is None:
        fail("OTA manifest version is malformed")
    if fields["board"] != "radar_puffin" or fields["soc"] != "mt8163" or fields["architecture"] != "armv7":
        fail("OTA manifest target identity mismatch")
    if fields["boot_filename"] != "boot.img" or fields["boot_size"] != str(len(boot)):
        fail("OTA manifest boot identity mismatch")
    if fields["boot_sha256"] != hashlib.sha256(boot).hexdigest():
        fail("OTA manifest boot hash mismatch")
    if fields["image_profile"] != "ota" or fields["update_channel"] not in {"dev", "stable"}:
        fail("OTA manifest profile or channel is invalid")
    validate_profile_policy(fields["service_profile"], fields.get("feature_policy", "preserve"))
    if release and fields["version"] != release:
        fail("OTA release identity mismatch")
    if expected_format == "v2":
        if feature_plan is None or feature_inventory is None or feature_asset_dir is None or expected_channel is None:
            fail("OTA v2 validation requires feature-plan.json, feature-assets.json, and staged assets")
    if expected_format == "v2":
        expected_public_key_sha256(public_key, expected_key_sha256)
    elif expected_key_sha256 is not None:
        expected_public_key_sha256(public_key, expected_key_sha256)
    try:
        from nacl.signing import VerifyKey
        if public_key.is_symlink() or not public_key.is_file():
            fail("trusted OTA public key is unavailable")
        key_text = public_key.read_text(encoding="ascii").strip()
        if re.fullmatch(r"[0-9a-f]{64}", key_text) is None:
            fail("trusted OTA public key is malformed")
        VerifyKey(bytes.fromhex(key_text)).verify(raw, bytes.fromhex(signature[:-1].decode("ascii")))
    except (OSError, UnicodeDecodeError, ValueError, TypeError, BadSignatureError) as exc:
        fail(f"OTA Ed25519 signature verification failed: {exc}")
    if expected_format == "v2":
        assert feature_plan is not None and feature_inventory is not None
        assert feature_asset_dir is not None and expected_channel is not None
        manifest = _parse_complete_v2_manifest(raw)
        if manifest["version"] != release:
            fail("OTA release identity mismatch")
        _bind_control_to_feature_contract(
            manifest, feature_plan, feature_inventory, feature_asset_dir,
            expected_channel, boot_path,
        )


def verify_runtime_capsule(
    verifier: Path,
    feature: str,
    base: dict[str, Any],
    runtime_dir: Path,
    release: str,
    source_commit: str,
) -> None:
    """Run Platform's pinned semantic capsule verifier on the real pair."""
    if verifier.is_symlink() or not verifier.is_file():
        fail("Platform runtime verifier is unavailable or unsafe")
    manifest_path = runtime_dir / f"{feature}.runtime-manifest.json"
    payload_path = runtime_dir / f"{feature}.runtime.squashfs"
    manifest = load_json(manifest_path, f"{feature} runtime capsule manifest")
    if not isinstance(manifest, dict):
        fail(f"{feature} runtime capsule manifest is malformed")
    dependencies = manifest.get("service_dependencies")
    compatibility = manifest.get("compatibility")
    component = manifest.get("component")
    component_version = manifest.get("component_version")
    build_identity = manifest.get("build_identity")
    max_bytes = manifest.get("max_bytes")
    if (not isinstance(dependencies, list) or not dependencies
            or not isinstance(compatibility, dict) or not isinstance(component, str)
            or not isinstance(component_version, str) or not isinstance(build_identity, str)
            or not isinstance(max_bytes, int) or isinstance(max_bytes, bool)):
        fail(f"{feature} runtime capsule lacks Platform verifier inputs")
    command = [
        sys.executable, str(verifier), "--feature-id", feature,
        "--base-payload", str(base["payload"]["path"]),
        "--base-manifest", str(base["manifest"]["path"]),
        "--product-release", release, "--source-commit", source_commit,
        "--component", component, "--component-version", component_version,
        "--build-identity", build_identity,
    ]
    for dependency in dependencies:
        command.extend(("--service-dependency", str(dependency)))
    command.extend((
        "--compatibility", json.dumps(compatibility, sort_keys=True, separators=(",", ":")),
        "--max-bytes", str(max_bytes), "--capsule", str(payload_path),
        "--manifest", str(manifest_path),
    ))
    try:
        result = subprocess.run(command, text=True, capture_output=True, check=False, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail(f"Platform runtime verifier could not run for {feature}: {exc}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        fail(f"Platform runtime verifier rejected {feature}: {detail[-1] if detail else 'unknown error'}")


def validate_v2_publisher_asset_set(output: Path, release: str, feature_assets: list[dict[str, Any]]) -> None:
    """Ensure the prepared publisher directory has exactly its v2 capsule set."""
    expected = {str(item["name"]) for item in feature_assets}
    prefix = f"libreecho-radar-puffin-{release}-"
    members = [path for path in output.iterdir() if path.name.startswith(prefix)]
    if any(path.is_symlink() or not path.is_file() for path in members):
        fail("v2 publisher asset set contains a non-regular member")
    actual = {path.name for path in members}
    if actual != expected:
        fail(f"v2 publisher asset set is incomplete or contains an unexpected capsule: expected={sorted(expected)} actual={sorted(actual)}")


def _validate_sha256_membership(output: Path, sums: Path) -> set[str]:
    if sums.is_symlink() or not sums.is_file():
        fail("stable release is missing SHA256SUMS")
    records: dict[str, str] = {}
    for line in sums.read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9][A-Za-z0-9._+-]{0,159})", line)
        if match is None or match.group(2) in records:
            fail("SHA256SUMS contains a malformed or duplicate record")
        records[match.group(2)] = match.group(1)
    actual = {
        path.name for path in output.iterdir()
        if path.is_file() and not path.is_symlink() and path.name != sums.name
    }
    if set(records) != actual:
        fail(f"SHA256SUMS membership mismatch: listed={sorted(records)} actual={sorted(actual)}")
    for name, expected in records.items():
        actual_hash, _ = digest(output / name)
        if actual_hash != expected:
            fail(f"SHA256SUMS hash mismatch: {name}")
    return actual


def validate_stable_publisher(output: Path, release_tag: str, expected_key_sha256: str | None = None) -> set[str]:
    """Run every stable gate without making a GitHub API call."""
    if output.is_symlink() or not output.is_dir():
        fail("stable release directory is unavailable")
    match = re.fullmatch(r"radar-puffin-v([0-9]+\.[0-9]+\.[0-9]+)", release_tag)
    if match is None:
        fail("stable release tag is malformed")
    version = match.group(1)
    prefix = f"libreecho-{release_tag}"
    sums = output / f"{prefix}-SHA256SUMS"
    actual = _validate_sha256_membership(output, sums)
    build_path = output / f"{prefix}-build.json"
    build = load_json(build_path, "stable build manifest")
    if (build.get("schema") != "libreecho-stable-release-v1"
            or build.get("release") != release_tag
            or build.get("channel") != "stable"
            or build.get("status") != "PREPARED_NOT_FLASHED"
            or build.get("signed") is not True):
        fail("stable build manifest identity or status is invalid")
    standard = {
        f"{prefix}.ota.tar", f"{prefix}-boot.img", f"{prefix}-ota-public-key.hex",
        f"{prefix}-release-notes.md", f"{prefix}-installer.py", f"{prefix}-run-one-shot.sh",
        f"{prefix}-initial-install.tar", f"{prefix}-build.json",
        f"{prefix}-SHA256SUMS",
    }
    standard.update(
        f"{prefix}-{feature}.{suffix}"
        for feature in FEATURES
        for suffix in ("squashfs", "manifest.json")
    )
    ota_format = build.get("ota_format", "v1")
    if ota_format == "v2":
        plan_path = output / f"{prefix}-feature-plan.json"
        inventory_path = output / f"{prefix}-feature-assets.json"
        standard.update({plan_path.name, inventory_path.name})
        plan = load_json(plan_path, "published feature-plan.json")
        inventory = load_json(inventory_path, "published feature-assets.json")
        if build.get("feature_plan") != plan or build.get("feature_assets") != inventory.get("assets"):
            fail("published feature contract differs from the stable build manifest")
        feature_assets = inventory.get("assets")
        if not isinstance(feature_assets, list):
            fail("published feature-assets.json is malformed")
        standard.update(str(item.get("name")) for item in feature_assets if isinstance(item, dict))
    elif ota_format != "v1":
        fail("stable build manifest has an unsupported OTA format")
    if actual | {sums.name} != standard:
        fail(f"stable release has an incomplete or extra asset set: expected={sorted(standard)} actual={sorted(actual | {sums.name})}")
    artifacts = build.get("artifacts")
    if not isinstance(artifacts, list) or {item.get("name") for item in artifacts if isinstance(item, dict)} != actual - {sums.name, build_path.name}:
        fail("stable build manifest artifact inventory is incomplete")

    public_key = output / f"{prefix}-ota-public-key.hex"
    ota = output / f"{prefix}.ota.tar"
    if ota_format == "v2":
        validate_control_tar(
            ota, public_key, "v2", version,
            feature_plan=plan, feature_inventory=inventory,
            feature_asset_dir=output, expected_channel="stable",
            boot_path=output / f"{prefix}-boot.img",
            expected_key_sha256=expected_key_sha256 or os.environ.get("LIBREECHO_OTA_EXPECTED_PUBLIC_KEY_SHA256"),
        )
    else:
        validate_control_tar(ota, public_key, "v1", "")
    return actual


def stage_inventory_assets(inventory: Any, source_root: Path, output: Path) -> None:
    assets = inventory.get("assets") if isinstance(inventory, dict) else None
    if not isinstance(assets, list):
        fail("asset inventory is malformed")
    if output.exists():
        fail("OTA asset staging directory already exists")
    output.mkdir(parents=True)
    for item in assets:
        source = source_root / item["name"]
        target = output / item["name"]
        actual_hash, actual_size = digest(source)
        if (actual_hash, actual_size) != (item["sha256"], item["size"]):
            fail(f"staged asset identity mismatch: {item['name']}")
        shutil.copyfile(source, target)
