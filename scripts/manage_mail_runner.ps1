param(
    [ValidateSet("start", "restart", "stop", "shutdown", "status", "detach-restart")]
    [string]$Action = "status",
    [string]$ConfigPath = "",
    [string]$ProjectRoot = "",
    [string]$RuntimeDir = "",
    [switch]$NoPopup
)

$script:RunnerProcessDiscoveryFallbackWarned = $false

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

function Show-SuccessPopup {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title,
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    if ($NoPopup) {
        return
    }

    try {
        $shell = New-Object -ComObject WScript.Shell
        [void]$shell.Popup($Message, 5, $Title, 64)
    } catch {
    }
}

function Get-OptionalIntValue {
    param(
        [object]$Value
    )

    if ($null -eq $Value) {
        return $null
    }

    $text = ("" + $Value).Trim()
    if ($text -match '^\d+$') {
        return [int]$text
    }
    return $null
}

function Get-PidRecord {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PidFile
    )

    if (-not (Test-Path $PidFile)) {
        return $null
    }

    try {
        $raw = Get-Content -Encoding utf8 -Raw $PidFile
    } catch {
        return $null
    }

    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $null
    }

    $trimmed = $raw.Trim()
    if ($trimmed -match '^\d+$') {
        return [pscustomobject]@{
            LauncherProcessId = $null
            HostProcessId     = [int]$trimmed
        }
    }

    try {
        $payload = $trimmed | ConvertFrom-Json
    } catch {
        return $null
    }

    return [pscustomobject]@{
        LauncherProcessId = Get-OptionalIntValue -Value $payload.launcher_pid
        HostProcessId     = Get-OptionalIntValue -Value $payload.host_pid
    }
}

function Save-PidRecord {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PidFile,
        [int]$LauncherProcessId = 0,
        [int]$HostProcessId = 0
    )

    $payload = [ordered]@{
        launcher_pid = if ($LauncherProcessId -gt 0) { $LauncherProcessId } else { $null }
        host_pid     = if ($HostProcessId -gt 0) { $HostProcessId } else { $null }
    }
    $payload | ConvertTo-Json | Set-Content -Encoding utf8 -Path $PidFile
}

function Quote-PowerShellLiteral {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    return "'" + $Value.Replace("'", "''") + "'"
}

function Start-DetachedRunnerRestart {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedConfigPath,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedRuntimeDir
    )

    $restartCommand = "Start-Sleep -Seconds 2; & " + (Quote-PowerShellLiteral -Value $ScriptPath) + " restart -ConfigPath " + (Quote-PowerShellLiteral -Value $ResolvedConfigPath) + " -RuntimeDir " + (Quote-PowerShellLiteral -Value $ResolvedRuntimeDir)
    if ($NoPopup) {
        $restartCommand += " -NoPopup"
    }
    $helper = Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $restartCommand) `
        -WindowStyle Hidden `
        -PassThru
    return ("powershell-helper-pid=" + $helper.Id)
}

function Add-UniqueProcessId {
    param(
        [System.Collections.ArrayList]$Target,
        [object]$ProcessId
    )

    $candidate = Get-OptionalIntValue -Value $ProcessId
    if (($null -ne $candidate) -and (-not $Target.Contains($candidate))) {
        [void]$Target.Add($candidate)
    }
}

function Write-RunnerProcessDiscoveryFallbackWarning {
    if (-not $script:RunnerProcessDiscoveryFallbackWarned) {
        Write-Warning "Win32_Process lookup is unavailable in this shell. Falling back to host_state.json + loop.pid for runner management; legacy process auto-detection may be incomplete."
        $script:RunnerProcessDiscoveryFallbackWarned = $true
    }
}

function New-RunnerProcessRecordFromPid {
    param(
        [int]$ProcessId,
        [string]$RunnerKind = "host",
        [string]$CommandLine = ""
    )

    if ($ProcessId -le 0) {
        return $null
    }

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $null
    }

    $name = if ($process.ProcessName) { $process.ProcessName + ".exe" } else { "python.exe" }
    $displayCommandLine = if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        "(command line unavailable; discovered via pid file or host_state.json)"
    } else {
        $CommandLine
    }

    return [pscustomobject]@{
        ProcessId       = $ProcessId
        ParentProcessId = $null
        Name            = $name
        RunnerKind      = $RunnerKind
        CommandLine     = $displayCommandLine
    }
}

