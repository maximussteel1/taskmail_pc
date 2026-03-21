param(
    [string]$ConfigPath = "",
    [string]$ProjectRoot = "",
    [string]$RuntimeDir = "",
    [string]$TaskRoot = "",
    [int]$RefreshSeconds = 5,
    [int]$MaxBufferLines = 1000,
    [int]$HistoryLimit = 12,
    [int]$Iterations = 0,
    [string]$ThreadId = "",
    [string]$WindowTitle = "",
    [string]$ReadyFile = "",
    [string]$ExitStatePath = "",
    [switch]$RequestKill,
    [switch]$ExitWhenThreadNotRunning,
    [switch]$NoClear
)

function Resolve-FullPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseDir,
        [Parameter(Mandatory = $true)]
        [string]$PathText
    )

    if ([string]::IsNullOrWhiteSpace($PathText)) {
        return ""
    }
    if ([System.IO.Path]::IsPathRooted($PathText)) {
        $candidate = $PathText
    } else {
        $candidate = Join-Path $BaseDir $PathText
    }
    return [System.IO.Path]::GetFullPath($candidate)
}

function Invoke-ObserveCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonPath,
        [Parameter(Mandatory = $true)]
        [string[]]$BaseArgs,
        [Parameter(Mandatory = $true)]
        [string[]]$ObserveArgs
    )

    $output = (& $PythonPath @BaseArgs @ObserveArgs | Out-String)
    if ($LASTEXITCODE -ne 0) {
        throw "Observe command failed with exit code ${LASTEXITCODE}: $($ObserveArgs -join ' ')"
    }
    return $output.TrimEnd("`r", "`n")
}

function Set-ConsoleBufferLimit {
    param(
        [Parameter(Mandatory = $true)]
        [int]$BufferLines
    )

    try {
        $rawUi = $Host.UI.RawUI
        $bufferSize = $rawUi.BufferSize
        $windowSize = $rawUi.WindowSize
        $targetHeight = [Math]::Max($BufferLines, $windowSize.Height)
        $bufferSize.Width = [Math]::Max($bufferSize.Width, $windowSize.Width)
        $bufferSize.Height = $targetHeight
        $rawUi.BufferSize = $bufferSize
    } catch {
    }
}

function Write-MonitorState {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PathText,
        [Parameter(Mandatory = $true)]
        [hashtable]$Payload
    )

    if ([string]::IsNullOrWhiteSpace($PathText)) {
        return
    }
    $parent = Split-Path -Parent $PathText
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    ($Payload | ConvertTo-Json -Depth 4) + "`n" | Set-Content -Encoding utf8 $PathText
}

$ErrorActionPreference = "Stop"

if ($RefreshSeconds -lt 0) {
    throw "RefreshSeconds must be greater than or equal to 0."
}
if ($MaxBufferLines -lt 1) {
    throw "MaxBufferLines must be greater than or equal to 1."
}
if ($HistoryLimit -lt 1) {
    throw "HistoryLimit must be greater than or equal to 1."
}
if ($RequestKill -and [string]::IsNullOrWhiteSpace($ThreadId)) {
    throw "ThreadId is required when RequestKill is used."
}
if ($Iterations -lt 0) {
    throw "Iterations must be greater than or equal to 0."
}
if ($Iterations -eq 0 -and $RefreshSeconds -eq 0) {
    throw "RefreshSeconds must be greater than 0 when Iterations is 0."
}

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$resolvedProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)

if ([string]::IsNullOrWhiteSpace($RuntimeDir)) {
    $RuntimeDir = Join-Path $resolvedProjectRoot "_tmp_live_mail_runner"
}
$resolvedRuntimeDir = [System.IO.Path]::GetFullPath($RuntimeDir)
New-Item -ItemType Directory -Force -Path $resolvedRuntimeDir | Out-Null

if (-not [string]::IsNullOrWhiteSpace($WindowTitle)) {
    try {
        $Host.UI.RawUI.WindowTitle = $WindowTitle
    } catch {
    }
}
Set-ConsoleBufferLimit -BufferLines $MaxBufferLines

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $preferred = Join-Path $resolvedRuntimeDir "mail_config.loop_30s.yaml"
    $fallbackBot = Join-Path $resolvedProjectRoot "mail_config.bot.local.yaml"
    $fallbackUser = Join-Path $resolvedProjectRoot "mail_config.local.yaml"
    if (Test-Path $preferred) {
        $ConfigPath = $preferred
    } elseif (Test-Path $fallbackBot) {
        $ConfigPath = $fallbackBot
    } else {
        $ConfigPath = $fallbackUser
    }
}
$resolvedConfigPath = Resolve-FullPath -BaseDir $resolvedProjectRoot -PathText $ConfigPath
if (-not (Test-Path $resolvedConfigPath)) {
    throw "Config file not found: $resolvedConfigPath"
}

$pythonPath = Join-Path $resolvedProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonPath)) {
    throw "Python executable not found: $pythonPath"
}

