<#
.SYNOPSIS
  Check that phones on the hospital Wi-Fi can reach OPD Saathi on this PC.

.DESCRIPTION
  Run from the repository root after start-demo.ps1:
      powershell -ExecutionPolicy Bypass -File .\scripts\lan-check.ps1

  It only reads settings and makes local HTTP requests. It reports this PC's Wi-Fi/LAN
  address, the Windows network profile, whether the web app listens on the network, whether
  the address inside the token and check-in QR codes is still this PC's address, and the
  Windows Firewall rules that matter.

  It never turns the firewall off. If (and only if) phones still cannot connect, an
  administrator can add one narrow rule (this TCP port, local subnet only) and remove it again:
      .\scripts\lan-check.ps1 -AddFirewallRule      (run in an Administrator PowerShell)
      .\scripts\lan-check.ps1 -RemoveFirewallRule

  Things this PC cannot detect: Wi-Fi "client isolation" (common on campus and guest Wi-Fi)
  and a phone on a different network. Always finish by opening the printed test link on a phone.
#>
param(
  [int]$WebPort = 0,
  [switch]$AddFirewallRule,
  [switch]$RemoveFirewallRule
)
$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path -Parent $PSScriptRoot)

$ruleGroup = "OPD Saathi"
$problems = 0

function Write-Ok($text) { Write-Host "  [ok]   $text" -ForegroundColor Green }
function Write-Warn($text) { Write-Host "  [warn] $text" -ForegroundColor Yellow; $script:problems++ }
function Write-Info($text) { Write-Host "         $text" }
function Write-Step($text) { Write-Host "`n$text" -ForegroundColor Cyan }

function Test-Administrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  return ([Security.Principal.WindowsPrincipal]$identity).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if ($RemoveFirewallRule) {
  if (-not (Test-Administrator)) { throw "Run this in an Administrator PowerShell to remove firewall rules." }
  $rules = Get-NetFirewallRule -Group $ruleGroup -ErrorAction SilentlyContinue
  if ($rules) { $rules | Remove-NetFirewallRule; Write-Host "Removed the '$ruleGroup' firewall rule(s)." -ForegroundColor Green }
  else { Write-Host "No '$ruleGroup' firewall rules to remove." }
  return
}

Write-Step "1. Docker and the OPD Saathi containers"
docker info --format "{{.ServerVersion}}" *> $null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not running. Start it, then run start-demo.ps1." }
Write-Ok "Docker is running"
$webContainer = (docker compose ps --format "{{.Name}}" web 2>$null) | Select-Object -First 1
$apiContainer = (docker compose ps --format "{{.Name}}" api 2>$null) | Select-Object -First 1
if (-not $webContainer -or -not $apiContainer) { throw "The web/api containers are not running. Run .\start-demo.ps1 first." }
Write-Ok "Containers: $webContainer, $apiContainer"

Write-Step "2. This PC's network address"
$adapters = @(Get-NetIPConfiguration -ErrorAction SilentlyContinue |
  Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq "Up" -and $_.IPv4Address })
if (-not $adapters) { Write-Warn "No connected Wi-Fi/LAN adapter with a gateway was found. Connect to the hospital network." }
$lanIp = $null
foreach ($adapter in $adapters) {
  $ip = ($adapter.IPv4Address | Select-Object -First 1).IPAddress
  if ($ip -like "169.254.*") { continue }
  if (-not $lanIp) { $lanIp = $ip }
  $netProfile = Get-NetConnectionProfile -InterfaceIndex $adapter.InterfaceIndex -ErrorAction SilentlyContinue
  Write-Ok ("{0}: {1} (network profile: {2})" -f $adapter.InterfaceAlias, $ip, $netProfile.NetworkCategory)
  if ($netProfile -and $netProfile.NetworkCategory -eq "Public") {
    Write-Info "Windows treats this network as Public, which blocks most incoming connections."
    Write-Info "On a trusted hospital network, set it to Private: Settings > Network > (this network) > Private."
  }
}

Write-Step "3. Is the web app reachable from the network?"
$binding = (docker port $webContainer 8080 2>$null) | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+:\d+$' } |
  Select-Object -First 1
if (-not $binding) { throw "Could not read the web container's port. Is it running?" }
$boundAddress, $boundPort = $binding -split ":"
if ($WebPort -eq 0) { $WebPort = [int]$boundPort }
if ($boundAddress -eq "127.0.0.1") {
  Write-Warn "The web app only listens on this PC (127.0.0.1:$boundPort). Phones cannot reach it."
  Write-Info "Run .\start-demo.ps1 while connected to the hospital network: it opens the port to the LAN."
} else {
  Write-Ok "Listening on all network addresses, port $WebPort"
}

