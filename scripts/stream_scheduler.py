"""レオパの活動時間（日没〜日の出）に合わせて YouTube Live 配信を自動開始・終了する常駐デーモン。"""

import datetime
import math
import os
import signal
import subprocess
import sys
import time
import zoneinfo

from astral import LocationInfo
from astral.sun import sun

import youtube_broadcast

POLL_INTERVAL_SECONDS = 60
WAIT_FOR_LIVE_TIMEOUT_SECONDS = 180
WAIT_FOR_LIVE_POLL_INTERVAL_SECONDS = 10


def load_config():
    return {
        "camera_index": int(os.environ["CAMERA_INDEX"]),
        "stream_width": int(os.environ["STREAM_WIDTH"]),
        "stream_height": int(os.environ["STREAM_HEIGHT"]),
        "stream_fps": int(os.environ["STREAM_FPS"]),
        "youtube_stream_key": os.environ["YOUTUBE_STREAM_KEY"],
        "youtube_client_id": os.environ["YOUTUBE_CLIENT_ID"],
        "youtube_client_secret": os.environ["YOUTUBE_CLIENT_SECRET"],
        "youtube_refresh_token": os.environ["YOUTUBE_REFRESH_TOKEN"],
        "latitude": float(os.environ["IR_LIGHT_LATITUDE"]),
        "longitude": float(os.environ["IR_LIGHT_LONGITUDE"]),
        "timezone": os.environ["IR_LIGHT_TIMEZONE"],
        "max_segment_hours": float(os.environ["MAX_SEGMENT_HOURS"]),
    }


def current_night_sun_times(latitude, longitude, tz, now):
    """now を含む「夜」（日没〜翌日の日の出）の sunset と sunrise を返す。

    当日の sunrise/sunset だけを見ると、日没後は当日 sunset が過去、日の出は
    「翌日」のものが必要になり、逆に日の出前（深夜〜明け方）は「前日」の sunset が
    必要になる。単純に当日の sunrise/sunset をペアで返すと sunset > sunrise になり
    compute_segments の区間が負（本番では配信が始まらない、設定によっては
    ZeroDivisionError）になっていたため、now を基準に前日/翌日へまたいで解決する。

    date だけで日没後/日の出前を判定せず、now と当日の sunrise を比較しているのは、
    0時をまたいでも同じ夜として同じタプルを返すようにするため（日付境界で余計な
    セグメント再作成が走らないように current_segment_end を安定させる）。
    """
    location = LocationInfo(latitude=latitude, longitude=longitude, timezone=str(tz))
    today = now.date()
    today_sun = sun(location.observer, date=today, tzinfo=tz)
    if now < today_sun["sunrise"]:
        # 深夜〜明け方: 今夜はまだ続いている前日の夜。前日の sunset と当日の sunrise の組。
        yesterday_sun = sun(
            location.observer, date=today - datetime.timedelta(days=1), tzinfo=tz
        )
        return yesterday_sun["sunset"], today_sun["sunrise"]
    # 日中〜日没後: これから、または今始まっている夜。当日の sunset と翌日の sunrise の組。
    tomorrow_sun = sun(
        location.observer, date=today + datetime.timedelta(days=1), tzinfo=tz
    )
    return today_sun["sunset"], tomorrow_sun["sunrise"]


def compute_segments(sunset, sunrise, max_segment_hours):
    """sunset〜sunrise を、どのセグメントも max_segment_hours 以下になるよう均等分割する。

    端数の短いセグメントを作らず、常に近い長さの動画に揃える
    （例: 14時間の夜を 10h+4h ではなく 7h×2 に分割する）。
    """
    total_seconds = (sunrise - sunset).total_seconds()
    max_seconds = max_segment_hours * 3600
    segment_count = math.ceil(total_seconds / max_seconds)
    segment_seconds = total_seconds / segment_count
    return [
        (
            sunset + datetime.timedelta(seconds=segment_seconds * i),
            sunset + datetime.timedelta(seconds=segment_seconds * (i + 1)),
        )
        for i in range(segment_count)
    ]


def build_rpicam_command(config):
    return [
        "rpicam-vid",
        "--codec", "h264",
        "--inline",
        "-t", "0",
        "--camera", str(config["camera_index"]),
        "--width", str(config["stream_width"]),
        "--height", str(config["stream_height"]),
        "--framerate", str(config["stream_fps"]),
        "--intra", str(config["stream_fps"] * 2),
        "-o", "-",
    ]


def build_ffmpeg_command(config, rtmp_url):
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-f", "h264",
        "-framerate", str(config["stream_fps"]),
        "-use_wallclock_as_timestamps", "1",
        "-i", "-",
        "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-map", "0:v",
        "-map", "1:a",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        "-f", "flv",
        rtmp_url,
    ]


