import html
import json
import re
import time
import unicodedata
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from pipeline_core import extract_valid_image_bytes

try:
    from PIL import Image
except Exception:
    Image = None

MB_SEARCH_API = "https://musicbrainz.org/ws/2/release/"
CAA_API = "https://coverartarchive.org/release/"
APPLE_SEARCH_API = "https://itunes.apple.com/search"
DOUBAN_SEARCH_URL = "https://search.douban.com/music/subject_search"

MB_HEADERS = {
    "User-Agent": "DownloadMusic/1.1 (contact: local)",
    "Accept": "application/json",
}
DOUBAN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "Referer": "https://music.douban.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
IMAGE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    ),
    "Referer": "https://music.163.com/",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}

_RESPONSE_CACHE: Dict[Tuple[Any, ...], Any] = {}
_FAILED_IMAGE_URLS: set[str] = set()


def _exception_text(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"


def _request(
    url: str,
    *,
    headers: Dict[str, str],
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 15,
    verbose: bool = False,
    label: str = "request",
) -> Optional[requests.Response]:
    for attempt in range(3):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=timeout)
        except requests.RequestException as error:
            if verbose:
                print(f"[COVER] {label} exception: {_exception_text(error)}")
            if attempt < 2:
                time.sleep(0.5 * (2**attempt))
                continue
            return None
        if response.status_code == 429 or 500 <= response.status_code < 600:
            if verbose:
                print(
                    f"[COVER] {label} retryable status={response.status_code} "
                    f"attempt={attempt + 1}/3"
                )
            if attempt < 2:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    delay = min(float(retry_after), 5.0)
                except (TypeError, ValueError):
                    delay = 0.5 * (2**attempt)
                time.sleep(delay)
                continue
        return response
    return None


def _cached_json(
    url: str,
    *,
    headers: Dict[str, str],
    params: Dict[str, Any],
    verbose: bool,
    label: str,
) -> Optional[Dict[str, Any]]:
    key = ("json", url, tuple(sorted(params.items())))
    if key in _RESPONSE_CACHE:
        return _RESPONSE_CACHE[key]
    response = _request(
        url, headers=headers, params=params, verbose=verbose, label=label
    )
    if response is None or response.status_code != 200:
        if verbose and response is not None:
            print(f"[COVER] {label} failed: status={response.status_code}")
        return None
    try:
        payload = response.json()
    except ValueError as error:
        if verbose:
            print(f"[COVER] {label} invalid JSON: {_exception_text(error)}")
        return None
    if not isinstance(payload, dict):
        return None
    _RESPONSE_CACHE[key] = payload
    return payload


def _cached_text(
    url: str,
    *,
    headers: Dict[str, str],
    params: Dict[str, Any],
    verbose: bool,
    label: str,
) -> Optional[str]:
    key = ("text", url, tuple(sorted(params.items())))
    if key in _RESPONSE_CACHE:
        return _RESPONSE_CACHE[key]
    response = _request(
        url, headers=headers, params=params, verbose=verbose, label=label
    )
    if response is None or response.status_code != 200:
        if verbose and response is not None:
            print(f"[COVER] {label} failed: status={response.status_code}")
        return None
    _RESPONSE_CACHE[key] = response.text
    return response.text


def _verify_image_bytes(data: bytes) -> bool:
    if not data:
        return False
    if Image is not None:
        try:
            with Image.open(BytesIO(data)) as image:
                image.verify()
            return True
        except Exception:
            pass
    return extract_valid_image_bytes(data) is not None


def _download(
    url: Optional[str],
    out_jpg: Path,
    headers: Dict[str, str],
    verbose: bool = False,
) -> bool:
    if not url or url in _FAILED_IMAGE_URLS:
        return False
    tmp_path = out_jpg.with_name(out_jpg.name + ".tmp")
    try:
        response = _request(
            url, headers=headers, verbose=verbose, label="image download"
        )
        if response is None or response.status_code != 200:
            if verbose and response is not None:
                print(f"[COVER] image download failed: status={response.status_code} url={url}")
            _FAILED_IMAGE_URLS.add(url)
            return False
        data = response.content
        parsed = extract_valid_image_bytes(data)
        if parsed:
            data, _mime = parsed
        if not _verify_image_bytes(data):
            if verbose:
                print(f"[COVER] downloaded image is invalid: {url}")
            _FAILED_IMAGE_URLS.add(url)
            return False
        tmp_path.write_bytes(data)
        if not _verify_image_bytes(tmp_path.read_bytes()):
            _FAILED_IMAGE_URLS.add(url)
            return False
        tmp_path.replace(out_jpg)
        return out_jpg.exists() and out_jpg.stat().st_size > 0
    except (OSError, requests.RequestException) as error:
        if verbose:
            print(f"[COVER] image download exception: {_exception_text(error)} url={url}")
        _FAILED_IMAGE_URLS.add(url)
        return False
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def fetch_cover_url(url: str, out_jpg: Path, verbose: bool = False) -> bool:
    if not url or not url.lower().startswith(("http://", "https://")):
        return False
    if verbose:
        print(f"[COVER] direct cover url: {url}")
    ok = _download(url, out_jpg, IMAGE_HEADERS, verbose)
    if verbose:
        result = "succeeded" if ok else "failed"
        print(f"[COVER] direct cover url {result}: {url}")
    return ok


