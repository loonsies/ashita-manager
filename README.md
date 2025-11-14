# Ashita Package Manager — Addons/Plugins and script manager for Ashita v4

<p align="center">
	<img src="https://github.com/loonsies/ashita-manager/blob/main/assets/logo_full.png?raw=true" alt="Ashita Package Manager" width="700" style="max-width: 100%;"/>
</p>

<h3 align="center" style="margin-bottom: 50px;">
	Simple, fast management of Ashita addons and plugins with script editing
</h3>

## This utility is still in testing and developement, please use carefully. Strongly recommended to backup your Ashita installation before starting to use it. Use at your own risk.

## At this moment, the package manager assumes all addons/plugins in your folders comes pre-installed with Ashita. Recommended to use on a fresh install, then reinstall your addons/plugins using the package manager.

## Features
- **Install from Git/Release:** Paste a repo URL, auto-detect type, pick branch, install
- **Initial scan:** Finds existing addons/plugins in your Ashita install on first launch
- **Script editor:** Add/remove plugins & addons, enable/disable or reorder loading order, manager keybinds/aliases and configuration commands

## Quick Use
- Launch the app; select your Ashita folder on first run
- Paste a Git URL; choose Type (Auto/Addons/Plugins) and Method (Clone/Release); click Install
- If multiple branches exist, choose one
- Use the Addons/Plugins tabs to update, remove, open repository, or view README
- Open the Scripts tab to edit your Ashite script: toggle items, reorder, and save

## Run from Source
```powershell
# Install dependencies
pip install -r requirements.txt

# Run the app
python ashita_manager.py
```

## Build to EXE
```powershell
# Install dependencies (including PyInstaller)
pip install -r requirements.txt

# Build standalone EXE
python -m PyInstaller --onefile --windowed --name "Ashita Package Manager" --icon assets/logo.ico --add-data "assets:assets" ashita_manager.py
```

The compiled .exe will be in the `dist/` folder as `Ashita Package Manager.exe`.