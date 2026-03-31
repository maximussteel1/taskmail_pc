param(
    [ValidateSet("start", "restart", "stop", "shutdown", "status", "detach-restart")]
    [string]$Action = "status",
    [string]$ConfigPath = "",
    [string]$ProjectRoot = "",
    [string]$RuntimeDir = "",
    [string]$RelaySyncHost = "",
    [string]$RelaySyncUser = "",
    [string]$RelaySyncKeyPath = "",
    [string]$RelaySyncRemoteTaskRoot = "",
    [double]$RelaySyncRepeatSeconds = 2.0,
    [switch]$DisableRelayTaskRootSync,
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

function Get-FirstNonEmptyText {
    param(
        [string[]]$Candidates
    )

    foreach ($candidate in $Candidates) {
        if (-not [string]::IsNullOrWhiteSpace($candidate)) {
            return $candidate.Trim()
        }
    }

    return ""
}

function Get-YamlScalarValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Key,
        [string]$DefaultValue = ""
    )

    if (-not (Test-Path $Path)) {
        return $DefaultValue
    }

    $pattern = '^\s*' + [regex]::Escape($Key) + '\s*:\s*(.*)$'
    foreach ($line in Get-Content -Encoding utf8 $Path) {
        if ($line -notmatch $pattern) {
            continue
        }

        $value = $Matches[1].Trim()
        if ([string]::IsNullOrWhiteSpace($value)) {
            return $DefaultValue
        }

        if (
            (($value.StartsWith("'")) -and ($value.EndsWith("'"))) `
                -or (($value.StartsWith('"')) -and ($value.EndsWith('"')))
        ) {
            return $value.Substring(1, $value.Length - 2)
        }

        $commentIndex = $value.IndexOf(" #")
        if ($commentIndex -ge 0) {
            $value = $value.Substring(0, $commentIndex).TrimEnd()
        }
        return $value
    }

    return $DefaultValue
}

function Get-UriHost {
    param(
        [string]$UriText
    )

    if ([string]::IsNullOrWhiteSpace($UriText)) {
        return ""
    }

    try {
        return ([System.Uri]$UriText).Host
    } catch {
        return ""
    }
}

function Get-RelayTaskRootSyncSettings {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedProjectRoot,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedConfigPath,
        [string]$RelaySyncHost = "",
        [string]$RelaySyncUser = "",
        [string]$RelaySyncKeyPath = "",
        [string]$RelaySyncRemoteTaskRoot = "",
        [double]$RelaySyncRepeatSeconds = 2.0,
        [switch]$DisableRelayTaskRootSync
    )

    $outboundTransport = (Get-YamlScalarValue -Path $ResolvedConfigPath -Key "outbound_transport" -DefaultValue "email").ToLowerInvariant()
    $relayUrl = Get-YamlScalarValue -Path $ResolvedConfigPath -Key "relay_url"
    $taskRootText = Get-YamlScalarValue -Path $ResolvedConfigPath -Key "task_root" -DefaultValue "tasks"
    $resolvedLocalTaskRoot = Resolve-FullPath -BaseDir $ResolvedProjectRoot -PathText $taskRootText
    $resolvedHost = Get-FirstNonEmptyText @(
        $RelaySyncHost,
        $env:MAIL_RUNNER_RELAY_SYNC_HOST,
        (Get-UriHost -UriText $relayUrl)
    )
    $resolvedUser = Get-FirstNonEmptyText @(
        $RelaySyncUser,
        $env:MAIL_RUNNER_RELAY_SYNC_USER,
        "ubuntu"
    )
    $resolvedRemoteTaskRoot = Get-FirstNonEmptyText @(
        $RelaySyncRemoteTaskRoot,
        $env:MAIL_RUNNER_RELAY_SYNC_REMOTE_TASK_ROOT,
        "/opt/mail_runner_relay/shared/task_root"
    )
    $resolvedKeyPathText = Get-FirstNonEmptyText @(
        $RelaySyncKeyPath,
        $env:MAIL_RUNNER_RELAY_SYNC_KEY_PATH,
        (Join-Path $ResolvedProjectRoot "work_bot.pem")
    )
    $resolvedKeyPath = if ([string]::IsNullOrWhiteSpace($resolvedKeyPathText)) {
        ""
    } else {
        Resolve-FullPath -BaseDir $ResolvedProjectRoot -PathText $resolvedKeyPathText
    }

    $reason = ""
    $enabled = $false
    if ($DisableRelayTaskRootSync) {
        $reason = "disabled by operator flag"
    } elseif ($RelaySyncRepeatSeconds -le 0) {
        $reason = "repeat-seconds must be greater than 0"
    } elseif ($outboundTransport -ne "relay") {
        $reason = "outbound_transport is not relay"
    } elseif ([string]::IsNullOrWhiteSpace($relayUrl)) {
        $reason = "relay_url is missing"
    } elseif ([string]::IsNullOrWhiteSpace($resolvedHost)) {
        $reason = "relay host could not be resolved from relay_url"
    } elseif ([string]::IsNullOrWhiteSpace($resolvedLocalTaskRoot)) {
        $reason = "task_root could not be resolved"
    } elseif ([string]::IsNullOrWhiteSpace($resolvedKeyPath) -or (-not (Test-Path $resolvedKeyPath))) {
        $reason = "relay sync SSH key is missing"
    } else {
        $enabled = $true
    }

    return [pscustomobject]@{
        Enabled        = $enabled
        Reason         = $reason
        RelayUrl       = $relayUrl
        LocalTaskRoot  = $resolvedLocalTaskRoot
        Host           = $resolvedHost
        User           = $resolvedUser
        KeyPath        = $resolvedKeyPath
        RemoteTaskRoot = $resolvedRemoteTaskRoot
        RepeatSeconds  = $RelaySyncRepeatSeconds
    }
}

function Get-ManagedProcessesFromPidFile {
    param(
        [string]$PidFile = "",
        [string]$RunnerKind = "managed"
    )

    if ([string]::IsNullOrWhiteSpace($PidFile)) {
        return @()
    }

    $pidRecord = Get-PidRecord -PidFile $PidFile
    if ($null -eq $pidRecord) {
        return @()
    }

    $records = @()
    foreach ($candidatePid in @($pidRecord.LauncherProcessId, $pidRecord.HostProcessId)) {
        $normalizedPid = Get-OptionalIntValue -Value $candidatePid
        if ($null -eq $normalizedPid) {
            continue
        }
        $record = New-RunnerProcessRecordFromPid -ProcessId $normalizedPid -RunnerKind $RunnerKind -CommandLine ("(" + $RunnerKind + " process from pid file)")
        if ($null -ne $record) {
            $records += $record
        }
    }

    return @($records | Sort-Object ProcessId -Unique)
}

function Stop-ManagedProcessesFromPidFile {
    param(
        [string]$PidFile = ""
    )

    if ([string]::IsNullOrWhiteSpace($PidFile)) {
        return
    }

    $pidRecord = Get-PidRecord -PidFile $PidFile
    if ($null -eq $pidRecord) {
        return
    }

    Stop-RunnerProcessId -ProcessId $pidRecord.LauncherProcessId
    Stop-RunnerProcessId -ProcessId $pidRecord.HostProcessId
}

function Wait-ForManagedProcessState {
    param(
        [string]$PidFile = "",
        [Parameter(Mandatory = $true)]
        [bool]$ShouldExist,
        [int]$TimeoutSeconds = 10
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $exists = @(Get-ManagedProcessesFromPidFile -PidFile $PidFile).Count -gt 0
        if ($ShouldExist -and $exists) {
            return $true
        }
        if ((-not $ShouldExist) -and (-not $exists)) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }

    return $false
}

function Wait-ForProcessStable {
    param(
        [int]$ProcessId,
        [int]$TimeoutSeconds = 10,
        [int]$StableSeconds = 2
    )

    if ($ProcessId -le 0) {
        return $false
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $stableSince = $null
    while ((Get-Date) -lt $deadline) {
        if ($null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            if ($null -eq $stableSince) {
                $stableSince = Get-Date
            }
            if (((Get-Date) - $stableSince).TotalSeconds -ge $StableSeconds) {
                return $true
            }
        } else {
            $stableSince = $null
        }
        Start-Sleep -Milliseconds 500
    }

    return $false
}

function Get-ManagedProcessDiagnostics {
    param(
        [string]$PidFile = "",
        [string]$StdoutLog = "",
        [string]$StderrLog = ""
    )

    $lines = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($PidFile)) {
        $pidRecord = Get-PidRecord -PidFile $PidFile
        if ($null -eq $pidRecord) {
            $lines.Add("pid file: (missing or unreadable)")
        } else {
            $lines.Add("pid file launcher_pid=" + $pidRecord.LauncherProcessId + " host_pid=" + $pidRecord.HostProcessId)
        }
    }

    $processes = @(Get-ManagedProcessesFromPidFile -PidFile $PidFile -RunnerKind "relay-sync")
    if ($processes) {
        $lines.Add("managed_processes:")
        foreach ($proc in ($processes | Sort-Object ProcessId)) {
            $lines.Add("  - kind=" + $proc.RunnerKind + " pid=" + $proc.ProcessId + " name=" + $proc.Name + " cmd=" + $proc.CommandLine)
        }
    } else {
        $lines.Add("managed_processes: (none)")
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
        $stdoutTail = @(Get-Content -Encoding utf8 -Tail 20 $StdoutLog)
        if ($stdoutTail.Count -gt 0) {
            $lines.Add("stdout tail:")
            foreach ($line in $stdoutTail) {
                $lines.Add("  " + $line)
            }
        }
    }

    return ($lines -join [Environment]::NewLine)
}

function Quote-PowerShellLiteral {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    return "'" + $Value.Replace("'", "''") + "'"
}

function Convert-ToEncodedPowerShellCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CommandText
    )

    return [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($CommandText))
}

function Get-DetachedLauncherTaskName {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedRuntimeDir,
        [Parameter(Mandatory = $true)]
        [string]$Purpose
    )

    $normalized = ($Purpose.Trim().ToLowerInvariant() + "|" + $ResolvedRuntimeDir.Trim().ToLowerInvariant())
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($normalized)
        $hashBytes = $sha.ComputeHash($bytes)
    } finally {
        $sha.Dispose()
    }
    $hash = ([BitConverter]::ToString($hashBytes)).Replace("-", "").Substring(0, 12)
    return ("MailRunner_" + $Purpose + "_" + $hash)
}

function Start-DetachedPowerShellViaStartProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PowerShellCommand
    )

    $encodedCommand = Convert-ToEncodedPowerShellCommand -CommandText $PowerShellCommand
    Start-Process `
        -FilePath "powershell.exe" `
        -WindowStyle Hidden `
        -ArgumentList @(
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", $encodedCommand
        ) | Out-Null
    return "start-process"
}

function Start-ScheduledTaskPowerShellCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TaskName,
        [Parameter(Mandatory = $true)]
        [string]$PowerShellCommand
    )

    $encodedCommand = Convert-ToEncodedPowerShellCommand -CommandText $PowerShellCommand
    $currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument ("-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -EncodedCommand " + $encodedCommand)
    $trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(5))
    $principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType InteractiveToken -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    return ("scheduled-task=" + $TaskName)
}