def _normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold()
    value = value.replace("‐", "-").replace("‑", "-").replace("–", "-").replace("—", "-")
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value)


def _album_base(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "")
    value = re.sub(
        r"\s*[\(\[]\s*(?:re)?master(?:ed)?(?:\s+\d{4})?\s*[\)\]]\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return value.strip()


def _text_matches(actual: str, expected: str, allow_base: bool = False) -> bool:
    actual_norm = _normalize_text(actual)
    expected_norm = _normalize_text(expected)
    if not actual_norm or not expected_norm:
        return False
    if actual_norm == expected_norm:
        return True
    if allow_base:
        actual_base = _normalize_text(_album_base(actual))
        expected_base = _normalize_text(_album_base(expected))
        return bool(actual_base and expected_base and actual_base == expected_base)
    return False


def _artist_names_from_release(release: Dict[str, Any]) -> List[str]:
    names = []
    for item in release.get("artist-credit", []) or []:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name and isinstance(item.get("artist"), dict):
            name = (item["artist"].get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _release_matches(release: Dict[str, Any], artist: str, album: str) -> bool:
    if not _text_matches(str(release.get("title") or ""), album, allow_base=True):
        return False
    return any(_text_matches(name, artist) for name in _artist_names_from_release(release))


def _album_queries(album: str) -> List[str]:
    values = [album, _album_base(album)]
    result = []
    seen = set()
    for value in values:
        value = value.strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _download_caa_image(release_id: str, out_jpg: Path, verbose: bool) -> bool:
    payload = _cached_json(
        f"{CAA_API}{release_id}",
        headers=MB_HEADERS,
        params={},
        verbose=verbose,
        label=f"CAA query {release_id}",
    )
    if not payload:
        return False
    images = payload.get("images", []) or []
    if not images:
        if verbose:
            print(f"[COVER] CAA has no images: {release_id}")
        return False
    front = next(
        (item for item in images if isinstance(item, dict) and item.get("front")),
        images[0],
    )
    if not isinstance(front, dict):
        return False
    candidates = []
    thumbnails = front.get("thumbnails") or {}
    for key in ("1200", "500", "large", "small"):
        url = thumbnails.get(key)
        if url and url not in candidates:
            candidates.append(url)
    image_url = front.get("image")
    if image_url and image_url not in candidates:
        candidates.append(image_url)
    for url in candidates:
        if _download(url, out_jpg, MB_HEADERS, verbose):
            if verbose:
                print(f"[COVER] CAA download succeeded: {release_id}")
            return True
    return False


def _try_fetch_cover_musicbrainz(
    artist: str, album: str, out_jpg: Path, verbose: bool
) -> bool:
    if not artist or not album:
        return False
    seen_release_ids = set()
    for query_album in _album_queries(album):
        query = f'release:"{query_album}" AND artist:"{artist}"'
        if verbose:
            print(f"[COVER] MB query: {query}")
        payload = _cached_json(
            MB_SEARCH_API,
            headers=MB_HEADERS,
            params={"query": query, "fmt": "json", "limit": 20},
            verbose=verbose,
            label=f"MB query {query}",
        )
        if not payload:
            continue
        matches = [
            release
            for release in payload.get("releases", []) or []
            if isinstance(release, dict) and _release_matches(release, artist, album)
        ]
        for release in matches:
            release_id = str(release.get("id") or "")
            if not release_id or release_id in seen_release_ids:
                continue
            seen_release_ids.add(release_id)
            if _download_caa_image(release_id, out_jpg, verbose):
                return True
    if verbose:
        print(f"[COVER] MB no usable matched release: {artist} - {album}")
    return False


def _contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text or ""))


