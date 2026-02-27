:: @echo off
setlocal enabledelayedexpansion


:: 遍历当前目录及所有子目录下的*.mp3文件
for /R %%F in (*.mp3) do (
    echo Processing file: %%F
    :: 删除旧的元数据文件，避免覆盖提示
    if exist temp_metadata.txt del temp_metadata.txt

    :: 使用ffmpeg提取每个文件的元数据
    ffmpeg -i "%%F" -f ffmetadata temp_metadata.txt

    :: 读取曲目号
    set "TRACK="
    for /f "tokens=2 delims==" %%a in ('type temp_metadata.txt ^| findstr /i "^track"') do (
        set "TRACK=%%a"
        set "TRACK=!TRACK: =!"
        echo Found track: !TRACK!
    )

    if defined TRACK (
        :: 提取原始文件名的纯名称部分（不含扩展名）
        set "OLDNAME=%%~nF"
        set "EXTENSION=%%~xF"

        :: 移除原始文件名中的第一个破折号前的所有内容
        set "NEWNAME=!OLDNAME:*- =!"
        set "NEWNAME=!TRACK! - !NEWNAME!!EXTENSION!"

        :: 重命名文件
        echo Renaming "%%F" to "!NEWNAME!"
        ren "%%F" "!NEWNAME!"
    ) else (
        echo Invalid or no track number found for "%%F".
    )
)



:: 清理临时文件
if exist temp_metadata.txt del temp_metadata.txt

echo Process complete.
pause
endlocal
