#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
只处理 project/res/*：

0) 用 Unlock Music CLI(um.exe) 把 res/**.ncm 转成 mp3（每目录批量）
   - 修复 Windows 下 subprocess 输出解码报错（GBK/UTF-8 混杂）
1) 按 artist - album 重命名 res/* 一级目录（用 ID3，避免 ffprobe 标签乱码）
2) 递归重命名 mp3：{track} - {titlepart}.mp3（titlepart 取原文件名最后一个 "- " 后的部分）
3) 每目录统一封面：选该目录下第一个含 ID3 APIC 的 mp3 作为封面源
4) 每个 mp3 用“目录封面图片”+自己的音频生成同名 mp4
   - 封面如果长边>720才缩到720，保持比例，不放大
   - 这里会落地一个目录级 Cover.jpg（只生成一次，避免重复抽取）
"""

import argparse
import json
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from mutagen.id3 import APIC, ID3
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
        return any(k.startswith("APIC") for k in id3.keys())
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
        apic_keys = [k for k in id3.keys() if k.startswith("APIC")]
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


def find_leaf_mp3_dirs(res_root: Path) -> List[Path]:
    mp3_dirs = {p.parent for p in res_root.rglob("*.mp3") if p.is_file()}
    parents_with_children: set[Path] = set()
    for d in mp3_dirs:
        for parent in d.parents:
            if parent in mp3_dirs:
                parents_with_children.add(parent)
    leaf_dirs = [d for d in mp3_dirs if d not in parents_with_children]
    return sorted(leaf_dirs, key=lambda p: str(p).lower())


def gen_mp4_with_cover_jpg(
    ffmpeg: str,
    ffprobe: str,
    cover_jpg: Path,
    audio_mp3: Path,
    out_mp4: Path,
    use_gpu: bool,
    dry_run: bool,
    overwrite: bool,
) -> bool:
    vcodec = "h264_nvenc" if use_gpu else "libx264"
    ffmpeg_timeout_sec = 300
    ffmpeg_timeout_retries = 1
    ffmpeg_cpu_retries = 1

    scale_filter = "scale=720:-1"

    # 输出文件（与命令行保持一致）
    out_file = out_mp4

    def _build_cmd(codec: str) -> List[str]:
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
            scale_filter,
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

    status = _try_ffmpeg(vcodec, ffmpeg_timeout_retries, "GPU" if use_gpu else "CPU")
    if status == "timeout" and use_gpu:
        print(
            f"[WARN] GPU 编码连续超时，改用 libx264 再试 {ffmpeg_cpu_retries} 次：{out_mp4}",
            file=sys.stderr,
        )
        vcodec = "libx264"
        status = _try_ffmpeg(vcodec, ffmpeg_cpu_retries, "CPU")
    if status != "ok":
        if status == "timeout":
            print(f"[WARN] ffmpeg 超时，跳过：{out_mp4}", file=sys.stderr)
        return False

    # 生成成功后校验输出文件
    if not out_file.exists() or out_file.stat().st_size == 0:
        return False
    if not is_mp4_ok(ffprobe, out_file):
        print(f"[WARN] 生成的 MP4 校验失败，将重试时重建：{out_mp4}", file=sys.stderr)
        try:
            out_file.unlink()
        except Exception:
            pass
        return False
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


def convert_ncm_to_mp3(
    um: Path, input_path: Path, output_dir: Path, dry_run: bool
) -> bool:
    cmd = [str(um), "-o", str(output_dir), str(input_path)]
    print(f"[NCM->MP3] {input_path} -> {output_dir}  (via {um.name})")
    if dry_run:
        return True

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
            return True
        print(
            f"[WARN] NCM 转换失败：{input_path}\n{err_text}\n{out_text}",
            file=sys.stderr,
        )
        return False
    return True


def main():
    ap = argparse.ArgumentParser(
        description="只处理 res/：NCM->MP3 + 重命名 + 每目录统一封面生成 MP4"
    )
    ap.add_argument("--root", default=".", help="项目根目录（包含 res/）")
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

    if not res_root.exists() or not res_root.is_dir():
        print(f"[FATAL] 未找到 res 目录：{res_root}", file=sys.stderr)
        sys.exit(2)

    ffmpeg = which_or_die("ffmpeg")
    ffprobe = which_or_die("ffprobe")  # 用于校验 mp4 完整性

    use_gpu = not args.no_gpu
    dry_run = args.dry_run

    print(f"[PROJECT] {project_root}")
    print(f"[RES] {res_root}")
    print(
        f"[MODE] dry_run={dry_run} use_gpu={use_gpu} overwrite={args.overwrite} force_rename={args.force_rename}"
    )

    # Step 0: NCM -> MP3
    if not args.skip_ncm:
        um = find_um_exe(project_root)
        if not um:
            print(
                "[FATAL] 未找到 um/um.exe。请放到 project/tools/um.exe 或加入 PATH。",
                file=sys.stderr,
            )
            sys.exit(2)

        ncm_files = [p for p in res_root.rglob("*.ncm") if p.is_file()]
        for ncm in ncm_files:
            convert_ncm_to_mp3(
                um=um, input_path=ncm, output_dir=ncm.parent, dry_run=dry_run
            )

    # Step 1: 重命名“最里面的目录”（artist - album），用 ID3 避免乱码
    album_dirs = find_leaf_mp3_dirs(res_root)
    for d in album_dirs:
        album_counts: Dict[Tuple[str, str], int] = {}
        for mp3 in d.glob("*.mp3"):
            if not mp3.is_file():
                continue
            tags = read_id3_basic(mp3)
            artist = (tags.get("artist") or "").strip()
            album = (tags.get("album") or "").strip()
            if not artist or not album:
                continue
            key = (artist, album)
            album_counts[key] = album_counts.get(key, 0) + 1

        if not album_counts:
            continue

        if len(album_counts) > 1:
            print(f"[WARN] 目录包含多个专辑，将使用数量最多的标签重命名：{d}")

        artist, album = max(album_counts, key=album_counts.get)
        new_name = sanitize_windows_name(f"{artist} - {album}")
        new_path = d.parent / new_name
        try:
            safe_rename(d, new_path, dry_run, args.force_rename)
        except PermissionError as e:
            print(f"[WARN] 重命名失败（权限/占用）：{d} err={e}", file=sys.stderr)

    # 目录可能变化，重扫
    album_dirs = find_leaf_mp3_dirs(res_root)

    # Step 2: 重命名 mp3
    # 断点续跑去重：如果已存在 '{track} - {title}.mp3'，删除同内容的 'artist - title.mp3'
    dedupe_mp3_when_track_exists(res_root, dry_run)
    for mp3 in res_root.rglob("*.mp3"):
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

    # Step 3/4: 每目录统一封面（APIC）+ 生成 MP4
    for d in album_dirs:
        from netease_cover import fetch_album_cover

        cover_src = pick_cover_source_mp3(d)
        cover_file = pick_cover_file(d) or (d / "Cover.jpg")

        def _try_fetch_netease_cover() -> Optional[Path]:
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
            print(f"[COVER] 网易云封面拉取成功：{artist} - {album}")
            for m in d.rglob("*.mp3"):
                write_apic(m, target)
            return target

        # 情况 1：目录内已有 APIC
        if cover_src:
            if not cover_file.exists():
                extract_apic_to_jpg(cover_src, cover_file)
                print(f"[COVER] 使用现有 APIC：{cover_src.name}")
        else:
            # 情况 2：全目录无 APIC → 拉网易云
            fetched = _try_fetch_netease_cover()
            if not fetched:
                print(f"[SKIP] 拉取网易云封面失败：{d}")
                continue
            cover_file = fetched

        cover_file = pick_cover_file(d) or (d / "Cover.jpg")
        if not cover_file.exists():
            if not cover_src:
                print(f"[SKIP] 无封面来源，跳过生成：{d}")
                continue
            ok = extract_apic_to_jpg(cover_src, cover_file)
            if not ok:
                print(f"[SKIP] 提取封面失败，跳过生成：{d}")
                continue
            print(f"[COVER] {cover_src.name} -> {cover_file}")

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
                            fetched = _try_fetch_netease_cover()
                            if fetched:
                                cover_file = fetched
                            else:
                                print(f"[SKIP] 封面无法解码且修复失败：{d}")
                                continue
                    else:
                        fetched = _try_fetch_netease_cover()
                        if fetched:
                            cover_file = fetched
                        else:
                            print(f"[SKIP] 封面无法解码且修复失败：{d}")
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
                use_gpu=use_gpu,
                dry_run=dry_run,
                overwrite=args.overwrite,
            )

    print("[DONE]")


if __name__ == "__main__":
    main()