def _apple_artwork_urls(url: str) -> List[str]:
    if not url:
        return []
    high_resolution = re.sub(r"/\d+x\d+[^/]*\.(jpg|png)$", r"/1200x1200bb.\1", url)
    return list(dict.fromkeys([high_resolution, url]))


def _try_fetch_cover_apple(
    artist: str, album: str, out_jpg: Path, verbose: bool
) -> bool:
    countries = ("TW", "US") if _contains_cjk(f"{artist}{album}") else ("US", "TW")
    for query_album in _album_queries(album):
        term = f"{artist} {query_album}".strip()
        for country in countries:
            payload = _cached_json(
                APPLE_SEARCH_API,
                headers=IMAGE_HEADERS,
                params={
                    "term": term,
                    "media": "music",
                    "entity": "album",
                    "limit": 20,
                    "country": country,
                },
                verbose=verbose,
                label=f"Apple query {country} {term}",
            )
            if not payload:
                continue
            candidates = []
            for item in payload.get("results", []) or []:
                if not isinstance(item, dict):
                    continue
                album_match = _text_matches(
                    str(item.get("collectionName") or ""), album, allow_base=True
                )
                artist_match = _text_matches(str(item.get("artistName") or ""), artist)
                if album_match and artist_match:
                    candidates.append(item)
            for item in candidates:
                for url in _apple_artwork_urls(str(item.get("artworkUrl100") or "")):
                    if _download(url, out_jpg, IMAGE_HEADERS, verbose):
                        if verbose:
                            print(
                                f"[COVER] Apple hit: {item.get('artistName')} - "
                                f"{item.get('collectionName')} country={country}"
                            )
                        return True
    if verbose:
        print(f"[COVER] Apple no verified match: {artist} - {album}")
    return False


def _extract_douban_items(text: str) -> List[Dict[str, Any]]:
    match = re.search(r"window\.__DATA__\s*=\s*({.*?});", text, re.DOTALL)
    if not match:
        return []
    try:
        payload = json.loads(match.group(1))
    except ValueError:
        return []
    items = payload.get("items", []) if isinstance(payload, dict) else []
    return [item for item in items if isinstance(item, dict)]


def _extract_douban_cover_url(text: str) -> Optional[str]:
    items = _extract_douban_items(text)
    if items:
        url = str(items[0].get("cover_url") or "")
        return html.unescape(url) if url else None
    return None


def _douban_item_matches(item: Dict[str, Any], artist: str, album: str) -> bool:
    if item.get("tpl_name") != "search_subject":
        return False
    if not _text_matches(str(item.get("title") or ""), album, allow_base=True):
        return False
    abstract = str(item.get("abstract") or "")
    return _normalize_text(artist) in _normalize_text(abstract)


def _try_fetch_cover_douban(
    artist: str, album: str, out_jpg: Path, verbose: bool
) -> bool:
    query = f"{album} {artist}".strip()
    text = _cached_text(
        DOUBAN_SEARCH_URL,
        headers=DOUBAN_HEADERS,
        params={"search_text": query, "cat": "1003"},
        verbose=verbose,
        label=f"Douban query {query}",
    )
    if not text:
        return False
    for item in _extract_douban_items(text):
        if not _douban_item_matches(item, artist, album):
            continue
        url = str(item.get("cover_url") or "")
        if url and _download(html.unescape(url), out_jpg, DOUBAN_HEADERS, verbose):
            if verbose:
                print(f"[COVER] Douban hit: {artist} - {album}")
            return True
    if verbose:
        print(f"[COVER] Douban no verified album result: {artist} - {album}")
    return False


def fetch_album_cover(
    artist: str, album: str, out_jpg: Path, verbose: bool = False
) -> bool:
    artist = (artist or "").strip()
    album = (album or "").strip()
    if not artist or not album:
        if verbose:
            print(
                f"[COVER] 元数据不足，跳过在线封面："
                f"artist={artist or '<empty>'} album={album or '<empty>'}"
            )
        return False

    sources = (
        ("MusicBrainz/CAA", _try_fetch_cover_musicbrainz),
        ("Apple", _try_fetch_cover_apple),
        ("Douban", _try_fetch_cover_douban),
    )
    for source_name, fetcher in sources:
        if fetcher(artist, album, out_jpg, verbose):
            return True
        if verbose:
            print(f"[COVER] {source_name} source exhausted: {artist} - {album}")
    return False


if __name__ == "__main__":
    artist = "Artist"
    album = "Album"
    out = Path("Cover.jpg")
    ok = fetch_album_cover(artist, album, out, verbose=True)
    print("ok =", ok, "out =", out.resolve())