$publicUrl = ((docker inspect $apiContainer --format "{{range .Config.Env}}{{println .}}{{end}}") |
  Where-Object { $_ -like "PUBLIC_WEB_URL=*" } | Select-Object -First 1) -replace "^PUBLIC_WEB_URL=", ""
$expected = if ($lanIp) { "http://${lanIp}:$WebPort" } else { "" }
if (-not $publicUrl) {
  Write-Warn "No phone address is set, so token and check-in QR codes point at 'localhost' (this PC only)."
  Write-Info "Run .\start-demo.ps1 while connected to the hospital network."
} elseif ($expected -and $publicUrl.TrimEnd("/") -ne $expected) {
  Write-Warn "QR codes point at $publicUrl, but this PC is now $expected (the network address changed)."
  Write-Info "Phones scanning a QR would wait and fail. Run .\start-demo.ps1 -NoBuild to update it."
} else {
  Write-Ok "QR codes point at $publicUrl"
}

if ($lanIp) {
  try {
    $ready = Invoke-WebRequest -Uri "http://${lanIp}:$WebPort/api/readyz" -UseBasicParsing -TimeoutSec 5
    Write-Ok "http://${lanIp}:$WebPort/api/readyz answered $($ready.StatusCode): the API is ready behind the web app"
  } catch {
    Write-Warn "http://${lanIp}:$WebPort/api/readyz did not answer: $($_.Exception.Message)"
  }
}

Write-Step "4. Windows Firewall"
$profiles = Get-NetFirewallProfile | Where-Object { $_.Enabled }
Write-Info ("Firewall is on for: {0} (keep it on)" -f (($profiles | ForEach-Object { $_.Name }) -join ", "))
$dockerAllow = @(Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue |
  Where-Object { $_.Program -like "*com.docker.backend.exe" } |
  Get-NetFirewallRule -ErrorAction SilentlyContinue |
  Where-Object { $_.Enabled -eq "True" -and $_.Direction -eq "Inbound" })
$allow = @($dockerAllow | Where-Object { $_.Action -eq "Allow" })
$block = @($dockerAllow | Where-Object { $_.Action -eq "Block" })
if ($allow) { Write-Ok ("Docker Desktop may accept connections on: {0}" -f (($allow | ForEach-Object { $_.Profile.ToString() } | Sort-Object -Unique) -join ", ")) }
else { Write-Warn "No inbound Allow rule for Docker Desktop was found. Phones may be blocked." }
if ($block) { Write-Warn "A Block rule for Docker Desktop exists; it overrides Allow rules: $(($block | ForEach-Object { $_.DisplayName }) -join ', ')" }
$ours = @(Get-NetFirewallRule -Group $ruleGroup -ErrorAction SilentlyContinue)
if ($ours) { Write-Info "OPD Saathi rule present (remove with .\scripts\lan-check.ps1 -RemoveFirewallRule)" }

if ($AddFirewallRule) {
  if (-not (Test-Administrator)) { throw "Run this in an Administrator PowerShell to add the firewall rule." }
  if ($ours) { Write-Info "The OPD Saathi rule already exists." }
  else {
    New-NetFirewallRule -DisplayName "OPD Saathi web app (TCP $WebPort, local subnet)" -Group $ruleGroup `
      -Direction Inbound -Action Allow -Protocol TCP -LocalPort $WebPort -RemoteAddress LocalSubnet `
      -Profile Private, Public | Out-Null
    Write-Ok "Added an inbound rule for TCP $WebPort from the local subnet only."
    Write-Info "Remove it after the event with: .\scripts\lan-check.ps1 -RemoveFirewallRule"
  }
}

Write-Step "5. Test from a phone (this PC cannot check Wi-Fi isolation)"
if ($lanIp) {
  Write-Info "Connect a phone to the SAME Wi-Fi, then open:"
  Write-Host "    http://${lanIp}:$WebPort/api/readyz" -ForegroundColor Yellow
  Write-Info "It should show {""status"":""ready""}. If it times out while this PC passes every check above,"
  Write-Info "the Wi-Fi isolates devices from each other: use a dedicated router or a phone hotspot"
  Write-Info "that both this PC and the phones join. Patients then scan the phone check-in QR"
  Write-Info "(Staff > Settings > Kiosks > Phone check-in QR > Show QR)."
}
Write-Host ""
if ($problems -eq 0) { Write-Host "No problems found on this PC." -ForegroundColor Green }
else { Write-Host "$problems problem(s) found; see the [warn] lines above." -ForegroundColor Yellow }
