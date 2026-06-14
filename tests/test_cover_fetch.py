import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

import netease_cover as cover


class FakeResponse:
    def __init__(
        self,
        status_code=200,
        *,
        payload=None,
        text="",
        content=b"",
        headers=None,
    ):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = content
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise requests.JSONDecodeError("invalid", "", 0)
        return self._payload


class CoverFetchTests(unittest.TestCase):
    def setUp(self):
        cover._RESPONSE_CACHE.clear()
        cover._FAILED_IMAGE_URLS.clear()

    @patch("netease_cover.time.sleep")
    @patch("netease_cover.requests.get")
    def test_request_retries_timeout_429_and_500(self, get, _sleep):
        get.side_effect = [
            requests.Timeout("slow"),
            FakeResponse(429),
            FakeResponse(200, payload={"ok": True}),
        ]
        response = cover._request(
            "https://example.test",
            headers={},
            label="test",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get.call_count, 3)

    @patch("netease_cover._download_caa_image")
    @patch("netease_cover._cached_json")
    def test_musicbrainz_tries_later_matching_release(self, cached_json, download):
        cached_json.return_value = {
            "releases": [
                {
                    "id": "first",
                    "title": "Hard Groove",
                    "artist-credit": [{"name": "The RH Factor"}],
                },
                {
                    "id": "second",
                    "title": "Hard Groove",
                    "artist-credit": [{"name": "The RH Factor"}],
                },
            ]
        }
        download.side_effect = [False, True]
        ok = cover._try_fetch_cover_musicbrainz(
            "The RH Factor", "Hard Groove", Path("Cover.jpg"), False
        )
        self.assertTrue(ok)
        self.assertEqual(
            [call.args[0] for call in download.call_args_list], ["first", "second"]
        )

    @patch("netease_cover._download")
    @patch("netease_cover._cached_json")
    def test_apple_matches_remastered_album(self, cached_json, download):
        cached_json.return_value = {
            "results": [
                {
                    "artistName": "The Go-Betweens",
                    "collectionName": "Tallulah (Remastered)",
                    "artworkUrl100": "https://example.test/100x100bb.jpg",
                }
            ]
        }
        download.return_value = True
        ok = cover._try_fetch_cover_apple(
            "The Go-Betweens",
            "Tallulah (Remastered)",
            Path("Cover.jpg"),
            False,
        )
        self.assertTrue(ok)
        self.assertIn("1200x1200", download.call_args.args[0])

    def test_douban_rejects_artist_photo_and_accepts_album(self):
        payload = {
            "items": [
                {
                    "tpl_name": "search_personage",
                    "title": "江蕙",
                    "abstract": "江蕙",
                    "cover_url": "https://example.test/artist.jpg",
                },
                {
                    "tpl_name": "search_subject",
                    "title": "江蕙",
                    "abstract": "江蕙 / 1990 / 专辑 / CD",
                    "cover_url": "https://example.test/album.jpg",
                },
            ]
        }
        text = f"<script>window.__DATA__ = {json.dumps(payload)};</script>"
        items = cover._extract_douban_items(text)
        self.assertFalse(cover._douban_item_matches(items[0], "江蕙", "江蕙"))
        self.assertTrue(cover._douban_item_matches(items[1], "江蕙", "江蕙"))

    @patch("netease_cover._download")
    @patch("netease_cover._cached_text")
    def test_douban_downloads_verified_album(self, cached_text, download):
        payload = {
            "items": [
                {
                    "tpl_name": "search_subject",
                    "title": "Hard Groove",
                    "abstract": "The RH Factor / 2003 / 专辑 / CD",
                    "cover_url": "https://example.test/album.jpg",
                }
            ]
        }
        cached_text.return_value = f"window.__DATA__ = {json.dumps(payload)};"
        download.return_value = True
        self.assertTrue(
            cover._try_fetch_cover_douban(
                "The RH Factor", "Hard Groove", Path("Cover.jpg"), False
            )
        )

    @patch("netease_cover._verify_image_bytes", return_value=True)
    @patch("netease_cover.extract_valid_image_bytes", return_value=None)
    @patch("netease_cover._request")
    def test_download_cleans_temp_file_on_write_failure(
        self, request, _extract, _verify
    ):
        request.return_value = FakeResponse(200, content=b"image")
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "missing" / "Cover.jpg"
            self.assertFalse(cover._download("https://example.test/a.jpg", output, {}))
            self.assertFalse(output.with_name("Cover.jpg.tmp").exists())

    def test_missing_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "Cover.jpg"
            self.assertFalse(cover.fetch_album_cover("", "Unknown Album", output))


if __name__ == "__main__":
    unittest.main()
