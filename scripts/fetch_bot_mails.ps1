param(
    [string]$ConfigPath = "",
    [string]$OutputPath = "",
    [int]$Count = 100,
    [switch]$NoPopup
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$resolvedProjectRoot = [System.IO.Path]::GetFullPath($projectRoot)
$runtimeDir = Join-Path $resolvedProjectRoot "_tmp_live_mail_runner"

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $preferred = Join-Path $runtimeDir "mail_config.loop_30s.yaml"
    $fallback = Join-Path $resolvedProjectRoot "mail_config.bot.local.yaml"
    if (Test-Path $preferred) {
        $ConfigPath = $preferred
    } else {
        $ConfigPath = $fallback
    }
}

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $OutputPath = Join-Path $runtimeDir "recent_bot_100_mails.json"
}

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "fetch_mails.ps1") `
    -ConfigPath $ConfigPath `
    -OutputPath $OutputPath `
    -Count $Count `
    -NoPopup:$NoPopup

exit $LASTEXITCODE
