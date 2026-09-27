"""レオパの活動時間（日没〜日の出）に合わせて YouTube Live 配信を自動開始・終了する常駐デーモン。"""

import datetime
import math
import subprocess


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
