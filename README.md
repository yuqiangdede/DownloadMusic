# Music 音乐处理管道

这是一个用于处理网易云音乐文件的 Python 项目，主要功能包括：
- 使用 Unlock Music CLI 将 NCM 转换为 MP3
- 基于 ID3 标签重命名目录和文件
- 从网易云音乐 API 获取专辑封面
- 生成带封面艺术的 MP4 视频
- 处理 Windows 下的路径和编码问题

## 环境要求

- Python 3.14+
- ffmpeg / ffprobe（需在 PATH 中）
- Unlock Music CLI（`um.exe`）

## 安装依赖

```bash
python -m pip install mutagen requests
```

## 运行主管道

```bash
# 运行完整的音乐处理管道
python mp3_pipeline.py --root .

# 试运行（预览更改而不执行）
python mp3_pipeline.py --dry-run

# 跳过 NCM 转换步骤
python mp3_pipeline.py --skip-ncm

# 强制 CPU 编码（禁用 GPU）
python mp3_pipeline.py --no-gpu

# 覆盖现有 MP4 文件
python mp3_pipeline.py --overwrite

# 重命名冲突时自动加后缀
python mp3_pipeline.py --force-rename
```

## 运行单个组件

```bash
# 测试网易云音乐封面获取
python netease_cover.py
```

## 目录结构

```
Music/
├── mp3_pipeline.py          # 主处理脚本
├── netease_cover.py         # 网易云音乐 API 集成
├── tools/um.exe            # Unlock Music CLI
├── res/                    # 音乐文件输入目录
│   ├── Artist - Album/     # 处理后的目录
│   └── *.ncm, *.mp3        # 输入文件
└── .venv/                  # 虚拟环境
```

## 注意事项

- 建议先用 `--dry-run` 进行预览
- 处理前确认 `ffmpeg`、`ffprobe`、`um.exe` 可用
- 在大目录上运行前先用小目录验证效果