function Get-RunnerProcessesFallback {
    param(
        [ValidateSet("all", "host", "legacy")]
        [string]$Kind = "all",
        [string]$PidFile = "",
        [string]$HostStatePath = ""
    )

    Write-RunnerProcessDiscoveryFallbackWarning
    if ($Kind -eq "legacy") {
        return @()
    }

    $candidatePids = New-Object System.Collections.ArrayList
    $pidRecord = $null
    if (-not [string]::IsNullOrWhiteSpace($PidFile)) {
        $pidRecord = Get-PidRecord -PidFile $PidFile
        if ($null -ne $pidRecord) {
            Add-UniqueProcessId -Target $candidatePids -ProcessId $pidRecord.LauncherProcessId
            Add-UniqueProcessId -Target $candidatePids -ProcessId $pidRecord.HostProcessId
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($HostStatePath)) {
        $hostState = Get-HostState -HostStatePath $HostStatePath
        Add-UniqueProcessId -Target $candidatePids -ProcessId (Get-HostStatePid -HostState $hostState)
    }

    $records = @()
    foreach ($candidatePid in $candidatePids) {
        $label = ""
        if (($null -ne $pidRecord) -and ($candidatePid -eq $pidRecord.LauncherProcessId)) {
            $label = "(launcher process from loop.pid)"
        } elseif (($null -ne $pidRecord) -and ($candidatePid -eq $pidRecord.HostProcessId)) {
            $label = "(host process from loop.pid)"
        } else {
            $label = "(host process from host_state.json)"
        }
        $record = New-RunnerProcessRecordFromPid -ProcessId $candidatePid -RunnerKind "host" -CommandLine $label
        if ($null -ne $record) {
            $records += $record
        }
    }

    return @($records | Sort-Object ProcessId -Unique)
}

function Stop-RunnerProcessId {
    param(
        [int]$ProcessId
    )

    if ($ProcessId -le 0) {
        return
    }
    if ($null -eq (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
        return
    }

    if ($env:OS -eq "Windows_NT") {
        try {
            & taskkill /T /F /PID $ProcessId *> $null
        } catch {
        }
    }

    try {
        Stop-Process -Id $ProcessId -Force -ErrorAction Stop
    } catch {
    }
}

function Test-IsHostRunnerCommandLine {
    param(
        [string]$CommandLine
    )

    return $CommandLine -match '(^| )-m +mail_runner\.host( |$)'
}

function Test-IsLegacyRunnerCommandLine {
    param(
        [string]$CommandLine
    )

    return $CommandLine -match '(^| )-m +mail_runner\.app( |$)' `
        -and $CommandLine -match '(^| )--loop( |$)'
}

function Get-RunnerProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedProjectRoot,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedConfigPath,
        [string]$PidFile = "",
        [string]$HostStatePath = "",
        [ValidateSet("all", "host", "legacy")]
        [string]$Kind = "all"
    )

    $escapedConfig = [regex]::Escape($ResolvedConfigPath)
    try {
        $matches = @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
            $_.Name -match '^python(?:w)?\.exe$' `
                -and $_.CommandLine -match $escapedConfig `
                -and (
                    (Test-IsHostRunnerCommandLine -CommandLine $_.CommandLine) `
                    -or (Test-IsLegacyRunnerCommandLine -CommandLine $_.CommandLine)
                )
        } | ForEach-Object {
            $runnerKind = if (Test-IsHostRunnerCommandLine -CommandLine $_.CommandLine) {
                "host"
            } else {
                "legacy"
            }
            [pscustomobject]@{
                ProcessId       = $_.ProcessId
                ParentProcessId = $_.ParentProcessId
                Name            = $_.Name
                RunnerKind      = $runnerKind
                CommandLine     = $_.CommandLine
            }
        })
    } catch {
        return Get-RunnerProcessesFallback -Kind $Kind -PidFile $PidFile -HostStatePath $HostStatePath
    }

    if ($Kind -eq "host") {
        return @($matches | Where-Object { $_.RunnerKind -eq "host" })
    }
    if ($Kind -eq "legacy") {
        return @($matches | Where-Object { $_.RunnerKind -eq "legacy" })
    }
    return $matches
}

