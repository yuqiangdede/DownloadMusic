# 音乐处理管道

用于处理网易云音乐文件的 Python 项目，自动化音乐文件整理和视频生成流程。

## 功能特性

- **NCM → MP3 转换**：使用 Unlock Music CLI 将网易云音乐加密格式转换为标准 MP3
- **智能重命名**：基于 ID3 标签自动重命名目录（`Album`）和文件（`Track - Title.mp3`）
- **封面管理**：自动获取专辑封面（从 ID3 标签或网易云音乐 API）
- **MP4 生成**：为每首歌曲生成带封面艺术的 MP4 视频
- **Windows 优化**：专门处理 Windows 平台的路径、编码问题，支持中文路径
- **重复文件检测**：基于文件指纹自动删除冗余文件
- **GPU 加速**：默认使用 NVIDIA NVENC 硬件编码加速视频生成

## 环境要求

- **Python 3.14+**
- **ffmpeg / ffprobe**（需在系统 PATH 中）
- **Unlock Music CLI** (`um.exe`)

### 获取工具

1. **ffmpeg / ffprobe**：从 [ffmpeg 官网](https://ffmpeg.org/download.html) 下载并添加到 PATH
2. **Unlock Music CLI**：
   - 下载后放置到以下任一位置：
     - `tools/um.exe`
     - `um.exe`（项目根目录）
     - `web/um.exe`
     - 系统任意 PATH 路径

## 安装依赖

```bash
# 安装 Python 依赖包
python -m pip install mutagen requests

# （可选）激活虚拟环境
.venv\Scripts\activate
```

**依赖说明**：
- `mutagen`：用于读取和修改 MP3 的 ID3 标签
- `requests`：用于网易云音乐 API 的 HTTP 请求

## 使用方法

### 目录结构

将音乐文件放入 `res/` 目录：

```
DownloadMusic/
├── mp3_pipeline.py          # 主处理脚本
├── netease_cover.py         # 网易云音乐 API 集成
├── tools/um.exe            # Unlock Music CLI
├── res/                    # 音乐文件输入目录（请在此处放置文件）
│   ├── [您的音乐文件]/
│   │   ├── *.ncm           # NCM 加密格式
│   │   └── *.mp3           # 普通 MP3
└── .venv/                  # 虚拟环境（可选）
```

### 运行主管道

```bash
# 运行完整的音乐处理管道（推荐首次运行前先试运行）
python mp3_pipeline.py --dry-run

# 正式运行
python mp3_pipeline.py --root .

# 指定其他根目录
python mp3_pipeline.py --root /path/to/project
```

### 命令行参数

| 参数 | 说明 |
|------|------|
| `--root PATH` | 项目根目录（默认当前目录，需包含 `res/` 子目录） |
| `--dry-run` | 试运行模式：预览所有操作但不实际执行 |
| `--skip-ncm` | 跳过 NCM → MP3 转换步骤 |
| `--no-gpu` | 禁用 GPU 编码，改用 CPU（libx264） |
| `--overwrite` | 覆盖已存在的 MP4 文件 |
| `--force-rename` | 重命名冲突时自动添加后缀（避免跳过） |

### 示例命令

```bash
# 快速预览处理效果
python mp3_pipeline.py --dry-run

# 仅处理已转换的 MP3，跳过 NCM 转换
python mp3_pipeline.py --skip-ncm

# 使用 CPU 编码（适合无 GPU 环境）
python mp3_pipeline.py --no-gpu

# 强制覆盖所有 MP4 文件重新生成
python mp3_pipeline.py --overwrite

# 避免重命名冲突时跳过文件
python mp3_pipeline.py --force-rename
```

### 测试网易云音乐封面获取

```bash
# 独立测试封面获取功能
python netease_cover.py
```

## 处理流程详解

脚本按以下顺序执行：

### Step 0: NCM → MP3 转换
- 使用 `um.exe` 批量转换 `res/` 目录下所有 `.ncm` 文件
- 每个 NCM 文件转换后的 MP3 保存在同一目录
- 自动处理 Windows 下子进程输出解码报错（GBK/UTF-8 混杂问题）

### Step 1: 目录重命名
- 递归扫描所有包含 MP3 的目录（找到"叶子"目录，即不含子 MP3 目录）
- 读取目录内 MP3 的 ID3 标签（artist、album）
- 统计出现最多的专辑标签，将目录重命名为 `Album` 格式
- 使用 ID3 读取避免 ffprobe 的标签乱码问题
- 如果目录包含多个专辑，使用数量最多的标签

### Step 2: 文件重命名
- 递归重命名所有 MP3：`{track} - {title}.mp3`
  - `track`：从 ID3 的 TRCK 标签提取（自动处理 "1/10" 格式）
  - `title`：从原文件名最后一个 `- ` 后的部分提取
- 自动删除重复文件：如果同目录已存在正确的 `Track - Title.mp3`，删除内容相同的旧命名文件

### Step 3: 封面获取
为每个目录统一封面：
1. **优先使用目录内 MP3 的 APIC 标签**：提取第一个包含封面信息的 MP3 的封面
2. **回退到网易云音乐 API**：如果目录内无封面，根据 artist + album 自动拉取
3. **封面提取**：将封面写入目录级 `Cover.jpg` 文件（只生成一次）
4. **封面修复**：自动检测损坏封面并尝试修复，或重新从源提取

### Step 4: MP4 生成
为每个 MP3 生成同名 MP4 文件：
- 使用目录级 `Cover.jpg` 作为静态画面
- 合并 MP3 音频流
- 封面缩放：长边 > 720px 时缩放到 720，保持比例，不放大
- 编码参数：
  - 视频编码：`h264_nvenc`（GPU）或 `libx264`（CPU）
  - 音频编码：`aac`，比特率 192k
  - 视频滤镜：`scale=720:-1`
- 完整性检测：生成后自动校验 MP4 文件是否有效
- 超时处理：GPU 超时自动回退到 CPU

## 处理前后对比

### 输入（`res/`）
```
res/
└── UnknownAlbum/
    ├── song1.ncm              # NCM 加密格式
    ├── artist - song title.mp3  # 命名不规范
    └── another song.mp3
```

### 输出
```
res/
└── Artist Name - Album Name/   # 按 ID3 标签重命名目录
    ├── 01 - Song Title.mp3     # 按 track + title 重命名
    ├── 01 - Song Title.mp4     # 自动生成的 MP4
    ├── 02 - Another Song.mp3
    ├── 02 - Another Song.mp4
    └── Cover.jpg               # 统一的专辑封面
```

## Windows 平台说明

本项目专门针对 Windows 平台进行了优化：

- **长路径支持**：自动为 ffmpeg/ffprobe 命令添加 `\\?\` 前缀，支持 260 字符以上的路径
- **编码容错**：自动处理 GBK/UTF-8 混杂的子进程输出，避免解码报错
- **文件名清理**：自动移除 Windows 非法字符（`<>:"/\|?*`）和特殊符号（`!`、`！`）
- **中文支持**：完整支持包含中文、空格的文件名和路径

## 输出日志说明

运行时输出以下日志标记：

| 标记 | 含义 |
|------|------|
| `[FATAL]` | 致命错误，程序将退出 |
| `[WARN]` | 警告信息，不影响整体处理 |
| `[INFO]` | 一般信息（可选） |
| `[CMD]` | 正在执行的命令 |
| `[NCM->MP3]` | NCM 转换操作 |
| `[RENAME]` | 文件/目录重命名 |
| `[DEDUP]` | 重复文件删除 |
| `[COVER]` | 封面获取/处理操作 |
| `[MP4]` | MP4 生成操作 |
| `[SKIP]` | 跳过某项操作（已存在、无标签等） |
| `[PROJECT]` | 项目根目录信息 |
| `[RES]` | res 目录信息 |
| `[MODE]` | 当前运行模式 |
| `[DONE]` | 处理完成 |

## 故障排除

### 常见问题

**1. 未找到 um.exe**
```
[FATAL] 未找到 um/um.exe。请放到 project/tools/um.exe 或加入 PATH。
```
**解决**：下载 Unlock Music CLI，放置到 `tools/um.exe` 或添加到系统 PATH

**2. 未找到 ffmpeg/ffprobe**
```
[FATAL] 未找到 ffmpeg，请先安装并加入 PATH。
```
**解决**：从 [ffmpeg 官网](https://ffmpeg.org/download.html) 下载并添加到 PATH

**3. 未找到 res 目录**
```
[FATAL] 未找到 res 目录：...
```
**解决**：确保运行时指定的 `--root` 目录下存在 `res/` 子目录

**4. 缺少依赖 mutagen**
```
[FATAL] 缺少依赖 mutagen。请先执行：pip install mutagen
```
**解决**：运行 `python -m pip install mutagen requests`

**5. GPU 编码超时**
```
[WARN] GPU 编码连续超时，改用 libx264 再试...
```
**解决**：这是自动回退机制，正常情况。如频繁出现，使用 `--no-gpu` 强制 CPU 编码

**6. 封面无法解码**
```
[WARN] 封面损坏，已修复：...
```
**解决**：程序会自动尝试修复。如失败，可尝试手动替换 `Cover.jpg`

**7. 权限错误**
```
[WARN] 重命名失败（权限/占用）：...
```
**解决**：关闭可能占用文件的程序（播放器、音乐软件等），以管理员身份运行

### 调试建议

1. **首次使用**：先用 `--dry-run` 预览所有操作
2. **测试小批量**：在 `res/` 下用少量文件测试效果
3. **检查标签**：确保 MP3 文件有正确的 ID3 标签（可用音乐播放器软件查看）
4. **手动干预**：如某目录处理失败，可手动添加 `Cover.jpg` 后重新运行

## 性能考虑

- **重复检测**：使用文件指纹算法（基于大小 + 头尾采样 SHA1），快速识别相同文件
- **批量操作**：按目录分组处理，减少磁盘 I/O
- **封面缓存**：每目录只提取一次封面，避免重复工作
- **GPU 加速**：默认使用 NVIDIA NVENC 编码，速度比 CPU 快 5-10 倍
- **超时保护**：外部进程（ffmpeg、um.exe）均设置超时，避免卡死

## 注意事项

- **备份重要数据**：首次运行前建议备份 `res/` 目录
- **先试运行**：正式处理前使用 `--dry-run` 预览效果
- **检查工具可用性**：确认 `ffmpeg`、`ffprobe`、`um.exe` 可用
- **小批量测试**：在大目录上运行前先用小目录验证效果
- **网络需求**：封面获取需要访问网易云音乐 API，确保网络通畅

## 技术细节

### ID3 标签处理
- 使用的标签字段：
  - `TPE1`：艺术家（Artist）
  - `TALB`：专辑（Album）
  - `TRCK`：音轨号（Track）
  - `TIT2`：标题（Title）
  - `APIC`：封面图片
- 容错处理：标签缺失时使用空字符串回退

### 文件指纹算法
- MP3 文件：跳过 ID3v2/v1 标签区，只对音频数据采样计算 SHA1
- 其他文件：使用大小 + 头尾采样计算 SHA1
- 用于重复检测和断点续跑

### 封面格式支持
- 优先格式：JPEG (`Cover.jpg`)、PNG (`Cover.png`)
- 自动检测：通过文件头或 ffprobe 识别格式
- 格式修正：自动重命名不匹配扩展名的封面文件

## 许可证

本项目仅供个人学习和研究使用。
