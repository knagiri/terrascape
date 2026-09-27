import datetime
import zoneinfo
from unittest.mock import MagicMock

import stream_scheduler

JST = zoneinfo.ZoneInfo("Asia/Tokyo")


def _dt(hour, minute=0, day=1):
    return datetime.datetime(2026, 12, day, hour, minute, tzinfo=JST)


def test_compute_segments_short_night_no_split():
    # 9時間の夜、上限10時間 -> 分割なし
    sunset = _dt(19, 0)
    sunrise = _dt(4, 0, day=2)
    segments = stream_scheduler.compute_segments(sunset, sunrise, max_segment_hours=10)
    assert segments == [(sunset, sunrise)]


def test_compute_segments_long_night_splits_evenly():
    # 14時間の夜、上限10時間 -> 2分割、7時間ずつ
    sunset = _dt(17, 0)
    sunrise = _dt(7, 0, day=2)
    segments = stream_scheduler.compute_segments(sunset, sunrise, max_segment_hours=10)
    assert len(segments) == 2
    expected_mid = sunset + datetime.timedelta(hours=7)
    assert segments[0] == (sunset, expected_mid)
    assert segments[1] == (expected_mid, sunrise)


def test_compute_segments_exactly_at_limit_no_split():
    # ちょうど10時間 -> 分割なし（境界値）
    sunset = _dt(19, 0)
    sunrise = _dt(5, 0, day=2)
    segments = stream_scheduler.compute_segments(sunset, sunrise, max_segment_hours=10)
    assert segments == [(sunset, sunrise)]


def _config():
    return {
        "camera_index": 0,
        "stream_width": 1280,
        "stream_height": 720,
        "stream_fps": 30,
    }


def test_build_rpicam_command_includes_2s_keyframe_interval():
    cmd = stream_scheduler.build_rpicam_command(_config())
    assert cmd[0] == "rpicam-vid"
    assert "--intra" in cmd
    assert cmd[cmd.index("--intra") + 1] == "60"  # 30fps * 2秒
    assert "--camera" in cmd
    assert cmd[cmd.index("--camera") + 1] == "0"


def test_build_ffmpeg_command_includes_audio_track_and_wallclock():
    cmd = stream_scheduler.build_ffmpeg_command(_config(), "rtmp://example/live2/key")
    assert cmd[0] == "ffmpeg"
    assert "-use_wallclock_as_timestamps" in cmd
    assert "anullsrc=channel_layout=stereo:sample_rate=44100" in cmd
    assert cmd[-1] == "rtmp://example/live2/key"
    assert "-shortest" in cmd


def test_start_encoder_pipes_rpicam_stdout_into_ffmpeg_stdin():
    rpicam_proc = MagicMock()
    rpicam_proc.stdout = MagicMock()
    ffmpeg_proc = MagicMock()
    calls = []

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return rpicam_proc if cmd[0] == "rpicam-vid" else ffmpeg_proc

    result_rpicam, result_ffmpeg = stream_scheduler.start_encoder(
        _config(), "rtmp://example/live2/key", popen_fn=fake_popen
    )

    assert result_rpicam is rpicam_proc
    assert result_ffmpeg is ffmpeg_proc
    ffmpeg_call_kwargs = calls[1][1]
    assert ffmpeg_call_kwargs["stdin"] is rpicam_proc.stdout
    rpicam_proc.stdout.close.assert_called_once()  # SIGPIPE 伝播のため呼び出し元で閉じる


def test_stop_encoder_terminates_and_waits_both_processes():
    rpicam_proc = MagicMock()
    ffmpeg_proc = MagicMock()

    stream_scheduler.stop_encoder(rpicam_proc, ffmpeg_proc, timeout_seconds=5)

    rpicam_proc.terminate.assert_called_once()
    ffmpeg_proc.terminate.assert_called_once()
    rpicam_proc.wait.assert_called_once_with(timeout=5)
    ffmpeg_proc.wait.assert_called_once_with(timeout=5)


def test_encoder_is_alive_false_when_either_process_exited():
    rpicam_proc = MagicMock(poll=MagicMock(return_value=None))
    ffmpeg_proc = MagicMock(poll=MagicMock(return_value=1))
    assert stream_scheduler.encoder_is_alive(rpicam_proc, ffmpeg_proc) is False

    rpicam_proc.poll.return_value = None
    ffmpeg_proc.poll.return_value = None
    assert stream_scheduler.encoder_is_alive(rpicam_proc, ffmpeg_proc) is True
