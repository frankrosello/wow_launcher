"""Addon manager panel for the WoW launcher (drawn on top of the launcher window)."""
from collections import deque
import json
import os
from pathlib import Path
import queue
import re
import shutil
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import subprocess
import sys
import zipfile

MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
TOC_SUFFIX = re.compile(
	r"[_-](mainline|classic|vanilla|tbc|bcc|wrath|wotlk|cata|mists|standard)$", re.I)
GITHUB_REPO = re.compile(r"^https?://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", re.I)
DOWNLOAD_HEADERS = {"User-Agent": "Mozilla/5.0 WoW-Launcher-AddonManager"}
OVERLAY_BG = "#100f12"

# CurseForge is reached through a small proxy that holds the API key (see
# curseforge_proxy.js), so users never have to supply a key themselves.
CURSE_API = "https://curseforge-proxy.frankierose212005.workers.dev"
CURSE_STATE_FILE = "AddOns_curseforge.json"    # saved next to the AddOns folder
CURSE_MATCH_FILE = "AddOns_curseforge_matches_v2.json"   # folder name -> project id
# Which CurseForge game-version major numbers belong to each launcher game.
# Games not listed here accept any file.
FLAVOR_RULES = {
	"Retail": lambda major: major >= 10,
	"Classic Era": lambda major: major == 1,
	"TBC Anniversary": lambda major: major == 2,
	"Mists of Pandaria Classic": lambda major: major == 5,
}
# Soft shadow around the floating panel: (x offset, y offset, blur radius, opacity)
OVERLAY_SHADOW_LAYERS = ((0, 6, 9, 0.70), (0, 2, 3, 0.60))


def _hex_rgb(color):
	return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


def _blend(start, end, fraction):
	return "#%02x%02x%02x" % tuple(
		round(a + (b - a) * fraction) for a, b in zip(_hex_rgb(start), _hex_rgb(end)))


