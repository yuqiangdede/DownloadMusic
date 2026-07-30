#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""保留 res 原目录结构，将 MP3/NCM 转换为 MP3 和 MP4。"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, Optional

import pipeline_core as m
from netease_cover import fetch_album_cover, fetch_cover_url


AUDIO_EXTENSIONS = {".mp3", ".ncm"}


def iter_files(root: Path, suffix: str) -> Iterable[Path]:
    for path in root.rglob(f"*{suffix}"):
        if path.is_file():
            yield path


def relative_target(res_root: Path, dist_root: Path, source: Path, suffix: str) -> Path:
    return dist_root / source.relative_to(res_root).with_suffix(suffix)


def copy_file(src: Path, dst: Path, dry_run: bool) -> bool:
    if dst.exists():
        try:
            same = m.file_fingerprint_quick(src) == m.file_fingerprint_quick(dst)
        except Exception:
            same = False
        if same:
            print(f"[SKIP] 目标已存在且内容一致：{dst}")
            return True
        print(f"[WARN] 目标已存在但内容不同，将以 res 文件覆盖：{dst}", file=sys.stderr)
    else:
        print(f"[COPY] {src} -> {dst}")
    if dry_run:
        return True
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
    except OSError as error:
        print(f"[WARN] 复制失败：{src} -> {dst} err={error}", file=sys.stderr)
        return False


def cache_key(tags: Dict[str, str], relative_mp3: Path) -> str:
    artist = (tags.get("artist") or "").strip()
    album = (tags.get("album") or "").strip()
    identity = f"{artist}\n{album}" if artist and album else str(relative_mp3)
    digest = hashlib.sha1(identity.encode("utf-8", errors="replace")).hexdigest()[:12]
    readable = m.sanitize_windows_name(f"{artist} - {album}" if artist and album else relative_mp3.stem)
    return f"{readable[:80]}-{digest}"


def cached_cover(cache_root: Path, key: str, ffmpeg: str) -> Optional[Path]:
    for path in sorted(cache_root.glob(f"{key}.*"), key=lambda p: p.suffix.lower()):
        if path.is_file() and m.is_image_decodable(ffmpeg, path):
            return path
    return None


def save_cover_bytes(
    cache_root: Path,
    key: str,
    data: bytes,
    mime: str,
    ffmpeg: str,
    dry_run: bool,
) -> Optional[Path]:
    parsed = m.extract_valid_image_bytes(data)
    if parsed:
        data, mime = parsed
    if not data:
        return None
    extension = m.cover_filename_for_mime(mime).split(".")[-1].lower()
    target = cache_root / f"{key}.{extension}"
    if dry_run:
        return target
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".tmp")
        temp.write_bytes(data)
        temp.replace(target)
    except OSError as error:
        print(f"[WARN] 保存封面缓存失败：{target} err={error}", file=sys.stderr)
        return None
    if not m.is_image_decodable(ffmpeg, target):
        print(f"[WARN] 封面缓存无法解码：{target}", file=sys.stderr)
        try:
            target.unlink()
        except OSError:
            pass
        return None
    return target


