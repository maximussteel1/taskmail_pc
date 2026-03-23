param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,
    [Parameter(Mandatory = $true)]
    [string]$RuntimeDir,
    [string]$ProjectRoot = "",
    [switch]$StopTrackedSidecars
)

function Resolve-FullPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$BaseDir,
        [Parameter(Mandatory = $true)]
        [string]$PathText
    )

    if ([System.IO.Path]::IsPathRooted($PathText)) {
        return [System.IO.Path]::GetFullPath($PathText)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $BaseDir $PathText))
}

function Get-ConfigScalarValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ConfigFile,
        [Parameter(Mandatory = $true)]
        [string]$Key,
        [string]$DefaultValue = ""
    )

    try {
        $lines = Get-Content -Encoding utf8 $ConfigFile
    } catch {
        return $DefaultValue
    }

    foreach ($line in $lines) {
        if ($line -match ('^\s*' + [regex]::Escape($Key) + '\s*:\s*(.+?)\s*$')) {
            $value = $matches[1].Trim()
            if ($value -match '^(.*?)\s+#') {
                $value = $matches[1].Trim()
            }
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                return $value
            }
        }
    }

    return $DefaultValue
}

function Get-ThreadStateField {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RawText,
        [Parameter(Mandatory = $true)]
        [string]$FieldName
    )

    if ($RawText -match ('"' + [regex]::Escape($FieldName) + '"\s*:\s*"([^"]*)"')) {
        return $matches[1]
    }
    return ""
}

function Get-ThreadStateSnapshot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ThreadStatePath
    )

    try {
        $raw = Get-Content -Encoding utf8 -Raw $ThreadStatePath
    } catch {
        return $null
    }

    try {
        $payload = $raw | ConvertFrom-Json
        return [pscustomobject]@{
            ThreadId  = ("" + $payload.thread_id).Trim()
            Status    = ("" + $payload.status).Trim().ToLowerInvariant()
            TaskId    = ("" + $payload.current_task_id).Trim()
            RepoPath  = ("" + $payload.repo_path).Trim()
            Workdir   = ("" + $payload.workdir).Trim()
            Lifecycle = ("" + $payload.lifecycle).Trim().ToLowerInvariant()
            Path      = $ThreadStatePath
        }
    } catch {
        return [pscustomobject]@{
            ThreadId  = Get-ThreadStateField -RawText $raw -FieldName "thread_id"
            Status    = (Get-ThreadStateField -RawText $raw -FieldName "status").Trim().ToLowerInvariant()
            TaskId    = Get-ThreadStateField -RawText $raw -FieldName "current_task_id"
            RepoPath  = Get-ThreadStateField -RawText $raw -FieldName "repo_path"
            Workdir   = Get-ThreadStateField -RawText $raw -FieldName "workdir"
            Lifecycle = (Get-ThreadStateField -RawText $raw -FieldName "lifecycle").Trim().ToLowerInvariant()
            Path      = $ThreadStatePath
        }
    }
}

function Get-BlockingTaskStates {
    param(
        [Parameter(Mandatory = $true)]
        [string]$TaskRoot
    )

    if (-not (Test-Path $TaskRoot)) {
        return @()
    }

    $matches = @()
    $threadStateFiles = @(Get-ChildItem -Path $TaskRoot -Filter "thread_state.json" -Recurse -File -ErrorAction SilentlyContinue)
    foreach ($file in $threadStateFiles) {
        $snapshot = Get-ThreadStateSnapshot -ThreadStatePath $file.FullName
        if ($null -eq $snapshot) {
            continue
        }
        if ($snapshot.Status -in @("accepted", "running")) {
            $matches += $snapshot
        }
    }

    return @($matches | Sort-Object ThreadId, TaskId)
}

function Invoke-PowerShellFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $psArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath) + $Arguments
    $output = @(& powershell.exe @psArgs 2>&1)
    return [pscustomobject]@{
        ExitCode = $LASTEXITCODE
        Output   = @($output | ForEach-Object { "" + $_ })
    }
}

function Write-CommandOutput {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title,
        [Parameter(Mandatory = $true)]
        [object]$Result
    )

    Write-Output ("== " + $Title + " ==")
    if ($Result.Output.Count -eq 0) {
        Write-Output "(no output)"
        return
    }
    foreach ($line in $Result.Output) {
        Write-Output $line
    }
}