function Get-HostState {
    param(
        [Parameter(Mandatory = $true)]
        [string]$HostStatePath
    )

    if (-not (Test-Path $HostStatePath)) {
        return $null
    }

    try {
        return Get-Content -Encoding utf8 $HostStatePath | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Get-HostStatePid {
    param(
        [object]$HostState
    )

    if ($null -eq $HostState) {
        return $null
    }

    $pidText = ("" + $HostState.pid).Trim()
    if ($pidText -match '^\d+$') {
        return [int]$pidText
    }
    return $null
}

function Get-HostStateStatus {
    param(
        [object]$HostState
    )

    if ($null -eq $HostState) {
        return ""
    }

    return ("" + $HostState.status).Trim().ToLowerInvariant()
}

function Test-HostPidAlive {
    param(
        [int]$ProcessId
    )

    if ($ProcessId -le 0) {
        return $false
    }

    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Get-RunnerStateDiagnostics {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedProjectRoot,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedConfigPath,
        [string]$PidFile = "",
        [string]$HostStatePath = "",
        [string]$StdoutLog = "",
        [string]$StderrLog = ""
    )

    $lines = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($PidFile)) {
        $pidRecord = Get-PidRecord -PidFile $PidFile
        if ($null -eq $pidRecord) {
            $lines.Add("loop.pid: (missing or unreadable)")
        } else {
            $lines.Add("loop.pid launcher_pid=" + $pidRecord.LauncherProcessId + " host_pid=" + $pidRecord.HostProcessId)
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($HostStatePath)) {
        $hostState = Get-HostState -HostStatePath $HostStatePath
        if ($null -eq $hostState) {
            $lines.Add("host_state.json: (missing or unreadable)")
        } else {
            $hostPid = Get-HostStatePid -HostState $hostState
            $hostStatus = Get-HostStateStatus -HostState $hostState
            $hostAlive = if ($null -ne $hostPid) { Test-HostPidAlive -ProcessId $hostPid } else { $false }
            $lines.Add("host_state status=" + $hostStatus + " pid=" + $hostPid + " pid_alive=" + $hostAlive)
            if ($hostState.exit_reason) {
                $lines.Add("host_state exit_reason=" + $hostState.exit_reason)
            }
        }
    }

    $runnerProcesses = @(Get-RunnerProcesses -ResolvedProjectRoot $ResolvedProjectRoot -ResolvedConfigPath $ResolvedConfigPath -PidFile $PidFile -HostStatePath $HostStatePath -Kind all)
    if ($runnerProcesses) {
        $lines.Add("runner_processes:")
        foreach ($proc in ($runnerProcesses | Sort-Object ProcessId)) {
            $lines.Add("  - kind=" + $proc.RunnerKind + " pid=" + $proc.ProcessId + " name=" + $proc.Name + " cmd=" + $proc.CommandLine)
        }
    } else {
        $lines.Add("runner_processes: (none)")
    }

    if (-not [string]::IsNullOrWhiteSpace($StderrLog) -and (Test-Path $StderrLog)) {
        $stderrTail = @(Get-Content -Encoding utf8 -Tail 20 $StderrLog)
        if ($stderrTail.Count -gt 0) {
            $lines.Add("stderr tail:")
            foreach ($line in $stderrTail) {
                $lines.Add("  " + $line)
            }
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($StdoutLog) -and (Test-Path $StdoutLog)) {
        $stdoutTail = @(Get-Content -Encoding utf8 -Tail 10 $StdoutLog)
        if ($stdoutTail.Count -gt 0) {
            $lines.Add("stdout tail:")
            foreach ($line in $stdoutTail) {
                $lines.Add("  " + $line)
            }
        }
    }

    return ($lines -join [Environment]::NewLine)
}

function Get-RunnerDisplayProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [object[]]$Processes,
        [object]$HostState
    )

    if (-not $Processes) {
        return @()
    }

    $hostPid = Get-HostStatePid -HostState $HostState
    if ($null -eq $hostPid) {
        return @(
            $Processes |
                Sort-Object ProcessId |
                Select-Object RunnerKind, ProcessId, ParentProcessId, Name, CommandLine
        )
    }

    $hostProc = @($Processes | Where-Object { $_.RunnerKind -eq "host" -and $_.ProcessId -eq $hostPid } | Select-Object -First 1)
    if (-not $hostProc) {
        return @(
            $Processes |
                Sort-Object ProcessId |
                Select-Object RunnerKind, ProcessId, ParentProcessId, Name, CommandLine
        )
    }

    $launcherProc = @(
        $Processes |
            Where-Object { $_.RunnerKind -eq "host" -and $_.ProcessId -eq $hostProc.ParentProcessId } |
            Select-Object -First 1
    )
    if (-not $launcherProc) {
        return @(
            $Processes |
                Sort-Object ProcessId |
                Select-Object RunnerKind, ProcessId, ParentProcessId, Name, CommandLine
        )
    }

    $display = @(
        [pscustomobject]@{
            RunnerKind        = "host"
            ProcessId         = $hostProc.ProcessId
            ParentProcessId   = $hostProc.ParentProcessId
            LauncherProcessId = $launcherProc.ProcessId
            Name              = $hostProc.Name
            CommandLine       = $hostProc.CommandLine
        }
    )
    $remaining = @(
        $Processes |
            Where-Object { $_.ProcessId -notin @($hostProc.ProcessId, $launcherProc.ProcessId) } |
            Sort-Object ProcessId |
            Select-Object RunnerKind, ProcessId, ParentProcessId, Name, CommandLine
    )
    return @($display + $remaining)
}

function Stop-RunnerProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedProjectRoot,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedConfigPath,
        [Parameter(Mandatory = $true)]
        [string]$PidFile,
        [Parameter(Mandatory = $true)]
        [string]$HostStatePath
    )

    $pidRecord = Get-PidRecord -PidFile $PidFile
    if ($null -ne $pidRecord) {
        Stop-RunnerProcessId -ProcessId $pidRecord.LauncherProcessId
        Stop-RunnerProcessId -ProcessId $pidRecord.HostProcessId
    }

    $hostState = Get-HostState -HostStatePath $HostStatePath
    $hostPid = Get-HostStatePid -HostState $hostState
    Stop-RunnerProcessId -ProcessId $hostPid

    $procs = Get-RunnerProcesses -ResolvedProjectRoot $ResolvedProjectRoot -ResolvedConfigPath $ResolvedConfigPath -PidFile $PidFile -HostStatePath $HostStatePath -Kind all |
        Sort-Object ProcessId -Descending
    foreach ($proc in $procs) {
        Stop-RunnerProcessId -ProcessId $proc.ProcessId
    }
}

