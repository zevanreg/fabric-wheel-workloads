import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch
from uuid import UUID
import zipfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import deploy
import release_pin

TARGET = "11111111-1111-4111-8111-111111111111"


def load_workflow(name):
    # Preserve GitHub's "on" key instead of coercing it to a YAML 1.1 boolean.
    return yaml.load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"),
                     Loader=yaml.BaseLoader)


def make_wheel(directory, name="demo-date", version="0.1.0", tag="py3-none-any"):
    path = Path(directory) / "demo_date-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr("demo_date/__init__.py", "def print_current_date(): pass\n")
        wheel.writestr("demo_date-0.1.0.dist-info/METADATA",
                      f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        wheel.writestr("demo_date-0.1.0.dist-info/WHEEL",
                      f"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: {tag}\n")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


class WorkloadTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".staging"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_platform_and_notebook_metadata(self):
        notebook = ROOT / "workspace/PrintCurrentDateWheel.Notebook/notebook-content.py"
        source = notebook.read_text(encoding="utf-8")
        compile(source, str(notebook), "exec")
        chunks = source.split("# METADATA ********************")[1:]
        metadata = []
        for chunk in chunks:
            metadata.append(json.loads("\n".join(
                line.removeprefix("# META ") for line in chunk.splitlines()
                if line.startswith("# META ")
            )))
        self.assertEqual(metadata[0]["kernel_info"]["name"], "synapse_pyspark")
        self.assertEqual(metadata[1]["language"], "python")
        platforms = {}
        for path in (ROOT / "workspace").glob("*/.platform"):
            platform = json.loads(path.read_text())
            self.assertTrue(platform["$schema"].endswith("/2.0.0/schema.json"))
            self.assertEqual(platform["config"]["version"], "2.0")
            UUID(platform["config"]["logicalId"])
            name, item_type = path.parent.name.rsplit(".", 1)
            self.assertEqual(platform["metadata"]["type"], item_type)
            self.assertEqual(platform["metadata"]["displayName"], name)
            platforms[item_type] = platform
        self.assertEqual(metadata[0]["dependencies"]["environment"]["environmentId"],
                         platforms["Environment"]["config"]["logicalId"])
        parameters = yaml.safe_load((ROOT / "workspace/parameter.yml").read_text())
        self.assertEqual(len(parameters["find_replace"]), 2)
        expected = ["$items.Environment.DateDemoEnvironment.$id", "$workspace.$id"]
        for entry, replacement in zip(parameters["find_replace"], expected):
            self.assertIn(entry["find_value"], source)
            self.assertEqual(entry["replace_value"], {"_ALL_": replacement})
            self.assertEqual(entry["item_type"], "Notebook")
            self.assertEqual(entry["item_name"], "PrintCurrentDateWheel")
        compute = yaml.safe_load((ROOT / "workspace/DateDemoEnvironment.Environment/Setting/Sparkcompute.yml").read_text())
        self.assertEqual(compute["runtime_version"], 1.3)
        self.assertEqual(compute["dynamic_executor_allocation"]["max_executors"], 1)

    def test_notebook_calls_library(self):
        module = ModuleType("demo_date")
        module.print_current_date = Mock()
        source = (ROOT / "workspace/PrintCurrentDateWheel.Notebook/notebook-content.py").read_text()
        with patch.dict(sys.modules, {"demo_date": module}):
            exec(compile(source, "notebook", "exec"), {})
        module.print_current_date.assert_called_once_with()

    def test_valid_wheel_and_bad_hash(self):
        wheel, digest = make_wheel(self.directory)
        self.assertEqual(deploy.validate_wheel(wheel, digest)["version"], "0.1.0")
        for invalid in ["", "bad", "0" * 64]:
            with self.assertRaises(ValueError):
                deploy.validate_wheel(wheel, invalid)

    def test_wrong_package_version_and_tag(self):
        for values in [{"name": "other"}, {"version": "2.0.0"}, {"tag": "cp311-cp311-win_amd64"}]:
            wheel, digest = make_wheel(self.directory, **values)
            with self.assertRaises(ValueError):
                deploy.validate_wheel(wheel, digest)

    def arguments(self, scope="all"):
        wheel, digest = make_wheel(self.directory)
        return ["--workspace-id", TARGET, "--stage", "test", "--wheel", str(wheel),
                "--sha256", digest, "--scope", scope,
                "--manifest", str(self.directory / "manifest.json")]

    def test_dry_run_never_imports_sdk_or_changes_sources(self):
        before = {str(p): p.read_bytes() for p in (ROOT / "workspace").rglob("*") if p.is_file()}
        with patch.dict(sys.modules, {"fabric_cicd": None, "azure.identity": None}), contextlib.redirect_stdout(io.StringIO()):
            deploy.main(self.arguments() + ["--dry-run"])
        manifest = json.loads((self.directory / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "dry-run")
        self.assertEqual(manifest["workspace_id"], TARGET)
        self.assertEqual(before, {str(p): p.read_bytes() for p in (ROOT / "workspace").rglob("*") if p.is_file()})

    def test_live_scope_and_staging(self):
        for scope in ["all", "environment"]:
            def inspect(directory, workspace, stage, selected_scope):
                self.assertEqual((workspace, stage, selected_scope), (TARGET, "test", scope))
                self.assertEqual(len(list(directory.glob("*/Libraries/CustomLibraries/*.whl"))), 1)
                self.assertEqual((directory / "PrintCurrentDateWheel.Notebook").exists(), scope == "all")
                self.assertEqual((directory / "parameter.yml").exists(), scope == "all")
            with patch.object(deploy, "publish", side_effect=inspect), contextlib.redirect_stdout(io.StringIO()):
                deploy.main(self.arguments(scope))
            self.assertEqual(json.loads((self.directory / "manifest.json").read_text())["status"], "published")

    def test_publish_uses_azure_cli_and_exact_item_scope(self):
        sdk = ModuleType("fabric_cicd")
        sdk.FabricWorkspace = Mock()
        sdk.publish_all_items = Mock()
        identity = ModuleType("azure.identity")
        identity.AzureCliCredential = Mock()
        with patch.dict(sys.modules, {"fabric_cicd": sdk, "azure.identity": identity}):
            for scope, types in [("all", ["Environment", "Notebook"]), ("environment", ["Environment"])]:
                deploy.publish(self.directory, TARGET, "prod", scope)
                self.assertEqual(sdk.FabricWorkspace.call_args.kwargs["item_type_in_scope"], types)
                self.assertIs(sdk.FabricWorkspace.call_args.kwargs["token_credential"], identity.AzureCliCredential.return_value)
                sdk.publish_all_items.assert_called_with(sdk.FabricWorkspace.return_value)

    def test_live_failure_records_failure(self):
        with patch.object(deploy, "publish", side_effect=RuntimeError("publish failed")):
            with self.assertRaises(RuntimeError):
                deploy.main(self.arguments())
        self.assertEqual(json.loads((self.directory / "manifest.json").read_text())["status"], "failed")

    def test_release_template_fails_closed_and_valid_pin_passes(self):
        template = {"repository": "YOUR_OWNER/fabric-date-library", "tag": "v0.1.0",
                    "asset": "demo_date-0.1.0-py3-none-any.whl", "sha256": ""}
        path = self.directory / "release.json"
        path.write_text(json.dumps(template))
        with self.assertRaises(ValueError):
            release_pin.load_pin(path)
        pin = {"repository": "example/fabric-date-library", "tag": "v0.1.0",
               "asset": "demo_date-0.1.0-py3-none-any.whl", "sha256": "a" * 64}
        path.write_text(json.dumps(pin))
        self.assertEqual(release_pin.load_pin(path), pin)
        for key, value in [("tag", "v0.2.0"), ("repository", "--bad"), ("sha256", "")]:
            path.write_text(json.dumps({**pin, key: value}))
            with self.assertRaises(ValueError):
                release_pin.load_pin(path)

    def test_committed_release_is_template_or_complete_pin(self):
        pin = json.loads((ROOT / "release.json").read_text())
        if pin["repository"] == "YOUR_OWNER/fabric-date-library":
            self.assertEqual(pin["sha256"], "")
            self.assertEqual(pin["asset"], "demo_date-0.1.0-py3-none-any.whl")
        else:
            release_pin.load_pin()

    def test_workflows_are_parseable_and_use_oidc(self):
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            workflow = yaml.safe_load(path.read_text())
            self.assertIn("jobs", workflow)
        workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
        self.assertEqual(workflow["permissions"], {"contents": "read", "id-token": "write"})
        self.assertFalse(workflow["concurrency"]["cancel-in-progress"])
        self.assertEqual(workflow["concurrency"]["group"], "fabric-wheel-${{ inputs.stage }}")
        self.assertEqual(workflow["jobs"]["deploy"]["environment"], "${{ inputs.stage }}")
        self.assertEqual(workflow["jobs"]["deploy"]["timeout-minutes"], 90)

    def test_ci_triggers_development_pushes_and_pull_requests(self):
        workflow = load_workflow("ci.yml")
        self.assertEqual(set(workflow["on"]), {"push", "pull_request"})
        self.assertEqual(workflow["on"]["push"]["branches"], ["dev"])

    def test_deploy_supports_reuse_and_manual_stage_scope_selection(self):
        workflow = load_workflow("deploy.yml")
        self.assertEqual(set(workflow["on"]), {"workflow_call", "workflow_dispatch"})
        call = workflow["on"]["workflow_call"]
        self.assertEqual(call["inputs"], {
            "stage": {"required": "true", "type": "string"},
            "scope": {"required": "false", "type": "string", "default": "all"},
        })
        self.assertEqual(call["secrets"], {"GH_RELEASE_TOKEN": {"required": "false"}})
        manual = workflow["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(set(manual), {"stage", "scope"})
        self.assertEqual(manual["stage"]["required"], "true")
        self.assertEqual(manual["stage"]["type"], "choice")
        self.assertEqual(manual["stage"]["options"], ["dev", "test", "prod"])
        self.assertEqual(manual["scope"]["required"], "true")
        self.assertEqual(manual["scope"]["type"], "choice")
        self.assertEqual(manual["scope"]["options"], ["all", "environment"])
        self.assertEqual(manual["scope"]["default"], "all")

    def test_checkout_uses_triggering_commit(self):
        job = load_workflow("deploy.yml")["jobs"]["deploy"]
        checkouts = [step for step in job["steps"] if step.get("uses") == "actions/checkout@v4"]
        self.assertEqual(len(checkouts), 1)
        self.assertEqual(checkouts[0]["with"], {"persist-credentials": "false"})
        self.assertNotIn("TARGET_REF", job["env"])
        self.assertNotIn("TARGET_REF", "\n".join(step.get("run", "") for step in job["steps"]))

    def test_main_promotes_test_before_prod_using_same_workflow(self):
        workflow = load_workflow("promote.yml")
        self.assertEqual(workflow["on"], {"push": {"branches": ["main"]}})
        self.assertEqual(workflow["permissions"], {"contents": "read", "id-token": "write"})
        jobs = workflow["jobs"]
        self.assertEqual(set(jobs), {"test", "prod"})
        self.assertNotIn("needs", jobs["test"])
        self.assertEqual(jobs["prod"]["needs"], "test")
        for stage in ["test", "prod"]:
            with self.subTest(stage=stage):
                self.assertEqual(jobs[stage]["uses"], "./.github/workflows/deploy.yml")
                self.assertEqual(jobs[stage]["with"], {"stage": stage})
                self.assertEqual(jobs[stage]["secrets"], {
                    "GH_RELEASE_TOKEN": "${{ secrets.GH_RELEASE_TOKEN }}",
                })
                self.assertNotIn("if", jobs[stage])

    def test_wheel_workflow_validates_before_auth_and_preserves_evidence(self):
        steps = load_workflow("deploy.yml")["jobs"]["deploy"]["steps"]
        labels = [step.get("name", step.get("uses", step.get("run"))) for step in steps]
        order = [
            "python -m unittest discover -s tests -v",
            "Validate committed release pin",
            "Download approved release wheel",
            "Validate wheel before authentication",
            "azure/login@v2",
            "Publish environment and notebook",
        ]
        positions = [labels.index(label) for label in order]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(steps[labels.index("Validate committed release pin")]["run"],
                         "python scripts/release_pin.py")
        download = steps[labels.index("Download approved release wheel")]
        self.assertEqual(download["env"]["GH_TOKEN"],
                         "${{ secrets.GH_RELEASE_TOKEN || github.token }}")
        self.assertIn('assert not r["draft"] and not r["prerelease"]', download["run"])
        preview = steps[labels.index("Validate wheel before authentication")]
        publish = steps[labels.index("Publish environment and notebook")]
        command = ('python scripts/deploy.py --stage "$STAGE" --scope "$SCOPE" '
                   '--wheel "artifacts/$WHEEL_ASSET" --sha256 "$WHEEL_SHA256"')
        self.assertEqual(publish["run"], command)
        self.assertEqual(preview["run"], command + " --dry-run")
        pin = {
            "WHEEL_ASSET": "${{ steps.pin.outputs.asset }}",
            "WHEEL_SHA256": "${{ steps.pin.outputs.sha256 }}",
        }
        self.assertEqual(preview["env"], pin)
        self.assertEqual(publish["env"], pin)
        evidence = steps[labels.index("Keep deployment evidence")]
        self.assertEqual(evidence["if"], "always()")
        self.assertEqual(evidence["with"]["path"].splitlines(), [
            "deployment-manifest.json", "release.json", "release-info.json",
        ])


if __name__ == "__main__":
    unittest.main()
