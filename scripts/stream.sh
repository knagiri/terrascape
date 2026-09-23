#!/usr/bin/env bash
set -euo pipefail

: "${CAMERA_INDEX:?CAMERA_INDEX is required}"
: "${STREAM_WIDTH:?STREAM_WIDTH is required}"
: "${STREAM_HEIGHT:?STREAM_HEIGHT is required}"
: "${STREAM_FPS:?STREAM_FPS is required}"

RTMP_URL="${RTMP_URL:-rtmp://a.rtmp.youtube.com/live2/${YOUTUBE_STREAM_KEY:?YOUTUBE_STREAM_KEY is required}}"

rpicam-vid --codec h264 --inline -t 0 --camera "$CAMERA_INDEX" \
  --width "$STREAM_WIDTH" --height "$STREAM_HEIGHT" --framerate "$STREAM_FPS" \
  --intra "$((STREAM_FPS * 2))" -o - \
  | ffmpeg -hide_banner -loglevel warning \
      -f h264 -framerate "$STREAM_FPS" -use_wallclock_as_timestamps 1 -i - \
      -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 \
      -map 0:v -map 1:a -c:v copy -c:a aac -b:a 128k -shortest -f flv "$RTMP_URL"
  # --intra: YouTube Live は2秒ごとの keyframe を推奨しており、無いと Stream が preparing のまま進まない。
  # -use_wallclock_as_timestamps 1: raw h264 の pipe 入力にはコンテナのタイムスタンプが無く、
  # 無指定だと映像パケットの pts が未設定になる
  # （ffmpeg が `Timestamps are unset in a packet for stream 0` 警告を出す）。
  # anullsrc は出力したサンプル数から 0 始まりの連続したタイムスタンプを生成する
  # （wallclock 基準ではない）。タイムスタンプを持つその音声と mux するため、
  # 映像側にもウォールクロック基準でタイムスタンプを付ける。
  # anullsrc: YouTube Live は音声トラックの無い RTMP を接続はするが配信としては成立させない
  # （TCP レベルではエラーが出ず、Stream Health にも反映されない）。
  # 爬虫類の様子を映すだけなので実音声は不要だが、無音トラックの追加自体は必須。
  # -shortest: anullsrc は無限入力なので、これが無いと rpicam-vid が
  # 落ちて映像入力が EOF になっても ffmpeg が音声だけで配信を続けてしまい、
  # systemd の Restart=always が発火しない。最短入力（映像）に合わせて終了させる。
  # -loglevel warning: 既定の info レベルだと ffmpeg が
  # `Output #0, flv, to 'rtmp://.../live2/<KEY>'` をストリームキー入りで
  # stderr に出力し、systemd 配下では journal に残ってしまう
  # （journalctl は adm / systemd-journal グループなら誰でも読める）。
