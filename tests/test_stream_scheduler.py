import datetime
import signal
import subprocess
import zoneinfo
from unittest.mock import MagicMock, patch

import pytest

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


def test_stop_encoder_kills_and_waits_process_when_wait_times_out():
    rpicam_proc = MagicMock()
    # 1回目(timeout付き)だけ TimeoutExpired、kill 後の2回目(引数無し)の wait は正常終了させる
    rpicam_proc.wait.side_effect = [
        subprocess.TimeoutExpired(cmd="rpicam-vid", timeout=5),
        None,
    ]
    ffmpeg_proc = MagicMock()

    stream_scheduler.stop_encoder(rpicam_proc, ffmpeg_proc, timeout_seconds=5)

    rpicam_proc.kill.assert_called_once()
    # 1回目: timeout 付きの wait（TimeoutExpired）、2回目: kill 後の引数無し wait
    assert rpicam_proc.wait.call_count == 2
    rpicam_proc.wait.assert_any_call(timeout=5)
    rpicam_proc.wait.assert_any_call()
    # rpicam 側が timeout してもう片方の後始末は飛ばさない
    ffmpeg_proc.terminate.assert_called_once()
    ffmpeg_proc.wait.assert_called_once_with(timeout=5)
    ffmpeg_proc.kill.assert_not_called()


def test_encoder_is_alive_false_when_either_process_exited():
    rpicam_proc = MagicMock(poll=MagicMock(return_value=None))
    ffmpeg_proc = MagicMock(poll=MagicMock(return_value=1))
    assert stream_scheduler.encoder_is_alive(rpicam_proc, ffmpeg_proc) is False

    rpicam_proc.poll.return_value = None
    ffmpeg_proc.poll.return_value = None
    assert stream_scheduler.encoder_is_alive(rpicam_proc, ffmpeg_proc) is True


def _set_required_env(monkeypatch):
    monkeypatch.setenv("CAMERA_INDEX", "0")
    monkeypatch.setenv("STREAM_WIDTH", "1280")
    monkeypatch.setenv("STREAM_HEIGHT", "720")
    monkeypatch.setenv("STREAM_FPS", "30")
    monkeypatch.setenv("YOUTUBE_STREAM_KEY", "key-123")
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "client-id")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("YOUTUBE_REFRESH_TOKEN", "refresh-token")
    monkeypatch.setenv("IR_LIGHT_LATITUDE", "35.6851")
    monkeypatch.setenv("IR_LIGHT_LONGITUDE", "139.7527")
    monkeypatch.setenv("IR_LIGHT_TIMEZONE", "Asia/Tokyo")
    monkeypatch.setenv("MAX_SEGMENT_HOURS", "10")


def test_load_config_reads_all_required_env_vars(monkeypatch):
    _set_required_env(monkeypatch)

    config = stream_scheduler.load_config()

    assert config["camera_index"] == 0
    assert config["stream_fps"] == 30
    assert config["youtube_stream_key"] == "key-123"
    assert config["max_segment_hours"] == 10.0
    assert config["latitude"] == 35.6851


