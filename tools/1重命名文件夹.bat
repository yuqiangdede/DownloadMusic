@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

REM 遍历当前目录下的所有文件夹
for /d %%D in (*) do (
    REM 初始化变量
    set "artist="
    set "album="
    set "found=0"

    REM 查找每个文件夹中的第一个mp3文件
    for %%F in ("%%D\*.mp3") do (
        if "!found!"=="0" (
            set "found=1"
            REM 使用ffmpeg提取元数据到临时文件
            set "tempFile=temp_metadata.txt"
            ffmpeg -i "%%F" -f ffmetadata "!tempFile!"

            REM 从临时文件中提取artist和album信息
            for /f "tokens=1* delims==" %%A in ('type "!tempFile!" ^| findstr "artist album"') do (
                if "%%A"=="artist" set "artist=%%B"
                if "%%A"=="album" set "album=%%B"
            )

            REM 删除临时文件
            del "!tempFile!"
        )
    )

    REM 如果找到了艺术家和专辑名，则重命名文件夹
    if defined artist if defined album (
        echo Renaming "%%D" to "!artist! - !album!"
        ren "%%D" "!artist! - !album!"
    )
)

echo Finished renaming folders.
pause
