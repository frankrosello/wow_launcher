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


class Overlay(tk.Frame):
	"""A panel drawn on top of the launcher window instead of a separate window.
	The launcher behind it is blurred and darkened while the panel is open."""

	def __init__(self, root, width, height):
		root.update_idletasks()
		width = min(width, max(200, root.winfo_width() - 20))
		height = min(height, max(200, root.winfo_height() - 20))
		self.scrim = self.make_scrim(root)
		super().__init__(root, bg=OVERLAY_BG)
		self.place(relx=.5, rely=.5, anchor="center", width=width, height=height)
		self.lift()
		self.grab_set()   # block clicks on the launcher underneath, like a modal window
		self.focus_set()
		self.bind("<Escape>", lambda _event: self.destroy())
		self.bind("<Destroy>", self.remove_scrim, add="+")

	@staticmethod
	def make_scrim(root):
		"""Full-window layer showing a blurred, darkened snapshot of the launcher."""
		scrim = tk.Label(root, bd=0, highlightthickness=0, bg="#0b0b0d")
		try:
			from PIL import Image, ImageFilter, ImageGrab, ImageTk
			root.update()
			left, top = root.winfo_rootx(), root.winfo_rooty()
			size = (root.winfo_width(), root.winfo_height())
			shot = ImageGrab.grab(bbox=(left, top, left + size[0], top + size[1]))
			shot = shot.convert("RGB").resize(size)
			shot = shot.filter(ImageFilter.GaussianBlur(5))
			shot = Image.blend(shot, Image.new("RGB", size, (8, 8, 10)), .55)
			scrim._photo = ImageTk.PhotoImage(shot, master=scrim)
			scrim.configure(image=scrim._photo)
		except Exception:
			pass  # no screen capture available: the plain dark layer is used instead
		scrim.place(x=0, y=0, relwidth=1, relheight=1)
		scrim.lift()
		return scrim

	def remove_scrim(self, event):
		if event.widget is self:
			try:
				self.scrim.destroy()
			except tk.TclError:
				pass

	def resize(self, height):
		self.place_configure(height=min(height, self.master.winfo_height() - 20))

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
	return {key: best.get(key) for key in ("id", "displayName", "fileName", "downloadUrl")}


def file_download_url(file):
	if file.get("downloadUrl"):
		return file["downloadUrl"]
	# Some authors hide the direct link from the API; the CDN path is predictable.
	file_id = int(file["id"])
	return (f"https://edge.forgecdn.net/files/{file_id // 1000}/{file_id % 1000}/"
			f"{file['fileName']}")


