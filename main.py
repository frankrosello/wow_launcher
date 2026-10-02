"""A classic-styled launcher for configured World of Warcraft installations."""
from collections import deque
import io
import math
from datetime import date, datetime, timedelta
import json
from html.parser import HTMLParser
import os
from pathlib import Path
import plistlib
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin
from urllib.request import Request, urlopen
import webbrowser

import psutil
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps, ImageTk

from addon_manager import AddonManager, Overlay
from armory import ARMORY_VERSIONS, ArmoryError, REGIONS, fetch_avatar, lookup_character, realm_slug, lookup_achievement_category, supported_character_sections, fetch_media_icon
from update_manager import UpdateController


BG = "#100f12"
PANEL = "#211d1b"
GOLD = "#e3c36e"
TEXT = "#e8dfcb"
MUTED = "#a89d88"
GAME_VERSIONS = (
	"WoW Forever Beta",
	"Retail",
	"Classic Era",
	"Mists of Pandaria Classic",
	"TBC Anniversary",
)
# Optional clients: hidden until auto-detected, or added with the EXTRA button in Options.
EXTRA_VERSIONS = ("Crusader Storm",)
ALL_VERSIONS = (*GAME_VERSIONS, *EXTRA_VERSIONS)
# Games started through a loader that may exit once the real client is running.
LOADER_VERSIONS = ("Crusader Storm",)
# Several games share WowClassic.exe / Wow.exe, so the exe's file version (major number)
# decides which game a folder belongs to: 1.x Era, 2.x TBC, 5.x MoP Classic.
# Only used when auto-detecting folders; a path you chose or saved is always trusted.
# Crusader Storm is not listed: it is found by its own Crusader-Storm.exe loader.
CLIENT_MAJOR_VERSIONS = {
	"Classic Era": (1,),
	"TBC Anniversary": (2,),
	"Mists of Pandaria Classic": (5,),
}
# Used for playtime when several games claim the same running exe.
RUNNING_CLIENT_MAJORS = {**CLIENT_MAJOR_VERSIONS, "Crusader Storm": (2,)}
IS_MAC = sys.platform == "darwin"
# Tk point sizes are 4/3 larger on Windows (96 dpi) than on macOS (72 dpi), so on a Mac the
# same size looks smaller. Sizes are given to Tk in pixels there (negative) to match.
# 1.333 matches Windows at 100% display scaling, 1.667 at 125%. Tune this to taste.
MAC_FONT_SCALE = 1.5
# Windows .exe names first, then the macOS .app bundle names (Mac clients are app bundles).
EXECUTABLES = {
	"WoW Forever Beta": ("WowB.exe", "World of Warcraft Classic Beta.app",
						 "World of Warcraft Beta.app"),
	"Retail": ("Wow.exe", "World of Warcraft.app"),
	"Classic Era": ("WowClassic.exe", "World of Warcraft Classic.app"),
	"Mists of Pandaria Classic": ("WowClassic.exe", "World of Warcraft Classic.app"),
	"TBC Anniversary": ("WowClassic.exe", "World of Warcraft Classic.app"),
	# Its own loader; it starts the client.
	"Crusader Storm": ("Crusader-Storm.exe", "CrusaderStorm.app"),
}
INSTALL_SUBDIRECTORIES = {
	"WoW Forever Beta": ("_classic_beta_", ""),
	"Retail": ("", "_retail_"),
	"Classic Era": ("", "_classic_era_", "_classic_"),
	"Mists of Pandaria Classic": ("", "_classic_", "_classic_mop_"),
	"TBC Anniversary": ("_anniversary_", "_classic_tbc_", "", "_classic_"),
	"Crusader Storm": ("", "Crusader-Storm_WoW"),
}
AUTO_DETECT_SUBDIRECTORIES = {
	"WoW Forever Beta": ("_classic_beta_",),
	"Retail": ("_retail_",),
	"Classic Era": ("_classic_era_",),
	"Mists of Pandaria Classic": ("_classic_mop_", "_classic_"),
	"TBC Anniversary": ("_anniversary_", "_classic_tbc_"),
	"Crusader Storm": ("Crusader-Storm_WoW",),
}
GAME_ART = {
	"WoW Forever Beta": ("wow_forever_logo", "wow_forever_background"),
	"Retail": ("wow_retail_logo", "wow_midnight_background"),
	"Classic Era": ("wow_classic_logo", "wow_classic_background"),
	"Mists of Pandaria Classic": ("wow_mop_classic_logo", "wow_mop_classic_background"),
	"TBC Anniversary": ("wow_tbc_anniversary_logo", "wow_tbc_anniversary_background"),
	"Crusader Storm": ("wow_tbc_anniversary_logo", "wow_tbc_anniversary_background"),
}
GAME_LOGO_SIZES = {
	"WoW Forever Beta": (520, 160),
	"Retail": (500, 160),
	"Classic Era": (540, 165),
	"Mists of Pandaria Classic": (540, 165),
	"TBC Anniversary": (540, 165),
	"Crusader Storm": (540, 165),
}
GAME_COPY = {
	"WoW Forever Beta": {
		"title": "A WORLD THAT NEVER ENDS",
		"subtitle": "Step into WoW Forever and rediscover Azeroth as a living, breathing world. "
			"Explore vast zones, face dangerous dungeons, and forge lasting friendships.",
		"headline": "A NEW ADVENTURE AWAITS", "tagline": "YOUR LEGEND NEVER ENDS",
		"slides": (
			("A NEW ADVENTURE AWAITS", "YOUR LEGEND NEVER ENDS"),
			("A WORLD WORTH EXPLORING", "EVERY ROAD LEADS TO A STORY"),
			("STRONGER TOGETHER", "FORGE BONDS THAT LAST"),
			("GLORY AWAITS", "DELVE, FIGHT, AND CONQUER"),
		),
		"play": "Play",
	},
	"Retail": {
		"title": "CHOOSE YOUR NEXT ADVENTURE",
		"subtitle": "Enter the modern World of Warcraft and continue an ever-evolving saga. Team up "
			"for dungeons, raids, and world events, or venture out alone when ready.",
		"headline": "A NEW CHAPTER BEGINS", "tagline": "YOUR STORY CONTINUES",
		"slides": (
			("A NEW CHAPTER BEGINS", "YOUR STORY CONTINUES"),
			("A WORLD IN MOTION", "EVERY ZONE HIDES A SECRET"),
			("ANSWER THE CALL", "DUNGEONS, RAIDS, AND MORE"),
			("FORGE YOUR LEGEND", "FACE WHAT LIES AHEAD"),
		),
		"play": "Play",
	},
	"Classic Era": {
		"title": "RETURN TO AZEROTH",
		"subtitle": "Return to original Azeroth and experience World of Warcraft in its timeless "
			"form. Earn each level, gather allies for dungeons, or find hidden corners.",
		"headline": "ADVENTURE BEGINS ANEW", "tagline": "RETURN TO THE WORLD THAT STARTED IT ALL",
		"slides": (
			("ADVENTURE BEGINS ANEW", "RETURN TO THE WORLD THAT STARTED IT ALL"),
			("EVERY LEVEL EARNED", "THE JOURNEY IS THE REWARD"),
			("GATHER YOUR ALLIES", "CLASSIC DUNGEONS AWAIT"),
			("HIDDEN CORNERS REMAIN", "SECRETS FOR THE CURIOUS"),
		),
		"play": "Play",
	},
	"Mists of Pandaria Classic": {
		"title": "THE MISTS ARE CALLING",
		"subtitle": "Journey back to Pandaria, a realm of serene forests, mist-covered peaks, and "
			"ancient conflicts. Master new challenges as danger hides behind the calm.",
		"headline": "JOURNEY THROUGH THE MISTS", "tagline": "PANDARIA AWAITS",
		"slides": (
			("JOURNEY THROUGH THE MISTS", "PANDARIA AWAITS"),
			("PEACE BEFORE THE STORM", "TRANQUIL LANDS, ANCIENT CONFLICT"),
			("MASTER NEW CHALLENGES", "WALK THE PATH OF THE PANDAREN"),
			("DANGER HIDES IN THE CALM", "STAND WITH YOUR ALLIES"),
		),
		"play": "Play",
	},
	"TBC Anniversary": {
		"title": "THROUGH THE DARK PORTAL",
		"subtitle": "Cross the Dark Portal and return to the broken world of Outland. Fight the "
			"Burning Legion beside your allies in dungeons and raids of Burning Crusade.",
		"headline": "STEP THROUGH THE DARK PORTAL", "tagline": "YOUR OUTLAND ADVENTURE BEGINS",
		"slides": (
			("STEP THROUGH THE DARK PORTAL", "YOUR OUTLAND ADVENTURE BEGINS"),
			("A SHATTERED WORLD", "EXPLORE THE REMNANTS OF DRAENOR"),
			("FACE THE BURNING LEGION", "DUNGEONS AND RAIDS AWAIT"),
			("LEGENDS ARE FORGED HERE", "CONQUER OUTLAND TOGETHER"),
		),
		"play": "Play",
	},
	"Crusader Storm": {
		"title": "THE BURNING CRUSADE, YOUR WAY",
		"subtitle": "Return to Outland on a free, BlizzLike Burning Crusade server. Level from 1 to "
			"70 at your own pace, team up across factions, and chase Hardcore goals.",
		"headline": "OUTLAND CALLS AGAIN", "tagline": "A BLIZZLIKE BURNING CRUSADE",
		"slides": (
			("OUTLAND CALLS AGAIN", "A BLIZZLIKE BURNING CRUSADE"),
			("LEVEL 1 TO 70", "YOUR PACE, YOUR JOURNEY"),
			("ALLIES ACROSS FACTIONS", "PLAY TOGETHER, PVP BY CHOICE"),
			("PROVE YOUR LEGEND", "ACHIEVEMENTS AND HARDCORE CHALLENGES"),
		),
		"play": "Play",
	},
}
GAME_THEMES = {
	"WoW Forever Beta": {
		"window": "#0a111b", "panel": "#151f2b", "panel_alt": "#101923",
		"surface": "#121c26", "control": "#20313d", "control_active": "#2d4655",
		"border": "#36768a", "accent": "#67cfe0", "accent_dark": "#286f82",
		"button_face": "#790c0a", "button_hover": "#a21712", "button_pressed": "#510704",
		"button_highlight": "#a52217", "button_shadow": "#510704",
		"accent_hover": "#318da0", "text": "#e4f0f2", "muted": "#a3bac1",
		"bright": "#f2fdff", "success": "#91d4b2", "warning": "#e4c17a",
		"error": "#d78478", "disabled": "#687983", "art_shadow": "#111923",
	},
	"Retail": {
		"window": "#100c19", "panel": "#21182d", "panel_alt": "#171222",
		"surface": "#1c1528", "control": "#302340", "control_active": "#45305d",
		"border": "#73549b", "accent": "#c29aff", "accent_dark": "#64428c",
		"button_face": "#790c0a", "button_hover": "#a21712", "button_pressed": "#510704",
		"button_highlight": "#a52217", "button_shadow": "#510704",
		"accent_hover": "#9564cf", "text": "#eee7f7", "muted": "#b8a9ca",
		"bright": "#fff7ff", "success": "#8fd7b7", "warning": "#e4c17a",
		"error": "#e08091", "disabled": "#776d84", "art_shadow": "#191323",
	},
	"Classic Era": {
		"window": "#17110c", "panel": "#2a2118", "panel_alt": "#1e1711",
		"surface": "#221a13", "control": "#3a2c20", "control_active": "#51402c",
		"border": "#8b7049", "accent": "#dfbd70", "accent_dark": "#79562f",
		"button_face": "#790c0a", "button_hover": "#a21712", "button_pressed": "#510704",
		"button_highlight": "#a52217", "button_shadow": "#510704",
		"accent_hover": "#a27631", "text": "#eee5d5", "muted": "#b9a98e",
		"bright": "#fff1ca", "success": "#a5c27c", "warning": "#e4b957",
		"error": "#d17c62", "disabled": "#7a7063", "art_shadow": "#1b1510",
	},
	"Mists of Pandaria Classic": {
		"window": "#111a18", "panel": "#1d2b26", "panel_alt": "#15211d",
		"surface": "#192520", "control": "#2c3d34", "control_active": "#405748",
		"border": "#71865d", "accent": "#dfc36e", "accent_dark": "#596d43",
		"button_face": "#790c0a", "button_hover": "#a21712", "button_pressed": "#510704",
		"button_highlight": "#a52217", "button_shadow": "#510704",
		"accent_hover": "#879858", "text": "#edf0dd", "muted": "#b0b99a",
		"bright": "#fff3c9", "success": "#9bc57b", "warning": "#e5bd67",
		"error": "#d17c62", "disabled": "#777a69", "art_shadow": "#17211d",
	},
	"TBC Anniversary": {
		"window": "#10170f", "panel": "#202b1b", "panel_alt": "#151e13",
		"surface": "#192318", "control": "#2d3c26", "control_active": "#405438",
		"border": "#71804b", "accent": "#d5b26e", "accent_dark": "#69552f",
		"button_face": "#790c0a", "button_hover": "#a21712", "button_pressed": "#510704",
		"button_highlight": "#a52217", "button_shadow": "#510704",
		"accent_hover": "#967b3f", "text": "#e8edda", "muted": "#aeb89b",
		"bright": "#fff1ca", "success": "#a5cb77", "warning": "#e6bb6f",
		"error": "#d17c62", "disabled": "#717b69", "art_shadow": "#141d12",
	},
	"Crusader Storm": {
		"window": "#10170f", "panel": "#202b1b", "panel_alt": "#151e13",
		"surface": "#192318", "control": "#2d3c26", "control_active": "#405438",
		"border": "#71804b", "accent": "#d5b26e", "accent_dark": "#69552f",
		"button_face": "#790c0a", "button_hover": "#a21712", "button_pressed": "#510704",
		"button_highlight": "#a52217", "button_shadow": "#510704",
		"accent_hover": "#967b3f", "text": "#e8edda", "muted": "#aeb89b",
		"bright": "#fff1ca", "success": "#a5cb77", "warning": "#e6bb6f",
		"error": "#d17c62", "disabled": "#717b69", "art_shadow": "#141d12",
	},
}
BASE_COLOR_ROLES = {
	"#100f12": "window", "#211d1b": "panel", "#211c18": "panel",
	"#181614": "surface", "#171512": "panel_alt", "#171820": "panel_alt",
	"#302922": "control", "#302a22": "control", "#473a27": "control_active",
	"#322a20": "control_active", "#51422c": "control_active", "#66512f": "control_active",
	"#745224": "accent_dark", "#a27631": "accent_hover", "#806b42": "border",
	"#8a7044": "border", "#51432f": "border", "#6f5b39": "border",
	"#78613c": "accent_dark", "#75613c": "border", "#756044": "border",
	"#b39554": "accent", "#c3a15c": "accent", "#a88653": "accent",
	"#e6c772": "bright", "#e2d3ab": "bright", "#b7a579": "muted",
	"#c7baa0": "muted", "#c9b987": "muted", "#fff1c7": "bright",
	"#e0c98c": "bright", "#fff0bd": "bright", "#e3c36e": "accent",
	"#e8dfcb": "text", "#a89d88": "muted", "#8fb978": "success",
	"#cf9c57": "warning", "#d17c62": "error", "#77736c": "disabled",
	"#fff": "bright", "white": "bright",
}
APP_TITLE = "World Of Warcraft Launcher"
APP_ICON_FILE = "wowicon.png"
APP_ICO_FILE = "wowicon.ico"
APP_ID = "WorldOfWarcraft.Launcher"
SCAN_MAX_DEPTH = 8
SCAN_HINTS = ("warcraft", "wow", "blizzard", "battle", "game", "classic", "retail", "beta")
SCAN_SKIP_NAMES = frozenset((
	"windows", "$recycle.bin", "system volume information", "programdata", "appdata",
	"node_modules", ".git", "recovery", "$windows.~bt", "$winreagent", "windowsapps",
	"winsxs", "perflogs", "msocache", "documents and settings", "intel", "amd",
	"library", "system", "private", "cores", ".trash", ".fseventsd", ".spotlight-v100"))
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
# Each game has its own four rotating screenshots in the screenshots folder:
# <prefix>screenshot1.png .. <prefix>screenshot4.png (e.g. classicscreenshot1.png).
SLIDESHOW_PREFIXES = {
	"Classic Era": "classic",
	"Retail": "retail",
	"TBC Anniversary": "tbc",
	"Crusader Storm": "tbc",   # a TBC-based client: shares the TBC set
	"Mists of Pandaria Classic": "mop",
	"WoW Forever Beta": "forever",
}
WOW_NEWS_URL = "https://worldofwarcraft.blizzard.com/en-us/news"
WOW_CLASSIC_NEWS_URL = "https://worldofwarcraft.blizzard.com/en-us/classic"
NEWS_TEXT_WIDTH = 395
NEWS_CARD_COUNT = 3   # news cards shown, on every platform (font metrics differ per OS)
NEWS_ID_PATTERN = re.compile(r"/news/(\d+)")
GENERIC_NEWS_TITLES = {"learn more", "read more", "view all", "read more stories", "more"}
VOID_TAGS = frozenset((
	"area", "base", "br", "col", "embed", "hr", "img", "input",
	"link", "meta", "source", "track", "wbr"))
NEWS_HEADERS = {
	"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
		"(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
	"Accept": "text/html,application/xhtml+xml",
	"Accept-Language": "en-US,en;q=0.9",
}
GAME_NEWS_TERMS = {
	"WoW Forever Beta": ("forever", "beta"),
	"Retail": ("midnight", "retail", "hotfix", "world of warcraft"),
	"Classic Era": ("classic era", "hardcore", "season of discovery", "hotfix", "classic"),
	"Mists of Pandaria Classic": (
		"mists of pandaria", "pandaria", "mop classic", "siege of orgrimmar", "hotfix"),
	"TBC Anniversary": (
		"tbc anniversary", "bcc", "burning crusade", "anniversary", "outland",
		"black temple", "hotfix"),
	"Crusader Storm": (
		"crusader storm", "burning crusade", "anniversary", "outland", "hotfix"),
}


def resource_path(*parts):
	"""Read-only bundled assets (images, fonts). Works from source and when compiled."""
	base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
	return base.joinpath(*parts)


def app_dir():
	"""Folder next to the .exe (or the script) for files that must persist, like settings."""
	if getattr(sys, "frozen", False):
		return Path(sys.executable).resolve().parent
	return Path(__file__).resolve().parent


def in_official_install(path):
	"""True if the path is inside a 'World of Warcraft' folder (Blizzard's install name)."""
	parts = re.split(r"[\\/]", str(path))[:-1]
	# A Mac app called "World of Warcraft.app" is a client, not the install folder.
	return any("world of warcraft" in part.casefold() and not part.casefold().endswith(".app")
			   for part in parts)


# Flavor folders inside "World of Warcraft": _retail_, _classic_era_, _classic_ (MoP Classic),
# _classic_beta_ (WoW Forever), _anniversary_ (TBC) and so on. Built from the auto-detect table.
OFFICIAL_FLAVOR_FOLDERS = {
	folder.casefold(): version
	for version in GAME_VERSIONS for folder in AUTO_DETECT_SUBDIRECTORIES[version]}


def official_game_for(path):
	"""Official game for a client at World of Warcraft/<flavor folder>/..., else None."""
	parts = re.split(r"[\\/]", str(path))
	for index, part in enumerate(parts):
		if "world of warcraft" in part.casefold() and not part.casefold().endswith(".app"):
			for child in parts[index + 1:-1]:
				game = OFFICIAL_FLAVOR_FOLDERS.get(child.casefold())
				if game is not None:
					return game
			return None
	return None


def flavor_game_for(path):
	"""Game implied by the client's flavor folder, only for official clients inside a
	'World of Warcraft' folder. Custom servers (Crusader Storm etc.) are never judged by
	a folder name, even if it looks like _classic_."""
	if not in_official_install(path):
		return None
	parts = re.split(r"[\\/]", str(path))
	if len(parts) >= 2:
		return OFFICIAL_FLAVOR_FOLDERS.get(parts[-2].casefold())
	return None


def path_key(path):
	"""Comparable form of a path (case-insensitive so Windows and macOS both match)."""
	return os.path.normcase(os.path.abspath(str(path))).casefold()


def client_path(executable):
	"""A running macOS client is .../Name.app/Contents/MacOS/Name; return the .app itself."""
	text = str(executable)
	index = text.lower().find(".app/")
	return text[:index + 4] if index != -1 else text


def is_wow_client(path):
	"""True for a WoW client: Wow*.exe on Windows, World of Warcraft*.app on macOS."""
	name = os.path.basename(str(path)).casefold()
	if name.endswith(".app"):
		return name.startswith("world of warcraft")
	return name.startswith("wow") and name.endswith(".exe")


def is_client_file(candidate):
	"""A client is a file, or on macOS an .app bundle (which is a folder)."""
	return candidate.is_file() or (
		candidate.suffix.casefold() == ".app" and candidate.is_dir())


def bundle_version(path):
	"""(major, minor, patch, build) from a macOS .app bundle's Info.plist, else None."""
	try:
		with open(Path(path) / "Contents" / "Info.plist", "rb") as handle:
			info = plistlib.load(handle)
	except Exception:
		return None
	text = str(info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or "")
	numbers = [int(part) for part in re.findall(r"\d+", text)][:4]
	return tuple(numbers + [0] * (4 - len(numbers))) if numbers else None


_EXE_VERSION_CACHE = {}


def exe_version(path):
	"""(major, minor, patch, build) from a Windows .exe's version resource, else None."""
	if os.name != "nt":
		return bundle_version(path) if str(path).casefold().endswith(".app") else None
	try:
		key = (str(path), Path(path).stat().st_mtime_ns)
	except OSError:
		return None
	if key in _EXE_VERSION_CACHE:
		return _EXE_VERSION_CACHE[key]
	result = None
	try:
		import ctypes
		from ctypes import wintypes
		version_dll = ctypes.WinDLL("version", use_last_error=True)
		get_size = version_dll.GetFileVersionInfoSizeW
		get_size.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD))
		get_size.restype = wintypes.DWORD
		get_info = version_dll.GetFileVersionInfoW
		get_info.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p)
		get_info.restype = wintypes.BOOL
		query = version_dll.VerQueryValueW
		query.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR,
						  ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT))
		query.restype = wintypes.BOOL
		size = get_size(str(path), None)
		if size:
			buffer = ctypes.create_string_buffer(size)
			pointer, length = ctypes.c_void_p(), wintypes.UINT()
			if (get_info(str(path), 0, size, buffer)
					and query(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length))
					and length.value >= 52):
				# VS_FIXEDFILEINFO: signature, struct version, then FileVersionMS / LS.
				fields = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32 * 4)).contents
				most, least = fields[2], fields[3]
				major_word, minor_word = most >> 16, most & 0xFFFF
				patch_word, build_digit = least >> 16, least & 0xFFFF
				if major_word >= 100:
					# WoW stores 1.15 as 115, 2.5 as 205, and 1.60 as 160.
					major, minor = divmod(major_word, 100)
					result = (major, minor, minor_word, patch_word * 10 + build_digit)
				else:
					result = (major_word, minor_word, patch_word, build_digit)
	except (AttributeError, OSError, ValueError):
		result = None
	_EXE_VERSION_CACHE[key] = result
	return result


# Where the floating panels sit (fractions of the window); shadows are drawn to match.
HERO_PLACE = {"relx": .045, "rely": .27, "relwidth": .91, "relheight": .56}
FOOTER_PLACE = {"relx": .045, "rely": .835, "relwidth": .91, "relheight": .125}
OPTIONS_PLACE = {"relx": .96, "rely": .045, "anchor": "ne"}
PANEL_SHADOW_LAYERS = (
	# (x offset, y offset, blur radius, opacity): a wide soft shadow plus a tight contact edge
	(0, 12, 20, 0.70),
	(0, 3, 4, 0.60),
)
BUTTON_SHADOW_PAD = 6  # transparent margin inside each button canvas that holds its shadow
BUTTON_SHADOW_LAYERS = (
	# (x offset, y offset, blur radius, opacity)
	(0, 2, 3, 0.70),
	(0, 1, 1, 0.85),
)
LOGO_SHADOW_LAYERS = (
	# (x offset, y offset, blur radius, opacity): a soft wide glow plus a tight dark edge
	(4, 6, 9, 0.75),
	(2, 3, 2.5, 0.95),
)


_PANEL_SHADOW_CACHE = {}
_BUTTON_SHADOW_CACHE = {}
_ROUNDED_MASK_CACHE = {}


def panel_shadow_overlay(size, rects, layers):
	"""Transparent RGBA image holding just the panel shadows; built once per size."""
	key = (size, tuple(rects), layers)
	overlay = _PANEL_SHADOW_CACHE.get(key)
	if overlay is None:
		overlay = Image.new("RGBA", size, (0, 0, 0, 0))
		for offset_x, offset_y, blur, opacity in layers:
			mask = Image.new("L", size, 0)
			draw = ImageDraw.Draw(mask)
			for left, top, right, bottom in rects:
				draw.rectangle((left + offset_x, top + offset_y,
								right + offset_x - 1, bottom + offset_y - 1), fill=255)
			mask = mask.filter(ImageFilter.GaussianBlur(blur)).point(
				lambda value, opacity=opacity: int(value * opacity))
			shadow = Image.new("RGBA", size, (0, 0, 0, 0))
			shadow.putalpha(mask)
			overlay.alpha_composite(shadow)
		_PANEL_SHADOW_CACHE[key] = overlay
	return overlay


