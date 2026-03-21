param(
    [ValidateSet("status", "stop", "prune")]
    [string]$Action = "status",
    [string]$ProjectRoot = "",
    [string]$TaskRoot = "",
    [string]$ThreadId = "",
    [string]$TaskId = ""
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

function Get-OptionalIntValue {
    param([object]$Value)

    if ($null -eq $Value) {
        return $null
    }

    $text = ("" + $Value).Trim()
    if ($text -match '^\d+$') {
        return [int]$text
    }
    return $null
}

function Get-TrackedCodexRecords {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedTaskRoot
    )

    if (-not (Test-Path $ResolvedTaskRoot)) {
        return @()
    }

    $records = @()
    $files = @(Get-ChildItem -Path $ResolvedTaskRoot -Filter "codex_sidecar_process.json" -Recurse -File -ErrorAction SilentlyContinue)
    foreach ($file in $files) {
        try {
            $payload = Get-Content -Encoding utf8 -Raw $file.FullName | ConvertFrom-Json
        } catch {
            continue
        }

        $pid = Get-OptionalIntValue -Value $payload.pid
        if ($null -eq $pid -or $pid -le 0) {
            continue
        }

        $records += [pscustomobject]@{
            Path      = $file.FullName
            ProcessId = $pid
            ThreadId  = ("" + $payload.thread_id).Trim()
            TaskId    = ("" + $payload.task_id).Trim()
            StartedAt = ("" + $payload.started_at).Trim()
            RunDir    = ("" + $payload.run_dir).Trim()
            RepoPath  = ("" + $payload.repo_path).Trim()
            Workdir   = ("" + $payload.workdir).Trim()
            Adapter   = ("" + $payload.adapter).Trim()
            Command   = @($payload.command | ForEach-Object { "" + $_ })
        }
    }

    return @($records | Sort-Object ThreadId, TaskId, ProcessId)
}

function Get-CodexChildProcesses {
    param(
        [Parameter(Mandatory = $true)]
        [int]$ParentProcessId
    )

    try {
        return @(
            Get-CimInstance Win32_Process -ErrorAction Stop |
                Where-Object {
                    $_.ParentProcessId -eq $ParentProcessId -and $_.Name -match '^codex(?:\.exe)?$'
                }
        )
    } catch {
        return @()
    }
}

function Get-TrackedCodexStatus {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Record
    )

    $nodeProcess = Get-Process -Id $Record.ProcessId -ErrorAction SilentlyContinue
    $childProcesses = @(Get-CodexChildProcesses -ParentProcessId $Record.ProcessId)
    $state = if ($null -ne $nodeProcess) {
        "running"
    } elseif ($childProcesses.Count -gt 0) {
        "child_only"
    } else {
        "stale"
    }

    return [pscustomobject]@{
        ThreadId      = $Record.ThreadId
        TaskId        = $Record.TaskId
        ProcessId     = $Record.ProcessId
        ChildProcessIds = if ($childProcesses) { @($childProcesses | ForEach-Object { $_.ProcessId }) -join "," } else { "" }
        State         = $state
        StartedAt     = $Record.StartedAt
        RunDir        = $Record.RunDir
        Workdir       = $Record.Workdir
        RecordPath    = $Record.Path
    }
}

function Stop-TrackedCodexRecord {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Record
    )

    $nodeProcess = Get-Process -Id $Record.ProcessId -ErrorAction SilentlyContinue
    if ($null -ne $nodeProcess) {
        & taskkill /T /F /PID $Record.ProcessId *> $null
        Start-Sleep -Milliseconds 300
    } else {
        $childProcesses = @(Get-CodexChildProcesses -ParentProcessId $Record.ProcessId)
        foreach ($child in $childProcesses) {
            & taskkill /T /F /PID $child.ProcessId *> $null
        }
        if ($childProcesses.Count -gt 0) {
            Start-Sleep -Milliseconds 300
        }
    }
}

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$resolvedProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)

if ([string]::IsNullOrWhiteSpace($TaskRoot)) {
    $TaskRoot = Join-Path $resolvedProjectRoot "tasks"
}
$resolvedTaskRoot = Resolve-FullPath -BaseDir $resolvedProjectRoot -PathText $TaskRoot

$records = @(Get-TrackedCodexRecords -ResolvedTaskRoot $resolvedTaskRoot)
if (-not [string]::IsNullOrWhiteSpace($ThreadId)) {
    $records = @($records | Where-Object { $_.ThreadId -eq $ThreadId })
}
if (-not [string]::IsNullOrWhiteSpace($TaskId)) {
    $records = @($records | Where-Object { $_.TaskId -eq $TaskId })
}

if ($Action -eq "status") {
    $statuses = @($records | ForEach-Object { Get-TrackedCodexStatus -Record $_ })
    Write-Output ("Tracked Codex sidecar records: " + $statuses.Count)
    if (-not $statuses) {
        Write-Output "No tracked Codex sidecar records found."
        exit 0
    }
    $statuses | Format-Table ThreadId, TaskId, ProcessId, ChildProcessIds, State, StartedAt -AutoSize
    exit 0
}

if ($Action -eq "stop") {
    foreach ($record in $records) {
        Stop-TrackedCodexRecord -Record $record
        $status = Get-TrackedCodexStatus -Record $record
        if ($status.State -eq "stale") {
            Remove-Item -LiteralPath $record.Path -ErrorAction SilentlyContinue
        }
    }
    $remaining = @(Get-TrackedCodexRecords -ResolvedTaskRoot $resolvedTaskRoot | ForEach-Object { Get-TrackedCodexStatus -Record $_ })
    Write-Output ("Remaining tracked Codex sidecar records: " + $remaining.Count)
    if ($remaining) {
        $remaining | Format-Table ThreadId, TaskId, ProcessId, ChildProcessIds, State, StartedAt -AutoSize
    }
    exit 0
}

if ($Action -eq "prune") {
    $removed = 0
    foreach ($record in $records) {
        $status = Get-TrackedCodexStatus -Record $record
        if ($status.State -eq "stale") {
            Remove-Item -LiteralPath $record.Path -ErrorAction SilentlyContinue
            $removed += 1
        }
    }
    Write-Output ("Pruned stale tracked Codex sidecar records: " + $removed)
    exit 0
}