def start_encoder(config, rtmp_url, popen_fn=subprocess.Popen):
    """rpicam-vid | ffmpeg のパイプラインを起動し、両方の Popen を返す。"""
    rpicam_proc = popen_fn(build_rpicam_command(config), stdout=subprocess.PIPE)
    ffmpeg_proc = popen_fn(build_ffmpeg_command(config, rtmp_url), stdin=rpicam_proc.stdout)
    # 親プロセスが読み取り端を持ったまま閉じないと、ffmpeg が先に終了したときに
    # 読み取り端が閉じきらず rpicam-vid が書き込みで SIGPIPE を受け取れずブロックし続ける。
    rpicam_proc.stdout.close()
    return rpicam_proc, ffmpeg_proc


def stop_encoder(rpicam_proc, ffmpeg_proc, timeout_seconds=10):
    """パイプラインを graceful に終了させる。

    terminate 後の wait がタイムアウトしたプロセスは kill してから改めて wait する。
    片方が TimeoutExpired を投げても、そこで打ち切らずもう片方の後始末を続ける
    （でないと片方が残り続け、次回の start_encoder がカメラを掴めない等の不具合になる）。
    """
    rpicam_proc.terminate()
    ffmpeg_proc.terminate()
    for proc in (rpicam_proc, ffmpeg_proc):
        try:
            proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def encoder_is_alive(rpicam_proc, ffmpeg_proc):
    return rpicam_proc.poll() is None and ffmpeg_proc.poll() is None


def _raise_system_exit(signum, frame):
    """SIGTERM ハンドラ。ir_light_daemon.py と同じ理由で、finally でのエンコーダ停止を保証する。"""
    raise SystemExit(0)


def _build_rtmp_url(stream_key):
    return f"rtmp://a.rtmp.youtube.com/live2/{stream_key}"


def main():
    config = load_config()
    tz = zoneinfo.ZoneInfo(config["timezone"])
    signal.signal(signal.SIGTERM, _raise_system_exit)

    youtube = youtube_broadcast.build_youtube_client(
        config["youtube_client_id"],
        config["youtube_client_secret"],
        config["youtube_refresh_token"],
    )
    stream_id = youtube_broadcast.find_stream_id(youtube, config["youtube_stream_key"])
    if stream_id is None:
        raise RuntimeError("YOUTUBE_STREAM_KEY に対応する liveStream が見つかりません")

    rpicam_proc = None
    ffmpeg_proc = None
    current_segment_end = None

    try:
        while True:
            now = datetime.datetime.now(tz)
            sunset, sunrise = current_night_sun_times(
                config["latitude"], config["longitude"], tz, now
            )
            segments = compute_segments(sunset, sunrise, config["max_segment_hours"])
            active_segment = next(
                (s for s in segments if s[0] <= now < s[1]), None
            )

            if active_segment is not None:
                try:
                    if rpicam_proc is None:
                        title = f"Terrascape Live {now:%Y-%m-%d %H:%M}"
                        broadcast_id = youtube_broadcast.create_broadcast(
                            youtube, stream_id, title, privacy_status="unlisted"
                        )
                        rpicam_proc, ffmpeg_proc = start_encoder(
                            config, _build_rtmp_url(config["youtube_stream_key"])
                        )
                        current_segment_end = active_segment[1]
                        if not youtube_broadcast.wait_for_live(
                            youtube,
                            broadcast_id,
                            WAIT_FOR_LIVE_TIMEOUT_SECONDS,
                            WAIT_FOR_LIVE_POLL_INTERVAL_SECONDS,
                        ):
                            # live にならなかった。一度止めて次のポーリングサイクルで再試行する。
                            stop_encoder(rpicam_proc, ffmpeg_proc)
                            rpicam_proc = None
                            ffmpeg_proc = None
                            current_segment_end = None
                    elif current_segment_end != active_segment[1]:
                        # セグメント境界をまたいだ（分割点に到達した）。一度止めて
                        # 次のポーリングサイクルで新しいセグメントとして再作成する。
                        stop_encoder(rpicam_proc, ffmpeg_proc)
                        rpicam_proc = None
                        ffmpeg_proc = None
                        current_segment_end = None
                    elif not encoder_is_alive(rpicam_proc, ffmpeg_proc):
                        # 配信時間帯中にクラッシュした。同じセグメント内で再起動する。
                        rpicam_proc, ffmpeg_proc = start_encoder(
                            config, _build_rtmp_url(config["youtube_stream_key"])
                        )
                except Exception as exc:
                    # YouTube API のネットワークエラー・トークン失効等はここで捕まえ、
                    # プロセス全体をクラッシュさせず次のポーリングサイクルでリトライする
                    # （spec の要求どおり）。rpicam_proc が既に起動済みなら状態はそのまま
                    # 保持し、次のループで encoder_is_alive の分岐に自然に合流する。
                    print(f"stream_scheduler: error handling segment: {exc}", file=sys.stderr)
            else:
                if rpicam_proc is not None:
                    stop_encoder(rpicam_proc, ffmpeg_proc)
                    rpicam_proc = None
                    ffmpeg_proc = None
                    current_segment_end = None

            time.sleep(POLL_INTERVAL_SECONDS)
    finally:
        if rpicam_proc is not None:
            stop_encoder(rpicam_proc, ffmpeg_proc)


if __name__ == "__main__":
    main()
