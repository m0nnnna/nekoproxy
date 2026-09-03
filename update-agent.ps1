#
# NekoProxy Agent Updater for Windows
#
# Updates the agent binary without touching config.
# Usage: .\update-agent.ps1 -BinaryPath "C:\path\to\new\nekoproxy-agent.exe"
#

param(
    [Parameter(Mandatory=$true)]
    [string]$BinaryPath
)

$BinaryName = "nekoproxy-agent.exe"

# Validate new binary exists
if (-not (Test-Path $BinaryPath)) {
    Write-Host "ERROR: File not found: $BinaryPath" -ForegroundColor Red
    exit 1
}

# Find the currently running agent process to determine install location
$process = Get-Process -Name "nekoproxy-agent" -ErrorAction SilentlyContinue
$service = Get-Service -Name "nekoproxy-agent" -ErrorAction SilentlyContinue

if ($process) {
    $InstallPath = $process.Path
    if (-not $InstallPath) {
        try {
            $InstallPath = $process.MainModule.FileName
        } catch {
            $InstallPath = $null
        }
    }
}

if (-not $InstallPath) {
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
Write-Host "  NekoProxy Agent Updater (Windows)" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Install path: $InstallPath"
Write-Host "New binary:   $BinaryPath"
Write-Host ""

# Step 1: Stop the process/service
Write-Host "Stopping agent..." -ForegroundColor Cyan
if ($service) {
    Stop-Service -Name "nekoproxy-agent" -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] Stopped service" -ForegroundColor Green
} elseif ($process) {
    Stop-Process -Name "nekoproxy-agent" -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Write-Host "[OK] Stopped process" -ForegroundColor Green
} else {
    Write-Host "[!] Agent was not running" -ForegroundColor Yellow
}

Start-Sleep -Seconds 1
$retries = 0
while ((Get-Process -Name "nekoproxy-agent" -ErrorAction SilentlyContinue) -and $retries -lt 10) {
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
Write-Host "Starting agent..." -ForegroundColor Cyan
if ($service) {
    Start-Service -Name "nekoproxy-agent"
    Start-Sleep -Seconds 3
    $svc = Get-Service -Name "nekoproxy-agent"
    if ($svc.Status -eq "Running") {
        Write-Host "[OK] Service is running!" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Service failed to start" -ForegroundColor Red
        $rollback = Read-Host "Rollback to previous version? (y/n)"
        if ($rollback -eq "y") {
            Stop-Service -Name "nekoproxy-agent" -Force -ErrorAction SilentlyContinue
            Copy-Item $BackupPath $InstallPath -Force
            Start-Service -Name "nekoproxy-agent"
            Write-Host "[OK] Rolled back" -ForegroundColor Green
        }
    }
} else {
    Start-Process -FilePath $InstallPath -WindowStyle Hidden
    Start-Sleep -Seconds 3
    $newProc = Get-Process -Name "nekoproxy-agent" -ErrorAction SilentlyContinue
    if ($newProc) {
        Write-Host "[OK] Agent is running!" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Agent failed to start" -ForegroundColor Red
        $rollback = Read-Host "Rollback to previous version? (y/n)"
        if ($rollback -eq "y") {
            Copy-Item $BackupPath $InstallPath -Force
            Start-Process -FilePath $InstallPath -WindowStyle Hidden
            Write-Host "[OK] Rolled back" -ForegroundColor Green
        }
    }
}

Write-Host ""
Write-Host "Update complete. Config preserved." -ForegroundColor Green
Write-Host "Backup at: $BackupPath" -ForegroundColor Cyan
Write-Host "Rollback:  Copy-Item '$BackupPath' '$InstallPath' -Force" -ForegroundColor Cyan
