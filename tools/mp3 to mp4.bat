@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

:: Set the working directory to the script's directory
cd /d %~dp0

:: 遍历当前目录及所有子目录下的*.mp3文件
for /r %%i in (*.mp3) do (
    echo Processing: "%%i"

    :: 获取MP3文件所在的目录
    set "FileDir=%%~dpi"

    :: 提取封面图, 保存为同目录下的Cover_temp.jpg
    ffmpeg -i "%%i" -an -vcodec copy "!FileDir!Cover_temp.jpg"

    :: 检查Cover_temp.jpg是否存在
    if exist "!FileDir!Cover_temp.jpg" (
        echo Found cover image for "%%i"
        :: 使用Cover_temp.jpg作为视频封面, 并将结果保存为MP4
        ffmpeg -loop 1 -i "!FileDir!Cover_temp.jpg" -i "%%i" -c:v h264_nvenc -c:a aac -b:a 192k -vf "scale=720:-1" -shortest "%%~dpi%%~ni.mp4"

        :: 删除临时封面图
        del "!FileDir!Cover_temp.jpg"
    ) else (
        echo Cover image not found for "%%i", using default image...
        :: 使用默认封面（如果需要的话，你可以添加一个默认的封面图片路径）
        ffmpeg -loop 1 -i "path_to_default_cover.jpg" -i "%%i" -c:v h264_nvenc -c:a aac -b:a 192k -vf "scale=720:-1" -shortest "%%~dpi%%~ni.mp4"
    )
)

echo Conversion completed.
pause
endlocal
