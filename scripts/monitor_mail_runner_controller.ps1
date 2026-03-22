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
    [switch]$RequestKill,
    [Alias("ExitWhenThreadNotRunning")]
    [switch]$ExitWhenThreadNotActive,
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

function Add-ArgumentPair {
    param(
        [System.Collections.Generic.List[string]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return
    }
    $Arguments.Add($Name)
    $Arguments.Add($Value)
}

function Test-ThreadIsActive {
    param(
        [string]$ResolvedTaskRoot,
        [string]$ThreadId
    )

    if ([string]::IsNullOrWhiteSpace($ResolvedTaskRoot) -or [string]::IsNullOrWhiteSpace($ThreadId)) {
        return $true
    }

    $statePath = Join-Path (Join-Path $ResolvedTaskRoot $ThreadId) "thread_state.json"
    if (-not (Test-Path $statePath)) {
        return $false
    }

    try {
        $state = Get-Content -Encoding utf8 -Raw $statePath | ConvertFrom-Json
    } catch {
        return $false
    }

    return (("" + $state.lifecycle).Trim() -eq "active")
}

function Start-MonitorWorker {
    param(
        [Parameter(Mandatory = $true)]
        [string]$WorkerScriptPath,
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,
        [Parameter(Mandatory = $true)]
        [string[]]$WorkerArguments,
        [switch]$PassThru
    )

    $argumentList = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $WorkerScriptPath
    ) + $WorkerArguments

    if ($PassThru) {
        return Start-Process -FilePath "powershell.exe" -WorkingDirectory $WorkingDirectory -ArgumentList $argumentList -PassThru
    }
    Start-Process -FilePath "powershell.exe" -WorkingDirectory $WorkingDirectory -ArgumentList $argumentList | Out-Null
    return $null
}

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$resolvedProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)

if ([string]::IsNullOrWhiteSpace($RuntimeDir)) {
    $RuntimeDir = Join-Path $resolvedProjectRoot "_tmp_live_mail_runner"
}
$resolvedRuntimeDir = [System.IO.Path]::GetFullPath($RuntimeDir)
New-Item -ItemType Directory -Force -Path $resolvedRuntimeDir | Out-Null

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
$resolvedTaskRoot = Resolve-FullPath -BaseDir $resolvedProjectRoot -PathText $TaskRoot
$pythonPath = Join-Path $resolvedProjectRoot ".venv\Scripts\python.exe"
$workerScriptPath = Join-Path $resolvedProjectRoot "scripts\monitor_mail_runner.ps1"

$workerArgs = [System.Collections.Generic.List[string]]::new()
Add-ArgumentPair -Arguments $workerArgs -Name "-ProjectRoot" -Value $resolvedProjectRoot
Add-ArgumentPair -Arguments $workerArgs -Name "-RuntimeDir" -Value $resolvedRuntimeDir
Add-ArgumentPair -Arguments $workerArgs -Name "-ConfigPath" -Value $resolvedConfigPath
Add-ArgumentPair -Arguments $workerArgs -Name "-TaskRoot" -Value $resolvedTaskRoot
Add-ArgumentPair -Arguments $workerArgs -Name "-RefreshSeconds" -Value ([string]$RefreshSeconds)
Add-ArgumentPair -Arguments $workerArgs -Name "-MaxBufferLines" -Value ([string]$MaxBufferLines)
Add-ArgumentPair -Arguments $workerArgs -Name "-HistoryLimit" -Value ([string]$HistoryLimit)
Add-ArgumentPair -Arguments $workerArgs -Name "-Iterations" -Value ([string]$Iterations)
Add-ArgumentPair -Arguments $workerArgs -Name "-ThreadId" -Value $ThreadId
Add-ArgumentPair -Arguments $workerArgs -Name "-WindowTitle" -Value $WindowTitle
if ($RequestKill) {
    $workerArgs.Add("-RequestKill")
}
if ($ExitWhenThreadNotActive) {
    $workerArgs.Add("-ExitWhenThreadNotActive")
}
if ($NoClear) {
    $workerArgs.Add("-NoClear")
}

if ($RequestKill -or [string]::IsNullOrWhiteSpace($ThreadId)) {
    Start-MonitorWorker -WorkerScriptPath $workerScriptPath -WorkingDirectory $resolvedProjectRoot -WorkerArguments $workerArgs.ToArray()
    exit 0
}

if (-not (Test-ThreadIsActive -ResolvedTaskRoot $resolvedTaskRoot -ThreadId $ThreadId)) {
    exit 0
}

$monitorStateDir = Join-Path $resolvedRuntimeDir "monitor_window_state"
New-Item -ItemType Directory -Force -Path $monitorStateDir | Out-Null
$token = [guid]::NewGuid().ToString("N")
$readyPath = Join-Path $monitorStateDir "${ThreadId}_${token}.ready.json"
$exitStatePath = Join-Path $monitorStateDir "${ThreadId}_${token}.exit.json"
Add-ArgumentPair -Arguments $workerArgs -Name "-ReadyFile" -Value $readyPath
Add-ArgumentPair -Arguments $workerArgs -Name "-ExitStatePath" -Value $exitStatePath

try {
    $child = Start-MonitorWorker `
        -WorkerScriptPath $workerScriptPath `
        -WorkingDirectory $resolvedProjectRoot `
        -WorkerArguments $workerArgs.ToArray() `
        -PassThru
    if ($null -eq $child) {
        exit 0
    }
    $child.WaitForExit()

    if (-not (Test-Path $readyPath)) {
        exit 0
    }

    $shouldQueueClose = $false
    if (-not (Test-Path $exitStatePath)) {
        $shouldQueueClose = $true
    } else {
        try {
            $exitState = Get-Content -Encoding utf8 -Raw $exitStatePath | ConvertFrom-Json
            if (("" + $exitState.reason).Trim() -eq "interrupted") {
                $shouldQueueClose = $true
            }
        } catch {
        }
    }

    if ($shouldQueueClose -and (Test-Path $pythonPath)) {
        $controlArgs = @(
            "-m", "mail_runner.runtime_control",
            "request-thread-close",
            $ThreadId,
            "--runtime-dir", $resolvedRuntimeDir,
            "--config", $resolvedConfigPath,
            "--source", "monitor_window_close"
        )
        if (-not [string]::IsNullOrWhiteSpace($resolvedTaskRoot)) {
            $controlArgs += @("--task-root", $resolvedTaskRoot)
        }
        & $pythonPath @controlArgs *> $null
    }
} finally {
    Remove-Item -LiteralPath $readyPath -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $exitStatePath -ErrorAction SilentlyContinue
}
