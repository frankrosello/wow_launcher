"""Keyless character lookups through the launcher's Cloudflare Worker.

Blizzard credentials belong only in Worker secrets. Retail can fall back to
Raider.IO when the shared service is unavailable.
"""
import json
import re
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

REGIONS = ("us", "eu", "kr", "tw")
USER_AGENT = "WoWLauncher/1.0"
TIMEOUT = 25
# URL of your deployed Cloudflare Worker (see armory-proxy/README.md), no trailing slash.
# When set, lookups work without the user entering any API keys.
PROXY_URL = "https://armoryproxy.frankierose212005.workers.dev"
MAX_AVATAR_BYTES = 2_000_000
ARMORY_LOCALES = {"us": "en-us", "eu": "en-gb", "kr": "ko-kr", "tw": "zh-tw"}

# Versions accepted by the Worker's /character endpoint.
ARMORY_VERSIONS = ("Retail", "Classic Era", "Mists of Pandaria Classic", "TBC Anniversary")
EXPERIMENTAL_VERSIONS = ("TBC Anniversary",)


class ArmoryError(Exception):
	"""A lookup problem whose message is fine to show to the user."""

	def __init__(self, message, status=None, url=None):
		super().__init__(message)
		self.status = status
		self.url = url   # a page worth opening instead, if there is one


def realm_slug(realm):
	"""'Area 52' -> 'area-52', "Quel'Thalas" -> 'quelthalas'."""
	text = unicodedata.normalize("NFKD", realm.strip().casefold())
	text = "".join(char for char in text if not unicodedata.combining(char))
	text = re.sub(r"['\u2019`]", "", text)
	text = re.sub(r"[\s_]+", "-", text)
	return re.sub(r"[^a-z0-9-]", "", text)


def armory_url(region, realm, name):
	return (f"https://worldofwarcraft.blizzard.com/{ARMORY_LOCALES.get(region, 'en-us')}"
			f"/character/{region}/{realm_slug(realm)}/{quote(name.strip().casefold())}")


def classic_armory_url(region):
	"""Blizzard's own Classic armory (character search); pick the game version there."""
	return f"https://worldofwarcraft.blizzard.com/{ARMORY_LOCALES.get(region, 'en-us')}/classic/{region}/armory"


def _get_json(url, headers=None, data=None):
	request = Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})})
	try:
		with urlopen(request, timeout=TIMEOUT) as response:
			return json.loads(response.read().decode("utf-8"))
	except HTTPError as error:
		message = f"The server answered with HTTP {error.code}."
		try:
			payload = json.loads(error.read(65536).decode("utf-8"))
			if isinstance(payload, dict) and isinstance(payload.get("error"), str):
				message = payload["error"][:500]
		except (ValueError, OSError):
			pass
		raise ArmoryError(message, error.code) from error
	except (URLError, TimeoutError, OSError) as error:
		raise ArmoryError("Could not reach the server. Check your internet connection.") from error
	except ValueError as error:
		raise ArmoryError("The server sent a response that could not be read.") from error


def _format_timestamp(milliseconds):
	try:
		return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(milliseconds) / 1000))
	except (TypeError, ValueError, OverflowError, OSError):
		return None


def _blizzard_result(profile, media, region, realm, name, version):
	"""Turn raw Blizzard profile (+ optional media) JSON into the armory result dict."""
	classic = version != "Retail"
	avatar_url = None
	try:
		assets = {asset.get("key"): asset.get("value") for asset in media.get("assets", [])}
		avatar_url = assets.get("avatar") or assets.get("inset") or next(iter(assets.values()), None)
	except AttributeError:
		pass   # the portrait is optional
	def name_of(key):
		value = profile.get(key)
		return value.get("name") if isinstance(value, dict) else None
	realm_name = name_of("realm") or realm.strip()
	return {
		"source": "Blizzard",
		"version": version,
		"name": profile.get("name") or name.strip(),
		"realm": realm_name, "region": region,
		"level": profile.get("level"),
		"race": name_of("race"), "class": name_of("character_class"),
		"spec": name_of("active_spec"), "faction": name_of("faction"),
		"guild": name_of("guild"),
		"item_level": profile.get("equipped_item_level") if profile.get("equipped_item_level") is not None else profile.get("average_item_level"),
		"achievement_points": profile.get("achievement_points"),
		"last_login": _format_timestamp(profile.get("last_login_timestamp")),
		"extras": [(label, value) for label, value in (
			("Gender", name_of("gender")),
			("Character title", name_of("active_title")),
			("Covenant", _name((profile.get("covenant_progress") or {}).get("chosen_covenant"))),
			("Renown", (profile.get("covenant_progress") or {}).get("renown_level")),
			("Average item level", profile.get("average_item_level")),
		) if value not in (None, "")],
		"profile_url": (classic_armory_url(region) if classic else
						armory_url(region, realm_name, profile.get("name") or name)),
		"avatar_url": avatar_url,
	}


