<#
.SYNOPSIS
Start the loopback Dendro bridge and Mistral Vibe workers on the vibe-main route.

.DESCRIPTION
Starts one bridge plus -Workers Vibe workers. The three review nodes arrive at the same moment,
so three workers let them run side by side; each has its own capacity group and its own Vibe
root. Every worker writes its own Vibe settings (see VibeBackend in worker.py): read_file
only, telemetry and update checks off.

The workers send prompts and photographs to Mistral's API. Starting them needs the owner's
approval in the current session (AGENTS.md section 2).

Vibe reads the key from MISTRAL_API_KEY, from the ignored .env file the worker loads, or from
the system keyring that `vibe --setup` writes to. No credential is accepted as an argument.

.EXAMPLE
PS> .\scripts\agent-provider\Start-VibeFactory.ps1 -Vibe "$env:TEMP\vb\Scripts\vibe.exe"
PS> .\scripts\agent-provider\Invoke-BridgeInspect.ps1 -Dialect anthropic `
      -BridgeModel vibe-main -ReviewerBridgeModel vibe-main -ArbiterBridgeModel vibe-main `
      -Image path\to\photo.jpg
#>
[CmdletBinding()]
param(
    [int]$Port = 8799,
    [string]$StateDir = '.bridge',
    [string]$Model = 'mistral-large-4-0',
    [string]$Vibe = '',
    [ValidateRange(1, 32)]
    [int]$Workers = 3,
    [int]$TimeoutSeconds = 1200,
    [double]$MaxPricePerCall = 0,
    # 'fastest' = the lowest thinking the model has (low for Large 4, off for Medium 3.5);
    # 'default' keeps the model's own (high for Large 4: about 3 minutes a call).
    [string]$Thinking = 'fastest',
    # Longest edge of the copies Vibe opens; 0 sends the photographs as received.
    [int]$MaxEdge = 1600,
    [string]$Python = '',
    [switch]$WithoutBridge
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$resolvedState = if ([IO.Path]::IsPathRooted($StateDir)) { $StateDir } else { Join-Path $root $StateDir }
$python = if ($Python) { $Python } else { Join-Path $root '.venv\Scripts\python.exe' }
if (-not (Test-Path $python)) { $python = (Get-Command python -ErrorAction Stop).Source }
$bridge = Join-Path $PSScriptRoot 'bridge.py'
$worker = Join-Path $PSScriptRoot 'worker.py'

if (-not $Vibe) {
    $command = Get-Command vibe -ErrorAction SilentlyContinue
    if ($null -eq $command) { throw 'vibe not found: install mistral-vibe or pass -Vibe <path>' }
    $Vibe = $command.Source
}
$Vibe = (Resolve-Path -LiteralPath $Vibe).Path

$logDir = Join-Path $resolvedState 'process-logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Same duplicate Path/PATH guard as Start-OxFactory.ps1.
$processPath = [Environment]::GetEnvironmentVariable('Path', [EnvironmentVariableTarget]::Process)
if (-not [string]::IsNullOrWhiteSpace($processPath)) {
    [Environment]::SetEnvironmentVariable('PATH', $null, [EnvironmentVariableTarget]::Process)
    [Environment]::SetEnvironmentVariable('Path', $processPath, [EnvironmentVariableTarget]::Process)
}

function Start-HiddenProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $stdout = Join-Path $logDir "$Name.stdout.log"
    $stderr = Join-Path $logDir "$Name.stderr.log"
    $process = Start-Process -FilePath $python -ArgumentList $Arguments -WorkingDirectory $root `
        -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr `
        -PassThru
    return [PSCustomObject]@{ name = $Name; pid = $process.Id; stdout = $stdout; stderr = $stderr }
}

$processes = @()
if (-not $WithoutBridge) {
    $processes += Start-HiddenProcess -Name 'bridge' -Arguments @(
        $bridge, '--port', "$Port", '--state-dir', $resolvedState
    )
}
foreach ($index in 1..$Workers) {
    $name = "vibe-$index"
    $arguments = @(
        $worker,
        '--state-dir', $resolvedState,
        '--worker-id', $name,
        '--capacity-group', "mistral-vibe-$index",
        '--route', 'vibe-main',
        '--backend', 'vibe',
        '--model', $Model,
        '--executable', $Vibe,
        '--timeout', "$TimeoutSeconds",
        '--vibe-root', (Join-Path $resolvedState "vibe-roots\$name"),
        '--vibe-thinking', $Thinking,
        '--vibe-max-edge', "$MaxEdge",
        '--env-file', (Join-Path $root '.env')
    )
    if ($MaxPricePerCall -gt 0) {
        $arguments += @('--vibe-max-price', "$MaxPricePerCall")
    }
    $processes += Start-HiddenProcess -Name $name -Arguments $arguments
}

$manifest = Join-Path $resolvedState 'vibe-processes.json'
$processes | ConvertTo-Json | Set-Content -Encoding UTF8 $manifest

Write-Host "Route: vibe-main ($Model, $Workers workers, thinking $Thinking, max edge $MaxEdge)" -ForegroundColor Cyan
Write-Host "Bridge: http://127.0.0.1:$Port" -ForegroundColor Cyan
$processes | Format-Table -AutoSize
Write-Host "Process manifest: $manifest" -ForegroundColor DarkGray
Write-Host "Logs: $logDir" -ForegroundColor DarkGray
