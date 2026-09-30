"""Addon manager window for the WoW launcher."""
import os
from pathlib import Path
import queue
import re
import shutil
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from urllib.request import Request, urlopen
import subprocess
import sys
import zipfile

MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
TOC_SUFFIX = re.compile(
	r"[_-](mainline|classic|vanilla|tbc|bcc|wrath|wotlk|cata|mists|standard)$", re.I)
GITHUB_REPO = re.compile(r"^https?://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", re.I)
DOWNLOAD_HEADERS = {"User-Agent": "Mozilla/5.0 WoW-Launcher-AddonManager"}


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


class AddonManager:
	COLUMNS = (("title", "Addon", 260), ("folder", "Folder", 190),
			   ("version", "Version", 100), ("interface", "Interface", 110),
			   ("state", "Status", 80))

	def __init__(self, app, addons_dir, version, button_class):
		self.app = app
		self.addons_dir = Path(addons_dir)
		self.disabled_dir = self.addons_dir.parent / "AddOns_Disabled"
		self.button_class = button_class
		self.downloads = queue.Queue()
		self.downloading = False
		self.closed = False
		self.build(version)
		self.refresh()

	# ---------- UI ----------
	def build(self, version):
		app = self.app
		window = self.window = tk.Toplevel(app.root)
		window.title("Addon Manager")
		window.geometry("940x620")
		window.minsize(800, 480)
		window.configure(bg="#100f12")
		window.transient(app.root)
		window.grab_set()
		if os.name == "nt":
			app.set_windows_icon(window)

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
			table, style="Addon.Treeview", selectmode="extended", show="headings",
			columns=[key for key, _t, _w in self.COLUMNS])
		for key, heading, width in self.COLUMNS:
			self.tree.heading(key, text=heading.upper())
			self.tree.column(key, width=width, anchor="w", stretch=(key == "title"))
		scrollbar = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview,
								  style="Addon.Vertical.TScrollbar")
		self.tree.configure(yscrollcommand=scrollbar.set)
		scrollbar.pack(side="right", fill="y")
		self.tree.pack(side="left", fill="both", expand=True)
		self.tree.tag_configure("off", foreground=theme["disabled"])
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

		window.protocol("WM_DELETE_WINDOW", self.close)
		app.add_gradient(body, "panel")
		app.apply_theme(app.version.get(), subtree=window)

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
			rows.append({
				"folder": path.name, "state": state,
				"title": clean_text(info.get("title")) or path.name,
				"version": clean_text(info.get("version")),
				"interface": clean_text(info.get("interface")),
				"notes": clean_text(info.get("notes")),
				"author": clean_text(info.get("author"))})
		return rows

	def refresh(self):
		if self.closed:
			return
		self.rows = self.scan(self.addons_dir, "on") + self.scan(self.disabled_dir, "off")
		needle = self.filter_var.get().strip().casefold()
		shown = [row for row in self.rows
				 if not needle or needle in row["title"].casefold()
				 or needle in row["folder"].casefold()]
		shown.sort(key=lambda row: row["title"].casefold())
		self.tree.delete(*self.tree.get_children())
		for row in shown:
			self.tree.insert(
				"", "end", iid=f"{row['state']}:{row['folder']}",
				values=(row["title"], row["folder"], row["version"],
						row["interface"].split(",")[-1].strip(),
						"Enabled" if row["state"] == "on" else "Disabled"),
				tags=(() if row["state"] == "on" else ("off",)))
		disabled = sum(1 for row in self.rows if row["state"] == "off")
		self.count_label.config(text=f"{len(self.rows)} ADDONS  \u00b7  {disabled} DISABLED")

	def show_notes(self, _event=None):
		selected = self.tree.selection()
		if len(selected) != 1:
			return
		state, _sep, folder = selected[0].partition(":")
		for row in self.rows:
			if row["state"] == state and row["folder"] == folder:
				details = " \u00b7 ".join(part for part in (row["author"], row["notes"]) if part)
				self.set_status(details or row["title"])
				return

	def selected(self):
		return [tuple(iid.split(":", 1)) for iid in self.tree.selection()]

	# ---------- actions ----------
	def toggle_selected(self):
		chosen = self.selected()
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
		if self.downloading:
			self.set_status("A download is already running.", "warning")
			return
		self.downloading = True
		self.set_status("Downloading\u2026")
		threading.Thread(target=self.download, args=(url,), daemon=True).start()
		self.window.after(150, self.poll_download)

	def download(self, url):
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
			self.downloads.put((path, None))
		except Exception as error:
			self.downloads.put((None, str(error)))

	def poll_download(self):
		if self.closed:
			return
		try:
			path, error = self.downloads.get_nowait()
		except queue.Empty:
			self.window.after(150, self.poll_download)
			return
		self.downloading = False
		if error:
			self.set_status(f"Download failed: {error}", "error")
			return
		try:
			self.install_zip(path)
		finally:
			try:
				os.remove(path)
			except OSError:
				pass

	def install_zip(self, zip_path):
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
					return
				existing = [name for name in addons
							if (self.addons_dir / name).exists()
							or (self.disabled_dir / name).exists()]
				if existing and not messagebox.askyesno(
						"Replace addons",
						"These addons are already installed and will be replaced:\n\n"
						+ ", ".join(existing[:8]) + "\n\nContinue?", parent=self.window):
					return
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
			return
		self.refresh()
		self.set_status(f"Installed {len(addons)} addon(s): " + ", ".join(list(addons)[:5]),
						"success")