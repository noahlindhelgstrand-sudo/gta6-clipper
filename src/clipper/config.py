"""Konfiguration: config.yaml + miljövariabler (CLIPPER__SEKTION__NYCKEL) + hemligheter."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class TwitchSourceCfg(BaseModel):
    enabled: bool = True
    game_name: str = "Grand Theft Auto VI"
    clip_languages: list[str] = Field(default_factory=lambda: ["en"])


class KickSourceCfg(BaseModel):
    enabled: bool = True
    category_slug: str = "grand-theft-auto-vi"


class SourcesCfg(BaseModel):
    twitch: TwitchSourceCfg = TwitchSourceCfg()
    kick: KickSourceCfg = KickSourceCfg()


class SelectionCfg(BaseModel):
    lookback_hours: int = 24
    min_views: int = 200
    min_duration_s: float = 15
    max_duration_s: float = 60
    max_candidates_per_run: int = 3
    max_per_streamer_per_day: int = 1
    spoken_languages: list[str] = Field(default_factory=lambda: ["en"])  # tom = alla


class FacecamRect(BaseModel):
    x: float
    y: float
    w: float
    h: float


class StreamersCfg(BaseModel):
    policy: str = "auto"
    discovery_days: int = 7
    top_n: int = 25
    allowlist: list[str] = Field(default_factory=list)
    blocklist: list[str] = Field(default_factory=list)
    facecam: dict[str, FacecamRect] = Field(default_factory=dict)

    @field_validator("policy")
    @classmethod
    def _policy(cls, v: str) -> str:
        if v not in ("auto", "allowlist"):
            raise ValueError("streamers.policy måste vara 'auto' eller 'allowlist'")
        return v

    @field_validator("allowlist", "blocklist")
    @classmethod
    def _norm(cls, v: list[str]) -> list[str]:
        out = []
        for item in v:
            item = item.strip().lower()
            if ":" not in item:
                item = f"twitch:{item}"
            out.append(item)
        return out


class RenderCfg(BaseModel):
    width: int = 1080
    height: int = 1920
    max_duration_s: float = 45
    fps: int = 30
    crf: int = 26
    preset: str = "veryfast"
    audio_bitrate: str = "128k"
    hook_intro_s: float = 0.5
    hook_overlay_s: float = 3.0
    font: str = "DejaVu Sans"
    censor_profanity: bool = True     # f*** i undertexter (bättre monetisering)


class TranscribeCfg(BaseModel):
    model: str = "base"
    compute_type: str = "int8"
    device: str = "cpu"


class MetadataCfg(BaseModel):
    model: str = "claude-opus-5-5"     # används bara om ANTHROPIC_API_KEY finns
    effort: str = "low"
    max_title_chars: int = 70
    local_ai: bool = True              # gratis lokal AI (CLIP + Qwen) när ingen Claude-nyckel finns
    local_llm: str = "Qwen/Qwen2.5-1.5B-Instruct"
    local_clip: str = "openai/clip-vit-base-patch32"
    suggestive_threshold: float = 0.55  # CLIP: andel "strippklubb/nakenhet" för att flagga


class QueueCfg(BaseModel):
    buffer_min: int = 6
    buffer_target: int = 8
    max_age_hours: int = 72
    max_suggestive_in_queue: int = 1   # högst så många suggestiva klipp i kön samtidigt


_SLOT_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ScheduleCfg(BaseModel):
    slots: list[str] = Field(default_factory=lambda: ["12:00", "17:00", "20:30"])
    grace_hours: float = 3

    @field_validator("slots")
    @classmethod
    def _slots(cls, v: list[str]) -> list[str]:
        for s in v:
            if not _SLOT_RE.match(s):
                raise ValueError(f"ogiltig slot '{s}', använd HH:MM")
            if s.startswith("02:"):
                # 02:00–02:59 finns inte/finns två gånger vid sommar-/vintertidsskifte i Europa
                raise ValueError(f"slot '{s}' ligger i DST-glappet 02:00–02:59; välj annan tid")
        if len(set(v)) != len(v):
            raise ValueError("dubbletter i schedule.slots")
        return sorted(v)


class PublishCfg(BaseModel):
    platforms: list[str] = Field(default_factory=lambda: ["youtube"])
    privacy_status: str = "private"
    category_id: str = "20"
    made_for_kids: bool = False
    max_retries: int = 5
    max_suggestive_per_day: int = 1    # högst så många suggestiva uppladdningar per dygn

    @field_validator("privacy_status")
    @classmethod
    def _privacy(cls, v: str) -> str:
        if v not in ("private", "unlisted", "public"):
            raise ValueError("privacy_status måste vara private|unlisted|public")
        return v


class StorageCfg(BaseModel):
    backend: str = "github_release"
    release_tag: str = "state"
    local_dir: str = "data"


class NotifyCfg(BaseModel):
    daily_summary: bool = True


class Secrets(BaseModel):
    """Läses från miljön – aldrig från fil i repot."""
    twitch_client_id: str | None = None
    twitch_client_secret: str | None = None
    youtube_client_id: str | None = None
    youtube_client_secret: str | None = None
    youtube_refresh_token: str | None = None
    anthropic_api_key: str | None = None
    discord_webhook_url: str | None = None
    ntfy_topic: str | None = None
    github_token: str | None = None
    github_repository: str | None = None

    @classmethod
    def from_env(cls) -> "Secrets":
        e = os.environ.get
        return cls(
            twitch_client_id=e("TWITCH_CLIENT_ID"),
            twitch_client_secret=e("TWITCH_CLIENT_SECRET"),
            youtube_client_id=e("YOUTUBE_CLIENT_ID"),
            youtube_client_secret=e("YOUTUBE_CLIENT_SECRET"),
            youtube_refresh_token=e("YOUTUBE_REFRESH_TOKEN"),
            anthropic_api_key=e("ANTHROPIC_API_KEY"),
            discord_webhook_url=e("DISCORD_WEBHOOK_URL"),
            ntfy_topic=e("NTFY_TOPIC"),
            github_token=e("GITHUB_TOKEN") or e("GH_TOKEN"),
            github_repository=e("GITHUB_REPOSITORY"),
        )


class CampaignCfg(BaseModel):
    """En betald clipping-kampanj (t.ex. Content Rewards). Klipp från kampanjens streamers
    prioriteras och får kampanjens hashtags/omnämnande i beskrivningen."""
    name: str
    streamers: list[str]                     # "twitch:lacy"
    url: str = ""                            # kampanjsidan – länkas i sammanfattningen
    hashtags: list[str] = Field(default_factory=list)
    mention: str = ""                        # rad som läggs i beskrivningen, t.ex. "@lacy"
    boost: float = 3.0                       # rankningsfaktor jämfört med andra klipp
    active: bool = True

    @field_validator("streamers")
    @classmethod
    def _norm(cls, v: list[str]) -> list[str]:
        return [s.strip().lower() if ":" in s else f"twitch:{s.strip().lower()}" for s in v]


class Config(BaseModel):
    timezone: str = "Europe/Stockholm"
    language: str = "en"
    sources: SourcesCfg = SourcesCfg()
    selection: SelectionCfg = SelectionCfg()
    streamers: StreamersCfg = StreamersCfg()
    render: RenderCfg = RenderCfg()
    transcribe: TranscribeCfg = TranscribeCfg()
    metadata: MetadataCfg = MetadataCfg()
    queue: QueueCfg = QueueCfg()
    schedule: ScheduleCfg = ScheduleCfg()
    publish: PublishCfg = PublishCfg()
    storage: StorageCfg = StorageCfg()
    notify: NotifyCfg = NotifyCfg()
    campaigns: list[CampaignCfg] = Field(default_factory=list)
    work_dir: str = "work"
    secrets: Secrets = Field(default_factory=Secrets, exclude=True)

    @field_validator("language")
    @classmethod
    def _lang(cls, v: str) -> str:
        if v not in ("en", "sv"):
            raise ValueError("language måste vara 'en' eller 'sv'")
        return v


def _coerce(value: str) -> Any:
    try:
        return yaml.safe_load(value)
    except yaml.YAMLError:
        return value


def _apply_env_overrides(data: dict, environ: dict[str, str]) -> dict:
    prefix = "CLIPPER__"
    for key, raw in environ.items():
        if not key.startswith(prefix):
            continue
        path = [p.lower() for p in key[len(prefix):].split("__") if p]
        node = data
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = _coerce(raw)
    return data


def load_config(path: str | Path = "config.yaml", environ: dict[str, str] | None = None) -> Config:
    environ = dict(os.environ) if environ is None else environ
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    data = _apply_env_overrides(data or {}, environ)
    cfg = Config.model_validate(data)
    cfg.secrets = Secrets.from_env()
    return cfg


def campaign_for(cfg: "Config", streamer_key: str) -> "CampaignCfg | None":
    return next((c for c in cfg.campaigns if c.active and streamer_key in c.streamers), None)
