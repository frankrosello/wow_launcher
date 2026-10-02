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

## Building the launcher (PyInstaller)

The launcher is built into a standalone Windows `.exe` with [PyInstaller](https://pyinstaller.org/), so players don't need Python installed.

### What you need to build
- Python 3.9 or newer, with Tkinter (included with most Python installers)
- `pip install pillow psutil pyinstaller`
- The three code files in one folder: `main.py`, `addon_manager.py`, and `update_manager.py`

### Build
Open a terminal in the project folder and run (Windows Command Prompt):

```bat
pyinstaller --noconfirm --windowed --name "WoW Launcher" --icon wowicon.ico ^
  --add-data "logos;logos" --add-data "backgrounds;backgrounds" ^
  --add-data "screenshots;screenshots" --add-data "fonts;fonts" ^
  --add-data "wowicon.png;." --add-data "wowicon.ico;." ^
  --add-data "wow_logo.png;." --add-data "image.png;." ^
  main.py
```

Your build appears in `dist\WoW Launcher\`. Run `WoW Launcher.exe` from there.

Notes:
- `--windowed` hides the console window. `--icon` sets the icon of the `.exe` itself.
- Each `--add-data "source;destination"` bundles an artwork file or folder. Leave out any that you don't have, because PyInstaller stops with an error if a listed path is missing.
- In PowerShell, use a backtick (`` ` ``) instead of `^` at the end of each line, or put the whole command on one line.
- On macOS or Linux, separate source and destination with `:` instead of `;`. A macOS app icon must be an `.icns` file.
- The default build is a folder (`--onedir`), which starts quickly and keeps the artwork easy to swap. Add `--onefile` for a single `.exe`, but it starts more slowly and the bundled artwork can't be changed afterwards.
- The launcher saves `launcher_settings.json` next to the `.exe`, so keep the folder somewhere you can write to.
- Some antivirus programs flag PyInstaller programs as suspicious by mistake. If that happens, build it yourself from source rather than trusting a random download.

> **Before you publish a build:** the `.exe` bundles whatever artwork you added. If that includes Blizzard logos, backgrounds, or screenshots, don't upload the build as a public release. See the [Disclaimer](#disclaimer).

On first launch the setup wizard helps you point the launcher at your game folders. You can change them later under **Options**.

### Artwork folders (optional)

The launcher looks for its artwork next to `main.py`, or inside the build if it was bundled with `--add-data`. If a file is missing, it falls back to a plain themed look.

| Folder / file | Used for |
|---|---|
| `logos/` | Per-game logos |
| `backgrounds/` | Per-game background art |
| `screenshots/` | Four rotating images per game, named `classicscreenshot1.png` to `classicscreenshot4.png`, and likewise `retailscreenshot…`, `tbcscreenshot…`, `mopscreenshot…`, `foreverscreenshot…` |
| `fonts/` | Optional `*friz*.ttf` font files. On Windows the launcher also looks in your game's own `Fonts` folders. |
| `wowicon.png` / `wowicon.ico` | Window and taskbar icon |

Use your own images or ones you have the right to use. See the disclaimer below.

---

## Technology

**Language:** Python 3 (the whole launcher). The CurseForge proxy it talks to is a small separate JavaScript service on Cloudflare Workers.

**Third-party libraries**
| Library | Used for |
|---|---|
| [Pillow](https://pypi.org/project/Pillow/) (9.1+) | Artwork loading, resizing, gradients, rounded corners, shadows, and the blurred backdrop behind panels |
| [psutil](https://pypi.org/project/psutil/) | Detecting running game processes for playtime tracking |
| [PyInstaller](https://pyinstaller.org/) | Packaging the launcher as a standalone app (build time only) |

**Python standard library**
- `tkinter` (including `ttk`, `font`, `filedialog`, `messagebox`, and `simpledialog`) for the whole interface
- `urllib` for web requests, `zipfile` and `shutil` for addon installs
- `threading`, `queue`, and `concurrent.futures` for background work such as news, update checks, and downloads
- `json`, `pathlib`, `re`, `html.parser`, `shlex`, `subprocess`, `plistlib`, and `ctypes` (Windows font and taskbar-icon handling)

---

## How it works and what it connects to

The launcher starts your existing game executable. It does not patch, modify, or inspect game binaries, and it does not touch your login or the Battle.net Agent. The addon manager only reads and writes inside your `Interface\AddOns` folder (and `AddOns_Disabled` next to it).

Network requests go to:
- the official World of Warcraft news pages (news feed)
- Blizzard's public version service (update notifications)
- a small CurseForge proxy that holds the API key, so you never need your own (addon search and downloads)
- any URL or GitHub link you enter yourself in the addon manager

Settings and playtime are stored locally in `launcher_settings.json` next to the launcher.

---

## Possible future additions

These are ideas, not promises:

- **Backup and restore** of `WTF` and `Interface\AddOns`, including an automatic snapshot before addon updates or a game patch
- **Safer addon updates:** install to a temporary folder and swap it in, so a failed copy can never leave an addon missing
- **Addon compatibility flags:** compare each addon's Interface number with the installed client build and warn when a patch is likely to break it
- **Open Battle.net button** on the update notice
- **Periodic update re-checks** while the launcher stays open
- **Addon profiles** (for example raid, PvP, minimal) that switch sets of enabled addons
- **More addon sources** beyond CurseForge, such as GitHub releases or WoWInterface
- **Cache cleaner** and an **error-log viewer** for post-patch troubleshooting
- **More launch options:** window mode, process priority, and "close launcher on play"
- **Windows `.exe` build** via PyInstaller for people who don't want to install Python

---

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

_Add the license for the launcher's source code here (for example MIT). This license applies to the code only and never to any Blizzard assets._
