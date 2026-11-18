from __future__ import annotations

from ..config import Config
from .base import StateStore, StorageError  # noqa: F401


def build_store(cfg: Config) -> StateStore:
    if cfg.storage.backend == "local":
        from .local import LocalStore
        return LocalStore(cfg.storage.local_dir)
    if cfg.storage.backend == "github_release":
        from .github_release import GitHubReleaseStore
        return GitHubReleaseStore(cfg.secrets.github_repository, cfg.secrets.github_token, cfg.storage.release_tag)
    raise ValueError(f"okänd storage.backend: {cfg.storage.backend}")
