# Kubernetes images

Container images for running inspections as Kubernetes batch Jobs, one pod per photograph.
The cluster, the Job manifests and the control plane that launches them are not in this
repository; these images are what they run.

| File | Image |
|---|---|
| [`Dockerfile`](Dockerfile) | Batch image: the `dendro` CLI with the `openai`, `anthropic` and `images` extras, plus `prompts/`, `knowledge/`, `evals/` and [`scripts/batch/`](../../scripts/batch/) in `/app`. Runs as UID 10001. |
| [`agents.Dockerfile`](agents.Dockerfile) | The batch image plus the Claude Code and Codex CLIs and [`scripts/agent-provider/`](../../scripts/agent-provider/). |

Build both from the repository root:

```bash
docker build -f deploy/k8s/Dockerfile -t dendro-inspector:local .
docker build -f deploy/k8s/agents.Dockerfile --build-arg BASE_IMAGE=dendro-inspector:local \
  -t dendro-inspector:local-agents .
```

The batch scripts are standard library only and call `dendro inspect --json`:

- `run_dataset_case.py`: one photograph of a pinned Hugging Face dataset revision, chosen by
  `JOB_COMPLETION_INDEX`, checked against its SHA-256 before use.
- `run_upload_case.py`: one uploaded case from `RESULTS_DIR/<run>/inputs/`.
- `summarize_run.py`: collects the per-case records of a run.

## Continuous integration

[`container-images.yml`](../../.github/workflows/container-images.yml) builds both images on
every push and pull request and smoke-tests them, including the public evaluation suite run
from inside the batch image. It needs no secrets and pushes nothing.

## Deploying from GitHub

[`deploy-aks.yml`](../../.github/workflows/deploy-aks.yml) builds both images in Azure Container
Registry, tagged `<short commit>` and `<short commit>-agents`, and registers them with the
cluster's control function. The function starts a smoke run of a few dataset photographs with
the new image; the workflow waits for it, writes its results and pod logs to the job summary,
and fails if any photograph failed.

| Trigger | Smoke run | Default images for new runs |
|---|---|---|
| Pull request into `main` | yes | unchanged |
| Push to `main` (a merged pull request; `main` is protected) | yes | replaced |
| Manual (Actions → Deploy to AKS → Run workflow, on `main`) | `smoke_cases` input | replaced if `promote` is ticked |

Pull requests into other branches, and pushes to them, get only the checks in `ci.yml`.

**Who deploys without asking.** A first job, with no secrets, looks up the repository role of
whoever triggered the run and, for a pull request, of its author. When both are admin or
maintain, the deploy runs in the `aks` environment at once. Anyone else's pull request still
gets every check, and its deploy runs in `aks-review`, which waits until a maintainer approves
it (Actions → the run → Review deployments). Each new push needs a new approval.

The workflow runs on `pull_request_target`, so it is always `main`'s copy of this file, and a
pull request cannot change the check. Both environments accept only runs whose workflow comes
from `main`, so a workflow written on another branch cannot reach them. The pull request's code
is only uploaded to the registry as the build context; nothing from it runs on the runner that
holds the credentials. A smoke run uses the models configured on the cluster, so each one costs
a few photographs' worth of tokens.

A change to `deploy-aks.yml` itself takes effect only once it is on `main`.

The control function's dashboard lists every deployment with its pull request, commit, image
and smoke run.

One-time setup, by a maintainer:

1. An Entra ID application with federated credentials for subjects
   `repo:<owner>/<repo>:environment:aks` and `…:environment:aks-review` (organisations that use
   immutable-ID subject claims present `repo:<owner>@<id>/<repo>@<id>:environment:…`; issuer `https://token.actions.githubusercontent.com`,
   audience `api://AzureADTokenExchange`), and the **Contributor** role on the container
   registry only (`az acr build` schedules a run, which `AcrPush` does not allow). It needs no
   rights on the cluster or the function app.
2. A function key on the control function's `register_deployment`, `get_run`, `run_logs` and
   `run_summary` functions only, so CI can deploy and read results but not start, stop or
   change anything else.
3. Two GitHub environments, `aks` and `aks-review`, each limited to deployments from `main`;
   `aks-review` with the maintainers as required reviewers. Both hold:

   | Name | Kind | Value |
   |---|---|---|
   | `AKS_CONTROL_KEY` | secret | the function key from step 2 |
   | `AZURE_CLIENT_ID` | variable | the application's client ID |
   | `AZURE_TENANT_ID` | variable | the Entra ID tenant |
   | `AZURE_SUBSCRIPTION_ID` | variable | the subscription holding the registry |
   | `AKS_ACR_NAME` | variable | registry name, without `.azurecr.io` |
   | `AKS_CONTROL_URL` | variable | the control function's base URL, without `/api` |
   | `AKS_SMOKE_CASES` | variable, optional | photographs per smoke run (default 3, max 10) |
