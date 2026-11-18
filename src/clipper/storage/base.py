from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class StorageError(RuntimeError):
    pass


class StateStore(ABC):
    """Persistens mellan körningar: SQLite-databasen + renderade videor."""

    @abstractmethod
    def pull_db(self, dest: Path) -> bool:
        """Hämta senaste databasen till dest. False om ingen finns ännu."""

    @abstractmethod
    def push_db(self, src: Path) -> None:
        ...

    @abstractmethod
    def put_video(self, src: Path, name: str) -> None:
        ...

    @abstractmethod
    def get_video(self, name: str, dest: Path) -> Path:
        ...

    @abstractmethod
    def delete_video(self, name: str) -> None:
        ...

    @abstractmethod
    def list_videos(self) -> list[str]:
        ...