function Wait-ForRunnerState {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedProjectRoot,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedConfigPath,
        [string]$PidFile = "",
        [Parameter(Mandatory = $true)]
        [bool]$ShouldExist,
        [string]$HostStatePath = "",
        [int]$TimeoutSeconds = 15
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $exists = @(Get-RunnerProcesses -ResolvedProjectRoot $ResolvedProjectRoot -ResolvedConfigPath $ResolvedConfigPath -PidFile $PidFile -HostStatePath $HostStatePath -Kind all).Count -gt 0
        $hostPidAlive = $false
        if (-not [string]::IsNullOrWhiteSpace($HostStatePath)) {
            $hostState = Get-HostState -HostStatePath $HostStatePath
            $hostPid = Get-HostStatePid -HostState $hostState
            if ($null -ne $hostPid) {
                $hostPidAlive = Test-HostPidAlive -ProcessId $hostPid
            }
        }

        if ($ShouldExist) {
            if (-not [string]::IsNullOrWhiteSpace($HostStatePath)) {
                if ($hostPidAlive) {
                    return $true
                }
            } elseif ($exists) {
                return $true
            }
        } elseif ((-not $exists) -and (-not $hostPidAlive)) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Wait-ForStableHostState {
    param(
        [Parameter(Mandatory = $true)]
        [string]$HostStatePath,
        [int]$TimeoutSeconds = 20,
        [int]$StableSeconds = 3
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $stableSince = $null
    $stablePid = $null
    while ((Get-Date) -lt $deadline) {
        $hostState = Get-HostState -HostStatePath $HostStatePath
        $hostStatus = Get-HostStateStatus -HostState $hostState
        $hostPid = Get-HostStatePid -HostState $hostState
        $hostAlive = if ($null -ne $hostPid) { Test-HostPidAlive -ProcessId $hostPid } else { $false }

        if (($hostStatus -eq "failed") -or ($hostStatus -eq "stopped")) {
            return $false
        }

        if (($hostStatus -eq "running") -and $hostAlive) {
            if (($null -eq $stableSince) -or ($stablePid -ne $hostPid)) {
                $stableSince = Get-Date
                $stablePid = $hostPid
            }
            if (((Get-Date) - $stableSince).TotalSeconds -ge $StableSeconds) {
                return $true
            }
        } else {
            $stableSince = $null
            $stablePid = $null
        }
        Start-Sleep -Milliseconds 500
    }

    return $false
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
if (-not (Test-Path $resolvedConfigPath)) {
    throw "Config file not found: $resolvedConfigPath"
}

$pythonPath = Join-Path $resolvedProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonPath)) {
    throw "Python executable not found: $pythonPath"
}

$pidFile = Join-Path $resolvedRuntimeDir "loop.pid"
$stdoutLog = Join-Path $resolvedRuntimeDir "loop.stdout.log"
$stderrLog = Join-Path $resolvedRuntimeDir "loop.stderr.log"
$userFile = Join-Path $resolvedRuntimeDir "loop.user.txt"
$hostStatePath = Join-Path $resolvedRuntimeDir "host_state.json"
$scriptPath = [System.IO.Path]::GetFullPath($MyInvocation.MyCommand.Path)

if ($Action -eq "detach-restart") {
    $launcherPath = Start-DetachedRunnerRestart `
        -ScriptPath $scriptPath `
        -ResolvedConfigPath $resolvedConfigPath `
        -ResolvedRuntimeDir $resolvedRuntimeDir
    Write-Output ("Detached mail runner restart scheduled via launcher: " + $launcherPath)
    exit 0
}

if ($Action -in @("stop", "shutdown", "restart")) {
    Stop-RunnerProcesses -ResolvedProjectRoot $resolvedProjectRoot -ResolvedConfigPath $resolvedConfigPath -PidFile $pidFile -HostStatePath $hostStatePath
    if (-not (Wait-ForRunnerState -ResolvedProjectRoot $resolvedProjectRoot -ResolvedConfigPath $resolvedConfigPath -PidFile $pidFile -ShouldExist $false -HostStatePath $hostStatePath)) {
        $diagnostics = Get-RunnerStateDiagnostics `
            -ResolvedProjectRoot $resolvedProjectRoot `
            -ResolvedConfigPath $resolvedConfigPath `
            -PidFile $pidFile `
            -HostStatePath $hostStatePath `
            -StdoutLog $stdoutLog `
            -StderrLog $stderrLog
        throw ("Mail runner did not stop cleanly.`n" + $diagnostics)
    }
    Remove-Item -LiteralPath $pidFile -ErrorAction SilentlyContinue
    if ($Action -eq "stop") {
        Write-Output "Mail runner stopped."
        Show-SuccessPopup -Title "Mail runner stopped" -Message "The mail runner has stopped successfully."
        exit 0
    }
    if ($Action -eq "shutdown") {
        Write-Output "Mail runner stopped."
        Show-SuccessPopup -Title "Mail runner shut down" -Message "The mail runner has shut down successfully."
        exit 0
    }
}

