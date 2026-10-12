# Fabric wheel workloads

Standalone workload repository for a five-repository demo:

| Repository | Responsibility |
| --- | --- |
| `fabric-date-library` | Build `demo-date` and publish versioned GitHub release wheels/checksums |
| **`fabric-wheel-workloads`** | Publish an Environment containing the pinned wheel and its Notebook |
| `fabric-infrastructure` | Provision separate dev/test/prod workspaces |
| `fabric-inline-workloads` | Publish the standard-library-only comparison Notebook |

`PrintCurrentDateWheel` imports `demo_date.print_current_date()` and prints
today's UTC date as `YYYY-MM-DD`. No Lakehouse is necessary. The wheel resides
in `Libraries/CustomLibraries` in the staged Environment definition, **not**
in git and not in a notebook `%pip` cell. Fabric runtime 1.3 executes the
notebook; Python 3.11 runs the deployment tooling in GitHub Actions.

## First-time setup

1. Publish `fabric-date-library` to GitHub. Its `v*` tag workflow releases
   `demo_date-0.1.0-py3-none-any.whl` and `SHA256SUMS`.
2. Run `fabric-infrastructure` against your existing active Fabric
   capacity. Copy the dev/test/prod IDs from its `workspace-ids.json`.
   These workloads neither provision capacity nor change capacity assignment.
3. Publish this local repo yourself: it starts on `main` without commits,
   a remote, or a GitHub repository. Create a `dev` branch for development,
   protect `dev` and `main`, and require **Offline CI** on pull requests.
4. Create GitHub environments **dev**, **test**, and **prod** here. Set these
   environment variables for each:

   | Variable | Value |
   | --- | --- |
   | `AZURE_CLIENT_ID` | Deployment Entra application/client ID |
   | `AZURE_TENANT_ID` | Entra tenant ID |
   | `FABRIC_WORKSPACE_ID` | Corresponding wheel workspace ID |

5. Configure Entra federated credentials for each environment:
   issuer `https://token.actions.githubusercontent.com`,
   audience `api://AzureADTokenExchange`,
   subject `repo:OWNER/fabric-wheel-workloads:environment:dev`
   (repeat for `test` and `prod`). `azure/login@v2` authenticates using
   OIDC; `AzureCliCredential` reuses that login. No Azure client secret or
   subscription ID is required for this Fabric-only deployment.
6. Enable the Fabric tenant setting allowing service principals to use
   Fabric APIs for the deployment principal's security group. Give it the
   necessary workspace access, typically Contributor/Member; using the same
   principal as infrastructure (workspace owner/Admin) avoids a separate grant.
   Workspace access is distinct from Azure capacity RBAC. Environment and
   Notebook APIs must support the principal in your tenant.
7. Require reviewer approval on **test/prod**; restrict deployment branches
   and dispatch access to trusted refs. A push to `main` starts test/prod
   promotion, so merge only reviewed changes into protected `main`.
8. Configure the automatic pin PR below, or update `release.json` manually:
   replace `YOUR_OWNER`, set the released tag/asset, and fill `sha256` with
   the **actual published wheel** checksum verified from the release.
   Review and merge the pin PR into `dev` before deploying.
   A local build's SHA can differ from a later GitHub build. The blank
   template deliberately blocks deployment; CI still runs offline.
9. For a private library in a separate repo, add optional repository secret
   `GH_RELEASE_TOKEN`, a fine-grained PAT or GitHub App token with library
   **Contents: read** access. Default `GITHUB_TOKEN` does **not** authorize
   another private repository. Public release downloads can use the default
   token. GitHub CLI handles asset retrieval/authentication and redirects.
   Main promotion explicitly forwards this optional secret to both reusable
   deployment calls.

Do not populate the pin automatically from an unreviewed download at deployment
time. A committed, independently reviewed wheel SHA-256 is the trust boundary;
tags/assets can otherwise be replaced. Only stable, non-draft, non-prerelease
releases are accepted by the workflow.

## Local offline verification (PowerShell)

