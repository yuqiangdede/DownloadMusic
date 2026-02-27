@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

:: Set the working directory to the script's directory
cd /d %~dp0

:: Traverse all directories at the same level to find and process the first MP3 file in each directory
for /d %%d in (*) do (
    echo Directory: %%d
    set "found=0"

    :: Change to the directory
    pushd "%%d"

    :: Search for the first MP3 file in the current directory and all subdirectories
    for /R %%f in (*.mp3) do (
        if "!found!"=="0" (
            set "found=1"
            echo MP3 File: %%f
            set "MP3_FILE=%%f"
            echo Found MP3 file: !MP3_FILE!
         

            :: Extract cover image using ffmpeg, save to the same directory as the MP3
            ffmpeg -i "!MP3_FILE!" -an -vcodec copy "!MP3_FILE!\..\Cover.jpg"
            if exist "!MP3_FILE!\..\Cover.jpg" (
                echo Cover image successfully extracted to: !MP3_FILE!\..\Cover.jpg
            ) else (
                echo Failed to extract cover image.
            )
        )
    )

    :: Restore the previous directory before moving to the next directory
    popd
)

echo No more directories to check.


endlocal
