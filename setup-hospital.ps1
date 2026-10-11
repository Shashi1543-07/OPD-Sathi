<#
.SYNOPSIS
  Install OPD Saathi on a hospital's own server and onboard the hospital. No demo data.

.DESCRIPTION
  Run once on the hospital server, from the repository root:
    powershell -ExecutionPolicy Bypass -File .\setup-hospital.ps1

  It creates .env with fresh random secrets (if missing), builds and starts the stack
  (its own PostgreSQL database, Redis, API, worker and web app), applies migrations, and
  asks for the hospital's name and first administrator. That administrator then signs in
  and adds staff and kiosks from Settings. Run it again to restart or update the
  installation; use -AddHospital to onboard another hospital on the same server.

  Kiosks and staff PCs on the hospital network open the address it prints at the end.
#>
param(
  [int]$WebPort = 5173,
  [switch]$NoBuild,
  [switch]$AddHospital
)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Write-Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function New-Secret([int]$bytes = 32) {
  $buffer = New-Object byte[] $bytes
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buffer)
  return $buffer
}
function New-Hex { -join ((New-Secret) | ForEach-Object { $_.ToString("x2") }) }
function New-Base64Key { [Convert]::ToBase64String((New-Secret)) }
function Get-EnvValue($name) {
  $line = Get-Content ".env" | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
  if ($line) { return ($line -replace "^$name=", "") }
  return ""
}
function Set-EnvValue($name, $value) {
  $text = Get-Content ".env" -Raw
  if ($text -match "(?m)^$name=") { $text = $text -replace "(?m)^$name=.*$", "$name=$value" }
  else { $text = $text.TrimEnd() + "`n$name=$value`n" }
  Set-Content -Path ".env" -Value $text -Encoding utf8 -NoNewline
}

Write-Step "Checking Docker"
docker info --format "{{.ServerVersion}}" *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running. Start it and run this script again." }

$firstInstall = -not (Test-Path ".env")
if ($firstInstall) {
  Write-Step "Creating .env with fresh secrets for this hospital"
  Copy-Item ".env.example" ".env"
}
# Every installation gets its own secrets; never the example or development defaults.
foreach ($name in "POSTGRES_PASSWORD","OSA_APP_PASSWORD","OSA_MIGRATOR_PASSWORD","OSA_WORKER_PASSWORD","REDIS_PASSWORD","GRAFANA_ADMIN_PASSWORD","JWT_SECRET") {
  $value = Get-EnvValue $name
  if ($value.Length -lt 32 -or $value -like "replace-*" -or $value -like "*development-only*" -or $value -like "*local-only*") {
    if ($name -like "*PASSWORD" -and -not $firstInstall -and $value) {
      # Changing a database password after the database exists would lock the app out.
      continue
    }
    Set-EnvValue $name (New-Hex)
  }
}
foreach ($name in "APP_DATA_KEY","APP_HMAC_KEY","CURSOR_HMAC_KEY") {
  $value = Get-EnvValue $name
  if ($value.Length -lt 32 -or $value -like "replace-*" -or $value -like "*development-only*") {
    if ($name -ne "CURSOR_HMAC_KEY" -and -not $firstInstall -and $value) {
      # Patient data is encrypted with these keys; replacing them would make it unreadable.
      throw "$name in .env looks invalid. Restore it from the hospital's backup instead of replacing it."
    }
    Set-EnvValue $name (New-Base64Key)
  }
}

# Staff PCs and kiosks on the hospital network reach the app at this server's LAN address.
$lan = $null
if (Get-Command Get-NetIPConfiguration -ErrorAction SilentlyContinue) {
  $lan = Get-NetIPConfiguration -ErrorAction SilentlyContinue |
    Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } |
    ForEach-Object { $_.IPv4Address.IPAddress } |
    Where-Object { $_ -and $_ -notlike "169.254.*" } |
    Select-Object -First 1
}
$publicUrl = Get-EnvValue "PUBLIC_WEB_URL"
if (-not $publicUrl -and $lan) { $publicUrl = "http://${lan}:$WebPort" }
$origins = "http://localhost:$WebPort,http://127.0.0.1:$WebPort"
if ($publicUrl) { $origins = "$origins,$publicUrl" }
Set-EnvValue "WEB_PORT" "$WebPort"
Set-EnvValue "WEB_BIND" "0.0.0.0"
Set-EnvValue "WEB_ORIGIN" $origins
if ($publicUrl) { Set-EnvValue "PUBLIC_WEB_URL" $publicUrl }

$build = @()
if (-not $NoBuild) { $build = @("--build") }

Write-Step "Starting this hospital's database and cache"
docker compose up -d postgres redis redis-cache worker-role-bootstrap
if ($LASTEXITCODE -ne 0) { throw "Could not start the database containers." }

Write-Step "Applying database migrations"
docker compose run --rm @build api alembic -c /app/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw "Migrations failed." }

Write-Step "Starting API, worker and web app"
docker compose up -d @build api worker web
if ($LASTEXITCODE -ne 0) { throw "Could not start the application containers." }

if ($firstInstall -or $AddHospital) {
  Write-Step "Onboarding the hospital"
  Write-Host "Enter the hospital's details and its first administrator. The password is not shown as you type."
  docker compose run --rm api python -m app.cli setup-hospital
  if ($LASTEXITCODE -ne 0) { throw "The hospital could not be created. Fix the message above and run with -AddHospital." }
}

$address = if ($publicUrl) { $publicUrl } else { "http://localhost:$WebPort" }
Write-Host ""
Write-Host "OPD Saathi is running for this hospital." -ForegroundColor Green
Write-Host ""
Write-Host "  Staff (any PC on the hospital network):  $address/staff"
Write-Host "  Kiosks:                                   $address/kiosk"
Write-Host ""
Write-Host "  Next steps for the hospital administrator:"
Write-Host "    1. Sign in at /staff and open Settings."
Write-Host "    2. Departments: the standard ones are ready; rename, deactivate or add your own."
Write-Host "    3. Users: add each doctor and nurse with their department."
Write-Host "    4. Kiosks: register each kiosk by its location, then open the setup link it gives"
Write-Host "       once on that kiosk. It stays signed in from then on."
Write-Host ""
Write-Host "  Back up the database and the .env file regularly (scripts/backup.sh)." -ForegroundColor Yellow
Write-Host "  Keep .env private: it holds this hospital's encryption keys." -ForegroundColor Yellow
