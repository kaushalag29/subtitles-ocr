#!/bin/bash

# REQUIREMENTS:
# python srt module: install with `pip3 install srt`
# custom fork of macOCR: https://github.com/glowinthedark/macOCR
#
# USAGE:
# ./do-all.sh video.mp4

# Initialize the answer variable to always be "yes"
answer="Y"

# read -p "Generate cropped video $1_video-cropped.mp4? (Y/N).." answer
case ${answer:0:1} in
    y|Y )
        ################### TODO: adjust crop area for input video #########################
        # https://video.stackexchange.com/questions/4563/how-can-i-crop-a-video-with-ffmpeg
        # ffmpeg -y -i "$1" -filter:v "crop=1738:400:100:965" -c:a copy "$1_video-cropped.mp4"
        video_file="$1"
        width=$(ffprobe -v error -select_streams v:0 -show_entries stream=width -of default=noprint_wrappers=1:nokey=1 "$video_file")
        height=$(ffprobe -v error -select_streams v:0 -show_entries stream=height -of default=noprint_wrappers=1:nokey=1 "$video_file")
        # Define the cropped height and edges trim width
        cropped_height=400
        edges_trim_width=50
        # Calculate the new width and height for cropping
        new_width=$((width - 2 * edges_trim_width))
        y_start=$((height - cropped_height))
        # Rechange cropped height by x (50) amount to get rid of bottom section by 50px
        cropped_height=350
        # Calculate the x and y start positions for cropping
        x_start=$edges_trim_width
        ffmpeg -y -i "$1" -filter:v "crop=${new_width}:${cropped_height}:${x_start}:${y_start}" -c:a copy "$1_video-cropped.mp4"
        # Crop upper portion of video
        ffmpeg -y -i "$1" -filter:v "crop=${new_width}:250:${x_start}:0" -c:a copy "$1_video-upper-cropped.mp4"
    ;;
    * )
        echo Skipping...
    ;;
esac

# STEP 2: extract key frames to png images with detection threshold

# generate 1 snapshot per second
# read -p "Generate snapshots (y/n)?.." answer
case ${answer:0:1} in
    y|Y )
        rm -rfv "$1_img"
        mkdir -p "$1_img"
        rm -rfv "$1_upper_img"
        mkdir -p "$1_upper_img"
        ffmpeg -y -i "$1_video-cropped.mp4" -start_number 0 -vf "fps=1" -q:v 2 "$1_img/snap_%04d.png"
        ffmpeg -y -i "$1_video-upper-cropped.mp4" -start_number 0 -vf "fps=1" -q:v 2 "$1_upper_img/snap_%04d.png"
    ;;    * )
        echo Skipping...
    ;;
esac

# read -p "Start OCR (y/n)?.." answer
case ${answer:0:1} in
    y|Y )
        if [ -f "$1_results.json" ]; then
            rm -rf -v "$1_results.json"
            rm -rf -v "$1_upper_results.json"
        else
            echo "File does not exist"
        fi
        # rm -rf -v "$1_results.json"
        python3 do-ocr.py "$1_img" "$1_results.json"
        python3 do-ocr.py "$1_upper_img" "$1_upper_results.json"
    ;;
    * )
        echo Skipping...
    ;;
esac


# read -p "Generate SRT (y/n)?.." answer
case ${answer:0:1} in
    y|Y )
        if [ -f "$1.ocr.srt" ]; then
            rm -v "$1.ocr.srt"
        else
            echo "File does not exist"
        fi
        # rm "$1.ocr.srt"
        python3 gensrt.py "$1_results.json" "$1.ocr.srt" "$1_upper_results.json"
    ;;
    * )
        echo Skipping...
    ;;
esac

# read -p "SRT normalize and deduplicate inplace (y/n)?.." answer
case ${answer:0:1} in
    y|Y )
      srt-normalise -i "$1.ocr.srt" --inplace --debug
    ;;
    * )
        echo Skipping...
    ;;
esac

# TODO: install required DEPENDENCIES with `pip install -U srt opencc pypinyin hanzidentifier` 
# read -p "Generate pinyin SRT (y/n)?.." answer
# case ${answer:0:1} in
#     y|Y )
#         python3 srt_subs_zh2pinyin.py "$1.ocr.srt" --force-normalize-input-to-simplified -t -o "$1.ocr.pinyin.srt"
#     ;;
#     * )
#         echo Skipping...
#     ;;
# esac

# # TODO: get your free/paid API key at https://www.deepl.com/docs-api/api-access
# read -p "Deepl translate zh:en $1.ocr.srt (y/n)?.." answer
# case ${answer:0:1} in
#     y|Y )
#         python3 deepl.py zh:en "$1.ocr.srt"
#     ;;
#     * )
#         echo Skipping...
#     ;;
# esac


# read -p "SRT merge (y/n)?.." answer
# case ${answer:0:1} in
#     y|Y )
#         python3 srt_merge.py "$1.ocr.pinyin.srt" "$1.ocr.en.srt"
#     ;;
#     * )
#         echo Skipping...
#     ;;
# esac

exit 0

