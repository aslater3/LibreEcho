"""Product target registry, canonical identity, and legacy-compatible naming."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = "radar_puffin"
KNOWN_TARGETS = (DEFAULT, "biscuit")
# Canonical per-target identity the schema allOf blocks pin: release slug,
# boot-chain products, hardware profile. Kept next to the registry because the
# descriptor loader validates itself and cannot read the schema twice.
IDENTITY = {
    "radar_puffin": ("radar-puffin", ["RADAR"], "radar_puffin@1"),
    "biscuit": ("biscuit", ["BISCUIT"], "biscuit@0"),
}


def _validate(value, schema, label="target"):
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{label}: invalid constant")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{label}: unsupported value")
    for item in schema.get("allOf", []):
        condition = item.get("if")
        if condition is not None:
            try:
                _validate(value, condition, label)
            except ValueError:
                continue
            _validate(value, item["then"], label)
        else:
            _validate(value, item, label)
    kind = schema.get("type")
    valid = {"object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "boolean": isinstance(value, bool)}
    if kind and not valid[kind]:
        raise ValueError(f"{label}: expected {kind}")
    if kind == "object":
        props = schema.get("properties", {})
        if set(schema.get("required", [])) - set(value):
            raise ValueError(f"{label}: missing required fields")
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise ValueError(f"{label}: unknown fields")
        for key, item in value.items():
            if key in props:
                _validate(item, props[key], label + "." + key)
    if kind == "array":
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{label}: empty array")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            raise ValueError(f"{label}: duplicate items")
        for item in value:
            _validate(item, schema["items"], label + "[]")
    if kind == "string" and "pattern" in schema and not re.fullmatch(schema["pattern"], value):
        raise ValueError(f"{label}: malformed string")


def validate_descriptor(value, target=None):
    schema = json.loads((ROOT / "release/target.schema.json").read_text())
    _validate(value, schema)
    identity = value["target_id"]
    if identity not in KNOWN_TARGETS or (target is not None and identity != target):
        raise ValueError("unknown or mismatched target")
    expected = IDENTITY[identity]
    if (value["release_slug"], value["fastboot_products"], value["platform"]["hw_profile"]) != expected:
        raise ValueError("target profile identity mismatch")
    if not isinstance(value["hardware_accepted"], bool):
        raise ValueError("hardware_accepted must be a boolean")
    return value


def load_target(target=DEFAULT):
    if target not in KNOWN_TARGETS:
        raise ValueError(f"unknown target: {target}")
    path = ROOT / "release/targets" / (target + ".json")
    if path.is_symlink() or not path.is_file():
        raise ValueError("target descriptor is unavailable or unsafe")
    return validate_descriptor(json.loads(path.read_text()), target)


def hardware_accepted(target=DEFAULT):
    """The target descriptor's hardware-acceptance decision; builds copy it."""
    return load_target(target)["hardware_accepted"] is True


def accepted_targets():
    """Targets whose descriptor allows a stable, publicly offered release."""
    return tuple(target for target in KNOWN_TARGETS if hardware_accepted(target))


def descriptor_sha256(target=DEFAULT):
    return hashlib.sha256(json.dumps(load_target(target), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def parse_targets(value):
    targets = value.split(",") if isinstance(value, str) else value
    if not isinstance(targets, list) or not targets or len(set(targets)) != len(targets):
        raise ValueError("targets must be a non-empty unique list")
    for target in targets:
        load_target(target)
    return targets


KERNEL_BUILD_FIELDS = ("arch", "kernel.defconfig", "kernel.dtb")


def kernel_build_identity(target=DEFAULT) -> tuple:
    """Kernel build inputs a target descriptor pins.

    The kernel source commit and toolchain come from the run, not the
    descriptor, so equal identities mean one kernel build serves every target.
    """
    descriptor = load_target(target)
    return (descriptor["arch"], descriptor["kernel"]["defconfig"], descriptor["kernel"]["dtb"])


def require_shared_kernel(targets) -> None:
    """Refuse a target set that needs more than the one kernel a run compiles.

    build/build.sh compiles one image and the first target's DTB, then reuses
    both for every target in the set, so a differing field means later targets
    would be packaged with the wrong kernel.
    """
    identities = [(target, kernel_build_identity(target)) for target in targets]
    anchor_target, anchor = identities[0]
    conflicts = []
    for target, identity in identities[1:]:
        differing = [f"{field} ({anchor_value} vs {value})"
                     for field, anchor_value, value in zip(KERNEL_BUILD_FIELDS, anchor, identity)
                     if anchor_value != value]
        if differing:
            conflicts.append(f"{target} and {anchor_target} differ in " + ", ".join(differing))
    if conflicts:
        raise ValueError(f"target set spans different kernel builds: {'; '.join(conflicts)}; "
                         "one build run compiles one kernel")


def asset_prefix(tag, target=DEFAULT):
    slug = load_target(target)["release_slug"]
    if not re.fullmatch(r"radar-puffin-(?:v\d+\.\d+\.\d+|(?:build|nightly)-[0-9a-f-]+)", tag):
        raise ValueError("invalid combined release tag")
    return "libreecho-" + (tag if target == DEFAULT else slug + "-" + tag.removeprefix("radar-puffin-"))


def target_from_product(product, override=None):
    if override is not None:
        load_target(override)
        return override
    matches = [t for t in KNOWN_TARGETS if product.upper() in load_target(t)["fastboot_products"]]
    if len(matches) != 1:
        raise ValueError("unknown boot-chain product; explicit --target required")
    return matches[0]


def contract_target(candidate, manifest):
    # Historical board-less Product candidates are Radar only. New candidates
    # must bind both the Platform manifest and the canonical descriptor digest.
    target = candidate.get("board", DEFAULT)
    load_target(target)
    board = manifest.get("board", manifest.get("device", DEFAULT))
    if board != target:
        raise ValueError("candidate and image manifest target mismatch")
    if "board" in candidate and candidate.get("target_descriptor_sha256") != descriptor_sha256(target):
        raise ValueError("candidate target descriptor digest mismatch")
    return target


def platform_target_args(tool, target, descriptor=None):
    load_target(target)
    result = subprocess.run([sys.executable, str(tool), "--help"], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ValueError(f"Platform capability probe failed: {tool.name}")
    help_text = result.stdout + result.stderr
    if re.search(r"(?<![\w-])--target(?:[ =\n]|$)", help_text) is None:
        if target != DEFAULT:
            raise ValueError(f"Platform {tool.name} lacks --target support; multi-target build requires the coordinated Platform PR")
        return []
    args = ["--target", target]
    if descriptor is not None:
        if "--target-descriptor-sha256" not in help_text:
            raise ValueError(f"Platform {tool.name} lacks --target-descriptor-sha256 support")
        args += ["--target-descriptor-sha256", descriptor]
    return args
