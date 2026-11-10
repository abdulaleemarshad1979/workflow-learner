from __future__ import annotations

import re
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

SECRET = re.compile(
    r"password|passwd|secret|token|otp|one.?time|credit.?card|card.?number|cvv|cvc", re.IGNORECASE
)
PARAM = re.compile(r"\{\{([a-zA-Z][a-zA-Z0-9_]*)\}\}")


def clean_url(value: str) -> str:
    u = urlsplit(value)
    if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password:
        raise ValueError("Only HTTP(S) URLs without credentials are supported")
    query = urlencode([(k, v) for k, v in parse_qsl(u.query) if not SECRET.search(k)], safe="{}")
    # Fragments can contain OAuth tokens. Preserve ordinary SPA routes only.
    fragment = "" if SECRET.search(u.fragment) or "access_token" in u.fragment else u.fragment
    return urlunsplit((u.scheme, u.netloc, u.path, query, fragment))


def origin(url: str) -> str:
    u = urlsplit(clean_url(url))
    return f"{u.scheme}://{u.netloc}"


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[
        "navigate",
        "click",
        "fill",
        "select",
        "check",
        "press",
        "manual",
        "native_focus",
        "native_fill",
        "native_action",
        "native_launch",
    ]
    label: str = Field(default="", max_length=180)
    url: str = Field(default="", max_length=4096)
    selector: str = Field(default="", max_length=2048)
    value: str = Field(default="", max_length=10000)
    tab: str = Field(default="main", max_length=80)
    frame: str = Field(default="", max_length=4096)
    app: str = Field(default="", max_length=180)
    path: list[int] = Field(default_factory=list, max_length=32)
    role: str = Field(default="", max_length=100)
    confirm: bool = True

    @field_validator("url", "frame")
    @classmethod
    def urls(cls, value):
        return clean_url(value) if value else value

    def fingerprint(self):
        return (self.kind, self.url, self.selector, self.app, tuple(self.path), self.tab, self.frame)


class Recording(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    workflow_id: str | None = None
    # Empty means all normal web origins, except the control dashboard and login pages.
    origins: list[str] = Field(default_factory=list, max_length=100)
    native: bool = False

    @field_validator("origins")
    @classmethod
    def normalize(cls, values):
        return [origin(v) for v in values]


class Event(BaseModel):
    session_id: str
    event_id: str = Field(min_length=1, max_length=100)
    step: Step


class WorkflowEdit(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    steps: list[Step] = Field(min_length=1, max_length=1000)


class RunRequest(BaseModel):
    mode: Literal["guided", "automatic", "bypass"] = "bypass"
    parameters: dict[str, str] = Field(default_factory=dict)
    remember: bool = True


class Autopilot(BaseModel):
    """Hands-free replay settings for one reviewed workflow."""

    enabled: bool = False
    # Either repeat every N minutes, or once a day at local HH:MM. Neither = on demand only.
    every_minutes: int | None = Field(default=None, ge=5, le=10080)
    daily_at: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class Result(BaseModel):
    command_id: str
    ok: bool
    error: str = Field(default="", max_length=500)


def parameters(steps: list[dict]) -> list[str]:
    return sorted({p for s in steps for field in ("value", "url") for p in PARAM.findall(s.get(field, ""))})


def substitute(step: dict, values: dict[str, str]) -> dict:
    result = dict(step)
    for field in ("value", "url"):
        result[field] = PARAM.sub(lambda m: values[m[1]], result.get(field, ""))
    return Step.model_validate(result).model_dump()
