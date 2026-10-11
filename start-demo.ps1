<#
.SYNOPSIS
  Start the complete OPD Saathi demo with Docker: database, cache, API, worker and web app.

.DESCRIPTION
  Run from the repository root:   powershell -ExecutionPolicy Bypass -File .\start-demo.ps1
  First run builds the images (several minutes). Later runs take seconds.
  Creates .env with random local secrets if it does not exist. To enable AI and
  natural voices, put GEMINI_API_KEY (and optionally SARVAM_API_KEY) in .env and
  run this script again.
#>
param(
  [int]$WebPort = 5173,
  [switch]$NoBuild
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

function Get-EnvValue($lines, $name) {
  $line = $lines | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
  if ($line) { return ($line -replace "^$name=", "") }
  return ""
}

Write-Step "Checking Docker"
docker info --format "{{.ServerVersion}}" *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running. Start it and run this script again." }

if (-not (Test-Path ".env")) {
  Write-Step "Creating .env with random local secrets"
  $text = Get-Content ".env.example" -Raw
  foreach ($name in "POSTGRES_PASSWORD","OSA_APP_PASSWORD","OSA_MIGRATOR_PASSWORD","OSA_WORKER_PASSWORD","REDIS_PASSWORD","GRAFANA_ADMIN_PASSWORD") {
    $text = $text -replace "(?m)^$name=.*$", "$name=$(New-Hex)"
  }
  foreach ($name in "APP_DATA_KEY","APP_HMAC_KEY","CURSOR_HMAC_KEY") {
    $text = $text -replace "(?m)^$name=.*$", "$name=$(New-Base64Key)"
  }
  $text = $text -replace "(?m)^JWT_SECRET=.*$", "JWT_SECRET=$(New-Hex)"
  $text = $text -replace "(?m)^DEMO_ADMIN_PASSWORD=.*$", "DEMO_ADMIN_PASSWORD=Saathi-$(New-Hex)"
  $text = $text -replace "(?m)^DEMO_STAFF_PASSWORD=.*$", "DEMO_STAFF_PASSWORD=Saathi-$(New-Hex)"
  Set-Content -Path ".env" -Value $text -Encoding utf8
}

$envLines = Get-Content ".env"
$adminEmail = Get-EnvValue $envLines "DEMO_ADMIN_EMAIL"
if (-not $adminEmail) { $adminEmail = "admin@demo.opdsathi.test" }
$adminPassword = Get-EnvValue $envLines "DEMO_ADMIN_PASSWORD"
$staffPassword = Get-EnvValue $envLines "DEMO_STAFF_PASSWORD"
if ($adminPassword.Length -lt 12 -or $adminPassword -like "replace-*") { $adminPassword = "Saathi-$(New-Hex)"; Add-Content .env "`nDEMO_ADMIN_PASSWORD=$adminPassword" }
if ($staffPassword.Length -lt 12 -or $staffPassword -like "replace-*") { $staffPassword = "Saathi-$(New-Hex)"; Add-Content .env "DEMO_STAFF_PASSWORD=$staffPassword" }
# Token-signing keys must never fall back to the public defaults in docker-compose.yml
# (anyone with the repo could forge a sign-in). Older .env files may lack them.
foreach ($name in "JWT_SECRET","CURSOR_HMAC_KEY") {
  $value = Get-EnvValue $envLines $name
  if ($value.Length -lt 32 -or $value -like "*development-only*" -or $value -like "replace-*") {
    $fresh = if ($name -eq "JWT_SECRET") { New-Hex } else { New-Base64Key }
    $text = Get-Content ".env" -Raw
    if ($text -match "(?m)^$name=") { $text = $text -replace "(?m)^$name=.*$", "$name=$fresh" }
    else { $text = $text.TrimEnd() + "`n$name=$fresh`n" }
    Set-Content -Path ".env" -Value $text -Encoding utf8 -NoNewline
    Write-Host "Generated a private $name in .env (everyone signs in again once)." -ForegroundColor Yellow
  }
}
$gemini = Get-EnvValue $envLines "GEMINI_API_KEY"
$sarvam = Get-EnvValue $envLines "SARVAM_API_KEY"

# The web container serves the app; free the port or pick another one.
$busy = Get-NetTCPConnection -LocalPort $WebPort -State Listen -ErrorAction SilentlyContinue |
  Where-Object { (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName -ne "com.docker.backend" }
if ($busy) {
  Write-Host "Port $WebPort is used by another program (often 'npm run dev'); using 8080 instead." -ForegroundColor Yellow
  $WebPort = 8080
}
$webUrl = "http://localhost:$WebPort"
$env:WEB_PORT = "$WebPort"
$env:WEB_ORIGIN = "http://localhost:5173,http://127.0.0.1:5173,$webUrl,http://127.0.0.1:$WebPort"
$env:PROVIDER_MODE = "standard"

# Patients scan the token QR code with their phone, which cannot open "localhost". Use this
# PC's Wi-Fi/LAN address for the QR link and let phones on the same network reach the app.
$phoneUrl = Get-EnvValue $envLines "PUBLIC_WEB_URL"
if (-not $phoneUrl -and (Get-Command Get-NetIPConfiguration -ErrorAction SilentlyContinue)) {
  $lan = Get-NetIPConfiguration -ErrorAction SilentlyContinue |
    Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" } |
    ForEach-Object { $_.IPv4Address.IPAddress } |
    Where-Object { $_ -and $_ -notlike "169.254.*" } |
    Select-Object -First 1
  if ($lan) { $phoneUrl = "http://${lan}:$WebPort" }
}
if ($phoneUrl) {
  $env:PUBLIC_WEB_URL = $phoneUrl
  $env:WEB_BIND = "0.0.0.0"
  $env:WEB_ORIGIN = "$($env:WEB_ORIGIN),$phoneUrl"
}

$build = @()
if (-not $NoBuild) { $build = @("--build") }

Write-Step "Starting database and cache"
docker compose up -d postgres redis redis-cache worker-role-bootstrap
if ($LASTEXITCODE -ne 0) { throw "Could not start the database containers." }

Write-Step "Applying database migrations"
docker compose run --rm @build api alembic -c /app/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw "Migrations failed." }

Write-Step "Preparing the demo hospital and staff accounts"
docker compose run --rm -e DEMO_ADMIN_EMAIL=$adminEmail -e DEMO_ADMIN_PASSWORD=$adminPassword -e DEMO_STAFF_PASSWORD=$staffPassword api python -m app.cli seed-demo | Out-Host

Write-Step "Adding a month of sample OPD history for Reports"
docker compose run --rm api python -m app.cli seed-demo-history --days 30 | Out-Host

Write-Step "Starting API, worker and web app"
docker compose up -d @build api worker web
if ($LASTEXITCODE -ne 0) { throw "Could not start the application containers." }

Write-Step "Creating a kiosk activation link"
$kiosk = docker compose run --rm api python -m app.cli demo-kiosk --web-url $webUrl
$kioskLink = ($kiosk | Select-String "activate=").ToString().Trim()

Write-Host ""
Write-Host "OPD Saathi is running." -ForegroundColor Green
Write-Host ""
Write-Host "  Patient kiosk (open once to activate):"
Write-Host "    $kioskLink" -ForegroundColor Yellow
Write-Host "  Staff workspace:   $webUrl/staff"
Write-Host "    Admin:   $adminEmail  /  $adminPassword"
Write-Host "    Doctors (password $staffPassword), one per department:"
Write-Host "      doctor01 General Medicine | doctor02 Orthopaedics | doctor03 Ear, Nose and Throat"
Write-Host "      doctor04 Obstetrics and Gynaecology | doctor05 Paediatrics | doctor06 Dermatology"
Write-Host "      doctor07 Ophthalmology | doctor08 General Surgery     (each @demo.opdsathi.test)"
Write-Host "    Nurses (password $staffPassword), General Medicine with doctor01:"
Write-Host "      nurse01 ... nurse05@demo.opdsathi.test"
Write-Host "  API docs:          http://localhost:8000/docs"
if ($phoneUrl) {
  Write-Host "  Token QR codes open on phones via $phoneUrl (phone must be on the same Wi-Fi)." -ForegroundColor Green
} else {
  Write-Host "  No Wi-Fi/LAN address found: token QR codes will only open on this PC." -ForegroundColor Yellow
}
Write-Host ""
if ($gemini) { Write-Host "  AI: Gemini key found - AI summaries, translation and photo reading are ON." -ForegroundColor Green }
else { Write-Host "  AI: no GEMINI_API_KEY in .env - running with rules + browser voice only." -ForegroundColor Yellow }
if ($sarvam) { Write-Host "  Voice: Sarvam key found - Indian-language speech and voices are ON." -ForegroundColor Green }
Write-Host ""
Write-Host "  Use Chrome or Edge for voice. Stop everything with:  docker compose down"
Start-Process $kioskLink
