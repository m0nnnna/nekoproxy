#
# NekoProxy Controller Installer for Windows
#
# Registers the controller as a Windows service using the .exe in this directory.
# Run as Administrator. The service will use nekoproxy-controller.exe from the
# same folder as this script.
#
# Usage: .\install-controller.ps1
#        .\install-controller.ps1 -StartService
#        .\install-controller.ps1 -Uninstall
#

param(
    [switch]$StartService,
    [switch]$Uninstall
)

$BinaryName = "nekoproxy-controller.exe"
$ServiceName = "nekoproxy-controller"

# Find the .exe in the same directory as this script, or current directory
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ExePath = Join-Path $ScriptDir $BinaryName
if (-not (Test-Path $ExePath)) {
    $ExePath = Join-Path (Get-Location) $BinaryName
}
if (-not (Test-Path $ExePath)) {
    Write-Host "ERROR: $BinaryName not found in script directory or current directory." -ForegroundColor Red
    Write-Host "  Script dir: $ScriptDir" -ForegroundColor Yellow
    Write-Host "  Current dir: $(Get-Location)" -ForegroundColor Yellow
    exit 1
}

# Require elevation for install/remove
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: Run PowerShell as Administrator to install or remove the service." -ForegroundColor Red
    exit 1
}

if ($Uninstall) {
    Write-Host ""
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host "  Uninstall NekoProxy Controller (Windows)" -ForegroundColor Cyan
    Write-Host "================================================" -ForegroundColor Cyan
    Write-Host ""
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc) {
        & $ExePath remove
        Write-Host "[OK] Service removed." -ForegroundColor Green
    } else {
        Write-Host "[!] Service not installed." -ForegroundColor Yellow
    }
    Write-Host ""
    exit 0
}

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Install NekoProxy Controller (Windows)" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Using: $ExePath" -ForegroundColor Gray
Write-Host ""

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "[!] Service already installed. Use -Uninstall to remove first." -ForegroundColor Yellow
    exit 1
}

# Register the service (exe must be run from its directory so SCM has correct path)
Push-Location (Split-Path $ExePath -Parent)
try {
    & $ExePath install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Service registration failed (exit code $LASTEXITCODE)." -ForegroundColor Red
        exit 1
    }
    Write-Host "[OK] Service registered." -ForegroundColor Green

    # Optional: set startup to automatic
    Set-Service -Name $ServiceName -StartupType Automatic -ErrorAction SilentlyContinue
    Write-Host "[OK] Startup type set to Automatic." -ForegroundColor Green

    if ($StartService) {
        Start-Service -Name $ServiceName
        Start-Sleep -Seconds 2
        $svc = Get-Service -Name $ServiceName
        if ($svc.Status -eq "Running") {
            Write-Host "[OK] Service started. Controller listening on port 8001." -ForegroundColor Green
        } else {
            Write-Host "[!] Service may still be starting. Check: Get-Service $ServiceName" -ForegroundColor Yellow
        }
    } else {
        Write-Host ""
        Write-Host "To start the controller: Start-Service $ServiceName" -ForegroundColor Cyan
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Useful commands:" -ForegroundColor Cyan
Write-Host "  Start-Service $ServiceName" -ForegroundColor Gray
Write-Host "  Stop-Service $ServiceName" -ForegroundColor Gray
Write-Host "  Get-Service $ServiceName" -ForegroundColor Gray
Write-Host "  Uninstall: .\install-controller.ps1 -Uninstall" -ForegroundColor Gray
Write-Host ""
