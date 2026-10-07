# Running individual private-photo experiments

- **Status:** Draft
- **Owner:** Dendro Inspector maintainers
- **Date:** 2026-08-23
- **Last-verified:** 2026-08-23

This runbook processes a private photo dataset through the Dendro-owned bridge one image at
a time. Claude is the primary worker, the OpenCode/OpenRouter/Cline Ox transports form the
reviewer pool, and Sol is called only when the deterministic escalation gate requires an
arbiter.

The outer dataset loop is sequential. The three reviewers for one photograph may run
concurrently, but the next photograph does not start until the current run has been recorded.
This keeps provider pressure bounded and gives every photograph its own result, trace and
immutable `run_id`.

## Sources of truth

- `evals/100 top/manifest.json` is the canonical private experiment ledger.
- `evals/100 top/manifest.md` is generated from the JSON ledger. Do not edit it manually.
- `scripts/agent-provider/run_photo_ledger.py` verifies image hashes, runs Dendro and appends
  one run record at a time.
- `scripts/agent-provider/Start-OxFactory.ps1` declares the live provider topology.
- Each run stores `stdout.json`, `stderr.log` and a Dendro trace under
  `evals/100 top/runs/<run_id>/`.

Both the photographs and these ledger artifacts are ignored by Git. Confirm that before a
real run:

```powershell
git check-ignore -v "evals/100 top/manifest.json"
git check-ignore -v "evals/100 top/manifest.md"
```

Never put a credential in the manifest, a command line, a trace or this repository. Provider
workers use their existing CLI login state or the ignored local `.env` file.

## 1. Preflight

Run these commands from the repository root in PowerShell 7+:

```powershell
Test-Path -LiteralPath ".venv\Scripts\python.exe"
Test-Path -LiteralPath "evals\100 top\manifest.json"
.venv\Scripts\dendro.exe prompt-info
```

The two `Test-Path` commands must return `True`. `prompt-info` must report
`compatibility_status: compatible`.

Authenticate the enabled provider clients before starting the batch. Do not print or inspect
their tokens as part of the experiment:

- Claude Code for `claude-main`;
- OpenCode Zen for one `ox-factory` transport;
- Cline for another `ox-factory` transport;
- OpenRouter through the ignored `.env` file for the direct Ox transport;
- Codex for `sol-judge`.

Choose a unique batch id, a matching fresh bridge-state directory and an unused loopback
port. A batch id may contain only lowercase letters, digits and hyphens and must be at most 80
characters. The configuration id is a stable topology identifier and uses underscores:

```powershell
$experimentBatch = "2026-08-23-full-claude-ox-sol-v1"
$experimentConfig = "claude_ox_sol_v1"
$experimentState = ".bridge\$experimentBatch"
$experimentPort = 8803

if (Test-Path -LiteralPath $experimentState) {
    throw "Choose a new experimentBatch: bridge state already exists."
}
if (Get-NetTCPConnection -LocalPort $experimentPort -State Listen -ErrorAction SilentlyContinue) {
    throw "Choose another experimentPort: this port is already listening."
}
```

Always use a new state directory for a new batch or after an abandoned client run. Reusing a
state can expose the next run to stale pending requests or cached answers from the abandoned
queue.

## 2. Start the bridge factory

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
    .\scripts\agent-provider\Start-OxFactory.ps1 `
    -Port $experimentPort `
    -StateDir $experimentState `
    -ClineTimeoutSeconds 240
```

Verify the exact processes declared by that state and the loopback listener:

```powershell
$factoryProcesses = Get-Content `
    (Join-Path $experimentState "factory-processes.json") -Raw | ConvertFrom-Json

$factoryProcesses | ForEach-Object {
    $process = Get-Process -Id $_.pid -ErrorAction SilentlyContinue
    [pscustomobject]@{
        Worker = $_.name
        Pid = $_.pid
        Alive = [bool]$process
    }
}

Test-NetConnection -ComputerName 127.0.0.1 `
    -Port $experimentPort -InformationLevel Quiet
```

Every declared worker must show `Alive=True`; the connection test must return `True`.

To monitor routing, retries and fallback between Ox transports from another PowerShell
window:

```powershell
Get-Content (Join-Path $experimentState "process-logs\bridge.stdout.log") -Wait
```

## 3. Run one canary photograph

Run the first photograph alone before committing the providers to the full dataset:

```powershell
.venv\Scripts\python.exe scripts\agent-provider\run_photo_ledger.py `
    --manifest "evals\100 top\manifest.json" `
    --configuration $experimentConfig `
    --batch-id $experimentBatch `
    --photo-id photo-001 `
    --port $experimentPort `
    --limit 1
```

A completed canary prints `RECORDED photo-001 status=completed`. Verify its ledger entry:

```powershell
$ledger = Get-Content "evals\100 top\manifest.json" -Raw | ConvertFrom-Json
$canary = $ledger.photos | Where-Object id -eq "photo-001"
$canary.runs | Where-Object run_id -eq "$experimentBatch-001" | ConvertTo-Json -Depth 8
```

Check the status, result, duration, model-call count and `trace_path`. A completed run is an
engineering result, not a correctness claim. `reference_label` remains independent from the
system prediction.

## 4. Run all photographs sequentially

Use the same batch id after the canary:

```powershell
.venv\Scripts\python.exe scripts\agent-provider\run_photo_ledger.py `
    --manifest "evals\100 top\manifest.json" `
    --configuration $experimentConfig `
    --batch-id $experimentBatch `
    --port $experimentPort `
    --limit 100
