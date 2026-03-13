param(
    [ValidateSet("start", "restart", "stop", "status")]
    [string]$Action = "status",
    [string]$ConfigPath = "",
    [string]$ProjectRoot = "",
    [string]$RuntimeDir = ""
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

function Get-RunnerProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedProjectRoot
    )

    $escapedRoot = [regex]::Escape($ResolvedProjectRoot)
    $patterns = @("mail_runner\.app", "run_loop_current_user", "start_loop_current_user", $escapedRoot)
    $joined = [string]::Join("|", $patterns)
    return @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match 'python|powershell|cmd' -and $_.CommandLine -match $joined
    })
}

function Stop-RunnerProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedProjectRoot,
        [Parameter(Mandatory = $true)]
        [string]$PidFile
    )

    if (Test-Path $PidFile) {
        $recordedPid = (Get-Content -Encoding utf8 $PidFile | Select-Object -First 1).Trim()
        if ($recordedPid -match '^\d+$') {
            try {
                Stop-Process -Id ([int]$recordedPid) -Force -ErrorAction Stop
            } catch {
            }
        }
    }

    $procs = Get-RunnerProcesses -ResolvedProjectRoot $ResolvedProjectRoot |
        Sort-Object ProcessId -Descending
    foreach ($proc in $procs) {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
        } catch {
        }
    }
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
    $fallback = Join-Path $resolvedProjectRoot "mail_config.local.yaml"
    if (Test-Path $preferred) {
        $ConfigPath = $preferred
    } else {
        $ConfigPath = $fallback
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

$runnerScriptPath = Join-Path $resolvedRuntimeDir "run_loop_current_user.ps1"
$bootstrapCmdPath = Join-Path $resolvedRuntimeDir "start_loop_current_user.cmd"
$pidFile = Join-Path $resolvedRuntimeDir "loop.pid"
$stdoutLog = Join-Path $resolvedRuntimeDir "loop.stdout.log"
$stderrLog = Join-Path $resolvedRuntimeDir "loop.stderr.log"
$userFile = Join-Path $resolvedRuntimeDir "loop.user.txt"

if ($Action -in @("stop", "restart")) {
    Stop-RunnerProcesses -ResolvedProjectRoot $resolvedProjectRoot -PidFile $pidFile
    if ($Action -eq "stop") {
        Write-Output "Mail runner stopped."
        exit 0
    }
}

if ($Action -eq "status") {
    $procs = Get-RunnerProcesses -ResolvedProjectRoot $resolvedProjectRoot
    if (-not $procs) {
        Write-Output "Mail runner is not running."
        exit 1
    }
    $procs |
        Sort-Object ProcessId |
        Select-Object ProcessId, ParentProcessId, Name, CommandLine |
        Format-List
    exit 0
}

$existing = Get-RunnerProcesses -ResolvedProjectRoot $resolvedProjectRoot
if ($existing) {
    Write-Output "Mail runner is already running."
    $existing |
        Sort-Object ProcessId |
        Select-Object ProcessId, ParentProcessId, Name, CommandLine |
        Format-List
    exit 0
}

$runnerScript = @"
`$ErrorActionPreference = "Stop"
Set-Location "$resolvedProjectRoot"
`$PID | Set-Content -Path "$pidFile"
whoami | Set-Content -Path "$userFile"
& "$pythonPath" -m mail_runner.app --loop --config "$resolvedConfigPath"
"@
Set-Content -Encoding utf8 -Path $runnerScriptPath -Value $runnerScript

$bootstrapCmd = @"
@echo off
setlocal
cd /d "$resolvedProjectRoot"
powershell -NoProfile -ExecutionPolicy Bypass -File "$runnerScriptPath" 1>> "$stdoutLog" 2>> "$stderrLog"
"@
Set-Content -Encoding ascii -Path $bootstrapCmdPath -Value $bootstrapCmd

cmd /c start "" /b "$bootstrapCmdPath"

Start-Sleep -Seconds 3
$started = Get-RunnerProcesses -ResolvedProjectRoot $resolvedProjectRoot
if (-not $started) {
    throw "Mail runner did not start successfully."
}

Write-Output ("Mail runner started with config: " + $resolvedConfigPath)
$started |
    Sort-Object ProcessId |
    Select-Object ProcessId, ParentProcessId, Name, CommandLine |
    Format-List
