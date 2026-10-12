"""Validate and stage an immutable wheel, then publish Fabric items."""

import argparse
from datetime import datetime, timezone
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from uuid import UUID
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def validate_wheel(path, expected_sha256):
    path = Path(path)
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise ValueError("--sha256 must be an independently pinned 64-character SHA-256")
    match = re.fullmatch(r"demo_date-([0-9]+(?:\.[0-9]+){2})-py3-none-any\.whl", path.name)
    if not match:
        raise ValueError("Expected a demo_date-X.Y.Z-py3-none-any.whl universal wheel")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256.lower():
        raise ValueError("Wheel SHA-256 does not match the approved pin")
    with zipfile.ZipFile(path) as wheel:
        prefix = f"demo_date-{match[1]}.dist-info/"
        if wheel.namelist().count(prefix + "METADATA") != 1:
            raise ValueError("Expected exactly one matching wheel METADATA")
        metadata = BytesParser().parsebytes(wheel.read(prefix + "METADATA"))
        name = re.sub(r"[-_.]+", "-", metadata.get("Name", "")).lower()
        if name != "demo-date" or metadata.get("Version") != match[1]:
            raise ValueError("Wheel package name/version does not match its filename")
        wheel_metadata = BytesParser().parsebytes(wheel.read(prefix + "WHEEL"))
        if wheel_metadata.get("Root-Is-Purelib", "").lower() != "true":
            raise ValueError("Wheel must be pure Python")
        if wheel_metadata.get_all("Tag") != ["py3-none-any"]:
            raise ValueError("Wheel must declare only the py3-none-any tag")
        if "demo_date/__init__.py" not in wheel.namelist():
            raise ValueError("Wheel must contain the demo_date package")
    return {"asset": path.name, "sha256": digest, "version": match[1]}


def git_commit():
    result = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "uncommitted"


def publish(directory, workspace_id, stage, scope):
    from azure.identity import AzureCliCredential
    from fabric_cicd import FabricWorkspace, publish_all_items

    workspace = FabricWorkspace(
        workspace_id=workspace_id,
        repository_directory=str(directory),
        environment=stage,
        item_type_in_scope=["Environment", "Notebook"] if scope == "all" else ["Environment"],
        token_credential=AzureCliCredential(),
    )
    publish_all_items(workspace)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", default=os.getenv("FABRIC_WORKSPACE_ID"))
    parser.add_argument("--stage", choices=["dev", "test", "prod"], required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--scope", choices=["all", "environment"], default="all")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--manifest", type=Path, default=ROOT / "deployment-manifest.json")
    args = parser.parse_args(argv)
    if not args.workspace_id:
        parser.error("--workspace-id or FABRIC_WORKSPACE_ID is required")
    workspace_id = str(UUID(args.workspace_id))
    if UUID(workspace_id).int == 0:
        raise ValueError("Target workspace must not be the source placeholder GUID")
    wheel_info = validate_wheel(args.wheel, args.sha256)
    staging = ROOT / ".staging"
    staging.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="deploy-", dir=staging) as temporary:
        directory = Path(temporary) / "workspace"
        shutil.copytree(ROOT / "workspace", directory)
        libraries = directory / "DateDemoEnvironment.Environment" / "Libraries" / "CustomLibraries"
        libraries.mkdir(parents=True)
        shutil.copy2(args.wheel, libraries / args.wheel.name)
        if args.scope == "environment":
            shutil.rmtree(directory / "PrintCurrentDateWheel.Notebook")
            (directory / "parameter.yml").unlink()
        manifest = {
            "commit": git_commit(), "stage": args.stage, "workspace_id": workspace_id,
            "scope": args.scope, "wheel": wheel_info,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "status": "dry-run" if args.dry_run else "started",
        }
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if not args.dry_run:
            completed = False
            try:
                publish(directory, workspace_id, args.stage, args.scope)
                completed = True
            finally:
                manifest["status"] = "published" if completed else "failed"
                args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
