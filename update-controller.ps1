#
# NekoProxy Controller Updater for Windows
#
# Updates the controller program files without touching config or data
# (.env, nekoproxy.db, *.pem, logs\, uploads\ are all left alone).
#
# The controller is a PyInstaller *onedir* build: nekoproxy-controller.exe only
# runs with the matching _internal\ folder beside it. Copying the exe alone over
# an older install leaves a mismatched _internal and the service dies on start,
# so this script replaces both.
#
# Usage:
#   .\update-controller.ps1 -BinaryPath "C:\path\to\new\nekoproxy-controller"      # the folder
#   .\update-controller.ps1 -BinaryPath "C:\path\to\new\nekoproxy-controller\nekoproxy-controller.exe"
#
param(
    [Parameter(Mandatory=$true)]
    [string]$BinaryPath
)

$BinaryName  = "nekoproxy-controller.exe"
$ServiceName = "nekoproxy-controller"

# --- Resolve the new build (accept the onedir folder or the exe inside it) ---
if (-not (Test-Path $BinaryPath)) {
    Write-Host "ERROR: Not found: $BinaryPath" -ForegroundColor Red
    exit 1
}
if ((Get-Item $BinaryPath).PSIsContainer) {
    $NewExe = Join-Path $BinaryPath $BinaryName
} else {
    $NewExe = $BinaryPath
}
if (-not (Test-Path $NewExe)) {
    Write-Host "ERROR: $BinaryName not found at $NewExe" -ForegroundColor Red
    exit 1
}
$NewDir      = Split-Path $NewExe -Parent
$NewInternal = Join-Path $NewDir "_internal"
if (-not (Test-Path $NewInternal)) {
    Write-Host "ERROR: $NewInternal not found." -ForegroundColor Red
    Write-Host "       Point -BinaryPath at the folder produced by the build" -ForegroundColor Yellow
    Write-Host "       (dist\windows\nekoproxy-controller\), not just the .exe." -ForegroundColor Yellow
    exit 1
}

# --- Locate the current install ---
$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
$process = Get-Process -Name "nekoproxy-controller" -ErrorAction SilentlyContinue

$InstallPath = $null
if ($service) {
    # The SCM ImagePath is '"<exe>" service' - strip the quotes and the argument.
    $imagePath = (Get-CimInstance -ClassName Win32_Service -Filter "Name='$ServiceName'").PathName
    if ($imagePath -match '^\s*"([^"]+)"') { $InstallPath = $Matches[1] }
    elseif ($imagePath) { $InstallPath = ($imagePath -split ' ')[0] }
}
if (-not $InstallPath -and $process) {
    $InstallPath = $process.Path
    if (-not $InstallPath) {
        try { $InstallPath = $process.MainModule.FileName } catch { $InstallPath = $null }
    }
}
if (-not $InstallPath -or -not (Test-Path $InstallPath)) {
    $candidates = @(
        "$env:ProgramFiles\NekoProxy\$BinaryName",
        "$env:ProgramFiles\NekoProxy\nekoproxy-controller\$BinaryName",
        "$env:LOCALAPPDATA\NekoProxy\$BinaryName",
        ".\$BinaryName"
    )
    $InstallPath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $InstallPath -or -not (Test-Path $InstallPath)) {
    Write-Host "ERROR: Cannot find the current installation." -ForegroundColor Red
    $InstallDir = Read-Host "Install directory (the folder containing $BinaryName)"
    $InstallPath = Join-Path $InstallDir $BinaryName
    if (-not (Test-Path $InstallPath)) {
        Write-Host "ERROR: $InstallPath not found" -ForegroundColor Red
        exit 1
    }
}

$InstallDir     = Split-Path $InstallPath -Parent
$OldInternal    = Join-Path $InstallDir "_internal"
$BackupExe      = "$InstallPath.backup"
$BackupInternal = "$OldInternal.backup"

