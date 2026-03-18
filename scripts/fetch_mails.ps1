param(
    [string]$ConfigPath = "",
    [string]$OutputPath = "",
    [int]$Count = 100,
    [switch]$NoPopup
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

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$resolvedProjectRoot = [System.IO.Path]::GetFullPath($projectRoot)
$runtimeDir = Join-Path $resolvedProjectRoot "_tmp_live_mail_runner"
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $preferred = Join-Path $runtimeDir "mail_config.loop_30s.yaml"
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

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $runtimeDir "recent_100_mails.json"
}
$resolvedOutputPath = Resolve-FullPath -BaseDir $resolvedProjectRoot -PathText $OutputPath

$pythonPath = Join-Path $resolvedProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $pythonPath)) {
    throw "Python executable not found: $pythonPath"
}

$scriptPath = Join-Path $PSScriptRoot "fetch_mails.py"
if (-not (Test-Path $scriptPath)) {
    throw "Fetch script not found: $scriptPath"
}

if ($Count -le 0) {
    throw "Count must be greater than 0."
}

$output = & $pythonPath $scriptPath --config $resolvedConfigPath --output $resolvedOutputPath --count $Count --all 2>&1
$exitCode = $LASTEXITCODE
if ($output) {
    $output | Write-Output
}
if ($exitCode -ne 0) {
    throw "Mail fetch failed with exit code $exitCode."
}
if (-not (Test-Path $resolvedOutputPath)) {
    throw "Output file was not created: $resolvedOutputPath"
}

Show-SuccessPopup -Title "Mail fetch complete" -Message "Fetched the latest $Count mails.`nSaved to: $resolvedOutputPath"