def fetch_to_cache(
    cache_root: Path,
    key: str,
    mp3: Path,
    ncm: Optional[Path],
    ffmpeg: str,
    dry_run: bool,
    allow_online: bool,
) -> Optional[Path]:
    tags = m.read_id3_basic(mp3)
    cached = cached_cover(cache_root, key, ffmpeg)
    if cached:
        print(f"[COVER] 使用缓存：{cached.name}")
        return cached
    if dry_run:
        print(f"[COVER] 计划获取封面：{mp3}")
        return cache_root / f"{key}.jpg"
    try:
        cache_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print(f"[WARN] 创建封面缓存目录失败：{cache_root} err={error}", file=sys.stderr)
        return None

    if ncm:
        embedded = m.extract_ncm_cover_bytes(ncm)
        if embedded:
            cover = save_cover_bytes(cache_root, key, embedded, m.detect_image_mime_from_bytes(embedded), ffmpeg, False)
            if cover:
                print(f"[COVER] 使用 NCM 内嵌封面：{ncm.name}")
                return cover
        if allow_online:
            metadata = m.read_ncm_metadata(ncm, verbose=True)
            for url in m.ncm_metadata_cover_urls(metadata):
                temp = cache_root / f"{key}.ncm-download"
                if fetch_cover_url(url, temp, verbose=True):
                    try:
                        cover = save_cover_bytes(
                            cache_root,
                            key,
                            temp.read_bytes(),
                            m.mime_from_cover_path(temp),
                            ffmpeg,
                            False,
                        )
                    finally:
                        try:
                            temp.unlink()
                        except OSError:
                            pass
                    if cover:
                        print(f"[COVER] 使用 NCM 元数据封面：{ncm.name}")
                        return cover

    temp = cache_root / f"{key}.apic.jpg"
    if m.extract_apic_to_jpg(mp3, temp):
        try:
            cover = save_cover_bytes(
                cache_root, key, temp.read_bytes(), m.mime_from_cover_path(temp), ffmpeg, False
            )
        finally:
            try:
                temp.unlink()
            except OSError:
                pass
        if cover:
            print(f"[COVER] 使用 MP3 APIC：{mp3.name}")
            return cover

    artist = (tags.get("artist") or "").strip()
    album = (tags.get("album") or "").strip()
    if allow_online and artist and album:
        temp = cache_root / f"{key}.online.jpg"
        if fetch_album_cover(artist, album, temp, verbose=True):
            try:
                cover = save_cover_bytes(
                    cache_root, key, temp.read_bytes(), m.mime_from_cover_path(temp), ffmpeg, False
                )
            finally:
                try:
                    temp.unlink()
                except OSError:
                    pass
            if cover:
                print(f"[COVER] 在线封面成功：{artist} - {album}")
                return cover
    print(f"[WARN] 未找到可用封面，跳过：{mp3}", file=sys.stderr)
    return None


def validate_project(project_root: Path, res_root: Path, dist_root: Path) -> None:
    if not res_root.is_dir():
        print(f"[FATAL] 未找到 res 目录：{res_root}", file=sys.stderr)
        raise SystemExit(2)
    if res_root.resolve() == dist_root.resolve():
        print("[FATAL] dist 不能与 res 相同。", file=sys.stderr)
        raise SystemExit(2)
    if not project_root.is_dir():
        print(f"[FATAL] 项目目录不存在：{project_root}", file=sys.stderr)
        raise SystemExit(2)


def convert_ncms(
    res_root: Path,
    dist_root: Path,
    ncm_files: list[Path],
    existing_mp3_targets: set[Path],
    project_root: Path,
    dry_run: bool,
    skip_ncm: bool,
) -> Dict[Path, Path]:
    ncm_by_target: Dict[Path, Path] = {}
    if skip_ncm:
        print("[MODE] 跳过 NCM 转换")
        return ncm_by_target
    um = m.find_um_exe(project_root)
    if not um:
        print("[FATAL] 未找到 um/um.exe，请放到 tools/um.exe 或加入 PATH。", file=sys.stderr)
        raise SystemExit(2)
    if um.stat().st_size == 0:
        print(f"[FATAL] um 可执行文件为空：{um}", file=sys.stderr)
        raise SystemExit(2)

    for ncm in ncm_files:
        target = relative_target(res_root, dist_root, ncm, ".mp3").resolve()
        if target in existing_mp3_targets:
            print(f"[WARN] 同名 MP3 和 NCM，使用已有 MP3：{ncm}", file=sys.stderr)
            continue
        if target.exists():
            print(f"[SKIP] NCM 对应 MP3 已存在：{target}")
            ncm_by_target[target] = ncm
            continue
        output_dir = target.parent
        before = m.collect_audio_files(output_dir) if output_dir.exists() else set()
        started = time.time()
        m.ensure_dir(output_dir, dry_run)
        ok = m.convert_ncm_to_mp3(um, ncm, output_dir, dry_run, write_cover=False)
        if not ok or dry_run:
            if ok:
                ncm_by_target[target] = ncm
            continue
        if not target.exists():
            candidate = m.find_ncm_output_audio_file(output_dir, ncm, before, started)
            if candidate and candidate.suffix.lower() == ".mp3" and not target.exists():
                try:
                    candidate.replace(target)
                    print(f"[RENAME] NCM 输出统一为原文件名：{candidate.name} -> {target.name}")
                except OSError as error:
                    print(f"[WARN] 无法整理 NCM 输出：{candidate} err={error}", file=sys.stderr)
        if target.exists():
            ncm_by_target[target] = ncm
        else:
            print(f"[WARN] NCM 转换后未找到目标 MP3：{target}", file=sys.stderr)
    return ncm_by_target


