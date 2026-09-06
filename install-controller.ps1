#
# NekoProxy Controller Installer for Windows
#
# Installs nekoproxy-controller.exe as a Windows service that starts
# automatically when the server boots (instead of running in a shell window).
#
# Run as Administrator. The service uses nekoproxy-controller.exe from the same
# folder as this script; its database, TLS cert, uploads and .env all live in
# that same folder.
#
# Usage:
#   .\install-controller.ps1                    # register + auto-start on boot
#   .\install-controller.ps1 -StartService      # also start it now
#   .\install-controller.ps1 -DelayedStart      # boot start, delayed (after network)
#   .\install-controller.ps1 -Uninstall         # stop + remove the service
#
# Optional config (writes .env next to the exe if it does not exist):
#   .\install-controller.ps1 -ListenHost 0.0.0.0 -Port 8001 -StartService
#
param(
    [switch]$StartService,
    [switch]$DelayedStart,
    [switch]$Uninstall,
    [switch]$NonInteractive,
    [string]$ListenHost,
    [string]$Port,
    [string]$DatabaseUrl
)

$BinaryName  = "nekoproxy-controller.exe"
$ServiceName = "nekoproxy-controller"

# --- Locate the exe ---
# onedir layout: <root>\nekoproxy-controller\nekoproxy-controller.exe
# Also accept the exe sitting directly beside this script or in the current dir.
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$SubDir = [IO.Path]::GetFileNameWithoutExtension($BinaryName)   # "nekoproxy-controller"
$Candidates = @(
    (Join-Path $ScriptDir (Join-Path $SubDir $BinaryName)),
    (Join-Path (Get-Location) (Join-Path $SubDir $BinaryName)),
    (Join-Path $ScriptDir $BinaryName),
    (Join-Path (Get-Location) $BinaryName)
)
$ExePath = $Candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $ExePath) {
    Write-Host "ERROR: $BinaryName not found. Looked in:" -ForegroundColor Red
    $Candidates | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    exit 1
}
$ExeDir = Split-Path $ExePath -Parent

# --- Verify the deployment is complete before touching the SCM ---
# This is an onedir build: the exe needs its _internal\ folder beside it. Copying
# the exe alone (or a half-copied _internal) gives
#   "Failed to load Python DLL ... _internal\python3xx.dll"
# and the service can never start - it dies in the bootloader, so it never gets
# far enough to write a log or tell the SCM anything.
function Test-Deployment {
    param([string]$Exe, [string]$Dir)

    $internal = Join-Path $Dir "_internal"
    if (-not (Test-Path $internal)) {
        Write-Host "ERROR: $internal is missing." -ForegroundColor Red
        Write-Host "       This is a onedir build - copy the WHOLE nekoproxy-controller folder," -ForegroundColor Yellow
        Write-Host "       not just the .exe." -ForegroundColor Yellow
        return $false
    }
    if (-not (Get-ChildItem $internal -Filter "python*.dll" -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: no python*.dll in $internal - the folder is incomplete." -ForegroundColor Red
        Write-Host "       Re-copy the whole nekoproxy-controller folder from the build." -ForegroundColor Yellow
        return $false
    }

    # Cheapest real proof: make the exe run and report on itself.
    Push-Location $Dir
    try { $out = & $Exe selfcheck 2>&1; $code = $LASTEXITCODE } finally { Pop-Location }
    if ($code -ne 0) {
        Write-Host "ERROR: the controller exe does not run here (exit $code):" -ForegroundColor Red
        $out | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
        Write-Host "       Re-copy the whole nekoproxy-controller folder from the build." -ForegroundColor Yellow
        return $false
    }
    return $true
}

# --- Require elevation ---
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: Run PowerShell as Administrator to install or remove the service." -ForegroundColor Red
    exit 1
}

# --- Uninstall path ---
if ($Uninstall) {
    Write-Host ""
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host "  Uninstall NekoProxy Controller (Windows service)" -ForegroundColor Cyan
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host ""
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc) {
        if ($svc.Status -ne "Stopped") {
            Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }
        # Ask the exe to deregister itself, but never trust that it worked: if the
        # deployment is broken the exe cannot run at all, and reporting success
        # here leaves the service registered and every later install failing with
        # "already installed".
        Push-Location $ExeDir
        try { $out = & $ExePath remove 2>&1; $code = $LASTEXITCODE } finally { Pop-Location }
        if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
            Write-Host "[!] '$ExePath remove' did not remove the service (exit $code):" -ForegroundColor Yellow
            $out | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
            Write-Host "[*] Falling back to sc.exe delete..." -ForegroundColor Cyan
            & sc.exe delete $ServiceName | Out-Null
            Start-Sleep -Seconds 2
        }
        if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
            Write-Host "ERROR: service '$ServiceName' is still registered." -ForegroundColor Red
            Write-Host "       Close services.msc / any nekoproxy-controller process and retry," -ForegroundColor Yellow
            Write-Host "       or remove it manually: sc.exe delete $ServiceName" -ForegroundColor Yellow
            Write-Host ""
            exit 1
        }
        Write-Host "[OK] Service removed. Database and .env were left in place." -ForegroundColor Green
    } else {
        Write-Host "[!] Service not installed." -ForegroundColor Yellow
    }
    Write-Host ""
    exit 0
}

if (-not (Test-Deployment -Exe $ExePath -Dir $ExeDir)) { exit 1 }

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Install NekoProxy Controller (Windows service)" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Exe   : $ExePath" -ForegroundColor Gray

