#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
只处理 project/res/*，输出到 project/dist/*：

0) 用 Unlock Music CLI(um.exe) 把 res/**.ncm 转成 mp3（每目录批量）到 dist
   - 修复 Windows 下 subprocess 输出解码报错（GBK/UTF-8 混杂）
   - 尝试写入 NCM 内嵌封面（补齐 MP3 封面）
1) 在 dist 内按 artist - album 分类目录（用 ID3，避免 ffprobe 标签乱码）
2) 在 dist 内递归重命名 mp3：{track} - {titlepart}.mp3（titlepart 取原文件名最后一个 "- " 后的部分）
3) 在 dist 内每目录统一封面：选该目录下第一个含 ID3 APIC 的 mp3 作为封面源
4) 在 dist 内每个 mp3 用“目录封面图片”+自己的音频生成同名 mp4
   - 封面如果长边>720才缩到720，保持比例，不放大
   - 这里会落地一个目录级 Cover.jpg（只生成一次，避免重复抽取）
"""

import argparse
import json
import hashlib
import os
import re
import struct
import subprocess
import sys
import time
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from mutagen.id3 import ID3
    from mutagen.id3._frames import APIC
except Exception:
    APIC = None
    ID3 = None


def write_apic(mp3_path: Path, cover_jpg: Path) -> bool:
    if ID3 is None or APIC is None:
        return False
    try:
        id3 = ID3(str(mp3_path))
    except Exception:
        id3 = ID3()

    try:
        data = cover_jpg.read_bytes()
        id3.delall("APIC")
        id3.add(
            APIC(
                encoding=3,
                mime="image/jpeg",
                type=3,
                desc="Cover",
                data=data,
            )
        )
        id3.save(str(mp3_path))
        return True
    except Exception:
        return False


WIN_INVALID = r'<>:"/\|?*'
WIN_INVALID_RE = re.compile(rf"[{re.escape(WIN_INVALID)}]")


def windows_input_path(path: Path) -> str:
    """Windows 长路径前缀，用于 ffmpeg/ffprobe 处理含中文/空格的路径"""
    if os.name == "nt":
        return "\\\\?\\" + str(path.resolve())
    return str(path)


def run_cmd_bytes(
    cmd: List[str], timeout_sec: Optional[float] = None
) -> Tuple[int, bytes, bytes]:
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=False,
            shell=False,
            timeout=timeout_sec,
        )
        return p.returncode, p.stdout, p.stderr
    except OSError as e:
        return 127, b"", str(e).encode("utf-8", errors="replace")
    except subprocess.TimeoutExpired as e:
        outb = e.stdout if e.stdout else b""
        errb = e.stderr if e.stderr else b""
        return 124, outb, errb


def decode_bytes(b: bytes) -> str:
    # 容错解码：优先 utf-8，再 gbk，最后替换
    for enc in ("utf-8", "gbk"):
        try:
            return b.decode(enc)
        except Exception:
            pass
    return b.decode("utf-8", errors="replace")


def collect_audio_files(dir_path: Path) -> set[Path]:
    audio_exts = (".mp3", ".flac", ".wav", ".m4a")
    return {
        p.resolve()
        for p in dir_path.iterdir()
        if p.is_file() and p.suffix.lower() in audio_exts
    }


def map_to_dist(res_root: Path, dist_root: Path, src_path: Path) -> Path:
    return dist_root / src_path.relative_to(res_root)


def ensure_dir(path: Path, dry_run: bool) -> None:
    if path.exists():
        return
    if not dry_run:
        path.mkdir(parents=True, exist_ok=True)


def copy_if_missing(src: Path, dst: Path, dry_run: bool) -> bool:
    if dst.exists():
        try:
            if src.stat().st_size == dst.stat().st_size:
                return True
        except Exception:
            return True
        print(f"[WARN] 目标已存在且大小不同，跳过复制：{dst}")
        return False
    print(f"[INFO] 复制：{src} -> {dst}")
    if not dry_run:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except Exception as e:
            print(f"[WARN] 复制失败：{src} err={e}", file=sys.stderr)
            return False
    return True


def sync_audio_from_res_to_dist(res_root: Path, dist_root: Path, dry_run: bool) -> None:
    for src in res_root.rglob("*"):
        if not src.is_file():
            continue
        if src.suffix.lower() not in (".mp3", ".flac", ".wav", ".m4a"):
            continue
        dst = map_to_dist(res_root, dist_root, src)
        copy_if_missing(src, dst, dry_run)


def sync_cover_from_res_to_dist(res_root: Path, dist_root: Path, dry_run: bool) -> None:
    for src in res_root.rglob("*"):
        if not src.is_file():
            continue
        if src.name not in ("Cover.jpg", "Cover.png"):
            continue
        dst = map_to_dist(res_root, dist_root, src)
        copy_if_missing(src, dst, dry_run)


def sync_lyrics_from_res_to_dist(res_root: Path, dist_root: Path, dry_run: bool) -> None:
    for src in res_root.rglob("*.lrc"):
        if not src.is_file():
            continue
        dst = map_to_dist(res_root, dist_root, src)
        copy_if_missing(src, dst, dry_run)


def extract_ncm_cover_bytes(ncm_path: Path) -> Optional[bytes]:
    try:
        with ncm_path.open("rb") as f:
            if f.read(8) != b"CTENFDAM":
                return None
            f.seek(2, 1)
            key_len_raw = f.read(4)
            if len(key_len_raw) != 4:
                return None
            key_len = struct.unpack("<I", key_len_raw)[0]
            f.seek(key_len, 1)
            meta_len_raw = f.read(4)
            if len(meta_len_raw) != 4:
                return None
            meta_len = struct.unpack("<I", meta_len_raw)[0]
            f.seek(meta_len, 1)
            f.seek(4, 1)  # crc
            img_len_raw = f.read(4)
            if len(img_len_raw) != 4:
                return None
            img_len = struct.unpack("<I", img_len_raw)[0]
            if img_len <= 0 or img_len > 20 * 1024 * 1024:
                return None
            data = f.read(img_len)
            if len(data) != img_len:
                return None
            return data
    except Exception:
        return None


def detect_image_mime_from_bytes(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "image/jpeg"


def find_ncm_output_audio_file(
    output_dir: Path, ncm_path: Path, before_files: set[Path], start_ts: float
) -> Optional[Path]:
    for ext in (".mp3", ".flac", ".wav", ".m4a"):
        candidate = output_dir / f"{ncm_path.stem}{ext}"
        if candidate.exists():
            return candidate

    after_files = collect_audio_files(output_dir)
    new_files = sorted(
        after_files - before_files, key=lambda p: p.stat().st_mtime, reverse=True
    )
    if new_files:
        return new_files[0]

    candidates = list(after_files)
    recent = [p for p in candidates if p.stat().st_mtime >= start_ts - 1]
    if recent:
        return max(recent, key=lambda p: p.stat().st_mtime)
    return None


def try_write_ncm_cover(
    ncm_path: Path, output_dir: Path, before_files: set[Path], start_ts: float
) -> None:
    cover_data = extract_ncm_cover_bytes(ncm_path)
    if not cover_data:
        return
    target = find_ncm_output_audio_file(output_dir, ncm_path, before_files, start_ts)
    if not target:
        print(f"[WARN] NCM 转换成功但未找到输出文件，跳过写入封面：{ncm_path}")
        return
    if target.suffix.lower() != ".mp3":
        return
    if has_apic(target):
        print(f"[COVER] 已有封面，跳过写入：{target.name}")
        return
    mime = detect_image_mime_from_bytes(cover_data)
    ok = write_apic_bytes(target, cover_data, mime)
    if ok:
        print(f"[COVER] 写入 NCM 内嵌封面：{target.name}")
    else:
        print(f"[WARN] 写入封面失败：{target}")


def mp3_audio_fingerprint_quick(p: Path, sample_bytes: int = 65536) -> str:
    """MP3 快速指纹：忽略 ID3v2/v1 标签，仅对音频区头尾采样 sha1。"""
    try:
        st = p.stat()
        with p.open("rb") as f:
            start = 0
            header = f.read(10)
            if len(header) == 10 and header[:3] == b"ID3":
                size_bytes = header[6:10]
                tag_size = (
                    ((size_bytes[0] & 0x7F) << 21)
                    | ((size_bytes[1] & 0x7F) << 14)
                    | ((size_bytes[2] & 0x7F) << 7)
                    | (size_bytes[3] & 0x7F)
                )
                start = 10 + tag_size

            end = st.st_size
            if end >= 128:
                f.seek(end - 128)
                tail_tag = f.read(128)
                if tail_tag[:3] == b"TAG":
                    end -= 128

            if start >= end:
                start = 0
                end = st.st_size

            h = hashlib.sha1()
            h.update(str(end - start).encode("utf-8"))
            f.seek(start)
            head = f.read(sample_bytes)
            h.update(head)
            if end - start > sample_bytes:
                f.seek(max(start, end - sample_bytes))
                tail = f.read(sample_bytes)
                h.update(tail)
            return h.hexdigest()
    except Exception:
        return ""


def file_fingerprint_quick(p: Path, sample_bytes: int = 65536) -> str:
    """快速指纹：size + 头尾采样 sha1。用于去重和断点判断。"""
    if p.suffix.lower() == ".mp3":
        return mp3_audio_fingerprint_quick(p, sample_bytes=sample_bytes)
    try:
        st = p.stat()
        h = hashlib.sha1()
        h.update(str(st.st_size).encode("utf-8"))
        with p.open("rb") as f:
            head = f.read(sample_bytes)
            h.update(head)
            if st.st_size > sample_bytes:
                try:
                    f.seek(max(0, st.st_size - sample_bytes))
                    tail = f.read(sample_bytes)
                    h.update(tail)
                except Exception:
                    pass
        return h.hexdigest()
    except Exception:
        return ""


def dedupe_mp3_when_track_exists(res_root: Path, dry_run: bool) -> None:
    """如果同目录已存在 '{track} - {title}.mp3'，则删除同内容的 'artist - title.mp3' 这类冗余文件。"""
    # 先按目录分组，减少重复 IO
    by_dir: Dict[Path, List[Path]] = {}
    for p in res_root.rglob("*.mp3"):
        if p.is_file():
            by_dir.setdefault(p.parent, []).append(p)

    for d, mp3s in by_dir.items():
        # 建立 “目标文件”集合：以数字开头的 track 命名
        track_named: Dict[Tuple[str, str], Path] = {}
        for p in mp3s:
            tags = read_id3_basic(p)
            track = parse_track(tags.get("track", ""))
            title = (tags.get("title") or "").strip()
            if not track or not title:
                continue
            target_stem = sanitize_windows_name(f"{track} - {title}")
            target_path = d / (target_stem + ".mp3")
            if target_path.exists():
                track_named[(track, title)] = target_path

        if not track_named:
            continue

        for p in mp3s:
            tags = read_id3_basic(p)
            track = parse_track(tags.get("track", ""))
            title = (tags.get("title") or "").strip()
            if not track or not title:
                continue
            target = track_named.get((track, title))
            if not target:
                continue
            if p.resolve() == target.resolve():
                continue

            # 只有“同内容”才删，避免误删不同版本
            fp1 = file_fingerprint_quick(p)
            fp2 = file_fingerprint_quick(target)
            if fp1 and fp1 == fp2:
                print(f"[DEDUP] 已有 {target.name}，删除冗余：{p.name}")
                if not dry_run:
                    try:
                        p.unlink()
                    except Exception as e:
                        print(f"[WARN] 删除失败：{p} err={e}", file=sys.stderr)


def which(bin_name: str) -> Optional[str]:
    from shutil import which as _which

    return _which(bin_name)


def which_or_die(bin_name: str) -> str:
    p = which(bin_name)
    if not p:
        print(f"[FATAL] 未找到 {bin_name}，请先安装并加入 PATH。", file=sys.stderr)
        sys.exit(2)
    return p


def sanitize_windows_name(name: str) -> str:
    # Windows 禁止字符直接删除；同时删除 !/！
    s = WIN_INVALID_RE.sub("", name)
    s = s.replace("!", "").replace("！", "")
    s = re.sub(r"\s+", " ", s).strip().rstrip(".")
    return s if s else "Unnamed"


def safe_rename(
    src: Path, dst: Path, dry_run: bool, force_suffix_on_conflict: bool
) -> bool:
    if src.resolve() == dst.resolve():
        return False

    if dst.exists():
        if not force_suffix_on_conflict:
            if src.is_file() and dst.is_file():
                fp1 = file_fingerprint_quick(src)
                fp2 = file_fingerprint_quick(dst)
                if fp1 and fp1 == fp2:
                    print(f"[DEDUP] 已有 {dst.name}，删除冗余：{src.name}")
                    if not dry_run:
                        try:
                            src.unlink()
                        except Exception as e:
                            print(f"[WARN] 删除失败：{src} err={e}", file=sys.stderr)
                    return True
            print(f"[SKIP] 目标已存在，跳过重命名：{dst}")
            return False
        base = dst
        i = 1
        while dst.exists():
            dst = base.with_name(f"{base.stem}__{i}{base.suffix}")
            i += 1

    print(f"[RENAME] {src}  ->  {dst}")
    if not dry_run:
        src.rename(dst)
    return True


def parse_track(track_raw: str) -> Optional[str]:
    if not track_raw:
        return None
    t = str(track_raw).strip()
    if "/" in t:
        t = t.split("/", 1)[0].strip()
    t = re.sub(r"\s+", "", t)
    return t if t else None


def strip_prefix_before_last_dash_space(stem: str) -> str:
    idx = stem.rfind("- ")
    if idx >= 0:
        return stem[idx + 2 :].strip()
    return stem.strip()


def pick_first_mp3_recursive(dir_path: Path) -> Optional[Path]:
    for p in dir_path.rglob("*.mp3"):
        if p.is_file():
            return p
    return None


def read_id3_basic(mp3_path: Path) -> Dict[str, str]:
    if ID3 is None:
        return {}
    try:
        id3 = ID3(str(mp3_path))

        def _get(frame_id: str) -> str:
            f = id3.get(frame_id)
            if not f:
                return ""
            try:
                return str(f.text[0]).strip()
            except Exception:
                return str(f).strip()

        return {
            "artist": _get("TPE1"),
            "album": _get("TALB"),
            "track": _get("TRCK"),
            "title": _get("TIT2"),
        }
    except Exception:
        return {}


def has_apic(mp3_path: Path) -> bool:
    if ID3 is None:
        return False
    try:
        id3 = ID3(str(mp3_path))
        return any(k.startswith("APIC") or k.startswith("PIC") for k in id3.keys())
    except Exception:
        return False


def write_apic_bytes(mp3_path: Path, cover_data: bytes, mime: str) -> bool:
    if ID3 is None or APIC is None:
        return False
    try:
        id3 = ID3(str(mp3_path))
    except Exception:
        id3 = ID3()

    try:
        id3.delall("APIC")
        id3.add(
            APIC(
                encoding=3,
                mime=mime,
                type=3,
                desc="Cover",
                data=cover_data,
            )
        )
        id3.save(str(mp3_path))
        return True
    except Exception:
        return False


def extract_apic_to_jpg(mp3_path: Path, out_jpg: Path) -> bool:
    """
    将 MP3 的 APIC 封面写到 out_jpg。
    不做复杂格式判断，直接按原 mime 写出，若不是 jpg/png，ffmpeg 也能读多数格式。
    """
    if ID3 is None:
        return False
    try:
        id3 = ID3(str(mp3_path))
        apic_keys = [k for k in id3.keys() if k.startswith("APIC") or k.startswith("PIC")]
        if not apic_keys:
            return False
        apic = id3[apic_keys[0]]
        data = apic.data  # bytes
        out_jpg.write_bytes(data)
        return out_jpg.exists() and out_jpg.stat().st_size > 0
    except Exception:
        return False


def pick_cover_file(dir_path: Path) -> Optional[Path]:
    for name in ("Cover.jpg", "Cover.png"):
        p = dir_path / name
        if p.exists():
            return p
    return None


def detect_image_format(img_path: Path) -> Optional[str]:
    try:
        with img_path.open("rb") as f:
            header = f.read(8)
        if header.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if header[:3] == b"\xff\xd8\xff":
            return "jpg"
    except Exception:
        return None
    return None


def probe_image_format(ffprobe: str, img_path: Path) -> Optional[str]:
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name",
        "-of",
        "default=nw=1:nk=1",
        windows_input_path(img_path),
    ]
    rc, outb, _errb = run_cmd_bytes(cmd, timeout_sec=10)
    if rc != 0 or not outb:
        return None
    fmt = outb.decode("utf-8", errors="replace").strip().lower()
    return fmt if fmt else None


def normalize_cover_extension(cover_path: Path, ffprobe: Optional[str] = None) -> Path:
    fmt = detect_image_format(cover_path)
    if not fmt and ffprobe:
        fmt = probe_image_format(ffprobe, cover_path)
    if fmt in ("mjpeg", "jpeg"):
        fmt = "jpg"
    if fmt == "png" and cover_path.suffix.lower() != ".png":
        target = cover_path.with_name("Cover.png")
        if target.exists():
            return target
        try:
            cover_path.replace(target)
            print(f"[COVER] 检测到 PNG 封面，重命名为 {target.name}")
            return target
        except Exception:
            return cover_path
    if fmt == "jpg" and cover_path.suffix.lower() != ".jpg":
        target = cover_path.with_name("Cover.jpg")
        if target.exists():
            return target
        try:
            cover_path.replace(target)
            print(f"[COVER] 检测到 JPG 封面，重命名为 {target.name}")
            return target
        except Exception:
            return cover_path
    return cover_path


def is_image_decodable(ffmpeg: str, img_path: Path) -> bool:
    if not img_path.exists() or img_path.stat().st_size == 0:
        return False
    cmd = [
        ffmpeg,
        "-v",
        "error",
        "-i",
        windows_input_path(img_path),
        "-f",
        "null",
        "-",
    ]
    rc, _outb, _errb = run_cmd_bytes(cmd, timeout_sec=30)
    return rc == 0


def fix_cover_image(ffmpeg: str, cover_path: Path) -> bool:
    fixed = cover_path.with_name("Cover_fixed.jpg")
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        windows_input_path(cover_path),
        "-q:v",
        "2",
        windows_input_path(fixed),
    ]
    rc, outb, errb = run_cmd_bytes(cmd, timeout_sec=60)
    if rc != 0 or not fixed.exists() or fixed.stat().st_size == 0:
        print(
            f"[WARN] 封面修复失败：{cover_path}\n{decode_bytes(errb)}\n{decode_bytes(outb)}",
            file=sys.stderr,
        )
        return False
    try:
        cover_path.unlink()
        fixed.replace(cover_path)
    except Exception as e:
        print(f"[WARN] 封面替换失败：{cover_path} err={e}", file=sys.stderr)
        return False
    return True


def pick_cover_source_mp3(top_dir: Path) -> Optional[Path]:
    for p in top_dir.rglob("*.mp3"):
        if p.is_file() and has_apic(p):
            return p
    return None


def prepare_cover_for_dir(
    d: Path,
    ffmpeg: str,
    ffprobe: str,
    dry_run: bool,
    allow_online_fetch: bool = True,
) -> Optional[Path]:
    from netease_cover import fetch_album_cover

    print(f"[COVER] 准备封面：{d}")

    cover_src = pick_cover_source_mp3(d)
    cover_file = pick_cover_file(d) or (d / "Cover.jpg")
    # 若有 APIC，优先使用 MP3 内嵌封面；不锁定已有封面文件
    cover_locked = cover_file.exists() and not cover_src

    def _try_fetch_online_cover() -> Optional[Path]:
        if not allow_online_fetch:
            return None
        mp3_for_tags = cover_src or pick_first_mp3_recursive(d)
        tags = read_id3_basic(mp3_for_tags) if mp3_for_tags else {}
        artist = tags.get("artist", "")
        album = tags.get("album", "")
        if not artist or not album:
            return None
        target = d / "Cover.jpg"
        ok = fetch_album_cover(artist, album, target)
        if not ok:
            return None
        if not is_image_decodable(ffmpeg, target):
            return None
        print(f"[COVER] 在线封面拉取成功：{artist} - {album}")
        for m in d.rglob("*.mp3"):
            write_apic(m, target)
        return target

    if cover_src:
        # 情况 1：优先从 MP3 的 APIC 提取（即使已有封面文件）
        ok = extract_apic_to_jpg(cover_src, cover_file)
        if ok:
            print(f"[COVER] 使用现有 APIC：{cover_src.name}")
        else:
            # APIC 提取失败时再回退到已有封面或在线拉取
            if cover_file.exists():
                print(f"[COVER] APIC 提取失败，使用目录已有封面：{cover_file.name}")
            else:
                fetched = _try_fetch_online_cover()
                if not fetched:
                    if allow_online_fetch:
                        print(f"[SKIP] 拉取在线封面失败：{d}")
                    else:
                        print(f"[SKIP] 未获取封面(禁用在线拉取)：{d}")
                    return None
                cover_file = fetched
    else:
        if cover_locked:
            print(f"[COVER] 目录已有封面文件，按现有封面生成：{cover_file.name}")
        else:
            # 情况 2：全目录无 APIC → 若已有封面文件则不拉在线封面
            if cover_file.exists():
                print(f"[COVER] 目录已有封面文件，跳过在线拉取：{d}")
            else:
                fetched = _try_fetch_online_cover()
                if not fetched:
                    if allow_online_fetch:
                        print(f"[SKIP] 拉取在线封面失败：{d}")
                    else:
                        print(f"[SKIP] 未获取封面(禁用在线拉取)：{d}")
                    return None
                cover_file = fetched

    cover_file = pick_cover_file(d) or (d / "Cover.jpg")
    if not cover_file.exists():
        if not cover_src:
            print(f"[SKIP] 无封面来源，跳过生成：{d}")
            return None
        ok = extract_apic_to_jpg(cover_src, cover_file)
        if not ok:
            print(f"[SKIP] 提取封面失败，跳过生成：{d}")
            return None
        print(f"[COVER] {cover_src.name} -> {cover_file}")

    if not cover_locked:
        cover_file = normalize_cover_extension(cover_file, ffprobe)
        if cover_file.exists() and not is_image_decodable(ffmpeg, cover_file):
            alt_cover = None
            if cover_file.name.lower() == "cover.jpg":
                alt_cover = d / "Cover.png"
            elif cover_file.name.lower() == "cover.png":
                alt_cover = d / "Cover.jpg"

            if (
                alt_cover
                and alt_cover.exists()
                and is_image_decodable(ffmpeg, alt_cover)
            ):
                print(f"[COVER] 当前封面损坏，改用备用：{alt_cover.name}")
                cover_file = alt_cover
            else:
                ok = fix_cover_image(ffmpeg, cover_file)
                if ok:
                    print(f"[COVER] 封面损坏，已修复：{cover_file}")
                else:
                    # 尝试从 APIC 重新提取
                    if cover_src:
                        try:
                            cover_file.unlink()
                        except Exception:
                            pass
                        target = d / "Cover.jpg"
                        ok2 = extract_apic_to_jpg(cover_src, target)
                        if ok2 and is_image_decodable(ffmpeg, target):
                            cover_file = target
                            print(f"[COVER] 重新提取封面：{cover_src.name}")
                        else:
                            fetched = _try_fetch_online_cover()
                            if fetched:
                                cover_file = fetched
                            else:
                                print(f"[SKIP] 封面无法解码且修复失败：{d}")
                                return None
                    else:
                        fetched = _try_fetch_online_cover()
                        if fetched:
                            cover_file = fetched
                        else:
                            print(f"[SKIP] 封面无法解码且修复失败：{d}")
                            return None
    else:
        if cover_file.exists() and not is_image_decodable(ffmpeg, cover_file):
            print(f"[WARN] 封面文件无法解码，按要求不替换，跳过生成：{d}", file=sys.stderr)
            return None

    return cover_file if cover_file.exists() else None


def find_leaf_mp3_dirs(res_root: Path) -> List[Path]:
    mp3_dirs = {p.parent for p in res_root.rglob("*.mp3") if p.is_file()}
    parents_with_children: set[Path] = set()
    for d in mp3_dirs:
        for parent in d.parents:
            if parent in mp3_dirs:
                parents_with_children.add(parent)
    leaf_dirs = [d for d in mp3_dirs if d not in parents_with_children]
    return sorted(leaf_dirs, key=lambda p: str(p).lower())


def organize_mp3_by_artist_album(
    res_root: Path, dry_run: bool, force_rename: bool
) -> None:
    album_to_mp3s: Dict[str, List[Path]] = {}
    album_to_artist_counts: Dict[str, Dict[str, int]] = {}

    for mp3 in res_root.rglob("*.mp3"):
        if not mp3.is_file():
            continue
        tags = read_id3_basic(mp3)
        album = (tags.get("album") or "").strip() or "Unknown Album"
        artist = (tags.get("artist") or "").strip() or "Unknown Artist"
        album_to_mp3s.setdefault(album, []).append(mp3)
        counts = album_to_artist_counts.setdefault(album, {})
        counts[artist] = counts.get(artist, 0) + 1

    for album, mp3s in album_to_mp3s.items():
        artist_counts = album_to_artist_counts.get(album, {})
        if len(artist_counts) > 1:
            print(f"[WARN] 专辑包含多个歌手，将使用数量最多的歌手命名：{album}")
        artist = max(artist_counts.items(), key=lambda item: item[1])[0] if artist_counts else ""
        folder_name = sanitize_windows_name(f"{artist} - {album}" if artist else album)
        target_dir = res_root / folder_name
        moved_cover_from: set[Path] = set()
        if not target_dir.exists():
            print(f"[INFO] 创建目录：{target_dir}")
            if not dry_run:
                target_dir.mkdir(parents=True, exist_ok=True)

        for mp3 in mp3s:
            if mp3.parent.resolve() == target_dir.resolve():
                continue
            if target_dir in mp3.parents:
                continue
            new_path = target_dir / mp3.name
            safe_rename(mp3, new_path, dry_run, force_rename)
            lrc_src = mp3.with_suffix(".lrc")
            if lrc_src.exists():
                lrc_dst = target_dir / lrc_src.name
                safe_rename(lrc_src, lrc_dst, dry_run, force_rename)
            src_dir = mp3.parent
            if src_dir in moved_cover_from:
                continue
            moved_cover_from.add(src_dir)
            for cover_name in ("Cover.jpg", "Cover.png"):
                cover_src = src_dir / cover_name
                if not cover_src.exists():
                    continue
                cover_dst = target_dir / cover_name
                if cover_dst.exists():
                    continue
                safe_rename(cover_src, cover_dst, dry_run, force_rename)


def gen_mp4_with_cover_jpg(
    ffmpeg: str,
    ffprobe: str,
    cover_jpg: Path,
    audio_mp3: Path,
    out_mp4: Path,
    lrc_path: Optional[Path],
    use_gpu: bool,
    dry_run: bool,
    overwrite: bool,
) -> bool:
    vcodec = "h264_nvenc" if use_gpu else "libx264"
    ffmpeg_timeout_sec = 300
    ffmpeg_timeout_retries = 1
    ffmpeg_cpu_retries = 1

    scale_filter = "scale=720:-1"
    subtitle_filter = ""
    srt_path: Optional[Path] = None
    temp_srt_path: Optional[Path] = None
    if lrc_path and lrc_path.exists():
        srt_path = lrc_path.with_suffix(".srt")
        ok = ensure_srt_for_lrc(lrc_path, srt_path, dry_run)
        if ok:
            subtitle_source: Optional[Path] = srt_path
            if needs_safe_subtitle_path(srt_path):
                temp_srt_path = make_safe_subtitle_path(srt_path)
                if not dry_run:
                    try:
                        temp_srt_path.parent.mkdir(parents=True, exist_ok=True)
                        temp_srt_path.write_bytes(srt_path.read_bytes())
                    except Exception as e:
                        print(
                            f"[WARN] 复制 SRT 失败：{srt_path} err={e}",
                            file=sys.stderr,
                        )
                        subtitle_source = None
                        temp_srt_path = None
                if temp_srt_path and (dry_run or temp_srt_path.exists()):
                    subtitle_source = temp_srt_path
            if subtitle_source:
                subtitle_filter = f"subtitles={ffmpeg_filter_path(subtitle_source)}"
            else:
                print(f"[WARN] 字幕文件不可用，将按无字幕生成：{audio_mp3}", file=sys.stderr)

    # 输出文件（与命令行保持一致）
    out_file = out_mp4

    def _build_cmd(codec: str) -> List[str]:
        vf = scale_filter
        if subtitle_filter:
            vf = f"{scale_filter},{subtitle_filter}"
        return [
            ffmpeg,
            "-loop",
            "1",
            "-i",
            windows_input_path(cover_jpg),
            "-i",
            windows_input_path(audio_mp3),
            "-c:v",
            codec,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-vf",
            vf,
            "-shortest",
            str(windows_input_path(out_file)),
        ]

    def _try_ffmpeg(codec: str, attempts: int, label: str) -> str:
        for i in range(1, attempts + 1):
            if out_file.exists():
                try:
                    out_file.unlink()
                except Exception:
                    pass
            cmd = _build_cmd(codec)
            print(f"[CMD] {subprocess.list2cmdline(cmd)}")
            rc, outb, errb = run_cmd_bytes(cmd, timeout_sec=ffmpeg_timeout_sec)
            if rc == 0:
                return "ok"
            if rc == 124:
                print(
                    f"[WARN] ffmpeg 超时({ffmpeg_timeout_sec}s)，{label} 重试 {i}/{attempts}：{out_mp4}",
                    file=sys.stderr,
                )
                continue
            print(
                f"[WARN] 生成失败：{out_mp4}\n{decode_bytes(errb)}\n{decode_bytes(outb)}",
                file=sys.stderr,
            )
            return "error"
        return "timeout"

    def _cleanup_temp_srt() -> None:
        if temp_srt_path and temp_srt_path.exists() and not dry_run:
            try:
                temp_srt_path.unlink()
            except Exception:
                pass

    def _encode_with_codec_fallback(codec: str) -> str:
        status = _try_ffmpeg(codec, ffmpeg_timeout_retries, "GPU" if use_gpu else "CPU")
        if status == "timeout" and use_gpu:
            print(
                f"[WARN] GPU 编码连续超时，改用 libx264 再试 {ffmpeg_cpu_retries} 次：{out_mp4}",
                file=sys.stderr,
            )
            status = _try_ffmpeg("libx264", ffmpeg_cpu_retries, "CPU")
        return status

    print(
        f"[MP4] cover={cover_jpg.name} audio={audio_mp3.name} -> {out_mp4.name} (vcodec={vcodec})"
    )
    if dry_run:
        return True

    # 如果已有 mp4，但可能是上次中断的损坏文件，先做完整性检测
    if out_mp4.exists() and not overwrite:
        if is_mp4_ok(ffprobe, out_mp4):
            return True
        print(f"[WARN] 检测到损坏/不完整 MP4，将重建：{out_mp4}")

    status = _encode_with_codec_fallback(vcodec)
    if status != "ok" and subtitle_filter:
        print(f"[WARN] 字幕渲染失败，回退为无字幕再试：{out_mp4}", file=sys.stderr)
        subtitle_filter = ""
        status = _encode_with_codec_fallback(vcodec)
    if status != "ok":
        if status == "timeout":
            print(f"[WARN] ffmpeg 超时，跳过：{out_mp4}", file=sys.stderr)
        _cleanup_temp_srt()
        return False

    # 生成成功后校验输出文件
    if not out_file.exists() or out_file.stat().st_size == 0:
        _cleanup_temp_srt()
        return False
    if not is_mp4_ok(ffprobe, out_file):
        print(f"[WARN] 生成的 MP4 校验失败，将重试时重建：{out_mp4}", file=sys.stderr)
        try:
            out_file.unlink()
        except Exception:
            pass
        _cleanup_temp_srt()
        return False
    _cleanup_temp_srt()
    if srt_path and srt_path.exists() and not dry_run:
        try:
            srt_path.unlink()
        except Exception:
            pass
    return out_file.exists() and is_mp4_ok(ffprobe, out_file)


def is_mp4_ok(ffprobe: str, mp4: Path) -> bool:
    """检测 mp4 是否可用。
    规则：ffprobe 能解析，包含 audio+video 流，duration > 0。
    """
    try:
        if not mp4.exists() or mp4.stat().st_size < 1024:
            return False

        cmd = [
            ffprobe,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_entries",
            "format=duration",
            "-show_entries",
            "stream=codec_type",
            str(windows_input_path(mp4)) if os.name == "nt" else str(mp4),
        ]
        rc, outb, _errb = run_cmd_bytes(cmd)
        if rc != 0 or not outb:
            return False
        data = json.loads(outb.decode("utf-8", errors="replace"))
        streams = data.get("streams") or []
        codec_types = {s.get("codec_type") for s in streams if isinstance(s, dict)}
        if "audio" not in codec_types or "video" not in codec_types:
            return False
        fmt = data.get("format") or {}
        dur = fmt.get("duration")
        if dur is None:
            return False
        try:
            dur_f = float(dur)
        except Exception:
            return False
        return dur_f > 0.1
    except Exception:
        return False


def parse_lrc_timestamp(ts: str) -> Optional[float]:
    try:
        if ":" not in ts:
            return None
        mm, rest = ts.split(":", 1)
        if "." in rest:
            ss, frac = rest.split(".", 1)
        else:
            ss, frac = rest, "0"
        minutes = int(mm)
        seconds = int(ss)
        frac = (frac + "00")[:2]
        centis = int(frac)
        return minutes * 60 + seconds + centis / 100.0
    except Exception:
        return None


def parse_lrc_lines(lrc_text: str) -> List[Tuple[float, str]]:
    items: List[Tuple[float, str]] = []
    for raw in lrc_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        tags = re.findall(r"\[(\d{1,2}:\d{2}(?:\.\d{1,3})?)\]", line)
        if not tags:
            continue
        text = re.sub(r"\[[^\]]+\]", "", line).strip()
        if not text:
            continue
        for ts in tags:
            t = parse_lrc_timestamp(ts)
            if t is not None:
                items.append((t, text))
    items.sort(key=lambda x: x[0])
    return items


def srt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h = ms // 3600000
    ms -= h * 3600000
    m = ms // 60000
    ms -= m * 60000
    s = ms // 1000
    ms -= s * 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ensure_srt_for_lrc(lrc_path: Path, srt_path: Path, dry_run: bool) -> bool:
    try:
        if srt_path.exists() and srt_path.stat().st_size > 0:
            return True
        data = lrc_path.read_bytes()
        lrc_text = decode_bytes(data)
        items = parse_lrc_lines(lrc_text)
        if not items:
            print(f"[WARN] LRC 无有效歌词行：{lrc_path}", file=sys.stderr)
            return False
        lines: List[str] = []
        for i, (start, text) in enumerate(items, start=1):
            end = items[i][0] - 0.01 if i < len(items) else start + 3.0
            if end <= start:
                end = start + 2.0
            lines.append(str(i))
            lines.append(f"{srt_timestamp(start)} --> {srt_timestamp(end)}")
            lines.append(text)
            lines.append("")
        srt_text = "\n".join(lines)
        if not dry_run:
            srt_path.write_text(srt_text, encoding="utf-8")
        return True
    except Exception as e:
        print(f"[WARN] 生成 SRT 失败：{lrc_path} err={e}", file=sys.stderr)
        return False


def ffmpeg_filter_path(path: Path) -> str:
    p = str(path.resolve())
    p = p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return f"'{p}'"


def needs_safe_subtitle_path(path: Path) -> bool:
    full = str(path.resolve())
    if "'" in full or '"' in full:
        return True
    try:
        full.encode("ascii")
    except UnicodeEncodeError:
        return True
    return False


def get_safe_subtitle_temp_dir() -> Path:
    candidates: List[Path] = []
    candidates.append(Path(tempfile.gettempdir()) / "downloadmusic_subtitles")
    if os.name == "nt":
        candidates.append(Path("C:/Temp/downloadmusic_subtitles"))
    candidates.append(Path.cwd() / "__subtitles_tmp")

    for d in candidates:
        probe = d / "probe.srt"
        if needs_safe_subtitle_path(probe):
            continue
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d
        except Exception:
            continue
    return Path.cwd()


def make_safe_subtitle_path(src: Path) -> Path:
    digest = hashlib.sha1(str(src).encode("utf-8", errors="ignore")).hexdigest()[:8]
    token = f"{os.getpid()}_{time.time_ns()}"
    safe_dir = get_safe_subtitle_temp_dir()
    return safe_dir / f"__sub_{digest}_{token}.srt"


def parse_lrc_metadata(text: str) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    for m in re.finditer(r"\[(ti|ar|al):([^\]]*)\]", text, flags=re.IGNORECASE):
        key = m.group(1).lower()
        if key not in meta:
            meta[key] = m.group(2).strip()
    return meta


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def strip_track_prefix_from_stem(stem: str) -> str:
    m = re.match(r"^\d+\s*-\s*(.+)$", stem)
    return m.group(1).strip() if m else stem.strip()


def split_stem_artist_title(stem: str) -> Tuple[str, str]:
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", stem.strip()


def align_lrc_with_mp3(dist_root: Path, dry_run: bool) -> None:
    lrc_entries = []
    for lrc in dist_root.rglob("*.lrc"):
        if not lrc.is_file():
            continue
        try:
            text = decode_bytes(lrc.read_bytes())
        except Exception:
            text = ""
        meta = parse_lrc_metadata(text)
        stem_artist, stem_title = split_stem_artist_title(lrc.stem)
        lrc_entries.append(
            {
                "path": lrc,
                "title": norm_text(meta.get("ti", "")),
                "artist": norm_text(meta.get("ar", "")),
                "album": norm_text(meta.get("al", "")),
                "stem": norm_text(lrc.stem),
                "stem_strip": norm_text(strip_track_prefix_from_stem(lrc.stem)),
                "stem_artist": norm_text(stem_artist),
                "stem_title": norm_text(stem_title),
            }
        )

    used: set[Path] = set()

    for mp3 in dist_root.rglob("*.mp3"):
        if not mp3.is_file():
            continue
        dst = mp3.with_suffix(".lrc")
        if dst.exists():
            continue
        tags = read_id3_basic(mp3)
        title = norm_text(tags.get("title") or "")
        artist = norm_text(tags.get("artist") or "")
        stem_norm = norm_text(mp3.stem)
        stem_strip = norm_text(strip_track_prefix_from_stem(mp3.stem))

        candidate = None
        # 1) 同目录同名/去 track 前缀匹配
        for e in lrc_entries:
            if e["path"] in used:
                continue
            if e["path"].parent != mp3.parent:
                continue
            if e["stem"] == stem_norm or e["stem_strip"] == stem_strip:
                candidate = e
                break
        # 1.5) 同目录按文件名提取的标题匹配（常见：Artist - Title.lrc）
        if not candidate:
            for e in lrc_entries:
                if e["path"] in used or e["path"].parent != mp3.parent:
                    continue
                if not e["stem_title"]:
                    continue
                if e["stem_title"] != stem_strip:
                    continue
                if artist and e["stem_artist"] and e["stem_artist"] != artist:
                    continue
                candidate = e
                break
        # 2) 同目录按标题/歌手匹配
        if not candidate and title:
            for e in lrc_entries:
                if e["path"] in used or e["path"].parent != mp3.parent:
                    continue
                if (
                    (e["title"] == title or e["stem_title"] == title)
                    and (not e["artist"] or e["artist"] == artist)
                ):
                    candidate = e
                    break
        # 3) 全局按标题/歌手匹配
        if not candidate and title:
            for e in lrc_entries:
                if e["path"] in used:
                    continue
                if (
                    (e["title"] == title or e["stem_title"] == title)
                    and (not e["artist"] or e["artist"] == artist)
                ):
                    candidate = e
                    break
        # 4) 全局文件名匹配
        if not candidate:
            for e in lrc_entries:
                if e["path"] in used:
                    continue
                if (
                    e["stem"] == stem_norm
                    or e["stem_strip"] == stem_strip
                    or e["stem_title"] == stem_strip
                ):
                    if artist and e["stem_artist"] and e["stem_artist"] != artist:
                        continue
                    candidate = e
                    break

        if not candidate:
            continue
        src = candidate["path"]
        if src.resolve() == dst.resolve():
            used.add(src)
            continue
        safe_rename(src, dst, dry_run, force_suffix_on_conflict=True)
        used.add(src)


def clean_dist_outputs(dist_root: Path, album_dirs: List[Path], dry_run: bool) -> None:
    keep_dirs = {d.resolve() for d in album_dirs}
    keep_names = {"Cover.jpg", "Cover.png"}
    keep_exts = {".mp3", ".mp4", ".lrc"}

    for child in dist_root.iterdir():
        if child.resolve() in keep_dirs:
            continue
        print(f"[DEDUP] 清理非专辑目录/文件：{child}")
        if not dry_run:
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except Exception as e:
                print(f"[WARN] 清理失败：{child} err={e}", file=sys.stderr)

    for d in album_dirs:
        for p in d.rglob("*"):
            if p.is_dir():
                print(f"[DEDUP] 清理子目录：{p}")
                if not dry_run:
                    try:
                        shutil.rmtree(p)
                    except Exception as e:
                        print(f"[WARN] 清理失败：{p} err={e}", file=sys.stderr)
                continue
            if p.name in keep_names:
                continue
            if p.suffix.lower() in keep_exts:
                continue
            print(f"[DEDUP] 清理非输出文件：{p}")
            if not dry_run:
                try:
                    p.unlink()
                except Exception as e:
                    print(f"[WARN] 清理失败：{p} err={e}", file=sys.stderr)


def find_um_exe(project_root: Path) -> Optional[Path]:
    candidates = [
        project_root / "tools" / "um.exe",
        project_root / "um.exe",
        project_root / "web" / "um.exe",
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            return c
    p = which("um")
    if p:
        return Path(p)
    return None


def build_um_cmd(um: Path, output_dir: Path, input_path: Path) -> List[str]:
    base = [str(um), "-o", str(output_dir), str(input_path)]
    if os.name == "nt":
        suffix = um.suffix.lower()
        if suffix in (".cmd", ".bat"):
            return ["cmd", "/c"] + base
        if suffix == ".ps1":
            return [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
            ] + base
    return base


def convert_ncm_to_mp3(
    um: Path, input_path: Path, output_dir: Path, dry_run: bool
) -> bool:
    cmd = build_um_cmd(um, output_dir, input_path)
    print(f"[NCM->MP3] {input_path} -> {output_dir}  (via {um.name})")
    if dry_run:
        return True

    start_ts = time.time()
    before_files = collect_audio_files(output_dir)

    rc, outb, errb = run_cmd_bytes(cmd)
    if rc != 0:
        out_text = decode_bytes(outb)
        err_text = decode_bytes(errb)
        combined = f"{out_text}\n{err_text}".strip()
        benign_markers = [
            "no suitable decoder",
            "skipping while no suitable decoder",
            "xm magic header not matched",
            "run app failed",
        ]
        if "successfully converted" in combined and any(
            m in combined for m in benign_markers
        ):
            try_write_ncm_cover(input_path, output_dir, before_files, start_ts)
            return True
        print(
            f"[WARN] NCM 转换失败：{input_path}\n{err_text}\n{out_text}",
            file=sys.stderr,
        )
        return False

    try_write_ncm_cover(input_path, output_dir, before_files, start_ts)
    return True


def main():
    ap = argparse.ArgumentParser(
        description="只处理 res/：NCM->MP3 + 重命名 + 每目录统一封面生成 MP4"
    )
    ap.add_argument("--root", default=".", help="项目根目录（包含 res/）")
    ap.add_argument("--dist", default="dist", help="输出目录（相对于项目根目录）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不执行")
    ap.add_argument("--no-gpu", action="store_true", help="不用 NVENC，改用 libx264")
    ap.add_argument("--overwrite", action="store_true", help="覆盖已存在的 mp4")
    ap.add_argument(
        "--force-rename", action="store_true", help="重命名冲突时自动加后缀避免冲突"
    )
    ap.add_argument("--skip-ncm", action="store_true", help="跳过 NCM 转换步骤")
    args = ap.parse_args()

    if ID3 is None:
        print(
            "[FATAL] 缺少依赖 mutagen。请先执行：pip install mutagen", file=sys.stderr
        )
        sys.exit(2)

    project_root = Path(args.root).resolve()
    res_root = project_root / "res"
    dist_root = project_root / args.dist

    if not res_root.exists() or not res_root.is_dir():
        print(f"[FATAL] 未找到 res 目录：{res_root}", file=sys.stderr)
        sys.exit(2)
    if dist_root.resolve() == res_root.resolve():
        print("[FATAL] dist 不能与 res 相同。", file=sys.stderr)
        sys.exit(2)

    ffmpeg = which_or_die("ffmpeg")
    ffprobe = which_or_die("ffprobe")  # 用于校验 mp4 完整性

    use_gpu = not args.no_gpu
    dry_run = args.dry_run

    print(f"[PROJECT] {project_root}")
    print(f"[RES] {res_root}")
    print(f"[DIST] {dist_root}")
    print(
        f"[MODE] dry_run={dry_run} use_gpu={use_gpu} overwrite={args.overwrite} force_rename={args.force_rename}"
    )

    ensure_dir(dist_root, dry_run)

    # Step 0: NCM -> MP3
    if not args.skip_ncm:
        um = find_um_exe(project_root)
        if not um:
            print(
                "[FATAL] 未找到 um/um.exe。请放到 project/tools/um.exe 或加入 PATH。",
                file=sys.stderr,
            )
            sys.exit(2)
        if um.exists() and um.is_file() and um.stat().st_size == 0:
            print(
                f"[FATAL] um 可执行文件为空（0 字节）：{um}\n"
                "请重新下载/放置正确的 Unlock Music CLI。",
                file=sys.stderr,
            )
            sys.exit(2)

        ncm_files = [p for p in res_root.rglob("*.ncm") if p.is_file()]
        for ncm in ncm_files:
            out_dir = map_to_dist(res_root, dist_root, ncm.parent)
            ensure_dir(out_dir, dry_run)
            convert_ncm_to_mp3(
                um=um, input_path=ncm, output_dir=out_dir, dry_run=dry_run
            )

    # Step 0.5: 复制已有音频/封面到 dist
    sync_audio_from_res_to_dist(res_root, dist_root, dry_run)
    sync_cover_from_res_to_dist(res_root, dist_root, dry_run)
    sync_lyrics_from_res_to_dist(res_root, dist_root, dry_run)

    # Step 1: 按 artist - album 分类目录（同专辑多歌手时用主歌手）
    organize_mp3_by_artist_album(dist_root, dry_run, args.force_rename)

    # Step 2: 重命名 mp3
    # 断点续跑去重：如果已存在 '{track} - {title}.mp3'，删除同内容的 'artist - title.mp3'
    dedupe_mp3_when_track_exists(dist_root, dry_run)
    for mp3 in dist_root.rglob("*.mp3"):
        if not mp3.is_file():
            continue
        tags = read_id3_basic(mp3)
        track = parse_track(tags.get("track", ""))
        if not track:
            print(f"[SKIP] 无 track，跳过：{mp3}")
            continue
        if mp3.stem.startswith(f"{track} - "):
            continue
        title_part = strip_prefix_before_last_dash_space(mp3.stem)
        new_stem = sanitize_windows_name(f"{track} - {title_part}")
        new_path = mp3.with_name(new_stem + mp3.suffix)
        safe_rename(mp3, new_path, dry_run, args.force_rename)
        lrc_path = mp3.with_suffix(".lrc")
        if lrc_path.exists():
            lrc_new = new_path.with_suffix(".lrc")
            safe_rename(lrc_path, lrc_new, dry_run, args.force_rename)

    # LRC 归位并与 mp3 同名
    align_lrc_with_mp3(dist_root, dry_run)

    # 目录可能变化，重扫
    album_dirs = find_leaf_mp3_dirs(dist_root)

    # Step 3/4: 每目录统一封面（APIC）+ 生成 MP4
    for d in album_dirs:
        cover_file = prepare_cover_for_dir(d, ffmpeg, ffprobe, dry_run)
        if not cover_file:
            continue

        for mp3 in d.rglob("*.mp3"):
            if not mp3.is_file():
                continue
            out_mp4 = mp3.with_suffix(".mp4")
            if out_mp4.exists() and not args.overwrite and is_mp4_ok(ffprobe, out_mp4):
                print(f"[SKIP] MP4 已存在且完整，跳过：{out_mp4}")
                continue
            gen_mp4_with_cover_jpg(
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                cover_jpg=cover_file,
                audio_mp3=mp3,
                out_mp4=out_mp4,
                lrc_path=mp3.with_suffix(".lrc"),
                use_gpu=use_gpu,
                dry_run=dry_run,
                overwrite=args.overwrite,
            )

    # Step 5: 清理 dist，仅保留 “歌手-专辑” 目录中的 mp3/mp4/cover
    clean_dist_outputs(dist_root, album_dirs, dry_run)

    print("[DONE]")


if __name__ == "__main__":
    main()
