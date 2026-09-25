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

Current version: v0.3.0

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

If you have already downloaded and extracted the app, skip to the setup
options below.

1. On this project's GitHub page, click the green "Code" button above the
   file list, then click "Download ZIP" in the menu that opens.
2. Open your Downloads folder. Right-click the downloaded ZIP file and
   choose "Extract All...", then click "Extract".
3. Windows opens the extracted `sevas-media-processor-main` folder. If all
   it holds is another `sevas-media-processor-main` folder, open that one:
   the right folder has `install.txt` and `README.md` in it. The steps
   below call it the extracted folder (`sevas-media-processor-main`).
4. Move the extracted folder (`sevas-media-processor-main`) to where you
   want to keep it - for example, your Desktop.

### Then: choose a setup path

You may choose one of the two setup paths below:

- Option 1: Copy and paste the setup script into PowerShell (recommended).
- Option 2: Manual setup, one command at a time.

### Option 1: Copy and paste the setup script

The setup script is the file `install.txt` in the extracted folder
(`sevas-media-processor-main`). To run it:

1. Open the extracted folder (`sevas-media-processor-main`).
2. Double-click `install.txt` to open it in Notepad. (Windows may show its
   name as `install`.)
3. Press Ctrl+A to select all of the text, then Ctrl+C to copy it.
4. Press the Windows key on your keyboard (it shows the Windows logo, four
   small squares, and sits in the bottom row between Ctrl and Alt), type
   `PowerShell`, and click "Windows PowerShell" in the results.
5. Right-click anywhere inside the black/blue PowerShell window to paste
   the text.
6. If a warning says the text contains multiple lines or is very large,
   click "Paste anyway".
7. Press Enter.
8. When the window asks for `install.txt`, drag `install.txt` from the
   extracted folder (`sevas-media-processor-main`) into the PowerShell
   window, then press Enter.
9. If it installs Python: close the PowerShell window, open Windows
   PowerShell again the same way, paste the text again and press Enter.

What the setup script does:

1. Verifies Python; if a suitable one is missing and you approve its prompt,
   installs Python 3.14 for your user account using winget.
2. Creates a "venv" (Virtual Environment) folder, so the libraries install
   into it rather than into the machine's Python.
3. Downloads the required third-party libraries into that virtual environment.
4. Creates the `input` and `output` folders and your `settings.json`
   (a copy of `settings.example.json`).
5. Creates a Desktop shortcut.

### Option 2: Manual Setup

1. Download and install Python 3.14 from: https://www.python.org/downloads/
   If you already have Python 3.14, you can skip this step. Newer versions are untested.
   CRITICAL: During installation, you MUST check the box at the bottom that says "Add python.exe to PATH".
2. Open the extracted folder (`sevas-media-processor-main`).
3. Hold down Shift on your keyboard and Right-Click any empty space inside the folder.
4. Click "Open PowerShell window here" or "Open in Terminal".
5. Type this and press Enter to create a virtual environment: `python -m venv venv`
6. Type one of these two lines and press Enter to install the dependencies.
   With Python 3.14: `.\venv\Scripts\python.exe -m pip install -r requirements.lock`
   With any other Python version: `.\venv\Scripts\python.exe -m pip install -r requirements_no_version.txt`
7. Create two folders named `input` and `output` inside the extracted folder
   (Right-click > New > Folder). Files you put in `input` are what the application processes;
   the results land in `output`.
8. Right-click in the extracted folder, select New > Shortcut.
9. Set the location to exactly this: `%windir%\System32\cmd.exe /c "start venv\Scripts\pythonw.exe src\main.py"`
10. Name it "Seva's Media Processor", and click Finish.
11. Right-click the new shortcut and select Properties. In the "Start in" box, put the
    extracted folder's full path (copy it from the folder window's address bar).
    Then, to give the shortcut its proper icon: click "Change Icon...", then "Browse...",
    and pick this file inside the extracted folder: `src\static\app_icon.ico`
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

## Updating to a new version

The application never checks for updates itself. Compare the version
shown at the bottom of the application's sidebar with the
"Current version" line near the top of this page. If they differ, a
newer version exists.

Two lists follow. Use the first one if you installed with a setup or
install script. Use the second one if you installed by typing the
commands yourself.

### Update with the setup script

1. Rename the extracted folder you use now (usually
   `sevas-media-processor-main`) by adding `-old` to the end of its name.
2. On this project's GitHub page, click the green "Code" button above
   the file list, then click "Download ZIP" in the menu that opens.
3. Open your Downloads folder. Right-click the downloaded ZIP file and
   choose "Extract All...", then click "Extract".
4. Windows opens the extracted `sevas-media-processor-main` folder. If all
   it holds is another `sevas-media-processor-main` folder, use that one:
   the right folder has `install.txt` and `README.md` in it. Move it to
   where the old folder is.
5. Rename the new folder to the old folder's name without the `-old`.
   For example, if the old folder is now `sevas-media-processor-main-old`,
   name the new folder `sevas-media-processor-main`; if it is now
   `my_app-old`, name the new folder `my_app`.
6. Move `settings.json`, the `input` folder and the `output` folder
   from the old folder into the new folder.
7. Open the new folder (usually `sevas-media-processor-main`).
8. Double-click `install.txt` to open it in Notepad. (Windows may show its
   name as `install`.)
9. Press Ctrl+A to select all of the text, then Ctrl+C to copy it.
10. Press the Windows key on your keyboard (it shows the Windows logo, four
    small squares, and sits in the bottom row between Ctrl and Alt), type
    `PowerShell`, and click "Windows PowerShell" in the results.
11. Right-click anywhere inside the black/blue PowerShell window to paste
    the text.
12. If a warning says the text contains multiple lines or is very large,
    click "Paste anyway".
13. Press Enter.
14. When the window asks for `install.txt`, drag `install.txt` from the new
    folder into the PowerShell window, then press Enter.
15. Launch the application from the Desktop shortcut. Once it works,
    delete the old folder.

### Update by hand

1. Rename the extracted folder you use now (usually
   `sevas-media-processor-main`) by adding `-old` to the end of its name.
2. On this project's GitHub page, click the green "Code" button above
   the file list, then click "Download ZIP" in the menu that opens.
3. Open your Downloads folder. Right-click the downloaded ZIP file and
   choose "Extract All...", then click "Extract".
4. Windows opens the extracted `sevas-media-processor-main` folder. If all
   it holds is another `sevas-media-processor-main` folder, use that one:
   the right folder has `install.txt` and `README.md` in it. Move it to
   where the old folder is.
5. Rename the new folder to the old folder's name without the `-old`.
   For example, if the old folder is now `sevas-media-processor-main-old`,
   name the new folder `sevas-media-processor-main`; if it is now
   `my_app-old`, name the new folder `my_app`.
6. Move `settings.json`, the `input` folder and the `output` folder
   from the old folder into the new folder.
7. Open the new folder (usually `sevas-media-processor-main`).
8. Hold down Shift on your keyboard and Right-Click any empty space inside the folder.
9. Click "Open PowerShell window here" or "Open in Terminal".
10. Type this and press Enter to create a virtual environment: `python -m venv venv`
11. Type one of these two lines and press Enter to install the dependencies.
    With Python 3.14: `.\venv\Scripts\python.exe -m pip install -r requirements.lock`
    With any other Python version: `.\venv\Scripts\python.exe -m pip install -r requirements_no_version.txt`
12. Launch the application from the Desktop shortcut. Once it works,
    delete the old folder.

If you cloned with git: `git pull`, delete the `venv` folder, then paste
`install.txt` into PowerShell opened in the cloned folder again.

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
