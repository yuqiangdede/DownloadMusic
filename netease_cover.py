import requests
from pathlib import Path
from typing import Optional

SEARCH_API = "https://music.163.com/api/search/get/web"


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Referer": "https://music.163.com",
    "Accept": "application/json,text/plain,*/*",
    "X-Real-IP": "211.161.244.70",  # 任意国内 IP，常用于绕过海外限制
}


def _download(url: Optional[str], out_jpg: Path) -> bool:
    if not url:
        return False
    try:
        img = requests.get(url, headers=HEADERS, timeout=15).content
        out_jpg.write_bytes(img)
        return out_jpg.exists() and out_jpg.stat().st_size > 0
    except Exception:
        return False


def fetch_album_cover(artist: str, album: str, out_jpg: Path) -> bool:
    if not artist or not album:
        return False

    q = f"{artist} {album}"

    # 1) 专辑搜索 type=10
    try:
        r = requests.post(
            SEARCH_API,
            data={"s": q, "type": 10, "limit": 1, "offset": 0},
            headers=HEADERS,
            timeout=15,
        )
        if r.status_code == 200:
            j = r.json()
            albums = j.get("result", {}).get("albums", []) or []
            if albums:
                return _download(albums[0].get("picUrl"), out_jpg)
    except Exception:
        pass

    # 2) 回退：单曲搜索 type=1 -> album.picUrl
    try:
        r = requests.post(
            SEARCH_API,
            data={"s": q, "type": 1, "limit": 1, "offset": 0},
            headers=HEADERS,
            timeout=15,
        )
        if r.status_code == 200:
            j = r.json()
            songs = j.get("result", {}).get("songs", []) or []
            if songs:
                pic = (songs[0].get("album") or {}).get("picUrl")
                return _download(pic, out_jpg)
    except Exception:
        pass

    return False


if __name__ == "__main__":
    artist = "许景淳"

    album = "天顶的月娘啊"
    out = Path("Cover.jpg")

    ok = fetch_album_cover(artist, album, out)
    print("ok =", ok, "out =", out.resolve())
