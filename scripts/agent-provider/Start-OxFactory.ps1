<#
.SYNOPSIS
Start the loopback Dendro bridge and the configured provider routes.

.DESCRIPTION
Starts one bridge plus one worker per upstream route. Claude handles primary analysis, the
OpenCode, OpenRouter and Cline Ox transports form one concurrent reviewer pool, and Sol
handles deterministic escalations. Every worker watches the same pending directory and
atomically claims the oldest eligible request.

The workers read credentials only from their normal local login state or the ignored .env
file. No credential is accepted as a command-line argument or written to the process manifest.

.EXAMPLE
PS> .\scripts\agent-provider\Start-OxFactory.ps1
PS> .\scripts\agent-provider\Invoke-BridgeInspect.ps1 -Dialect openrouter `
      -BridgeModel claude-main -ReviewerBridgeModel ox-factory `
      -ArbiterBridgeModel sol-judge -Image evals\golden\birch\photo.jpg
#>
[CmdletBinding()]
param(
    [int]$Port = 8799,
    [string]$StateDir = '.bridge',
    [string]$OpenCodeModel = 'opencode/x-preview-f-free',
    [string]$OxModel = 'stealth/ox-alpha',
    [string]$SolModel = 'gpt-5.6-sol',
    [string]$ClaudeModel = 'opus',
    [ValidateSet('codex', 'claude')]
    [string]$JudgeBackend = 'codex',
    [ValidateSet('ox', 'claude')]
    [string]$ReviewerBackend = 'ox',
    [int]$ClineTimeoutSeconds = 240,
    [switch]$WithoutOpenRouter,
    [switch]$WithoutSol,
    [switch]$WithoutClaude,
    [switch]$WithoutCline
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$resolvedState = Join-Path $root $StateDir
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = (Get-Command python -ErrorAction Stop).Source }
$bridge = Join-Path $PSScriptRoot 'bridge.py'
$worker = Join-Path $PSScriptRoot 'worker.py'

function Resolve-FactoryExecutable {
    param(
        [Parameter(Mandatory = $true)][string]$CommandName,
        [Parameter(Mandatory = $true)][string[]]$Candidates
    )
    foreach ($candidate in $Candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    $command = Get-Command $CommandName -ErrorAction SilentlyContinue
    if ($null -ne $command -and -not [string]::IsNullOrWhiteSpace($command.Source)) {
        return $command.Source
    }
    throw "Required executable not found: $CommandName"
}

$roamingAppData = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::ApplicationData
)
$localAppData = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::LocalApplicationData
)
$userProfileDir = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::UserProfile
)
$openCode = Resolve-FactoryExecutable -CommandName 'opencode' -Candidates @(
    (Join-Path $roamingAppData 'npm\node_modules\opencode-ai\bin\opencode.exe')
)
$codex = Resolve-FactoryExecutable -CommandName 'codex' -Candidates @(
    (Join-Path $localAppData 'Programs\OpenAI\Codex\bin\codex.exe')
)
$cline = Resolve-FactoryExecutable -CommandName 'cline' -Candidates @(
    (Join-Path $roamingAppData 'npm\cline.cmd')
)
$claude = Resolve-FactoryExecutable -CommandName 'claude' -Candidates @(
    (Join-Path $userProfileDir '.local\bin\claude.exe')
)

$logDir = Join-Path $resolvedState 'process-logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Some agent hosts inject both `Path` and `PATH` into the Windows environment block.
# Windows PowerShell 5.1 then throws before `Start-Process` can launch anything because its
# case-insensitive environment dictionary sees a duplicate key. All executable discovery is
# complete above, so retain the resolved process Path under one canonical spelling.
$processPath = [Environment]::GetEnvironmentVariable(
    'Path',
    [EnvironmentVariableTarget]::Process
)
if (-not [string]::IsNullOrWhiteSpace($processPath)) {
    [Environment]::SetEnvironmentVariable(
        'PATH',
        $null,
        [EnvironmentVariableTarget]::Process
    )
    [Environment]::SetEnvironmentVariable(
        'Path',
        $processPath,
        [EnvironmentVariableTarget]::Process
    )
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
    return [PSCustomObject]@{
        name = $Name
        pid = $process.Id
        stdout = $stdout
        stderr = $stderr
    }
}