if ($Action -eq "status") {
    $procs = Get-RunnerProcesses -ResolvedProjectRoot $resolvedProjectRoot -ResolvedConfigPath $resolvedConfigPath -PidFile $pidFile -HostStatePath $hostStatePath -Kind all
    $legacyProcs = @($procs | Where-Object { $_.RunnerKind -eq "legacy" })
    $hostState = Get-HostState -HostStatePath $hostStatePath
    $hostPid = Get-HostStatePid -HostState $hostState
    if ($hostState) {
        Write-Output "Host state:"
        $hostState | Format-List | Out-Host
        if (($null -ne $hostPid) -and (-not (Test-HostPidAlive -ProcessId $hostPid))) {
            Write-Warning "host_state.json is stale for this runtime_dir; the recorded host pid is no longer alive."
        }
    }
    if (-not $procs) {
        Write-Output "Mail runner is not running."
        exit 1
    }
    if ($legacyProcs) {
        Write-Warning "Legacy mail_runner.app --loop processes are still running for this config. Use restart/stop to clean them up."
    }
    (Get-RunnerDisplayProcesses -Processes $procs -HostState $hostState) |
        Select-Object RunnerKind, ProcessId, LauncherProcessId, ParentProcessId, Name, CommandLine |
        Format-List
    Show-SuccessPopup -Title "Mail runner status" -Message "The mail runner is currently running."
    exit 0
}