class AddonManager:
	COLUMNS = (("title", "Addon", 200), ("folder", "Folder", 150),
			   ("version", "Version", 80), ("interface", "Interface", 80),
			   ("state", "Status", 70), ("latest", "Latest", 90),
			   ("update", "Update", 80))

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
		self.parent_of = {}   # folder (casefold) -> main addon folder (casefold)
		self.children = {}    # main addon folder (casefold) -> [part rows]
		self.matches = self.load_matches()
		self.installed = self.load_installed()
		self.build(version)
		self.refresh()

	# ---------- UI ----------
	def build(self, version):
		app = self.app
		window = self.window = Overlay(app.root, 940, 660)

		body = tk.Frame(window, bg="#211c18", highlightbackground="#806b42",
						highlightthickness=2, padx=22, pady=18)
		body.pack(fill="both", expand=True, padx=14, pady=14)
		tk.Label(body, text="ADDON MANAGER", bg="#211c18", fg="#e3c36e",
				 font=app.ui_font(16, bold=True)).pack(anchor="w")
		tk.Label(body, text=f"{version}  \u00b7  {self.addons_dir}", bg="#211c18",
				 fg="#c7baa0", font=app.ui_font(8, italic=True)
				 ).pack(anchor="w", pady=(4, 10))

		search_row = tk.Frame(body, bg="#211c18")
		search_row.pack(fill="x", pady=(0, 8))
		tk.Label(search_row, text="FILTER", bg="#211c18", fg="#a89d88",
				 font=app.ui_font(8, bold=True)).pack(side="left", padx=(0, 8))
		self.filter_var = tk.StringVar()
		self.filter_var.trace_add("write", lambda *_a: self.refresh())
		tk.Entry(search_row, textvariable=self.filter_var, bg="#171512", fg="#e8dfcb",
				 insertbackground="#e8dfcb", relief="sunken", bd=1,
				 font=app.ui_font(9)).pack(side="left", fill="x", expand=True, ipady=4)
		self.count_label = tk.Label(search_row, text="", bg="#211c18", fg="#a89d88",
									font=app.ui_font(8, bold=True))
		self.count_label.pack(side="right", padx=(10, 0))
		for text, command in (("UPDATE", self.update_addons),
							  ("CHECK UPDATES", self.check_updates)):
			self.button_class(
				search_row, text=text, command=command, font=app.ui_font(8, bold=True),
				padx=8, pady=2, bg="#211c18", theme_provider=lambda: app.theme
			).pack(side="right", padx=(6, 0))

		footer = tk.Frame(body, bg="#211c18")
		footer.pack(fill="x", side="bottom", pady=(12, 0))
		self.status = tk.Label(body, text="Select addons, then enable, disable or remove them.",
							   bg="#211c18", fg="#a89d88", font=app.ui_font(8), anchor="w")
		self.status.pack(fill="x", side="bottom", pady=(8, 0))

		table = tk.Frame(body, bg="#171512", highlightbackground="#51432f",
						 highlightthickness=1)
		table.pack(fill="both", expand=True)
		theme = app.theme
		style = ttk.Style(window)
		style.theme_use("clam")
		style.configure("Addon.Treeview", background=theme["surface"],
						fieldbackground=theme["surface"], foreground=theme["text"],
						bordercolor=theme["control"], rowheight=24, borderwidth=0,
						font=app.ui_font(9))
		style.map("Addon.Treeview",
				  background=[("selected", theme["control_active"])],
				  foreground=[("selected", theme["bright"])])
		style.configure("Addon.Treeview.Heading", background=theme["control"],
						foreground=theme["accent"], relief="flat",
						font=app.ui_font(8, bold=True))
		style.map("Addon.Treeview.Heading",
				  background=[("active", theme["control_active"])])
		style.configure("Addon.Vertical.TScrollbar", background=theme["control"],
						troughcolor=theme["panel_alt"], bordercolor=theme["control"],
						arrowcolor=theme["accent"])
		self.tree = ttk.Treeview(
			table, style="Addon.Treeview", selectmode="extended",
			show="tree headings",
			columns=[key for key, _t, _w in self.COLUMNS])
		self.tree.heading("#0", text="")
		self.tree.column("#0", width=44, minwidth=44, stretch=False)
		for key, heading, width in self.COLUMNS:
			self.tree.heading(key, text=heading.upper())
			self.tree.column(key, width=width, anchor="w", stretch=(key == "title"))
		scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview,
								  style="Addon.Vertical.TScrollbar")
		self.tree.configure(yscrollcommand=scrollbar.set)
		scrollbar.pack(side="right", fill="y")
		self.tree.pack(side="left", fill="both", expand=True)
		self.tree.tag_configure("off", foreground=theme["disabled"])
		self.tree.tag_configure("update", foreground=theme["warning"])
		self.tree.bind("<<TreeviewSelect>>", self.show_notes)

		def button(text, command, side, padx=(0, 6), size=8):
			self.button_class(
				footer, text=text, command=command, font=app.ui_font(size, bold=True),
				padx=8, pady=4, bg="#211c18", theme_provider=lambda: app.theme
			).pack(side=side, padx=padx)

		button("INSTALL ZIP", self.install_from_file, "left")
		button("FROM URL", self.install_from_url, "left")
		button("CLOSE", self.close, "right", padx=(6, 0))
		button("OPEN FOLDER", self.open_folder, "right")
		button("REMOVE", self.remove_selected, "right")
		button("ENABLE / DISABLE", self.toggle_selected, "right")

		window.bind("<Destroy>", self.on_destroy, add="+")
		app.add_gradient(body, "panel")
		app.apply_theme(app.version.get(), subtree=window)

	def on_destroy(self, event):
		if event.widget is self.window:
			self.closed = True

	def close(self):
		self.closed = True
		self.window.destroy()

	def set_status(self, text, role="muted"):
		if self.closed:
			return
		self.status.config(text=text, fg=self.app.theme.get(role, "#a89d88"))

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
		roots.sort(key=lambda row: row["title"].casefold())
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

	def show_notes(self, _event=None):
		selected = self.tree.selection()
		if len(selected) != 1:
			return
		state, _sep, folder = selected[0].partition(":")
		for row in self.rows:
			if row["state"] == state and row["folder"] == folder:
				details = " \u00b7 ".join(part for part in (row["author"], row["notes"]) if part)
				latest, update = self.evaluate(row)
				if row["curse_id"]:
					details += (f"  [project {row['curse_id']} \u00b7 installed "
								f"'{self.group_versions.get(row['curse_id']) or row['version'] or '?'}'"
								f" \u00b7 latest '{latest or '?'}' \u00b7 {update or 'not checked'}]")
				self.set_status(details or row["title"])
				return

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
		self.busy = True
		self.next_update()
		self.window.after(150, self.poll_tasks)

	def next_update(self):
		mod, file = self.pending.popleft()
		name = file.get("displayName") or file.get("fileName") or mod
		self.set_status(f"Updating {name}\u2026 ({len(self.pending)} more)")
		threading.Thread(target=self.download, args=(
			file_download_url(file), {"mod": mod, "file": file, "name": name}),
			daemon=True).start()

	# ---------- actions ----------
	def toggle_selected(self):
		chosen = self.selected(same_state=True)
		if not chosen:
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
		message = f"{moved} addon(s) toggled."
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
			message = f"Updated {self.updated} addon project(s)."
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