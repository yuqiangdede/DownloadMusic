@echo off
setlocal enabledelayedexpansion

:: 遍历当前目录及所有子目录下的*.mp3文件
for /r %%i in (*.mp3) do (
    echo Processing: "%%i"

    :: 获取MP3文件所在的目录
    set "FileDir=%%~dpi"

    :: 检查Cover.jpg是否存在于MP3文件的同一目录下
    if exist "!FileDir!Cover.jpg" (
        echo Found cover image for "%%i"
        :: 使用该目录下的Cover.jpg作为视频封面
        ffmpeg -loop 1 -i "!FileDir!Cover.jpg" -i "%%i" -c:v h264_nvenc -c:a aac -b:a 192k -vf "scale=720:-1" -shortest "%%~dpi%%~ni.mp4"
    ) else (
        echo Cover image not found for "%%i", skipping...
    )
)

echo Conversion completed.
pause
