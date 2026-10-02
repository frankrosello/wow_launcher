"""Notify-only update checking for the launcher (no Battle.net Agent involved).

* The installed build comes from the game's own `.build.info` file.
* The newest build comes from Blizzard's public version service.
* The launcher only reports that an update exists; installing it is done in Battle.net.
"""
import queue
import threading
from pathlib import Path
from urllib.request import Request, urlopen

VERSION_URLS = (               # UNVERIFIED: confirm these still serve version tables
	"https://{region}.version.battle.net/v2/products/{product}/versions",
	"http://{region}.patch.battle.net:1119/{product}/versions",
)
REGIONS = ("us", "eu", "kr", "tw", "cn")
IDLE_LABEL = "CHECK UPDATES"
OUTDATED_LABEL = "UPDATE AVAILABLE"


# --------------------------------------------------------------------- build info
def parse_bpsv(text):
	"""Parse Blizzard's pipe-separated tables ('Name!TYPE:len|...' header) into dict rows."""
	rows, header = [], None
	for line in text.splitlines():
		line = line.strip()
		if not line or line.startswith("##"):
			continue
		if header is None:
			header = [column.split("!")[0] for column in line.split("|")]
			continue
		rows.append(dict(zip(header, line.split("|"))))
	return rows


def find_build_info(folder):
	folder = Path(folder)
	for candidate in (folder, *list(folder.parents)[:3]):
		path = candidate / ".build.info"
		if path.is_file():
			return path
	return None


def read_installed(folder):
	"""{'product', 'version', 'region'} from the game's .build.info, else None."""
	path = find_build_info(folder)
	if path is None:
		return None
	try:
		rows = parse_bpsv(path.read_text(encoding="utf-8", errors="replace"))
	except OSError:
		return None
	active = [row for row in rows if row.get("Active", "1") == "1"] or rows
	if not active:
		return None
	row = active[0]
	product = row.get("Product", "").strip()
	version = row.get("Version", "").strip()
	if not product or not version:
		return None
	region = (row.get("Branch") or "us").strip().lower()
	return {"product": product, "version": version,
			"region": region if region in REGIONS else "us"}


def fetch_latest(product, region):
	"""Newest published version string for a product."""
	last_error = None
	for template in VERSION_URLS:
		try:
			request = Request(template.format(region=region, product=product),
							  headers={"User-Agent": "WoWLauncher/1.0"})
			with urlopen(request, timeout=8) as response:
				rows = parse_bpsv(response.read(200_000).decode("utf-8", "replace"))
		except Exception as error:
			last_error = error
			continue
		for row in rows:
			if row.get("Region", "").lower() == region and row.get("VersionsName"):
				return row["VersionsName"].strip()
		for row in rows:
			if row.get("VersionsName"):
				return row["VersionsName"].strip()
	raise RuntimeError(f"version lookup failed ({last_error})")


def version_tuple(text):
	try:
		return tuple(int(part) for part in text.strip().split("."))
	except ValueError:
		return None


def needs_update(installed, latest):
	a, b = version_tuple(installed), version_tuple(latest)
	if a is not None and b is not None:
		return a < b
	return installed.strip() != latest.strip()


def check_game(executable):
	"""Compare installed vs newest build. Runs on a worker thread (network)."""
	installed = read_installed(Path(executable).parent)
	if installed is None:
		return {"state": "unsupported"}
	try:
		latest = fetch_latest(installed["product"], installed["region"])
	except Exception as error:
		return {"state": "error", "message": str(error), **installed}
	state = "update" if needs_update(installed["version"], latest) else "current"
	return {"state": state, "installed": installed["version"], "latest": latest}


# -------------------------------------------------------------------- controller
class UpdateController:
	"""Checks every game on launch and on demand, and reports what is out of date."""

	def __init__(self, ui, excluded=()):
		self.ui = ui
		self.root = ui.root
		self.excluded = set(excluded)   # games with their own loader: never checked
		self.states = {}                # version -> {"phase": ..., "result": ...}
		self.messages = queue.Queue()
		self.ui.update_button.set_label(IDLE_LABEL)
		self.root.after(250, self.poll)
		self.root.after(1500, self.check_all_on_launch)

	# kept so the rest of the launcher doesn't need to know updates are notify-only
	def is_updating(self, _version):
		return False

	def shutdown(self):
		pass

	def selected(self):
		return self.ui.version.get()

	def can_check(self, version):
		if version in self.excluded or version in self.ui.dynamic_versions:
			return False
		return self.ui.find_executable(version, self.ui.game_paths.get(version, "")) is not None

	def outdated(self):
		return [version for version, state in self.states.items()
				if (state.get("result") or {}).get("state") == "update"]

	def refresh_view(self):
		"""Button reads UPDATE AVAILABLE while the selected game is out of date."""
		self.ui.update_button.set_label(
			OUTDATED_LABEL if self.selected() in self.outdated() else IDLE_LABEL)

	def check_all_on_launch(self):
		for version in self.ui.game_versions:
			self.start_check(version, manual=False)

	def check_selected(self):
		"""CHECK UPDATES button."""
		version = self.selected()
		if not self.can_check(version):
			self.ui.set_status(f"Update checks are not available for {version}")
			return
		self.ui.set_status(f"Checking {version} for updates\u2026")
		self.start_check(version, manual=True)

	def start_check(self, version, manual):
		state = self.states.setdefault(version, {"phase": "idle", "result": None})
		if state["phase"] == "checking" or not self.can_check(version):
			return
		executable = self.ui.find_executable(version, self.ui.game_paths.get(version, ""))
		state["phase"] = "checking"
		threading.Thread(target=self.check_worker, args=(version, executable, manual),
						 daemon=True).start()

	def check_worker(self, version, executable, manual):
		try:
			result = check_game(executable)
		except Exception as error:
			result = {"state": "error", "message": str(error)}
		self.messages.put((version, result, manual))

	def poll(self):
		if not self.root.winfo_exists():
			return
		while True:
			try:
				version, result, manual = self.messages.get_nowait()
			except queue.Empty:
				break
			self.handle(version, result, manual)
		self.root.after(250, self.poll)

	def handle(self, version, result, manual):
		state = self.states.setdefault(version, {"phase": "idle", "result": None})
		state.update(phase="idle", result=result)
		self.refresh_view()
		outcome = result["state"]
		if outcome == "update":
			if manual:
				self.ui.set_status(
					f"{version}: update available ({result['installed']} \u2192 "
					f"{result['latest']}). Install it from Battle.net")
			else:
				names = ", ".join(self.outdated())
				self.ui.set_status(f"Updates available: {names}. Install from Battle.net")
		elif manual and outcome == "current":
			self.ui.set_status(f"{version} is up to date ({result['installed']})")
		elif manual and outcome == "error":
			self.ui.set_status(f"Update check failed: {result.get('message', '')}")
		elif manual:
			self.ui.set_status(f"Update checks are not available for {version}")