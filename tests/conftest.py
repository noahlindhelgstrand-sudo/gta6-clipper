import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clipper.config import Config  # noqa: E402
from clipper.db import Database  # noqa: E402
from clipper.models import ClipCandidate  # noqa: E402

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def make_clip(cid="c1", streamer="alice", views=1000, hours_ago=2.0, dur=30.0,
              platform="twitch", lang="en", now=NOW) -> ClipCandidate:
    return ClipCandidate(
        platform=platform, clip_id=cid, streamer=streamer, streamer_display=streamer.title(),
        title=f"clip {cid}", views=views, url=f"https://clips.example/{cid}",
        created_at=now - timedelta(hours=hours_ago), duration_s=dur, language=lang,
    )


@pytest.fixture
def cfg(tmp_path) -> Config:
    c = Config()
    c.work_dir = str(tmp_path / "work")
    c.storage.backend = "local"
    c.storage.local_dir = str(tmp_path / "data")
    return c


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(tmp_path / "state.db")
    yield d
    d.close()