# --- Optional .env next to the exe ---
$configPath = Join-Path $ExeDir ".env"
if (Test-Path $configPath) {
    Write-Host "Config: $configPath (existing - left untouched)" -ForegroundColor Gray
} elseif ($ListenHost -or $Port -or $DatabaseUrl -or (-not $NonInteractive -and
        (Read-Host "Write a .env now? Controller works with defaults (0.0.0.0:8001). [y/N]") -match '^(y|yes)$')) {
    if (-not $ListenHost -and -not $NonInteractive) { $ListenHost = Read-Host "Listen address [0.0.0.0]" }
    if (-not $Port -and -not $NonInteractive)       { $Port       = Read-Host "Listen port [8001]" }
    if (-not $DatabaseUrl -and -not $NonInteractive){ $DatabaseUrl = Read-Host "Database URL [sqlite:///./nekoproxy.db]" }
    if (-not $ListenHost)  { $ListenHost  = "0.0.0.0" }
    if (-not $Port)        { $Port        = "8001" }
    if (-not $DatabaseUrl) { $DatabaseUrl = "sqlite:///./nekoproxy.db" }
    $lines = @(
        "# NekoProxy Controller configuration - generated by install-controller.ps1 on $(Get-Date -Format s)",
        "NEKO_HOST=$ListenHost",
        "NEKO_PORT=$Port",
        "NEKO_DATABASE_URL=$DatabaseUrl"
    )
    # ASCII (no BOM) - a UTF-8 BOM would make the loader miss the first key.
    Set-Content -Path $configPath -Value $lines -Encoding ascii
    Write-Host "Config: $configPath (created)" -ForegroundColor Green
} else {
    Write-Host "Config: using defaults (0.0.0.0:8001, sqlite:///./nekoproxy.db next to the exe)" -ForegroundColor Gray
}
Write-Host ""

# --- Already installed? ---
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[!] Service already installed. Use -Uninstall first to reinstall." -ForegroundColor Yellow
    exit 1
}

# --- Register (run from exe dir so the SCM ImagePath is correct) ---
Push-Location $ExeDir
try {
    & $ExePath install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Service registration failed (exit code $LASTEXITCODE)." -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Service registered." -ForegroundColor Green

    # --- Start on boot ---
    if ($DelayedStart) {
        & sc.exe config $ServiceName start= delayed-auto | Out-Null
        Write-Host "[OK] Startup type: Automatic (Delayed Start)." -ForegroundColor Green
    } else {
        & sc.exe config $ServiceName start= auto | Out-Null
        Write-Host "[OK] Startup type: Automatic." -ForegroundColor Green
    }

    # --- Crash / exit recovery: let the SCM restart the controller if it dies ---
    & sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/10000/restart/30000 | Out-Null
    & sc.exe failureflag $ServiceName 1 | Out-Null
    Write-Host "[OK] Recovery: auto-restart after 5s / 10s / 30s." -ForegroundColor Green

    if ($StartService) {
        Start-Service -Name $ServiceName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 5
        $svc = Get-Service -Name $ServiceName
        if ($svc.Status -eq "Running") {
            Write-Host "[OK] Controller started." -ForegroundColor Green
        } else {
            # Don't leave the user with just a status word: the service writes the
            # real reason (port in use, bad .env, import error) to these files.
            Write-Host "[!] Service status: $($svc.Status)" -ForegroundColor Yellow
            foreach ($log in @("$ExeDir\logs\$ServiceName-startup.log", "$ExeDir\logs\$ServiceName.log")) {
                if (Test-Path $log) {
                    Write-Host ""
                    Write-Host "--- last 20 lines of $log ---" -ForegroundColor Yellow
                    Get-Content $log -Tail 20 | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
                } else {
                    Write-Host "[!] No $log - the exe died before it could log." -ForegroundColor Yellow
                }
            }
            Write-Host ""
            Write-Host "Also check: Get-EventLog -LogName Application -Newest 20 | Where-Object Source -match 'nekoproxy'" -ForegroundColor Gray
        }
    } else {
        Write-Host ""
        Write-Host "Start now with: Start-Service $ServiceName" -ForegroundColor Cyan
    }
} finally {
    Pop-Location
}

Write-Host ""
# Report the port the controller will actually listen on: -Port if given,
# otherwise whatever NEKO_PORT the existing .env carries, otherwise the default.
$EffectivePort = $Port
if (-not $EffectivePort -and (Test-Path $configPath)) {
    $portLine = Select-String -Path $configPath -Pattern '^\s*NEKO_PORT\s*=\s*(\S+)' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($portLine) { $EffectivePort = $portLine.Matches[0].Groups[1].Value }
}
if (-not $EffectivePort) { $EffectivePort = "8001" }

Write-Host "The controller will now start automatically every time the server boots." -ForegroundColor Green
Write-Host "Web UI (HTTPS, self-signed on first run): https://<server-ip>:$EffectivePort" -ForegroundColor Green
Write-Host "If clients are remote, allow the port:" -ForegroundColor Gray
Write-Host "  New-NetFirewallRule -DisplayName 'NekoProxy Controller' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $EffectivePort" -ForegroundColor Gray
Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Cyan
Write-Host "  Start-Service $ServiceName" -ForegroundColor Gray
Write-Host "  Stop-Service  $ServiceName" -ForegroundColor Gray
Write-Host "  Get-Service   $ServiceName" -ForegroundColor Gray
Write-Host "  Logs : $ExeDir\logs\$ServiceName.log" -ForegroundColor Gray
Write-Host "  Data : $ExeDir\nekoproxy.db  (and *-cert.pem / *-key.pem)" -ForegroundColor Gray
Write-Host "  Remove: .\install-controller.ps1 -Uninstall" -ForegroundColor Gray
Write-Host ""