$existing = Get-RunnerProcesses -ResolvedProjectRoot $resolvedProjectRoot -ResolvedConfigPath $resolvedConfigPath -PidFile $pidFile -HostStatePath $hostStatePath -Kind all
if ($existing) {
    Write-Output "Mail runner is already running."
    if (@($existing | Where-Object { $_.RunnerKind -eq "legacy" }).Count -gt 0) {
        Write-Warning "A legacy mail_runner.app --loop process is already using this config. Run restart to migrate cleanly to mail_runner.host."
    }
    $existingHostState = Get-HostState -HostStatePath $hostStatePath
    (Get-RunnerDisplayProcesses -Processes $existing -HostState $existingHostState) |
        Select-Object RunnerKind, ProcessId, LauncherProcessId, ParentProcessId, Name, CommandLine |
        Format-List
    Show-SuccessPopup -Title "Mail runner already running" -Message "The mail runner is already running."
    exit 0
}

whoami | Set-Content -Path $userFile
Remove-Item -LiteralPath $stdoutLog -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $stderrLog -ErrorAction SilentlyContinue
$startedProcess = Start-Process `
    -FilePath $pythonPath `
    -ArgumentList @("-m", "mail_runner.host", "--config", $resolvedConfigPath, "--runtime-dir", $resolvedRuntimeDir) `
    -WorkingDirectory $resolvedProjectRoot `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru
Save-PidRecord -PidFile $pidFile -LauncherProcessId $startedProcess.Id

if (-not (Wait-ForRunnerState -ResolvedProjectRoot $resolvedProjectRoot -ResolvedConfigPath $resolvedConfigPath -PidFile $pidFile -ShouldExist $true -HostStatePath $hostStatePath)) {
    $diagnostics = Get-RunnerStateDiagnostics `
        -ResolvedProjectRoot $resolvedProjectRoot `
        -ResolvedConfigPath $resolvedConfigPath `
        -PidFile $pidFile `
        -HostStatePath $hostStatePath `
        -StdoutLog $stdoutLog `
        -StderrLog $stderrLog
    throw ("Mail runner did not start successfully.`n" + $diagnostics)
}
if (-not (Wait-ForStableHostState -HostStatePath $hostStatePath)) {
    $diagnostics = Get-RunnerStateDiagnostics `
        -ResolvedProjectRoot $resolvedProjectRoot `
        -ResolvedConfigPath $resolvedConfigPath `
        -PidFile $pidFile `
        -HostStatePath $hostStatePath `
        -StdoutLog $stdoutLog `
        -StderrLog $stderrLog
    throw ("Mail runner host did not remain alive long enough to be considered stable.`n" + $diagnostics)
}
$hostState = Get-HostState -HostStatePath $hostStatePath
$hostPid = Get-HostStatePid -HostState $hostState
$storedHostPid = if ($null -ne $hostPid) { $hostPid } else { 0 }
Save-PidRecord -PidFile $pidFile -LauncherProcessId $startedProcess.Id -HostProcessId $storedHostPid
$started = Get-RunnerProcesses -ResolvedProjectRoot $resolvedProjectRoot -ResolvedConfigPath $resolvedConfigPath -PidFile $pidFile -HostStatePath $hostStatePath -Kind all
$successLabel = if ($Action -eq "restart") { "restarted" } else { "started" }

Write-Output ("Mail runner " + $successLabel + " with config: " + $resolvedConfigPath)
if ($hostState) {
    Write-Output "Host state:"
    $hostState | Format-List
}
(Get-RunnerDisplayProcesses -Processes $started -HostState $hostState) |
    Select-Object RunnerKind, ProcessId, LauncherProcessId, ParentProcessId, Name, CommandLine |
    Format-List
Show-SuccessPopup -Title ("Mail runner " + $successLabel) -Message ("The mail runner has " + $successLabel + " successfully.`nConfig: " + $resolvedConfigPath)