Use Python 3.11 for the SDK; fabric-cicd 1.3.0 requires Python >=3.9,<3.14.
The pin exists on public PyPI; some private indexes or a Python 3.14 interpreter
can hide it. Use your approved package index with Python 3.11, not an unreviewed
upgrade to make installation succeed.

```powershell
Set-Location C:\repos\fabric-wheel-workloads
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
$wheel = 'C:\repos\fabric-date-library\dist\demo_date-0.1.0-py3-none-any.whl'
$sha = (Get-FileHash $wheel -Algorithm SHA256).Hash.ToLower()
.\.venv\Scripts\python.exe scripts\deploy.py --stage dev --workspace-id 11111111-1111-4111-8111-111111111111 --wheel $wheel --sha256 $sha --dry-run
```

The sample GUID is only an offline target. Computing a local checksum here
demonstrates staging of your trusted local build; use an independently approved
release SHA for actual promotion. Dry-run never imports/constructs
`FabricWorkspace`, obtains credentials, or calls Fabric. It copies sources
under this repository's ignored `.staging`, validates the wheel ZIP's package
name/version/pure-Python tag and SHA, stages the wheel without extracting it,
and writes ignored `deployment-manifest.json`. No tracked sources change.
Dry-run requires only the standard library; tests additionally need PyYAML.

For a real local deployment, `az login --tenant YOUR_TENANT_ID`, use a **real**
workspace ID and approved wheel/SHA, and omit `--dry-run`. Local execution
without a commit records `uncommitted`; GitHub deployments record the triggering
workflow commit.

## Deploy and promote

1. Commit changes on `dev`. **Offline CI** runs on dev pushes and pull
   requests. Run **Deploy pinned wheel workloads** (`workflow_dispatch`),
   selecting the `dev` branch, stage **dev**, and scope **all**. Checkout
   and the committed `release.json` come from the commit that triggered
   this run; there is no separate workload SHA input.
2. CI tests and wheel/pin validation happen before Azure authentication.
   `fabric-cicd==1.3.0` publishes Environment then Notebook using its standard
   dependency order, and waits for Environment publish completion before
   reporting successful deployment. Notebook metadata is parameterized with
   `$items.Environment.DateDemoEnvironment.$id` and `$workspace.$id`;
   `_ALL_` applies those dynamic replacements to every stage.
3. In Fabric, open `PrintCurrentDateWheel`, verify the selected Environment is
   `DateDemoEnvironment`, start a **new Spark session**, and run the notebook.
   Confirm a single date line matching the UTC date at execution time.
   Workflows publish; they do **not** execute notebooks. This is the required
   live smoke test, and it needs an active supported paid/trial capacity.
4. After the dev smoke test, merge the reviewed change into `main`. Its push
   starts **Promote on main**, which calls the same deployment workflow for
   **test**, then **prod** only after test succeeds. Approve test, smoke-test
   after publication, then approve prod and repeat the smoke test. Both
   stages deploy the same triggering main commit, even if the branch later
   advances. Archive run artifacts: workload commit, wheel version/hash,
   workspace, stage, status, and time.

Automatic main promotion uses scope **all**. Manual dispatch remains available
for any stage from a trusted branch/tag permitted by the target environment.
Environment reviewer approvals still apply; workflows do not automate the
Fabric smoke test.

### Library-only change (no notebook source edits)

Release a new library tag. The configured automation verifies its published
wheel and opens a PR changing only `release.json` to the new tag/asset/hash.
Review and merge into `dev`. Once the notebook/binding exists, manually deploy
to dev with **environment** scope and smoke-test.
Merging into `main` follows the same test/prod promotion process, using **all**.

Environment-only deployment remains a manual option for any stage: it stages
only the Environment and does not republish notebooks. Initial deployment must
use **all** to create the notebook/binding. Existing Spark sessions may retain
the old library; restart them.

The library release workflow dispatches only the pin-update workflow, never
deployment. Reviewing and merging the release pin is the explicit promotion
boundary. Dev deployment is manual; test/prod promotion starts when that
reviewed change reaches `main`.