function Start-DetachedPowerShellCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedRuntimeDir,
        [Parameter(Mandatory = $true)]
        [string]$Purpose,
        [Parameter(Mandatory = $true)]
        [string]$PowerShellCommand
    )

    try {
        return Start-DetachedPowerShellViaStartProcess -PowerShellCommand $PowerShellCommand
    } catch {
        Write-Warning ("Start-Process launcher failed for " + $Purpose + " (" + $ResolvedRuntimeDir + "). Falling back to cmd /c start. error=" + $_.Exception.Message)
    }
    $encodedCommand = Convert-ToEncodedPowerShellCommand -CommandText $PowerShellCommand
    & cmd.exe /c start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $encodedCommand | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Detached PowerShell launcher failed with exit code $LASTEXITCODE."
    }
    return "cmd-start"
}

function Start-DetachedRunnerHost {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedProjectRoot,
        [Parameter(Mandatory = $true)]
        [string]$PythonPath,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedConfigPath,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedRuntimeDir,
        [Parameter(Mandatory = $true)]
        [string]$StdoutLog,
        [Parameter(Mandatory = $true)]
        [string]$StderrLog
    )

    $hostCmdLine = (
        '"' + $PythonPath + '"' +
        ' -m mail_runner.host --config "' + $ResolvedConfigPath + '"' +
        ' --runtime-dir "' + $ResolvedRuntimeDir + '"' +
        ' 1>>"' + $StdoutLog + '"' +
        ' 2>>"' + $StderrLog + '"'
    )
    $launcherCommand = (
        '$ErrorActionPreference = ''Stop''; ' +
        'Set-Location -LiteralPath ' + (Quote-PowerShellLiteral -Value $ResolvedProjectRoot) + '; ' +
        '& cmd.exe /d /c ' + (Quote-PowerShellLiteral -Value $hostCmdLine)
    )
    return Start-DetachedPowerShellCommand `
        -ResolvedRuntimeDir $ResolvedRuntimeDir `
        -Purpose "Host" `
        -PowerShellCommand $launcherCommand
}

function Start-DetachedRunnerRestart {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedProjectRoot,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedConfigPath,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedRuntimeDir,
        [string]$RelaySyncHost = "",
        [string]$RelaySyncUser = "",
        [string]$RelaySyncKeyPath = "",
        [string]$RelaySyncRemoteTaskRoot = "",
        [double]$RelaySyncRepeatSeconds = 2.0,
        [switch]$DisableRelayTaskRootSync,
        [switch]$NoPopup
    )

    $restartArguments = New-Object System.Collections.Generic.List[string]
    $restartArguments.Add("restart")
    $restartArguments.Add("-ProjectRoot " + (Quote-PowerShellLiteral -Value $ResolvedProjectRoot))
    $restartArguments.Add("-ConfigPath " + (Quote-PowerShellLiteral -Value $ResolvedConfigPath))
    $restartArguments.Add("-RuntimeDir " + (Quote-PowerShellLiteral -Value $ResolvedRuntimeDir))
    $restartArguments.Add(
        "-RelaySyncRepeatSeconds " + (
            Quote-PowerShellLiteral -Value $RelaySyncRepeatSeconds.ToString([System.Globalization.CultureInfo]::InvariantCulture)
        )
    )
    if (-not [string]::IsNullOrWhiteSpace($RelaySyncHost)) {
        $restartArguments.Add("-RelaySyncHost " + (Quote-PowerShellLiteral -Value $RelaySyncHost))
    }
    if (-not [string]::IsNullOrWhiteSpace($RelaySyncUser)) {
        $restartArguments.Add("-RelaySyncUser " + (Quote-PowerShellLiteral -Value $RelaySyncUser))
    }
    if (-not [string]::IsNullOrWhiteSpace($RelaySyncKeyPath)) {
        $restartArguments.Add("-RelaySyncKeyPath " + (Quote-PowerShellLiteral -Value $RelaySyncKeyPath))
    }
    if (-not [string]::IsNullOrWhiteSpace($RelaySyncRemoteTaskRoot)) {
        $restartArguments.Add("-RelaySyncRemoteTaskRoot " + (Quote-PowerShellLiteral -Value $RelaySyncRemoteTaskRoot))
    }
    if ($DisableRelayTaskRootSync) {
        $restartArguments.Add("-DisableRelayTaskRootSync")
    }
    if ($NoPopup) {
        $restartArguments.Add("-NoPopup")
    }
    $restartCommand = "Start-Sleep -Seconds 2; & " + (Quote-PowerShellLiteral -Value $ScriptPath) + " " + ($restartArguments -join " ")
    return Start-DetachedPowerShellCommand `
        -ResolvedRuntimeDir $ResolvedRuntimeDir `
        -Purpose "Restart" `
        -PowerShellCommand $restartCommand
}

