param(
  [string]$TaskName = "Milana Catalog Excel Update",
  [string]$At = "07:00",
  [ValidateSet("website", "local", "auto")]
  [string]$Source = "website",
  [ValidateSet("supabase", "excel", "both")]
  [string]$Destination = "supabase",
  [string]$Grid = "auto",
  [string]$OcrLang = "eng+rus+uzb",
  [switch]$Force,
  [switch]$EnableMlEmbeddings
)

$ErrorActionPreference = "Stop"

$Runner = Join-Path $PSScriptRoot "run_daily_processor.ps1"
if (!(Test-Path $Runner)) {
  throw "Could not find runner script: $Runner"
}

$ArgumentParts = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", "`"$Runner`"",
  "-Source", $Source,
  "-Destination", $Destination,
  "-Grid", $Grid,
  "-OcrLang", $OcrLang
)

if ($Force) {
  $ArgumentParts += "-Force"
}

if ($EnableMlEmbeddings) {
  $ArgumentParts += "-EnableMlEmbeddings"
}

$Action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument ($ArgumentParts -join " ") `
  -WorkingDirectory $PSScriptRoot

$Trigger = New-ScheduledTaskTrigger -Daily -At $At
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $Action `
  -Trigger $Trigger `
  -Settings $Settings `
  -Description "Downloads current Milana Google Drive catalogs and refreshes the configured product destination." `
  -Force | Out-Null

Write-Host "Scheduled task installed: $TaskName"
Write-Host "Daily time: $At"
Write-Host "Runner: $Runner"
