#!/usr/bin/env python3
"""Generate stable release notes from exact cross-repository source heads."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

REPOSITORIES = {
    "Product": "aslater3/LibreEcho",
    "Platform": "aslater3/LibreEcho-Platform",
    "Linux 6.1": "aslater3/LibreEcho-Linux-6.1",
    "UI": "aslater3/LibreEcho-UI",
}
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
TAG = re.compile(r"^radar-puffin-v([0-9]+\.[0-9]+\.[0-9]+)$")


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def gh_api(endpoint: str) -> object:
    result = subprocess.run(
        ["gh", "api", endpoint, "--paginate", "--slurp"],
        check=True,
        text=True,
        capture_output=True,
    )
    pages = json.loads(result.stdout)
    if not isinstance(pages, list):
        return pages
    if all(isinstance(page, list) for page in pages):
        flattened = []
        for page in pages:
            flattened.extend(page)
        return flattened
    if all(isinstance(page, dict) and "commits" in page for page in pages):
        merged = dict(pages[0]) if pages else {}
        commits = []
        for page in pages:
            commits.extend(page.get("commits", []))
        merged["commits"] = commits
        return merged
    return pages


def latest_product_tag(current: str) -> str | None:
    tags = gh_api(f"repos/{REPOSITORIES['Product']}/tags?per_page=100")
    versions = []
    for item in tags if isinstance(tags, list) else []:
        name = item.get("name", "")
        match = TAG.fullmatch(name)
        if match and match.group(1) != current:
            versions.append(match.group(1))
    return max(versions, key=lambda value: tuple(map(int, value.split("."))), default=None)


def ref_exists(repo: str, ref: str) -> bool:
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/git/ref/{ref}"],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def compare(repo: str, base: str | None, head: str) -> list[dict[str, str]]:
    if not base:
        commits = gh_api(f"repos/{repo}/commits?sha={head}&per_page=100")
    else:
        commits = gh_api(f"repos/{repo}/compare/{base}...{head}")
        commits = commits.get("commits", []) if isinstance(commits, dict) else []
    result = []
    for item in commits if isinstance(commits, list) else []:
        commit = item.get("commit", {})
        message = commit.get("message", "").splitlines()[0]
        result.append({"sha": item.get("sha", ""), "message": message})
    return result


def render(version: str, heads: dict[str, str], bases: dict[str, str | None], changes: dict[str, list[dict[str, str]]]) -> str:
    lines = [
        f"# LibreEcho radar-puffin v{version}",
        "",
        "This is the LibreEcho stable/product release for the Amazon Echo 2nd Gen "
        "(`radar_puffin`, ARMv7, Linux 6.1).",
        "",
        "## Release identity",
        "",
        "- Release channel: `stable`",
        "- Release classification: normal GitHub release",
        "- Product, Platform, Linux 6.1, and UI identities are bound to the exact "
        "stable build artifact.",
        "",
        "## Cross-repository included changes",
        "",
        "The following ledger is generated from the exact component heads used by "
        "the stable image. It is not inferred from branch names.",
        "",
    ]
    for name in REPOSITORIES:
        lines.extend([f"### {name}", "", f"- Selected head: `{heads[name]}`"])
        base = bases[name]
        lines.append(f"- Comparison base: `{base or 'no prior matching release ref'}`")
        items = changes[name]
        if not items:
            lines.extend(["- Changes: none in the selected comparison range", ""])
            continue
        lines.append("- Changes:")
        for item in items[:100]:
            lines.append(f"  - `{item['sha'][:12]}` {item['message']}")
        if len(items) > 100:
            lines.append(f"  - ... and {len(items) - 100} additional commits")
        lines.append("")

    lines.extend([
        "## Downloads and verification",
        "",
        "The release contains the signed OTA bundle, initial-install bundle, boot "
        "image, feature payloads and manifests, OTA public key, installer, and "
        "`SHA256SUMS`. Verify the published checksum inventory before use.",
        "",
        "## License and distribution boundary",
        "",
        "This release is `community-noncommercial`. The wakeword model is licensed "
        "under **CC-BY-NC-SA-4.0**: use is noncommercial, attribution is required, "
        "modifications must be indicated, and adaptations remain subject to "
        "**ShareAlike**.",
        "",
        "TTS voice assets include material under **CC-BY-SA-4.0** and retain their "
        "separate attribution and ShareAlike obligations.",
        "",
        "Review the bundled notices and the project release-closure records before "
        "redistribution. The release excludes credentials, signing keys, device "
        "identifiers, owner-local connectivity firmware, and vendor boot-chain "
        "material.",
        "",
        "## Validation boundary",
        "",
        "Stable publication proves that the Product workflow built, signed, and "
        "verified the release asset set. It does not by itself claim physical-device "
        "runtime acceptance; deployment, readback, runtime validation, and slot "
        "confirmation are separate evidence gates.",
        "",
    ])
    return "\n".join(lines)


def merge_authored_notes(authored: str, generated: str) -> str:
    marker = "## Release identity"
    if marker not in generated:
        fail("generated notes are missing the release identity ledger")
    return (
        authored.rstrip()
        + "\n\n---\n\n## Generated exact-source ledger\n\n"
        + generated.split(marker, 1)[1].lstrip()
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--product-head", required=True)
    parser.add_argument("--platform-head", required=True)
    parser.add_argument("--linux-head", required=True)
    parser.add_argument("--ui-head", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not VERSION.fullmatch(args.version):
        fail("version must be X.Y.Z")
    heads = {
        "Product": args.product_head,
        "Platform": args.platform_head,
        "Linux 6.1": args.linux_head,
        "UI": args.ui_head,
    }
    previous = latest_product_tag(args.version)
    bases: dict[str, str | None] = {}
    changes: dict[str, list[dict[str, str]]] = {}
    for name, repo in REPOSITORIES.items():
        if name == "Product":
            base = f"tags/radar-puffin-v{previous}" if previous else None
        else:
            release_ref = f"heads/release/{previous}" if previous else ""
            base = release_ref if release_ref and ref_exists(repo, release_ref) else "heads/main"
        bases[name] = base
        changes[name] = compare(repo, base, heads[name])
    if not args.output.is_file() or args.output.is_symlink():
        fail(f"missing or unsafe authored release notes: {args.output}")
    authored = args.output.read_text(encoding="utf-8")
    expected_title = f"# LibreEcho radar-puffin v{args.version}"
    if not authored.startswith(expected_title + "\n"):
        fail("authored release notes title does not match the requested version")
    generated = render(args.version, heads, bases, changes)
    args.output.write_text(merge_authored_notes(authored, generated), encoding="utf-8")
    print(f"release_notes={args.output}")
    print(f"previous_version={previous or 'none'}")
    print(f"repository_count={len(REPOSITORIES)}")
    print(f"change_count={sum(len(value) for value in changes.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