function Start-RelayTaskRootSync {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedProjectRoot,
        [Parameter(Mandatory = $true)]
        [string]$PythonPath,
        [Parameter(Mandatory = $true)]
        [object]$SyncSettings,
        [Parameter(Mandatory = $true)]
        [string]$PidFile,
        [Parameter(Mandatory = $true)]
        [string]$StdoutLog,
        [Parameter(Mandatory = $true)]
        [string]$StderrLog
    )

    if (-not $SyncSettings.Enabled) {
        throw "Relay task-root sync is not enabled for this config."
    }

    $syncScriptPath = Join-Path $ResolvedProjectRoot "scripts\sync_relay_task_root.py"
    if (-not (Test-Path $syncScriptPath)) {
        throw "Relay task-root sync script not found: $syncScriptPath"
    }

    New-Item -ItemType Directory -Force -Path $SyncSettings.LocalTaskRoot | Out-Null
    Remove-Item -LiteralPath $StdoutLog -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $StderrLog -ErrorAction SilentlyContinue

    $process = Start-Process `
        -FilePath $PythonPath `
        -WorkingDirectory $ResolvedProjectRoot `
        -ArgumentList @(
            "-u",
            $syncScriptPath,
            "--host", $SyncSettings.Host,
            "--user", $SyncSettings.User,
            "--key-path", $SyncSettings.KeyPath,
            "--local-task-root", $SyncSettings.LocalTaskRoot,
            "--remote-task-root", $SyncSettings.RemoteTaskRoot,
            "--repeat-seconds", $SyncSettings.RepeatSeconds.ToString([System.Globalization.CultureInfo]::InvariantCulture)
        ) `
        -RedirectStandardOutput $StdoutLog `
        -RedirectStandardError $StderrLog `
        -WindowStyle Hidden `
        -PassThru
    Save-PidRecord -PidFile $PidFile -HostProcessId $process.Id
    return $process
}

function Stop-RelayTaskRootSync {
    param(
        [string]$PidFile = "",
        [string]$StdoutLog = "",
        [string]$StderrLog = "",
        [int]$TimeoutSeconds = 10
    )

    Stop-ManagedProcessesFromPidFile -PidFile $PidFile
    if (-not (Wait-ForManagedProcessState -PidFile $PidFile -ShouldExist $false -TimeoutSeconds $TimeoutSeconds)) {
        $diagnostics = Get-ManagedProcessDiagnostics -PidFile $PidFile -StdoutLog $StdoutLog -StderrLog $StderrLog
        throw ("Relay task-root sync did not stop cleanly.`n" + $diagnostics)
    }
    Remove-Item -LiteralPath $PidFile -ErrorAction SilentlyContinue
}