def _name(value):
	return value.get("name", "") if isinstance(value, dict) else ""


def _value(value):
	if isinstance(value, dict):
		for key in ("effective", "value", "rating"):
			if value.get(key) is not None:
				return value[key]
		return None
	return value


def _character_details(data):
	"""Normalize optional Worker details; tolerate missing Classic endpoints."""
	details = {key: [] for key in ("equipment", "stats", "professions", "progress", "guild", "achievements")}
	messages = {}
	if data.get("schema") not in (2, 3):
		for key in details:
			messages[key] = "Additional details require the updated Armory service. Your basic profile is still available."
	equipment = data.get("equipment") or {}
	for item in equipment.get("equipped_items", []):
		if not isinstance(item, dict):
			continue
		parts = [item.get("name") or _name(item.get("item")) or "Unknown item"]
		level = _value(item.get("level"))
		if level is not None:
			parts.append(f"Item level {level}")
		quality = _name(item.get("quality"))
		if quality:
			parts.append(quality)
		for enchantment in item.get("enchantments", []):
			if isinstance(enchantment, dict) and enchantment.get("display_string"):
				parts.append(enchantment["display_string"])
		for socket in item.get("sockets", []):
			if isinstance(socket, dict):
				gem = socket.get("display_string") or _name(socket.get("item"))
				if gem:
					parts.append(f"Gem: {gem}")
		details["equipment"].append((_name(item.get("slot")) or "Equipment", " • ".join(parts)))
	stats = data.get("statistics") or {}
	for key, caption in (("health", "Health"), ("power", "Resource"), ("strength", "Strength"),
		("agility", "Agility"), ("intellect", "Intellect"), ("stamina", "Stamina"),
		("spirit", "Spirit"), ("armor", "Armor"), ("attack_power", "Attack power"),
		("spell_power", "Spell power"), ("speed", "Speed")):
		value = _value(stats.get(key))
		if value is not None:
			details["stats"].append((caption, f"{value:,}" if isinstance(value, (int, float)) else str(value)))
	for key, caption in (("melee_crit", "Melee critical strike"), ("spell_crit", "Spell critical strike"),
		("ranged_crit", "Ranged critical strike"), ("melee_haste", "Melee haste"),
		("spell_haste", "Spell haste"), ("mastery", "Mastery"), ("versatility", "Versatility"),
		("dodge", "Dodge"), ("parry", "Parry"), ("block", "Block"), ("lifesteal", "Leech")):
		value = stats.get(key)
		if isinstance(value, dict) and value.get("value") is not None:
			parts = [f"{value['value']:.2f}%"] if isinstance(value["value"], (int, float)) else [str(value["value"])]
			if value.get("rating") is not None:
				parts.append(f"{value['rating']} rating")
			details["stats"].append((caption, " • ".join(parts)))
	# Versatility uses scalar damage bonuses in some profile API responses.
	if stats.get("versatility_damage_done_bonus") is not None:
		details["stats"].append(("Versatility damage bonus", f"{stats['versatility_damage_done_bonus']}%"))
	professions = data.get("professions") or {}
	for group in ("primaries", "secondaries"):
		for profession in professions.get(group, []):
			if not isinstance(profession, dict):
				continue
			name = _name(profession.get("profession")) or "Profession"
			tiers = profession.get("tiers") or [profession]
			for tier in tiers:
				label = _name(tier.get("tier"))
				rank, maximum = tier.get("skill_points"), tier.get("max_skill_points")
				progress = f"{rank} / {maximum}" if rank is not None and maximum is not None else "Rank unavailable"
				details["professions"].append((name, f"{label} • {progress}" if label else progress))
	raids = data.get("raids") or {}
	for expansion in raids.get("expansions", []):
		for instance in expansion.get("instances", []):
			for mode in instance.get("modes", []):
				progress = mode.get("progress") or {}
				completed, total = progress.get("completed_count"), progress.get("total_count")
				if completed is None or total is None:
					continue
				name = _name(instance.get("instance")) or "Raid"
				difficulty = _name(mode.get("difficulty")) or "Difficulty unavailable"
				details["progress"].append((name, f"{difficulty}: {completed} / {total} bosses"))
	pvp = data.get("pvp") or {}
	for key, label in (("honor_level", "Honor level"), ("honorable_kills", "Honorable kills")):
		if pvp.get(key) is not None:
			details["progress"].append((label, pvp[key]))
	for bracket in pvp.get("brackets", []):
		if not isinstance(bracket, dict):
			continue
		name = _name(bracket.get("bracket")) or "PvP bracket"
		if bracket.get("rating") is not None:
			details["progress"].append((name, f"{bracket['rating']} rating"))
	profile = data.get("profile") or {}
	guild = data.get("guild") or {}
	basic_guild = profile.get("guild") or {}
	if guild or basic_guild:
		for label, value in (("Guild name", guild.get("name") or basic_guild.get("name")),
			("Realm", _name(guild.get("realm")) or _name(basic_guild.get("realm"))),
			("Faction", _name(guild.get("faction"))),
			("Created", _format_timestamp(guild.get("created_timestamp"))),
			("Guild achievement points", (data.get("guild_achievements") or {}).get("total_points", guild.get("achievement_points")))):
			if value not in (None, ""):
				details["guild"].append((label, value))
		roster = (data.get("guild_roster") or {}).get("members")
		if isinstance(roster, list):
			details["guild"].append(("Members in published roster", len(roster)))
			members = sorted((member for member in roster if isinstance(member, dict)),
				key=lambda member: (member.get("rank", 99), (member.get("character") or {}).get("name", "").casefold()))
			for member in members:
				character = member.get("character") or {}
				parts = []
				if character.get("level") is not None:
					parts.append(f"Level {character['level']}")
				parts.extend(value for value in (_name(character.get("playable_race")),
					_name(character.get("playable_class"))) if value)
				if member.get("rank") is not None:
					parts.append("Guild leader" if member["rank"] == 0 else f"Rank {member['rank']}")
				details["guild"].append((character.get("name") or "Guild member", " • ".join(parts) or "Details unavailable"))
	else:
		messages["guild"] = "This character has no guild listed in the published profile."
	achievements = data.get("achievements")
	if isinstance(achievements, dict):
		completed = [item for item in achievements.get("achievements", [])
			if isinstance(item, dict) and item.get("completed_timestamp") is not None]
		completed.sort(key=lambda item: item.get("completed_timestamp", 0), reverse=True)
		points = achievements.get("total_points", profile.get("achievement_points"))
		if points is not None:
			details["achievements"].append(("Achievement points", points))
		details["achievements"].append(("Completed achievements", achievements.get("total_quantity", len(completed))))
		for item in completed:
			achievement = item.get("achievement") or {}
			label = achievement.get("name") or f"Achievement {achievement.get('id', item.get('id', 'unknown'))}"
			value = _format_timestamp(item.get("completed_timestamp")) or "Completion date unavailable"
			if achievement.get("points") is not None:
				value += f" • {achievement['points']} points"
			details["achievements"].append((label, value))
	else:
		if profile.get("achievement_points") is not None:
			details["achievements"].append(("Achievement points", profile["achievement_points"]))
		messages["achievements"] = "Achievement history is unavailable for this character or game version."
	if data.get("schema") != 3:
		for key in ("guild", "achievements"):
			details[key].append(("Additional details", "Update the Armory Worker and refresh this character to load guild and achievement history."))
	return details, messages


