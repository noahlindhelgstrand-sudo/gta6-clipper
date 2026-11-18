"""Lokal lagring – för Docker/VM och tester."""
from __future__ import annotations

import shutil
from pathlib import Path

from .base import StateStore, StorageError


class LocalStore(StateStore):
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "videos").mkdir(parents=True, exist_ok=True)

    def pull_db(self, dest: Path) -> bool:
        src = self.root / "state.db"
        if not src.exists():
            return False
        if src.resolve() != Path(dest).resolve():
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        return True

    def push_db(self, src: Path) -> None:
        dst = self.root / "state.db"
        if Path(src).resolve() != dst.resolve():
            tmp = dst.with_suffix(".tmp")
            shutil.copy2(src, tmp)
            tmp.replace(dst)

    def put_video(self, src: Path, name: str) -> None:
        shutil.copy2(src, self.root / "videos" / name)

    def get_video(self, name: str, dest: Path) -> Path:
        src = self.root / "videos" / name
        if not src.exists():
            raise StorageError(f"video saknas i lagringen: {name}")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return Path(dest)

    def delete_video(self, name: str) -> None:
        (self.root / "videos" / name).unlink(missing_ok=True)

    def list_videos(self) -> list[str]:
        return sorted(p.name for p in (self.root / "videos").glob("*.mp4"))
