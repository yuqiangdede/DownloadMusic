import html
import re
import requests
from pathlib import Path
from typing import Optional

MB_SEARCH_API = "https://musicbrainz.org/ws/2/release/"
CAA_API = "https://coverartarchive.org/release/"
DOUBAN_SEARCH_URL = "https://search.douban.com/music/subject_search"


MB_HEADERS = {
    "User-Agent": "DownloadMusic/1.0 (contact: local)",
    "Accept": "application/json",
}
DOUBAN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Referer": "https://music.douban.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _download(url: Optional[str], out_jpg: Path) -> bool:
    if not url:
        return False
    try:
        img = requests.get(url, headers=DOUBAN_HEADERS, timeout=15).content
        out_jpg.write_bytes(img)
        return out_jpg.exists() and out_jpg.stat().st_size > 0
    except Exception:
        return False


def _download_caa_image(release_id: str, out_jpg: Path, verbose: bool) -> bool:
    try:
        r = requests.get(
            f"{CAA_API}{release_id}",
            headers=MB_HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            if verbose:
                print(f"[COVER] CAA 查询失败: {release_id} status={r.status_code}")
            return False
        j = r.json()
        images = j.get("images", []) or []
        if not images:
            if verbose:
                print(f"[COVER] CAA 无图片: {release_id}")
            return False
        front = None
        for img in images:
            if img.get("front"):
                front = img
                break
        if not front:
            front = images[0]
        url = None
        for key in ("image",):
            if front.get(key):
                url = front.get(key)
                break
        if not url:
            if verbose:
                print(f"[COVER] CAA 图片缺少 URL: {release_id}")
            return False
        img = requests.get(url, headers=MB_HEADERS, timeout=15).content
        out_jpg.write_bytes(img)
        ok = out_jpg.exists() and out_jpg.stat().st_size > 0
        if verbose:
            print(f"[COVER] CAA 下载 {'成功' if ok else '失败'}: {release_id}")
        return ok
    except Exception:
        if verbose:
            print(f"[COVER] CAA 下载异常: {release_id}")
        return False


def _norm_artist_text(text: str) -> str:
    t = (text or "").casefold()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", t)


def _release_artist_matches(release: dict, artist: str) -> bool:
    expected = _norm_artist_text(artist)
    if not expected:
        return True
    credits = release.get("artist-credit", []) or []
    names = []
    for item in credits:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name and isinstance(item.get("artist"), dict):
            name = (item.get("artist", {}).get("name") or "").strip()
        if name:
            names.append(name)
    for name in names:
        got = _norm_artist_text(name)
        if not got:
            continue
        if got == expected or got in expected or expected in got:
            return True
    return False


def _search_mb_release_id(
    query: str,
    verbose: bool,
    expected_artist: Optional[str] = None,
) -> Optional[str]:
    try:
        r = requests.get(
            MB_SEARCH_API,
            params={"query": query, "fmt": "json", "limit": 5},
            headers=MB_HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            if verbose:
                print(f"[COVER] MB 查询失败: {query} status={r.status_code}")
            return None
        j = r.json()
        releases = j.get("releases", []) or []
        if not releases:
            if verbose:
                print(f"[COVER] MB 无结果: {query}")
            return None
        if expected_artist:
            for rel in releases:
                if not isinstance(rel, dict):
                    continue
                if _release_artist_matches(rel, expected_artist):
                    return rel.get("id")
            if verbose:
                print(
                    f"[COVER] MB 命中但艺人不匹配，已拒绝回退：{query} artist={expected_artist}"
                )
            return None
        return releases[0].get("id")
    except Exception:
        if verbose:
            print(f"[COVER] MB 查询异常: {query}")
        return None


def _try_fetch_cover_musicbrainz(
    artist: str, album: str, out_jpg: Path, verbose: bool
) -> bool:
    if not album:
        return False
    if artist:
        q = f'release:"{album}" AND artist:"{artist}"'
        if verbose:
            print(f"[COVER] MB 查询: {q}")
        release_id = _search_mb_release_id(q, verbose, expected_artist=artist)
        if release_id and _download_caa_image(release_id, out_jpg, verbose):
            return True
        if verbose:
            print(f"[COVER] 严格模式：艺人查询失败后不做专辑名回退：{artist} - {album}")
        return False
    q = f'release:"{album}"'
    if verbose:
        print(f"[COVER] MB 回退查询: {q}")
    release_id = _search_mb_release_id(q, verbose)
    if release_id:
        return _download_caa_image(release_id, out_jpg, verbose)
    return False


def _extract_douban_cover_url(text: str) -> Optional[str]:
    patterns = [
        r'"cover_url"\s*:\s*"([^"]+)"',
        r'"pic"\s*:\s*"([^"]+)"',
        r'"img"\s*:\s*"([^"]+)"',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if not m:
            continue
        url = m.group(1)
        url = url.replace("\\u002F", "/").replace("\\/", "/")
        url = html.unescape(url)
        return url
    return None


def _try_fetch_cover_douban(query: str, out_jpg: Path, verbose: bool) -> bool:
    try:
        r = requests.get(
            DOUBAN_SEARCH_URL,
            params={"search_text": query, "cat": "1003"},
            headers=DOUBAN_HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            if verbose:
                print(f"[COVER] 豆瓣查询失败: {query} status={r.status_code}")
            return False
        url = _extract_douban_cover_url(r.text)
        if not url:
            if verbose:
                print(f"[COVER] 豆瓣无封面结果: {query}")
            return False
        if verbose:
            print(f"[COVER] 豆瓣命中: {query}")
        return _download(url, out_jpg)
    except Exception:
        if verbose:
            print(f"[COVER] 豆瓣查询异常: {query}")
        return False


def fetch_album_cover(artist: str, album: str, out_jpg: Path, verbose: bool = False) -> bool:
    if not album:
        return False

    if _try_fetch_cover_musicbrainz(artist, album, out_jpg, verbose):
        return True

    if artist:
        if verbose:
            print(f"[COVER] 严格模式：禁用无艺人回退抓图，放弃：{artist} - {album}")
        return False

    # 回退：只用专辑名
    if verbose:
        print(f"[COVER] 豆瓣回退查询: {album}")
    return _try_fetch_cover_douban(album, out_jpg, verbose)


if __name__ == "__main__":
    artist = "许景淳"

    album = "天顶的月娘啊"
    out = Path("Cover.jpg")

    ok = fetch_album_cover(artist, album, out)
    print("ok =", ok, "out =", out.resolve())
