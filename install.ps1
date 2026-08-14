# Copyright 2026 Vsevolod Belonogov
# SPDX-License-Identifier: Apache-2.0

$ErrorActionPreference = "Stop"
$InstallDir = $PSScriptRoot
$RequiredMajor = 3
$RequiredMinor = 14
$UseStrictRequirements = $true

Write-Host "1/5 Verifying Global Python Installation..."
try {
    $pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
    $versionString = & $pythonPath --version 2>&1
    # Extracts the version numbers (e.g., "Python 3.14.3" -> 3, 14, 3)
    $versionParts = ($versionString -replace '[^\d.]', '').Split('.')
    $major = [int]$versionParts[0]
    $minor = [int]$versionParts[1]

    if ($major -lt $RequiredMajor -or ($major -eq $RequiredMajor -and $minor -lt $RequiredMinor)) {
        Write-Host "Found Python $versionString, but this application is tested with Python 3.14." -ForegroundColor Yellow
        $upgradeChoice = Read-Host "Would you like to automatically upgrade to Python 3.14? (Y/N)"

        if ($upgradeChoice -match '^[Yy]') {
            throw "User approved upgrade"
        } else {
            Write-Host "Upgrade declined." -ForegroundColor Yellow
            $proceedChoice = Read-Host "Would you like to proceed with your older version of Python AT YOUR OWN RISK? (Y/N)"

            if ($proceedChoice -match '^[Yy]') {
                Write-Host "Proceeding with Python $versionString at your own risk." -ForegroundColor Magenta
                Write-Host "Note: We will not install the exact versions of the dependencies to try to make it work on your older setup. Errors may still occur." -ForegroundColor Magenta
                $UseStrictRequirements = $false
            } else {
                Write-Host "Setup cancelled. Cannot proceed without a compatible Python version." -ForegroundColor Red
                exit
            }
        }
    } elseif ($major -gt $RequiredMajor -or $minor -gt $RequiredMinor) {
        Write-Host "Found Python $versionString, which is newer than Python $RequiredMajor.$RequiredMinor - the version this application is tested with." -ForegroundColor Yellow
        $proceedChoice = Read-Host "Would you like to proceed with your newer version of Python AT YOUR OWN RISK? (Y/N)"

        if ($proceedChoice -match '^[Yy]') {
            Write-Host "Proceeding with Python $versionString at your own risk." -ForegroundColor Magenta
            Write-Host "Note: We will not install the exact tested versions of the dependencies, because builds for your newer Python may not exist yet. Errors may still occur." -ForegroundColor Magenta
            $UseStrictRequirements = $false
        } else {
            Write-Host "Setup cancelled. This version of the application is tested with Python $RequiredMajor.$RequiredMinor." -ForegroundColor Red
            exit
        }
    } else {
        Write-Host "Found Python $versionString. (Requirements met)." -ForegroundColor Green
    }
} catch {
    if ($_.Exception.Message -match "User approved upgrade") {
        Write-Host "Attempting to install via winget..." -ForegroundColor Yellow
    } else {
        Write-Host "Python not found. Attempting to install via winget..." -ForegroundColor Yellow
    }
    winget install --id Python.Python.3.14 --silent --accept-source-agreements --accept-package-agreements

    Write-Host "Please restart this terminal window to complete the Python path registration." -ForegroundColor Cyan
    Read-Host "Press Enter to exit and then re-open this folder..."
    exit
}

Write-Host "2/5 Creating/Verifying isolated Virtual Environment..."
if (!(Test-Path "$InstallDir\venv")) {
    & $pythonPath -m venv "$InstallDir\venv"
    Write-Host "Virtual environment created." -ForegroundColor Green
} else {
    Write-Host "Virtual environment already exists. Skipping creation." -ForegroundColor Cyan
}

$venvPython = "$InstallDir\venv\Scripts\python.exe"
$venvPythonw = "$InstallDir\venv\Scripts\pythonw.exe"

Write-Host "3/5 Installing media libraries into the virtual environment (This takes a moment)..."
& $venvPython -m pip install --upgrade pip | Out-Null

if ($UseStrictRequirements) {
    # Every package pinned, so this install matches the tested one exactly
    & $venvPython -m pip install -r "$InstallDir\requirements.lock"
} else {
    # Unpinned, and direct dependencies only, for older or newer Python versions
    & $venvPython -m pip install -r "$InstallDir\requirements_no_version.txt"
}

Write-Host "4/5 Preparing first-run files..."
foreach ($dir in @("input", "output")) {
    if (!(Test-Path "$InstallDir\$dir")) {
        New-Item -ItemType Directory -Path "$InstallDir\$dir" | Out-Null
        Write-Host "Created empty '$dir' folder." -ForegroundColor Green
    }
}
if (!(Test-Path "$InstallDir\settings.json")) {
    Copy-Item "$InstallDir\settings.example.json" "$InstallDir\settings.json"
    Write-Host "Created settings.json from the settings.example.json template." -ForegroundColor Green
}

Write-Host "5/5 Creating your Desktop Shortcut..."
$WshShell = New-Object -comObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$Home\Desktop\Seva's Media Processor.lnk")
$Shortcut.TargetPath = $venvPythonw
$Shortcut.Arguments = ".\src\main.py"
$Shortcut.WorkingDirectory = $InstallDir
$Shortcut.WindowStyle = 1
$Shortcut.IconLocation = "$InstallDir\src\static\app_icon.ico,0"
$Shortcut.Save()

Write-Host ("=" * 30)
Write-Host "INSTALLATION COMPLETE!"
Write-Host "Launch the app using the 'Seva's Media Processor' shortcut on your Desktop."
Read-Host "Press Enter to close this window"
