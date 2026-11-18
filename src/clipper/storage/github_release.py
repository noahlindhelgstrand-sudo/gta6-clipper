"""GitHub Release-assets som gratis lagring.

En (pre-)release med taggen `state` håller:
  - state-<unix-ms>.db   – databasen. Ny version laddas upp FÖRE gamla raderas,
                            så en krasch mitt i lämnar alltid minst en giltig DB.
  - <clip>.mp4           – renderade klipp i kön (raderas när de postats)
Workflows serialiseras med `concurrency: clipper-state` så att två jobb aldrig skriver samtidigt.
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

from ..log import get_logger
from .base import StateStore, StorageError

log = get_logger("storage")
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"


class GitHubReleaseStore(StateStore):
    def __init__(self, repo: str, token: str, tag: str = "state", session: requests.Session | None = None):
        if not repo or not token:
            raise StorageError("GITHUB_REPOSITORY/GITHUB_TOKEN saknas för github_release-lagring")
        self.repo, self.tag = repo, tag
        self.http = session or requests.Session()
        self.http.headers.update({"Authorization": f"Bearer {token}",
                                  "Accept": "application/vnd.github+json",
                                  "X-GitHub-Api-Version": "2022-11-28"})
        self._release: dict | None = None

    # --- release & assets ---
    def _req(self, method: str, url: str, ok=(200, 201, 204), **kw) -> requests.Response:
        for attempt in range(4):
            try:
                r = self.http.request(method, url, timeout=kw.pop("timeout", 120), **kw)
            except requests.RequestException as e:
                if attempt == 3:
                    raise StorageError(f"GitHub {method} {url}: {e}") from e
                time.sleep(2 ** attempt)
                continue
            if r.status_code in ok:
                return r
            if r.status_code >= 500 and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise StorageError(f"GitHub {method} {url} -> {r.status_code}: {r.text[:300]}")
        raise StorageError("unreachable")

    def release(self, refresh: bool = False) -> dict:
        if self._release and not refresh:
            return self._release
        r = self.http.get(f"{API}/repos/{self.repo}/releases/tags/{self.tag}", timeout=30)
        if r.status_code == 404:
            log.info("skapar state-release", extra={"tag": self.tag})
            r = self._req("POST", f"{API}/repos/{self.repo}/releases", json={
                "tag_name": self.tag, "name": "clipper state (automatisk – radera inte)",
                "body": "Databas och renderade klipp för gta6-clipper. Hanteras automatiskt.",
                "prerelease": True})
        elif r.status_code != 200:
            raise StorageError(f"kunde inte läsa release: {r.status_code} {r.text[:200]}")
        self._release = r.json()
        return self._release

    def _assets(self) -> list[dict]:
        rel = self.release()
        out, page = [], 1
        while True:
            r = self._req("GET", f"{API}/repos/{self.repo}/releases/{rel['id']}/assets",
                          params={"per_page": 100, "page": page})
            batch = r.json()
            out += batch
            if len(batch) < 100:
                return out
            page += 1

    def _asset(self, name: str) -> dict | None:
        return next((a for a in self._assets() if a["name"] == name), None)

    def _download(self, asset: dict, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = self._req("GET", f"{API}/repos/{self.repo}/releases/assets/{asset['id']}",
                      headers={"Accept": "application/octet-stream"}, stream=True)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        return dest

    def _upload(self, src: Path, name: str) -> None:
        rel = self.release()
        with open(src, "rb") as f:
            self._req("POST", f"{UPLOADS}/repos/{self.repo}/releases/{rel['id']}/assets",
                      params={"name": name}, data=f, timeout=600,
                      headers={"Content-Type": "application/octet-stream"})

    def _delete(self, asset: dict) -> None:
        self._req("DELETE", f"{API}/repos/{self.repo}/releases/assets/{asset['id']}", ok=(204, 404))

    # --- StateStore ---
    def _db_assets(self) -> list[dict]:
        return sorted((a for a in self._assets() if a["name"].startswith("state-") and a["name"].endswith(".db")),
                      key=lambda a: a["name"])

    def pull_db(self, dest: Path) -> bool:
        dbs = self._db_assets()
        if not dbs:
            return False
        self._download(dbs[-1], Path(dest))
        log.info("databas hämtad", extra={"asset": dbs[-1]["name"]})
        return True

    def push_db(self, src: Path) -> None:
        old = self._db_assets()
        name = f"state-{int(time.time() * 1000):015d}.db"
        self._upload(Path(src), name)
        for a in old:
            self._delete(a)
        log.info("databas sparad", extra={"asset": name})

    def put_video(self, src: Path, name: str) -> None:
        existing = self._asset(name)
        if existing:
            self._delete(existing)
        self._upload(Path(src), name)

    def get_video(self, name: str, dest: Path) -> Path:
        a = self._asset(name)
        if not a:
            raise StorageError(f"video saknas i release: {name}")
        return self._download(a, Path(dest))

    def delete_video(self, name: str) -> None:
        a = self._asset(name)
        if a:
            self._delete(a)

    def list_videos(self) -> list[str]:
        return sorted(a["name"] for a in self._assets() if a["name"].endswith(".mp4"))