def _rounded_mask(width, height, radius, scale=4):
	"""Smooth rounded-rectangle mask; only the four corners are supersampled."""
	from PIL import Image, ImageDraw
	radius = max(1, min(radius, width // 2, height // 2))
	size = radius * 2 + 2
	big = Image.new("L", (size * scale, size * scale), 0)
	ImageDraw.Draw(big).rounded_rectangle(
		(0, 0, size * scale - 1, size * scale - 1), radius=radius * scale, fill=255)
	small = big.resize((size, size), Image.Resampling.LANCZOS)
	extent = radius + 1
	corner = small.crop((0, 0, extent, extent))
	mask = Image.new("L", (width, height), 255)
	mask.paste(corner, (0, 0))
	mask.paste(corner.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (width - extent, 0))
	mask.paste(corner.transpose(Image.Transpose.FLIP_TOP_BOTTOM), (0, height - extent))
	mask.paste(corner.transpose(Image.Transpose.ROTATE_180), (width - extent, height - extent))
	return mask


class Overlay(tk.Frame):
	"""A panel drawn on top of the launcher window instead of a separate window.
	The launcher behind it is blurred and darkened, and the panel is painted up front:
	rounded corners, a thin themed border and a soft shadow, like the main screen panels.
	Build your content inside `overlay.make_body()`."""

	MARGIN = 10   # room around the panel for its shadow
	RADIUS = 10   # corner radius, same as the main panels
	GAP = 6       # panel color between the rounded edge and the content frame

	def __init__(self, root, width, height, reuse_scrim=None):
		root.update_idletasks()
		width = min(width, max(200, root.winfo_width() - 20))
		height = min(height, max(200, root.winfo_height() - 20))
		# A panel opened over another panel shares its blurred backdrop instead of
		# photographing the screen again (which would blur and darken it twice).
		self.owns_scrim = reuse_scrim is None
		self.scrim = reuse_scrim if reuse_scrim is not None else self.make_scrim(root)
		self.root_size = (root.winfo_width(), root.winfo_height())
		self.panel_size = (width, height)
		self.backdrop_image = None
		self._backdrop_photo = None
		theme = getattr(getattr(root, "launcher", None), "theme", None) or {}
		self.fill = theme.get("panel", "#211c18")
		self.edge = _blend(self.fill, theme.get("border", "#806b42"), .32)
		super().__init__(root, bg=OVERLAY_BG)
		self.place(relx=.5, rely=.5, anchor="center", width=width, height=height)
		self.lift()
		self.canvas = tk.Canvas(self, bg=OVERLAY_BG, highlightthickness=0, bd=0)
		self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
		self.build_backdrop()
		self.grab_set()   # block clicks on the launcher underneath, like a modal window
		self.focus_set()
		self.bind("<Escape>", lambda _event: self.destroy())
		self.bind("<Destroy>", self.remove_scrim, add="+")

	def make_body(self):
		"""The frame to put content in. It sits just inside the rounded edge."""
		body = tk.Frame(self, bg=self.fill, bd=0, highlightthickness=0, padx=16, pady=12)
		inset = self.MARGIN + self.GAP
		body.pack(fill="both", expand=True, padx=inset, pady=inset)
		return body

	@staticmethod
	def make_scrim(root):
		"""Full-window layer showing a blurred, darkened snapshot of the launcher."""
		scrim = tk.Label(root, bd=0, highlightthickness=0, bg="#0b0b0d")
		scrim._image = None
		try:
			from PIL import Image, ImageFilter, ImageGrab, ImageTk
			root.update()
			left, top = root.winfo_rootx(), root.winfo_rooty()
			size = (root.winfo_width(), root.winfo_height())
			shot = ImageGrab.grab(bbox=(left, top, left + size[0], top + size[1]))
			shot = shot.convert("RGB").resize(size)
			shot = shot.filter(ImageFilter.GaussianBlur(5))
			shot = Image.blend(shot, Image.new("RGB", size, (8, 8, 10)), .55)
			scrim._image = shot
			scrim._photo = ImageTk.PhotoImage(shot, master=scrim)
			scrim.configure(image=scrim._photo)
		except Exception:
			pass  # no screen capture available: the plain dark layer is used instead
		scrim.place(x=0, y=0, relwidth=1, relheight=1)
		scrim.lift()
		return scrim

	def build_backdrop(self):
		"""Paint blurred launcher + rounded shadow + rounded bordered panel in one image."""
		width, height = self.panel_size
		root_width, root_height = self.root_size
		left, top = (root_width - width) // 2, (root_height - height) // 2
		try:
			from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageTk
			shot = getattr(self.scrim, "_image", None)
			if shot is not None:
				image = shot.crop((left, top, left + width, top + height)).convert("RGBA")
			else:
				image = Image.new("RGBA", (width, height), (11, 11, 13, 255))
			margin, radius = self.MARGIN, self.RADIUS
			panel_w, panel_h = width - margin * 2, height - margin * 2
			for offset_x, offset_y, blur, opacity in OVERLAY_SHADOW_LAYERS:
				mask = Image.new("L", (width, height), 0)
				ImageDraw.Draw(mask).rounded_rectangle(
					(margin + offset_x, margin + offset_y,
					 width - margin + offset_x - 1, height - margin + offset_y - 1),
					radius=radius, fill=255)
				mask = mask.filter(ImageFilter.GaussianBlur(blur)).point(
					lambda value, opacity=opacity: int(value * opacity))
				shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
				shadow.putalpha(mask)
				image.alpha_composite(shadow)
			outer = _rounded_mask(panel_w, panel_h, radius)
			inner = Image.new("L", (panel_w, panel_h), 0)
			inner.paste(_rounded_mask(panel_w - 2, panel_h - 2, radius - 1), (1, 1))
			panel = Image.new("RGBA", (panel_w, panel_h), (*_hex_rgb(self.fill), 255))
			panel.putalpha(inner)
			border = Image.new("RGBA", (panel_w, panel_h), (*_hex_rgb(self.edge), 255))
			border.putalpha(ImageChops.subtract(outer, inner))
			panel.alpha_composite(border)
			image.alpha_composite(panel, (margin, margin))
			self.backdrop_image = image
			self._backdrop_photo = ImageTk.PhotoImage(image.convert("RGB"), master=self.canvas)
			self.canvas.delete("all")
			self.canvas.create_image(0, 0, image=self._backdrop_photo, anchor="nw")
		except Exception:
			self.backdrop_image = None   # plain dark margin

	def remove_scrim(self, event):
		if event.widget is self and self.owns_scrim:
			try:
				self.scrim.destroy()
			except tk.TclError:
				pass

	def resize(self, height):
		height = min(height, self.master.winfo_height() - 20)
		self.panel_size = (self.panel_size[0], height)
		self.place_configure(height=height)
		self.build_backdrop()

	def protocol(self, name=None, handler=None):
		"""Mimics Toplevel.protocol: the handler runs on Escape instead of a plain close."""
		if name == "WM_DELETE_WINDOW" and handler is not None:
			self.bind("<Escape>", lambda _event: handler())


def clean_text(text):
	"""Strip WoW color codes and inline textures from a TOC value."""
	return re.sub(r"\|c[0-9a-fA-F]{8}|\|r|\|T.*?\|t", "", text or "").strip()


def read_toc(folder):
	"""Metadata dict (lowercase keys) from the addon's .toc, or None if it has none."""
	tocs = [folder / f"{folder.name}.toc"]
	try:
		tocs.extend(sorted(folder.glob("*.toc")))
	except OSError:
		return None
	for toc in tocs:
		if not toc.is_file():
			continue
		info = {}
		try:
			text = toc.read_text(encoding="utf-8-sig", errors="replace")
		except OSError:
			continue
		for line in text.splitlines():
			match = re.match(r"##\s*([^:]+?)\s*:\s*(.*)", line)
			if match:
				info.setdefault(match.group(1).casefold(), match.group(2).strip())
		return info
	return None


def find_addon_dirs(root):
	"""[(folder, addon_name)] for every addon inside an extracted archive."""
	found = []
	for dirpath, dirnames, filenames in os.walk(root):
		tocs = sorted(name for name in filenames if name.casefold().endswith(".toc"))
		if not tocs:
			continue
		folder = Path(dirpath)
		bases = [TOC_SUFFIX.sub("", Path(name).stem) for name in tocs]
		# Normal zips have Name/Name.toc; GitHub zips are wrapped (Name-main/Name.toc).
		name = folder.name if folder.name in bases else bases[0]
		found.append((folder, name))
		dirnames[:] = []  # an addon's subfolders belong to it
	return found


def parse_version(text):
	"""First dotted number in the text as a tuple, e.g. 'v1.2.10-beta' -> (1, 2, 10)."""
	match = re.search(r"\d+(?:\.\d+)*", text or "")
	return tuple(int(part) for part in match.group(0).split(".")) if match else None


def version_label(file):
	"""Short version text for a CurseForge file, taken from its display name."""
	name = file.get("displayName") or file.get("fileName") or ""
	match = re.search(r"v?\d+(?:\.\d+)+[\w.+-]*", name)
	label = match.group(0) if match else name
	return re.sub(r"\.zip$", "", label, flags=re.I)[:14]


def short_number(value):
	"""1234567 -> '1.2M', 45000 -> '45K'."""
	value = int(value or 0)
	if value >= 1_000_000:
		return f"{value / 1_000_000:.1f}M"
	if value >= 1_000:
		return f"{value / 1_000:.0f}K"
	return str(value)


def pick_file(files, accepts):
	"""Newest file for this game (releases preferred over betas/alphas), or None."""
	candidates = []
	for item in files:
		majors = [int(version.split(".")[0]) for version in item.get("gameVersions", [])
				  if re.fullmatch(r"\d+(\.\d+)+", version)]
		if accepts is not None and not any(accepts(major) for major in majors):
			continue
		candidates.append(item)
	if not candidates:
		return None
	releases = [item for item in candidates if item.get("releaseType") == 1] or candidates
	best = max(releases, key=lambda item: item["id"])
	return {key: best.get(key)
			for key in ("id", "displayName", "fileName", "downloadUrl", "dependencies")}


def file_download_url(file):
	if file.get("downloadUrl"):
		return file["downloadUrl"]
	# Some authors hide the direct link from the API; the CDN path is predictable.
	file_id = int(file["id"])
	return (f"https://edge.forgecdn.net/files/{file_id // 1000}/{file_id % 1000}/"
			f"{file['fileName']}")


class AddonManager:
	COLUMNS = (("title", "Addon", 200), ("folder", "Folder", 150),
			   ("version", "Version", 80), ("interface", "Interface", 95),
			   ("state", "Status", 80), ("latest", "Latest", 90),
			   ("update", "Update", 85))

	def __init__(self, app, addons_dir, version, button_class):
		self.app = app
		self.addons_dir = Path(addons_dir)
		self.disabled_dir = self.addons_dir.parent / "AddOns_Disabled"
		self.state_path = self.addons_dir.parent / CURSE_STATE_FILE
		self.match_path = self.addons_dir.parent / CURSE_MATCH_FILE
		self.accepts = FLAVOR_RULES.get(version)
		self.button_class = button_class
		self.tasks = queue.Queue()
		self.busy = False
		self.closed = False
		self.latest = {}      # curse project id -> newest compatible file (or None)
		self.pending = deque()
		self.updated = 0
		self.failed = []
		self.fresh = False    # True while installing new addons (vs. updating)
		self.sort = ("title", False)   # (column, descending) for the addon list
		self.parent_of = {}   # folder (casefold) -> main addon folder (casefold)
		self.children = {}    # main addon folder (casefold) -> [part rows]
		self.browse = None    # the "browse addons" panel, while open
		self.browse_mods = {}   # project id -> CurseForge project from the last search
		self.browse_token = 0   # identifies the newest search so stale results are dropped
		self.browse_tasks = queue.Queue()
		self.matches = self.load_matches()
		self.installed = self.load_installed()
		self.build(version)
		self.refresh()

	# ---------- UI ----------
	def build(self, version):
		app = self.app
		window = self.window = Overlay(app.root, 940, 660)
		theme = app.theme

		def button(parent, text, command, side="left", padx=(0, 6)):
			widget = self.button_class(
				parent, text=text, command=command, font=app.ui_font(8, bold=True),
				padx=8, pady=4, bg="#211c18", theme_provider=lambda: app.theme)
			widget.pack(side=side, padx=padx)
			return widget

		body = window.make_body()

		# Header: title and folder on the left, folder / close on the right.
		header = tk.Frame(body, bg="#211c18")
		header.pack(fill="x")
		header_buttons = tk.Frame(header, bg="#211c18")
		header_buttons.pack(side="right")
		button(header_buttons, "OPEN FOLDER", self.open_folder)
		button(header_buttons, "CLOSE", self.close, padx=(0, 0))
		titles = tk.Frame(header, bg="#211c18")
		titles.pack(side="left", fill="x", expand=True)
		tk.Label(titles, text="ADDON MANAGER", bg="#211c18", fg="#e3c36e",
				 font=app.ui_font(15, bold=True)).pack(anchor="w")
		tk.Label(titles, text=f"{version}  \u00b7  {self.short_path(self.addons_dir)}",
				 bg="#211c18", fg="#c7baa0", font=app.ui_font(8)
				 ).pack(anchor="w", pady=(3, 0))
		tk.Frame(body, bg="#78613c", height=1).pack(fill="x", pady=(10, 0))

		# Toolbar: filter on the left, update actions on the right.
		toolbar = tk.Frame(body, bg="#211c18")
		toolbar.pack(fill="x", pady=(10, 8))
		self.update_button = button(toolbar, "UPDATE SELECTED", self.update_addons,
									"right", (6, 0))
		button(toolbar, "CHECK UPDATES", self.check_updates, "right", (6, 0))
		tk.Label(toolbar, text="FILTER", bg="#211c18", fg="#a89d88",
				 font=app.ui_font(8, bold=True)).pack(side="left", padx=(0, 8))
		self.filter_var = tk.StringVar()
		self.filter_var.trace_add("write", lambda *_a: self.refresh())
		entry = tk.Entry(toolbar, textvariable=self.filter_var, bg="#171512", fg="#e8dfcb",
						 insertbackground="#e8dfcb", relief="flat", bd=1, highlightthickness=1,
						 highlightbackground="#51432f", highlightcolor="#806b42",
						 font=app.ui_font(9))
		entry.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))
		entry.bind("<Escape>", lambda _event: self.close())

		# Bottom: install actions on the left, actions for the selected addons on the right.
		actions = tk.Frame(body, bg="#211c18")
		actions.pack(fill="x", side="bottom", pady=(10, 0))
		button(actions, "BROWSE ADDONS", self.open_browse)
		button(actions, "INSTALL ZIP", self.install_from_file)
		button(actions, "FROM URL", self.install_from_url)
		button(actions, "REMOVE", self.remove_selected, "right", (6, 0))
		button(actions, "DISABLE", lambda: self.set_enabled(False), "right", (6, 0))
		button(actions, "ENABLE", lambda: self.set_enabled(True), "right", (6, 0))

		status_row = tk.Frame(body, bg="#211c18")
		status_row.pack(fill="x", side="bottom", pady=(8, 0))
		self.count_label = tk.Label(status_row, text="", bg="#211c18", fg="#a89d88",
									font=app.ui_font(8, bold=True))
		self.count_label.pack(side="right", padx=(10, 0))
		self.status = tk.Label(
			status_row, bg="#211c18", fg="#a89d88", font=app.ui_font(8), anchor="w",
			text="Select addons to enable, disable or remove them. Right-click for more.")
		self.status.pack(side="left", fill="x", expand=True)

		# A recessed rounded strip around the list, like the playtime box on the main screen.
		table = tk.Frame(body, bg="#181614", padx=3, pady=3)
		table.pack(fill="both", expand=True)
		style = ttk.Style(window)
		style.theme_use("clam")
		flat = theme["surface"]
		style.configure("Addon.Treeview", background=flat, fieldbackground=flat,
						foreground=theme["text"], borderwidth=0, relief="flat",
						rowheight=26, font=app.ui_font(9))
		style.layout("Addon.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # no frame
		style.map("Addon.Treeview",
				  background=[("selected", theme["control_active"])],
				  foreground=[("selected", theme["bright"])])
		style.configure("Addon.Treeview.Heading", background=theme["control"],
						foreground=theme["accent"], relief="flat", borderwidth=0,
						lightcolor=theme["control"], darkcolor=theme["control"],
						bordercolor=theme["control"], font=app.ui_font(8, bold=True))
		style.map("Addon.Treeview.Heading",
				  background=[("active", theme["control_active"])])
		# Slim, flat scrollbar: no arrow buttons, just a thumb.
		style.layout("Addon.Vertical.TScrollbar", [
			("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
				("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
		thumb = theme["control_active"]
		style.configure("Addon.Vertical.TScrollbar", background=thumb, troughcolor=flat,
						bordercolor=flat, lightcolor=thumb, darkcolor=thumb,
						relief="flat", borderwidth=0, width=10, arrowsize=10, gripcount=0)
		style.map("Addon.Vertical.TScrollbar",
				  background=[("pressed", theme["accent"]), ("active", theme["accent_dark"])],
				  lightcolor=[("pressed", theme["accent"]), ("active", theme["accent_dark"])],
				  darkcolor=[("pressed", theme["accent"]), ("active", theme["accent_dark"])])
		self.tree = ttk.Treeview(
			table, style="Addon.Treeview", selectmode="extended",
			show="tree headings",
			columns=[key for key, _t, _w in self.COLUMNS])
		self.tree.heading("#0", text="")
		self.tree.column("#0", width=44, minwidth=44, stretch=False)
		for key, heading, width in self.COLUMNS:
			self.tree.heading(key, text=heading.upper(), anchor="w",
							  command=lambda key=key: self.sort_by(key))
			self.tree.column(key, width=width, anchor="w", stretch=(key == "title"))
		self.update_headings()
		scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview,
								  style="Addon.Vertical.TScrollbar")
		self.tree.configure(yscrollcommand=scrollbar.set)
		scrollbar.pack(side="right", fill="y", padx=(3, 1), pady=2)
		self.tree.pack(side="left", fill="both", expand=True)
		self.tree.tag_configure("off", foreground=theme["disabled"])
		self.tree.tag_configure("update", foreground=theme["warning"])
		self.empty_label = tk.Label(table, text="", bg=theme["surface"], fg=theme["muted"],
									font=app.ui_font(10, italic=True), justify="center")

		self.menu = tk.Menu(window, tearoff=0, bd=0, bg=theme["control"], fg=theme["text"],
							activebackground=theme["control_active"],
							activeforeground=theme["bright"], font=app.ui_font(9))
		self.menu.add_command(label="Enable", command=lambda: self.set_enabled(True))
		self.menu.add_command(label="Disable", command=lambda: self.set_enabled(False))
		self.menu.add_separator()
		self.menu.add_command(label="Update", command=self.update_addons)
		self.menu.add_separator()
		self.menu.add_command(label="Remove\u2026", command=self.remove_selected)

		self.tree.bind("<<TreeviewSelect>>", self.on_select)
		self.tree.bind("<Button-3>", self.show_menu)
		if sys.platform == "darwin":
			self.tree.bind("<Button-2>", self.show_menu)
			self.tree.bind("<Control-Button-1>", self.show_menu)
		self.tree.bind("<Delete>", lambda _event: self.remove_selected())
		self.tree.bind("<Control-a>", self.select_all)
		self.tree.bind("<Escape>", lambda _event: self.close())

		window.bind("<Destroy>", self.on_destroy, add="+")
		app.style_popup_row(table)
		app.style_popup(window, body)
		app.apply_theme(app.version.get(), subtree=window)
		self.update_button.set_label("UPDATE ALL")
		entry.focus_set()

	def on_destroy(self, event):
		if event.widget is self.window:
			self.closed = True

	def close(self):
		self.closed = True
		self.window.destroy()

	@staticmethod
	def short_path(path, limit=64):
		text = str(path)
		return text if len(text) <= limit else "\u2026" + text[-(limit - 1):]

	@staticmethod
	def clip(text, limit=110):
		text = " ".join(str(text).split())
		return text if len(text) <= limit else text[:limit - 1].rstrip() + "\u2026"

	def set_status(self, text, role="muted"):
		if self.closed:
			return
		self.status.config(text=self.clip(text), fg=self.app.theme.get(role, "#a89d88"))

	# ---------- listing ----------
	def scan(self, folder, state):
		rows = []
		try:
			entries = sorted(path for path in folder.iterdir() if path.is_dir())
		except OSError:
			return rows
		for path in entries:
			if path.name.startswith((".", "Blizzard_")):
				continue
			info = read_toc(path)
			if info is None:
				continue
			curse_id = clean_text(info.get("x-curse-project-id"))
			if not curse_id.isdigit():
				# No ID in the .toc: fall back to a project matched by name earlier.
				curse_id = str(self.matches.get(path.name, ""))
			deps = [dep.casefold()
					for key in ("dependencies", "requireddeps", "optionaldeps")
					for dep in re.split(r"[,\s]+", info.get(key, "")) if dep]
			rows.append({
				"folder": path.name, "state": state, "deps": deps,
				"title": clean_text(info.get("title")) or path.name,
				"version": clean_text(info.get("version")),
				"interface": clean_text(info.get("interface")),
				"notes": clean_text(info.get("notes")),
				"author": clean_text(info.get("author")),
				"curse_id": curse_id if curse_id.isdigit() else ""})
		return rows

	def refresh(self):
		if self.closed:
			return
		opened = {iid for iid in self.tree.get_children() if self.tree.item(iid, "open")}
		self.rows = self.scan(self.addons_dir, "on") + self.scan(self.disabled_dir, "off")
		self.link_parts()
		self.build_groups()
		needle = self.filter_var.get().strip().casefold()

		def matches(row):
			return (not needle or needle in row["title"].casefold()
					or needle in row["folder"].casefold())

		self.tree.delete(*self.tree.get_children())
		roots = [row for row in self.rows if row["folder"].casefold() not in self.parent_of]
		roots.sort(key=lambda row: (self.sort_value(row), row["title"].casefold()),
				   reverse=self.sort[1])
		for root in roots:
			kids = sorted(self.children.get(root["folder"].casefold(), []),
						  key=lambda row: row["title"].casefold())
			if not any(matches(row) for row in [root] + kids):
				continue
			iid = self.insert_row("", root, kids, opened, bool(needle))
			for kid in kids:
				if not self.tree.exists(f"{kid['state']}:{kid['folder']}"):
					self.insert_row(iid, kid, [], opened, False)
		disabled = sum(1 for row in self.rows if row["state"] == "off")
		self.count_label.config(
			text=f"{len(roots)} ADDONS  \u00b7  {len(self.rows)} FOLDERS  \u00b7  {disabled} DISABLED")
		if self.tree.get_children():
			self.empty_label.place_forget()
		else:
			self.empty_label.config(
				text="No addons match your filter." if needle else
				"No addons installed yet.\nPress BROWSE ADDONS to find some.")
			self.empty_label.place(relx=.5, rely=.4, anchor="center")
			self.empty_label.lift()

	def insert_row(self, parent_iid, row, kids, opened, force_open):
		latest, update = self.evaluate(row)
		if any(self.evaluate(kid)[1] == "Update" for kid in kids):
			update = "Update"
		tags = []
		if row["state"] == "off":
			tags.append("off")
		if update == "Update":
			tags.append("update")
		iid = f"{row['state']}:{row['folder']}"
		title = row["title"] + (f"  (+{len(kids)} parts)" if kids else "")
		self.tree.insert(
			parent_iid, "end", iid=iid, open=(iid in opened or force_open),
			values=(title, row["folder"], row["version"],
					row["interface"].split(",")[-1].strip(),
					"Enabled" if row["state"] == "on" else "Disabled",
					latest, update),
			tags=tuple(tags))
		return iid

	def build_groups(self):
		"""Work out which folders belong under which main addon.
		Sets self.parent_of {folder: main folder} and self.children {main: [rows]}."""
		by_name = {}
		for row in self.rows:
			by_name.setdefault(row["folder"].casefold(), row)
		parent = {}
		# 1. name prefix: Bagnon_Config -> Bagnon
		for name in by_name:
			options = [other for other in by_name
					   if len(other) < len(name) and name.startswith(other)
					   and name[len(other)] in "_-"]
			if options:
				parent[name] = max(options, key=len)

		def root(name):
			seen = set()
			while name in parent and name not in seen:
				seen.add(name)
				name = parent[name]
			return name

		# 2. same CurseForge project: the shortest-named folder is the main one
		projects = {}
		for name, row in by_name.items():
			if row["curse_id"]:
				projects.setdefault(row["curse_id"], []).append(name)
		for names in projects.values():
			tops = sorted({root(name) for name in names}, key=lambda n: (len(n), n))
			for other in tops[1:]:
				parent[other] = tops[0]
		# 3. dependency used by exactly one main addon: BagBrother -> Bagnon
		dependents = {}
		for name, row in by_name.items():
			for dep in row.get("deps", []):
				if dep in by_name and dep != name:
					dependents.setdefault(dep, set()).add(name)
		for dep, users in sorted(dependents.items()):
			if dep in parent:
				continue
			owners = {root(user) for user in users}
			owners.discard(dep)
			if len(owners) == 1:
				parent[dep] = owners.pop()

		self.parent_of = {name: root(name) for name in parent}
		self.children = {}
		for row in self.rows:
			key = row["folder"].casefold()
			if key in self.parent_of:
				self.children.setdefault(self.parent_of[key], []).append(row)

	def link_parts(self):
		"""Multi-folder addons (Bagnon, Bagnon_Config, Bagnon_GuildBank...) are one
		CurseForge project: give sub-folders their parent's project id, and use one
		version per project (the main folder's) so sub-modules never disagree."""
		owners = {row["folder"]: row["curse_id"] for row in self.rows if row["curse_id"]}
		for row in self.rows:
			if row["curse_id"]:
				continue
			folder = row["folder"].casefold()
			parents = [name for name in owners
					   if len(name) < len(folder) and folder.startswith(name.casefold())
					   and folder[len(name)] in "_-"]
			if parents:
				row["curse_id"] = owners[max(parents, key=len)]
		self.group_versions = {}
		for row in sorted(self.rows, key=lambda item: len(item["folder"])):
			have = row["version"]
			if row["curse_id"] and have and "@" not in have:
				self.group_versions.setdefault(row["curse_id"], have)

	def on_select(self, _event=None):
		count = len(self.tree.selection())
		self.update_button.set_label("UPDATE SELECTED" if count else "UPDATE ALL")
		self.show_notes()

	def show_notes(self, _event=None):
		selected = self.tree.selection()
		if len(selected) > 1:
			self.set_status(f"{len(selected)} addons selected.")
			return
		if len(selected) != 1:
			return
		state, _sep, folder = selected[0].partition(":")
		for row in self.rows:
			if row["state"] == state and row["folder"] == folder:
				details = " \u00b7 ".join(
					part for part in (row["author"], self.clip(row["notes"], 60)) if part)
				latest, update = self.evaluate(row)
				if row["curse_id"]:
					details += (f"  [installed "
								f"'{self.group_versions.get(row['curse_id']) or row['version'] or '?'}'"
								f" \u00b7 latest '{latest or '?'}' \u00b7 {update or 'not checked'}]")
				self.set_status(details or row["title"])
				return

	def select_all(self, _event=None):
		self.tree.selection_set(self.tree.get_children())
		return "break"

	def show_menu(self, event):
		iid = self.tree.identify_row(event.y)
		if iid and iid not in self.tree.selection():
			self.tree.selection_set(iid)
		if not self.tree.selection():
			return
		try:
			self.menu.tk_popup(event.x_root, event.y_root)
		finally:
			self.menu.grab_release()

	def sort_value(self, row):
		key = self.sort[0]
		if key == "update":
			return self.evaluate(row)[1]
		if key == "latest":
			return self.evaluate(row)[0].casefold()
		return str(row.get(key, "")).casefold()

	def sort_by(self, key):
		descending = (not self.sort[1]) if self.sort[0] == key else False
		self.sort = (key, descending)
		self.update_headings()
		self.refresh()

	def update_headings(self):
		for key, heading, _width in self.COLUMNS:
			arrow = ""
			if key == self.sort[0]:
				arrow = "  \u25bc" if self.sort[1] else "  \u25b2"
			self.tree.heading(key, text=heading.upper() + arrow)

	def selected(self, same_state=False):
		"""(state, folder) for selected rows, including the parts of selected parents.
		same_state=True skips parts that are not in the parent's state (for toggling)."""
		result = []
		for iid in self.tree.selection():
			state, folder = iid.split(":", 1)
			if (state, folder) not in result:
				result.append((state, folder))
			for child in self.tree.get_children(iid):
				child_state, child_folder = child.split(":", 1)
				if same_state and child_state != state:
					continue
				if (child_state, child_folder) not in result:
					result.append((child_state, child_folder))
		return result

	# ---------- CurseForge ----------
	def load_installed(self):
		"""{project id: {"fileId": n}} for addons this manager updated from CurseForge."""
		try:
			data = json.loads(self.state_path.read_text(encoding="utf-8"))
			return data if isinstance(data, dict) else {}
		except (OSError, json.JSONDecodeError):
			return {}

	def save_installed(self):
		try:
			self.state_path.write_text(json.dumps(self.installed, indent=2), encoding="utf-8")
		except OSError:
			pass

	def load_matches(self):
		"""{folder name: project id} for addons identified by name on CurseForge."""
		try:
			data = json.loads(self.match_path.read_text(encoding="utf-8"))
			return data if isinstance(data, dict) else {}
		except (OSError, json.JSONDecodeError):
			return {}

	def save_matches(self):
		try:
			self.match_path.write_text(json.dumps(self.matches, indent=2), encoding="utf-8")
		except OSError:
			pass

	def evaluate(self, row):
		"""(latest version text, update status) for one addon row."""
		mod = row.get("curse_id")
		if not mod:
			return "", "\u2014"
		if mod not in self.latest:
			return "", ""
		file = self.latest[mod]
		if file is None:
			return "", "No file"
		label = version_label(file)
		installed = self.group_versions.get(mod) or row["version"]
		have = installed.lstrip("vV").casefold()
		usable = bool(have) and "@" not in have
		text = f"{file.get('displayName') or ''} {file.get('fileName') or ''}".casefold()
		if usable and re.search(r"(?<![\d.])" + re.escape(have) + r"(?!\.?\d)", text):
			return label, "Current"
		tracked = self.installed.get(mod)
		if tracked:
			if file["id"] > tracked.get("fileId", 0):
				return label, "Update"
			old, new = parse_version(installed), parse_version(label)
			if usable and old and new and old < new:
				# Tracked as the latest file, but the TOC still says an older version:
				# the author may not have bumped it. Flag it instead of hiding it.
				return label, "Mismatch"
			return label, "Current"
		if not usable:
			return label, "Unknown"
		old, new = parse_version(installed), parse_version(label)
		if old and new:
			return label, "Update" if new > old else "Current"
		return label, "Unknown"

	def curse_request(self, path, body):
		data = json.dumps(body).encode("utf-8") if body is not None else None
		request = Request(CURSE_API.strip().rstrip("/") + path, data=data, headers={
			"Accept": "application/json", "Content-Type": "application/json",
			"User-Agent": DOWNLOAD_HEADERS["User-Agent"]})
		with urlopen(request, timeout=30) as response:
			return json.loads(response.read().decode("utf-8"))

	def find_project(self, folder, title):
		"""CurseForge project id whose files install this folder, or ''."""
		for term in dict.fromkeys([title, folder]):
			if not term:
				continue
			query = urlencode({"gameId": 1, "classId": 1, "searchFilter": term,
							   "pageSize": 20})
			data = self.curse_request("/v1/mods/search?" + query, None)
			hits = []
			for mod in data.get("data", []):
				for file in mod.get("latestFiles", []):
					if any((m.get("name") or "").casefold() == folder.casefold()
						   for m in file.get("modules", [])):
						hits.append(mod)
						break
			if hits:
				names = {title.casefold(), folder.casefold()}
				best = next((m for m in hits if m.get("name", "").casefold() in names),
							hits[0])
				return str(best["id"])
		return ""

	def check_updates(self):
		if self.busy:
			self.set_status("Please wait for the current task to finish.", "warning")
			return
		if "YOUR-PROXY" in CURSE_API:
			self.set_status("Set CURSE_API at the top of addon_manager.py to your proxy URL.",
							"warning")
			return
		ids = {row["curse_id"] for row in self.rows if row["curse_id"]}
		unmatched = [(row["folder"], row["title"]) for row in self.rows
					 if not row["curse_id"]]
		if not ids and not unmatched:
			self.set_status("No addons found to check.", "warning")
			return
		self.busy = True
		self.set_status(f"Checking CurseForge ({len(unmatched)} addon(s) to identify)\u2026")
		threading.Thread(target=self.check_worker, args=(ids, unmatched),
						 daemon=True).start()
		self.window.after(150, self.poll_tasks)

	def check_worker(self, ids, unmatched):
		try:
			latest, modules, matches, project_names = {}, {}, {}, {}

			def fetch(id_list):
				id_list = sorted(set(id_list), key=int)
				for start in range(0, len(id_list), 50):
					chunk = id_list[start:start + 50]
					data = self.curse_request("/v1/mods", {
						"modIds": [int(item) for item in chunk], "filterPcOnly": True})
					for mod in data.get("data", []):
						files = mod.get("latestFiles", [])
						latest[str(mod["id"])] = pick_file(files, self.accepts)
						project_names[str(mod["id"])] = mod.get("name") or ""
						# every folder these project files install (main addon + parts)
						modules[str(mod["id"])] = {
							(part.get("name") or "").casefold()
							for item in files for part in item.get("modules", [])}
				for item in id_list:
					latest.setdefault(item, None)

			fetch(ids)
			to_search = []
			for folder, title in unmatched:
				# A shared folder (e.g. BagBrother) can be bundled in several projects'
				# zips: prefer the project actually named after it, never guess between
				# several unrelated ones (those fall through to the name search).
				holders = [mod for mod, names in modules.items()
						   if folder.casefold() in names]
				own = [mod for mod in holders if project_names.get(mod, "").casefold()
					   in (folder.casefold(), title.casefold())]
				owner = own[0] if own else (holders[0] if len(holders) == 1 else "")
				if owner:
					matches[folder] = owner
				else:
					to_search.append((folder, title))
			found = set()
			for folder, title in to_search:
				try:
					mod = self.find_project(folder, title)
				except HTTPError as error:
					if error.code in (401, 403):
						raise
					continue   # skip addons whose search failed (e.g. rate limit)
				if mod:
					matches[folder] = mod
					found.add(mod)
				time.sleep(0.1)
			fetch(found - set(latest))
			self.tasks.put(("check", latest, None, matches))
		except HTTPError as error:
			reason = "the update service refused the request" if error.code in (401, 403) else f"HTTP {error.code}"
			self.tasks.put(("check", None, reason, None))
		except Exception as error:
			self.tasks.put(("check", None, str(error), None))

	def update_addons(self):
		if self.busy:
			self.set_status("Please wait for the current task to finish.", "warning")
			return
		chosen = {folder for _state, folder in self.selected()}
		mods = []
		for row in self.rows:
			if not row["curse_id"] or row["curse_id"] in mods:
				continue
			if chosen and row["folder"] not in chosen:
				continue
			status = self.evaluate(row)[1]
			# Selected rows can be forced even when the version can't be compared.
			if status == "Update" or (chosen and status in ("Unknown", "Mismatch")):
				mods.append(row["curse_id"])
		if not mods:
			self.set_status("Nothing to update. Run CHECK UPDATES first"
							+ (" (or select addons that have an update)." if chosen else "."),
							"warning")
			return
		self.pending = deque((mod, self.latest[mod]) for mod in mods)
		self.updated, self.failed = 0, []
		self.fresh = False
		self.busy = True
		self.next_update()
		self.window.after(150, self.poll_tasks)

	def next_update(self):
		mod, file = self.pending.popleft()
		name = file.get("displayName") or file.get("fileName") or mod
		self.set_status(f"{'Installing' if self.fresh else 'Updating'} {name}\u2026 "
						f"({len(self.pending)} more)")
		threading.Thread(target=self.download, args=(
			file_download_url(file), {"mod": mod, "file": file, "name": name}),
			daemon=True).start()

	# ---------- browse / install new addons ----------
	def supports(self, mod):
		"""True if the CurseForge project has a file for the selected launcher game."""
		if self.accepts is None:
			return True
		if pick_file(mod.get("latestFiles", []), self.accepts):
			return True
		for index in mod.get("latestFilesIndexes", []):
			version = index.get("gameVersion", "")
			if re.fullmatch(r"\d+(\.\d+)+", version) and self.accepts(int(version.split(".")[0])):
				return True
		return False

	def open_browse(self):
		if self.busy:
			self.set_status("Please wait for the current task to finish.", "warning")
			return
		if "YOUR-PROXY" in CURSE_API:
			self.set_status("Set CURSE_API at the top of addon_manager.py to your proxy URL.",
							"warning")
			return
		app = self.app
		theme = app.theme
		window = self.browse = Overlay(app.root, 940, 640, reuse_scrim=self.window.scrim)
		self.window.place_forget()   # hide the manager so only one panel shows over the backdrop

		def button(parent, text, command, side="left", padx=(0, 6)):
			widget = self.button_class(
				parent, text=text, command=command, font=app.ui_font(8, bold=True),
				padx=8, pady=4, bg="#211c18", theme_provider=lambda: app.theme)
			widget.pack(side=side, padx=padx)
			return widget

		body = window.make_body()
		tk.Label(body, text="BROWSE ADDONS", bg="#211c18", fg="#e3c36e",
				 font=app.ui_font(15, bold=True)).pack(anchor="w")
		tk.Label(body, text=f"CurseForge  \u00b7  {app.version.get()}  \u00b7  "
				 "most popular first  \u00b7  double-click to install",
				 bg="#211c18", fg="#c7baa0", font=app.ui_font(8)
				 ).pack(anchor="w", pady=(3, 0))
		tk.Frame(body, bg="#78613c", height=1).pack(fill="x", pady=(10, 0))

		row = tk.Frame(body, bg="#211c18")
		row.pack(fill="x", pady=(10, 8))
		button(row, "SEARCH", lambda: self.browse_search(), "right", (6, 0))
		tk.Label(row, text="SEARCH", bg="#211c18", fg="#a89d88",
				 font=app.ui_font(8, bold=True)).pack(side="left", padx=(0, 8))
		self.browse_var = tk.StringVar()
		entry = tk.Entry(row, textvariable=self.browse_var, bg="#171512", fg="#e8dfcb",
						 insertbackground="#e8dfcb", relief="flat", bd=1, highlightthickness=1,
						 highlightbackground="#51432f", highlightcolor="#806b42",
						 font=app.ui_font(9))
		entry.pack(side="left", fill="x", expand=True, ipady=4, padx=(0, 6))
		entry.bind("<Return>", lambda _event: self.browse_search())
		entry.bind("<Escape>", lambda _event: window.destroy())

		actions = tk.Frame(body, bg="#211c18")
		actions.pack(fill="x", side="bottom", pady=(10, 0))
		button(actions, "BACK TO MANAGER", window.destroy)
		button(actions, "INSTALL SELECTED", lambda: self.browse_install(), "right", (6, 0))
		self.browse_status = tk.Label(body, text="", bg="#211c18", fg="#a89d88",
									  font=app.ui_font(8), anchor="w")
		self.browse_status.pack(fill="x", side="bottom", pady=(8, 0))

		table = tk.Frame(body, bg="#181614", padx=3, pady=3)
		table.pack(fill="both", expand=True)
		columns = (("name", "Addon", 210), ("author", "Author", 120),
				   ("downloads", "Downloads", 90), ("updated", "Updated", 90),
				   ("status", "Status", 85), ("summary", "Summary", 260))
		self.browse_tree = ttk.Treeview(
			table, style="Addon.Treeview", selectmode="extended", show="headings",
			columns=[key for key, _t, _w in columns])
		for key, heading, width in columns:
			self.browse_tree.heading(key, text=heading.upper(), anchor="w")
			self.browse_tree.column(key, width=width, anchor="w", stretch=(key == "summary"))
		scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.browse_tree.yview,
								  style="Addon.Vertical.TScrollbar")
		self.browse_tree.configure(yscrollcommand=scrollbar.set)
		scrollbar.pack(side="right", fill="y", padx=(3, 1), pady=2)
		self.browse_tree.pack(side="left", fill="both", expand=True)
		self.browse_tree.tag_configure("off", foreground=theme["disabled"])
		self.browse_empty = tk.Label(table, text="", bg=theme["surface"], fg=theme["muted"],
									 font=app.ui_font(10, italic=True), justify="center")
		self.browse_tree.bind("<<TreeviewSelect>>", self.on_browse_select)
		self.browse_tree.bind("<Double-1>", self.on_browse_double_click)
		self.browse_tree.bind("<Return>", lambda _event: self.browse_install())
		self.browse_tree.bind("<Escape>", lambda _event: window.destroy())

		window.bind("<Destroy>", self.on_browse_destroy, add="+")
		app.style_popup_row(table)
		app.style_popup(window, body)
		app.apply_theme(app.version.get(), subtree=window)
		entry.focus_set()
		self.browse_search()      # an empty search lists the most popular addons
		window.after(100, self.poll_browse)

	def on_browse_destroy(self, event):
		if event.widget is self.browse:
			self.browse = None
			if self.closed:
				return
			try:
				width, height = self.window.panel_size
				self.window.place(relx=.5, rely=.5, anchor="center",
								  width=width, height=height)
				self.window.lift()
				self.window.grab_set()   # hand the modal grab back to the manager
			except tk.TclError:
				pass

	def browse_note(self, text, role="muted"):
		if self.browse is not None:
			self.browse_status.config(text=self.clip(text, 130),
									 fg=self.app.theme.get(role, "#a89d88"))

	def on_browse_select(self, _event=None):
		selected = self.browse_tree.selection()
		if len(selected) == 1:
			mod = self.browse_mods.get(selected[0], {})
			self.browse_note(f"{mod.get('name', '')}: {mod.get('summary', '')}")
		elif selected:
			self.browse_note(f"{len(selected)} addons selected. Press INSTALL SELECTED.")

	def on_browse_double_click(self, event):
		if self.browse_tree.identify_region(event.x, event.y) == "cell":
			self.browse_install()

	def browse_search(self):
		self.browse_token += 1
		self.browse_note("Searching\u2026")
		threading.Thread(target=self.browse_worker,
						 args=(self.browse_var.get().strip(), self.browse_token),
						 daemon=True).start()

	def browse_worker(self, term, token):
		try:
			params = {"gameId": 1, "classId": 1, "sortField": 2, "sortOrder": "desc",
					  "pageSize": 40}
			if term:
				params["searchFilter"] = term
			data = self.curse_request("/v1/mods/search?" + urlencode(params), None)
			self.browse_tasks.put((token, data.get("data", []), None))
		except Exception as error:
			self.browse_tasks.put((token, None, str(error)))

	def poll_browse(self):
		window = self.browse
		if self.closed or window is None or not window.winfo_exists():
			return
		try:
			token, mods, error = self.browse_tasks.get_nowait()
		except queue.Empty:
			window.after(100, self.poll_browse)
			return
		if token == self.browse_token:
			self.show_results(mods, error)
		window.after(100, self.poll_browse)

	def show_results(self, mods, error):
		if error:
			self.browse_note(f"Search failed: {error}", "warning")
			return
		have = {row["curse_id"] for row in self.rows if row["curse_id"]}
		self.browse_mods = {str(mod["id"]): mod for mod in mods}
		self.browse_tree.delete(*self.browse_tree.get_children())
		# Projects for this game first; the rest are dimmed at the bottom.
		for mod in sorted(mods, key=lambda item: not self.supports(item)):
			mod_id = str(mod["id"])
			ok = self.supports(mod)
			status = "Installed" if mod_id in have else ("" if ok else "Other game")
			authors = ", ".join(a.get("name", "") for a in mod.get("authors", [])[:2])
			self.browse_tree.insert(
				"", "end", iid=mod_id, tags=() if ok else ("off",),
				values=(mod.get("name", ""), authors, short_number(mod.get("downloadCount")),
						(mod.get("dateModified") or "")[:10], status,
						mod.get("summary", "")))
		if mods:
			self.browse_empty.place_forget()
			self.browse_note(f"{len(mods)} result(s). Select addons and press INSTALL SELECTED.")
		else:
			self.browse_empty.config(text="No addons found.\nTry a different search.")
			self.browse_empty.place(relx=.5, rely=.4, anchor="center")
			self.browse_empty.lift()
			self.browse_note("No results.")

	def browse_install(self):
		ids = list(self.browse_tree.selection())
		if not ids:
			self.browse_note("Select one or more addons first.", "warning")
			return
		have = {row["curse_id"] for row in self.rows if row["curse_id"]}
		already = [self.browse_mods[i].get("name", i) for i in ids if i in have]
		if already and not messagebox.askyesno(
				"Reinstall addons", "Already installed and will be replaced:\n\n"
				+ ", ".join(already[:8]) + "\n\nContinue?", parent=self.browse):
			return
		self.browse.destroy()   # progress is shown on the manager's status line
		self.busy = True
		self.set_status("Finding files\u2026")
		threading.Thread(target=self.resolve_worker, args=(ids, have), daemon=True).start()
		self.window.after(150, self.poll_tasks)

	def project_files(self, mod_id):
		"""Recent files of one CurseForge project."""
		try:
			files = self.curse_request(f"/v1/mods/{mod_id}/files?pageSize=50", None).get("data", [])
			if files:
				return files
		except Exception:
			pass
		# Fall back to the endpoint the update check already uses.
		data = self.curse_request("/v1/mods", {"modIds": [int(mod_id)], "filterPcOnly": True})
		mods = data.get("data", [])
		return mods[0].get("latestFiles", []) if mods else []

	def resolve_worker(self, ids, have):
		"""Pick the right file for each chosen project, plus its required dependencies."""
		try:
			plan, seen, skipped = [], set(), []

			def visit(mod_id, depth):
				if mod_id in seen:
					return
				seen.add(mod_id)
				file = pick_file(self.project_files(mod_id), self.accepts)
				if file is None:
					if depth == 0:
						skipped.append(self.browse_mods.get(mod_id, {}).get("name", mod_id))
					return
				plan.append((mod_id, file))
				if depth < 2:
					for dep in file.get("dependencies") or []:
						dep_id = str(dep.get("modId"))
						if dep.get("relationType") == 3 and dep_id not in have:   # required
							visit(dep_id, depth + 1)

			for mod_id in ids:
				visit(mod_id, 0)
			if not plan:
				raise ValueError("no file compatible with this game version")
			self.tasks.put(("resolve", plan, None, skipped))
		except Exception as error:
			self.tasks.put(("resolve", None, str(error), None))

	# ---------- actions ----------
	def set_enabled(self, enable):
		"""Enable or disable the selected addons (and their parts)."""
		source = "off" if enable else "on"
		chosen = [(state, folder) for state, folder in self.selected(same_state=True)
				  if state == source]
		if not chosen:
			if self.tree.selection():
				self.set_status("Those addons are already "
								+ ("enabled." if enable else "disabled."), "warning")
			else:
				self.set_status("Select one or more addons first.", "warning")
			return
		moved, failed = 0, 0
		for state, folder in chosen:
			source_root, target_root = (
				(self.addons_dir, self.disabled_dir) if state == "on"
				else (self.disabled_dir, self.addons_dir))
			target = target_root / folder
			try:
				if target.exists():
					raise OSError("target exists")
				target_root.mkdir(parents=True, exist_ok=True)
				shutil.move(str(source_root / folder), str(target))
				moved += 1
			except OSError:
				failed += 1
		self.refresh()
		message = f"{moved} addon(s) {'enabled' if enable else 'disabled'}."
		if failed:
			message += f" {failed} failed (is the game running, or does a copy already exist?)"
		self.set_status(message, "warning" if failed else "success")

	def remove_selected(self):
		chosen = self.selected()
		if not chosen:
			self.set_status("Select one or more addons first.", "warning")
			return
		names = ", ".join(folder for _state, folder in chosen[:6])
		if len(chosen) > 6:
			names += f" and {len(chosen) - 6} more"
		if not messagebox.askyesno(
				"Remove addons", f"Permanently delete:\n\n{names}\n\n"
				"Saved settings in the WTF folder are kept.", parent=self.window):
			return
		removed, failed = 0, 0
		for state, folder in chosen:
			root = self.addons_dir if state == "on" else self.disabled_dir
			try:
				shutil.rmtree(root / folder)
				removed += 1
			except OSError:
				failed += 1
		self.refresh()
		message = f"{removed} addon(s) removed."
		if failed:
			message += f" {failed} could not be deleted."
		self.set_status(message, "warning" if failed else "success")

	def open_folder(self):
		try:
			self.addons_dir.mkdir(parents=True, exist_ok=True)
			if os.name == "nt":
				os.startfile(str(self.addons_dir))
			else:
				subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open",
								  str(self.addons_dir)])
		except OSError as error:
			self.set_status(f"Could not open folder: {error}", "error")

	def install_from_file(self):
		selected = filedialog.askopenfilename(
			parent=self.window, title="Select an addon .zip",
			filetypes=[("Zip archives", "*.zip"), ("All files", "*.*")])
		if selected:
			self.install_zip(selected)

	def install_from_url(self):
		url = simpledialog.askstring(
			"Install from URL",
			"Direct link to an addon .zip, or a GitHub repository link:",
			parent=self.window)
		if not url or not url.strip():
			return
		url = url.strip()
		github = GITHUB_REPO.match(url)
		if github:
			url = f"https://github.com/{github.group(1)}/{github.group(2)}/archive/HEAD.zip"
		if self.busy:
			self.set_status("Please wait for the current task to finish.", "warning")
			return
		self.busy = True
		self.set_status("Downloading\u2026")
		threading.Thread(target=self.download, args=(url, None), daemon=True).start()
		self.window.after(150, self.poll_tasks)

	def download(self, url, context):
		path = None
		try:
			handle, path = tempfile.mkstemp(suffix=".zip")
			total = 0
			with os.fdopen(handle, "wb") as output, urlopen(
					Request(url, headers=DOWNLOAD_HEADERS), timeout=30) as response:
				while True:
					chunk = response.read(65536)
					if not chunk:
						break
					total += len(chunk)
					if total > MAX_DOWNLOAD_BYTES:
						raise OSError("file is larger than 200 MB")
					output.write(chunk)
			self.tasks.put(("download", path, None, context))
		except Exception as error:
			if path:
				try:
					os.remove(path)
				except OSError:
					pass
			self.tasks.put(("download", None, str(error), context))

	def poll_tasks(self):
		if self.closed:
			return
		try:
			kind, value, error, context = self.tasks.get_nowait()
		except queue.Empty:
			self.window.after(150, self.poll_tasks)
			return
		if kind == "check":
			self.busy = False
			if error:
				self.set_status(f"Update check failed: {error}", "error")
				return
			if context:
				self.matches.update(context)
				self.save_matches()
			self.latest = value
			self.refresh()
			mods = {row["curse_id"] for row in self.rows
					if row["curse_id"] and self.evaluate(row)[1] == "Update"}
			if mods:
				self.set_status(f"{len(mods)} addon project(s) have updates. Press UPDATE.",
								"warning")
			else:
				self.set_status("Everything checked is up to date.", "success")
			return

		if kind == "resolve":
			# Files chosen for a browse-and-install: download them one after another.
			if error:
				self.busy = False
				self.set_status(f"Install failed: {error}", "error")
				return
			self.pending = deque(value)
			self.updated = 0
			self.failed = [f"{name} (no compatible file)" for name in context or []]
			self.fresh = True
			self.next_update()
			self.window.after(150, self.poll_tasks)
			return

		# A finished download: a manual URL install (context None) or one CurseForge update.
		if error:
			if context:
				self.failed.append(context["name"])
			else:
				self.busy = False
				self.set_status(f"Download failed: {error}", "error")
				return
		else:
			try:
				names = self.install_zip(value, confirm=context is None)
			finally:
				try:
					os.remove(value)
				except OSError:
					pass
			if context:
				if names:
					self.installed[context["mod"]] = {"fileId": context["file"]["id"]}
					self.save_installed()
					if self.fresh:
						# Remember the project so the addon is recognised even if its
						# .toc has no CurseForge id, and shows as up to date right away.
						self.latest[context["mod"]] = context["file"]
						for name in names:
							self.matches.setdefault(name, context["mod"])
						self.save_matches()
					self.updated += 1
				else:
					self.failed.append(context["name"])
		if context and self.pending:
			self.next_update()
			self.window.after(150, self.poll_tasks)
			return
		self.busy = False
		if context:
			self.refresh()
			verb = "Installed" if self.fresh else "Updated"
			message = f"{verb} {self.updated} addon project(s)."
			if self.failed:
				message += " Failed: " + ", ".join(self.failed[:4])
			self.set_status(message, "warning" if self.failed else "success")

	def install_zip(self, zip_path, confirm=True):
		"""Install every addon in a zip. Returns the installed names, or None on failure."""
		try:
			with tempfile.TemporaryDirectory() as temp:
				root = Path(temp).resolve()
				with zipfile.ZipFile(zip_path) as archive:
					for member in archive.namelist():
						if not (root / member).resolve().is_relative_to(root):
							raise ValueError("the archive contains unsafe paths")
					archive.extractall(temp)
				addons = {}
				for folder, name in find_addon_dirs(root):
					addons.setdefault(name, folder)
				if not addons:
					self.set_status("No addons (.toc files) found in that archive.", "warning")
					return None
				existing = [name for name in addons
							if (self.addons_dir / name).exists()
							or (self.disabled_dir / name).exists()]
				if confirm and existing and not messagebox.askyesno(
						"Replace addons",
						"These addons are already installed and will be replaced:\n\n"
						+ ", ".join(existing[:8]) + "\n\nContinue?", parent=self.window):
					return None
				self.addons_dir.mkdir(parents=True, exist_ok=True)
				for name, folder in addons.items():
					# An addon that is currently disabled stays disabled after updating.
					keep_disabled = ((self.disabled_dir / name).exists()
									 and not (self.addons_dir / name).exists())
					destination_root = self.disabled_dir if keep_disabled else self.addons_dir
					destination_root.mkdir(parents=True, exist_ok=True)
					for old_root in (self.addons_dir, self.disabled_dir):
						if (old_root / name).exists():
							shutil.rmtree(old_root / name)
					shutil.copytree(folder, destination_root / name)
		except (OSError, zipfile.BadZipFile, ValueError) as error:
			self.set_status(f"Install failed: {error}", "error")
			return None
		if confirm:
			# A manual install makes any remembered CurseForge file number stale.
			for name in addons:
				for root_dir in (self.addons_dir, self.disabled_dir):
					info = read_toc(root_dir / name) or {}
					self.installed.pop(clean_text(info.get("x-curse-project-id")), None)
				self.installed.pop(str(self.matches.get(name, "")), None)
			self.save_installed()
		self.refresh()
		self.set_status(f"Installed {len(addons)} addon(s): " + ", ".join(list(addons)[:5]),
						"success")
		return list(addons)