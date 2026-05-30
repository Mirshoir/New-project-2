param(
  [ValidateSet("website", "local", "auto")]
  [string]$Source = "website",
  [ValidateSet("supabase", "excel", "both")]
  [string]$Destination = "supabase",
  [string]$WebsiteIndex = "$PSScriptRoot\index.html",
  [string]$InputDir = "$PSScriptRoot\incoming_pdfs",
  [string]$OutputDir = "$PSScriptRoot\outputs\catalog_processing",
  [int]$ExpectedCount = 4,
  [string]$Grid = "auto",
  [string]$OcrLang = "eng+rus+uzb",
  [string]$SupabaseUrl = $env:SUPABASE_URL,
  [string]$SupabaseKey = $env:SUPABASE_SERVICE_ROLE_KEY,
  [string]$SupabaseTable = $(if ($env:SUPABASE_PRODUCTS_TABLE) { $env:SUPABASE_PRODUCTS_TABLE } else { "milana_products" }),
  [string]$SupabaseOverridesTable = $(if ($env:SUPABASE_OVERRIDES_TABLE) { $env:SUPABASE_OVERRIDES_TABLE } else { "milana_product_overrides" }),
  [string]$SupabaseImageBucket = $(if ($env:SUPABASE_IMAGE_BUCKET) { $env:SUPABASE_IMAGE_BUCKET } else { "product-images" }),
  [string]$SupabaseImagePrefix = $(if ($env:SUPABASE_IMAGE_PREFIX) { $env:SUPABASE_IMAGE_PREFIX } else { "milana/latest" }),
  [switch]$Force,
  [switch]$DownloadOnly,
  [switch]$EnableMlEmbeddings
)

$ErrorActionPreference = "Stop"

$VenvDir = Join-Path $PSScriptRoot ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"

if (!(Test-Path $PythonExe)) {
  python -m venv $VenvDir
}

& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")

if ($EnableMlEmbeddings) {
  & $PythonExe -m pip install -r (Join-Path $PSScriptRoot "requirements-ml.txt")
}

$ArgsList = @(
  "-m", "catalog_processor",
  "--source", $Source,
  "--destination", $Destination,
  "--website-index", $WebsiteIndex,
  "--input", $InputDir,
  "--output", $OutputDir,
  "--expected-count", $ExpectedCount,
  "--grid", $Grid,
  "--ocr-lang", $OcrLang
)

if ($SupabaseUrl) {
  $ArgsList += @("--supabase-url", $SupabaseUrl)
}

if ($SupabaseKey) {
  $ArgsList += @("--supabase-key", $SupabaseKey)
}

if ($SupabaseTable) {
  $ArgsList += @("--supabase-table", $SupabaseTable)
}

if ($SupabaseOverridesTable) {
  $ArgsList += @("--supabase-overrides-table", $SupabaseOverridesTable)
}

if ($SupabaseImageBucket) {
  $ArgsList += @("--supabase-image-bucket", $SupabaseImageBucket)
}

if ($SupabaseImagePrefix) {
  $ArgsList += @("--supabase-image-prefix", $SupabaseImagePrefix)
}

if ($Force) {
  $ArgsList += "--force"
}

if ($DownloadOnly) {
  $ArgsList += "--download-only"
}

if ($EnableMlEmbeddings) {
  $ArgsList += "--enable-ml-embeddings"
}

& $PythonExe @ArgsList