def _proxy_lookup(region, realm, name, version):
	query = urlencode({"region": region, "realm": realm.strip(), "name": name.strip(),
					   "version": version})
	try:
		data = _get_json(f"{PROXY_URL.rstrip('/')}/character?{query}")
	except ArmoryError as error:
		if error.status == 404:
			message = (f"No {version} character found. Check the name, realm and region. "
					   f"Characters that have not logged in for a long time are not in "
					   f"Blizzard's data.")
			if version in EXPERIMENTAL_VERSIONS:
				message += f" {version} lookups are experimental and may not be available yet."
			raise ArmoryError(message, 404,
							  classic_armory_url(region) if version != "Retail" else None
							  ) from error
		if error.status == 429:
			raise ArmoryError("Too many lookups right now. Try again in a moment.",
							  429) from error
		raise
	profile = data.get("profile") if isinstance(data, dict) else None
	if not isinstance(profile, dict):
		raise ArmoryError("The armory service sent a response that could not be read.")
	result = _blizzard_result(profile, data.get("media"), region, realm, name, version)
	result["details"], result["detail_messages"] = _character_details(data)
	return result


def _raiderio_lookup(region, realm, name):
	query = urlencode({
		"region": region, "realm": realm.strip(), "name": name.strip(),
		"fields": "gear,guild,raid_progression,mythic_plus_scores_by_season:current"})
	try:
		data = _get_json(f"https://raider.io/api/v1/characters/profile?{query}")
	except ArmoryError as error:
		if error.status in (400, 404):
			raise ArmoryError(
				"No Retail character found. Check the name, realm and region.") from error
		if error.status == 429:
			raise ArmoryError("Raider.IO rate limit reached. Try again in a moment.") from error
		raise
	extras = []
	seasons = data.get("mythic_plus_scores_by_season") or []
	score = (seasons[0].get("scores") or {}).get("all") if seasons else None
	if score:
		extras.append(("Mythic+ score", f"{score:g}"))
	raids = data.get("raid_progression") or {}
	summaries = [f"{value.get('summary')}" for value in raids.values()
				 if isinstance(value, dict) and value.get("summary")]
	if summaries:
		extras.append(("Raid progress", ",  ".join(summaries[-3:])))
	gear = data.get("gear") or {}
	guild = data.get("guild") or {}
	return {
		"source": "Raider.IO",
		"version": "Retail",
		"name": data.get("name") or name.strip(),
		"realm": data.get("realm") or realm.strip(), "region": data.get("region", region),
		"level": None,
		"race": data.get("race"), "class": data.get("class"),
		"spec": data.get("active_spec_name"), "faction": (data.get("faction") or "").title() or None,
		"guild": guild.get("name"),
		"item_level": gear.get("item_level_equipped"),
		"achievement_points": data.get("achievement_points"),
		"last_login": None,
		"extras": extras,
		"profile_url": armory_url(region, data.get("realm") or realm, data.get("name") or name),
		"avatar_url": data.get("thumbnail_url"),
	}


