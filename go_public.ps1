# Infinite OCR: make the app reachable from the internet over HTTPS, with Google sign-in, and start it at every boot.
# Run once in an Administrator PowerShell:
#     powershell -ExecutionPolicy Bypass -File C:\app\go_public.ps1
# It asks three questions. Run it again any time to change an answer.
$ErrorActionPreference = 'Stop'
$app = 'C:\app'

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }
function Ask($question) { $a = Read-Host $question; return $a.Trim() }

Write-Host "Infinite OCR - public setup" -ForegroundColor Green
Write-Host "You need: a domain name that points at this server, and a Google sign-in Client ID (see DEPLOY.md)."
Write-Host "Settings you set by hand in C:\app\start.bat are replaced by your answers here."
Write-Host ""

$domain = (Ask "1/3  Domain name (for example mypdfocr.duckdns.org, without https://)").ToLower()
$client = Ask "2/3  Google Client ID (ends with .apps.googleusercontent.com)"
$emails = (Ask "3/3  Email allowed to sign in (several can be separated by commas)").ToLower() -replace '\s', ''

if ($domain -notmatch '^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$') { throw "That does not look like a domain name: $domain" }
if ($client -notmatch '^[0-9A-Za-z._-]+\.apps\.googleusercontent\.com$') { throw "That does not look like a Google Client ID: $client" }
if ($emails -and $emails -notmatch '^[^@,;&|<>^%"\s]+@[^@,;&|<>^%"\s]+(,[^@,;&|<>^%"\s]+@[^@,;&|<>^%"\s]+)*$') { throw "Emails must look like a@x.com,b@y.com (no spaces or odd symbols)" }
if (-not $emails) { throw "Give at least one email, otherwise nobody can sign in." }

Step "Checking that the name points at this server"
$mine = (Invoke-RestMethod -Uri 'https://checkip.amazonaws.com' -TimeoutSec 15).ToString().Trim()
$seen = @()
try { $seen = @(Resolve-DnsName -Name $domain -Type A -ErrorAction Stop | Where-Object { $_.Type -eq 'A' } | ForEach-Object { $_.IPAddress }) } catch { }
if ($seen -contains $mine) {
    Write-Host "OK: $domain points at $mine"
} else {
    Write-Host "WARNING: $domain points at '$($seen -join ', ')' but this server is $mine." -ForegroundColor Yellow
    Write-Host "HTTPS cannot be issued until the name points here. Fix the DNS record, wait a few minutes, then run this script again."
    if ((Ask "Continue anyway? (y/N)") -ne 'y') { throw "Stopped: fix the DNS record first." }
}