if ($service -and -not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERROR: Run PowerShell as Administrator to update the service." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  NekoProxy Controller Updater (Windows)" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Install dir: $InstallDir"
Write-Host "New build:   $NewDir"
Write-Host ""

# --- Step 1: Stop ---
Write-Host "Stopping controller..." -ForegroundColor Cyan
if ($service) {
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] Stopped service" -ForegroundColor Green
} elseif ($process) {
    Stop-Process -Name "nekoproxy-controller" -Force -ErrorAction SilentlyContinue
    Write-Host "[OK] Stopped process" -ForegroundColor Green
} else {
    Write-Host "[!] Controller was not running" -ForegroundColor Yellow
}

$retries = 0
while ((Get-Process -Name "nekoproxy-controller" -ErrorAction SilentlyContinue) -and $retries -lt 15) {
    Start-Sleep -Seconds 1
    $retries++
}

# --- Step 2: Back up the program files (data files are untouched) ---
Write-Host "Backing up current build..." -ForegroundColor Cyan
Copy-Item $InstallPath $BackupExe -Force
if (Test-Path $OldInternal) {
    if (Test-Path $BackupInternal) { Remove-Item -Recurse -Force $BackupInternal }
    Move-Item $OldInternal $BackupInternal -Force
}
Write-Host "[OK] Backed up to $BackupExe / $BackupInternal" -ForegroundColor Green

# --- Step 3: Install the new build ---
Write-Host "Installing new build..." -ForegroundColor Cyan
try {
    Copy-Item $NewExe $InstallPath -Force
    Copy-Item $NewInternal $OldInternal -Recurse -Force
    Write-Host "[OK] Installed exe + _internal" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Copy failed: $_" -ForegroundColor Red
    Write-Host "Restoring previous build..." -ForegroundColor Yellow
    Copy-Item $BackupExe $InstallPath -Force
    if (Test-Path $OldInternal) { Remove-Item -Recurse -Force $OldInternal }
    if (Test-Path $BackupInternal) { Move-Item $BackupInternal $OldInternal -Force }
    exit 1
}

function Restore-Previous {
    Copy-Item $BackupExe $InstallPath -Force
    if (Test-Path $OldInternal) { Remove-Item -Recurse -Force $OldInternal }
    if (Test-Path $BackupInternal) { Copy-Item $BackupInternal $OldInternal -Recurse -Force }
}

# --- Step 4: Start ---
Write-Host "Starting controller..." -ForegroundColor Cyan
if ($service) {
    Start-Service -Name $ServiceName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 5
    $svc = Get-Service -Name $ServiceName
    if ($svc.Status -eq "Running") {
        Write-Host "[OK] Service is running!" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Service failed to start (status: $($svc.Status))." -ForegroundColor Red
        Write-Host "        Check $InstallDir\logs\$ServiceName.log" -ForegroundColor Yellow
        Write-Host "        and  $InstallDir\logs\$ServiceName-startup.log" -ForegroundColor Yellow
        $rollback = Read-Host "Rollback to previous version? (y/n)"
        if ($rollback -eq "y") {
            Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
            Restore-Previous
            Start-Service -Name $ServiceName
            Write-Host "[OK] Rolled back" -ForegroundColor Green
        }
    }
} else {
    Start-Process -FilePath $InstallPath -WorkingDirectory $InstallDir -WindowStyle Hidden
    Start-Sleep -Seconds 5
    if (Get-Process -Name "nekoproxy-controller" -ErrorAction SilentlyContinue) {
        Write-Host "[OK] Controller is running!" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Controller failed to start" -ForegroundColor Red
        $rollback = Read-Host "Rollback to previous version? (y/n)"
        if ($rollback -eq "y") {
            Restore-Previous
            Start-Process -FilePath $InstallPath -WorkingDirectory $InstallDir -WindowStyle Hidden
            Write-Host "[OK] Rolled back" -ForegroundColor Green
        }
    }
}

Write-Host ""
Write-Host "Update complete. Config and data preserved." -ForegroundColor Green
Write-Host "Backup: $BackupExe and $BackupInternal" -ForegroundColor Cyan
Write-Host ""