def add_panel_shadows(image, rects, layers=PANEL_SHADOW_LAYERS):
	"""Darken the artwork around rectangles so widgets placed over it look like they float."""
	if not rects:
		return image
	image.alpha_composite(panel_shadow_overlay(image.size, rects, layers))
	return image


def button_shadow_overlay(size, points, layers):
	"""Shadow around a button outline with the button body left clear; cached per shape."""
	key = (size, tuple(points), layers)
	overlay = _BUTTON_SHADOW_CACHE.get(key)
	if overlay is None:
		overlay = Image.new("RGBA", size, (0, 0, 0, 0))
		for offset_x, offset_y, blur, opacity in layers:
			mask = Image.new("L", size, 0)
			shifted = [value + (offset_x if index % 2 == 0 else offset_y)
					   for index, value in enumerate(points)]
			ImageDraw.Draw(mask).polygon(shifted, fill=255)
			mask = mask.filter(ImageFilter.GaussianBlur(blur)).point(
				lambda value, opacity=opacity: int(value * opacity))
			shadow = Image.new("RGBA", size, (0, 0, 0, 0))
			shadow.putalpha(mask)
			overlay.alpha_composite(shadow)
		body = Image.new("L", size, 0)
		ImageDraw.Draw(body).polygon(list(points), fill=255)
		alpha = overlay.getchannel("A")
		alpha.paste(0, (0, 0), body)
		overlay.putalpha(alpha)
		if len(_BUTTON_SHADOW_CACHE) > 200:
			_BUTTON_SHADOW_CACHE.clear()
		_BUTTON_SHADOW_CACHE[key] = overlay
	return overlay


def add_button_shadow(image, points, layers=BUTTON_SHADOW_LAYERS):
	"""Darken `image` around a button outline (flat x, y point list), leaving the body clear."""
	image.alpha_composite(button_shadow_overlay(image.size, points, layers))
	return image


def add_drop_shadow(logo, layers=LOGO_SHADOW_LAYERS):
	"""Return (image, padding): the logo over a blurred black shadow, padded to fit it."""
	pad = int(max(blur for _x, _y, blur, _o in layers) * 3) + max(
		max(abs(x), abs(y)) for x, y, _b, _o in layers)
	size = (logo.width + pad * 2, logo.height + pad * 2)
	result = Image.new("RGBA", size, (0, 0, 0, 0))
	logo_alpha = logo.getchannel("A")
	for offset_x, offset_y, blur, opacity in layers:
		mask = Image.new("L", size, 0)
		mask.paste(logo_alpha, (pad + offset_x, pad + offset_y))
		mask = mask.filter(ImageFilter.GaussianBlur(blur)).point(
			lambda value, opacity=opacity: int(value * opacity))
		shadow = Image.new("RGBA", size, (0, 0, 0, 0))
		shadow.putalpha(mask)
		result.alpha_composite(shadow)
	result.alpha_composite(logo, (pad, pad))
	return result, pad


def hex_rgb(color):
	return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


def shade(color, factor):
	"""Lighten (factor > 0) or darken (factor < 0) a #rrggbb color."""
	red, green, blue = hex_rgb(color)
	if factor >= 0:
		channels = [round(value + (255 - value) * factor) for value in (red, green, blue)]
	else:
		channels = [round(value * (1 + factor)) for value in (red, green, blue)]
	return "#%02x%02x%02x" % tuple(channels)


def blend_hex(start, end, fraction):
	"""Blend two #rrggbb colors; fraction 0 gives `start`, 1 gives `end`."""
	fraction = min(1.0, max(0.0, fraction))
	return "#%02x%02x%02x" % tuple(
		round(a + (b - a) * fraction) for a, b in zip(hex_rgb(start), hex_rgb(end)))


def make_gradient(width, height, top, bottom):
	"""Vertical gradient between two RGB tuples."""
	column = Image.new("RGB", (1, height))
	column.putdata([
		tuple(round(a + (b - a) * (y / max(1, height - 1))) for a, b in zip(top, bottom))
		for y in range(height)])
	return column.resize((width, height), Image.Resampling.NEAREST)