Step "Saving the settings"
[Environment]::SetEnvironmentVariable('GOOGLE_CLIENT_ID', $client, 'Machine')
[Environment]::SetEnvironmentVariable('ALLOWED_EMAILS', $emails, 'Machine')
[Environment]::SetEnvironmentVariable('AUTH_DISABLED', $null, 'Machine')
foreach ($k in 'OPENROUTER_API_KEY', 'BODHAN_OCR_API_KEY') {
    if (-not [Environment]::GetEnvironmentVariable($k, 'Machine')) { Write-Host "WARNING: $k is not set on this machine. Review and Re-read will fail until it is (setx $k `"...`" /M)." -ForegroundColor Yellow }
}

Step "Writing start.bat with the real Python path"
$py = (& python -c "import sys; print(sys.executable)").Trim()
if (-not (Test-Path $py)) { throw "Could not find Python." }
$bat = @"
@echo off
cd /d $app
if not exist $app\data mkdir $app\data
set AUTH_DISABLED=
set GOOGLE_CLIENT_ID=$client
set ALLOWED_EMAILS=$emails
"$py" -m uvicorn ocr.api:app --host 127.0.0.1 --port 8000 >> $app\data\app.log 2>&1
"@
Set-Content -Path "$app\start.bat" -Value $bat -Encoding ASCII
Write-Host "Python: $py"

Step "Finding or installing Caddy (it provides HTTPS)"
function Find-Caddy {
    $c = Get-Command caddy -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $hits = @("C:\caddy\caddy.exe") + @(Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter caddy.exe -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    foreach ($h in $hits) { if (Test-Path $h) { return $h } }
    return $null
}
$caddy = Find-Caddy
if (-not $caddy) {
    try { winget install -e --id CaddyServer.Caddy --source winget --accept-package-agreements --accept-source-agreements } catch { Write-Host "winget could not install Caddy." }
    $caddy = Find-Caddy
}
if (-not $caddy) { throw "Caddy is not installed. Download caddy_*_windows_amd64.zip from https://github.com/caddyserver/caddy/releases, unzip caddy.exe into C:\caddy\ and run this script again." }
Write-Host "Caddy: $caddy"

Step "Writing the Caddy settings"
$cf = @"
$domain {
	request_body {
		max_size 60MB
	}
	reverse_proxy 127.0.0.1:8000
}
"@
Set-Content -Path "$app\Caddyfile" -Value $cf -Encoding ASCII
& $caddy validate --config "$app\Caddyfile" --adapter caddyfile
if ($LASTEXITCODE -ne 0) { throw "Caddy did not accept its settings." }

Step "Opening ports 80 and 443 in Windows"
foreach ($p in 80, 443) {
    if (-not (Get-NetFirewallRule -DisplayName "Web $p" -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -DisplayName "Web $p" -Direction Inbound -Protocol TCP -LocalPort $p -Action Allow | Out-Null
    }
}

Step "Setting the app and Caddy to start at every boot"
Get-Process python, caddy -ErrorAction SilentlyContinue | Stop-Process -Force
foreach ($t in @(Get-ScheduledTask | Where-Object { $_.TaskName -in 'OCR-App', 'OCR-Caddy' -or ($_.Actions | Where-Object { $_.Execute -like '*start.bat*' }) })) {
    Unregister-ScheduledTask -TaskName $t.TaskName -Confirm:$false
}
$trigger = New-ScheduledTaskTrigger -AtStartup
$who = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$conf = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
Register-ScheduledTask -TaskName 'OCR-App' -Trigger $trigger -Principal $who -Settings $conf -Action (New-ScheduledTaskAction -Execute "$app\start.bat") | Out-Null
Register-ScheduledTask -TaskName 'OCR-Caddy' -Trigger $trigger -Principal $who -Settings $conf -Action (New-ScheduledTaskAction -Execute $caddy -Argument "run --config `"$app\Caddyfile`" --adapter caddyfile" -WorkingDirectory $app) | Out-Null
Start-ScheduledTask -TaskName 'OCR-App'
Start-Sleep -Seconds 8
Start-ScheduledTask -TaskName 'OCR-Caddy'

Step "Checking"
$ok = $false
for ($i = 0; $i -lt 12 -and -not $ok; $i++) {
    try { $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/config' -UseBasicParsing -TimeoutSec 5; $ok = ($r.Content -match 'google_client_id') } catch { Start-Sleep -Seconds 3 }
}
if ($ok) { Write-Host "The app is running on this machine." } else { Write-Host "The app did not answer. Look at: Get-Content $app\data\app.log -Tail 30" -ForegroundColor Yellow }
$pub = $false
for ($i = 0; $i -lt 12 -and -not $pub; $i++) {
    try { $r = Invoke-WebRequest -Uri "https://$domain/api/config" -UseBasicParsing -TimeoutSec 10; $pub = ($r.Content -match 'google_client_id') } catch { Start-Sleep -Seconds 5 }
}
if ($pub) {
    Write-Host "HTTPS works: https://$domain" -ForegroundColor Green
} else {
    Write-Host "Could not confirm HTTPS from this machine yet. The certificate can take a minute. Try https://$domain from your own computer." -ForegroundColor Yellow
    Write-Host "If it still fails: in the AWS console open the instance's Security group and allow inbound HTTP (80) and HTTPS (443) from Anywhere."
}
Write-Host ""
Write-Host "Last step: restart the server once (Start, Power, Restart), wait 3 to 5 minutes, and open https://$domain to prove it starts by itself." -ForegroundColor Green
