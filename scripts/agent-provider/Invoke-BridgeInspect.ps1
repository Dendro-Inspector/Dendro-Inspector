<#
.SYNOPSIS
Run `dendro inspect` against the local agent-provider bridge.

.DESCRIPTION
Binds both roles to one adapter and repoints that adapter's base URL at the bridge, so the
request cannot reach a real vendor even when a working key for it sits in .env. The credential
it exports is a placeholder: the bridge only checks that the adapter sent one in the right
place.

Timeouts are the reason to prefer one dialect over another when a human or an agent is doing
the answering. `gemini` and `ollama` read their timeout from the environment, so this script
raises both to an hour. The OpenAI-compatible adapters (`nvidia`, `openrouter`) take theirs
from a constructor argument the registry does not pass, which leaves 300 s per call — fine
for replaying cached answers, tight for authoring them live.

.PARAMETER Dialect
Which adapter to exercise: gemini, ollama, nvidia or openrouter.

.PARAMETER Image
Photograph to inspect. Repeat the parameter for several.

.PARAMETER Fault
Optional failure to inject; see FAULTS in bridge.py. Applied by prefixing the base URL path,
so the adapter under test is unmodified.

.EXAMPLE
PS> .\scripts\agent-provider\Invoke-BridgeInspect.ps1 -Dialect gemini -Image evals\golden\log.jpg

.EXAMPLE
PS> .\scripts\agent-provider\Invoke-BridgeInspect.ps1 -Dialect nvidia -Fault multimodal -Image examples\log.jpg

.NOTES
See docs/agent-as-provider.md. Start bridge.py first.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('gemini', 'ollama', 'nvidia', 'openrouter')]
    [string]$Dialect,

    [Parameter(Mandatory = $true)]
    [string[]]$Image,

    [int]$Port = 8799,

    [ValidateSet('', 'unauthorized', 'quota', 'rate-limit', 'multimodal', 'flaky', 'truncated', 'fenced', 'garbage')]
    [string]$Fault = '',

    [string]$Location = '',

    [ValidateSet('spring', 'summer', 'autumn', 'winter', 'unknown')]
    [string]$Season = 'unknown',

    [ValidateSet('standing_tree', 'log', 'split_firewood', 'bark', 'leaf', 'needle', 'fruit', 'cone', 'wood', 'branch', 'seed', 'unknown')]
    [string]$ObjectType = 'unknown',

    [string]$Lang = 'en',

    [int]$StructuredRetries = 2,

    [string]$TraceOut = ''
)

$ErrorActionPreference = 'Stop'

$prefix = if ($Fault) { "/fault/$Fault" } else { '' }
$root = "http://127.0.0.1:$Port$prefix"

$env:DENDRO_PRIMARY_PROVIDER = $Dialect
$env:DENDRO_ARBITER_PROVIDER = $Dialect
$env:DENDRO_PRIMARY_MODEL = 'agent-bridge'
$env:DENDRO_ARBITER_MODEL = 'agent-bridge'
$env:DENDRO_STRUCTURED_RETRIES = "$StructuredRetries"

switch ($Dialect) {
    'gemini' {
        $env:GEMINI_ENDPOINT = "$root/v1beta"
        $env:GEMINI_API_KEY = 'bridge-local-placeholder'
        $env:GEMINI_TIMEOUT_SECONDS = '3600'
    }
    'ollama' {
        $env:OLLAMA_HOST = $root
        $env:OLLAMA_TIMEOUT_SECONDS = '3600'
    }
    'nvidia' {
        $env:NVIDIA_BASE_URL = "$root/v1"
        $env:NVIDIA_API_KEY = 'bridge-local-placeholder'
    }
    'openrouter' {
        $env:OPENROUTER_BASE_URL = "$root/v1"
        $env:OPENROUTER_API_KEY = 'bridge-local-placeholder'
    }
}

$dendro = Join-Path (Get-Location) '.venv\Scripts\dendro.exe'
if (-not (Test-Path $dendro)) { $dendro = 'dendro' }

$dendroArgs = @('inspect', '--lang', $Lang, '--object-type', $ObjectType, '--season', $Season, '--json')
foreach ($path in $Image) { $dendroArgs += @('--image', $path) }
if ($Location) { $dendroArgs += @('--location', $Location) }
if ($TraceOut) { $dendroArgs += @('--trace-out', $TraceOut) }

Write-Host "adapter  $Dialect -> $root" -ForegroundColor Cyan
if ($Fault) { Write-Host "fault    $Fault" -ForegroundColor Yellow }
Write-Host "command  $dendro $($dendroArgs -join ' ')" -ForegroundColor DarkGray

# `dendro` writes its structured logs to stderr on every run, including successful ones.
# Windows PowerShell 5.1 wraps native stderr in an ErrorRecord, which `Stop` would turn into a
# terminating error before the real exit code is ever read — so relax it for the call itself.
$ErrorActionPreference = 'Continue'
& $dendro @dendroArgs
exit $LASTEXITCODE
