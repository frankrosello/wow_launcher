"""Validation at settings and network boundaries, independent of the UI."""

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator


class Preferences(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    launch_behavior: Literal["Keep open", "Minimize", "Minimize to tray"] = "Keep open"
    news_refresh_minutes: Literal[0, 15, 30, 60, 180] = 30
    animations_enabled: bool = True
    addon_backups_enabled: bool = True
    playtime_paused: bool = False
    launcher_update_checks: bool = True


def clean_preferences(value):
    """Repair individual fields without discarding other valid preferences."""
    valid = {}
    if isinstance(value, dict):
        for name, field in Preferences.model_fields.items():
            if name in value:
                try:
                    if name == "news_refresh_minutes" and type(value[name]) is not int:
                        continue
                    valid[name] = TypeAdapter(field.annotation).validate_python(value[name], strict=True)
                except ValidationError:
                    pass
    return Preferences(**valid).model_dump()


class LauncherSettings(BaseModel):
    model_config = ConfigDict(extra="allow", validate_default=True)
    game_paths: dict[str, str] = Field(default_factory=dict)
    launch_args: dict[str, str] = Field(default_factory=dict)
    preferences: dict = Field(default_factory=dict)

    @field_validator("game_paths", "launch_args", mode="before")
    @classmethod
    def valid_strings(cls, value):
        if not isinstance(value, dict):
            return {}
        return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}

    @field_validator("preferences", mode="before")
    @classmethod
    def valid_preferences(cls, value):
        return clean_preferences(value)


def validate_settings(value):
    if not isinstance(value, dict):
        raise ValueError("Launcher settings must contain a JSON object")
    return LauncherSettings.model_validate(value).model_dump()


class NewsArticle(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")
    id: str = ""
    title: str = Field(min_length=1)
    summary: str = ""
    url: str

    @field_validator("url")
    @classmethod
    def http_link(cls, value):
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("News links must be HTTP or HTTPS URLs")
        return value


def validate_news(items):
    result = []
    if isinstance(items, list):
        for item in items:
            try:
                result.append(NewsArticle.model_validate(item).model_dump())
            except ValidationError:
                continue
    return result
