# AGENTS.md - 音乐处理项目

本文件包含在此音乐处理代码库中工作的智能编码代理的指南和命令。

## 项目概述

这是一个Python音乐处理管道，功能包括：
- 使用Unlock Music CLI将NCM（网易云音乐）文件转换为MP3
- 基于ID3标签重命名目录和文件
- 从网易云音乐API获取专辑封面
- 生成带有封面艺术和音频的MP4视频
- 处理Windows特定的路径和编码问题

## 构建/测试命令

### 环境设置
```bash
# 安装依赖
python -m pip install mutagen requests

# 激活虚拟环境（如果使用.venv）
.venv\Scripts\activate
```

### 运行主管道
```bash
# 运行完整的音乐处理管道
python mp3_pipeline.py --root .

# 试运行（预览更改而不执行）
python mp3_pipeline.py --dry-run

# 跳过NCM转换步骤
python mp3_pipeline.py --skip-ncm

# 强制CPU编码（禁用GPU）
python mp3_pipeline.py --no-gpu

# 覆盖现有MP4文件
python mp3_pipeline.py --overwrite

# 强制重命名冲突时添加后缀
python mp3_pipeline.py --force-rename
```

### 运行单个组件
```bash
# 测试网易云音乐封面获取
python netease_cover.py
```

### 测试单个组件
由于没有正式的测试框架，通过以下方式测试组件：
1. 首先使用`--dry-run`标志运行脚本
2. 在`res/`中使用小型测试目录
3. 手动检查返回值和错误处理

## 代码风格指南

### Python格式化
- 使用Python 3.14+特性
- 遵循PEP 8，使用4空格缩进
- 最大行长度：120个字符
- 一致使用类型提示（from typing import Dict, List, Optional, Tuple）

### 导入组织
```python
# 标准库导入优先
import argparse
import json
import os
import sys
from pathlib import Path

# 第三方导入次之
import requests
from mutagen.id3 import APIC, ID3

# 本地导入最后（如果有）
```

### 命名约定
- 函数：使用描述性名称的`snake_case`
- 变量：`snake_case`，描述性但简洁
- 常量：`UPPER_SNAKE_CASE`
- 文件路径：使用`pathlib.Path`对象，不使用字符串
- 类型提示：使用`Optional[str]`、`Dict[str, str]`等

### 错误处理模式
```python
# 对外部依赖使用广泛的异常处理
try:
    id3 = ID3(str(mp3_path))
except Exception:
    id3 = ID3()

# 返回布尔成功指示器
def write_apic(mp3_path: Path, cover_jpg: Path) -> bool:
    try:
        # 实现
        return True
    except Exception:
        return False

# 向stderr打印警告
print(f"[WARN] 操作失败: {error}", file=sys.stderr)
```

### 函数结构
- 保持函数专注，尽可能在50行以内
- 对错误条件使用早期返回
- 为复杂函数包含文档字符串
- 传递Path对象，不传递字符串路径

### Windows特定考虑
- 对ffmpeg/ffprobe命令使用`windows_input_path()`
- 使用`decode_bytes()`处理GBK/UTF-8编码问题
- 使用`sanitize_windows_name()`清理文件名
- 长路径需要时使用`\\\\?\\`前缀

### 日志/输出格式
使用一致的括号前缀：
- `[FATAL]` - 退出程序的严重错误
- `[WARN]` - 值得注意的非致命问题
- `[INFO]` - 一般信息（可选）
- `[CMD]` - 正在执行的命令
- `[RENAME]` - 文件重命名操作
- `[DEDUP]` - 重复文件删除
- `[COVER]` - 封面艺术操作
- `[MP4]` - MP4生成操作

### ID3标签处理
- 使用前始终检查`ID3 is None`
- 使用`read_id3_basic()`获取常见标签（artist、album、track、title）
- 优雅处理缺失标签，使用空字符串回退
- 使用`parse_track()`清理音轨号

### 文件操作
- 对文件操作使用`pathlib.Path`方法
- 操作前检查文件存在性
- 使用`resolve()`获取规范路径
- 适当处理权限错误

### 子进程使用
- 使用`run_cmd_bytes()`进行一致的子进程处理
- 始终为外部命令指定超时
- 使用`decode_bytes()`处理子进程输出编码

## 依赖项

### 必需包
- `mutagen` - ID3标签操作
- `requests` - 网易云音乐API的HTTP请求

### 外部工具
- `ffmpeg` - 视频/音频处理（必须在PATH中）
- `ffprobe` - 媒体分析（必须在PATH中）
- `um.exe` - NCM转换的Unlock Music CLI

### 工具位置
项目在以下位置查找`um.exe`：
1. `tools/um.exe`
2. `um.exe`（项目根目录）
3. `web/um.exe`
4. 系统PATH

## 目录结构

```
Music/
├── mp3_pipeline.py          # 主处理脚本
├── netease_cover.py         # 网易云音乐API集成
├── tools/um.exe            # Unlock Music CLI
├── res/                    # 音乐文件的输入目录
│   ├── Artist - Album/     # 处理后的目录
│   └── *.ncm, *.mp3        # 输入文件
└── .venv/                  # 虚拟环境
```

## 常见模式

### 安全文件操作
```python
def safe_rename(src: Path, dst: Path, dry_run: bool, force_suffix: bool) -> bool:
    if src.resolve() == dst.resolve():
        return False
    # 带冲突处理的实现
```

### ID3标签读取
```python
tags = read_id3_basic(mp3_path)
artist = (tags.get("artist") or "").strip()
album = (tags.get("album") or "").strip()
```

### 命令构建
```python
cmd = [
    ffmpeg,
    "-i", windows_input_path(input_path),
    "-c:v", codec,
    str(windows_input_path(output_path)),
]
```

## 测试指南

- 始终首先使用`--dry-run`测试
- 在大型集合上运行前使用小型测试目录
- 验证外部工具（ffmpeg、ffprobe、um.exe）可用
- 检查各种文件格式的ID3标签处理
- 测试带有中文字符和空格的Windows路径处理

## 性能考虑

- 使用文件指纹进行重复检测
- 按目录批量操作以减少I/O
- 缓存封面艺术提取结果
- 在可用时使用GPU编码（h264_nvenc）
- 为外部进程实现超时处理