$processes = @()
$processes += Start-HiddenProcess -Name 'bridge' -Arguments @(
    $bridge, '--port', "$Port", '--state-dir', $resolvedState
)
# The reviewer pool is either the Ox transports or one Claude worker per concurrent review
# node. Each Claude reviewer needs its own capacity group: the three review nodes arrive at
# the same moment and a shared lease would serialize them.
if ($ReviewerBackend -eq 'claude') {
    $reviewerRoute = 'opus-review'
    foreach ($reviewerIndex in 1..3) {
        $processes += Start-HiddenProcess -Name "opus-review-$reviewerIndex" -Arguments @(
            $worker,
            '--state-dir', $resolvedState,
            '--worker-id', "opus-review-$reviewerIndex",
            '--capacity-group', "claude-code-review-$reviewerIndex",
            '--route', $reviewerRoute,
            '--backend', 'claude',
            '--model', $ClaudeModel,
            '--executable', $claude
        )
    }
}
else {
    $reviewerRoute = 'ox-factory'
    $processes += Start-HiddenProcess -Name 'opencode-ox' -Arguments @(
        $worker,
        '--state-dir', $resolvedState,
        '--worker-id', 'opencode-zen-ox',
        '--capacity-group', 'opencode-zen',
        '--route', $reviewerRoute,
        '--backend', 'opencode',
        '--model', $OpenCodeModel,
        '--executable', $openCode
    )
    if (-not $WithoutOpenRouter) {
        $processes += Start-HiddenProcess -Name 'openrouter-ox' -Arguments @(
            $worker,
            '--state-dir', $resolvedState,
            '--worker-id', 'openrouter-ox',
            '--capacity-group', 'openrouter-account',
            '--route', $reviewerRoute,
            '--backend', 'openrouter',
            '--model', $OxModel,
            '--env-file', (Join-Path $root '.env')
        )
    }
}
# The arbiter route is named after the model that answers it, so a trace never attributes an
# escalation to a judge that did not run it.
if ($JudgeBackend -eq 'claude') {
    $judgeName = 'opus-judge'
    $judgeRoute = 'opus-judge'
    $judgeCapacityGroup = 'claude-code-judge'
    $judgeModel = $ClaudeModel
    $judgeExecutable = $claude
}
else {
    $judgeName = 'codex-sol'
    $judgeRoute = 'sol-judge'
    $judgeCapacityGroup = 'codex-sol'
    $judgeModel = $SolModel
    $judgeExecutable = $codex
}
if (-not $WithoutSol) {
    $processes += Start-HiddenProcess -Name $judgeName -Arguments @(
        $worker,
        '--state-dir', $resolvedState,
        '--worker-id', $judgeName,
        '--capacity-group', $judgeCapacityGroup,
        '--route', $judgeRoute,
        '--backend', $JudgeBackend,
        '--model', $judgeModel,
        '--executable', $judgeExecutable
    )
}
if (-not $WithoutClaude) {
    $processes += Start-HiddenProcess -Name 'claude-main' -Arguments @(
        $worker,
        '--state-dir', $resolvedState,
        '--worker-id', 'claude-main',
        '--capacity-group', 'claude-code',
        '--route', 'claude-main',
        '--backend', 'claude',
        '--model', $ClaudeModel,
        '--executable', $claude
    )
}
if (-not $WithoutCline -and $ReviewerBackend -eq 'ox') {
    $processes += Start-HiddenProcess -Name 'cline-ox' -Arguments @(
        $worker,
        '--state-dir', $resolvedState,
        '--worker-id', 'cline-ox',
        '--capacity-group', 'cline-gateway',
        '--route', $reviewerRoute,
        '--backend', 'cline',
        '--provider', 'cline',
        '--model', $OxModel,
        '--executable', $cline,
        '--timeout', "$ClineTimeoutSeconds"
    )
}

$manifest = Join-Path $resolvedState 'factory-processes.json'
$processes | ConvertTo-Json | Set-Content -Encoding UTF8 $manifest

Write-Host "Main route: claude-main" -ForegroundColor Cyan
Write-Host "Reviewer route: $reviewerRoute" -ForegroundColor Cyan
Write-Host "Arbiter route: $judgeRoute" -ForegroundColor Cyan
Write-Host "Bridge: http://127.0.0.1:$Port" -ForegroundColor Cyan
$processes | Format-Table -AutoSize
Write-Host "Process manifest: $manifest" -ForegroundColor DarkGray
Write-Host "Logs: $logDir" -ForegroundColor DarkGray
