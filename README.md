# World of Warcraft Launcher

<img width="1102" height="732" alt="image" src="https://github.com/user-attachments/assets/5eca758b-674a-400d-915f-94efa72e6fda" />

A classic-styled, unofficial launcher for your World of Warcraft installations. Pick a game, read the news, manage your addons, and press Play, all from one window and without opening the Battle.net app.

**Current version: 1.1**

> **Unofficial fan project.** Not affiliated with, endorsed by, or sponsored by Blizzard Entertainment. See the [Disclaimer](#disclaimer).

---

## Features

### Game launcher

- **One launcher for every client:** WoW Forever Beta, Retail, Classic Era, Mists of Pandaria Classic, and TBC Anniversary.
- **Custom clients are picked up automatically.** Extra WoW flavor folders found next to your installs appear in the version menu.
- **First-run setup wizard** and an **Auto Search** that scans your drives for installs.
- **Per-game theming:** each game gets its own colors, logo, background art, and headline text.
- **Rotating screenshots:** four images per game that cross-fade as you switch games.
- **Playtime tracking:** session and total time per game, based on detecting the running client.
- **Playtime viewer:** daily graphs for 7, 30, 90, or 365 days, with per-game or combined totals, period navigation, and daily hover details. Tracking runs while the launcher is open; older lifetime totals have no dated history.
- **Launch arguments per game** (Options → ARGS). A dot on the button shows which games have arguments set.
- **Quick buttons:** Open Folder, Addons, and Options sit together beside Play.
- **Config editor:** edit each installation’s `WTF/Config.wtf` from Options. Saving creates `Config.wtf.bak` and preserves the file’s line endings; external changes are checked before saving.
- **Consistent popup controls:** Armory, Playtime, Options, Config, and addon screens use subtle buttons matching the selected game’s colors.

### Addon manager

- **Browse and install** addons from CurseForge, sorted by popularity. Required dependencies are installed with them.
- **Game-aware browsing:** hides projects without compatible file metadata for Retail, Classic Era, TBC Anniversary, MoP Classic, or Crusader Storm. Forever Beta searches `1.60.1` files and recognizes the separate Forever flavor. Compatibility follows the author’s CurseForge metadata.
- **Download progress:** a progress bar appears only during addon downloads and updates, then hides when finished. File-size metadata enables percentage progress.
- **Clearer layout:** separate install and selection actions, roomier table rows, and horizontal scrolling for long tables.
- **Update checking** with an "Update" status per addon, plus update one, several, or all.
- **Install from a `.zip` file, a direct URL, or a GitHub repository link.**
- **Enable and disable** addons without deleting them (disabled addons move to an `AddOns_Disabled` folder next to `AddOns`).
- **Remove** addons with a confirmation prompt. Your saved settings in `WTF` are kept.
- **Multi-folder addons are grouped** under one entry (for example `Bagnon`, `Bagnon_Config`, and so on).
- **Filter and sort** by name, folder, version, interface number, status, or update state.

### News and article reader

- **Official Blizzard news** on the launcher's main page, with a reload control and a last-updated label.
- **More News / news home:** browse a larger archive loaded from ten news pages and filter it by game or view All Games.
- **Unique articles:** stories are deduplicated by article ID across source pages. Game filters use explicit edition names in titles or summaries; general news stays under All Games. Stories covering multiple editions can appear in each relevant filter.
- **In-app article reader:** read article text and follow links without leaving the launcher. Includes Back, Forward, Reload, Home, and Open in Browser controls, with no address bar.
- **Mouse wheel and trackpad scrolling** throughout the news list, including over article cards, with macOS, Windows, and Linux wheel handling.
  
### Character Armory

- **Search by character name, realm, and region** for Retail, Classic Era, Mists of Pandaria Classic, and TBC Anniversary, subject to the data available from the Armory service.
- **Save characters** for quick access later, refresh their information, and copy profile details or open the official profile when available.
- **Separate search and character pages:** a compact search form and saved-character list lead to a full-panel character view through a single View Character action.
- **Independent game selection:** when opened from Forever or another unsupported version, the Armory defaults to Retail without changing the game selected in the launcher. Supported Classic versions remain selectable.
- **Character overview:** character imagery when available, identity and guild details, item level, and achievement points.
- **Equipment:** item icons and item details, with each item's level displayed on its equipment card.
- **Stats:** readable tables with stat icons, category browsing, search, and compact section footers. Honorable kills and honor level appear with stats instead of using a separate Progress page.
- **Professions:** profession icons, primary and secondary profession groups, and expandable skill tiers when reported by the service.
- **Guild roster:** a guild icon and summary, separate Name, Rank, Level, and Class columns, and search by member name, class, rank, or level. Click any column heading to sort; click again to reverse the order.
- **Open guild members directly:** double-click a roster entry or select it and press View Player. Lookups use each member's own realm when provided, including cross-realm guild members.
- **Achievements:** collapsible categories with category icons, expandable achievement lists, and search by achievement name or completion date. Empty BCC categories are hidden in MoP Classic.
- **Game-themed controls:** Armory borders, dropdowns, tables, and buttons follow the selected Armory version's colors.
- **Version-aware sections:** achievements and professions appear according to version support and the data returned by the service; some Classic endpoints may be unavailable.

### Update notifications

- Reads the installed build from each game's own `.build.info` file and compares it with Blizzard's public version service.
- The **CHECK UPDATES** button turns into **UPDATE AVAILABLE** when the selected game is out of date.
- **Notify-only:** the launcher never downloads or patches game files. Install game updates through Battle.net.

---

## Running from source

### Requirements

- Python 3.9 or newer, with Tkinter
- The launcher source files: `main.py`, `addon_manager.py`, `update_manager.py`, `armory.py`, and `launcher_network.py`

Install the Python dependencies:

```bash
pip install pillow psutil
```

Run the launcher directly from the project folder:

```bash
python main.py
```

The launcher can be run from source without building an `.exe`. This is useful for development and testing.

CurseForge browsing uses the proxy configured in `addon_manager.py`; character lookups use the service configured in `armory.py`. If you run your own services, update their endpoints and deploy the corresponding worker code.

---

## Building and installing

These spec files package the launcher for Windows, macOS, and Linux. Build on the operating system you are targeting, using the same Python environment where Pillow and psutil are installed.

### Prepare the project

Keep the spec files in the project folder alongside `main.py`, `addon_manager.py`, `update_manager.py`, `armory.py`, and `launcher_network.py`. Each spec uses its own directory (`SPECPATH`) to find the source and artwork.

Install the build dependencies:

```bash
python -m pip install pillow psutil pyinstaller
```

On macOS and Linux, use `python3` instead of `python` if that is your Python command. Tkinter must also be available in that environment. On Debian/Ubuntu, install it with `sudo apt install python3-tk` if needed.

### Build outputs

| OS | Spec file | Icon input | Output to install or share |
|---|---|---|---|
| Windows | `WoWLauncher-Windows.spec` | `wowicon.ico` | Entire `dist/WoWLauncher/` folder |
| macOS | `WoWLauncher-macOS.spec` | `wowicon.icns` | `dist/WoWLauncher.app` |
| Linux | `WoWLauncher-Linux.spec` | `wowicon.png` | Single executable: `dist/WoWLauncher` |

### Windows

From the project folder:

```bash
python -m PyInstaller WoWLauncher-Windows.spec --noconfirm --clean
```

Run the built launcher:

```text
dist\WoWLauncher\WoWLauncher.exe
```

This is a **folder build**, not a standalone `.exe`. To install it manually, copy the entire `dist\WoWLauncher` folder to your preferred location and create a shortcut to `WoWLauncher.exe`. Keep all generated supporting files, including `_internal`, with the executable. An installer must include the whole output folder. Python is not needed on the destination computer.

### macOS

From the project folder:

```bash
python3 -m PyInstaller WoWLauncher-macOS.spec --noconfirm --clean
```

Open the built application:

```bash
open dist/WoWLauncher.app
```

To install it, copy `WoWLauncher.app` into your Applications folder. Share or copy the whole `.app` bundle, preserving its contents. The icon comes from `wowicon.icns` beside the spec file; the `.app` contains the runtime and bundled assets.

For a **1.1** release, set both `CFBundleVersion` and `CFBundleShortVersionString` in `WoWLauncher-macOS.spec` to `"1.1"` before building. The supplied spec currently sets both to `"1.0.0"`.

### Linux

From the project folder:

```bash
python3 -m PyInstaller WoWLauncher-Linux.spec --noconfirm --clean
```

Run the built launcher:

```bash
chmod +x dist/WoWLauncher
./dist/WoWLauncher
```

This spec creates a **single executable**, not a `dist/WoWLauncher/` directory. Copy that file to your preferred location to install it. Build for the Linux architecture and distribution compatibility you intend to support.

The spec passes `wowicon.png` as its icon input and bundles it as runtime artwork. Linux executable files do not embed an application icon in the same way as Windows executables; a desktop shortcut can use the PNG separately.

### Bundled source and artwork

All three specs analyze `main.py` and collect its imported Python modules, including the addon manager, update manager, Armory module, and shared network helper. These modules must be available beside the entry point when building.

Each spec includes the following artwork **when it exists**:

- Folders: `backgrounds/`, `fonts/`, `logos/`, and `screenshots/`
- Files: `wow_logo.png`, `wowicon.png`, and `image.png`

Missing optional artwork is skipped by the spec. Keep the platform icon input listed above alongside its spec file. `wowicon.ico` and `wowicon.icns` are platform icon inputs; they are not listed as general data files in these specs.

The specs already define the bundle contents and output format. Build them directly; no additional `--add-data`, `--onefile`, or `--onedir` flags are needed. See the [PyInstaller spec-file documentation](https://pyinstaller.org/en/stable/spec-files.html).

### Rebuilding after changes

If you change the launcher source code or bundled artwork, rebuild with the spec file for your OS (see above). For a clean rebuild, remove the previous `build/` and `dist/` folders first.

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
