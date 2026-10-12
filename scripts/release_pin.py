"""Validate release pins or propose a verified published release update."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from deploy import validate_wheel

ROOT = Path(__file__).resolve().parents[1]


def load_pin(path=ROOT / "release.json"):
    pin = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_pin(pin)


def validate_pin(pin):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", pin.get("repository", "")):
        raise ValueError("repository must be OWNER/REPOSITORY")
    if pin["repository"].startswith("YOUR_OWNER/"):
        raise ValueError("Replace YOUR_OWNER in release.json")
    match = re.fullmatch(r"v([0-9]+(?:\.[0-9]+){2})", pin.get("tag", ""))
    if not match or pin.get("asset") != f"demo_date-{match[1]}-py3-none-any.whl":
        raise ValueError("tag and universal demo_date wheel asset must have matching X.Y.Z versions")
    if not re.fullmatch(r"[0-9a-f]{64}", pin.get("sha256", "")):
        raise ValueError("Pin the actual published wheel SHA-256 in release.json")
    return pin


def update_pin(tag, path=ROOT / "release.json"):
    path = Path(path)
    current = json.loads(path.read_text(encoding="utf-8"))
    match = re.fullmatch(r"v([0-9]+(?:\.[0-9]+){2})", tag)
    if not match:
        raise ValueError("Release tag must be vX.Y.Z")
    asset = f"demo_date-{match[1]}-py3-none-any.whl"
    proposed = validate_pin({"repository": current["repository"], "tag": tag,
                             "asset": asset, "sha256": "0" * 64})
    current_version = re.fullmatch(r"v([0-9]+(?:\.[0-9]+){2})", current.get("tag", ""))
    if current_version and tuple(map(int, match[1].split("."))) < tuple(map(int, current_version[1].split("."))):
        raise ValueError("Automatic pin updates cannot downgrade the release")
    repository = proposed["repository"]
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repository,
         "--json", "tagName,isDraft,isPrerelease,assets"],
        check=True, capture_output=True, text=True,
    )
    release = json.loads(result.stdout)
    if release["tagName"] != tag or release["isDraft"] or release["isPrerelease"]:
        raise ValueError("Only the requested stable, published release is accepted")
    names = [entry["name"] for entry in release["assets"]]
    if names.count(asset) != 1 or names.count("SHA256SUMS") != 1:
        raise ValueError("Release must contain exactly one wheel and SHA256SUMS asset")
    with tempfile.TemporaryDirectory(prefix="release-pin-") as directory:
        subprocess.run(
            ["gh", "release", "download", tag, "--repo", repository,
             "--pattern", asset, "--pattern", "SHA256SUMS", "--dir", directory],
            check=True,
        )
        checksums = (Path(directory) / "SHA256SUMS").read_text(encoding="utf-8")
        matches = re.findall(r"^([0-9a-fA-F]{64}) [ *]" + re.escape(asset) + r"$",
                             checksums, flags=re.MULTILINE)
        if len(matches) != 1:
            raise ValueError("SHA256SUMS must contain exactly one checksum for the wheel")
        proposed["sha256"] = validate_wheel(Path(directory) / asset, matches[0])["sha256"]
    if current.get("tag") == tag and current.get("sha256") and current != proposed:
        raise ValueError("An already pinned release cannot be replaced")
    if current != proposed:
        path.write_text(json.dumps(proposed, indent=2) + "\n", encoding="utf-8")
    return proposed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-tag")
    args = parser.parse_args()
    if args.update_tag:
        print(json.dumps(update_pin(args.update_tag), indent=2))
    else:
        pin = load_pin()
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            for key, value in pin.items():
                output.write(f"{key}={value}\n")