def test_main_starts_broadcast_and_encoder_during_active_segment(monkeypatch):
    """アクティブなセグメント内では broadcast 作成 -> エンコーダ起動 -> SIGTERM で停止、の
    一連の流れが呼ばれることを確認する。ir_light_daemon.py のテストと同様、time.sleep を
    差し替えて1周だけループを回してから SIGTERM を模す。
    """
    _set_required_env(monkeypatch)

    now = _dt(23, 0)  # 常に夜間（アクティブなセグメント内）になる時刻に固定
    sunset = _dt(19, 0)
    sunrise = _dt(5, 0, day=2)

    rpicam_proc = MagicMock()
    ffmpeg_proc = MagicMock()
    youtube_client = MagicMock()

    original_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def _deliver_sigterm(_seconds):
        handler = signal.getsignal(signal.SIGTERM)
        handler(signal.SIGTERM, None)

    # stream_scheduler.datetime モジュール全体ではなく datetime.datetime クラスだけを
    # 差し替える。モジュール全体を patch すると、同じファイル内の compute_segments が
    # 使う datetime.timedelta まで MagicMock になり、main() 内の比較
    # (s[0] <= now < s[1]) が実際の datetime と MagicMock の比較になって壊れる。
    with patch.object(stream_scheduler, "today_sun_times", return_value=(sunrise, sunset)), \
         patch("stream_scheduler.datetime.datetime") as mock_datetime_cls, \
         patch.object(stream_scheduler.youtube_broadcast, "build_youtube_client", return_value=youtube_client), \
         patch.object(stream_scheduler.youtube_broadcast, "find_stream_id", return_value="stream-1"), \
         patch.object(stream_scheduler.youtube_broadcast, "create_broadcast", return_value="bcast-1") as mock_create, \
         patch.object(stream_scheduler.youtube_broadcast, "wait_for_live", return_value=True), \
         patch.object(stream_scheduler, "start_encoder", return_value=(rpicam_proc, ffmpeg_proc)) as mock_start, \
         patch.object(stream_scheduler, "stop_encoder") as mock_stop, \
         patch.object(stream_scheduler, "encoder_is_alive", return_value=True), \
         patch.object(stream_scheduler.time, "sleep", _deliver_sigterm):

        mock_datetime_cls.now.return_value = now

        try:
            with pytest.raises(SystemExit):
                stream_scheduler.main()
        finally:
            signal.signal(signal.SIGTERM, original_sigterm_handler)

    mock_create.assert_called_once()
    mock_start.assert_called_once()
    mock_stop.assert_called_once_with(rpicam_proc, ffmpeg_proc)


def test_main_logs_and_continues_when_create_broadcast_fails(monkeypatch, capsys):
    """YouTube API 呼び出しが例外を投げても main() はクラッシュせず、ログを残して次の
    ポーリングサイクルで再試行することを確認する（spec の「API 呼び出し失敗はログに残し、
    次のポーリングサイクルでリトライする」を満たすガード）。
    """
    _set_required_env(monkeypatch)

    now = _dt(23, 0)
    sunset = _dt(19, 0)
    sunrise = _dt(5, 0, day=2)

    original_sigterm_handler = signal.getsignal(signal.SIGTERM)
    call_count = {"n": 0}

    def _sleep_twice_then_sigterm(_seconds):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            handler = signal.getsignal(signal.SIGTERM)
            handler(signal.SIGTERM, None)

    with patch.object(stream_scheduler, "today_sun_times", return_value=(sunrise, sunset)), \
         patch("stream_scheduler.datetime.datetime") as mock_datetime_cls, \
         patch.object(stream_scheduler.youtube_broadcast, "build_youtube_client", return_value=MagicMock()), \
         patch.object(stream_scheduler.youtube_broadcast, "find_stream_id", return_value="stream-1"), \
         patch.object(
             stream_scheduler.youtube_broadcast,
             "create_broadcast",
             side_effect=RuntimeError("network error"),
         ) as mock_create, \
         patch.object(stream_scheduler, "start_encoder") as mock_start, \
         patch.object(stream_scheduler, "stop_encoder"), \
         patch.object(stream_scheduler.time, "sleep", _sleep_twice_then_sigterm):

        mock_datetime_cls.now.return_value = now

        try:
            with pytest.raises(SystemExit):
                stream_scheduler.main()
        finally:
            signal.signal(signal.SIGTERM, original_sigterm_handler)

    # create_broadcast が例外を投げても main() はクラッシュせず、次のポーリングでも
    # 再試行した（2回呼ばれている = 1回目の例外後もループを継続した証拠）。
    assert mock_create.call_count == 2
    mock_start.assert_not_called()  # broadcast作成が失敗し続けたのでエンコーダは一度も起動しない
    captured = capsys.readouterr()
    assert "error" in captured.err.lower()
