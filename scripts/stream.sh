#!/usr/bin/env bash
set -euo pipefail

: "${CAMERA_INDEX:?CAMERA_INDEX is required}"
: "${STREAM_WIDTH:?STREAM_WIDTH is required}"
: "${STREAM_HEIGHT:?STREAM_HEIGHT is required}"
: "${STREAM_FPS:?STREAM_FPS is required}"

RTMP_URL="${RTMP_URL:-rtmp://a.rtmp.youtube.com/live2/${YOUTUBE_STREAM_KEY:?YOUTUBE_STREAM_KEY is required}}"

rpicam-vid --codec h264 --inline -t 0 --camera "$CAMERA_INDEX" \
  --width "$STREAM_WIDTH" --height "$STREAM_HEIGHT" --framerate "$STREAM_FPS" -o - \
  | ffmpeg -f h264 -i - -c copy -f flv "$RTMP_URL"