$observeBaseArgs = @(
    "-m", "mail_runner.observe",
    "--config", $resolvedConfigPath,
    "--runtime-dir", $resolvedRuntimeDir
)
if (-not [string]::IsNullOrWhiteSpace($TaskRoot)) {
    $resolvedTaskRoot = Resolve-FullPath -BaseDir $resolvedProjectRoot -PathText $TaskRoot
    $observeBaseArgs += @("--task-root", $resolvedTaskRoot)
}

if (-not [string]::IsNullOrWhiteSpace($ThreadId)) {
    if ($RequestKill) {
        $controlArgs = @(
            "-m", "mail_runner.runtime_control",
            "request-thread-kill",
            $ThreadId,
            "--runtime-dir", $resolvedRuntimeDir,
            "--config", $resolvedConfigPath,
            "--source", "monitor_window"
        )
        if (-not [string]::IsNullOrWhiteSpace($TaskRoot)) {
            $controlArgs += @("--task-root", $resolvedTaskRoot)
        }
        & $pythonPath @controlArgs
        exit $LASTEXITCODE
    }

    if (-not [string]::IsNullOrWhiteSpace($ReadyFile)) {
        Write-MonitorState -PathText $ReadyFile -Payload @{
            thread_id   = $ThreadId
            ready_at    = (Get-Date).ToString("s")
            runtime_dir = $resolvedRuntimeDir
        }
    }

    Write-Host "Mail Runner Monitor"
    Write-Host "Config: $resolvedConfigPath"
    Write-Host "Runtime Dir: $resolvedRuntimeDir"
    Write-Host "Focused Thread: $ThreadId"
    Write-Host "Poll Seconds: $RefreshSeconds"
    Write-Host "Buffer Lines: $MaxBufferLines"
    Write-Host "History Limit: $HistoryLimit"
    Write-Host "Kill Command: .\\scripts\\monitor_mail_runner.cmd -ThreadId $ThreadId -RequestKill"
    Write-Host ""

    $followArgs = @($observeBaseArgs)
    $followArgs += @(
        "follow-thread-live",
        $ThreadId,
        "--poll-seconds",
        [string]$RefreshSeconds,
        "--history-limit",
        [string]$HistoryLimit
    )
    if ($ExitWhenThreadNotRunning) {
        $followArgs += "--exit-when-inactive"
    }
    if ($Iterations -gt 0) {
        $followArgs += @("--iterations", [string]$Iterations)
    }
    if (-not [string]::IsNullOrWhiteSpace($ExitStatePath)) {
        $followArgs += @("--exit-state-path", $ExitStatePath)
    }

    & $pythonPath @followArgs
    if ($LASTEXITCODE -ne 0) {
        if (-not [string]::IsNullOrWhiteSpace($ExitStatePath) -and -not (Test-Path $ExitStatePath)) {
            Write-MonitorState -PathText $ExitStatePath -Payload @{
                reason    = "script_error"
                thread_id = $ThreadId
                exit_code = $LASTEXITCODE
            }
        }
        throw "Observe command failed with exit code ${LASTEXITCODE}: follow-thread-live $ThreadId"
    }
    exit 0
}

$iteration = 0
while ($true) {
    if (-not $NoClear) {
        Clear-Host
    }

    $updatedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "Mail Runner Monitor"
    Write-Host "Updated: $updatedAt"
    Write-Host "Config: $resolvedConfigPath"
    Write-Host "Runtime Dir: $resolvedRuntimeDir"
    Write-Host "Refresh Seconds: $RefreshSeconds"
    if (-not [string]::IsNullOrWhiteSpace($ThreadId)) {
        Write-Host "Focused Thread: $ThreadId"
    }
    Write-Host ""

    Write-Host "=== STATUS ==="
    $statusOutput = Invoke-ObserveCommand -PythonPath $pythonPath -BaseArgs $observeBaseArgs -ObserveArgs @("status")
    if (-not [string]::IsNullOrWhiteSpace($statusOutput)) {
        Write-Host $statusOutput
    }
    Write-Host ""

    Write-Host "=== RUNNING ==="
    $runningOutput = Invoke-ObserveCommand -PythonPath $pythonPath -BaseArgs $observeBaseArgs -ObserveArgs @("list-running")
    if (-not [string]::IsNullOrWhiteSpace($runningOutput)) {
        Write-Host $runningOutput
    }
    Write-Host ""

    Write-Host "=== QUEUE ==="
    $queueOutput = Invoke-ObserveCommand -PythonPath $pythonPath -BaseArgs $observeBaseArgs -ObserveArgs @("list-queue")
    if (-not [string]::IsNullOrWhiteSpace($queueOutput)) {
        Write-Host $queueOutput
    }

    Write-Host ""
    Write-Host "Press Ctrl+C to close this monitor window."

    $iteration += 1
    if ($Iterations -gt 0 -and $iteration -ge $Iterations) {
        break
    }
    if ($RefreshSeconds -gt 0) {
        Start-Sleep -Seconds $RefreshSeconds
    }
}