```

The runner sees that `<batch-id>-001` already exists, skips that immutable canary run and
continues with the remaining photographs. For each new photograph it:

1. verifies the image SHA-256 recorded in the manifest;
2. sends only that photograph and its declared context through the local bridge;
3. waits for the graph, reviewers and conditional arbiter to finish;
4. writes the result and trace under a unique run directory;
5. appends the run to `manifest.json` atomically;
6. regenerates `manifest.md`;
7. starts the next photograph only after the append completes.

Do not run two ledger commands with the same manifest concurrently. The append is atomic, but
the complete read-run-append workflow is intentionally single-writer.

## 5. Check progress and outcomes

The following report counts only this batch:

```powershell
$ledger = Get-Content "evals\100 top\manifest.json" -Raw | ConvertFrom-Json
$batchRuns = @(
    $ledger.photos.runs |
        Where-Object { $_.run_id -like "$experimentBatch-*" }
)

[pscustomobject]@{
    Recorded = $batchRuns.Count
    Completed = @($batchRuns | Where-Object status -eq "completed").Count
    Failed = @($batchRuns | Where-Object status -eq "failed").Count
    Escalated = @($batchRuns | Where-Object escalated -eq $true).Count
    Abstained = @($batchRuns | Where-Object abstained -eq $true).Count
    NoTaxon = @($batchRuns | Where-Object { $null -eq $_.result.taxon }).Count
    Repairs = ($batchRuns | Measure-Object repair_count -Sum).Sum
}
```

`Abstained` counts only runs whose canonical `FinalDecision.abstained` flag is true: the
explicit abstention route broadened the verdict. `NoTaxon` counts completed runs that
returned no identity, including unknown or insufficient-evidence results reached through
review bounds. Do not use either as an alias for the other.

Inspect one run through the trace path stored in its ledger record. Do not infer provider
success from the final taxon alone; inspect provider calls, validation failures, component
projections, escalation reasons and `arbiter_used`.

The generated Markdown table is a human view:

```powershell
Get-Item "evals\100 top\manifest.md"
```

## 6. Recover without rewriting history

If one photograph exits normally with `status=failed`, the runner records that failed run and
stops. To continue the remaining batch:

1. stop the old factory using section 7;
2. choose a new, unused `$experimentState` while keeping the same `$experimentBatch`;
3. start the factory again;
4. repeat the command in section 4.

The runner skips all run ids already recorded under that batch, including the failed one, and
continues with later photographs.

Retry the failed photograph separately with a new batch id so the failed record remains
immutable:

```powershell
$retryBatch = "$experimentBatch-retry1"

.venv\Scripts\python.exe scripts\agent-provider\run_photo_ledger.py `
    --manifest "evals\100 top\manifest.json" `
    --configuration $experimentConfig `
    --batch-id $retryBatch `
    --photo-id photo-037 `
    --port $experimentPort `
    --limit 1
```

Replace `photo-037` with the failed photo id. Never edit or replace an earlier `runs[]` entry.
A new attempt always gets a new `run_id`.

If the process was forcibly interrupted before a run could be appended, leave its orphaned
run directory intact for audit and retry with a new batch id. Do not delete it to make the old
id reusable.

## 7. Stop only this factory

Wait until the ledger runner has exited. Do not stop workers while a photograph is in flight.
Resolve the state beneath the repository, then stop only the exact PIDs recorded in that
state's process manifest:

```powershell
$repositoryRoot = (Resolve-Path ".").Path
$resolvedExperimentState = (Resolve-Path -LiteralPath $experimentState).Path

if (-not $resolvedExperimentState.StartsWith(
    $repositoryRoot + [IO.Path]::DirectorySeparatorChar
)) {
    throw "Experiment state is outside the repository."
}

$factoryProcesses = Get-Content `
    (Join-Path $resolvedExperimentState "factory-processes.json") -Raw |
    ConvertFrom-Json

foreach ($factoryProcess in $factoryProcesses) {
    $process = Get-Process -Id $factoryProcess.pid -ErrorAction SilentlyContinue
    if ($process -and $process.ProcessName -eq "python") {
        Stop-Process -Id $factoryProcess.pid -Force
    }
}
```

Never use `Stop-Process -Name python`; other Python processes may belong to unrelated work.
Verify cleanup:

```powershell
$factoryProcesses | ForEach-Object {
    [pscustomobject]@{
        Worker = $_.name
        Alive = [bool](Get-Process -Id $_.pid -ErrorAction SilentlyContinue)
    }
}

Test-NetConnection -ComputerName 127.0.0.1 `
    -Port $experimentPort -InformationLevel Quiet -WarningAction SilentlyContinue
```

Every worker must show `Alive=False`; the connection test must return `False`.

## Interpretation boundary

The ledger distinguishes `reference_label` from every system `run`. Never copy a prediction
into the reference field. Reference labels need independent provenance, and accuracy may only
be reported over the explicitly labelled denominator.

A photographed failure may reveal a generalized defect, but it may not directly author a
taxon card, prompt rule or threshold. Follow `AGENTS.md` section 16: reproduce the invariant
with a non-golden synthetic test and use an independent domain source before changing policy.

For provider architecture and routing details, see [Using a coding agent as the model
provider](agent-as-provider.md). For benchmark interpretation, see
[Evaluation](evaluation.md).
