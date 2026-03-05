#
# NekoProxy Controller Updater for Windows
#
# Updates the controller binary without touching config or data.
# Usage: .\update-controller.ps1 -BinaryPath "C:\path\to\new\nekoproxy-controller.exe"
#

param(
    [Parameter(Mandatory=$true)]
    [string]$BinaryPath
)

$BinaryName = "nekoproxy-controller.exe"

# Validate new binary exists
if (-not (Test-Path $BinaryPath)) {
    Write-Host "ERROR: File not found: $BinaryPath" -ForegroundColor Red
    exit 1
}

# Find the currently running controller process to determine install location
$process = Get-Process -Name "nekoproxy-controller" -ErrorAction SilentlyContinue
$service = Get-Service -Name "nekoproxy-controller" -ErrorAction SilentlyContinue

if ($process) {
    $InstallPath = $process.Path
    if (-not $InstallPath) {
        # Fallback: try to get from MainModule
        try {
            $InstallPath = $process.MainModule.FileName
        } catch {
            $InstallPath = $null
        }
    }
}

if (-not $InstallPath) {
    # Try common install locations
    $candidates = @(
        "$env:ProgramFiles\NekoProxy\$BinaryName",
        "$env:LOCALAPPDATA\NekoProxy\$BinaryName",
        ".\$BinaryName"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            $InstallPath = $candidate
            break
        }
    }
}

if (-not $InstallPath -or -not (Test-Path $InstallPath)) {
    Write-Host "ERROR: Cannot find current installation." -ForegroundColor Red
    Write-Host "Please specify the install directory:" -ForegroundColor Yellow
    $InstallDir = Read-Host "Install directory"
    $InstallPath = Join-Path $InstallDir $BinaryName
    if (-not (Test-Path $InstallPath)) {
        Write-Host "ERROR: $InstallPath not found" -ForegroundColor Red
        exit 1
    }
}

$InstallDir = Split-Path $InstallPath -Parent
$BackupPath = "$InstallPath.backup"

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  NekoProxy Controller Updater (Windows)" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Install path: $InstallPath"
Write-Host "New binary:   $BinaryPath"
Write-Host ""

# Step 1: Stop the process/service
Write-Host "Stopping controller..." -ForegroundColor Cyan
if ($service) {
    Stop-Service -Name "nekoproxy-controller" -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] Stopped service" -ForegroundColor Green
} elseif ($process) {
    Stop-Process -Name "nekoproxy-controller" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Write-Host "[OK] Stopped process" -ForegroundColor Green
} else {
    Write-Host "[!] Controller was not running" -ForegroundColor Yellow
}

# Wait for process to fully exit
Start-Sleep -Seconds 1
$retries = 0
while ((Get-Process -Name "nekoproxy-controller" -ErrorAction SilentlyContinue) -and $retries -lt 10) {
    Start-Sleep -Seconds 1
    $retries++
}

# Step 2: Backup current binary
Write-Host "Backing up current binary..." -ForegroundColor Cyan
Copy-Item $InstallPath $BackupPath -Force
Write-Host "[OK] Backed up to $BackupPath" -ForegroundColor Green

# Step 3: Copy new binary
Write-Host "Installing new binary..." -ForegroundColor Cyan
Copy-Item $BinaryPath $InstallPath -Force
Write-Host "[OK] Installed new binary" -ForegroundColor Green

# Step 4: Start
Write-Host "Starting controller..." -ForegroundColor Cyan
if ($service) {
    Start-Service -Name "nekoproxy-controller"
    Start-Sleep -Seconds 3
    $svc = Get-Service -Name "nekoproxy-controller"
    if ($svc.Status -eq "Running") {
        Write-Host "[OK] Service is running!" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Service failed to start" -ForegroundColor Red
        $rollback = Read-Host "Rollback to previous version? (y/n)"
        if ($rollback -eq "y") {
            Stop-Service -Name "nekoproxy-controller" -Force -ErrorAction SilentlyContinue
            Copy-Item $BackupPath $InstallPath -Force
            Start-Service -Name "nekoproxy-controller"
            Write-Host "[OK] Rolled back" -ForegroundColor Green
        }
    }
} else {
    # Start as a regular process
    Start-Process -FilePath $InstallPath -WindowStyle Hidden
    Start-Sleep -Seconds 3
    $newProc = Get-Process -Name "nekoproxy-controller" -ErrorAction SilentlyContinue
    if ($newProc) {
        Write-Host "[OK] Controller is running!" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Controller failed to start" -ForegroundColor Red
        $rollback = Read-Host "Rollback to previous version? (y/n)"
        if ($rollback -eq "y") {
            Copy-Item $BackupPath $InstallPath -Force
            Start-Process -FilePath $InstallPath -WindowStyle Hidden
            Write-Host "[OK] Rolled back" -ForegroundColor Green
        }
    }
}

Write-Host ""
Write-Host "Update complete. Config and data preserved." -ForegroundColor Green
Write-Host "Backup at: $BackupPath" -ForegroundColor Cyan
Write-Host "Rollback:  Copy-Item '$BackupPath' '$InstallPath' -Force" -ForegroundColor Cyan
