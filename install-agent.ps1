#
# NekoProxy Agent (proxy) Installer for Windows
#
# Installs nekoproxy-agent.exe as a Windows service that starts automatically
# when the server boots. The controller is NOT a service - it runs in a shell
# window; only the proxy/agent runs as a service.
#
# Run as Administrator. The service uses nekoproxy-agent.exe from the same
# folder as this script and reads agent.env from that same folder.
#
# Usage:
#   .\install-agent.ps1                     # register + auto-start on boot
#   .\install-agent.ps1 -StartService       # also start it now
#   .\install-agent.ps1 -DelayedStart       # boot start, delayed (after network)
#   .\install-agent.ps1 -Uninstall          # stop + remove the service
#
# Unattended config (writes agent.env if it does not exist):
#   .\install-agent.ps1 -ControllerUrl https://10.0.0.1:8001 -Hostname win-proxy-1 `
#       -WireguardIp 10.0.0.5 -StartService
#
param(
    [switch]$StartService,
    [switch]$DelayedStart,
    [switch]$Uninstall,
    [switch]$NonInteractive,
    [string]$ControllerUrl,
    [string]$Hostname,
    [string]$WireguardIp,
    [string]$ControlUrl,
    [string]$PublicIp,
    [string]$AgentSecret
)

$BinaryName  = "nekoproxy-agent.exe"
$ServiceName = "nekoproxy-agent"

# --- Locate the exe ---
# onedir layout: <root>\nekoproxy-agent\nekoproxy-agent.exe
# Also accept the exe sitting directly beside this script or in the current dir.
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$SubDir = [IO.Path]::GetFileNameWithoutExtension($BinaryName)   # "nekoproxy-agent"
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
    Write-Host "  Uninstall NekoProxy Agent (Windows service)" -ForegroundColor Cyan
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host ""
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc) {
        if ($svc.Status -ne "Stopped") {
            Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }
        Push-Location $ExeDir
        try { & $ExePath remove } finally { Pop-Location }
        Write-Host "[OK] Service removed." -ForegroundColor Green
    } else {
        Write-Host "[!] Service not installed." -ForegroundColor Yellow
    }
    Write-Host ""
    exit 0
}

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Install NekoProxy Agent (Windows service)" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Exe   : $ExePath" -ForegroundColor Gray

# --- Config file (agent.env next to the exe) ---
$configPath = Join-Path $ExeDir "agent.env"
if (Test-Path $configPath) {
    Write-Host "Config: $configPath (existing - left untouched)" -ForegroundColor Gray
} else {
    $interactive = -not $NonInteractive
    if (-not $ControllerUrl -and $interactive) {
        Write-Host ""
        Write-Host "No agent.env found. Enter the agent configuration:" -ForegroundColor Cyan
        Write-Host "  Use https:// if the controller has TLS enabled (default)." -ForegroundColor Gray
        $ControllerUrl = Read-Host "Controller URL (e.g. https://10.0.0.1:8001)"
        if (-not $Hostname)    { $Hostname    = Read-Host "Hostname for this agent [$env:COMPUTERNAME]" }
        if (-not $WireguardIp) { $WireguardIp = Read-Host "WireGuard IP (blank = internal agent)" }
        if (-not $WireguardIp -and -not $ControlUrl) {
            $ControlUrl = Read-Host "Control URL for controller to reach this agent (e.g. https://127.0.0.1:8002)"
        }
        if (-not $PublicIp)    { $PublicIp    = Read-Host "Public IP (optional, Enter to skip)" }
        if (-not $AgentSecret) { $AgentSecret = Read-Host "Agent registration secret (optional, Enter if none)" }
    }

    if (-not $ControllerUrl) {
        Write-Host ""
        Write-Host "ERROR: No agent.env and no -ControllerUrl given." -ForegroundColor Red
        Write-Host "Create $configPath with at least NEKO_AGENT_CONTROLLER_URL, or re-run with -ControllerUrl." -ForegroundColor Yellow
        exit 1
    }

    if (-not $Hostname) { $Hostname = $env:COMPUTERNAME }

    $lines = @(
        "# NekoProxy Agent configuration - generated by install-agent.ps1 on $(Get-Date -Format s)",
        "NEKO_AGENT_CONTROLLER_URL=$ControllerUrl",
        "NEKO_AGENT_HOSTNAME=$Hostname"
    )
    if ($WireguardIp) { $lines += "NEKO_AGENT_WIREGUARD_IP=$WireguardIp" }
    if ($ControlUrl)  { $lines += "NEKO_AGENT_CONTROL_URL=$ControlUrl" }
    if ($PublicIp)    { $lines += "NEKO_AGENT_PUBLIC_IP=$PublicIp" }
    if ($AgentSecret) { $lines += "NEKO_AGENT_AGENT_SECRET=$AgentSecret" }
    # ASCII (no BOM) - a UTF-8 BOM would make the loader miss the first key.
    Set-Content -Path $configPath -Value $lines -Encoding ascii
    Write-Host "Config: $configPath (created)" -ForegroundColor Green
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

    # --- Crash / exit recovery: let the SCM restart the proxy if it dies ---
    & sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/10000/restart/30000 | Out-Null
    & sc.exe failureflag $ServiceName 1 | Out-Null
    Write-Host "[OK] Recovery: auto-restart after 5s / 10s / 30s." -ForegroundColor Green

    if ($StartService) {
        Start-Service -Name $ServiceName
        Start-Sleep -Seconds 3
        $svc = Get-Service -Name $ServiceName
        if ($svc.Status -eq "Running") {
            Write-Host "[OK] Agent started (Control API on port 8002)." -ForegroundColor Green
        } else {
            Write-Host "[!] Service status: $($svc.Status). Check logs\$ServiceName.log" -ForegroundColor Yellow
        }
    } else {
        Write-Host ""
        Write-Host "Start now with: Start-Service $ServiceName" -ForegroundColor Cyan
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "The agent will now start automatically every time the server boots." -ForegroundColor Green
Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Cyan
Write-Host "  Start-Service $ServiceName" -ForegroundColor Gray
Write-Host "  Stop-Service  $ServiceName" -ForegroundColor Gray
Write-Host "  Get-Service   $ServiceName" -ForegroundColor Gray
Write-Host "  Logs : $ExeDir\logs\$ServiceName.log" -ForegroundColor Gray
Write-Host "  Remove: .\install-agent.ps1 -Uninstall" -ForegroundColor Gray
Write-Host ""
