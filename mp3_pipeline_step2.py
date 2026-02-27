#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import sys
from pathlib import Path

import mp3_pipeline as m


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Step2: 用已有封面生成 MP4（不改封面）"
    )
    parser.add_argument("--root", default=".", help="project root (contains dist/)")
    parser.add_argument("--dry-run", action="store_true", help="preview without changing files")
    parser.add_argument("--no-gpu", action="store_true", help="force CPU encoding")
    parser.add_argument("--overwrite", action="store_true", help="overwrite existing mp4")
    args = parser.parse_args()

    project_root = Path(args.root).resolve()
    dist_root = project_root / "dist"

    ffmpeg = m.which_or_die("ffmpeg")
    ffprobe = m.which_or_die("ffprobe")
    use_gpu = not args.no_gpu

    print(f"[PROJECT] {project_root}")
    print(f"[DIST] {dist_root}")
    print(
        f"[MODE] dry_run={args.dry_run} use_gpu={use_gpu} overwrite={args.overwrite}"
    )

    album_dirs = m.find_leaf_mp3_dirs(dist_root)
    for d in album_dirs:
        cover_file = m.pick_cover_file(d)
        if not cover_file or not cover_file.exists():
            print(f"[SKIP] 无封面文件，跳过生成：{d}")
            continue
        if not m.is_image_decodable(ffmpeg, cover_file):
            print(f"[WARN] 封面无法解码，按要求不替换，跳过生成：{d}", file=sys.stderr)
            continue
        for mp3 in d.rglob("*.mp3"):
            if not mp3.is_file():
                continue
            out_mp4 = mp3.with_suffix(".mp4")
            if out_mp4.exists() and not args.overwrite and m.is_mp4_ok(ffprobe, out_mp4):
                print(f"[SKIP] MP4 已存在且完整，跳过：{out_mp4}")
                continue
            m.gen_mp4_with_cover_jpg(
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                cover_jpg=cover_file,
                audio_mp3=mp3,
                out_mp4=out_mp4,
                lrc_path=mp3.with_suffix(".lrc"),
                use_gpu=use_gpu,
                dry_run=args.dry_run,
                overwrite=args.overwrite,
            )

    print("[DONE] Step2")


if __name__ == "__main__":
    main()