def make_rounded_mask(width, height, radius, scale=3):
	"""Build a smooth rounded-rectangle mask by supersampling only the four corners."""
	radius = max(1, min(radius, width // 2, height // 2))
	extent = radius + 1
	corner_size = extent * 2
	large_size = corner_size * scale
	corner = Image.new("L", (large_size, large_size), 0)
	ImageDraw.Draw(corner).rounded_rectangle(
		(0, 0, large_size - 1, large_size - 1),
		radius=radius * scale, fill=255)
	corner = corner.resize((corner_size, corner_size), Image.Resampling.LANCZOS)
	corner = corner.crop((0, 0, extent, extent))
	mask = Image.new("L", (width, height), 0)
	mask.paste(corner, (0, 0))
	mask.paste(corner.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (width - extent, 0))
	mask.paste(corner.transpose(Image.Transpose.FLIP_TOP_BOTTOM), (0, height - extent))
	mask.paste(corner.transpose(Image.Transpose.ROTATE_180),
				(width - extent, height - extent))
	draw = ImageDraw.Draw(mask)
	if width > 2 * extent:
		draw.rectangle((extent, 0, width - extent - 1, height - 1), fill=255)
	if height > 2 * extent:
		draw.rectangle((0, extent, width - 1, height - extent - 1), fill=255)
	return mask


def split_launch_args(text):
	"""Launch-argument text -> list of arguments. Spaces separate arguments and quotes
	keep a value with spaces together, e.g.  -config "C:\\My Games\\Config.wtf"."""
	text = (text or "").strip()
	if not text:
		return []
	try:
		parts = shlex.split(text, posix=False)   # posix=False keeps Windows backslashes
	except ValueError:                           # unbalanced quote: fall back to plain spaces
		parts = text.split()
	return [part[1:-1] if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'" else part
			for part in parts]


class OfficialNewsParser(HTMLParser):
	"""Extract official WoW article cards (ArticleTile divs or blz-card elements)."""

	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.items = []
		self.current = None
		self.root_tag = None
		self.tile_depth = 0
		self.capture_name = None
		self.capture_tag = None
		self.capture_depth = 0
		self.capture_parts = []

	def handle_starttag(self, tag, attrs):
		attributes = dict(attrs)
		classes = set((attributes.get("class") or "").split())
		if self.current is None:
			if tag == "div" and "ArticleTile" in classes:
				self.current = {"title": "", "summary": "", "url": ""}
				self.root_tag = "div"
				self.tile_depth = 1
			elif tag == "blz-card":
				self.current = {"title": "", "summary": "", "url": ""}
				self.root_tag = "blz-card"
				self.tile_depth = 1
			return
		if tag in VOID_TAGS:
			return
		if tag == self.root_tag:
			self.tile_depth += 1
		if self.capture_name is not None:
			self.capture_depth += 1
		href = attributes.get("href") or ""
		if tag == "a" and "ArticleTile-link" in classes and href:
			self.current["url"] = href
		elif (tag in ("a", "blz-button") and NEWS_ID_PATTERN.search(href)
				and not self.current["url"]):
			self.current["url"] = href

		field = None
		if tag == "div" and "ArticleTile-title" in classes:
			field = "title"
		elif tag == "div" and "ArticleTile-subtitle" in classes:
			field = "summary"
		elif tag == "span" and attributes.get("slot") == "heading":
			field = "title"
		elif tag == "span" and attributes.get("slot") == "description":
			field = "summary"
		if field:
			self.capture_name = field
			self.capture_tag = tag
			self.capture_depth = 0
			self.capture_parts = []

	def handle_endtag(self, tag):
		if self.current is None or tag in VOID_TAGS:
			return
		if self.capture_name is not None:
			if tag == self.capture_tag and self.capture_depth == 0:
				self.current[self.capture_name] = " ".join(
					" ".join(self.capture_parts).split())
				self.capture_name = None
				self.capture_tag = None
				self.capture_parts = []
			elif self.capture_depth:
				self.capture_depth -= 1
		if tag == self.root_tag:
			self.tile_depth -= 1
			if self.tile_depth <= 0:
				if self.current["title"] and self.current["url"]:
					self.items.append(self.current)
				self.current = None
				self.root_tag = None
				self.capture_name = None

	def handle_data(self, data):
		if self.current is not None and self.capture_name is not None:
			self.capture_parts.append(data)


class NewsLinkParser(HTMLParser):
	"""Fallback: collect any link to /news/<id> together with its visible text."""

	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.items = []
		self.tag = None
		self.depth = 0
		self.href = ""
		self.label = ""
		self.parts = []

	def handle_starttag(self, tag, attrs):
		if tag in VOID_TAGS:
			return
		if self.tag is not None:
			if tag == self.tag:
				self.depth += 1
			return
		attributes = dict(attrs)
		href = attributes.get("href") or ""
		if tag in ("a", "blz-button") and NEWS_ID_PATTERN.search(href):
			self.tag = tag
			self.depth = 0
			self.href = href
			self.label = attributes.get("aria-label") or attributes.get("title") or ""
			self.parts = []

	def handle_endtag(self, tag):
		if tag != self.tag:
			return
		if self.depth:
			self.depth -= 1
			return
		title = " ".join(" ".join(self.parts).split()) or " ".join(self.label.split())
		if title and title.casefold() not in GENERIC_NEWS_TITLES and not title.isdigit():
			self.items.append({"title": title, "summary": "", "url": self.href})
		self.tag = None

	def handle_data(self, data):
		if self.tag is not None:
			self.parts.append(data)


BUTTON_STONE = "#626663"
BUTTON_STONE_LIGHT = "#929691"
BUTTON_STONE_DARK = "#393c3a"
BUTTON_RED = "#790c0a"
BUTTON_RED_HOVER = "#a21712"
BUTTON_RED_PRESSED = "#510704"
BUTTON_TEXT = "#ffe05b"
BUTTON_TEXT_OUTLINE = "#100b05"


class StoneButton(tk.Canvas):
	"""A clickable launcher control styled like a beveled stone game button."""

	def __init__(self, parent, text, command, font, padx=16, pady=7,
				 background_provider=None, theme_provider=None, style="stone", version_colored=False, **kwargs):
		self.style = style
		self.version_colored = version_colored
		self.label = text
		self.command = command
		self.button_font = font
		self.padx = padx
		self.pady = pady
		self.hovered = False
		self.pressed = False
		self.background_provider = background_provider
		self.disabled = False
		self.theme_provider = theme_provider or (lambda: {})
		super().__init__(parent, bg=kwargs.pop("bg", "#181614"),
					 highlightthickness=0, bd=0, cursor="hand2", takefocus=True,
					 **kwargs)
		self.bind("<Configure>", self.redraw)
		self.bind("<Enter>", self.on_enter)
		self.bind("<Leave>", self.on_leave)
		self.bind("<ButtonPress-1>", self.on_press)
		self.bind("<ButtonRelease-1>", self.on_release)
		self.bind("<space>", self.on_key_press)
		self.bind("<KeyRelease-space>", self.on_key_release)
		self.bind("<Return>", self.invoke)
		self.bind("<Button-1>", lambda _event: self.focus_set(), add="+")
		self.bind("<FocusIn>", self.redraw)
		self.bind("<FocusOut>", self.redraw)
		self.configure(
			width=self.button_font.measure(text) + padx * 2 + 24 + BUTTON_SHADOW_PAD * 2,
			height=self.button_font.metrics("linespace") + pady * 2 + 18 + BUTTON_SHADOW_PAD * 2)
		self.redraw()

	def redraw(self, _event=None):
		if not self.winfo_exists():
			return
		width, height = self.winfo_width(), self.winfo_height()
		if width < 4 or height < 4:
			return
		self.delete("all")
		button_theme = self.theme_provider()
		if self.version_colored:
			# Popup buttons always use the version palette, even if popup styling resets style.
			self.style = "subtle"
			button_theme = dict(button_theme)
			for button_role, version_role in (("button_face", "control"),
				("button_hover", "control_active"), ("button_pressed", "window"),
				("button_highlight", "accent"), ("button_shadow", "accent_dark")):
				button_theme[button_role] = button_theme[version_role]
		background = None
		if self.background_provider is not None:
			background = self.background_provider(self, width, height)
		if self.style == "subtle":
			self.draw_subtle(width, height, button_theme, background)
			return
		inset = 2 + BUTTON_SHADOW_PAD
		cut = min(7, max(4, height // 10))
		shape = (inset + cut, inset, width - inset - cut, inset,
				width - inset, inset + cut, width - inset, height - inset - cut,
				width - inset - cut, height - inset, inset + cut, height - inset,
				inset, height - inset - cut, inset, inset + cut)
		self.paint_backdrop(width, height, shape, background)
		self.create_polygon(*shape, fill=BUTTON_STONE_DARK, outline=BUTTON_STONE_DARK)
		self.create_polygon(
			inset + cut + 1, inset + 3, width - inset - cut - 1, inset + 3,
			width - inset - 3, inset + cut + 1,
			width - inset - 3, height - inset - cut - 1,
			width - inset - cut - 1, height - inset - 3,
			inset + cut + 1, height - inset - 3,
			inset + 3, height - inset - cut - 1,
			inset + 3, inset + cut + 1,
			fill=BUTTON_STONE, outline=BUTTON_STONE_LIGHT, width=1)
		self.create_line(inset + cut + 2, inset + 4,
					 width - inset - cut - 2, inset + 4,
					 fill="#a2a49f", width=1)
		face_points = (
			inset + cut + 2, inset + 6, width - inset - cut - 2, inset + 6,
			width - inset - 6, inset + cut + 2, width - inset - 6,
			height - inset - cut - 2, width - inset - cut - 2, height - inset - 6,
			inset + cut + 2, height - inset - 6,
			inset + 6, height - inset - cut - 2, inset + 6, inset + cut + 2,
		)
		face = "#4a4a48" if self.disabled else button_theme.get("button_pressed", BUTTON_RED_PRESSED) if self.pressed else (
			button_theme.get("button_hover", BUTTON_RED_HOVER) if self.hovered
			else button_theme.get("button_face", BUTTON_RED))
		face_gradient = make_gradient(
			width, height, hex_rgb(shade(face, .2)), hex_rgb(shade(face, -.3)))
		face_mask = Image.new("L", (width, height), 0)
		ImageDraw.Draw(face_mask).polygon(face_points, fill=255)
		face_image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
		face_image.paste(face_gradient, (0, 0), face_mask)
		self._face_photo = ImageTk.PhotoImage(face_image, master=self)
		self.create_image(0, 0, image=self._face_photo, anchor="nw")
		self.create_polygon(*face_points, fill="", outline="#170d0b", width=1)
		self.create_line(inset + cut + 4, inset + 7,
					 width - inset - cut - 4, inset + 7,
					 fill=button_theme.get("button_highlight", "#a52217"), width=1)
		self.create_line(inset + 9, inset + cut + 5, inset + 9,
					 height - inset - cut - 5,
					 fill=button_theme.get("button_shadow", BUTTON_RED_PRESSED), width=1)

		x, y = width / 2, height / 2 + (1 if self.pressed else 0)
		for offset_x, offset_y in ((-1, -1), (0, -1), (1, -1),
								  (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
			self.create_text(x + offset_x, y + offset_y, text=self.label,
							  fill=BUTTON_TEXT_OUTLINE, font=self.button_font)
		self.create_text(x, y, text=self.label, font=self.button_font,
							  fill="#9a9c97" if self.disabled else BUTTON_TEXT)

	def shadowed_backdrop(self, width, height, outline_points, background):
		"""Backdrop image (art behind the button, or its solid color) with the drop shadow."""
		if background is None:
			try:
				red, green, blue = (value >> 8 for value in self.winfo_rgb(self.cget("bg")))
			except tk.TclError:
				red, green, blue = 24, 22, 20
			background = Image.new("RGBA", (width, height), (red, green, blue, 255))
		return add_button_shadow(background, outline_points)

	def paint_backdrop(self, width, height, outline_points, background):
		base = self.shadowed_backdrop(width, height, outline_points, background)
		self._background_patch = ImageTk.PhotoImage(base, master=self)
		self.create_image(0, 0, image=self._background_patch, anchor="nw")

	def draw_subtle(self, width, height, button_theme, background):
		"""Understated style: translucent themed glass with a thin double-line frame."""
		def role(name, fallback):
			return button_theme.get(name, fallback)

		def outline(inset, cut):
			return (inset + cut, inset, width - inset - cut, inset,
					width - inset, inset + cut, width - inset, height - inset - cut,
					width - inset - cut, height - inset, inset + cut, height - inset,
					inset, height - inset - cut, inset, inset + cut)

		cut = min(6, max(3, height // 10))
		outer = outline(2 + BUTTON_SHADOW_PAD, cut)
		if self.pressed:
			face, alpha = role("window", BG), 245
		elif self.hovered:
			face, alpha = role("control_active", "#473a27"), 235
		else:
			face, alpha = role("panel", PANEL), 215
		base = self.shadowed_backdrop(width, height, outer, background)
		glass = Image.new("RGBA", (width, height), (0, 0, 0, 0))
		rgb = tuple(int(face[index:index + 2], 16) for index in (1, 3, 5))
		ImageDraw.Draw(glass).polygon(outer, fill=(*rgb, alpha))
		base.alpha_composite(glass)
		self._background_patch = ImageTk.PhotoImage(base, master=self)
		self.create_image(0, 0, image=self._background_patch, anchor="nw")
		frame = role("accent", GOLD) if self.hovered else role("border", "#806b42")
		self.create_polygon(*outer, fill="", outline=frame, width=1)
		self.create_polygon(*outline(5 + BUTTON_SHADOW_PAD, max(2, cut - 2)), fill="",
							outline=role("accent_dark", "#78613c"), width=1)

		x, y = width / 2, height / 2 + (1 if self.pressed else 0)
		text_color = role("bright", "#fff1c7") if self.hovered else role("accent", GOLD)
		for offset_x, offset_y in ((-1, 0), (1, 0), (0, -1), (0, 1)):
			self.create_text(x + offset_x, y + offset_y, text=self.label,
							 fill=BUTTON_TEXT_OUTLINE, font=self.button_font)
		self.create_text(x, y, text=self.label, fill=text_color, font=self.button_font)

	def on_enter(self, _event=None):
		self.hovered = True
		self.redraw()

	def on_leave(self, _event=None):
		self.hovered = False
		self.pressed = False
		self.redraw()

	def on_press(self, _event=None):
		if self.disabled:
			return
		self.pressed = True
		self.redraw()

	def on_release(self, event):
		was_pressed = self.pressed
		self.pressed = False
		self.redraw()
		if was_pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
			self.invoke()

	def on_key_press(self, _event=None):
		self.pressed = True
		self.redraw()
		return "break"

	def on_key_release(self, _event=None):
		self.pressed = False
		self.redraw()
		self.invoke()
		return "break"

	def invoke(self, _event=None):
		if not self.disabled:
			self.command()
		return "break"

	def set_label(self, text):
		self.label = text
		self.redraw()

	def set_disabled(self, disabled):
		if self.disabled != disabled:
			self.disabled = disabled
			self.configure(cursor="arrow" if disabled else "hand2")
			self.redraw()


class GradientSurface:
	"""Paints a subtle vertical gradient behind a frame and the widgets sitting on it."""

	def __init__(self, root, theme_provider, role, lift=.05, drop=.10):
		self.root = root
		self.theme_provider = theme_provider
		self.role = role
		self.base = str(root.cget("bg"))
		self.lift = lift
		self.drop = drop
		self.members = []
		self.image = None
		self.origin = (0, 0)
		self.top = self.bottom = (0, 0, 0)
		self.dead = False
		self.after_id = None
		self._gradient_key = None
		root.bind("<Destroy>", self.on_destroy, add="+")
		self.track(root, paint=False)

	def on_destroy(self, event):
		if event.widget is self.root:
			self.dead = True
			if self.after_id is not None:
				try:
					self.root.after_cancel(self.after_id)
				except tk.TclError:
					pass

	def adopt(self):
		"""Take over every Frame/Label using the section's flat color; hook up buttons."""
		stack = list(self.root.winfo_children())
		while stack:
			widget = stack.pop()
			stack.extend(widget.winfo_children())
			if getattr(widget, "_gradient_bg", False):
				continue
			try:
				same = str(widget.cget("bg")).casefold() == self.base.casefold()
			except tk.TclError:
				continue
			if not same:
				continue
			if isinstance(widget, StoneButton):
				if widget.background_provider is None:
					widget.background_provider = self.backdrop
			elif isinstance(widget, (tk.Frame, tk.Label)):
				self.track(widget, paint=False)
		self.schedule()

	def track(self, widget, paint=True):
		if widget in self.members:
			return
		self.members.append(widget)
		if isinstance(widget, tk.Frame):
			label = tk.Label(widget, bd=0, highlightthickness=0, bg=self.base)
			label._gradient_bg = True
			label.place(x=0, y=0, relwidth=1, relheight=1)
			label.lower()
			widget._gradient_label = label
			widget.bind("<Configure>", self.schedule, add="+")
		if paint:
			self.schedule()

	def schedule(self, _event=None):
		if self.dead or self.after_id is not None:
			return
		try:
			self.after_id = self.root.after(25, self.paint)
		except tk.TclError:
			pass

	def color_at(self, fraction):
		fraction = min(1.0, max(0.0, fraction))
		return "#%02x%02x%02x" % tuple(
			round(a + (b - a) * fraction) for a, b in zip(self.top, self.bottom))

	def paint(self):
		self.after_id = None
		if self.dead:
			return
		try:
			self.root.update_idletasks()
			anchor = self.root._gradient_label
			width, height = anchor.winfo_width(), anchor.winfo_height()
			if width < 4 or height < 4:
				return
			base = self.theme_provider().get(self.role, self.base)
			self.top = hex_rgb(shade(base, self.lift))
			self.bottom = hex_rgb(shade(base, -self.drop))
			key = (width, height, self.top, self.bottom)
			if key != self._gradient_key:
				self.image = make_gradient(width, height, self.top, self.bottom)
				self._gradient_key = key
			self.origin = (anchor.winfo_rootx(), anchor.winfo_rooty())
			live = []
			for widget in self.members:
				if not widget.winfo_exists():
					continue
				live.append(widget)
				if isinstance(widget, tk.Frame):
					self.paint_frame(widget)
				else:
					self.paint_label(widget)
			self.members = live
			stack = list(self.root.winfo_children())
			while stack:
				widget = stack.pop()
				stack.extend(widget.winfo_children())
				if isinstance(widget, StoneButton) and widget.background_provider == self.backdrop:
					widget.redraw()
		except tk.TclError:
			pass

	def paint_frame(self, frame):
		label = frame._gradient_label
		width, height = label.winfo_width(), label.winfo_height()
		if width < 2 or height < 2:
			return
		x = label.winfo_rootx() - self.origin[0]
		y = label.winfo_rooty() - self.origin[1]
		signature = (x, y, width, height, self._gradient_key)
		if getattr(label, "_signature", None) == signature:
			return
		patch = self.image.crop((x, y, x + width, y + height))
		label._photo = ImageTk.PhotoImage(patch, master=label)
		label.configure(image=label._photo)
		label._signature = signature

	def paint_label(self, widget):
		center = widget.winfo_rooty() + widget.winfo_height() / 2 - self.origin[1]
		color = self.color_at(center / max(1, self.image.height))
		if str(widget.cget("bg")).casefold() != color:
			widget.configure(bg=color)

	def backdrop(self, widget, width, height):
		"""Gradient patch behind a StoneButton (used as its background_provider)."""
		if self.image is None:
			return None
		x = widget.winfo_rootx() - self.origin[0]
		y = widget.winfo_rooty() - self.origin[1]
		if x < 0 or y < 0 or x + width > self.image.width or y + height > self.image.height:
			return None
		return self.image.crop((x, y, x + width, y + height)).convert("RGBA")


class LauncherUI:
	def __init__(self, root):
		self.root = root
		root.launcher = self   # lets popups read the current theme
		root.title(APP_TITLE)
		root.geometry("1100x700")
		root.minsize(860, 580)
		root.resizable(False, False)
		root.configure(bg=BG)
		self._app_icons = []
		self._win_icons = None
		self.set_app_icon()
		self.settings_path = app_dir() / "launcher_settings.json"
		self.news_queue = queue.Queue()
		self.news_generation = 0
		self.news_anim_generation = 0
		self.news_cards = []
		self.hero_text_generation = 0
		self.art_text_generation = 0
		self.art_transitioning = False
		self.news_poll_id = None
		self.last_news = None
		self.news_layout_height = 0
		self.news_resize_id = None
		self.selection_generation = 0
		self.dynamic_versions = {}
		self.game_paths = self.load_settings()
		self.discovered_versions = self.discover_game_versions()
		self.game_versions = self.compute_game_versions()
		self.game_copy = GAME_COPY[GAME_VERSIONS[0]]
		self.art_text = (self.game_copy["headline"], self.game_copy["tagline"])
		self.art_text_fade = 1.0
		self.running_version = None
		self.session_started_at = None
		self.scan_results = queue.Queue()
		self.learn_requests = queue.Queue()
		self.scan_running = False
		self.last_scan_request = float("-inf")
		self.watches = []
		self.last_playtime_poll = time.monotonic()
		self.last_playtime_save = self.last_playtime_poll
		self.playtime_poll_id = None
		self._font_cache = {}
		self._registered_font_paths = set()
		self._font_family = "Friz Quadrata"
		self.theme = GAME_THEMES[GAME_VERSIONS[0]]
		self.surfaces = []
		self.register_friz_fonts()
		self.refresh_friz_font()
		self.game_logos = {}
		self.game_backgrounds = {}
		self._background_cache = {}
		self._prewarmed = set()
		self._background_executor = ThreadPoolExecutor(max_workers=1)
		self._background_future = None
		self._background_queue = queue.Queue()
		self._background_frames = None
		self._background_frame_index = 0
		self._background_requested = None
		self._background_poll_id = None
		self._slide_cache = {}
		self._shaded_cache = {}
		self._art_mask_cache = {}
		self._news_page_cache = {}
		self.fallback_logo = self.load_image(resource_path("wow_logo.png"), "RGBA", (1100, 340))
		self.base_background = self.load_image(resource_path("image.png"), "RGBA", (2200, 1400))
		self.active_art_version = GAME_VERSIONS[0]
		self.displayed_background = None
		self.options_button = None
		self.background_transition_id = None
		self.background_transition_generation = 0
		self._slideshow_cache = {}   # screenshot set key -> loaded images
		self.slideshow_key = SLIDESHOW_PREFIXES.get(GAME_VERSIONS[0], "")
		self.slideshow_images = self.slideshow_for(GAME_VERSIONS[0])
		self.slideshow_index = 0
		self.art_photo = None
		self.current_art_frame = None
		self.art_transition_generation = 0
		self.slideshow_after_id = None

		self.background = tk.Canvas(root, bg=BG, highlightthickness=0)
		self.background.place(relwidth=1, relheight=1)
		self.background.bind("<Configure>", self.draw_background)

		self.build_header()
		self.build_hero()
		self.build_footer()
		self.updates = UpdateController(self, excluded=LOADER_VERSIONS)
		if self.slideshow_images:
			self.slideshow_after_id = self.root.after(5000, self.advance_slideshow)
		self.status = tk.Label(root, text="Launcher ready", bg=BG, fg=MUTED,
							   font=self.ui_font(8))
		self.status.place(relx=.046, rely=.985, anchor="sw")
		self.root.protocol("WM_DELETE_WINDOW", self.close_launcher)
		self.playtime_poll_id = self.root.after(1000, self.poll_game_processes)
		self.update_news_age()
		self.news_poll_id = self.root.after(250, self.poll_news_queue)

		self._background_poll_id = self.root.after(30, self.poll_background_queue)
		self.update_install_status()
		self.apply_theme(GAME_VERSIONS[0])
		self.update_game_copy()
		self.load_game_news()
		if self.discovered_versions:
			if self.persist_settings():
				games = ", ".join(self.discovered_versions)
				self.set_status(f"New game installs detected: {games}")
		if not self.setup_complete:
			self.root.after(400, self.open_setup_wizard)

	def set_app_icon(self):
		"""Set a crisp window/taskbar icon (Win32 API on Windows, PNG on Linux and when
		running from source on macOS; the built macOS app keeps its bundle icon)."""
		if os.name == "nt":
			if self.set_windows_icon():
				# Tk can reapply its default icon when the window first maps; set again.
				self.root.after(300, self.set_windows_icon)
				return
		if IS_MAC and getattr(sys, "frozen", False):
			# The .app bundle already carries the .icns icon (set in the spec). Calling
			# iconphoto here would replace the Dock icon with the flat PNG.
			return
		icon = self.load_image(resource_path(APP_ICON_FILE), "RGBA")
		if icon is None:
			return
		self._app_icons = [
			ImageTk.PhotoImage(icon.resize((size, size), Image.Resampling.LANCZOS),
							   master=self.root)
			for size in (256, 64, 48, 32, 16)]
		try:
			self.root.iconphoto(True, *self._app_icons)
		except tk.TclError:
			pass

	def set_windows_icon(self, window=None):
		"""Load real 256px + small frames from the .ico and hand them straight to Windows."""
		window = window or self.root
		ico_path = resource_path(APP_ICO_FILE)
		if not ico_path.is_file():
			return False
		try:
			import ctypes
			from ctypes import wintypes
			user32 = ctypes.WinDLL("user32", use_last_error=True)
			user32.LoadImageW.argtypes = (
				wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
				ctypes.c_int, ctypes.c_int, wintypes.UINT)
			user32.LoadImageW.restype = wintypes.HANDLE
			user32.GetParent.argtypes = (wintypes.HWND,)
			user32.GetParent.restype = wintypes.HWND
			user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
			user32.SendMessageW.argtypes = (
				wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
			user32.SendMessageW.restype = ctypes.c_ssize_t

			window.update_idletasks()
			hwnd = user32.GetParent(window.winfo_id())
			if not hwnd:
				return False
			if self._win_icons is None:
				small_size = user32.GetSystemMetrics(49) or 16  # SM_CXSMICON
				image_icon, load_from_file = 1, 0x10
				big = user32.LoadImageW(
					None, str(ico_path), image_icon, 256, 256, load_from_file)
				small = user32.LoadImageW(
					None, str(ico_path), image_icon, small_size, small_size, load_from_file)
				if not big or not small:
					return False
				self._win_icons = (big, small)  # keep handles alive
			big, small = self._win_icons
			wm_seticon = 0x0080
			user32.SendMessageW(hwnd, wm_seticon, 1, big)    # ICON_BIG
			user32.SendMessageW(hwnd, wm_seticon, 0, small)  # ICON_SMALL
			return True
		except (AttributeError, OSError, tk.TclError):
			return False

	def add_gradient(self, frame, role, lift=.05, drop=.10):
		self.surfaces = [surface for surface in self.surfaces if not surface.dead]
		surface = GradientSurface(frame, lambda: self.theme, role, lift, drop)
		self.surfaces.append(surface)
		surface.adopt()
		return surface

	def add_rounded_surface(self, frame, role, radius=8, outside_role=None, backdrop=None):
		"""Round a frame's corners. `outside_role` names the theme color shown in the corners
		(default: the parent's color); `backdrop` returns a PIL image the size of the parent
		to show in the corners instead (used by the popup panels)."""
		outside = self.theme[outside_role] if outside_role else str(frame.master.cget("bg"))
		frame.configure(bg=outside, bd=0, highlightthickness=0)
		canvas = tk.Canvas(frame, bg=outside, highlightthickness=0, bd=0)
		canvas.place(x=0, y=0, relwidth=1, relheight=1)
		canvas.tk.call("lower", canvas._w)
		section = {"frame": frame, "canvas": canvas, "role": role,
				   "outside": outside, "radius": radius, "backdrop_photo": None,
				   "outside_role": outside_role, "backdrop": backdrop}
		if not hasattr(self, "rounded_sections"):
			self.rounded_sections = []
			self.root.bind("<Configure>", self.refresh_rounded_surfaces, add="+")
		self.rounded_sections.append(section)
		frame.bind("<Configure>", self.refresh_rounded_surfaces, add="+")
		self.draw_rounded_surface(section)
		return section

	def draw_rounded_surface(self, section):
		frame, canvas = section["frame"], section["canvas"]
		if not frame.winfo_exists():
			return
		width, height = frame.winfo_width(), frame.winfo_height()
		if width < 4 or height < 4:
			return
		theme = self.theme
		if section.get("outside_role"):
			section["outside"] = theme[section["outside_role"]]
		else:
			section["outside"] = str(frame.master.cget("bg"))
		fill = theme[section["role"]]
		border = blend_hex(fill, theme["border"], .32)
		position = (frame.winfo_x(), frame.winfo_y())
		if section.get("backdrop") is not None:
			base_image = section["backdrop"]()
		elif frame.master is self.root:
			base_image = self.displayed_background
		else:
			base_image = None
		backdrop_key = id(base_image) if base_image is not None else None
		signature = (width, height, position, section["outside"], fill, border, backdrop_key)
		if section.get("signature") == signature:
			return
		canvas.configure(bg=section["outside"], width=width, height=height)
		canvas.delete("all")
		if base_image is not None:
			x, y = frame.winfo_x(), frame.winfo_y()
			x2 = min(x + width, base_image.width)
			y2 = min(y + height, base_image.height)
			if x >= 0 and y >= 0 and x2 > x and y2 > y:
				backdrop = base_image.crop((x, y, x2, y2))
				if backdrop.size != (width, height):
					padded = Image.new("RGBA", (width, height), section["outside"])
					padded.alpha_composite(backdrop, (0, 0))
					backdrop = padded
				section["backdrop_photo"] = ImageTk.PhotoImage(backdrop, master=canvas)
				canvas.create_image(0, 0, image=section["backdrop_photo"], anchor="nw")
		mask_key = (width, height, section["radius"])
		masks = _ROUNDED_MASK_CACHE.get(mask_key)
		if masks is None:
			outer = make_rounded_mask(width, height, section["radius"])
			inner = make_rounded_mask(width - 2, height - 2,
									  max(1, section["radius"] - 1))
			padded_inner = Image.new("L", (width, height), 0)
			padded_inner.paste(inner, (1, 1))
			inner = padded_inner
			masks = (inner, ImageChops.subtract(outer, inner))
			if len(_ROUNDED_MASK_CACHE) >= 12:
				_ROUNDED_MASK_CACHE.clear()
			_ROUNDED_MASK_CACHE[mask_key] = masks
		image = Image.new("RGBA", (width, height), (*hex_rgb(fill), 0))
		image.putalpha(masks[0])
		outline = Image.new("RGBA", (width, height), (*hex_rgb(border), 0))
		outline.putalpha(masks[1])
		image.alpha_composite(outline)
		section["photo"] = ImageTk.PhotoImage(image, master=canvas)
		canvas.create_image(0, 0, image=section["photo"], anchor="nw")
		canvas.tk.call("lower", canvas._w)
		section["signature"] = signature

	def refresh_rounded_surfaces(self, _event=None):
		if not hasattr(self, "rounded_sections"):
			return
		alive = []
		for section in self.rounded_sections:
			try:
				if not section["frame"].winfo_exists():
					continue  # its popup was closed
			except tk.TclError:
				continue
			try:
				self.draw_rounded_surface(section)
			except tk.TclError:
				pass  # try again on the next refresh instead of giving up on it
			alive.append(section)
		self.rounded_sections[:] = alive

	def style_popup(self, window, body):
		"""The Overlay paints its own rounded, bordered panel; nothing to add."""

	def style_popup_row(self, row):
		"""A recessed rounded strip, like the playtime box on the main screen."""
		self.add_rounded_surface(row, "surface", radius=7, outside_role="panel")

	def clear_children(self, frame):
		for child in frame.winfo_children():
			if not getattr(child, "_gradient_bg", False):
				child.destroy()

	def build_header(self):
		self.update_button = StoneButton(
			self.root, text="UPDATE AVAILABLE", command=lambda: self.updates.check_selected(),
			font=self.ui_font(8, bold=True), padx=10, pady=4, bg=BG,
			background_provider=self.backdrop_patch, theme_provider=lambda: self.theme,
			style="subtle")
		self.update_button.place(relx=.96, rely=.045, anchor="ne")
		self.armory_button = StoneButton(
			self.root, text="ARMORY", command=self.open_armory,
			font=self.ui_font(8, bold=True), padx=10, pady=4, bg=BG,
			background_provider=self.backdrop_patch, theme_provider=lambda: self.theme,
			style="subtle")
		self.armory_button.place(relx=.045, rely=.045, anchor="nw")

	def backdrop_patch(self, widget, width, height):
		if self.displayed_background is None:
			return None
		left = max(0, widget.winfo_x())
		top = max(0, widget.winfo_y())
		right = min(self.displayed_background.width, left + width)
		bottom = min(self.displayed_background.height, top + height)
		if right <= left or bottom <= top:
			return None
		patch = self.displayed_background.crop((left, top, right, bottom))
		if patch.size != (width, height):
			padded = Image.new("RGBA", (width, height), self.theme["window"])
			padded.alpha_composite(patch, (0, 0))
			return padded
		return patch

	def build_hero(self):
		self.hero = tk.Frame(self.root, bg=PANEL, bd=0, highlightthickness=0)
		self.hero.place(**HERO_PLACE)

		self.art = tk.Canvas(self.hero, bg="#211c18", highlightthickness=0)
		self.art.place(x=2, y=2, relwidth=.49, relheight=1, height=-4)
		self.art.bind("<Configure>", self.draw_art)

		panel = tk.Frame(self.hero, bg="#211c18", bd=0, highlightthickness=0)
		panel.place(relx=.49, x=2, y=2, relwidth=.51, relheight=1,
					width=-4, height=-4)

		inner = tk.Frame(panel, bg="#211c18")
		inner.pack(fill="both", expand=True, padx=24, pady=12)
		self.hero_title = tk.Label(inner, text="ENTER THE WORLD", bg="#211c18", fg=GOLD,
							   font=self.ui_font(15, bold=True))
		self.hero_title.pack(anchor="w")
		self.hero_subtitle = tk.Label(inner, text="Choose an installed game to continue.",
								  bg="#211c18", fg="#c7baa0",
								  font=self.ui_font(10),
								  wraplength=450, justify="left")
		self.hero_subtitle.pack(anchor="w", pady=(3, 8))
		tk.Frame(inner, bg="#78613c", height=1).pack(fill="x", pady=(0, 8))

		tk.Label(inner, text="GAME VERSION", bg="#211c18", fg=MUTED,
				 font=self.ui_font(8, bold=True)).pack(anchor="w", pady=(2, 5))
		self.version = tk.StringVar(value=GAME_VERSIONS[0])
		self.version.trace_add("write", self.on_version_changed)
		version_menu = tk.OptionMenu(inner, self.version, *self.game_versions)
		version_menu.config(bg="#302922", fg=TEXT, activebackground="#51422c",
						activeforeground=GOLD, relief="raised", bd=2,
						font=self.ui_font(11, bold=True), anchor="w", padx=10)
		version_menu["menu"].config(bg="#302922", fg=TEXT,
								 activebackground="#806b42", activeforeground="white")
		self.version_menu = version_menu
		version_menu.pack(fill="x")

		news = tk.Frame(inner, bg="#211c18", bd=0, highlightthickness=0, padx=0, pady=0)
		news.pack(fill="both", expand=True, pady=(8, 0))
		news_header = tk.Frame(news, bg="#211c18")
		news_header.pack(fill="x", pady=(0, 2))
		tk.Label(news_header, text="\u25c6  LATEST NEWS", bg="#211c18", fg=GOLD,
				 font=self.ui_font(8, bold=True)).pack(side="left")
		self.news_status_label = tk.Label(news_header, text="CONNECTING…",
									  bg="#211c18", fg=MUTED,
									  font=self.ui_font(7))
		self.news_reload_button = tk.Label(news_header, text="↻",
			font=self.ui_font(11), bg="#211c18", fg=MUTED,
			bd=0, highlightthickness=0, padx=3, pady=0, cursor="hand2", takefocus=True)
		for event in ("<Button-1>", "<Return>", "<space>"):
			self.news_reload_button.bind(event, lambda _event: self.load_game_news(force=True))
		for event in ("<Enter>", "<FocusIn>"):
			self.news_reload_button.bind(event,
				lambda _event: self.news_reload_button.config(fg=self.theme["text"]))
		for event in ("<Leave>", "<FocusOut>"):
			self.news_reload_button.bind(event,
				lambda _event: self.news_reload_button.config(fg=self.theme["muted"]))
		self.news_reload_button.pack(side="right", padx=(5, 0))
		self.news_status_label.pack(side="right")
		self.news_items_frame = tk.Frame(news, bg="#211c18")
		self.news_items_frame.pack(fill="both", expand=True)
		self.news_items_frame.bind("<Configure>", self.on_news_area_resized)
		self.show_news_message("Fetching official news…")
		self.add_rounded_surface(self.hero, "panel", radius=10)

	def build_footer(self):
		footer = tk.Frame(self.root, bg="#181614", bd=0, highlightthickness=0)
		footer.place(**FOOTER_PLACE)
		self.footer = footer

		playtime = tk.Frame(footer, bg="#181614", bd=0, highlightthickness=0,
							padx=12, pady=3)
		playtime.place(relx=.018, rely=.05, relwidth=.37, relheight=.9)
		playtime_header = tk.Frame(playtime, bg="#181614")
		playtime_header.pack(fill="x")
		# A label avoids macOS drawing a white native button over the dark theme.
		self.playtime_button = tk.Label(playtime_header, text="PLAYTIME  ›",
			bg="#181614", fg=GOLD, font=self.ui_font(8, bold=True),
			bd=0, highlightthickness=0, padx=0, pady=0, cursor="hand2", takefocus=True)
		self.playtime_button.pack(side="left")
		for event in ("<Button-1>", "<Return>", "<space>"):
			self.playtime_button.bind(event, lambda _event: self.open_playtime_viewer())
		for event in ("<Enter>", "<FocusIn>"):
			self.playtime_button.bind(event,
				lambda _event: self.playtime_button.config(fg=self.theme["bright"]))
		for event in ("<Leave>", "<FocusOut>"):
			self.playtime_button.bind(event,
				lambda _event: self.playtime_button.config(fg=self.theme["accent"]))
		self.playtime_status_label = tk.Label(playtime_header, text="\u25cf CHECKING",
										  bg="#181614", fg=MUTED,
										  font=self.ui_font(7, bold=True))
		self.playtime_status_label.pack(side="right")
		tk.Frame(playtime, bg="#51432f", height=1).pack(fill="x", pady=(2, 2))
		stats = tk.Frame(playtime, bg="#181614")
		stats.pack(fill="both", expand=True)
		session_column = tk.Frame(stats, bg="#181614")
		session_column.pack(side="left", fill="y")
		tk.Label(session_column, text="SESSION", bg="#181614", fg=MUTED,
				 font=self.ui_font(7, bold=True), anchor="w").pack(anchor="w")
		self.playtime_session_label = tk.Label(session_column, text="--:--:--",
										 bg="#181614", fg=MUTED,
										 font=self.ui_font(11, bold=True), anchor="w")
		self.playtime_session_label.pack(anchor="w")
		tk.Frame(stats, bg="#51432f", width=1).pack(side="left", fill="y", padx=14, pady=2)
		total_column = tk.Frame(stats, bg="#181614")
		total_column.pack(side="left", fill="both", expand=True)
		self.playtime_total_caption = tk.Label(total_column, text="TOTAL", bg="#181614",
											 fg=MUTED, font=self.ui_font(7, bold=True), anchor="w")
		self.playtime_total_caption.pack(anchor="w")
		self.playtime_total_label = tk.Label(total_column, text="0h 00m",
										 bg="#181614", fg=TEXT,
										 font=self.ui_font(11, bold=True), anchor="w")
		self.playtime_total_label.pack(anchor="w")

		actions = tk.Frame(footer, bg="#181614")
		actions.place(relx=.41, rely=.08, relwidth=.36, relheight=.84)
		for label, command in (
				("OPEN FOLDER", self.open_game_folder),
				("ADD-ONS", self.open_addon_manager),
				("OPTIONS", self.open_options)):
			button = StoneButton(actions, text=label, command=command,
						font=self.ui_font(8, bold=True), padx=7, pady=5,
						bg="#181614", theme_provider=lambda: self.theme
						)
			button.pack(side="left", padx=3, expand=True)
			if label == "OPTIONS":
				self.options_button = button

		self.play = StoneButton(footer, text="Play", command=self.play_game,
							font=self.ui_font(16, bold=True), padx=22, pady=8,
							bg="#181614", theme_provider=lambda: self.theme)
		self.play.place(relx=.98, rely=.5, relwidth=.2, relheight=.92, anchor="e")
		self.playtime_surface = self.add_rounded_surface(playtime, "surface", radius=7)
		self.add_rounded_surface(footer, "surface", radius=9)

	def load_settings(self):
		self.playtime_seconds = {version: 0.0 for version in ALL_VERSIONS}
		self.playtime_history = {}  # local ISO date -> version -> seconds
		self.playtime_history_started = date.today().isoformat()
		self.enabled_extras = set()
		self.learned_clients = {}
		self.tracked_clients = {}  # pid -> (game, create_time)
		self.setup_complete = False
		self.launch_args = {}   # game -> argument text passed to the client
		self.armory = {}   # last lookup and saved character summaries; no API credentials
		try:
			data = json.loads(self.settings_path.read_text(encoding="utf-8"))
			folders = data.get("game_paths", {})
			saved_playtime = data.get("playtime_seconds", {})
			if isinstance(saved_playtime, dict):
				for version, value in saved_playtime.items():
					if isinstance(value, (int, float)) and not isinstance(value, bool):
						self.playtime_seconds[version] = max(0.0, float(value))
			history = data.get("playtime_history", {})
			if isinstance(history, dict):
				for day, versions in history.items():
					try:
						if date.fromisoformat(day).isoformat() != day or not isinstance(versions, dict):
							continue
					except (TypeError, ValueError):
						continue
					clean = {name: float(value) for name, value in versions.items()
						if isinstance(value, (int, float)) and not isinstance(value, bool)
						and math.isfinite(value) and value >= 0}
					if clean:
						self.playtime_history[day] = clean
			try:
				started = date.fromisoformat(data.get("playtime_history_started", ""))
				self.playtime_history_started = min(started, date.today()).isoformat()
			except (TypeError, ValueError):
				if self.playtime_history:
					self.playtime_history_started = min(self.playtime_history)
			known_paths = {
				version: str(folders.get(
					version,
					folders.get("WoW Forever", "") if version == "WoW Forever Beta" else ""))
				for version in ALL_VERSIONS
			}
			if isinstance(folders, dict):
				known_paths.update({
					version: str(path) for version, path in folders.items()
					if version not in ALL_VERSIONS and path
				})
			saved_extras = data.get("extras_enabled", [])
			if isinstance(saved_extras, list):
				self.enabled_extras = {name for name in saved_extras if name in EXTRA_VERSIONS}
			saved_clients = data.get("client_exes", {})
			if isinstance(saved_clients, dict):
				self.learned_clients = {
					name: {str(item) for item in items}
					for name, items in saved_clients.items()
					if name in LOADER_VERSIONS and isinstance(items, list)}
			saved_args = data.get("launch_args", {})
			if isinstance(saved_args, dict):
				self.launch_args = {str(name): value.strip()
									for name, value in saved_args.items()
									if isinstance(value, str) and value.strip()}
			saved_armory = data.get("armory", {})
			if isinstance(saved_armory, dict):
				self.armory = {key: saved_armory[key].strip() for key in
							   ("region", "realm", "name", "version")
							   if isinstance(saved_armory.get(key), str)}
				characters = saved_armory.get("characters", [])
				if isinstance(characters, list):
					self.armory["characters"] = [
						{key: item[key] for key in ("region", "realm", "name", "version", "snapshot", "updated")
						 if key in item}
						for item in characters if isinstance(item, dict)
						and all(isinstance(item.get(key), str) for key in ("region", "realm", "name", "version"))]
			if "setup_complete" in data:
				self.setup_complete = bool(data["setup_complete"])
			else:
				# Settings from before first-time setup existed: done if any folder is set.
				self.setup_complete = any(path for path in known_paths.values())
			return known_paths
		except (OSError, json.JSONDecodeError, AttributeError):
			return {version: "" for version in ALL_VERSIONS}

	def discover_game_versions(self):
		roots = set()
		for install_path in self.game_paths.values():
			if not install_path:
				continue
			path = Path(install_path).expanduser()
			roots.update((path, *list(path.parents)[:4]))

		program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
		program_files_x86 = Path(os.environ.get(
			"ProgramFiles(x86)", "C:/Program Files (x86)"))
		for root in (
			program_files / "World of Warcraft",
			program_files_x86 / "World of Warcraft",
			Path("C:/Games/World of Warcraft"),
			Path("D:/Games/World of Warcraft"),
		):
			roots.add(root)
		if IS_MAC:
			roots.update((
				Path("/Applications/World of Warcraft"),
				Path.home() / "Applications" / "World of Warcraft",
				Path("/Applications"), Path.home() / "Applications"))

		discovered = []
		for version, install_path in self.game_paths.items():
			if version in ALL_VERSIONS or not install_path:
				continue
			executable = self.find_wow_executable(Path(install_path).expanduser())
			if executable is not None:
				self.dynamic_versions[version] = executable.name

		for version in ALL_VERSIONS:
			current_path = self.game_paths.get(version, "")
			if current_path and self.find_executable(version, current_path) is not None:
				if version in EXTRA_VERSIONS:
					self.enabled_extras.add(version)
				continue
			found_path = None
			for root in sorted(roots, key=lambda item: len(item.parts)):
				for subdirectory in AUTO_DETECT_SUBDIRECTORIES[version]:
					candidate = root / subdirectory
					if self.find_executable(version, str(candidate), check_version=True) is None:
						continue
					found_path = str(candidate)
					break
				if found_path is not None:
					break
			if found_path is not None:
				self.game_paths[version] = found_path
				if version in EXTRA_VERSIONS:
					self.enabled_extras.add(version)
				discovered.append(version)

		known_folders = {
			folder.casefold()
			for folders in AUTO_DETECT_SUBDIRECTORIES.values()
			for folder in folders
		}
		known_executables = {
			os.path.normcase(os.path.abspath(str(executable)))
			for version in ALL_VERSIONS
			for executable in [self.find_executable(
				version, self.game_paths.get(version, ""))]
			if executable is not None
		}
		seen_folders = set()
		for root in sorted(roots, key=lambda item: len(item.parts)):
			candidates = [root]
			try:
				candidates.extend(path for path in root.iterdir() if path.is_dir())
			except OSError:
				continue
			for candidate in candidates:
				folder_name = candidate.name.casefold()
				folder_key = os.path.normcase(os.path.abspath(str(candidate)))
				if (folder_key in seen_folders or folder_name in known_folders
						or not (folder_name.startswith("_") and folder_name.endswith("_"))):
					continue
				seen_folders.add(folder_key)
				executable = self.find_wow_executable(candidate)
				if executable is None:
					continue
				executable_key = os.path.normcase(os.path.abspath(str(executable)))
				if executable_key in known_executables:
					continue
				label = self.dynamic_version_label(candidate.name)
				if label in ALL_VERSIONS:
					label = f"{label} ({candidate.name})"
				if label in self.dynamic_versions and self.dynamic_versions[label] != executable.name:
					label = f"{label} ({candidate.name})"
				is_new = label not in self.game_paths
				self.dynamic_versions[label] = executable.name
				self.game_paths[label] = str(candidate)
				known_executables.add(executable_key)
				if is_new:
					discovered.append(label)
		return discovered

	def find_wow_executable(self, folder):
		for executable_name in (
			"Wow.exe", "WowClassic.exe", "WowB.exe", "WowClassicT.exe", "WowClassicB.exe"):
			candidate = folder / executable_name
			if candidate.is_file():
				return candidate
		for app in sorted(folder.glob("World of Warcraft*.app")):
			if app.is_dir():
				return app
		return None

	def dynamic_version_label(self, folder_name):
		name_map = {"mop": "MoP", "ptr": "PTR", "p": "P", "tbc": "TBC"}
		words = [name_map.get(part.casefold(), part.title())
				 for part in folder_name.strip("_").split("_") if part]
		return " ".join(words) or "New WoW Client"

	def compute_game_versions(self):
		"""Menu order: detected custom clients, the built-in games, then any enabled extras."""
		extras = tuple(name for name in EXTRA_VERSIONS if name in self.enabled_extras)
		return (*self.dynamic_versions, *GAME_VERSIONS, *extras)

	HERO_SUBTITLE_WIDTH = 444   # a little under the label's wraplength (450)
	HERO_SUBTITLE_LINES = 3     # every description is laid out on this many lines

	def wrap_subtitle(self, text, width):
		return self.fit_text(text, self.ui_font(10), width, 99).split("\n")

	def balanced_subtitle(self, text):
		"""Wrap the description into explicit lines, narrowing the wrap width for shorter
		texts until they use exactly HERO_SUBTITLE_LINES lines."""
		target = self.HERO_SUBTITLE_LINES
		for width in range(self.HERO_SUBTITLE_WIDTH, 150, -2):
			lines = self.wrap_subtitle(text, width)
			if len(lines) >= target:
				return "\n".join(lines)
		return "\n".join(self.wrap_subtitle(text, self.HERO_SUBTITLE_WIDTH))

	def copy_for_version(self, version):
		if version in GAME_COPY:
			return GAME_COPY[version]
		return {
			"title": f"ENTER {version.upper()}",
			"subtitle": f"Explore {version}, an installed World of Warcraft client. Choose it "
				f"to launch that version directly and continue your adventure through Azeroth.",
			"headline": f"{version.upper()} AWAITS",
			"tagline": "A NEW ADVENTURE BEGINS",
			"play": "Play",
		}

	def slide_text(self, index=None):
		"""(headline, tagline) for the current game and slideshow image."""
		if index is None:
			index = self.slideshow_index if self.slideshow_images else 0
		slides = self.game_copy.get("slides")
		if slides:
			return slides[index % len(slides)]
		return (self.game_copy["headline"], self.game_copy["tagline"])

	def update_game_copy(self, animate=False):
		if not hasattr(self, "hero_title"):
			return
		self.game_copy = self.copy_for_version(self.version.get())
		self.play.set_label(self.game_copy["play"])
		subtitle = self.balanced_subtitle(self.game_copy["subtitle"])
		if animate:
			self.fade_hero_text(self.game_copy["title"], subtitle)
			self.transition_art_text()
			return
		self.hero_title.config(text=self.game_copy["title"])
		self.hero_subtitle.config(text=subtitle)
		self.art_text, self.art_text_fade = self.slide_text(), 1.0
		if self.current_art_frame is not None:
			self.draw_art_text()

	def fade_hero_text(self, title, subtitle):
		"""Fade the hero title/subtitle out, swap the text, fade it back in."""
		self.hero_text_generation += 1
		generation = self.hero_text_generation
		half = 3

		def step(index):
			if generation != self.hero_text_generation or not self.root.winfo_exists():
				return
			if index == half:
				self.hero_title.config(text=title)
				self.hero_subtitle.config(text=subtitle)
			k = 1 - (index + 1) / half if index < half else (index - half) / half
			for label, role in ((self.hero_title, "accent"), (self.hero_subtitle, "muted")):
				label.config(fg=blend_hex(str(label.cget("bg")), self.theme[role], k))
			if index < half * 2:
				self.root.after(20, lambda: step(index + 1))

		step(0)

	def transition_art_text(self):
		"""Cross-fade the text over the artwork when the game version changes."""
		self.art_text_generation += 1
		generation = self.art_text_generation
		old, steps = self.art_text, 6
		if self.art_transitioning:
			# The slideshow is already animating the text; it picks up the new copy.
			return

		def step(index):
			if generation != self.art_text_generation or self.art_transitioning:
				return
			t = index / steps
			if t < .5:
				self.art_text, self.art_text_fade = old, 1 - 2 * t
			else:
				self.art_text, self.art_text_fade = self.slide_text(), 2 * t - 1
			if self.current_art_frame is not None:
				self.draw_art_text()
			if index < steps:
				self.root.after(20, lambda: step(index + 1))

		step(1)

	def update_news_age(self):
		if self.last_news is None:
			return
		version, articles, errors, matched = self.last_news
		timestamps = [self._news_page_cache[url][0] for url in self.news_sources_for(version)
			if url in self._news_page_cache]
		if not timestamps:
			return
		seconds = max(0, int(time.monotonic() - min(timestamps)))
		if seconds < 60:
			age = "NOW"
		elif seconds < 3600:
			age = f"{seconds // 60}M AGO"
		elif seconds < 86400:
			age = f"{seconds // 3600}H AGO"
		else:
			age = f"{seconds // 86400}D AGO"
		prefix = "PARTIAL" if errors else ("OFFICIAL" if matched else "LATEST")
		self.news_status_label.config(text=f"{prefix} · UPDATED {age}",
			fg=self.theme["warning"] if errors else
			(self.theme["success"] if matched else self.theme["muted"]))

	def news_sources_for(self, version):
		sources = [WOW_NEWS_URL]
		if version not in ("WoW Forever Beta", "Retail"):
			sources.append(WOW_CLASSIC_NEWS_URL)
		return sources

	NEWS_CACHE_SECONDS = 600

	def news_source_articles(self, source_url, allow_network, force=False):
		"""(articles, failed) for one news page, cached for a few minutes. Returns None
		when a download would be needed but is not allowed."""
		cached = self._news_page_cache.get(source_url)
		if not force and cached is not None and time.monotonic() - cached[0] < self.NEWS_CACHE_SECONDS:
			return [dict(article) for article in cached[1]], False
		if not allow_network:
			return None
		try:
			request = Request(source_url, headers=NEWS_HEADERS)
			with urlopen(request, timeout=8) as response:
				page = response.read(2_000_000).decode("utf-8", "replace")
		except Exception:
			return [], True
		found = []
		for parser_class in (OfficialNewsParser, NewsLinkParser):
			if len(found) >= 6:
				break  # the slower fallback parser is only needed when the first finds little
			parser = parser_class()
			try:
				parser.feed(page)
				parser.close()
			except Exception:
				pass
			found.extend(parser.items)
		if found:
			self._news_page_cache[source_url] = (time.monotonic(), found)
		return [dict(article) for article in found], not found

	def build_news(self, version, allow_network=True, force=False):
		"""(selected articles, error count, matched) or None if it needs the network."""
		sources = self.news_sources_for(version)
		if allow_network:
			with ThreadPoolExecutor(max_workers=len(sources)) as pool:
				results = list(pool.map(
					lambda url: self.news_source_articles(url, True, force=force), sources))
		else:
			results = [self.news_source_articles(url, False) for url in sources]
			if any(result is None for result in results):
				return None
		articles = {}
		feed_errors = 0
		for source_url, (found, failed) in zip(sources, results):
			feed_errors += 1 if failed else 0
			for article in found:
				article["url"] = urljoin(source_url, article["url"])
				id_match = NEWS_ID_PATTERN.search(article["url"])
				key = id_match.group(1) if id_match else article["url"]
				existing = articles.get(key)
				if existing is None:
					articles[key] = article
				elif not existing["summary"] and article["summary"]:
					existing["summary"] = article["summary"]

		terms = GAME_NEWS_TERMS.get(version, (version.casefold(),))
		ranked = []
		for article in articles.values():
			text = f"{article['title']} {article['summary']}".casefold()
			for rank, term in enumerate(terms):
				if term in text:
					ranked.append((rank, article))
					break
		ranked.sort(key=lambda item: item[0])
		matched = bool(ranked)
		selected = [article for _rank, article in ranked][:6]
		for article in articles.values():
			if len(selected) >= 6:
				break
			if article not in selected:
				selected.append(article)
		return selected, feed_errors, matched

	def load_game_news(self, force=False):
		version = self.version.get()
		self.news_generation += 1
		generation = self.news_generation
		self.news_status_label.config(text="UPDATING…", fg=self.theme["muted"])
		self.show_news_message(f"Loading {version} news from Blizzard…")
		thread = threading.Thread(
			target=self.fetch_game_news,
			args=(version, generation, force), daemon=True)
		thread.start()

	def fetch_game_news(self, version, generation, force=False):
		selected, errors, matched = [], 1, False
		try:
			selected, errors, matched = self.build_news(version, force=force)
		except Exception:
			pass
		finally:
			self.news_queue.put((generation, version, selected, errors, matched))

	def poll_news_queue(self):
		if not self.root.winfo_exists():
			return
		while True:
			try:
				generation, version, articles, errors, matched = self.news_queue.get_nowait()
			except queue.Empty:
				break
			if generation != self.news_generation or version != self.version.get():
				continue
			self.render_game_news(version, articles, errors, matched)
		self.update_news_age()
		self.news_poll_id = self.root.after(250, self.poll_news_queue)

	def show_news_message(self, message):
		self.last_news = None
		self.news_anim_generation += 1
		self.clear_children(self.news_items_frame)
		label = tk.Label(self.news_items_frame, text=message, bg="#211c18", fg=self.theme["muted"],
				 font=self.ui_font(8, italic=True), anchor="w", justify="left",
				 wraplength=405)
		label.pack(fill="x", pady=(3, 0))

	def fit_text(self, text, font, width, max_lines):
		"""Wrap text to the pixel width and end with an ellipsis if it needs more lines."""
		def clip(line):
			if font.measure(line) <= width:
				return line
			low, high = 0, len(line)
			while low < high:
				middle = (low + high + 1) // 2
				if font.measure(line[:middle].rstrip() + "\u2026") <= width:
					low = middle
				else:
					high = middle - 1
			return line[:low].rstrip() + "\u2026"

		text = " ".join(str(text).split())
		if len(text) > 4000:
			text = text[:4000].rsplit(" ", 1)[0] + "\u2026"
		lines, current, truncated = [], "", False
		for word in text.split():
			candidate = f"{current} {word}".strip()
			if font.measure(candidate) <= width:
				current = candidate
				continue
			if current:
				lines.append(current)
				if len(lines) == max_lines:
					truncated = True
					current = ""
					break
			current = word
		if current:
			lines.append(current)
		if not lines:
			return ""
		lines = [clip(line) for line in lines]
		if truncated and not lines[-1].endswith("\u2026"):
			lines[-1] = clip(lines[-1] + "\u2026")
		return "\n".join(lines)

	NEWS_FALLBACK_SUMMARY = "Read the full story on the official World of Warcraft site."

	def news_summary(self, article):
		return article["summary"] or self.NEWS_FALLBACK_SUMMARY

	def news_card_height(self, lines):
		"""Estimated pixel height of one news card showing `lines` description lines."""
		height = 8 + self.ui_font(9, bold=True).metrics("linespace") + 6
		if lines:
			height += 2 + lines * self.ui_font(8).metrics("linespace") + 4
		return height

	def plan_news_layout(self, articles, available):
		"""Pick how many articles and description lines fill the area best."""
		minimum = min(NEWS_CARD_COUNT, len(articles))
		if available <= 1:
			return minimum, 1
		summary_font = self.ui_font(8)
		natural = [self.fit_text(self.news_summary(article), summary_font,
								 NEWS_TEXT_WIDTH, 10).count("\n") + 1 for article in articles]
		best, best_score = (minimum, 0), None
		for count in range(minimum, min(len(articles), NEWS_CARD_COUNT) + 1):
			for lines in (0, 1, 2):
				total = sum(self.news_card_height(min(lines, natural[index]))
							for index in range(count))
				if total > available:
					continue
				score = (total, lines, count)
				if best_score is None or score > best_score:
					best, best_score = (count, lines), score
		return best

	def on_news_area_resized(self, event):
		if self.last_news is None or abs(event.height - self.news_layout_height) <= 4:
			return
		if self.news_resize_id is not None:
			self.root.after_cancel(self.news_resize_id)
		self.news_resize_id = self.root.after(60, self.relayout_news)

	def relayout_news(self):
		self.news_resize_id = None
		if self.last_news is not None:
			self.render_game_news(*self.last_news, animate=False)

	def build_news_cards(self, articles, lines):
		theme = self.theme
		surface, hover_surface = theme["surface"], theme["control"]
		base = theme["panel"]
		records = []
		for article in articles:
			summary_text = self.news_summary(article)
			card = tk.Frame(self.news_items_frame, bg=base,
							highlightbackground=base, highlightthickness=1,
							padx=0, pady=2, cursor="hand2")
			card.pack(fill="both", expand=True, pady=1)
			accent_bar = tk.Frame(card, bg=base, width=3, cursor="hand2")
			accent_bar.pack(side="left", fill="y")
			chevron = tk.Label(card, text="\u203a", bg=base, fg=base,
							   font=self.ui_font(12, bold=True), cursor="hand2")
			chevron.pack(side="right", padx=(2, 8))
			body = tk.Frame(card, bg=base, cursor="hand2")
			body.pack(side="left", expand=True, fill="x", padx=(9, 2))
			title_font = self.ui_font(9, bold=True)
			title = tk.Label(body, text=self.fit_text(article["title"], title_font,
								 NEWS_TEXT_WIDTH - 8, 1), bg=base, fg=base,
							 font=title_font, anchor="w", justify="left", cursor="hand2")
			title.pack(fill="x")
			text_widgets = [body, title, chevron]
			if lines:
				summary_font = self.ui_font(8)
				summary = tk.Label(body, text=self.fit_text(
					summary_text, summary_font, NEWS_TEXT_WIDTH - 8, lines),
					bg=base, fg=base, font=summary_font, anchor="w",
					justify="left", cursor="hand2")
				summary.pack(fill="x", pady=(2, 0))
				text_widgets.append(summary)

			def set_hover(active, card=card, bar=accent_bar, chevron=chevron,
						  widgets=text_widgets):
				background = hover_surface if active else surface
				card.configure(bg=background,
							   highlightbackground=theme["accent"] if active else theme["control"])
				bar.configure(bg=theme["accent"] if active else theme["accent_dark"])
				for widget in widgets:
					widget.configure(bg=background)
				chevron.configure(fg=theme["accent"] if active else theme["muted"])

			for widget in (card, accent_bar, *text_widgets):
				widget.bind("<Button-1>", lambda _event, url=article["url"]:
					webbrowser.open(url))
				widget.bind("<Enter>", lambda _event, hover=set_hover: hover(True))
				widget.bind("<Leave>", lambda _event, hover=set_hover: hover(False))

			records.append({"card": card, "bar": accent_bar, "chevron": chevron,
							"title": title, "summary": summary if lines else None})
		return records

	def set_news_card_progress(self, record, progress):
		theme = self.theme
		base = theme["panel"]
		surface = theme["surface"]
		k = min(1.0, max(0.0, progress))
		record["card"].configure(
			bg=blend_hex(base, surface, k),
			highlightbackground=blend_hex(base, theme["control"], k))
		record["bar"].configure(bg=blend_hex(base, theme["accent_dark"], k))
		record["chevron"].configure(
			bg=blend_hex(base, surface, k), fg=blend_hex(base, theme["muted"], k))
		for key, color_key in (("title", "bright"), ("summary", "muted")):
			widget = record.get(key)
			if widget is not None:
				widget.configure(bg=blend_hex(base, surface, k),
								 fg=blend_hex(base, theme[color_key], k))

	def animate_news_cards(self, cards):
		"""Fade each news card in, staggered top to bottom."""
		self.news_anim_generation += 1
		generation = self.news_anim_generation
		steps, stagger, frame_ms = 5, 45, 20

		def run(record, step):
			if generation != self.news_anim_generation:
				return
			k = step / steps
			self.set_news_card_progress(record, k * (2 - k))  # ease-out
			if step < steps:
				self.root.after(frame_ms, lambda: run(record, step + 1))

		for record in cards:
			self.set_news_card_progress(record, 0)
		for index, record in enumerate(cards):
			self.root.after(index * stagger, lambda record=record: run(record, 1))

	def render_game_news(self, version, articles, errors, matched, plan=None, animate=True):
		self.news_anim_generation += 1  # cancels any animation still running
		self.clear_children(self.news_items_frame)
		if not articles:
			self.news_status_label.config(text="OFFLINE", fg=self.theme["warning"])
			self.show_news_message("News could not be reached. Check your connection or open the official news site.")
			self.news_items_frame.bind("<Button-1>",
				lambda _event: webbrowser.open(WOW_NEWS_URL))
			return

		self.last_news = (version, articles, errors, matched)
		self.update_news_age()
		actual_height = self.news_items_frame.winfo_height()
		available = max(1, actual_height - 12)
		self.news_layout_height = actual_height
		if plan is None:
			plan = self.plan_news_layout(articles, available)
		count, lines = plan
		self.news_cards = self.build_news_cards(articles[:count], lines)
		if animate:
			self.animate_news_cards(self.news_cards)
		else:
			# Cards are built in the panel colour (invisible); without the fade they must be
			# set to their final colours here, or a relayout leaves them blank/half-faded.
			for record in self.news_cards:
				self.set_news_card_progress(record, 1)

	def find_running_game(self, selected):
		executable_versions = {}
		folder_versions = {}
		loader_keys = {}
		for version in self.game_versions:
			executable = self.find_executable(version, self.game_paths.get(version, ""))
			if executable is None:
				continue
			key = path_key(executable)
			if version in LOADER_VERSIONS:
				# A custom server's exe is only a loader. It is not counted as playing;
				# the WoW client it starts is, so remember where to look for that client.
				loader_keys[key] = version
				folder_versions[path_key(executable.parent).rstrip(os.sep) + os.sep] = version
				for learned in tuple(self.learned_clients.get(version, ())):
					executable_versions.setdefault(path_key(learned), []).append(version)
			else:
				executable_versions.setdefault(key, []).append(version)

		processes = []
		for process in psutil.process_iter(["pid", "ppid", "name", "exe", "create_time"]):
			try:
				info = process.info
			except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
				continue
			if info.get("exe") and info.get("pid") is not None:
				# macOS clients run as .../Name.app/Contents/MacOS/Name; use the .app.
				info["exe"] = client_path(info["exe"])
				processes.append(info)

		# Follow the WoW client a loader starts, by process id, wherever it is installed.
		live = {info["pid"]: info for info in processes}
		for pid, (_game, created) in list(self.tracked_clients.items()):
			info = live.get(pid)
			if (info is None or info.get("create_time") != created
					or not self.process_alive(pid)):
				del self.tracked_clients[pid]
		loader_pids = {
			info["pid"]: loader_keys[path_key(info["exe"])]
			for info in processes if path_key(info["exe"]) in loader_keys}
		for info in processes:
			pid = info["pid"]
			if pid in self.tracked_clients:
				continue
			parent = info.get("ppid")
			game = loader_pids.get(parent)
			if game is None and parent in self.tracked_clients:
				game = self.tracked_clients[parent][0]
			if (game is not None and is_wow_client(info["exe"])
					and official_game_for(info["exe"]) is None and self.process_alive(pid)):
				self.tracked_clients[pid] = (game, info.get("create_time"))
				self.learn_client(game, info["exe"])
		for watch in list(self.watches):
			if time.monotonic() > watch["deadline"]:
				self.discard_watch(watch)
				continue
			for info in processes:
				executable = info["exe"]
				if ((info.get("create_time") or 0) < watch["started"] - 2
						or not is_wow_client(executable)
						or in_official_install(executable)
						or path_key(executable) == watch["loader_key"]):
					continue
				self.tracked_clients[info["pid"]] = (watch["version"], info.get("create_time"))
				self.learn_client(watch["version"], executable)
				self.discard_watch(watch)
				break
		if self.tracked_clients:
			games = {game for game, _created in self.tracked_clients.values()}
			return selected if selected in games else sorted(games)[0]

		for info in processes:
			key = path_key(info["exe"])
			if key in loader_keys:
				continue
			# A client inside World of Warcraft/<flavor folder>/ is always the official game
			# that folder belongs to, whether or not it is configured or started by a loader.
			official = official_game_for(info["exe"])
			if official is not None:
				if is_wow_client(info["exe"]) and self.process_alive(info["pid"]):
					return official
				continue
			candidates = list(executable_versions.get(key, ()))
			# A client outside World of Warcraft is a custom server's, matched through its
			# loader's folder.
			if not in_official_install(info["exe"]) and is_wow_client(info["exe"]):
				for prefix, game in folder_versions.items():
					if key.startswith(prefix) and game not in candidates:
						candidates.append(game)
			if not candidates or not self.process_alive(info["pid"]):
				continue
			if len(candidates) > 1:
				# Near-identical games sharing an exe (e.g. WowClassic.exe): use its version.
				candidates = self.narrow_by_version(candidates, info["exe"])
			return selected if selected in candidates else candidates[0]
		return None

	def process_alive(self, pid):
		"""False once a process has really exited. Windows keeps an exited process listed
		while another process (such as a loader) still holds a handle to it, and Unix keeps
		an unreaped child as a zombie, so being listed does not mean it is still running."""
		try:
			if not psutil.pid_exists(pid):
				return False
			return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
		except psutil.AccessDenied:
			return True  # it exists; we just are not allowed to inspect it
		except psutil.NoSuchProcess:
			return False

	def discard_watch(self, watch):
		try:
			self.watches.remove(watch)
		except ValueError:
			pass

	def learn_client(self, version, executable):
		"""Queue a custom server's client exe to be remembered (applied on the UI thread)."""
		if in_official_install(executable):
			return  # could be mistaken for the official game; it is tracked by pid instead
		key = path_key(executable)
		if key not in self.learned_clients.get(version, ()):
			self.learn_requests.put((version, key))

	def narrow_by_version(self, candidates, executable):
		"""Pick among games sharing an exe: flavor folder first, then file version."""
		flavor = flavor_game_for(executable)
		if flavor in candidates:
			return [flavor]
		client = exe_version(executable)
		if client is None:
			return candidates
		fitting = [version for version in candidates
				   if client[0] in RUNNING_CLIENT_MAJORS.get(version, (client[0],))]
		return fitting or candidates

	def format_session_time(self, seconds):
		seconds = max(0, int(seconds))
		hours, remainder = divmod(seconds, 3600)
		minutes, seconds = divmod(remainder, 60)
		return f"{hours:02}:{minutes:02}:{seconds:02}"

	def format_total_time(self, seconds):
		total_minutes = max(0, int(seconds)) // 60
		hours, minutes = divmod(total_minutes, 60)
		return f"{hours}h {minutes:02}m"

	def update_playtime_display(self, now=None):
		if not hasattr(self, "playtime_status_label"):
			return
		if now is None:
			now = time.monotonic()
		if self.running_version is None:
			version = self.version.get()
			self.playtime_status_label.config(
				text="\u25cf IDLE", fg=self.theme["disabled"])
			self.playtime_session_label.config(text="--:--:--", fg=self.theme["muted"])
		else:
			version = self.running_version
			self.playtime_status_label.config(
				text="\u25cf IN GAME", fg=self.theme["success"])
			elapsed = now - self.session_started_at if self.session_started_at is not None else 0
			self.playtime_session_label.config(
				text=self.format_session_time(elapsed), fg=self.theme["bright"])
		caption = version.upper()
		if len(caption) > 22:
			caption = caption[:21] + "\u2026"
		self.playtime_total_caption.config(text=f"TOTAL \u00b7 {caption}")
		self.playtime_total_label.config(
			text=self.format_total_time(self.playtime_seconds.get(version, 0)),
			fg=self.theme["text"])

	def record_playtime(self, version, seconds, end_timestamp=None):
		"""Use monotonic elapsed seconds; split history at local midnight."""
		if not math.isfinite(seconds) or seconds <= 0:
			return
		end = time.time() if end_timestamp is None else end_timestamp
		cursor = end - seconds
		self.playtime_seconds[version] = self.playtime_seconds.get(version, 0.0) + seconds
		while cursor < end:
			day = datetime.fromtimestamp(cursor).date()
			midnight = datetime.combine(day + timedelta(days=1), datetime.min.time()).timestamp()
			stop = min(end, midnight)
			entry = self.playtime_history.setdefault(day.isoformat(), {})
			entry[version] = entry.get(version, 0.0) + (stop - cursor)
			self.playtime_history_started = min(self.playtime_history_started, day.isoformat())
			cursor = stop

	def playtime_series(self, version, days, end_day):
		start = end_day - timedelta(days=days - 1)
		series = []
		for offset in range(days):
			day = start + timedelta(days=offset)
			values = self.playtime_history.get(day.isoformat(), {})
			seconds = sum(values.values()) if version == "All versions" else values.get(version, 0.0)
			series.append((day, seconds if day.isoformat() >= self.playtime_history_started else None))
		return series

	def open_playtime_viewer(self):
		"""A responsive native graph; hover details stay inside the panel."""
		theme = dict(self.theme)
		panel = theme["panel"]
		window = Overlay(self.root, 860, 570)
		body = window.make_body()
		body.config(bg=panel)
		def button(parent, text, command):
			return StoneButton(parent, text=text, command=command, font=self.ui_font(8, bold=True),
				bg=panel, theme_provider=lambda: theme, style="subtle", padx=8, pady=4)
		top = tk.Frame(body, bg=panel)
		top.pack(fill="x", pady=(0, 12))
		tk.Label(top, text="PLAYTIME", bg=panel, fg=theme["accent"],
			font=self.ui_font(15, bold=True)).pack(side="left")
		button(top, "CLOSE", window.destroy).pack(side="right")
		controls = tk.Frame(body, bg=panel)
		controls.pack(fill="x", pady=(0, 12))
		selected = tk.StringVar(value=self.version.get())
		versions = list(dict.fromkeys(["All versions", *self.game_versions,
			*self.playtime_seconds, *(name for values in self.playtime_history.values() for name in values)]))
		menu = tk.OptionMenu(controls, selected, *versions, command=lambda _: draw())
		menu_font = self.ui_font(8)
		# Reserve the longest title, so changing versions cannot squeeze other controls.
		menu_width = math.ceil(max(menu_font.measure(name) for name in versions) /
			max(1, menu_font.measure("0"))) + 2
		menu.config(bg=theme["control"], fg=theme["text"], relief="flat", bd=0,
			highlightthickness=0, font=menu_font, width=menu_width, anchor="w",
			activebackground=theme["control_active"])
		menu["menu"].config(bg=theme["control"], fg=theme["text"], font=self.ui_font(8))
		menu.pack(side="left", padx=(0, 12))
		state = {"days": 30, "end": date.today(), "timer": None, "series": []}
		navigation = tk.Frame(body, bg=panel)
		navigation.pack(fill="x", pady=(0, 12))
		for days in (7, 30, 90, 365):
			button(navigation, f"{days} DAYS", lambda value=days: choose_range(value)).pack(side="left", padx=2)
		nav_buttons = []
		for caption, direction in (("TODAY", 0), ("NEXT ›", 1), ("‹ PREVIOUS", -1)):
			control = button(navigation, caption, lambda value=direction: move(value))
			nav_buttons.append(control)
			control.pack(side="right", padx=2)
		nav_width = max(control.winfo_reqwidth() for control in nav_buttons)
		for control in nav_buttons:
			control.configure(width=nav_width)
		range_label = tk.Label(body, bg=panel, fg=theme["muted"], font=self.ui_font(9), anchor="w")
		range_label.pack(fill="x", pady=(0, 8))
		summary = tk.Frame(body, bg=panel)
		summary.pack(fill="x", pady=(0, 12))
		metrics = []
		for caption in ("PERIOD TOTAL", "AVERAGE / TRACKED DAY", "DAYS PLAYED", "LIFETIME TOTAL"):
			card = tk.Frame(summary, bg=theme["surface"], padx=10, pady=8)
			card.pack(side="left", fill="both", expand=True, padx=(0, 6))
			tk.Label(card, text=caption, bg=theme["surface"], fg=theme["muted"],
				font=self.ui_font(7, bold=True)).pack(anchor="w")
			value = tk.Label(card, bg=theme["surface"], fg=theme["text"], font=self.ui_font(12, bold=True))
			value.pack(anchor="w")
			metrics.append(value)
			self.add_rounded_surface(card, "surface", radius=7)
		canvas = tk.Canvas(body, bg=theme["surface"], bd=0, highlightthickness=0, height=260)
		canvas.pack(fill="both", expand=True)
		hover = tk.Label(body, text="Move over the graph for daily playtime.", bg=panel,
			fg=theme["text"], font=self.ui_font(9), anchor="w")
		hover.pack(fill="x", pady=(8, 2))
		tk.Label(body, text=f"Daily history starts {self.playtime_history_started}. Earlier totals have no dates. "
			"Tracking runs while the launcher is open.", bg=panel, fg=theme["muted"],
			font=self.ui_font(8), anchor="w", wraplength=790).pack(fill="x")

		def choose_range(days):
			state["days"] = days
			draw()

		def move(direction):
			state["end"] = date.today() if direction == 0 else min(date.today(),
				state["end"] + timedelta(days=direction * state["days"]))
			draw()

		def draw(event=None):
			if not canvas.winfo_exists():
				return
			series = self.playtime_series(selected.get(), state["days"], state["end"])
			state["series"] = series
			known = [seconds for _, seconds in series if seconds is not None]
			total = sum(known)
			lifetime = sum(self.playtime_seconds.values()) if selected.get() == "All versions" else self.playtime_seconds.get(selected.get(), 0)
			for label, value in zip(metrics, (self.format_total_time(total),
				self.format_total_time(total / len(known)) if known else "Unavailable",
				str(sum(seconds > 0 for seconds in known)), self.format_total_time(lifetime))):
				label.config(text=value)
			range_label.config(text=f"{series[0][0]:%b %d, %Y} – {series[-1][0]:%b %d, %Y}  ·  {selected.get()}")
			canvas.delete("all")
			width, height = max(200, canvas.winfo_width()), max(140, canvas.winfo_height())
			left, right, top, bottom = 58, width - 18, 20, height - 35
			peak = max(known, default=0)
			ceiling = max(60, peak * 1.15)
			state["bounds"] = (left, right, top, bottom, ceiling)
			for step in range(5):
				y = bottom - (bottom - top) * step / 4
				seconds = ceiling * step / 4
				label = f"{seconds / 3600:.1f}h" if ceiling >= 3600 else f"{seconds / 60:.1f}m"
				canvas.create_line(left, y, right, y, fill=theme["border"])
				canvas.create_text(left - 8, y, text=label, anchor="e", fill=theme["muted"], font=self.ui_font(8))
			step_width = (right - left) / len(series)
			points = []
			for index, (_, seconds) in enumerate(series):
				if seconds is None:
					continue
				x = left + (index + .5) * step_width
				y = bottom - seconds / ceiling * (bottom - top)
				if state["days"] <= 30:
					if seconds > 0:
						canvas.create_rectangle(x - step_width * .34, y, x + step_width * .34, bottom,
							fill=theme["accent"], outline="")
				else:
					points.extend((x, y))
			if len(points) >= 4:
				canvas.create_line(*points, fill=theme["accent"], width=2)
			elif points:
				x, y = points
				canvas.create_oval(x-3, y-3, x+3, y+3, fill=theme["accent"], outline="")
			for index in sorted({0, len(series)//4, len(series)//2, 3*len(series)//4, len(series)-1}):
				canvas.create_text(left + (index + .5)*step_width, bottom + 18,
					text=series[index][0].strftime("%b %d"), fill=theme["muted"], font=self.ui_font(8))
			if not peak:
				canvas.create_text((left+right)/2, (top+bottom)/2,
					text="No recorded playtime in this period.", fill=theme["muted"], font=self.ui_font(11))
			hover.config(text="Move over the graph for daily playtime.")

		def inspect(event):
			if "bounds" not in state:
				return
			left, right, top, bottom, ceiling = state["bounds"]
			canvas.delete("cursor")
			if not left <= event.x <= right or not top <= event.y <= bottom:
				return
			index = min(len(state["series"])-1, int((event.x-left)/(right-left)*len(state["series"])))
			day, seconds = state["series"][index]
			value = self.format_session_time(seconds) if seconds is not None else "No dated history"
			hover.config(text=f"{day:%A, %b %d, %Y}  ·  {value}")
			x = left + (index+.5)*(right-left)/len(state["series"])
			canvas.create_line(x, top, x, bottom, fill=theme["muted"], dash=(3,3), tags="cursor")

		def leave(event):
			canvas.delete("cursor")
			hover.config(text="Move over the graph for daily playtime.")

		def tick():
			if body.winfo_exists():
				draw()
				state["timer"] = self.root.after(15000, tick)

		def cleanup(event):
			if event.widget is body and state["timer"] is not None:
				self.root.after_cancel(state["timer"])
				state["timer"] = None

		canvas.bind("<Configure>", draw)
		canvas.bind("<Motion>", inspect)
		canvas.bind("<Leave>", leave)
		body.bind("<Destroy>", cleanup, add="+")
		tick()

	def request_process_scan(self):
		"""Look for running game processes on a worker thread (psutil is slow on Windows)."""
		if self.scan_running:
			return
		self.scan_running = True
		threading.Thread(target=self.process_scan_worker,
						 args=(self.version.get(),), daemon=True).start()

	def process_scan_worker(self, selected):
		try:
			result = ("ok", self.find_running_game(selected))
		except Exception as error:  # never let one bad scan stop playtime polling
			result = ("error", str(error))
		self.scan_results.put(result)

	def poll_game_processes(self):
		now = time.monotonic()
		delta = max(0.0, now - self.last_playtime_poll)
		self.last_playtime_poll = now
		if self.running_version is not None:
			self.record_playtime(self.running_version, delta)
		while True:
			try:
				version, key = self.learn_requests.get_nowait()
			except queue.Empty:
				break
			known = self.learned_clients.setdefault(version, set())
			if key not in known:
				known.add(key)
				self.persist_settings()
		while True:
			try:
				kind, value = self.scan_results.get_nowait()
			except queue.Empty:
				break
			self.scan_running = False
			if kind == "error":
				self.set_status(f"Playtime check failed: {value}")
			elif value != self.running_version:
				if self.running_version is not None:
					self.persist_settings()
				self.running_version = value
				self.session_started_at = now if value is not None else None
		if not self.scan_running and now - self.last_scan_request >= 2.0:
			self.last_scan_request = now
			self.request_process_scan()
		self.update_playtime_display(now)
		if now - self.last_playtime_save >= 15:
			if self.running_version is not None:
				self.persist_settings()
			self.last_playtime_save = now
		self.playtime_poll_id = self.root.after(1000, self.poll_game_processes)

	def persist_settings(self):
		try:
			self.settings_path.write_text(json.dumps({
				"game_paths": self.game_paths,
				"playtime_seconds": self.playtime_seconds,
				"playtime_history": self.playtime_history,
				"playtime_history_started": self.playtime_history_started,
				"setup_complete": self.setup_complete,
				"launch_args": self.launch_args,
				"armory": self.armory,
				"extras_enabled": sorted(self.enabled_extras),
				"client_exes": {name: sorted(paths)
								for name, paths in self.learned_clients.items()},
			}, indent=2), encoding="utf-8")
		except OSError as error:
			self.set_status(f"Could not save launcher settings: {error}")
			return False
		return True

	def close_launcher(self):
		self.updates.shutdown()
		if self.running_version is not None:
			self.record_playtime(self.running_version, max(0.0, time.monotonic() - self.last_playtime_poll))
		self.persist_settings()
		if hasattr(self, "_background_executor"):
			self._background_executor.shutdown(wait=False, cancel_futures=True)
		self.root.destroy()

	def ui_font(self, size, bold=False, italic=False):
		key = (size, bold, italic)
		if key not in self._font_cache:
			self._font_cache[key] = tkfont.Font(
				root=self.root,
				family=self._font_family,
				size=-round(size * MAC_FONT_SCALE) if IS_MAC else size,
				weight="bold" if bold else "normal",
				slant="italic" if italic else "roman")
		return self._font_cache[key]

	def apply_theme(self, version, subtree=None):
		self.theme = GAME_THEMES.get(version, GAME_THEMES[GAME_VERSIONS[0]])
		color_roles = {color.casefold(): role
					   for role, color in self.theme.items()}
		for palette in GAME_THEMES.values():
			for role, color in palette.items():
				color_roles.setdefault(color.casefold(), role)
		for color, role in BASE_COLOR_ROLES.items():
			color_roles[color.casefold()] = role

		root_widget = subtree if subtree is not None else self.root
		widgets = [root_widget]
		for widget in widgets:
			widgets.extend(widget.winfo_children())
			for option in ("bg", "fg", "activebackground", "activeforeground",
						   "highlightbackground", "highlightcolor", "insertbackground",
						   "selectcolor", "selectbackground", "selectforeground"):
				try:
					current = str(widget.cget(option)).casefold()
				except tk.TclError:
					continue
				role = color_roles.get(current)
				if role is not None:
					try:
						widget.configure(**{option: self.theme[role]})
					except tk.TclError:
						pass
			if isinstance(widget, StoneButton):
				widget.redraw()

		if subtree is None:
			if hasattr(self, "version_menu"):
				self.version_menu["menu"].configure(
					bg=self.theme["control"], fg=self.theme["text"],
					activebackground=self.theme["control_active"],
					activeforeground=self.theme["bright"])
				self.refresh_version_menu()
			if self.current_art_frame is not None:
				self.render_art_frame(self.current_art_frame)
			self.refresh_rounded_surfaces()

		for surface in self.surfaces:
			if not surface.dead:
				surface.schedule()

	def register_friz_fonts(self):
		font_files = set(self.find_friz_files(resource_path("fonts")))
		for version, install_path in self.game_paths.items():
			if not install_path:
				continue
			base = Path(install_path).expanduser()
			for subdirectory in INSTALL_SUBDIRECTORIES.get(version, ("",)):
				game_dir = base / subdirectory if subdirectory else base
				for relative_font_dir in ("Fonts", "Interface/Fonts", "Data/Fonts"):
					font_files.update(self.find_friz_files(game_dir / relative_font_dir))

		if os.name == "nt":
			register = self.register_font_windows()
		elif sys.platform == "darwin":
			register = self.register_font_macos
		else:
			register = self.register_font_linux()
		if register is None:
			return
		for font_file in sorted(font_files, key=str):
			if str(font_file) not in self._registered_font_paths:
				try:
					if register(str(font_file)):
						self._registered_font_paths.add(str(font_file))
				except Exception:
					continue

	@staticmethod
	def find_friz_files(folder):
		"""Friz font files in a folder, matched case-insensitively (FRIZQT__.TTF etc.)."""
		try:
			return [item for item in Path(folder).iterdir()
					if "friz" in item.name.casefold()
					and item.suffix.casefold() in (".ttf", ".otf")]
		except OSError:
			return []

	@staticmethod
	def register_font_windows():
		try:
			import ctypes
			gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
			add_font = gdi32.AddFontResourceExW
			add_font.argtypes = (ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p)
			add_font.restype = ctypes.c_int
		except (AttributeError, OSError):
			return None
		return lambda path: bool(add_font(path, 0x10, None))   # FR_PRIVATE

	@staticmethod
	def register_font_macos(path):
		"""Make a font usable by this process only (CoreText process scope)."""
		import ctypes
		core_foundation = ctypes.cdll.LoadLibrary(
			"/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
		core_text = ctypes.cdll.LoadLibrary(
			"/System/Library/Frameworks/CoreText.framework/CoreText")
		core_foundation.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
		core_foundation.CFURLCreateFromFileSystemRepresentation.argtypes = (
			ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool)
		core_foundation.CFRelease.argtypes = (ctypes.c_void_p,)
		core_text.CTFontManagerRegisterFontsForURL.restype = ctypes.c_bool
		core_text.CTFontManagerRegisterFontsForURL.argtypes = (
			ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p)
		raw = os.fsencode(path)
		url = core_foundation.CFURLCreateFromFileSystemRepresentation(
			None, raw, len(raw), False)
		if not url:
			return False
		try:
			# kCTFontManagerScopeProcess = 1. Returns False if already registered; harmless.
			core_text.CTFontManagerRegisterFontsForURL(url, 1, None)
		finally:
			core_foundation.CFRelease(url)
		return True

	@staticmethod
	def register_font_linux():
		try:
			import ctypes
			import ctypes.util
			library = ctypes.util.find_library("fontconfig")
			if not library:
				return None
			fontconfig = ctypes.cdll.LoadLibrary(library)
			fontconfig.FcConfigAppFontAddFile.argtypes = (ctypes.c_void_p, ctypes.c_char_p)
			fontconfig.FcConfigAppFontAddFile.restype = ctypes.c_int
		except OSError:
			return None
		return lambda path: bool(fontconfig.FcConfigAppFontAddFile(None, os.fsencode(path)))

	def refresh_friz_font(self):
		try:
			families = tkfont.families(self.root)
		except tk.TclError:
			families = ()
		self._font_family = next(
			(name for name in families
			 if "friz quadrata" in name.casefold() or "frizqt" in name.casefold()),
			"Friz Quadrata")
		for font in self._font_cache.values():
			font.configure(family=self._font_family)

	def load_image(self, path, mode, max_size=None):
		try:
			with Image.open(path) as source:
				if max_size:
					source.thumbnail(max_size, Image.Resampling.LANCZOS, reducing_gap=3.0)
				image = source.convert(mode)
		except (OSError, ValueError):
			return None
		return image

	def find_art_file(self, folder_name, stem):
		folder = resource_path(folder_name)
		for extension in IMAGE_EXTENSIONS:
			candidate = folder / f"{stem}{extension}"
			if candidate.is_file():
				return candidate
		return None

	def selected_logo(self, version):
		if version not in self.game_logos:
			logo = None
			if version in GAME_ART:
				path = self.find_art_file("logos", GAME_ART[version][0])
				logo = self.load_image(path, "RGBA", (1100, 340)) if path else None
				if logo is not None:
					bounds = logo.getchannel("A").getbbox()
					if bounds:
						logo = logo.crop(bounds)
			self.game_logos[version] = logo
		return self.game_logos[version] or self.fallback_logo

	def selected_background(self, version):
		if version not in self.game_backgrounds:
			background = None
			if version in GAME_ART:
				background_stem = GAME_ART[version][1]
				preview = resource_path("backgrounds", "launcher_previews",
										f"{background_stem}.webp")
				if preview.is_file():
					background = self.load_image(preview, "RGBA")
				else:
					path = self.find_art_file("backgrounds", background_stem)
					background = self.load_image(path, "RGBA", (2200, 1400)) if path else None
			self.game_backgrounds[version] = background
		return self.game_backgrounds[version] or self.base_background

	def floating_rects(self, width, height):
		"""Pixel rectangles of the panels and buttons that float over the background."""
		rects = []
		for place in (HERO_PLACE, FOOTER_PLACE):
			left, top = place["relx"] * width, place["rely"] * height
			rects.append((int(left), int(top), int(left + place["relwidth"] * width),
						  int(top + place["relheight"] * height)))
		return rects

	def compose_game_background(self, version, size):
		key = (version, size)
		result = self._background_cache.get(key)
		if result is None:
			result = self._compose_game_background(version, size)
			self._background_cache[key] = result
		return result

	def compose_game_preview(self, version, size):
		"""Fast interim backdrop; the full per-game art is composed off-thread afterward."""
		width, height = size
		if self.base_background is None:
			preview = Image.new("RGBA", size, self.theme["window"])
		else:
			preview = ImageOps.fit(
				self.base_background, size, method=Image.Resampling.BILINEAR)
		logo = self.game_logos.get(version) or self.fallback_logo
		if logo is not None:
			logo = logo.copy()
			logo.thumbnail(GAME_LOGO_SIZES.get(version, (540, 165)),
						   Image.Resampling.BILINEAR)
			preview.alpha_composite(logo, ((width - logo.width) // 2, max(0, int(height * .025))))
		return preview

	def prewarm_backgrounds(self, size):
		"""Compose every game's background in the background so switching is instant."""
		if size in self._prewarmed:
			return
		self._prewarmed.add(size)
		versions = list(self.game_versions)

		def work():
			for version in versions:
				try:
					self.compose_game_background(version, size)
				except Exception:
					pass

		threading.Thread(target=work, daemon=True).start()

	def _compose_game_background(self, version, size):
		width, height = size
		background_image = self.selected_background(version)
		if background_image is None:
			return add_panel_shadows(
				Image.new("RGBA", size, BG), self.floating_rects(width, height))
		background = ImageOps.fit(
			background_image, size, method=Image.Resampling.LANCZOS)
		background = add_panel_shadows(background, self.floating_rects(width, height))
		logo = self.selected_logo(version)
		if logo is not None:
			logo = logo.copy()
			logo.thumbnail(GAME_LOGO_SIZES.get(version, (540, 165)),
						   Image.Resampling.LANCZOS)
			logo_x = (width - logo.width) // 2
			logo_y = max(0, int(height * .025))
			shadowed, pad = add_drop_shadow(logo)
			left, top = logo_x - pad, logo_y - pad
			crop_left, crop_top = max(0, -left), max(0, -top)
			if crop_left or crop_top:
				shadowed = shadowed.crop((crop_left, crop_top, shadowed.width, shadowed.height))
			background.alpha_composite(shadowed, (max(0, left), max(0, top)))
		return background

	def slideshow_for(self, version):
		"""The screenshots for one game, loaded the first time that game is shown."""
		key = SLIDESHOW_PREFIXES.get(version, "")
		if key not in self._slideshow_cache:
			self._slideshow_cache[key] = self.load_slideshow_images(key)
		return self._slideshow_cache[key]

	def load_slideshow_images(self, prefix):
		"""<prefix>screenshot1..4 from the screenshots folder. Games without a prefix
		(auto-detected custom clients) use the older shared screenshot1..4 files."""
		images = []
		for number in range(1, 5):
			path = self.find_art_file("screenshots", f"{prefix}screenshot{number}")
			if path is None:
				continue
			image = self.load_image(path, "RGB", (1000, 800))
			if image is not None:
				images.append(image)
		return images

	def switch_slideshow(self, version):
		"""Show the selected game's own screenshots, cross-fading from the current one."""
		key = SLIDESHOW_PREFIXES.get(version, "")
		if key == self.slideshow_key:
			return
		self.slideshow_key = key
		self.slideshow_images = self.slideshow_for(version)
		self.slideshow_index = 0
		if self.slideshow_after_id is not None:
			self.root.after_cancel(self.slideshow_after_id)
			self.slideshow_after_id = None
		self.art_transition_generation += 1
		self.art_transitioning = False
		width, height = self.art.winfo_width(), self.art.winfo_height()
		if width <= 1 or height <= 1:
			self.slideshow_after_id = self.root.after(5000, self.advance_slideshow)
			return
		size = (width, height)
		if self.slideshow_images:
			end = self.fitted_slide(0, size)
		else:
			# No screenshots for this game: fall back to its background art.
			background = self.selected_background(version)
			end = (ImageOps.fit(background, size, method=Image.Resampling.LANCZOS)
				   if background is not None else None)
		if end is None:
			return
		start = self.current_art_frame or end
		self.art_transitioning = True
		self.animate_slideshow_transition(
			start, end, self.art_transition_generation, 1, 5, self.art_text)

	def advance_slideshow(self):
		if not self.slideshow_images or not self.root.winfo_exists():
			return
		self.slideshow_index = (self.slideshow_index + 1) % len(self.slideshow_images)
		width, height = self.art.winfo_width(), self.art.winfo_height()
		if width <= 1 or height <= 1:
			self.slideshow_after_id = self.root.after(5000, self.advance_slideshow)
			return
		start = self.current_art_frame or self.fitted_slide(
			(self.slideshow_index - 1) % len(self.slideshow_images), (width, height))
		end = self.fitted_slide(self.slideshow_index, (width, height))
		self.art_transition_generation += 1
		self.art_transitioning = True
		self.animate_slideshow_transition(
			start, end, self.art_transition_generation, 1, 5, self.art_text)

	def animate_slideshow_transition(self, start, end, generation, frame_index, steps,
									 old_text):
		if generation != self.art_transition_generation:
			return
		t = frame_index / steps
		if t < .5:
			self.art_text, self.art_text_fade = old_text, 1 - 2 * t
		else:
			self.art_text, self.art_text_fade = self.slide_text(), 2 * t - 1
		if t < 1:
			shaded = Image.blend(self.shade_art(start), self.shade_art(end), t)
			self.render_art_frame(start, shaded=shaded)
		else:
			self.render_art_frame(end)
		if frame_index < steps:
			self.slideshow_after_id = self.root.after(
				20, lambda: self.animate_slideshow_transition(
					start, end, generation, frame_index + 1, steps, old_text))
		else:
			self.art_transitioning = False
			self.slideshow_after_id = self.root.after(5000, self.advance_slideshow)

	def client_matches(self, version, executable):
		"""True if the exe fits this game. The flavor folder decides when present;
		otherwise the exe's file version (or allow it if unreadable)."""
		if official_game_for(executable) == version:
			return True
		flavor = flavor_game_for(executable)
		if flavor is not None:
			return flavor == version
		allowed = CLIENT_MAJOR_VERSIONS.get(version)
		if allowed is None:
			return True
		client = exe_version(executable)
		return client is None or client[0] in allowed

	def find_executable(self, version, install_path, check_version=False):
		base = Path(install_path).expanduser()
		if not base.is_dir():
			return None
		if version in self.dynamic_versions:
			return self.find_wow_executable(base)
		for subdirectory in INSTALL_SUBDIRECTORIES.get(version, ("",)):
			folder = base / subdirectory if subdirectory else base
			candidates = [folder / name for name in EXECUTABLES.get(version, ())]
			if IS_MAC and subdirectory and version not in LOADER_VERSIONS:
				# Mac flavor folders hold a "World of Warcraft ... .app"; accept any spelling.
				candidates.extend(sorted(folder.glob("World of Warcraft*.app")))
			for candidate in candidates:
				if is_client_file(candidate) and (
						not check_version or self.client_matches(version, candidate)):
					return candidate
		return None

	def update_install_status(self, *_args):
		if not hasattr(self, "version_menu"):
			return
		self.refresh_version_menu()
		self.update_playtime_display()

	def on_version_changed(self, *_args):
		self.update_install_status()
		if hasattr(self, "updates"):
			self.updates.refresh_view()
		version = self.version.get()
		self.selection_generation += 1
		generation = self.selection_generation
		self.switch_slideshow(version)
		self.update_game_copy(animate=True)
		if version != self.active_art_version:
			self.switch_game_art(version)

		def refresh_selected_game():
			if (generation != self.selection_generation or not self.root.winfo_exists()
					or version != self.version.get()):
				return
			self.apply_theme(version)
			self.load_game_news()

		self.root.after_idle(refresh_selected_game)

	def draw_background_frame(self, frame):
		self.displayed_background = frame
		self._background_photo = ImageTk.PhotoImage(frame.convert("RGB"), master=self.root)
		self.background.delete("all")
		self.background.create_image(0, 0, image=self._background_photo, anchor="nw")
		self.draw_credit()
		if self.options_button is not None:
			self.options_button.redraw()
		update_button = getattr(self, "update_button", None)
		if update_button is not None:
			update_button.redraw()
		armory_button = getattr(self, "armory_button", None)
		if armory_button is not None:
			armory_button.redraw()

	def draw_credit(self):
		"""White credit text with a drop shadow, drawn straight onto the background art."""
		width, height = self.background.winfo_width(), self.background.winfo_height()
		if width <= 1 or height <= 1:
			return
		self.background.delete("credit")
		x, y = width * .954, height * .985
		font = self.ui_font(8, bold=True)
		text = "Created by Frank Rosello, version 1.0.1"
		for offset_x in range(4):
			for offset_y in range(4):
				if offset_x or offset_y:
					self.background.create_text(
						x + offset_x, y + offset_y, text=text, fill="#000000",
						font=font, anchor="se", tags="credit")
		self.background.create_text(x, y, text=text, fill="#ffffff", font=font,
									anchor="se", tags="credit")

	def switch_game_art(self, version):
		self.active_art_version = version
		width, height = self.background.winfo_width(), self.background.winfo_height()
		if width <= 1 or height <= 1:
			return
		size = (width, height)
		request = (version, size)
		if request == self._background_requested:
			return
		self._background_requested = request
		self.background_transition_generation += 1
		generation = self.background_transition_generation
		old_background = self.displayed_background
		if old_background is not None and old_background.size != size:
			old_background = None
		elif old_background is not None:
			old_background = old_background.copy()
		if request not in self._background_cache:
			preview = self.compose_game_preview(version, size)
			self.draw_background_frame(preview)
			old_background = preview.copy()
		if self._background_future is not None:
			self._background_future.cancel()

		future = self._background_executor.submit(self.compose_game_background, version, size)
		self._background_future = future

		def completed(result):
			try:
				new_background = result.result()
				if old_background is None:
					frames = (new_background,)
				else:
					frames = tuple(Image.blend(old_background, new_background, amount)
									for amount in (.35, .7, 1.0))
					if frames[-1] is not new_background:
						frames = (*frames[:-1], new_background)
				self._background_queue.put((generation, version, frames, None))
			except Exception as error:
				self._background_queue.put((generation, version, (), error))

		future.add_done_callback(completed)

	def poll_background_queue(self):
		if not self.root.winfo_exists():
			return
		while True:
			try:
				generation, version, frames, error = self._background_queue.get_nowait()
			except queue.Empty:
				break
			if generation != self.background_transition_generation or version != self.version.get():
				continue
			if error is not None:
				self.set_status(f"Could not load {version} background: {error}")
				continue
			self._background_frames = frames
			self._background_frame_index = 0
			self.display_next_background_frame(generation)
		self._background_poll_id = self.root.after(40, self.poll_background_queue)

	def display_next_background_frame(self, generation):
		if generation != self.background_transition_generation:
			return
		frames = self._background_frames
		if not frames or self._background_frame_index >= len(frames):
			return
		frame = frames[self._background_frame_index]
		self._background_frame_index += 1
		self.draw_background_frame(frame)
		if self._background_frame_index < len(frames):
			self.background_transition_id = self.root.after(
				45, lambda: self.display_next_background_frame(generation))

	def refresh_version_menu(self):
		if not hasattr(self, "version_menu"):
			return
		menu = self.version_menu["menu"]
		for index, version in enumerate(self.game_versions):
			installed = self.find_executable(
				version, self.game_paths.get(version, "")) is not None
			menu.entryconfigure(index,
								state=tk.NORMAL if installed else tk.DISABLED,
								foreground=self.theme["text"] if installed else self.theme["disabled"])
		selected_installed = self.find_executable(
			self.version.get(), self.game_paths.get(self.version.get(), "")) is not None
		self.version_menu.config(
			fg=self.theme["text"] if selected_installed else self.theme["disabled"])

	def open_options(self):
		extra_height = 66  # how much the panel grows for each extra row
		shown_extras = sum(1 for name in EXTRA_VERSIONS if name in self.enabled_extras)
		window = Overlay(self.root, 820, 580 + extra_height * shown_extras)

		def stone(parent, text, command, size=8, padx=8, pady=4):
			return StoneButton(parent, text=text, command=command,
							   font=self.ui_font(size, bold=True), padx=padx, pady=pady,
							   bg="#211c18", theme_provider=lambda: self.theme, style="subtle", version_colored=True)

		body = window.make_body()
		tk.Label(body, text="GAME INSTALLATIONS", bg="#211c18", fg=GOLD,
				 font=self.ui_font(15, bold=True)).pack(anchor="w")
		tk.Label(body,
				 text="Point each game at its folder. A green READY tag means the launcher "
					  "found the game there.",
				 bg="#211c18", fg="#c7baa0", font=self.ui_font(9)
				 ).pack(anchor="w", pady=(3, 10))
		tk.Frame(body, bg="#78613c", height=1).pack(fill="x", pady=(0, 6))

		entries = {}
		arg_vars = {}   # game -> StringVar holding its launch arguments

		# Bottom first so it is never pushed off the panel: buttons, then the status line.
		footer = tk.Frame(body, bg="#211c18")
		footer.pack(fill="x", side="bottom", pady=(10, 0))
		options_status = tk.Label(body, text="Settings are saved on this computer.",
								  bg="#211c18", fg=MUTED, font=self.ui_font(8), anchor="w")
		options_status.pack(fill="x", side="bottom", pady=(8, 0))
		auto_button = stone(footer, "AUTO SEARCH", lambda: toggle_scan())
		auto_button.pack(side="left", padx=(0, 8))
		stone(footer, "EXTRA", lambda: add_extra()).pack(side="left")
		stone(footer, "CONFIG", lambda: self.open_game_config(parent=window, paths=entries)
			).pack(side="left", padx=(8, 0))
		stone(footer, "SAVE", lambda: self.save_options(window, entries, options_status, arg_vars),
			  size=9, padx=12).pack(side="right")
		stone(footer, "CANCEL", window.destroy).pack(side="right", padx=(0, 8))

		rows_frame = tk.Frame(body, bg="#211c18")
		rows_frame.pack(fill="both", expand=True)

		def refresh_chip(version, path_var, chip):
			text = path_var.get().strip()
			if text and self.find_executable(version, text) is not None:
				chip.config(text="\u25cf READY", fg=self.theme["success"])
			elif text:
				chip.config(text="\u25cf NOT FOUND", fg=self.theme["warning"])
			else:
				chip.config(text="\u25cf NOT SET", fg=self.theme["disabled"])

		def add_row(version, path=None):
			row = tk.Frame(rows_frame, bg="#181614", padx=8, pady=5)
			row.pack(fill="x", pady=3)
			tk.Label(row, text=version, width=24, anchor="w", bg="#181614",
					 fg=TEXT, font=self.ui_font(9, bold=True)).pack(side="left", padx=(2, 8))
			path_var = tk.StringVar(
				value=self.game_paths.get(version, "") if path is None else path)
			entry = tk.Entry(row, textvariable=path_var, bg="#171512", fg=TEXT,
							 insertbackground=TEXT, relief="flat", bd=1,
							 highlightthickness=1, highlightbackground="#51432f",
							 highlightcolor="#806b42", font=self.ui_font(9))
			entry.pack(side="left", fill="x", expand=True, padx=4, ipady=4)
			chip = tk.Label(row, text="", width=12, anchor="w", bg="#181614",
							fg=MUTED, font=self.ui_font(7, bold=True))
			chip.pack(side="left", padx=(6, 0))
			stone(row, "BROWSE", lambda variable=path_var: self.browse_folder(
				window, variable), pady=2).pack(side="left", padx=(6, 2))
			args_var = tk.StringVar(value=self.launch_args.get(version, ""))
			args_button = stone(
				row, "ARGS \u25cf", lambda v=version, var=args_var: self.edit_launch_args(
					window, v, var), pady=2)
			args_button.pack(side="left", padx=(2, 2))

			def refresh_args(*_a, var=args_var, button=args_button):
				# A dot marks games that have launch arguments set.
				button.set_label("ARGS \u25cf" if var.get().strip() else "ARGS")

			args_var.trace_add("write", refresh_args)
			refresh_args()
			arg_vars[version] = args_var
			path_var.trace_add("write", lambda *_a, v=version, var=path_var, c=chip:
							   refresh_chip(v, var, c))
			refresh_chip(version, path_var, chip)
			entries[version] = path_var
			self.style_popup_row(row)
			return row

		for version in self.game_versions:
			add_row(version)

		def add_extra():
			"""Pick the extra client's .exe; its row appears here and is enabled on SAVE."""
			name = EXTRA_VERSIONS[0]
			current = entries[name].get().strip() if name in entries else ""
			initial = current if current and Path(current).is_dir() else str(Path.home())
			if IS_MAC:
				# Mac clients are app bundles (folders), so pick the folder holding the loader.
				folder = filedialog.askdirectory(
					parent=window, initialdir=initial, title=f"Select the {name} folder")
				if not folder:
					return
				if self.find_executable(name, folder) is None:
					options_status.config(
						text="No " + " or ".join(EXECUTABLES[name][1:]) + " found there.",
						fg=self.theme["error"])
					return
			else:
				windows_names = [exe for exe in EXECUTABLES[name]
								 if exe.casefold().endswith(".exe")]
				selected = filedialog.askopenfilename(
					parent=window, initialdir=initial, title=f"Select the {name} client",
					filetypes=[("WoW client", " ".join(windows_names)),
							   ("Executables", "*.exe")])
				if not selected:
					return
				if Path(selected).name.casefold() not in {n.casefold() for n in windows_names}:
					options_status.config(
						text="Please choose " + " or ".join(windows_names) + ".",
						fg=self.theme["error"])
					return
				if not self.client_matches(name, Path(selected)):
					options_status.config(
						text=f"That client's version doesn't match {name}.",
						fg=self.theme["error"])
					return
				folder = str(Path(selected).parent)
			if name in entries:
				entries[name].set(folder)
			else:
				row = add_row(name, folder)
				window.update_idletasks()
				window.resize(window.winfo_height() + extra_height)
				self.apply_theme(self.version.get(), subtree=row)
			options_status.config(text=f"{name} added. Press SAVE to keep it.",
								  fg=self.theme["success"])

		# --- AUTO SEARCH: scan every drive for installs and fill in the rows -----------
		scan = {"cancel": threading.Event(), "queue": queue.Queue(),
				"scanning": False, "found": 0}

		def short_path(path, limit=60):
			return path if len(path) <= limit else "\u2026" + path[-(limit - 1):]

		def apply_found(version, path):
			"""Fill a row with a found install, but never replace a folder that already works."""
			if version in entries:
				current = entries[version].get().strip()
				if current and self.find_executable(version, current) is not None:
					return
				entries[version].set(path)
			elif version in EXTRA_VERSIONS:
				row = add_row(version, path)
				window.update_idletasks()
				window.resize(window.winfo_height() + extra_height)
				self.apply_theme(self.version.get(), subtree=row)
			else:
				return
			scan["found"] += 1

		def poll_scan():
			if not window.winfo_exists():
				return
			while True:
				try:
					kind, *payload = scan["queue"].get_nowait()
				except queue.Empty:
					break
				if kind == "progress":
					path, checked = payload
					options_status.config(
						text=f"Searching {short_path(path)} \u00b7 {checked:,} folders",
						fg=self.theme["muted"])
				elif kind == "found":
					version, path = payload
					apply_found(version, path)
				elif kind == "done":
					_checked, cancelled = payload
					scan["scanning"] = False
					auto_button.set_label("AUTO SEARCH")
					if cancelled:
						message, good = "Search stopped.", scan["found"] > 0
					elif scan["found"]:
						message, good = (f"Found {scan['found']} install(s). "
										 "Press SAVE to keep them."), True
					else:
						message, good = "No new installs found.", False
					options_status.config(
						text=message,
						fg=self.theme["success"] if good else self.theme["warning"])
			if scan["scanning"]:
				window.after(100, poll_scan)

		def start_scan():
			scan["cancel"] = threading.Event()
			scan["queue"] = queue.Queue()
			scan["scanning"] = True
			scan["found"] = 0
			auto_button.set_label("STOP SEARCH")
			options_status.config(text="Searching\u2026", fg=self.theme["muted"])
			threading.Thread(target=self.scan_for_installs,
							 args=(scan["queue"], scan["cancel"]), daemon=True).start()
			poll_scan()

		def toggle_scan():
			if scan["scanning"]:
				scan["cancel"].set()
				auto_button.set_label("STOPPING\u2026")
			else:
				start_scan()

		def on_options_destroyed(event):
			if event.widget is window:
				scan["cancel"].set()

		window.bind("<Destroy>", on_options_destroyed, add="+")
		self.style_popup(window, body)
		self.apply_theme(self.version.get(), subtree=window)

	def scan_roots(self):
		"""Drives (or common folders on other systems) to search for game installs."""
		if os.name == "nt":
			try:
				import ctypes
				import string
				kernel32 = ctypes.windll.kernel32
				mask = kernel32.GetLogicalDrives()
				roots = []
				for index, letter in enumerate(string.ascii_uppercase):
					root = f"{letter}:\\"
					# Drive types 2 and 3 are removable and fixed; skip network and optical.
					if mask & (1 << index) and kernel32.GetDriveTypeW(root) in (2, 3):
						roots.append(Path(root))
				if roots:
					return roots
			except (AttributeError, OSError):
				pass
			return [Path("C:/")]
		return [Path.home(), Path("/mnt"), Path("/media"), Path("/opt"),
				Path("/Applications"), Path("/Volumes")]

	def scan_for_installs(self, results, cancel):
		"""Breadth-first search for WoW client folders. Runs in a thread; never touches Tk."""
		folder_versions = {}
		for version, folders in AUTO_DETECT_SUBDIRECTORIES.items():
			for folder in folders:
				folder_versions.setdefault(folder.casefold(), []).append(version)
		pending = deque((root, 0) for root in self.scan_roots())
		checked = 0
		last_report = 0.0
		try:
			while pending and not cancel.is_set():
				current, depth = pending.popleft()
				checked += 1
				now = time.monotonic()
				if now - last_report > 0.15:
					results.put(("progress", str(current), checked))
					last_report = now
				try:
					entries = list(os.scandir(current))
				except OSError:
					continue
				for entry in entries:
					try:
						if not entry.is_dir(follow_symlinks=False):
							continue
					except OSError:
						continue
					name = entry.name.casefold()
					if name in folder_versions:
						for version in folder_versions[name]:
							if self.find_executable(version, entry.path, check_version=True) is not None:
								results.put(("found", version, entry.path))
						continue
					child_depth = depth + 1
					if name in SCAN_SKIP_NAMES or child_depth >= SCAN_MAX_DEPTH:
						continue
					if child_depth <= 2 or any(hint in name for hint in SCAN_HINTS):
						pending.append((Path(entry.path), child_depth))
		finally:
			results.put(("done", checked, cancel.is_set()))

	def open_setup_wizard(self):
		"""First launch: search every drive for game installs and let the user confirm them."""
		window = Overlay(self.root, 820, 680)

		body = window.make_body()
		tk.Label(body, text="FIRST-TIME SETUP", bg="#211c18", fg=GOLD,
				 font=self.ui_font(15, bold=True)).pack(anchor="w")
		tk.Label(body,
				 text="Welcome! The launcher is searching your drives for World of Warcraft "
					  "installs. Review what it finds, and use BROWSE to pick any folder it misses.",
				 bg="#211c18", fg="#c7baa0", font=self.ui_font(9),
				 wraplength=740, justify="left").pack(anchor="w", pady=(3, 8))
		tk.Frame(body, bg="#78613c", height=1).pack(fill="x", pady=(0, 6))
		scan_status = tk.Label(body, text="Preparing search\u2026", bg="#211c18", fg=MUTED,
							   font=self.ui_font(8, bold=True), anchor="w")
		scan_status.pack(fill="x", pady=(0, 6))

		rows = {}
		for version in self.game_versions:
			row = tk.Frame(body, bg="#181614", padx=8, pady=6)
			row.pack(fill="x", pady=3)
			tk.Label(row, text=version, width=25, anchor="w", bg="#181614",
					 fg=TEXT, font=self.ui_font(9, bold=True)).pack(side="left", padx=(2, 8))
			path_var = tk.StringVar(value=self.game_paths.get(version, ""))
			entry = tk.Entry(row, textvariable=path_var, bg="#171512", fg=TEXT,
							 insertbackground=TEXT, relief="flat", bd=1,
							 highlightthickness=1, highlightbackground="#51432f",
							 highlightcolor="#806b42", font=self.ui_font(9))
			entry.pack(side="left", fill="x", expand=True, padx=4, ipady=4)
			state_label = tk.Label(row, text="", width=14, anchor="w", bg="#181614",
								   fg=MUTED, font=self.ui_font(7, bold=True))
			state_label.pack(side="left", padx=(6, 0))
			StoneButton(row, text="BROWSE", command=lambda variable=path_var: self.browse_folder(
				window, variable), font=self.ui_font(8, bold=True), padx=8, pady=4,
				bg="#181614", theme_provider=lambda: self.theme
				).pack(side="left", padx=(6, 2))
			self.style_popup_row(row)
			rows[version] = (path_var, state_label)

		state = {"cancel": threading.Event(), "queue": queue.Queue(),
				 "scanning": False, "closed": False, "extras": {}}

		def is_ready(version, path):
			return bool(path) and self.find_executable(version, path) is not None

		def refresh_states(*_args):
			for version, (path_var, label) in rows.items():
				text = path_var.get().strip()
				if is_ready(version, text):
					label.config(text="\u25cf READY", fg=self.theme["success"])
				elif text:
					label.config(text="\u25cf CHECK PATH", fg=self.theme["warning"])
				elif state["scanning"]:
					label.config(text="\u25cf SEARCHING", fg=self.theme["muted"])
				else:
					label.config(text="\u25cf NOT FOUND", fg=self.theme["disabled"])

		def ready_count():
			return sum(1 for version, (path_var, _label) in rows.items()
					   if is_ready(version, path_var.get().strip()))

		def short_path(path, limit=78):
			return path if len(path) <= limit else "\u2026" + path[-(limit - 1):]

		def poll():
			if state["closed"] or not window.winfo_exists():
				return
			while True:
				try:
					kind, *payload = state["queue"].get_nowait()
				except queue.Empty:
					break
				if kind == "progress":
					path, checked = payload
					scan_status.config(
						text=f"SEARCHING  {short_path(path)}  \u00b7  {checked:,} folders checked",
						fg=self.theme["muted"])
				elif kind == "found":
					version, path = payload
					path_var = rows[version][0] if version in rows else None
					if path_var is not None and not path_var.get().strip():
						path_var.set(path)
					if version in EXTRA_VERSIONS and version not in rows:
						state["extras"].setdefault(version, path)
				elif kind == "done":
					checked, cancelled = payload
					state["scanning"] = False
					scan_button.set_label("SCAN AGAIN")
					found = ready_count()
					if cancelled:
						message = f"Search stopped. {found} install(s) ready."
					elif found:
						message = (f"Search complete: {found} install(s) ready "
								   f"({checked:,} folders checked). Use BROWSE for any that are missing.")
					else:
						message = ("No installs were found automatically. "
								   "Use BROWSE to select your game folders.")
					scan_status.config(
						text=message,
						fg=self.theme["success"] if found else self.theme["warning"])
					refresh_states()
			if state["scanning"]:
				window.after(100, poll)

		def start_scan():
			state["cancel"] = threading.Event()
			state["queue"] = queue.Queue()
			state["scanning"] = True
			scan_button.set_label("STOP SEARCH")
			scan_status.config(text="SEARCHING  starting\u2026", fg=self.theme["muted"])
			refresh_states()
			threading.Thread(target=self.scan_for_installs,
							 args=(state["queue"], state["cancel"]), daemon=True).start()
			poll()

		def toggle_scan():
			if state["scanning"]:
				state["cancel"].set()
				scan_button.set_label("STOPPING\u2026")
			else:
				start_scan()

		def finish(save):
			state["closed"] = True
			state["cancel"].set()
			if save:
				for version, (path_var, _label) in rows.items():
					self.game_paths[version] = path_var.get().strip()
				for name, extra_path in state["extras"].items():
					if not self.game_paths.get(name):
						self.game_paths[name] = extra_path
					self.enabled_extras.add(name)
			self.setup_complete = True
			window.destroy()
			self.apply_setup_results()

		def on_continue():
			if ready_count() == 0 and not messagebox.askyesno(
					"No installs selected",
					"No valid game folders are selected yet. Continue anyway?\n\n"
					"You can set them later from OPTIONS.", parent=window):
				return
			finish(True)

		footer = tk.Frame(body, bg="#211c18")
		footer.pack(fill="x", side="bottom", pady=(12, 0))
		scan_button = StoneButton(footer, text="STOP SEARCH", command=toggle_scan,
					font=self.ui_font(8, bold=True), padx=8, pady=4, bg="#211c18",
					theme_provider=lambda: self.theme)
		scan_button.pack(side="left")
		StoneButton(footer, text="CONTINUE", command=on_continue,
					font=self.ui_font(9, bold=True), padx=12, pady=4, bg="#211c18",
					theme_provider=lambda: self.theme).pack(side="right")
		StoneButton(footer, text="SKIP", command=lambda: finish(False),
					font=self.ui_font(8, bold=True), padx=8, pady=4, bg="#211c18",
					theme_provider=lambda: self.theme).pack(side="right", padx=(0, 6))

		window.protocol("WM_DELETE_WINDOW", lambda: finish(False))
		for path_var, _label in rows.values():
			path_var.trace_add("write", refresh_states)
		self.style_popup(window, body)
		self.apply_theme(self.version.get(), subtree=window)
		start_scan()

	def apply_setup_results(self):
		"""Refresh the version list and selection after first-time setup."""
		self.discovered_versions = self.discover_game_versions()
		self.game_versions = self.compute_game_versions()
		self.rebuild_version_menu()
		self.persist_settings()
		installed = [version for version in self.game_versions
					 if self.find_executable(
						 version, self.game_paths.get(version, "")) is not None]
		if installed and self.version.get() not in installed:
			self.version.set(installed[0])
		self.update_install_status()
		self.set_status(f"Setup complete: {len(installed)} game install(s) ready")

	def rebuild_version_menu(self):
		menu = self.version_menu["menu"]
		menu.delete(0, "end")
		for version in self.game_versions:
			menu.add_command(label=version, command=tk._setit(self.version, version))
		self.refresh_version_menu()

	def edit_launch_args(self, parent, version, variable):
		"""A small panel over Options for one game's launch arguments."""
		window = Overlay(self.root, 600, 340, reuse_scrim=parent.scrim)
		parent.place_forget()   # one panel at a time over the shared backdrop

		def restore(event):
			if event.widget is not window:
				return
			try:
				width, height = parent.panel_size
				parent.place(relx=.5, rely=.5, anchor="center", width=width, height=height)
				parent.lift()
				parent.grab_set()   # hand the modal grab back to Options
			except tk.TclError:
				pass

		def stone(parent_frame, text, command):
			return StoneButton(parent_frame, text=text, command=command,
							   font=self.ui_font(8, bold=True), padx=8, pady=4,
							   bg="#211c18", theme_provider=lambda: self.theme, style="subtle", version_colored=True)

		body = window.make_body()
		text_var = tk.StringVar(value=variable.get())

		def apply():
			variable.set(" ".join(text_var.get().splitlines()).strip())
			window.destroy()

		actions = tk.Frame(body, bg="#211c18")
		actions.pack(fill="x", side="bottom", pady=(10, 0))
		stone(actions, "OK", apply).pack(side="right")
		stone(actions, "CANCEL", window.destroy).pack(side="right", padx=(0, 8))
		stone(actions, "CLEAR", lambda: text_var.set("")).pack(side="left")

		tk.Label(body, text="LAUNCH ARGUMENTS", bg="#211c18", fg=GOLD,
				 font=self.ui_font(15, bold=True)).pack(anchor="w")
		tk.Label(body, text=f"{version}  \u00b7  used every time you press Play",
				 bg="#211c18", fg="#c7baa0", font=self.ui_font(8)
				 ).pack(anchor="w", pady=(3, 0))
		tk.Frame(body, bg="#78613c", height=1).pack(fill="x", pady=(10, 0))
		entry = tk.Entry(body, textvariable=text_var, bg="#171512", fg=TEXT,
						 insertbackground=TEXT, relief="flat", bd=1, highlightthickness=1,
						 highlightbackground="#51432f", highlightcolor="#806b42",
						 font=self.ui_font(10))
		entry.pack(fill="x", ipady=6, pady=(14, 8))
		entry.bind("<Return>", lambda _event: apply())
		entry.bind("<Escape>", lambda _event: window.destroy())
		hint = ("Separate arguments with spaces and put quotes around a value that contains "
				"spaces, for example -console. They are passed straight to the game, so only "
				"use arguments you trust. Leave this empty to launch normally.")
		if version in LOADER_VERSIONS:
			hint += (" This game starts through its own loader, so the arguments go to the "
					 "loader, which may not pass them on to the client.")
		tk.Label(body, text=hint, bg="#211c18", fg=MUTED, font=self.ui_font(8),
				 wraplength=520, justify="left").pack(anchor="w")

		window.bind("<Destroy>", restore, add="+")
		self.style_popup(window, body)
		self.apply_theme(self.version.get(), subtree=window)
		entry.focus_set()
		entry.icursor("end")

	def browse_folder(self, parent, variable):
		current = variable.get().strip()
		initial = current if Path(current).is_dir() else str(Path.home())
		selected = filedialog.askdirectory(parent=parent, initialdir=initial,
										   title="Choose game installation folder")
		if selected:
			variable.set(selected)

	def save_options(self, window, entries, options_status, arg_vars=None):
		self.game_paths.update({version: variable.get().strip()
								for version, variable in entries.items()})
		for version, variable in (arg_vars or {}).items():
			text = variable.get().strip()
			if text:
				self.launch_args[version] = text
			else:
				self.launch_args.pop(version, None)
		# An extra is shown while it has a folder; clearing the folder hides it again.
		for name in EXTRA_VERSIONS:
			if name in entries:
				if self.game_paths.get(name):
					self.enabled_extras.add(name)
				else:
					self.enabled_extras.discard(name)
		if not self.persist_settings():
			options_status.config(text="Could not save settings. Check folder permissions.",
								  fg=self.theme["error"])
			return
		self.register_friz_fonts()
		self.refresh_friz_font()
		self.game_versions = self.compute_game_versions()
		self.rebuild_version_menu()
		if self.version.get() not in self.game_versions:
			self.version.set(GAME_VERSIONS[0])
		self.update_install_status()
		self.set_status("Game settings saved")
		window.destroy()

	def draw_background(self, event):
		width, height = event.width, event.height
		if width <= 1 or height <= 1:
			return
		self.active_art_version = self.version.get() if hasattr(self, "version") else GAME_VERSIONS[0]
		self.switch_game_art(self.active_art_version)

	def draw_art(self, event):
		canvas = self.art
		w, h = event.width, event.height
		if w <= 1 or h <= 1:
			return
		canvas.delete("all")
		if self.slideshow_images:
			artwork = self.fitted_slide(self.slideshow_index, (w, h))
		else:
			artwork = self.selected_background(self.active_art_version)
			if artwork is not None:
				artwork = ImageOps.fit(artwork, (w, h), method=Image.Resampling.LANCZOS)
		if artwork is not None:
			self.render_art_frame(artwork)
			return
		canvas.create_rectangle(0, 0, w, h, fill=self.theme["panel_alt"], outline="")
		canvas.create_oval(w*.05, h*.03, w*.9, h*1.15,
						fill=self.theme["control"], outline=self.theme["border"], width=5)
		canvas.create_oval(w*.16, h*.12, w*.8, h*.97,
						fill=self.theme["panel"], outline=self.theme["accent"], width=2)
		canvas.create_oval(w*.25, h*.19, w*.72, h*.84,
						fill=self.theme["control_active"], outline=self.theme["border"], width=2)
		# Layered mountain silhouettes and a stylized guardian create a painted-scene feel.
		canvas.create_polygon(0, h*.69, w*.2, h*.43, w*.34, h*.62,
							  w*.53, h*.37, w*.77, h*.66, w, h*.47,
							  w, h, 0, h, fill=self.theme["panel_alt"], outline="")
		canvas.create_polygon(w*.14, h, w*.27, h*.54, w*.42, h*.48,
							  w*.58, h*.7, w*.73, h, fill=self.theme["window"],
							  outline=self.theme["border"], width=2)
		canvas.create_oval(w*.34, h*.4, w*.57, h*.64,
						fill=self.theme["panel"], outline=self.theme["accent"], width=3)
		canvas.create_polygon(w*.44, h*.43, w*.35, h*.26, w*.25, h*.32,
							  w*.34, h*.48, w*.27, h*.58, w*.4, h*.54,
							  w*.5, h*.7, w*.6, h*.52, w*.73, h*.57,
							  w*.63, h*.42, w*.69, h*.28, w*.57, h*.34,
							  w*.5, h*.22, fill=self.theme["control"],
							  outline=self.theme["accent"], width=2)
		canvas.create_oval(w*.42, h*.46, w*.45, h*.49, fill=self.theme["accent"], outline="")
		canvas.create_oval(w*.54, h*.46, w*.57, h*.49, fill=self.theme["accent"], outline="")
		canvas.create_text(w*.5, h*.79, text=self.art_text[0],
						  fill=self.theme["bright"], font=self.ui_font(16, bold=True),
						  width=w-36, justify="center")
		canvas.create_text(w*.5, h*.88, text=self.art_text[1],
						  fill=self.theme["accent"], font=self.ui_font(12, italic=True),
						  width=w-36, justify="center")

	def fitted_slide(self, index, size):
		key = (self.slideshow_key, index, size)
		frame = self._slide_cache.get(key)
		if frame is None:
			frame = ImageOps.fit(self.slideshow_images[index], size,
								 method=Image.Resampling.LANCZOS)
			self._slide_cache[key] = frame
		return frame

	def shade_art(self, artwork):
		"""Darken the lower part of the artwork with real transparency (Tk's canvas stipple
		is drawn solid on macOS). The result is cached per image and theme."""
		key = (id(artwork), self.theme["art_shadow"])
		hit = self._shaded_cache.get(key)
		if hit is not None and hit[0] is artwork:
			return hit[1]
		width, height = artwork.size
		overlay = Image.new("RGBA", artwork.size, (0, 0, 0, 0))
		ImageDraw.Draw(overlay).rectangle(
			(0, int(height * .69), width, height),
			fill=(*hex_rgb(self.theme["art_shadow"]), 128))
		result = Image.alpha_composite(artwork.convert("RGBA"), overlay).convert("RGB")
		if len(self._shaded_cache) > 12:
			self._shaded_cache.clear()
		self._shaded_cache[key] = (artwork, result)
		return result

	def render_art_frame(self, artwork, shaded=None):
		self.current_art_frame = artwork
		if shaded is None:
			shaded = self.shade_art(artwork)
		image = shaded.convert("RGBA")
		width, height = image.size
		mask = self._art_mask_cache.get((width, height))
		if mask is None:
			mask = make_rounded_mask(width, height, 10)
			mask_draw = ImageDraw.Draw(mask)
			mask_draw.rectangle((10, 0, width - 1, height - 1), fill=255)
			self._art_mask_cache[(width, height)] = mask
		image.putalpha(mask)
		self.art_photo = ImageTk.PhotoImage(image, master=self.root)
		self.art.delete("all")
		self.art.create_image(0, 0, image=self.art_photo, anchor="nw")
		self.draw_art_text()

	def draw_art_text(self):
		"""(Re)draw just the headline and tagline; the image is left untouched."""
		width, height = self.art.winfo_width(), self.art.winfo_height()
		self.art.delete("art_text")
		headline, tagline = self.art_text
		fade = self.art_text_fade
		if fade > .03 and width > 1:
			self.art.create_text(width*.5, height*.79, text=headline,
							 fill=blend_hex(self.theme["art_shadow"], self.theme["bright"], fade),
							 font=self.ui_font(16, bold=True), tags="art_text",
							 width=width-36, justify="center")
			self.art.create_text(width*.5, height*.88, text=tagline,
							 fill=blend_hex(self.theme["art_shadow"], self.theme["accent"], fade),
							 font=self.ui_font(12, italic=True), tags="art_text",
							 width=width-36, justify="center")

	def selected_game_directory(self):
		version = self.version.get()
		install_path = self.game_paths.get(version, "")
		if not install_path:
			self.set_status(f"Choose the {version} folder in Options first")
			return None
		executable = self.find_executable(version, install_path)
		if executable is not None:
			return executable.parent
		base = Path(install_path).expanduser()
		if base.is_dir():
			for subdirectory in INSTALL_SUBDIRECTORIES.get(version, ("",)):
				candidate = base / subdirectory if subdirectory else base
				if candidate.is_dir():
					return candidate
		self.set_status(f"The {version} install folder is unavailable")
		return None

	def open_directory(self, directory, description):
		if not directory.is_dir():
			self.set_status(f"No {description} folder found for {self.version.get()}")
			return
		try:
			if os.name == "nt":
				os.startfile(str(directory))
			else:
				command = "open" if sys.platform == "darwin" else "xdg-open"
				subprocess.Popen([command, str(directory)])
		except OSError as error:
			self.set_status(f"Could not open {description} folder")
			messagebox.showerror("Could not open folder", str(error), parent=self.root)
			return
		self.set_status(f"Opened {description} folder")

	def open_game_folder(self):
		directory = self.selected_game_directory()
		if directory is not None:
			self.open_directory(directory, "game")

	def open_addons_folder(self):
		directory = self.selected_game_directory()
		if directory is not None:
			self.open_directory(directory / "Interface" / "AddOns", "AddOns")

	def open_addon_manager(self):
		directory = self.selected_game_directory()
		if directory is not None:
			AddonManager(self, directory / "Interface" / "AddOns",
						 self.version.get(), StoneButton)

	def open_game_config(self, parent=None, paths=None):
		"""Edit each installation's WTF/Config.wtf from Options."""
		parent = parent or self.root
		paths = self.game_paths if paths is None else paths
		window = Overlay(parent, 820, 570)
		body = window.make_body()
		panel = self.theme["panel"]
		body.config(bg=panel)
		selected = tk.StringVar(value=self.version.get())
		state = {"path": None, "original": None, "newline": "\n", "bom": False}
		top = tk.Frame(body, bg=panel)
		top.pack(fill="x", pady=(0, 8))
		tk.Label(top, text="GAME CONFIG", bg=panel, fg=self.theme["accent"],
			font=self.ui_font(15, bold=True)).pack(side="left")
		menu = tk.OptionMenu(top, selected, *paths, command=lambda _: load())
		menu.config(bg=self.theme["control"], fg=self.theme["text"],
			font=self.ui_font(9), bd=0, highlightthickness=0)
		menu.pack(side="right")
		footer = tk.Frame(body, bg=panel)
		footer.pack(side="bottom", fill="x", pady=(10, 0))
		status = tk.Label(body, bg=panel, fg=self.theme["muted"],
			font=self.ui_font(8), anchor="w", wraplength=740)
		status.pack(side="bottom", fill="x", pady=(8, 0))
		area = tk.Frame(body, bg=panel)
		area.pack(fill="both", expand=True)
		editor = tk.Text(area, wrap="none", undo=True, bg=self.theme["control"],
			fg=self.theme["text"], insertbackground=self.theme["text"],
			font=("Courier", 10), bd=0, padx=8, pady=8)
		vertical = tk.Scrollbar(area, orient="vertical", command=editor.yview)
		horizontal = tk.Scrollbar(area, orient="horizontal", command=editor.xview)
		editor.config(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
		area.rowconfigure(0, weight=1)
		area.columnconfigure(0, weight=1)
		editor.grid(row=0, column=0, sticky="nsew")
		vertical.grid(row=0, column=1, sticky="ns")
		horizontal.grid(row=1, column=0, sticky="ew")

		def load():
			version = selected.get()
			value = paths.get(version, "")
			folder = value.get().strip() if hasattr(value, "get") else str(value).strip()
			state.update(path=None, original=None)
			editor.config(state="normal")
			editor.delete("1.0", "end")
			try:
				if not folder:
					raise ValueError("Choose an installation folder in Options first.")
				executable = self.find_executable(version, folder)
				base = Path(folder).expanduser()
				candidates = [base / sub for sub in INSTALL_SUBDIRECTORIES.get(version, ("",))]
				directory = executable.parent if executable is not None else next(
					(candidate for candidate in candidates if (candidate / "WTF" / "Config.wtf").is_file()), base)
				path = directory / "WTF" / "Config.wtf"
				raw = path.read_bytes()
				text = raw.decode("utf-8-sig")
				state.update(path=path, original=raw, newline="\r\n" if b"\r\n" in raw else "\n",
					bom=raw.startswith(b"\xef\xbb\xbf"))
				editor.insert("1.0", text.replace("\r\n", "\n"))
				status.config(text=str(path), fg=self.theme["muted"])
			except (OSError, ValueError) as error:
				status.config(text=f"Cannot load Config.wtf: {error}", fg=self.theme["warning"])
				editor.config(state="disabled")
			editor.edit_reset()

		def save():
			path = state["path"]
			if path is None:
				return
			try:
				if path.read_bytes() != state["original"]:
					raise ValueError("The file changed outside the editor. Reopen it before saving.")
				text = editor.get("1.0", "end-1c").replace("\n", state["newline"])
				raw = text.encode("utf-8-sig" if state["bom"] else "utf-8")
				path.with_name("Config.wtf.bak").write_bytes(state["original"])
				path.write_bytes(raw)
				state["original"] = raw
				status.config(text="Config.wtf saved. Backup: Config.wtf.bak", fg=self.theme["success"])
			except (OSError, ValueError) as error:
				status.config(text=f"Could not save: {error}", fg=self.theme["warning"])

		for caption, command in (("CLOSE", window.destroy), ("SAVE", save)):
			StoneButton(footer, text=caption, command=command, font=self.ui_font(9, bold=True),
				bg=panel, theme_provider=lambda: self.theme, style="subtle", version_colored=True, padx=8, pady=4).pack(side="right", padx=(6, 0))
		load()

	def open_armory(self):
		"""Look up a character by name and realm and show its summary."""
		panel = self.theme["panel"]
		window = Overlay(self.root, 860, 360)
		body = window.make_body()
		search_page = tk.Frame(body, bg=self.theme["panel"])
		search_page.pack(fill="both", expand=True)
		character_page = tk.Frame(body, bg=self.theme["panel"])
		saved = self.armory
		state = {"busy": False, "url": "", "photo": None, "queue": queue.Queue(),
				 "result": None, "generation": 0, "version": self.version.get(), "poll": None, "tab": "Overview", "categories": {}, "category_pending": set(), "icon_bytes": {}, "icon_pending": set(),
				 "icon_watchers": {}, "icon_photos": {}}
		characters = self.armory.setdefault("characters", [])

		def stone(parent, text, command, size=8, padx=8, pady=4):
			return StoneButton(parent, text=text, command=command,
							   font=self.ui_font(size, bold=True), padx=padx, pady=pady,
							   bg=panel, theme_provider=lambda: self.theme, style="subtle")

		def field(parent, caption, value, width, show=""):
			column = tk.Frame(parent, bg=panel)
			tk.Label(column, text=caption, bg=panel, fg=self.theme["muted"], anchor="w",
					 font=self.ui_font(7, bold=True)).pack(anchor="w")
			variable = tk.StringVar(value=value)
			entry = tk.Entry(column, textvariable=variable, width=width, show=show,
							 bg=self.theme["control"], fg=self.theme["text"], insertbackground=self.theme["text"], relief="flat", bd=0,
							 highlightthickness=0, highlightbackground=self.theme["border"],
							 highlightcolor=self.theme["accent"], font=self.ui_font(9))
			entry.pack(fill="x", ipady=4)
			return column, variable, entry

		top = tk.Frame(search_page, bg=panel)
		top.pack(fill="x", pady=(0, 12))
		tk.Label(top, text="CHARACTER ARMORY", bg=panel, fg=self.theme["accent"],
				 font=self.ui_font(15, bold=True)).pack(side="left")
		version_var = self.version
		version_menu = tk.OptionMenu(top, version_var,
			*dict.fromkeys((*GAME_VERSIONS, *self.game_versions)),
			command=lambda _value: sync_version())
		version_menu.config(bg=self.theme["control"], fg=self.theme["text"],
			activebackground=self.theme["control_active"], activeforeground=self.theme["bright"],
			relief="flat", bd=0, highlightthickness=0, font=self.ui_font(9), padx=10, pady=4)
		version_menu.pack(side="right")

		form = tk.Frame(search_page, bg=panel)
		form.pack(fill="x")
		name_box, name_var, name_entry = field(form, "CHARACTER", saved.get("name", ""), 18)
		realm_box, realm_var, realm_entry = field(form, "REALM", saved.get("realm", ""), 22)
		region_box, region_var, region_entry = field(form, "REGION", saved.get("region", "us"), 5)
		name_box.pack(side="left", padx=(0, 8))
		realm_box.pack(side="left", fill="x", expand=True, padx=(0, 8))
		region_box.pack(side="left")

		action_row = tk.Frame(search_page, bg=panel)
		action_row.pack(fill="x", pady=(10, 0))
		lookup_status = tk.Label(action_row, text="", bg=panel, fg=self.theme["muted"], anchor="w",
								 justify="left", wraplength=480, font=self.ui_font(8))
		lookup_button = stone(action_row, "LOOK UP", lambda: start_lookup(), size=9, padx=12)
		lookup_button.pack(side="left", padx=(0, 10))
		lookup_status.pack(side="left", fill="x", expand=True)

		# Bottom first so it is never pushed off the panel.
		footer = tk.Frame(search_page, bg=panel)
		footer.pack(fill="x", side="bottom", pady=(10, 0))
		stone(footer, "CLOSE", window.destroy).pack(side="right")
		stone(footer, "VIEW CHARACTER", lambda: view_character()).pack(side="left")
		search_summary = tk.Label(search_page, text="Search for a character or choose a saved profile.",
			bg=self.theme["panel"], fg=self.theme["muted"], font=self.ui_font(10), anchor="w")
		search_summary.pack(fill="x", side="bottom", pady=(8, 4))

		# One compact row; the picker expands only when opened.
		library_row = tk.Frame(search_page, bg=panel)
		library_row.pack(fill="x", pady=(8, 8))
		tk.Label(library_row, text="SAVED", bg=panel, fg=self.theme["muted"],
				 font=self.ui_font(7, bold=True)).pack(side="left", padx=(0, 8))
		saved_var = tk.StringVar(value="No saved characters")
		character_picker = tk.OptionMenu(library_row, saved_var, "No saved characters")
		character_picker.config(bg=self.theme["control"], fg=self.theme["text"],
			activebackground=self.theme["control_active"], activeforeground=self.theme["bright"],
			relief="flat", bd=0, highlightthickness=0, anchor="w", font=self.ui_font(8))
		character_picker.pack(side="left", fill="x", expand=True, padx=(0, 8))
		stone(library_row, "SAVE", lambda: save_character(), pady=2).pack(side="left")
		stone(library_row, "REMOVE", lambda: remove_character(), pady=2).pack(side="left", padx=(4, 0))
		visible_characters = []
		saved_choices = {}

		viewer_toolbar = tk.Frame(character_page, bg=self.theme["panel"])
		viewer_toolbar.pack(fill="x", pady=(0, 8))
		stone(viewer_toolbar, "BACK", lambda: show_search()).pack(side="left")
		stone(viewer_toolbar, "REFRESH", lambda: start_lookup()).pack(side="left", padx=4)
		stone(viewer_toolbar, "COPY PROFILE", lambda: copy_profile()).pack(side="left")
		stone(viewer_toolbar, "OPEN ARMORY PAGE", lambda: open_page()).pack(side="right")
		stone(viewer_toolbar, "CLOSE", window.destroy).pack(side="right", padx=4)
		viewer_status = tk.Label(character_page, text="", bg=self.theme["panel"], fg=self.theme["muted"],
			font=self.ui_font(8), anchor="w")
		viewer_status.pack(fill="x", side="bottom", pady=(3, 0))

		def show_search():
			hide_tooltip()
			character_page.pack_forget()
			window.resize(360)
			search_page.pack(fill="both", expand=True)
			name_entry.focus_set()

		def view_character():
			if not state["result"]:
				lookup_status.config(text="Look up or select a character first.", fg=self.theme["warning"])
				return
			search_page.pack_forget()
			window.resize(700)
			character_page.pack(fill="both", expand=True)
			render_details()

		tab_row = tk.Frame(character_page, bg=panel)
		tab_row.pack(fill="x", pady=(0, 4))
		tab_buttons = {}
		for title in ("Overview", "Equipment", "Stats", "Professions", "Progress", "Guild", "Achievements"):
			button = stone(tab_row, title.upper(), lambda value=title: switch_tab(value), size=7, padx=5, pady=2)
			button.pack(side="left", padx=(0, 4))
			tab_buttons[title] = button

		detail_area = tk.Frame(character_page, bg=panel)
		detail_area.pack(fill="both", expand=True, pady=(12, 8))
		results = tk.Frame(detail_area, bg=panel)
		results.pack(fill="both", expand=True)
		filter_var = tk.StringVar(value="")

		def request_icon(kind, identifier, target, size=32, tree_item=None):
			result = state["result"]
			if not result or not isinstance(identifier, int) or identifier <= 0:
				return
			key = (result["region"], result["version"], kind, identifier)
			watcher = (state["generation"], target, tree_item, size)
			if key in state["icon_bytes"]:
				apply_icon(key, state["icon_bytes"][key], watcher)
				return
			state["icon_watchers"].setdefault(key, []).append(watcher)
			if key in state["icon_pending"]:
				return
			state["icon_pending"].add(key)
			if "icon_executor" not in state:
				state["icon_executor"] = ThreadPoolExecutor(max_workers=4, thread_name_prefix="armory-icons")
			cache_dir = self.settings_path.parent / "armory-icons"
			def download():
				try:
					data = fetch_media_icon(*key, cache_dir=cache_dir)
				except Exception:
					data = None
				state["queue"].put((0, "icon", (key, data), None))
			state["icon_executor"].submit(download)

		def apply_icon(key, data, watcher):
			generation, target, tree_item, size = watcher
			if not data or generation != state["generation"] or not target.winfo_exists():
				return
			photo_key = (key, size)
			photo = state["icon_photos"].get(photo_key)
			if photo is None:
				try:
					image = Image.open(io.BytesIO(data)).convert("RGBA")
					image = ImageOps.fit(image, (size, size), method=Image.Resampling.LANCZOS)
					photo = ImageTk.PhotoImage(image, master=self.root)
				except (OSError, ValueError, Image.DecompressionBombError):
					return
				state["icon_photos"][photo_key] = photo
			if tree_item is not None:
				if target.exists(tree_item):
					target.item(tree_item, image=photo)
			else:
				target.config(image=photo, text="")
				target.image = photo

		def refresh_achievement_icons():
			tree = state.get("achievement_tree")
			if state["tab"] != "Achievements" or not tree or not tree.winfo_exists() or not tree.winfo_viewable():
				return
			visible = {tree.identify_row(y) for y in range(12, tree.winfo_height(), 13)}
			for item in visible:
				identifier = state.get("achievement_icon_nodes", {}).get(item)
				if identifier is not None and item not in state["achievement_icon_requested"]:
					state["achievement_icon_requested"].add(item)
					request_icon("achievement", identifier, tree, size=18, tree_item=item)

		def refresh_section_tabs():
			support = supported_character_sections(self.version.get(), state["result"])
			visible = []
			for title, button in tab_buttons.items():
				button.pack_forget()
				if support.get(title, True):
					button.pack(side="left", padx=(0, 4))
					visible.append(title)
			if state["tab"] not in visible:
				state["tab"] = "Overview"
			return support

		def show_result(result, avatar=None, cached=False):
			self.clear_children(results)
			state["result"] = result
			support = refresh_section_tabs()
			state["categories"], state["category_pending"] = {}, set()
			state["url"] = result.get("profile_url", "")
			photo = None
			if avatar:
				try:
					image = Image.open(io.BytesIO(avatar)).convert("RGBA")
					image = ImageOps.fit(image, (52, 52), method=Image.Resampling.LANCZOS)
					image.putalpha(make_rounded_mask(52, 52, 7))
					photo = ImageTk.PhotoImage(image, master=self.root)
				except (OSError, ValueError):
					photo = None
			state["photo"] = photo   # keep a reference or Tk drops the image
			header = tk.Frame(results, bg=self.theme["surface"], padx=10, pady=8)
			header.pack(fill="x")
			if photo is not None:
				tk.Label(header, image=photo, bg=self.theme["surface"], bd=0).pack(side="left", padx=(0, 12))
			titles = tk.Frame(header, bg=self.theme["surface"])
			titles.pack(side="left", fill="x", expand=True)
			tk.Label(titles, text=result["name"], bg=self.theme["surface"], fg=self.theme["accent"], anchor="w",
					 font=self.ui_font(15, bold=True)).pack(anchor="w")
			level = f"Level {result['level']} " if result.get("level") else ""
			summary = " ".join(part for part in (
				result.get("race"), result.get("spec"), result.get("class")) if part)
			tk.Label(titles, text=(level + summary).strip(), bg=self.theme["surface"], fg=self.theme["text"],
					 anchor="w", font=self.ui_font(10)).pack(anchor="w")
			self.style_popup_row(header)
			metrics = tk.Frame(results, bg=self.theme["panel"])
			metrics.pack(fill="x", pady=(4, 4))
			for column, (label, value) in enumerate((
				("ITEM LEVEL", result.get("item_level")),
				*([("ACHIEVEMENTS", result.get("achievement_points"))] if support["Achievements"] else []),
				("FACTION", result.get("faction")),
				("GUILD", result.get("guild")))):
				metrics.columnconfigure(column, weight=1, uniform="metrics")
				card = tk.Frame(metrics, bg=self.theme["surface"], padx=8, pady=4)
				card.grid(row=0, column=column, sticky="nsew", padx=(0, 6))
				tk.Label(card, text=label, bg=self.theme["surface"], fg=self.theme["muted"],
					font=self.ui_font(7, bold=True), anchor="w").pack(fill="x")
				tk.Label(card, text=str(value) if value not in (None, "") else "Unavailable",
					bg=self.theme["surface"], fg=self.theme["text"], anchor="w",
					wraplength=160, font=self.ui_font(10, bold=True)).pack(fill="x", pady=(3, 0))
				self.style_popup_row(card)
			tk.Label(results, text=f"{'Saved snapshot' if cached else 'Updated just now'} • {result['source']} • {result['realm']} / {result['region'].upper()}",
				bg=self.theme["panel"], fg=self.theme["muted"], anchor="w",
				font=self.ui_font(7)).pack(fill="x", side="bottom", pady=(3, 0))
			search_row = tk.Frame(results, bg=self.theme["panel"])
			search_row.pack(fill="x", side="bottom", pady=(4, 0))
			view_note = tk.Label(search_row, text="", bg=self.theme["panel"], fg=self.theme["muted"], font=self.ui_font(7))
			view_note.pack(side="left")
			tk.Entry(search_row, textvariable=filter_var, bg=self.theme["control"], fg=self.theme["text"],
				insertbackground=self.theme["text"], relief="flat", bd=0, highlightthickness=0,
				font=self.ui_font(8), width=20).pack(side="right", ipady=3)
			tk.Label(search_row, text="FIND", bg=self.theme["panel"], fg=self.theme["muted"], font=self.ui_font(7)).pack(side="right", padx=5)
			content = tk.Frame(results, bg=self.theme["panel"])
			content.pack(fill="both", expand=True)
			state.update(content=content, view_note=view_note)
			filter_var.set("")
			content.bind("<Configure>", lambda _event: schedule_details())
			render_details()
			lookup_status.config(text="Saved profile. Refresh to check for changes." if cached else "Character loaded. Save it for quick access.", fg=self.theme["muted"])
			self.apply_theme(self.version.get(), subtree=window)
			search_summary.config(text=f"{result['name']} • {result['realm']} • {result['version']}\nSave this character or select View Character.")
			viewer_status.config(text="Saved profile" if cached else "Character loaded")
			view_character()

		def switch_tab(title):
			support = refresh_section_tabs()
			state["tab"] = title if support.get(title, True) else "Overview"
			filter_var.set("")
			if state["result"]:
				render_details()

		def schedule_details():
			if state["result"] and body.winfo_exists():
				# Debounce actual size changes; child creation must not start a render loop.
				content = state.get("content")
				if content and (content.winfo_width(), content.winfo_height()) != state.get("size"):
					if state.get("resize"):
						self.root.after_cancel(state["resize"])
					state["resize"] = self.root.after(40, render_details)

		def render_details():
			state["resize"] = None
			hide_tooltip()
			content = state.get("content")
			if not content or not content.winfo_exists() or not state["result"]:
				return
			self.clear_children(content)
			refresh_section_tabs()
			result, title = state["result"], state["tab"]
			if title == "Achievements":
				for label, button in tab_buttons.items():
					button.set_label(label.upper() + (" •" if label == title else ""))
				render_achievements(content, result)
				return
			for label, button in tab_buttons.items():
				button.set_label(label.upper() + (" •" if label == title else ""))
			if title == "Overview":
				rows = [("Game version", result.get("version")), ("Realm", result.get("realm")),
					("Region", result.get("region", "").upper()), ("Race", result.get("race")),
					("Class", result.get("class")), ("Specialization", result.get("spec")),
					("Level", result.get("level")), ("Last login", result.get("last_login")),
					*result.get("extras", [])]
			else:
				rows = result.get("details", {}).get(title.lower(), [])
			query_text = filter_var.get().strip().casefold()
			if query_text:
				rows = [(caption, value) for caption, value in rows if query_text in f"{caption} {value}".casefold()]
			width, height = content.winfo_width(), content.winfo_height()
			state["size"] = (width, height)
			font = self.ui_font(8)
			line_height = font.metrics("linespace")
			columns = 3 if width >= 720 else 2 if width >= 500 else 1
			capacity = max(columns, columns * max(1, height // (line_height * 2 + 10)))
			total = len(rows)
			# Very large lists use an honest summary, never another hidden page.
			if title in ("Guild", "Achievements", "Progress", "Professions") and total > capacity:
				if title == "Progress" and not query_text:
					rows = rows[-capacity:]
				else:
					rows = rows[:capacity]
				state["view_note"].config(text=f"Showing {len(rows)} of {total} entries • use Find to narrow results")
			else:
				state["view_note"].config(text="Hover over a value for full details")
			if not rows:
				message = "No matches. Try another search." if query_text else result.get("detail_messages", {}).get(title.lower(),
					"These details are unavailable for this character or game version.")
				tk.Label(content, text=message, bg=self.theme["panel"], fg=self.theme["muted"],
					font=font, justify="left", anchor="w", wraplength=max(180, width - 24)).pack(fill="x", pady=8)
				return
			row_count = (len(rows) + columns - 1) // columns
			# Place cells within the existing viewport, so labels cannot grow it.
			cell_width = max(70, width // columns - 24)
			def fit_text(text, available=cell_width):
				if font.measure(text) <= available:
					return text
				while text and font.measure(text + "…") > available:
					text = text[:-1]
				return text + "…"
			for index, (caption, value) in enumerate(rows):
				text = str(value) if value not in (None, "") else "Unavailable"
				card = tk.Frame(content, bg=self.theme["surface"], padx=6, pady=2)
				card.place(relx=(index % columns) / columns, rely=(index // columns) / row_count,
					relwidth=1 / columns, relheight=1 / row_count)
				ref = (result.get("icon_refs", {}).get(title.lower(), {})).get(caption)
				text_parent, text_width = card, cell_width
				if isinstance(ref, dict):
					icon = tk.Label(card, text="□", bg=self.theme["surface"], fg=self.theme["muted"],
						font=self.ui_font(18), bd=0, padx=3)
					icon.pack(side="left", padx=(0, 6))
					text_parent = tk.Frame(card, bg=self.theme["surface"])
					text_parent.pack(side="left", fill="both", expand=True)
					text_width = max(40, cell_width - 42)
					request_icon(ref.get("kind"), ref.get("id"), icon)
				tk.Label(text_parent, text=fit_text(caption, text_width), bg=self.theme["surface"], fg=self.theme["accent"],
					anchor="w", font=self.ui_font(7, bold=True)).pack(fill="x")
				# Equipment names are prominent; enchants and gems remain in the hover detail.
				preview = text.replace("\n", " • ")
				label = tk.Label(text_parent, text=fit_text(preview, text_width), bg=self.theme["surface"], fg=self.theme["text"],
					anchor="w", font=font)
				label.pack(fill="x")
				# Bind the value once; parent/child Enter events must not create competing popups.
				label.bind("<Enter>", lambda _event, source=label, heading=caption, detail=text:
					queue_tooltip(source, heading, detail))
				label.bind("<Leave>", lambda _event: hide_tooltip())
				label.bind("<ButtonPress-1>", lambda _event: hide_tooltip())
				label.bind("<Destroy>", lambda event: cancel_source_tooltip(event.widget))

		def render_achievements(content, result):
			state["view_note"].config(text="Expand a category to see its achievements; collapse it to free space")
			style_name = f"Armory{str(id(window))}.Treeview"
			style = ttk.Style(self.root)
			style.configure(style_name, background=self.theme["surface"], fieldbackground=self.theme["surface"],
				foreground=self.theme["text"], borderwidth=0, font=self.ui_font(9), rowheight=26)
			style.configure(style_name + ".Heading", background=self.theme["control"], foreground=self.theme["text"], font=self.ui_font(8, bold=True))
			style.map(style_name, background=[("selected", self.theme["control_active"])],
				foreground=[("selected", self.theme["bright"])])
			tree = ttk.Treeview(content, style=style_name, columns=("completed",), show="tree headings", selectmode="browse")
			tree.heading("#0", text="Category / achievement")
			tree.heading("completed", text="Completed")
			tree.column("#0", width=490, minwidth=220)
			tree.column("completed", width=160, minwidth=140, stretch=False)
			scrollbar = ttk.Scrollbar(content, orient="vertical", command=tree.yview)
			scrollbar.pack(side="right", fill="y")
			tree.config(yscrollcommand=scrollbar.set)
			tree.pack(fill="both", expand=True)
			state["achievement_tree"] = tree
			state["achievement_icon_nodes"], state["achievement_icon_requested"] = {}, set()
			completed = result.get("achievement_records", [])
			query_text = filter_var.get().strip().casefold()
			if query_text:
				for item in completed:
					if query_text in f"{item['name']} {item['completed']}".casefold():
						row = tree.insert("", "end", text=item["name"], values=(item["completed"],))
						state["achievement_icon_nodes"][row] = item["id"]
				return
			history_note = f"{len(completed)} completed achievements" if "achievement_records" in result else "Refresh to load achievement history"
			tree.insert("", "end", text=f"{result.get('achievement_points', 'Unavailable')} points • {history_note}")
			roots = result.get("achievement_categories", [])
			for category in roots:
				add_category(tree, "", category)
			tree.insert("", "end", iid="all-history", text="All completed achievements", open=False)
			tree.insert("all-history", "end", text="Expand to view saved achievement history")
			tree.bind("<<TreeviewOpen>>", lambda _event: expand_category(tree))

		def add_category(tree, parent, category):
			key = f"category:{category['id']}"
			if tree.exists(key):
				return
			tree.insert(parent, "end", iid=key, text=category["name"], open=False)
			tree.insert(key, "end", iid=key + ":placeholder", text="Expand to load category")

		def expand_category(tree):
			key = tree.focus()
			if key == "all-history":
				for child in tree.get_children(key):
					tree.delete(child)
				for item in state["result"].get("achievement_records", []):
					row = tree.insert(key, "end", text=item["name"], values=(item["completed"],))
					state["achievement_icon_nodes"][row] = item["id"]
				return
			if not key.startswith("category:") or key.endswith(":placeholder"):
				return
			category_id = int(key.split(":")[1])
			if category_id in state["categories"]:
				fill_category(tree, key, state["categories"][category_id])
				return
			if category_id in state["category_pending"]:
				return
			state["category_pending"].add(category_id)
			generation = state["generation"]
			region, version = state["result"]["region"], state["result"]["version"]
			def load():
				try:
					payload = lookup_achievement_category(region, version, category_id)
				except ArmoryError as error:
					payload = {"error": str(error)}
				except Exception:
					payload = {"error": "Category could not be loaded."}
				state["queue"].put((generation, "category", (category_id, payload), None))
			threading.Thread(target=load, daemon=True).start()

		def fill_category(tree, key, data):
			if not tree.winfo_exists() or not tree.exists(key):
				return
			for child in tree.get_children(key):
				tree.delete(child)
			if data.get("error"):
				tree.insert(key, "end", text=data["error"])
				return
			for child in data.get("subcategories", []):
				add_category(tree, key, child)
			ids = {item["id"] for item in data.get("achievements", [])}
			rows = [item for item in state["result"].get("achievement_records", []) if item["id"] in ids]
			for item in rows:
				row = tree.insert(key, "end", text=item["name"], values=(item["completed"],))
				state["achievement_icon_nodes"][row] = item["id"]
			if not rows and not data.get("subcategories"):
				tree.insert(key, "end", text="No completed achievements in this category")

		def hide_tooltip():
			state["hover_generation"] = state.get("hover_generation", 0) + 1
			timer = state.pop("tooltip_timer", None)
			if timer is not None:
				self.root.after_cancel(timer)
			state.pop("tooltip_source", None)
			tooltip = state.pop("tooltip", None)
			if tooltip is not None and tooltip.winfo_exists():
				tooltip.destroy()

		def cancel_source_tooltip(source):
			if state.get("tooltip_source") is source:
				hide_tooltip()

		def still_hovering(source):
			try:
				if not source.winfo_exists() or not source.winfo_viewable() or self.root.focus_displayof() is None:
					return False
				x, y = self.root.winfo_pointerxy()
				return self.root.winfo_containing(x, y) is source
			except tk.TclError:
				return False

		def queue_tooltip(source, heading, detail):
			hide_tooltip()
			state["tooltip_source"] = source
			generation = state["hover_generation"]
			def reveal():
				state.pop("tooltip_timer", None)
				if generation != state.get("hover_generation") or not still_hovering(source):
					return
				show_tooltip(source, heading, detail)
			state["tooltip_timer"] = self.root.after(350, reveal)

		def show_tooltip(source, heading, detail):
			# Keep the hover inside the existing Tk panel. No native popup window
			# and no desktop-coordinate geometry, including on multiple Mac displays.
			tooltip = tk.Frame(body, bg=self.theme["surface"], bd=0, highlightthickness=1,
				highlightbackground=self.theme["border"], takefocus=False)
			state["tooltip"] = tooltip
			tk.Label(tooltip, text=f"{heading}\n{detail}", bg=self.theme["surface"], fg=self.theme["text"],
				font=self.ui_font(9), justify="left", wraplength=max(80, min(420, body.winfo_width() - 36)),
				padx=10, pady=8, takefocus=False).pack()
			tooltip.update_idletasks()
			if not still_hovering(source):
				hide_tooltip()
				return
			width, height = tooltip.winfo_reqwidth(), tooltip.winfo_reqheight()
			x = source.winfo_rootx() - body.winfo_rootx()
			y = source.winfo_rooty() - body.winfo_rooty() + source.winfo_height() + 6
			if y + height > body.winfo_height() - 8:
				y = source.winfo_rooty() - body.winfo_rooty() - height - 6
			x = max(8, min(x, body.winfo_width() - width - 8))
			y = max(8, min(y, body.winfo_height() - height - 8))
			tooltip.place(x=x, y=y)
			tooltip.lift()

		def filter_details(*_args):
			if state["result"]:
				render_details()
		filter_var.trace_add("write", filter_details)

		def identity(item):
			return (item.get("version"), item.get("region", "").casefold(),
					realm_slug(item.get("realm", "")), item.get("name", "").casefold())

		def refresh_list():
			visible_characters[:] = [item for item in characters if item["version"] == self.version.get()]
			saved_choices.clear()
			menu = character_picker["menu"]
			menu.delete(0, "end")
			for item in visible_characters:
				label = f"{item['name']} — {item['realm']} ({item['region'].upper()})"
				saved_choices[label] = item
				menu.add_command(label=label, command=lambda value=label: select_character(value))
			if not visible_characters:
				saved_var.set("No saved characters for this version")
				menu.add_command(label="No saved characters", state="disabled")
			elif saved_var.get() not in saved_choices:
				saved_var.set(f"Choose a character ({len(visible_characters)} saved)")
			menu.config(bg=self.theme["control"], fg=self.theme["text"],
				activebackground=self.theme["control_active"], activeforeground=self.theme["bright"])

		def save_character():
			result = state["result"]
			if not result or result.get("version") != self.version.get():
				lookup_status.config(text="Look up a character first.", fg=self.theme["warning"])
				return
			item = {key: result[key] for key in ("region", "realm", "name", "version")}
			item.update(snapshot=result, updated=time.strftime("%Y-%m-%d %H:%M"))
			for index, previous in enumerate(characters):
				if identity(previous) == identity(item):
					characters[index] = item
					break
			else:
				characters.append(item)
			self.persist_settings()
			refresh_list()
			saved_var.set(f"{item['name']} — {item['realm']} ({item['region'].upper()})")
			lookup_status.config(text="Character saved.", fg=self.theme["success"])

		def remove_character():
			item = saved_choices.get(saved_var.get())
			if item:
				characters.remove(item)
				self.persist_settings()
				refresh_list()
				lookup_status.config(text="Removed from saved characters.", fg=self.theme["muted"])

		def select_character(label):
			item = saved_choices.get(label)
			if not item:
				return
			saved_var.set(label)
			state["generation"] += 1
			state["busy"] = False
			name_var.set(item["name"])
			realm_var.set(item["realm"])
			region_var.set(item["region"])
			state["result"], state["url"] = None, ""
			self.clear_children(results)
			if isinstance(item.get("snapshot"), dict):
				show_result(item["snapshot"], cached=True)
				lookup_status.config(text=f"Saved {item.get('updated', 'previously')}. Refresh for current data.")
			else:
				start_lookup()

		def copy_profile():
			result = state["result"]
			if not result:
				lookup_status.config(text="Look up or select a saved character first.", fg=self.theme["warning"])
				return
			text = "\n".join(f"{label}: {result[key]}" for label, key in (
				("Name", "name"), ("Game", "version"), ("Realm", "realm"), ("Region", "region"),
				("Level", "level"), ("Class", "class"), ("Specialization", "spec"),
				("Guild", "guild"), ("Item level", "item_level"), ("Armory", "profile_url"))
				if result.get(key) is not None)
			self.root.clipboard_clear()
			self.root.clipboard_append(text)
			lookup_status.config(text="Profile copied.", fg=self.theme["success"])

		def worker(generation, region, realm, name, version):
			try:
				result = lookup_character(region, realm, name, version=version)
				state["queue"].put((generation, "ok", result, fetch_avatar(result.get("avatar_url"))))
			except ArmoryError as error:
				state["queue"].put((generation, "error", str(error), error.url))
			except Exception as error:
				state["queue"].put((generation, "error", f"Lookup failed: {error}", None))

		def sync_version():
			state["version"] = self.version.get()
			state["generation"] += 1
			state["busy"], state["result"], state["url"] = False, None, ""
			state["photo"] = None
			refresh_section_tabs()
			show_search()
			search_summary.config(text="Search for a character or choose a saved profile.")
			self.apply_theme(self.version.get(), subtree=window)
			version_menu["menu"].config(bg=self.theme["control"], fg=self.theme["text"],
				activebackground=self.theme["control_active"], activeforeground=self.theme["bright"])
			self.clear_children(results)
			refresh_list()
			available = self.version.get() in ARMORY_VERSIONS
			lookup_button.set_disabled(not available)
			tk.Label(results, text="Find a character" if available else "Armory unavailable",
				bg=self.theme["panel"], fg=self.theme["accent"], font=self.ui_font(15, bold=True)
			).pack(anchor="w", pady=(25, 6), padx=12)
			tk.Label(results, text="Enter a name and realm above, or choose a saved character.\nEquipment, combat stats, professions and progress appear in the tabs."
				if available else f"Character data is not available for {self.version.get()}.\nUse the version menu above to browse another game.",
				bg=self.theme["panel"], fg=self.theme["muted"], font=self.ui_font(9),
				justify="left", anchor="w", wraplength=700).pack(fill="x", padx=12)
			lookup_status.config(text="Enter a character or select a saved profile." if available else
				f"Character lookups are not available for {self.version.get()}.",
				fg=self.theme["muted"] if available else self.theme["warning"])
			self.apply_theme(self.version.get(), subtree=window)

		def poll():
			if not body.winfo_exists():
				return
			source = state.get("tooltip_source")
			if source is not None and not still_hovering(source):
				hide_tooltip()
			if state["version"] != self.version.get():
				sync_version()
			while True:
				try:
					generation, kind, payload, extra = state["queue"].get_nowait()
				except queue.Empty:
					break
				if kind == "icon":
					key, data = payload
					state["icon_pending"].discard(key)
					state["icon_bytes"][key] = data
					for watcher in state["icon_watchers"].pop(key, []):
						apply_icon(key, data, watcher)
					continue
				if generation != state["generation"]:
					continue
				if kind == "category":
					category_id, data = payload
					state["category_pending"].discard(category_id)
					state["categories"][category_id] = data
					tree = state.get("achievement_tree")
					if tree and tree.winfo_exists():
						fill_category(tree, f"category:{category_id}", data)
					continue
				state["busy"] = False
				if kind == "ok":
					show_result(payload, extra)
					for item in characters:
						if identity(item) == identity(payload):
							item.update(snapshot=payload, updated=time.strftime("%Y-%m-%d %H:%M"))
							self.persist_settings()
							break
					self.set_status(f"Armory: loaded {payload['name']}")
				else:
					if extra:
						state["url"] = extra
					lookup_status.config(text=payload, fg=self.theme["error"])
					viewer_status.config(text=payload, fg=self.theme["error"])
			refresh_achievement_icons()
			state["poll"] = self.root.after(150, poll)

		def start_lookup(_event=None):
			if state["version"] != self.version.get():
				sync_version()
			if state["busy"]:
				return
			version = self.version.get()
			if version not in ARMORY_VERSIONS:
				lookup_status.config(text=f"Character lookups are not available for {version}.", fg=self.theme["warning"])
				return
			name, realm = name_var.get().strip(), realm_var.get().strip()
			region = region_var.get().strip().casefold() or "us"
			if not name or not realm or len(name) > 24 or len(realm) > 40 or region not in REGIONS:
				lookup_status.config(text="Enter a valid character, realm and region (us, eu, kr, tw).", fg=self.theme["warning"])
				return
			self.armory.update(name=name, realm=realm, region=region, version=version)
			self.persist_settings()
			state["generation"] += 1
			state["busy"] = True
			state["url"], state["result"] = "", None
			self.clear_children(results)
			lookup_status.config(text="Looking up character…", fg=self.theme["muted"])
			viewer_status.config(text="Refreshing character…", fg=self.theme["muted"])
			threading.Thread(target=worker, daemon=True,
				args=(state["generation"], region, realm, name, version)).start()

		def open_page():
			if state["url"]:
				webbrowser.open(state["url"])
			else:
				lookup_status.config(text="Look up a character first.", fg=self.theme["warning"])

		for entry in (name_entry, realm_entry, region_entry):
			entry.bind("<Return>", start_lookup)
		def cleanup(event):
			if event.widget is body:
				hide_tooltip()
				if state.get("icon_executor"):
					state["icon_executor"].shutdown(wait=False, cancel_futures=True)
				for timer in (state.get("poll"), state.get("resize")):
					if timer:
						self.root.after_cancel(timer)
		body.bind("<Destroy>", cleanup, add="+")
		sync_version()
		state["poll"] = self.root.after(150, poll)
		name_entry.focus_set()

	def play_game(self):
		version = self.version.get()
		if self.updates.is_updating(version):
			self.set_status(f"{version} is updating")
			return
		install_path = self.game_paths.get(version, "")
		if not install_path:
			self.set_status(f"Choose the {version} folder in Options")
			self.open_options()
			return
		executable = self.find_executable(version, install_path)
		if executable is None:
			self.set_status(f"Could not find the game executable for {version}")
			messagebox.showerror(
				"Game executable not found",
				f"No WoW executable was found for {version} in:\n{install_path}\n\n"
				f"Choose the correct game folder in Options.",
				parent=self.root)
			return
		try:
			# macOS clients are .app bundles, which are opened rather than executed directly.
			args = split_launch_args(self.launch_args.get(version, ""))
			if executable.suffix.casefold() == ".app":
				command = ["open", str(executable)] + (["--args", *args] if args else [])
			else:
				command = [str(executable), *args]
			subprocess.Popen(command, cwd=str(executable.parent))
		except OSError as error:
			self.set_status(f"Could not start {version}")
			messagebox.showerror("Could not launch game", str(error), parent=self.root)
			return
		self.set_status(f"Launching {version}" + (f" with {len(args)} argument(s)" if args else "") + "…")
		if version in LOADER_VERSIONS:
			self.watch_for_client(version, executable)

	def watch_for_client(self, version, loader):
		"""After a loader launch, watch for the WoW client it starts (it may live outside
		the loader's folder). The background process scan does the checking."""
		self.watches.append({
			"version": version, "loader_key": path_key(loader),
			"started": time.time(), "deadline": time.monotonic() + 90})

	def set_status(self, message):
		self.status.config(text=message)


def set_windows_app_id():
	"""Give the launcher its own taskbar identity so Windows shows its icon, not Python's."""
	if os.name != "nt":
		return
	try:
		import ctypes
		ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
	except (AttributeError, OSError):
		pass


if __name__ == "__main__": 
	set_windows_app_id() 
	app = tk.Tk() 
	LauncherUI(app) 
	app.mainloop()