def lookup_character(region, realm, name, version="Retail"):
	"""Return a character summary without accepting or storing user credentials."""
	region = region.strip().casefold()
	if region not in REGIONS:
		raise ArmoryError("Region must be one of: " + ", ".join(REGIONS) + ".")
	if version not in ARMORY_VERSIONS:
		raise ArmoryError(f"Character lookups are not available for {version}.")
	if not name.strip() or not realm.strip() or len(name) > 24 or len(realm) > 40:
		raise ArmoryError("Enter a valid character name and realm.")
	if PROXY_URL:
		try:
			return _proxy_lookup(region, realm, name, version)
		except ArmoryError as error:
			if version != "Retail" or error.status in (400, 404, 429):
				raise
	if version != "Retail":
		raise ArmoryError("The character service is unavailable.", url=classic_armory_url(region))
	return _raiderio_lookup(region, realm, name)


def fetch_avatar(url):
	"""Download the portrait image, or return None. Never raises."""
	if not url or not url.startswith("https://"):
		return None
	try:
		request = Request(url, headers={"User-Agent": USER_AGENT})
		with urlopen(request, timeout=TIMEOUT) as response:
			data = response.read(MAX_AVATAR_BYTES + 1)
		return data if len(data) <= MAX_AVATAR_BYTES else None
	except (URLError, HTTPError, TimeoutError, OSError, ValueError):
		return None