def main() -> None:
    parser = argparse.ArgumentParser(description="Step3：保留 res 目录结构生成 MP3 和 MP4")
    parser.add_argument("--root", default=".", help="项目根目录，默认当前目录")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不修改文件")
    parser.add_argument("--skip-ncm", action="store_true", help="跳过 NCM 转 MP3")
    parser.add_argument("--no-gpu", action="store_true", help="使用 CPU 编码")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有 MP4")
    parser.add_argument("--no-online-cover", action="store_true", help="禁用在线封面获取")
    args = parser.parse_args()

    project_root = Path(args.root).resolve()
    res_root = project_root / "res"
    dist_root = project_root / "dist"
    validate_project(project_root, res_root, dist_root)
    ffmpeg = m.which_or_die("ffmpeg")
    ffprobe = m.which_or_die("ffprobe")
    m.ensure_dir(dist_root, args.dry_run)
    cache_root = project_root / "cache" / "covers"

    print(f"[PROJECT] {project_root}")
    print(f"[RES] {res_root}")
    print(f"[DIST] {dist_root}")
    print(
        f"[MODE] dry_run={args.dry_run} use_gpu={not args.no_gpu} "
        f"overwrite={args.overwrite} online_cover={not args.no_online_cover}"
    )

    mp3_sources = sorted(iter_files(res_root, ".mp3"), key=lambda p: str(p).lower())
    ncm_sources = sorted(iter_files(res_root, ".ncm"), key=lambda p: str(p).lower())
    existing_targets: set[Path] = set()
    source_for_target: Dict[Path, Path] = {}
    for source in mp3_sources:
        target = relative_target(res_root, dist_root, source, ".mp3").resolve()
        existing_targets.add(target)
        source_for_target[target] = source
        copy_file(source, target, args.dry_run)
    ncm_by_target = convert_ncms(
        res_root,
        dist_root,
        ncm_sources,
        existing_targets,
        project_root,
        args.dry_run,
        args.skip_ncm,
    )
    for target, ncm in ncm_by_target.items():
        source_for_target.setdefault(target, ncm)

    for target, source in sorted(source_for_target.items(), key=lambda item: str(item[0]).lower()):
        if not target.exists() and not args.dry_run:
            print(f"[SKIP] 没有可处理的 MP3：{target}", file=sys.stderr)
            continue
        lrc_source = source.with_suffix(".lrc")
        lrc_target = target.with_suffix(".lrc")
        if lrc_source.exists():
            copy_file(lrc_source, lrc_target, args.dry_run)

        tags = m.read_id3_basic(target) if target.exists() else m.read_id3_basic(source)
        key = cache_key(tags, target.relative_to(dist_root))
        cover = fetch_to_cache(
            cache_root,
            key,
            target,
            ncm_by_target.get(target.resolve()),
            ffmpeg,
            args.dry_run,
            not args.no_online_cover,
        )
        if not cover:
            continue
        if not args.dry_run and target.exists() and not m.write_apic(target, cover):
            print(f"[WARN] 写入 MP3 APIC 失败：{target}", file=sys.stderr)

        out_mp4 = target.with_suffix(".mp4")
        if out_mp4.exists() and not args.overwrite and m.is_mp4_ok(ffprobe, out_mp4):
            print(f"[SKIP] MP4 已存在且完整：{out_mp4}")
            continue
        m.gen_mp4_with_cover_jpg(
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            cover_jpg=cover,
            audio_mp3=target,
            out_mp4=out_mp4,
            lrc_path=lrc_target if lrc_target.exists() or args.dry_run else None,
            use_gpu=not args.no_gpu,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
        )

    print("[DONE] Step3")


if __name__ == "__main__":
    main()
