# World of Warcraft Launcher

A classic-styled, unofficial launcher for your World of Warcraft installations. Pick a game, read the news, manage your addons, and press Play, all from one window and without opening the Battle.net app.

**Current version: 1.0.0**

> **Unofficial fan project.** Not affiliated with, endorsed by, or sponsored by Blizzard Entertainment. See the [Disclaimer](#disclaimer).

---

## Features

### Game launcher
- **One launcher for every client:** WoW Forever Beta, Retail, Classic Era, Mists of Pandaria Classic, and TBC Anniversary. An optional extra slot supports the Crusader Storm loader.
- **Custom clients are picked up automatically.** Extra WoW flavor folders found next to your installs appear in the version menu.
- **First-run setup wizard** and an **Auto Search** that scans your drives for installs.
- **Per-game theming:** each game gets its own colors, logo, background art, and headline text.
- **Rotating screenshots:** four images per game that cross-fade as you switch games.
- **Latest news** pulled live from the official World of Warcraft news pages.
- **Playtime tracking:** session and total time per game, based on detecting the running client.
- **Launch arguments per game** (Options → ARGS). A dot on the button shows which games have arguments set.
- **Quick buttons:** open the game folder, open the addon manager, or open the `WTF` configuration folder.

### Addon manager
- **Browse and install** addons from CurseForge, sorted by popularity. Required dependencies are installed with them.
- **Game-aware:** only offers files that match the selected game (Retail, Classic Era, TBC, MoP Classic).
- **Update checking** with an "Update" status per addon, plus update one, several, or all.
- **Install from a `.zip` file, a direct URL, or a GitHub repository link.**
- **Enable and disable** addons without deleting them (disabled addons move to an `AddOns_Disabled` folder next to `AddOns`).
- **Remove** addons with a confirmation prompt. Your saved settings in `WTF` are kept.
- **Multi-folder addons are grouped** under one entry (for example `Bagnon`, `Bagnon_Config`, and so on).
- **Filter and sort** by name, folder, version, interface number, status, or update state.

### Update notifications
- Reads the installed build from each game's own `.build.info` file and compares it with Blizzard's public version service.
- The **CHECK UPDATES** button turns into **UPDATE AVAILABLE** when the selected game is out of date.
- **Notify-only:** the launcher never downloads or patches game files. Install game updates through Battle.net.

---

## Running from source

### Requirements
- Python 3.9 or newer, with Tkinter
- The launcher source files: `main.py`, `addon_manager.py`, and `update_manager.py`

Install the Python dependencies:

```bash
pip install pillow psutil
```

Run the launcher directly from the project folder:

```bash
python main.py
```

The launcher can be run from source without building an `.exe`. This is useful for development and testing.

---

## Building with PyInstaller

PyInstaller is used to package the launcher into a standalone Windows application so users do not need Python installed.

### Install PyInstaller

```bash
pip install pyinstaller
```

### Using the included spec file

The repository includes `WoWLauncher.spec`, which contains the launcher entry point, bundled artwork folders/files, application name, and Windows icon configuration.

From the project folder, run:

```bash
python -m PyInstaller WoWLauncher.spec --noconfirm --clean
```

The completed application will be placed in:

```text
dist\WoWLauncher\
```

Run:

```text
dist\WoWLauncher\WoWLauncher.exe
```

### What the spec file includes

The PyInstaller configuration bundles:

- `main.py` as the application entry point
- `wowicon.ico` and `wowicon.png`
- `wow_logo.png`
- `image.png`
- `logos/`
- `backgrounds/`
- `screenshots/`
- `fonts/`

It also builds the application as a windowed executable, so a console window is not shown.

### Rebuilding after changes

If you change the launcher source code or bundled artwork, rebuild the application:

```bash
pyinstaller --noconfirm WoWLauncher.spec
```

For a clean rebuild, remove the previous `build/` and `dist/` folders first.

### Important

The generated `.exe` contains whatever artwork is bundled through the spec file. If those files include Blizzard logos, backgrounds, screenshots, fonts, or other Blizzard-owned assets, do not distribute the resulting build publicly unless you have the necessary rights to do so. See the [Disclaimer](#disclaimer).

PyInstaller builds may also be flagged by some antivirus programs as false positives. Building the application yourself from the source is recommended rather than downloading an untrusted executable.

## Disclaimer

This is an **unofficial, fan-made project**. It is **not affiliated with, authorized, maintained, sponsored, or endorsed by Blizzard Entertainment, Inc.** or any of its affiliates.

- **World of Warcraft®**, **Warcraft®**, **Blizzard®**, **Battle.net®**, and related names, logos, and game artwork are trademarks or registered trademarks of **Blizzard Entertainment, Inc.**, and all rights to them belong to Blizzard and its licensors.
- **Blizzard assets are not covered by this project's license.** Any Blizzard logos, backgrounds, screenshots, fonts, or other artwork that the launcher displays remain the property of Blizzard Entertainment. They are shown here to identify the games the launcher works with and are not offered for reuse or redistribution. If you fork or redistribute this project, use your own artwork or make sure you have the right to use it.
- Game news is fetched live from Blizzard's public website and belongs to Blizzard.
- **CurseForge** and addon content shown in the addon manager belong to their respective owners and authors. Each addon is subject to its own license.
- Other names, including third-party clients and loaders, are the property of their respective owners.
- This launcher only starts game clients you have already installed. It does not bypass authentication, download game data, or circumvent any protection. You are responsible for following the **Blizzard End User License Agreement and Terms of Use** and the terms of any service you connect to.
- The software is provided **"as is", without warranty of any kind**. Use it at your own risk. Back up your `WTF` and `AddOns` folders before bulk changes.

If you are a rights holder and want something removed from this repository, please open an issue and it will be taken down promptly.

---

## License

This project is licensed under the MIT License for the original source code written by Frank Rosello.

Copyright (c) 2026 Frank Rosello

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

**Important:** The MIT License applies only to the original source code of this
launcher. It does **not** grant permission to use, copy, modify, or redistribute
any Blizzard Entertainment assets, including World of Warcraft or Blizzard
logos, artwork, screenshots, fonts, or other copyrighted materials. Those
materials remain subject to their respective rights and licenses as described
in the [Disclaimer](#disclaimer).
