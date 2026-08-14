# Seva's Media Processor

[![CI](https://github.com/condensingcomplexitywithseva/sevas-media-processor/actions/workflows/ci.yml/badge.svg)](https://github.com/condensingcomplexitywithseva/sevas-media-processor/actions/workflows/ci.yml)

Ask an AI about your own photos, videos, PDFs and scans - and get the answers
back as a spreadsheet.

Point it at a folder and it does the rest. Every file is converted, read by
the AI model you choose - including a free one running on your own computer -
and the answers land in an Excel or CSV file you can sort and search.
Receipts, invoices, screenshots, scanned paperwork, years of camera photos:
ask once, get an answer for all of them.

Not interested in AI? Switch it off and it is a bulk converter: iPhone HEICs,
videos, GIFs and PDFs all become clean, uniform JPEGs.

Everything runs on your own machine. Nothing leaves it unless you turn the AI
on and tell it where to send the images.

This is a local Windows desktop application: it runs on Windows 10 and 11
only.

(Reviewing this for an IT department? The network and data answers, and what
the installer does, are in SECURITY.md.)

## Work in progress

This is a 0.x application under active development. Interfaces, settings,
and outputs may change between versions. Feedback is welcome; no support
and no publishing schedule are promised.

## What file types it accepts

Capitalisation does not matter - .HEIC and .heic are the same file to it.

| Category | Extensions |
|---|---|
| Photos and images | .jpeg .jpg .jpe .jfif .png .bmp .dib .tif .tiff .heic .heif .avif |
| Animations | .gif .webp |
| Documents | .pdf |
| Video | .mp4 .mov .avi .mkv .wmv .webm |

Anything else in the folder is left alone, listed as unsupported in the
report, and the run carries on. So is a file that turns out to be corrupt or
damaged - it is reported with the reason, and never stops the rest.

## Initial setup instructions

The application requires 64-bit Windows 10 or 11 (any modern computer).

### First: download and extract

If you already have the application folder on your computer, skip to the
setup options below.

1. On this project's GitHub page, click the green "Code" button above the
   file list, then click "Download ZIP" in the menu that opens.
2. Open your Downloads folder. Right-click the downloaded ZIP file and
   choose "Extract All...", then click "Extract".
3. You now have the application folder (GitHub names it
   `sevas-media-processor-main`). Move it to where you want to keep it -
   for example, your Desktop. Everything below happens inside this folder.

### Then: choose a setup path

You may choose one of the three setup paths below:

- Option 1: Run the install script (recommended).
- Option 2: Copy and paste the same script into a terminal.
- Option 3: Manual setup, one command at a time.

### Option 1: Run the install script

1. Open the application folder.
2. Right-click `install.ps1` and choose "Run with PowerShell". (On Windows 11,
   click "Show more options" to see it.)
3. If it installs Python: close the window and start over from step 2.

What the script does:

1. Verifies Python; if a suitable one is missing and you approve its prompt,
   installs Python 3.14 for your user account using winget.
2. Creates a "venv" (Virtual Environment) folder, so the libraries install
   into it rather than into the machine's Python.
3. Downloads the required third-party libraries into that virtual environment.
4. Creates the `input` and `output` folders and your `settings.json`
   (a copy of `settings.example.json`).
5. Creates a Desktop shortcut.

### Option 2: Copy and paste

The same script, pasted into a terminal:

1. Open the application folder.
2. Hold down Shift on your keyboard and Right-Click any empty space inside the folder.
3. Click "Open PowerShell window here" or "Open in Terminal".
4. Copy the ENTIRE block of code below (starting from $ErrorActionPreference).
5. Right-click anywhere inside the black/blue command window to paste the code.
6. Press Enter.
7. If it installs Python: close the terminal and start over from step 1.

```powershell
$ErrorActionPreference = "Stop"
$InstallDir = $PWD
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
```

### Option 3: Manual Setup

1. Download and install Python 3.14 from: https://www.python.org/downloads/
   If you already have Python 3.14, you can skip this step. (Newer versions
   are untested: the exact tested library versions may not exist for them
   yet - see the note under step 6.)
   CRITICAL: During installation, you MUST check the box at the bottom that says "Add python.exe to PATH".
2. Open the application folder.
3. Hold down Shift on your keyboard and Right-Click any empty space inside the folder.
4. Click "Open PowerShell window here" or "Open in Terminal".
5. Type this and press Enter to create a virtual environment: `python -m venv venv`
6. Type this and press Enter to install the dependencies: `.\venv\Scripts\python.exe -m pip install -r requirements.lock`
   Note: If you are proceeding with an older or newer Python version at your own risk, then run this instead: `.\venv\Scripts\python.exe -m pip install -r requirements_no_version.txt`
7. Create two folders named `input` and `output` inside the application folder
   (Right-click > New > Folder). Files you put in `input` are what the application processes;
   the results land in `output`.
8. Right-click in the application folder, select New > Shortcut.
9. Set the location to exactly this: `%windir%\System32\cmd.exe /c "start venv\Scripts\pythonw.exe src\main.py"`
10. Name it "Seva's Media Processor", and click Finish.
11. Right-click the new shortcut and select Properties. In the "Start in" box, put the
    application folder's full path (copy it from the folder window's address bar).
    Then, to give the shortcut its proper icon: click "Change Icon...", then "Browse...",
    and pick this file inside the application folder: `src\static\app_icon.ico`
    Click OK twice to confirm.
12. To launch the application, simply double-click the newly created "Seva's Media Processor" shortcut.

## What will be installed

*   Python 3.14: A popular programming language powering the application.
*   av (18.0.0): The Python interface for FFmpeg. This is the core engine used to analyze, decode, extract, and process video and audio files.
*   numpy (2.5.2): A foundational math library. It handles massive, high-speed number crunching, which is essential for manipulating the raw pixel data inside images and video frames.
*   openpyxl (3.1.5): A specialized tool used for reading, writing, and modifying modern Microsoft Excel spreadsheets (.xlsx files).
*   pillow (12.3.0): The standard Python imaging library. It is used to open, crop, resize, filter, and save standard image files like JPEGs and PNGs.
*   pillow-avif-plugin (1.6.0): An add-on for Pillow that allows the application to read and write AVIF files, a modern, highly compressed web image format.
*   pillow_heif (1.5.0): Another Pillow add-on that enables support for HEIC/HEIF images. This is crucial for processing the default, high-efficiency photos taken by iPhones.
*   pydantic (2.13.4): A strict data validation tool. It acts as a gatekeeper, ensuring that any information the application processes is formatted exactly as expected to prevent bugs and crashes.
*   pypdfium2 (5.12.1): A Python wrapper for Google's PDFium engine. It is used to quickly open, render, and extract text or images from PDF documents.
*   requests (2.34.2): The industry standard for handling network traffic in Python. It is used in exactly one place: sending your images to the AI endpoint you configure, when the optional AI feature is switched on (see SECURITY.md).
*   sqlmodel (0.0.39): A database management library. It is used to safely structure, save, and retrieve the application's internal data (like metadata, file paths, or user settings) using standard SQL databases.
*   Flask (3.1.3): a lightweight WSGI web application framework.
*   pywebview (6.2.1): A lightweight wrapper that gives the application its native desktop window, through the WebView2 runtime built into Windows, instead of relying on a standard web browser.
*   SQLAlchemy (2.0.51): The database engine that sqlmodel is built on. The application talks to it directly for the parts of saving and retrieving records that sqlmodel does not cover.
*   Werkzeug (3.1.8): The web-server machinery that Flask is built on. The application uses it to start its own small server on a private port that only your own computer can reach.
*   pythonnet (3.1.0, Windows only): A bridge to the .NET libraries built into Windows. It does one cosmetic job here: putting the application's icon on the window title bar and the taskbar button.

Each of the libraries above relies on supporting libraries of its own, which
the installer fetches automatically - about twenty more. The file
requirements.lock lists every single package the install puts on your machine,
with the exact version of each - on the tested Python (3.14). If you chose to
proceed on an older or newer Python instead, the installer says so and
installs unpinned versions, which the lock file does not describe.

## Your data and the network

Seva's Media Processor runs fully offline. The AI feature is switched OFF by default,
and the application makes no network connections of its own - no telemetry, no
update checks, no analytics, no crash reporting.

When you do switch the AI feature on, the only outbound traffic is to the AI
endpoint YOU configure in the settings, and it carries only the images being
processed plus your prompt. If you would rather nothing left the machine at
all, point it at Ollama or LM Studio running on your own computer - both are
built-in choices. The "custom" provider can point at any other endpoint,
such as an internal AI gateway.

The full details are in SECURITY.md.

## Licence

Seva's Media Processor is open source under the Apache License 2.0 - see LICENSE.txt.
You are free to use it at home or at work, including commercially.

Provided as-is, without warranty of any kind - see LICENSE.txt.

The third-party libraries listed above are downloaded by pip from PyPI onto
your own machine; they are not redistributed as part of this project, and
several of them carry their own separate licences.

## Author

Built and maintained by Seva. The code was written with the help of AI tools
under close human direction: every change was specified by a human, reviewed
by a human, and is covered by the test suite.

The project is covered on the YouTube channel
"Condensing Complexity With Seva", where the design decisions behind it are
explained in full:
<https://www.youtube.com/@CondensingComplexityWithSeva>
