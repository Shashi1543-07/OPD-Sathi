<#
.SYNOPSIS
  Check, just before a demo, that OPD Saathi is ready: services, database, AI, voice and demo data.

.DESCRIPTION
  Run from the repository root after start-demo.ps1:
      powershell -ExecutionPolicy Bypass -File .\scripts\demo-check.ps1

  It reads the running stack and changes nothing, except that it makes sure the demo's returning
  patient exists (it never removes anything) and sends one tiny request to each configured AI and
  voice service to prove the key and the internet connection work right now. No patient data is
  sent; skip those requests with -SkipAi. Phone access is checked by .\scripts\lan-check.ps1.
#>
param(
  [switch]$SkipAi
)
# Every check reports its own problem. "Continue" also keeps Windows PowerShell 5.1 from
# stopping when a tool inside a container logs to stderr (alembic does), even with 2>$null.
$ErrorActionPreference = "Continue"
Set-Location -Path (Split-Path -Parent $PSScriptRoot)

$problems = 0
function Write-Ok($text) { Write-Host "  [ok]   $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "  [warn] $text" -ForegroundColor Yellow; $script:problems++ }
function Write-Info($text) { Write-Host "         $text" }
function Write-Step($text) { Write-Host "`n$text" -ForegroundColor Cyan }

Write-Step "1. Services"
docker info --format "{{.ServerVersion}}" *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running. Start it, then run start-demo.ps1." }
$states = @(docker compose ps --format "{{.Service}}|{{.State}}|{{.Health}}" 2>$null)
foreach ($service in "postgres", "redis", "redis-cache", "api", "worker", "web") {
  $line = $states | Where-Object { $_ -like "$service|*" } | Select-Object -First 1
  if (-not $line) { Write-Warn "$service is not running. Run .\start-demo.ps1."; continue }
  $null, $state, $health = $line -split "\|"
  if ($state -ne "running" -or ($health -and $health -ne "healthy")) {
    Write-Warn "$service is $state $health"
  } else {
    Write-Ok ("$service running" + $(if ($health) { " ($health)" } else { "" }))
  }
}

Write-Step "2. Web app and API"
$webContainer = (docker compose ps --format "{{.Name}}" web 2>$null) | Select-Object -First 1
$binding = if ($webContainer) {
  (docker port $webContainer 8080 2>$null) | Where-Object { $_ -match ':\d+$' } | Select-Object -First 1
}
$port = if ($binding) { ($binding -split ":")[-1] } else { "5173" }
$base = "http://127.0.0.1:$port"
try {
  $ready = Invoke-RestMethod -Uri "$base/api/readyz" -TimeoutSec 5
  if ($ready.status -eq "ready") { Write-Ok "$base answers; database and queue store are reachable" }
  else { Write-Warn "$base/api/readyz answered: $($ready | ConvertTo-Json -Compress)" }
} catch {
  Write-Warn "$base/api/readyz did not answer: $($_.Exception.Message)"
}
try {
  $hospitals = @((Invoke-RestMethod -Uri "$base/api/auth/hospitals" -TimeoutSec 5).items)
  $demo = $hospitals | Where-Object { $_.name -like "Demo Government Hospital*" }
  if ($demo) { Write-Ok "Staff sign-in lists $($demo.name)" }
  else { Write-Warn "The demo hospital is missing from staff sign-in. Run .\start-demo.ps1." }
  $leftover = @($hospitals | Where-Object { $_.name -like "*Synthetic*" -or $_.name -like "*test*" })
  if ($leftover) {
    Write-Warn "Sign-in also lists test hospitals left by an interrupted test: $(($leftover | ForEach-Object { $_.name }) -join ', ')"
  }
} catch {
  Write-Warn "Could not read the staff sign-in list: $($_.Exception.Message)"
}

Write-Step "3. Database"
$current = (docker compose exec -T api alembic -c /app/alembic.ini current 2>$null) -join " "
if ($current -match "\(head\)") { Write-Ok "Schema is up to date ($($current.Trim()))" }
else { Write-Warn "The database is not at the latest migration ($current). Run .\start-demo.ps1 (it migrates)." }

Write-Step "4. AI and voice"
$selected = docker compose logs api 2>$null | Select-String '"event": "providers_selected"' | Select-Object -Last 1
if ($selected) {
  $llm = [regex]::Match($selected.Line, '"llm": "([^"]+)"').Groups[1].Value
  $stt = [regex]::Match($selected.Line, '"stt": "([^"]+)"').Groups[1].Value
  $tts = [regex]::Match($selected.Line, '"tts": "([^"]+)"').Groups[1].Value
  Write-Info "Configured: AI $llm, speech recognition $stt, read-aloud $tts"
}
if ($SkipAi) {
  Write-Info "Skipped the live AI and voice requests (-SkipAi)."
} else {
  $answers = docker compose exec -T api python -m app.cli ai-check 2>$null
  $failed = $LASTEXITCODE -ne 0
  foreach ($line in $answers) {
    if ($line -match "did not answer") { Write-Warn $line } elseif ($line) { Write-Ok $line }
  }
  if ($failed) {
    Write-Info "The demo still works without AI: rules, tapping and typing, and the browser's voices."
    Write-Info "Say so when you reach an AI step; each one has a fallback (see docs/DEMO.md)."
  }
}

Write-Step "5. Demo patients"
$card = docker compose exec -T api python -m app.cli seed-demo-returning 2>$null |
  Select-String "OPD number" | Select-Object -First 1
if ($card) {
  Write-Ok "Returning patient ready: $($card.ToString().Trim())"
  Write-Info "At the kiosk: age 52, Female, then 'Yes, I have my OPD card'."
} else {
  Write-Warn "Could not prepare the returning patient. Run .\start-demo.ps1."
}
if (Test-Path "docs\demo-papers\synthetic-lab-report.png") {
  Write-Ok "Old test report to photograph: docs\demo-papers\synthetic-lab-report.png"
} else {
  Write-Warn "docs\demo-papers\synthetic-lab-report.png is missing (python services/api/scripts/make_demo_papers.py)."
}

Write-Host ""
if ($problems -eq 0) { Write-Host "Ready for the demo. Check phone access with .\scripts\lan-check.ps1." -ForegroundColor Green }
else { Write-Host "$problems problem(s) found; see the [warn] lines above." -ForegroundColor Yellow }