### Automatic pin PR setup

1. Publish these workflow/script changes on `main` in both repositories and
   bring the workload changes onto `dev`. The update workflow runs from `main`
   and checks out `dev`; that branch must exist and contain the updater.
2. In **fabric-wheel-workloads**, set secret `PIN_PR_TOKEN` to a fine-grained
   PAT with **Contents: read/write** and **Pull requests: read/write** for
   this repository. Use a PAT (or a suitable GitHub App token), not the default
   `GITHUB_TOKEN`, so the generated PR triggers **Offline CI**. The token's
   identity must have access to the repository. Keep branch review protections.
3. In **fabric-date-library**, set repository variable `WORKLOADS_REPOSITORY`
   to `zevanreg/fabric-wheel-workloads` and secret `WORKLOADS_DISPATCH_TOKEN`
   to a fine-grained PAT with **Actions: read/write** for the workload repo.
   The library token only dispatches the workflow; it does not write the pin.
4. For a private library, configure `GH_RELEASE_TOKEN` here as described above.
   Downloads use that token; PR writes use the separate `PIN_PR_TOKEN`.
5. Push a new library release tag. After successful publication, the library
   dispatches **Update library release pin**. For an already published release
   (including `v0.1.0`), run that workflow manually on `main` with its exact tag.

The updater accepts only stable published `vX.Y.Z` releases from the repository
already configured in `release.json`. It verifies `SHA256SUMS`, the downloaded
wheel's hash and package metadata, then runs offline tests before opening a
pin-only PR against `dev`. PR branches are `automation/library-release-pin/vX.Y.Z`;
repeating a tag updates the same PR or does nothing if already pinned. It rejects
downgrades and changed assets for an already pinned tag. Updates are serialized;
there is no auto-merge, deployment, or Azure authentication. Check both workflow
runs if dispatch or PR creation fails; a dispatch failure does not undo publication.

This automation does not bypass review: a checksum supplied by the same release
verifies consistency, not independent trust in its publisher. Keep release
protections, immutable assets, and review the resulting pin before merging.

### Notebook change / rollback

Edit the notebook on `dev`, pass CI, manually deploy **all**, smoke-test, and
merge into `main` for test/prod promotion.

Roll back by reverting workload sources and the release pin to a previously
approved version on `dev`, then repeat the same deployment/promotion process.
Alternatively, manually dispatch from a retained approved branch/tag if the
target environment permits it; the workflow does not accept a separate SHA
input. Use **all** when notebook definitions also need restoring, and retain
the approved wheel release. Never rebuild the old wheel and assume its checksum
is identical.

## Boundaries and troubleshooting

- No orphan cleanup/deletions, Lakehouse, schedule, or Fabric Git connection.
  Do not combine Git synchronization and this publisher on the same items.
- Concurrency serializes active deployments per repo/stage and never cancels
  a running publish. GitHub can replace older *pending* runs; manually verify
  which SHA completes. Use separate workspaces per repo/stage; this is not
  a cross-repository distributed lock.
- Publishing can take several minutes; failure evidence records `failed` when
  the publisher raises. Partial cloud updates are not automatically rolled back.
- Capacity sizing may constrain the demo's 8-core driver/executor. Adjust
  compute settings only after confirming supported capacity/pool limits.
- Import errors: confirm Environment publish succeeded, notebook binding points
  at the target workspace, and start a new Spark session. Authorization errors:
  check tenant setting, federated subject, workspace role, and environment vars.
- Offline tests verify definitions, mocking, staging, checksum guards, workflow
  entry points and promotion order, not Fabric API acceptance or actual capacity
  execution. No cloud deployment was performed while creating these local files.

References: [fabric-cicd parameterization](https://microsoft.github.io/fabric-cicd/latest/how_to/parameterization/),
[Environment publish API](https://learn.microsoft.com/en-us/rest/api/fabric/environment/items/publish-environment),
[Azure Login OIDC](https://github.com/Azure/login#login-with-openid-connect-oidc-recommended).