function Get-RelayTaskRootSyncStatusObject {
    param(
        [Parameter(Mandatory = $true)]
        [object]$SyncSettings,
        [Parameter(Mandatory = $true)]
        [string]$PidFile,
        [Parameter(Mandatory = $true)]
        [string]$StdoutLog,
        [Parameter(Mandatory = $true)]
        [string]$StderrLog
    )

    $processes = @(Get-ManagedProcessesFromPidFile -PidFile $PidFile -RunnerKind "relay-sync")
    return [pscustomobject]@{
        enabled          = $SyncSettings.Enabled
        reason           = $SyncSettings.Reason
        relay_url        = $SyncSettings.RelayUrl
        local_task_root  = $SyncSettings.LocalTaskRoot
        host             = $SyncSettings.Host
        user             = $SyncSettings.User
        remote_task_root = $SyncSettings.RemoteTaskRoot
        repeat_seconds   = $SyncSettings.RepeatSeconds
        pid_file         = $PidFile
        stdout_log       = $StdoutLog
        stderr_log       = $StderrLog
        running          = $processes.Count -gt 0
        managed_pids     = if ($processes) { (($processes | Sort-Object ProcessId | ForEach-Object { $_.ProcessId }) -join ",") } else { "" }
    }
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

function Get-RunnerProcessesFromMetadata {
    param(
        [ValidateSet("all", "host", "legacy")]
        [string]$Kind = "all",
        [string]$PidFile = "",
        [string]$HostStatePath = ""
    )

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

function Get-RunnerProcessesFallback {
    param(
        [ValidateSet("all", "host", "legacy")]
        [string]$Kind = "all",
        [string]$PidFile = "",
        [string]$HostStatePath = ""
    )

    Write-RunnerProcessDiscoveryFallbackWarning
    return Get-RunnerProcessesFromMetadata -Kind $Kind -PidFile $PidFile -HostStatePath $HostStatePath
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

    $metadataExists = (
        ((-not [string]::IsNullOrWhiteSpace($PidFile)) -and (Test-Path $PidFile)) `
        -or ((-not [string]::IsNullOrWhiteSpace($HostStatePath)) -and (Test-Path $HostStatePath))
    )
    $metadataMatches = @(
        Get-RunnerProcessesFromMetadata -Kind $Kind -PidFile $PidFile -HostStatePath $HostStatePath
    )
    if ($metadataExists) {
        return $metadataMatches
    }

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

function Get-ObserveRunningSessions {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonPath,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedConfigPath,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedRuntimeDir
    )

    $observeOutput = & $PythonPath `
        -m mail_runner.observe `
        --config $ResolvedConfigPath `
        --runtime-dir $ResolvedRuntimeDir `
        list-running 2>&1
    $observeExitCode = $LASTEXITCODE
    if ($observeExitCode -ne 0) {
        $detail = if ($observeOutput) { ($observeOutput -join [Environment]::NewLine) } else { "(no output)" }
        throw ("Unable to inspect running sessions before restart. observe exit=" + $observeExitCode + [Environment]::NewLine + $detail)
    }

    $runningLines = New-Object System.Collections.Generic.List[string]
    foreach ($item in @($observeOutput)) {
        $text = ("" + $item).Trim()
        if ([string]::IsNullOrWhiteSpace($text) -or $text -eq "(none)") {
            continue
        }
        $runningLines.Add($text)
    }
    return @($runningLines)
}

function Assert-NoRunningSessionsForDirectRestart {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonPath,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedConfigPath,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedRuntimeDir
    )

    $runningSessions = @(Get-ObserveRunningSessions `
        -PythonPath $PythonPath `
        -ResolvedConfigPath $ResolvedConfigPath `
        -ResolvedRuntimeDir $ResolvedRuntimeDir)
    if ($runningSessions.Count -le 0) {
        return
    }

    throw (
        "Refusing to directly restart the mail runner while running sessions are active. " +
        "Wait for them to finish or stop them explicitly before retrying." +
        [Environment]::NewLine +
        ($runningSessions -join [Environment]::NewLine)
    )
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
    $preferredRelay = Join-Path $resolvedProjectRoot "mail_config.bot.relay.local.yaml"
    $preferred = Join-Path $resolvedRuntimeDir "mail_config.loop_30s.yaml"
    $fallbackBot = Join-Path $resolvedProjectRoot "mail_config.bot.local.yaml"
    $fallbackUser = Join-Path $resolvedProjectRoot "mail_config.local.yaml"
    if (Test-Path $preferredRelay) {
        $ConfigPath = $preferredRelay
    } elseif (Test-Path $preferred) {
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
$relayTaskRootSyncPidFile = Join-Path $resolvedRuntimeDir "relay_task_root_sync.pid"
$relayTaskRootSyncStdoutLog = Join-Path $resolvedRuntimeDir "relay_task_root_sync.stdout.log"
$relayTaskRootSyncStderrLog = Join-Path $resolvedRuntimeDir "relay_task_root_sync.stderr.log"
$relayTaskRootSyncSettings = Get-RelayTaskRootSyncSettings `
    -ResolvedProjectRoot $resolvedProjectRoot `
    -ResolvedConfigPath $resolvedConfigPath `
    -RelaySyncHost $RelaySyncHost `
    -RelaySyncUser $RelaySyncUser `
    -RelaySyncKeyPath $RelaySyncKeyPath `
    -RelaySyncRemoteTaskRoot $RelaySyncRemoteTaskRoot `
    -RelaySyncRepeatSeconds $RelaySyncRepeatSeconds `
    -DisableRelayTaskRootSync:$DisableRelayTaskRootSync
$scriptPath = [System.IO.Path]::GetFullPath($MyInvocation.MyCommand.Path)
$existingRunnerProcesses = @(
    Get-RunnerProcesses `
        -ResolvedProjectRoot $resolvedProjectRoot `
        -ResolvedConfigPath $resolvedConfigPath `
        -PidFile $pidFile `
        -HostStatePath $hostStatePath `
        -Kind all
)

if (($Action -in @("restart", "detach-restart")) -and $existingRunnerProcesses) {
    Assert-NoRunningSessionsForDirectRestart `
        -PythonPath $pythonPath `
        -ResolvedConfigPath $resolvedConfigPath `
        -ResolvedRuntimeDir $resolvedRuntimeDir
}

if ($Action -eq "detach-restart") {
    $launcherPath = Start-DetachedRunnerRestart `
        -ScriptPath $scriptPath `
        -ResolvedProjectRoot $resolvedProjectRoot `
        -ResolvedConfigPath $resolvedConfigPath `
        -ResolvedRuntimeDir $resolvedRuntimeDir `
        -RelaySyncHost $RelaySyncHost `
        -RelaySyncUser $RelaySyncUser `
        -RelaySyncKeyPath $RelaySyncKeyPath `
        -RelaySyncRemoteTaskRoot $RelaySyncRemoteTaskRoot `
        -RelaySyncRepeatSeconds $RelaySyncRepeatSeconds `
        -DisableRelayTaskRootSync:$DisableRelayTaskRootSync `
        -NoPopup:$NoPopup
    Write-Output ("Detached mail runner restart scheduled via launcher: " + $launcherPath)
    exit 0
}

if ($Action -in @("stop", "shutdown", "restart")) {
    Stop-RelayTaskRootSync `
        -PidFile $relayTaskRootSyncPidFile `
        -StdoutLog $relayTaskRootSyncStdoutLog `
        -StderrLog $relayTaskRootSyncStderrLog
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
    $procs = $existingRunnerProcesses
    $legacyProcs = @($procs | Where-Object { $_.RunnerKind -eq "legacy" })
    $hostState = Get-HostState -HostStatePath $hostStatePath
    $hostPid = Get-HostStatePid -HostState $hostState
    $syncStatus = Get-RelayTaskRootSyncStatusObject `
        -SyncSettings $relayTaskRootSyncSettings `
        -PidFile $relayTaskRootSyncPidFile `
        -StdoutLog $relayTaskRootSyncStdoutLog `
        -StderrLog $relayTaskRootSyncStderrLog
    if ($hostState) {
        Write-Output "Host state:"
        $hostState | Format-List | Out-Host
        if (($null -ne $hostPid) -and (-not (Test-HostPidAlive -ProcessId $hostPid))) {
            Write-Warning "host_state.json is stale for this runtime_dir; the recorded host pid is no longer alive."
        }
    }
    Write-Output "Relay task-root sync:"
    $syncStatus | Format-List | Out-Host
    if ($relayTaskRootSyncSettings.Enabled -and (-not $syncStatus.running)) {
        Write-Warning "Relay task-root sync is enabled for this relay config but the companion process is not running. Current-session direct actions may fail when the VPS task_root snapshot lags."
        $syncDiagnostics = Get-ManagedProcessDiagnostics `
            -PidFile $relayTaskRootSyncPidFile `
            -StdoutLog $relayTaskRootSyncStdoutLog `
            -StderrLog $relayTaskRootSyncStderrLog
        if (-not [string]::IsNullOrWhiteSpace($syncDiagnostics)) {
            Write-Output $syncDiagnostics
        }
    } elseif ((-not $relayTaskRootSyncSettings.Enabled) -and $syncStatus.running) {
        Write-Warning "Relay task-root sync companion is still running even though sync is disabled for this config."
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
$existingSyncProcesses = @(Get-ManagedProcessesFromPidFile -PidFile $relayTaskRootSyncPidFile -RunnerKind "relay-sync")
if ($existing) {
    Write-Output "Mail runner is already running."
    if (@($existing | Where-Object { $_.RunnerKind -eq "legacy" }).Count -gt 0) {
        Write-Warning "A legacy mail_runner.app --loop process is already using this config. Run restart to migrate cleanly to mail_runner.host."
    }
    if ($relayTaskRootSyncSettings.Enabled -and (-not $existingSyncProcesses)) {
        $syncProcess = Start-RelayTaskRootSync `
            -ResolvedProjectRoot $resolvedProjectRoot `
            -PythonPath $pythonPath `
            -SyncSettings $relayTaskRootSyncSettings `
            -PidFile $relayTaskRootSyncPidFile `
            -StdoutLog $relayTaskRootSyncStdoutLog `
            -StderrLog $relayTaskRootSyncStderrLog
        if (-not (Wait-ForProcessStable -ProcessId $syncProcess.Id)) {
            $diagnostics = Get-ManagedProcessDiagnostics `
                -PidFile $relayTaskRootSyncPidFile `
                -StdoutLog $relayTaskRootSyncStdoutLog `
                -StderrLog $relayTaskRootSyncStderrLog
            throw ("Relay task-root sync did not remain alive long enough to be considered stable.`n" + $diagnostics)
        }
        Write-Output "Relay task-root sync companion started."
    } elseif ((-not $relayTaskRootSyncSettings.Enabled) -and $existingSyncProcesses) {
        Write-Warning "Relay task-root sync companion is still running even though sync is disabled for this config."
    }
    $existingHostState = Get-HostState -HostStatePath $hostStatePath
    $existingSyncStatus = Get-RelayTaskRootSyncStatusObject `
        -SyncSettings $relayTaskRootSyncSettings `
        -PidFile $relayTaskRootSyncPidFile `
        -StdoutLog $relayTaskRootSyncStdoutLog `
        -StderrLog $relayTaskRootSyncStderrLog
    Write-Output "Relay task-root sync:"
    $existingSyncStatus | Format-List | Out-Host
    (Get-RunnerDisplayProcesses -Processes $existing -HostState $existingHostState) |
        Select-Object RunnerKind, ProcessId, LauncherProcessId, ParentProcessId, Name, CommandLine |
        Format-List
    Show-SuccessPopup -Title "Mail runner already running" -Message "The mail runner is already running."
    exit 0
}

Stop-RelayTaskRootSync `
    -PidFile $relayTaskRootSyncPidFile `
    -StdoutLog $relayTaskRootSyncStdoutLog `
    -StderrLog $relayTaskRootSyncStderrLog
whoami | Set-Content -Path $userFile
Remove-Item -LiteralPath $stdoutLog -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $stderrLog -ErrorAction SilentlyContinue
$launcherPath = Start-DetachedRunnerHost `
    -ResolvedProjectRoot $resolvedProjectRoot `
    -PythonPath $pythonPath `
    -ResolvedConfigPath $resolvedConfigPath `
    -ResolvedRuntimeDir $resolvedRuntimeDir `
    -StdoutLog $stdoutLog `
    -StderrLog $stderrLog
Save-PidRecord -PidFile $pidFile

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
Save-PidRecord -PidFile $pidFile -HostProcessId $storedHostPid
$syncStatus = $null
if ($relayTaskRootSyncSettings.Enabled) {
    $syncProcess = Start-RelayTaskRootSync `
        -ResolvedProjectRoot $resolvedProjectRoot `
        -PythonPath $pythonPath `
        -SyncSettings $relayTaskRootSyncSettings `
        -PidFile $relayTaskRootSyncPidFile `
        -StdoutLog $relayTaskRootSyncStdoutLog `
        -StderrLog $relayTaskRootSyncStderrLog
    if (-not (Wait-ForProcessStable -ProcessId $syncProcess.Id)) {
        $diagnostics = Get-ManagedProcessDiagnostics `
            -PidFile $relayTaskRootSyncPidFile `
            -StdoutLog $relayTaskRootSyncStdoutLog `
            -StderrLog $relayTaskRootSyncStderrLog
        throw ("Relay task-root sync did not remain alive long enough to be considered stable.`n" + $diagnostics)
    }
}
$syncStatus = Get-RelayTaskRootSyncStatusObject `
    -SyncSettings $relayTaskRootSyncSettings `
    -PidFile $relayTaskRootSyncPidFile `
    -StdoutLog $relayTaskRootSyncStdoutLog `
    -StderrLog $relayTaskRootSyncStderrLog
$started = Get-RunnerProcesses -ResolvedProjectRoot $resolvedProjectRoot -ResolvedConfigPath $resolvedConfigPath -PidFile $pidFile -HostStatePath $hostStatePath -Kind all
$successLabel = if ($Action -eq "restart") { "restarted" } else { "started" }

Write-Output ("Mail runner " + $successLabel + " with config: " + $resolvedConfigPath)
Write-Output ("Detached launcher: " + $launcherPath)
if ($hostState) {
    Write-Output "Host state:"
    $hostState | Format-List
}
Write-Output "Relay task-root sync:"
$syncStatus | Format-List
(Get-RunnerDisplayProcesses -Processes $started -HostState $hostState) |
    Select-Object RunnerKind, ProcessId, LauncherProcessId, ParentProcessId, Name, CommandLine |
    Format-List
Show-SuccessPopup -Title ("Mail runner " + $successLabel) -Message ("The mail runner has " + $successLabel + " successfully.`nConfig: " + $resolvedConfigPath)