function Test-StatusShowsStopped {
    param(
        [Parameter(Mandatory = $true)]
        [object]$StatusResult
    )

    if ($StatusResult.ExitCode -ne 1) {
        return $false
    }
    foreach ($line in $StatusResult.Output) {
        if ($line -match 'Mail runner is not running\.') {
            return $true
        }
    }
    return $false
}

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
$resolvedProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
$resolvedConfigPath = Resolve-FullPath -BaseDir $resolvedProjectRoot -PathText $ConfigPath
$resolvedRuntimeDir = Resolve-FullPath -BaseDir $resolvedProjectRoot -PathText $RuntimeDir

if (-not (Test-Path $resolvedConfigPath)) {
    throw "Config file not found: $resolvedConfigPath"
}
if (-not (Test-Path $resolvedRuntimeDir)) {
    throw "Runtime dir not found: $resolvedRuntimeDir"
}

$taskRootValue = Get-ConfigScalarValue -ConfigFile $resolvedConfigPath -Key "task_root" -DefaultValue "tasks"
$configBaseDir = Split-Path -Parent $resolvedConfigPath
$resolvedTaskRoot = Resolve-FullPath -BaseDir $configBaseDir -PathText $taskRootValue

$manageScript = Join-Path $PSScriptRoot "manage_mail_runner.ps1"
$cleanupScript = Join-Path $PSScriptRoot "cleanup_project_codex.ps1"

Write-Output "Safe mail-runner shutdown preflight"
Write-Output ("Config Path : " + $resolvedConfigPath)
Write-Output ("Runtime Dir : " + $resolvedRuntimeDir)
Write-Output ("Task Root   : " + $resolvedTaskRoot)

$preStatus = Invoke-PowerShellFile -ScriptPath $manageScript -Arguments @(
    "status",
    "-ConfigPath", $resolvedConfigPath,
    "-RuntimeDir", $resolvedRuntimeDir,
    "-NoPopup"
)
Write-CommandOutput -Title "Pre-shutdown status" -Result $preStatus

$blockingStates = @(Get-BlockingTaskStates -TaskRoot $resolvedTaskRoot)
if ($blockingStates.Count -gt 0) {
    Write-Output "== Blocking task states =="
    $blockingStates |
        Select-Object ThreadId, Status, TaskId, RepoPath, Workdir, Lifecycle |
        Format-Table -AutoSize |
        Out-Host
    throw "Refusing shutdown because accepted/running thread_state.json entries still exist. Wait for completion or issue /kill first."
}

if (Test-StatusShowsStopped -StatusResult $preStatus) {
    Write-Output "Mail runner is already stopped for this config/runtime pair."
} else {
    $shutdownResult = Invoke-PowerShellFile -ScriptPath $manageScript -Arguments @(
        "shutdown",
        "-ConfigPath", $resolvedConfigPath,
        "-RuntimeDir", $resolvedRuntimeDir,
        "-NoPopup"
    )
    Write-CommandOutput -Title "Shutdown" -Result $shutdownResult
    if ($shutdownResult.ExitCode -ne 0) {
        throw "manage_mail_runner shutdown failed."
    }
}

$postStatus = Invoke-PowerShellFile -ScriptPath $manageScript -Arguments @(
    "status",
    "-ConfigPath", $resolvedConfigPath,
    "-RuntimeDir", $resolvedRuntimeDir,
    "-NoPopup"
)
Write-CommandOutput -Title "Post-shutdown status" -Result $postStatus
if (-not (Test-StatusShowsStopped -StatusResult $postStatus)) {
    throw "Post-shutdown verification failed: mail runner still appears to be running."
}

$sidecarStatus = Invoke-PowerShellFile -ScriptPath $cleanupScript -Arguments @(
    "status",
    "-TaskRoot", $resolvedTaskRoot
)
Write-CommandOutput -Title "Tracked Codex sidecars" -Result $sidecarStatus

if ($StopTrackedSidecars) {
    $sidecarStop = Invoke-PowerShellFile -ScriptPath $cleanupScript -Arguments @(
        "stop",
        "-TaskRoot", $resolvedTaskRoot
    )
    Write-CommandOutput -Title "Stop tracked Codex sidecars" -Result $sidecarStop
}

Write-Output "Shutdown handoff complete."
Write-Output ("If you need session continuity on another machine, sync this task root first: " + $resolvedTaskRoot)
