#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import sys
from pathlib import Path

import mp3_pipeline as m


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Step1: NCM->MP3, 归档专辑目录，提取封面，等待人工确认"
    )
    parser.add_argument("--root", default=".", help="project root (contains res/ and dist/)")
    parser.add_argument("--dry-run", action="store_true", help="preview without changing files")
    parser.add_argument("--skip-ncm", action="store_true", help="skip NCM -> MP3 conversion")
    parser.add_argument("--force-rename", action="store_true", help="force rename on conflicts")
    parser.add_argument(
        "--no-online-cover",
        action="store_true",
        help="do not fetch cover online (only use existing cover/APIC)",
    )
    args = parser.parse_args()

    project_root = Path(args.root).resolve()
    res_root = project_root / "res"
    dist_root = project_root / "dist"

    ffmpeg = m.which_or_die("ffmpeg")
    ffprobe = m.which_or_die("ffprobe")

    print(f"[PROJECT] {project_root}")
    print(f"[RES] {res_root}")
    print(f"[DIST] {dist_root}")
    print(f"[MODE] dry_run={args.dry_run} skip_ncm={args.skip_ncm}")

    m.ensure_dir(dist_root, args.dry_run)

    # Step 0: NCM -> MP3
    if not args.skip_ncm:
        um = m.find_um_exe(project_root)
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
            out_dir = m.map_to_dist(res_root, dist_root, ncm.parent)
            m.ensure_dir(out_dir, args.dry_run)
            m.convert_ncm_to_mp3(
                um=um, input_path=ncm, output_dir=out_dir, dry_run=args.dry_run
            )

    # Step 0.5: 复制已有音频/封面/歌词到 dist
    m.sync_audio_from_res_to_dist(res_root, dist_root, args.dry_run)
    m.sync_cover_from_res_to_dist(res_root, dist_root, args.dry_run)
    m.sync_lyrics_from_res_to_dist(res_root, dist_root, args.dry_run)

    # Step 1: 按 artist - album 分类目录
    m.organize_mp3_by_artist_album(dist_root, args.dry_run, args.force_rename)

    # Step 2: 重命名 mp3 + LRC 归位
    m.dedupe_mp3_when_track_exists(dist_root, args.dry_run)
    for mp3 in dist_root.rglob("*.mp3"):
        if not mp3.is_file():
            continue
        tags = m.read_id3_basic(mp3)
        track = m.parse_track(tags.get("track", ""))
        if not track:
            print(f"[SKIP] 无 track，跳过：{mp3}")
            continue
        if mp3.stem.startswith(f"{track} - "):
            continue
        title_part = m.strip_prefix_before_last_dash_space(mp3.stem)
        new_stem = m.sanitize_windows_name(f"{track} - {title_part}")
        new_path = mp3.with_name(new_stem + mp3.suffix)
        m.safe_rename(mp3, new_path, args.dry_run, args.force_rename)
        lrc_path = mp3.with_suffix(".lrc")
        if lrc_path.exists():
            lrc_new = new_path.with_suffix(".lrc")
            m.safe_rename(lrc_path, lrc_new, args.dry_run, args.force_rename)

    m.align_lrc_with_mp3(dist_root, args.dry_run)

    # Step 3: 提取/准备封面（不生成 MP4）
    album_dirs = m.find_leaf_mp3_dirs(dist_root)
    covers_dir = dist_root / "_covers"
    for d in album_dirs:
        _ = m.prepare_cover_for_dir(
            d,
            ffmpeg,
            ffprobe,
            args.dry_run,
            allow_online_fetch=not args.no_online_cover,
        )
        cover_file = m.pick_cover_file(d)
        if cover_file and cover_file.exists():
            m.ensure_dir(covers_dir, args.dry_run)
            album_name = m.sanitize_windows_name(d.name)
            dst = covers_dir / f"{album_name}{cover_file.suffix.lower()}"
            m.copy_if_missing(cover_file, dst, args.dry_run)

    print("[DONE] Step1")


if __name__ == "__main__":
    main()
