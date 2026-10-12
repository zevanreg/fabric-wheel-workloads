"""Validate release.json and expose trusted pins to GitHub Actions."""

import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def load_pin(path=ROOT / "release.json"):
    pin = json.loads(Path(path).read_text(encoding="utf-8"))
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


if __name__ == "__main__":
    pin = load_pin()
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for key, value in pin.items():
            output.write(f"{key}={value}\n")
