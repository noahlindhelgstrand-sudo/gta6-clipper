import json
from unittest.mock import MagicMock

import pytest
from googleapiclient.errors import HttpError

from clipper.config import PublishCfg
from clipper.models import VideoMetadata
from clipper.publishers.base import AuthExpiredError, PublishError, QuotaExceededError, clip_ref
from clipper.publishers.youtube import YouTubePublisher

META = VideoMetadata("Insane GTA 6 moment #Shorts", "desc\ncredit", ["#GTA6", "#Gaming", "#GTAVI"], "hook")


def http_error(status, reason=""):
    resp = MagicMock(status=status, reason="x")
    body = json.dumps({"error": {"errors": [{"reason": reason}], "code": status}}).encode()
    return HttpError(resp, body)


def make(next_chunk_effects, privacy="private", **cfg):
    yt = MagicMock()
    req = MagicMock()
    req.next_chunk.side_effect = next_chunk_effects
    yt.videos.return_value.insert.return_value = req
    pub = YouTubePublisher(yt, PublishCfg(privacy_status=privacy, **cfg), media_factory=lambda p: "MEDIA",
                           sleep=lambda s: None)
    return pub, yt, req


def test_upload_success_sends_correct_body(tmp_path):
    pub, yt, _ = make([(None, None), (None, {"id": "vid123", "status": {"privacyStatus": "private"}})])
    res = pub.upload(tmp_path / "v.mp4", META, "twitch:abc")
    assert res.external_id == "vid123" and res.url.endswith("/vid123") and not res.locked_private
    kwargs = yt.videos.return_value.insert.call_args.kwargs
    body = kwargs["body"]
    assert kwargs["part"] == "snippet,status" and kwargs["media_body"] == "MEDIA"
    assert body["snippet"]["categoryId"] == "20"
    assert body["status"]["privacyStatus"] == "private"
    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert clip_ref("twitch:abc") in body["snippet"]["tags"]
    assert "#Shorts" in body["snippet"]["title"]


def test_retries_with_backoff_on_5xx(tmp_path):
    pub, _, req = make([http_error(503), http_error(500),
                        (None, {"id": "v", "status": {"privacyStatus": "private"}})])
    assert pub.upload(tmp_path / "v.mp4", META, "k").external_id == "v"
    assert req.next_chunk.call_count == 3


def test_gives_up_after_max_retries(tmp_path):
    pub, _, _ = make([http_error(503)] * 10, max_retries=2)
    with pytest.raises(PublishError, match="gav upp"):
        pub.upload(tmp_path / "v.mp4", META, "k")


def test_quota_exceeded_is_not_retried(tmp_path):
    pub, _, req = make([http_error(403, "quotaExceeded")])
    with pytest.raises(QuotaExceededError):
        pub.upload(tmp_path / "v.mp4", META, "k")
    assert req.next_chunk.call_count == 1


def test_auth_error_raises_auth_expired(tmp_path):
    pub, _, _ = make([http_error(401, "authError")])
    with pytest.raises(AuthExpiredError):
        pub.upload(tmp_path / "v.mp4", META, "k")


def test_locked_private_detected(tmp_path):
    pub, _, _ = make([(None, {"id": "v", "status": {"privacyStatus": "private"}})], privacy="public")
    assert pub.upload(tmp_path / "v.mp4", META, "k").locked_private is True


def test_find_existing_by_clipref_tag():
    yt = MagicMock()
    yt.channels.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}
    yt.playlistItems.return_value.list.return_value.execute.return_value = {
        "items": [{"contentDetails": {"videoId": "a"}}, {"contentDetails": {"videoId": "b"}}]}
    yt.videos.return_value.list.return_value.execute.return_value = {"items": [
        {"id": "a", "snippet": {"tags": ["x"]}, "status": {"privacyStatus": "private"}},
        {"id": "b", "snippet": {"tags": [clip_ref("twitch:abc")]}, "status": {"privacyStatus": "private"}}]}
    pub = YouTubePublisher(yt, PublishCfg(), media_factory=lambda p: None)
    assert pub.find_existing("twitch:abc").external_id == "b"
    assert pub.find_existing("twitch:zzz") is None
