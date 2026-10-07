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


@pytest.fixture(autouse=True)
def _no_pending_broadcasts():
    """main() は broadcast 作成前に残骸の pending broadcast を列挙・削除する。

    youtube クライアントは MagicMock なので、patch しないと list_next が常に
    非 None を返し list_pending_broadcast_ids が無限ループする。既定では残骸なしとし、
    削除呼び出しを検証したいテストは with 内で改めて patch して上書きする。
    """
    with patch.object(
        stream_scheduler.youtube_broadcast, "list_pending_broadcast_ids", return_value=[]
    ), patch.object(stream_scheduler.youtube_broadcast, "delete_broadcast"):
        yield


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


LATITUDE = 35.6851
LONGITUDE = 139.7527


def test_current_night_sun_times_after_sunset_contains_now_with_positive_duration():
    # astral をモックせず実際の日の出/日の入り計算を使う回帰テスト。
    # 旧 today_sun_times は当日の sunrise/sunset をそのまま返すため、日没後は
    # sunset(当日) > sunrise(当日) になり compute_segments の区間が負になっていた
    # （修正前のコードでは本テストが落ちる。ZeroDivisionError または now を含まない
    # 空の segments になる）。
    now = _dt(23, 0)  # 日没後
    sunset, sunrise = stream_scheduler.current_night_sun_times(
        LATITUDE, LONGITUDE, JST, now
    )
    assert sunset < sunrise
    segments = stream_scheduler.compute_segments(sunset, sunrise, max_segment_hours=10)
    assert any(s[0] <= now < s[1] for s in segments)


def test_current_night_sun_times_after_midnight_contains_now_with_positive_duration():
    now = _dt(2, 0, day=2)  # 日付が変わった後、日の出前
    sunset, sunrise = stream_scheduler.current_night_sun_times(
        LATITUDE, LONGITUDE, JST, now
    )
    assert sunset < sunrise
    segments = stream_scheduler.compute_segments(sunset, sunrise, max_segment_hours=10)
    assert any(s[0] <= now < s[1] for s in segments)


def test_current_night_sun_times_same_night_before_and_after_midnight():
    # 日没後と0時後で「同じ夜」（同じ sunset/sunrise の組）を返すことを確認する。
    # main() の current_segment_end はこのタプルの sunrise 側から導かれるセグメント
    # 終端なので、ここが日付境界で変わると深夜0時に不要な再作成が起きる。
    before_midnight = _dt(23, 0)
    after_midnight = _dt(2, 0, day=2)
    result_before = stream_scheduler.current_night_sun_times(
        LATITUDE, LONGITUDE, JST, before_midnight
    )
    result_after = stream_scheduler.current_night_sun_times(
        LATITUDE, LONGITUDE, JST, after_midnight
    )
    assert result_before == result_after


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
    with patch.object(
             stream_scheduler, "current_night_sun_times", return_value=(sunset, sunrise)
         ), \
         patch("stream_scheduler.datetime.datetime") as mock_datetime_cls, \
         patch.object(stream_scheduler.youtube_broadcast, "build_youtube_client", return_value=youtube_client), \
         patch.object(stream_scheduler.youtube_broadcast, "find_stream_id", return_value="stream-1"), \
         patch.object(stream_scheduler.youtube_broadcast, "create_broadcast", return_value="bcast-1") as mock_create, \
         patch.object(stream_scheduler.youtube_broadcast, "wait_for_live", return_value=True) as mock_wait_live, \
         patch.object(stream_scheduler, "start_encoder", return_value=(rpicam_proc, ffmpeg_proc)) as mock_start, \
         patch.object(stream_scheduler, "stop_encoder") as mock_stop, \
         patch.object(stream_scheduler, "encoder_is_alive", return_value=True), \
         patch.object(stream_scheduler.time, "sleep", _deliver_sigterm):

        mock_datetime_cls.now.return_value = now

        # 呼び出し順を1本の mock_calls に集約して確認できるよう、共通の親へぶら下げる。
        call_order = MagicMock()
        call_order.attach_mock(mock_create, "create_broadcast")
        call_order.attach_mock(mock_start, "start_encoder")
        call_order.attach_mock(mock_wait_live, "wait_for_live")

        try:
            with pytest.raises(SystemExit):
                stream_scheduler.main()
        finally:
            signal.signal(signal.SIGTERM, original_sigterm_handler)

    mock_create.assert_called_once()
    mock_start.assert_called_once()
    mock_stop.assert_called_once_with(rpicam_proc, ffmpeg_proc)
    # create_broadcast は enableAutoStart=True で作るので、stream が active になれば
    # YouTube 側が自動で live へ遷移させる。手動の transition は呼ばず（呼ぶとこの
    # 自動遷移と衝突して invalidTransition エラーになる）、エンコーダ起動の直後に
    # live になるのを待つだけでよい。
    assert [c[0] for c in call_order.mock_calls] == [
        "create_broadcast",
        "start_encoder",
        "wait_for_live",
    ]
    mock_wait_live.assert_called_once_with(
        youtube_client,
        "bcast-1",
        stream_scheduler.WAIT_FOR_LIVE_TIMEOUT_SECONDS,
        stream_scheduler.WAIT_FOR_LIVE_POLL_INTERVAL_SECONDS,
    )
    youtube_client.liveBroadcasts.return_value.transition.assert_not_called()


def test_wait_for_live_timeout_allows_slow_auto_start():
    """autoStart による live 遷移は、ドキュメントの目安（5〜10秒）より大幅に遅いことが
    実機で確認されている（encoder 起動から90秒経っても ready のままだった）。timeout が
    短いと live 化の途中で encoder を止めて作り直すのを繰り返すので、10分の余裕を持たせる。
    """
    assert stream_scheduler.WAIT_FOR_LIVE_TIMEOUT_SECONDS == 600


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

    with patch.object(
             stream_scheduler, "current_night_sun_times", return_value=(sunset, sunrise)
         ), \
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


def _sleep_n_times_then_sigterm(n):
    """time.sleep の差し替え用: n 回目の呼び出しで SIGTERM を配送する。"""
    call_count = {"n": 0}

    def _sleep(_seconds):
        call_count["n"] += 1
        if call_count["n"] >= n:
            handler = signal.getsignal(signal.SIGTERM)
            handler(signal.SIGTERM, None)

    return _sleep


def test_main_stops_and_retries_when_wait_for_live_times_out(monkeypatch):
    """wait_for_live が timeout (False) を返したら一度停止し、次のポーリングサイクルで
    broadcast 作成からやり直す（再試行する）ことを確認する。
    """
    _set_required_env(monkeypatch)

    now = _dt(23, 0)  # 常に夜間（アクティブなセグメント内）になる時刻に固定
    sunset = _dt(19, 0)
    sunrise = _dt(5, 0, day=2)

    rpicam_proc = MagicMock()
    ffmpeg_proc = MagicMock()

    original_sigterm_handler = signal.getsignal(signal.SIGTERM)

    with patch.object(
             stream_scheduler, "current_night_sun_times", return_value=(sunset, sunrise)
         ), \
         patch("stream_scheduler.datetime.datetime") as mock_datetime_cls, \
         patch.object(stream_scheduler.youtube_broadcast, "build_youtube_client", return_value=MagicMock()), \
         patch.object(stream_scheduler.youtube_broadcast, "find_stream_id", return_value="stream-1"), \
         patch.object(stream_scheduler.youtube_broadcast, "create_broadcast", return_value="bcast-1") as mock_create, \
         patch.object(
             stream_scheduler.youtube_broadcast, "wait_for_live", side_effect=[False, True]
         ), \
         patch.object(stream_scheduler, "start_encoder", return_value=(rpicam_proc, ffmpeg_proc)) as mock_start, \
         patch.object(stream_scheduler, "stop_encoder") as mock_stop, \
         patch.object(stream_scheduler, "encoder_is_alive", return_value=True), \
         patch.object(stream_scheduler.time, "sleep", _sleep_n_times_then_sigterm(2)):

        mock_datetime_cls.now.return_value = now

        try:
            with pytest.raises(SystemExit):
                stream_scheduler.main()
        finally:
            signal.signal(signal.SIGTERM, original_sigterm_handler)

    # 1回目: create_broadcast -> start_encoder -> wait_for_live=False -> stop_encoder。
    # 2回目: 同じアクティブセグメント内で再度 create_broadcast -> start_encoder（再試行）。
    assert mock_create.call_count == 2
    assert mock_start.call_count == 2
    # 1回目の timeout による停止 + finally での後始末（2回目は wait_for_live=True で
    # 生存したまま SIGTERM を受けるので finally で止まる）で計2回。
    assert mock_stop.call_count == 2


def test_main_stops_encoder_and_retries_when_wait_for_live_raises(monkeypatch):
    """wait_for_live が例外を投げたら、encoder を起動したまま放置せず一度停止し、
    次のポーリングサイクルで broadcast 作成からやり直すことを確認する。

    wait_for_live は start_encoder の後ろで呼ばれる。ここで例外（API の
    一時的なネットワークエラー等）が出ても、外側の except Exception に捕まる前に
    encoder を停止して rpicam_proc 等を None に戻していないと、次のサイクルは
    「current_segment_end == active_segment[1] かつ encoder_is_alive=True」の
    分岐に入ってしまい、live になっていない broadcast へ送り続けたまま
    max_segment_hours まで何もしない（PR #19 で塞いだ後始末の抜け）。
    """
    _set_required_env(monkeypatch)

    now = _dt(23, 0)  # 常に夜間（アクティブなセグメント内）になる時刻に固定
    sunset = _dt(19, 0)
    sunrise = _dt(5, 0, day=2)

    rpicam_proc = MagicMock()
    ffmpeg_proc = MagicMock()

    original_sigterm_handler = signal.getsignal(signal.SIGTERM)

    with patch.object(
             stream_scheduler, "current_night_sun_times", return_value=(sunset, sunrise)
         ), \
         patch("stream_scheduler.datetime.datetime") as mock_datetime_cls, \
         patch.object(stream_scheduler.youtube_broadcast, "build_youtube_client", return_value=MagicMock()), \
         patch.object(stream_scheduler.youtube_broadcast, "find_stream_id", return_value="stream-1"), \
         patch.object(stream_scheduler.youtube_broadcast, "create_broadcast", return_value="bcast-1") as mock_create, \
         patch.object(
             stream_scheduler.youtube_broadcast,
             "wait_for_live",
             side_effect=[RuntimeError("network error"), True],
         ) as mock_wait_live, \
         patch.object(stream_scheduler, "start_encoder", return_value=(rpicam_proc, ffmpeg_proc)) as mock_start, \
         patch.object(stream_scheduler, "stop_encoder") as mock_stop, \
         patch.object(stream_scheduler, "encoder_is_alive", return_value=True), \
         patch.object(stream_scheduler.time, "sleep", _sleep_n_times_then_sigterm(2)):

        mock_datetime_cls.now.return_value = now

        try:
            with pytest.raises(SystemExit):
                stream_scheduler.main()
        finally:
            signal.signal(signal.SIGTERM, original_sigterm_handler)

    # 1回目: wait_for_live が例外 -> encoder を停止して broadcast 作成からやり直す。
    # 2回目: 同じアクティブセグメント内で再度 create_broadcast -> start_encoder（再試行）。
    assert mock_create.call_count == 2
    assert mock_start.call_count == 2
    assert mock_wait_live.call_count == 2
    # 1回目の例外による停止 + finally での後始末（2回目は正常に live になり生存したまま
    # SIGTERM を受けるので finally で止まる）で計2回。
    assert mock_stop.call_count == 2


def test_main_recreates_broadcast_when_encoder_crashed(monkeypatch):
    """配信時間帯中にエンコーダがクラッシュ（encoder_is_alive が False）したら、一度停止して
    状態を戻し、次のポーリングサイクルで broadcast 作成からやり直すことを確認する。
    create_broadcast は enableAutoStop=True なので、クラッシュ検知までの間に broadcast が
    complete になっていることがあり、同じ broadcast のまま encoder だけ再起動すると
    live な broadcast の無い stream へ送り続けてしまう。
    """
    _set_required_env(monkeypatch)

    now = _dt(23, 0)  # 常に夜間・同一セグメント内になる時刻に固定
    sunset = _dt(19, 0)
    sunrise = _dt(5, 0, day=2)

    rpicam_proc = MagicMock()
    ffmpeg_proc = MagicMock()

    original_sigterm_handler = signal.getsignal(signal.SIGTERM)

    with patch.object(
             stream_scheduler, "current_night_sun_times", return_value=(sunset, sunrise)
         ), \
         patch("stream_scheduler.datetime.datetime") as mock_datetime_cls, \
         patch.object(stream_scheduler.youtube_broadcast, "build_youtube_client", return_value=MagicMock()), \
         patch.object(stream_scheduler.youtube_broadcast, "find_stream_id", return_value="stream-1"), \
         patch.object(stream_scheduler.youtube_broadcast, "create_broadcast", return_value="bcast-1") as mock_create, \
         patch.object(stream_scheduler.youtube_broadcast, "wait_for_live", return_value=True), \
         patch.object(stream_scheduler, "start_encoder", return_value=(rpicam_proc, ffmpeg_proc)) as mock_start, \
         patch.object(stream_scheduler, "stop_encoder") as mock_stop, \
         patch.object(stream_scheduler, "encoder_is_alive", side_effect=[False]) as mock_alive, \
         patch.object(stream_scheduler.time, "sleep", _sleep_n_times_then_sigterm(3)):

        mock_datetime_cls.now.return_value = now

        try:
            with pytest.raises(SystemExit):
                stream_scheduler.main()
        finally:
            signal.signal(signal.SIGTERM, original_sigterm_handler)

    mock_alive.assert_called_once()
    # 初回 + クラッシュ後の次サイクルで broadcast 作成からやり直すので、どちらも2回。
    assert mock_create.call_count == 2
    assert mock_start.call_count == 2
    # クラッシュ検知時の停止（生き残った片方の後始末）+ SIGTERM 後の finally で計2回。
    assert mock_stop.call_count == 2


def test_main_stops_encoder_when_leaving_active_window(monkeypatch):
    """配信時間帯（アクティブセグメント）を外れたら、稼働中のエンコーダを停止することを
    確認する。1回目は夜間で起動し、2回目は日中（時間帯外）になって停止し、3回目に
    再び夜間へ戻って broadcast が作り直されることまで確認する。

    2回目で SIGTERM すると、正しい実装（外れたら止めて状態をリセットする）と誤った
    実装（何もせず古いエンコーダを握ったまま）のどちらも finally で1回だけ
    stop_encoder を呼ぶ形になり区別が付かない。3回目まで進め、再びアクティブに
    戻ったときに broadcast が実際に作り直された（create_broadcast / start_encoder が
    2回目呼ばれた）ことまで観測して初めて区別できる。
    """
    _set_required_env(monkeypatch)

    now_active = _dt(23, 0)  # 夜間（アクティブ）
    now_outside = _dt(6, 0, day=2)  # 日の出後（時間帯外）
    now_active_again = _dt(23, 0, day=2)  # 翌晩、再びアクティブ

    # patch context に入ると stream_scheduler.datetime（datetime モジュールそのもの）の
    # datetime クラスが差し替わるため、_dt() を使った datetime 構築は patch の外で
    # 済ませておく（同じ理由が既存テストのコメントにもある）。
    night1_sunrise_boundary = _dt(5, 0, day=2)
    night1 = (_dt(19, 0), _dt(5, 0, day=2))
    night2 = (_dt(19, 0, day=2), _dt(5, 0, day=3))

    rpicam_proc = MagicMock()
    ffmpeg_proc = MagicMock()

    original_sigterm_handler = signal.getsignal(signal.SIGTERM)

    def _fake_current_night_sun_times(latitude, longitude, tz, now):
        # 実装と同じ「now を含む夜」の判定を模す: 6:00(日の出後)は当夜(day1)に含まれず、
        # 23:00 はどちらの日も当夜のセグメントを作る。
        if now < night1_sunrise_boundary:
            return night1
        return night2

    with patch.object(
             stream_scheduler,
             "current_night_sun_times",
             side_effect=_fake_current_night_sun_times,
         ), \
         patch("stream_scheduler.datetime.datetime") as mock_datetime_cls, \
         patch.object(stream_scheduler.youtube_broadcast, "build_youtube_client", return_value=MagicMock()), \
         patch.object(stream_scheduler.youtube_broadcast, "find_stream_id", return_value="stream-1"), \
         patch.object(stream_scheduler.youtube_broadcast, "create_broadcast", return_value="bcast-1") as mock_create, \
         patch.object(stream_scheduler.youtube_broadcast, "wait_for_live", return_value=True), \
         patch.object(stream_scheduler, "start_encoder", return_value=(rpicam_proc, ffmpeg_proc)) as mock_start, \
         patch.object(stream_scheduler, "stop_encoder") as mock_stop, \
         patch.object(stream_scheduler, "encoder_is_alive", return_value=True), \
         patch.object(stream_scheduler.time, "sleep", _sleep_n_times_then_sigterm(3)):

        mock_datetime_cls.now.side_effect = [now_active, now_outside, now_active_again]

        try:
            with pytest.raises(SystemExit):
                stream_scheduler.main()
        finally:
            signal.signal(signal.SIGTERM, original_sigterm_handler)

    # 1回目のアクティブ + 時間帯外を経て翌晩再びアクティブになったときの、計2回。
    assert mock_create.call_count == 2
    assert mock_start.call_count == 2
    # 時間帯外に出たときの停止 + SIGTERM 後の finally での後始末で計2回。
    assert mock_stop.call_count == 2


def test_main_stops_at_segment_boundary_without_immediate_restart(monkeypatch):
    """セグメント境界（分割点）をまたいだら一度停止し、同じポーリングサイクル内では
    再起動せず、次のサイクルで新しいセグメントとして再作成されることを確認する。
    MAX_SEGMENT_HOURS を短くして夜を2セグメントに分割し、境界（0時）をまたがせる。

    境界をまたいだ直後に SIGTERM すると、正しい実装（一度止めて次サイクルで再作成）と
    誤った実装（何もせず古いエンコーダを握ったまま）のどちらも finally で1回だけ
    stop_encoder を呼ぶ形になり区別が付かない。区別するには境界をまたいだサイクルの
    "次" のサイクルまで進め、broadcast が実際に作り直された（create_broadcast /
    start_encoder が2回目呼ばれた）ことまで観測する必要がある。
    """
    _set_required_env(monkeypatch)
    monkeypatch.setenv("MAX_SEGMENT_HOURS", "5")  # 10時間の夜 -> 5時間ずつ2分割

    now_segment1 = _dt(23, 0)  # 1つ目のセグメント（19:00〜0:00）内
    now_segment2_boundary = _dt(0, 30, day=2)  # 2つ目のセグメント内、境界をまたいだ直後
    now_segment2_after_restart = _dt(0, 31, day=2)  # 同じセグメント内、再作成後
    sunset = _dt(19, 0)
    sunrise = _dt(5, 0, day=2)

    rpicam_proc = MagicMock()
    ffmpeg_proc = MagicMock()

    original_sigterm_handler = signal.getsignal(signal.SIGTERM)

    with patch.object(
             stream_scheduler, "current_night_sun_times", return_value=(sunset, sunrise)
         ), \
         patch("stream_scheduler.datetime.datetime") as mock_datetime_cls, \
         patch.object(stream_scheduler.youtube_broadcast, "build_youtube_client", return_value=MagicMock()), \
         patch.object(stream_scheduler.youtube_broadcast, "find_stream_id", return_value="stream-1"), \
         patch.object(stream_scheduler.youtube_broadcast, "create_broadcast", return_value="bcast-1") as mock_create, \
         patch.object(stream_scheduler.youtube_broadcast, "wait_for_live", return_value=True), \
         patch.object(stream_scheduler, "start_encoder", return_value=(rpicam_proc, ffmpeg_proc)) as mock_start, \
         patch.object(stream_scheduler, "stop_encoder") as mock_stop, \
         patch.object(stream_scheduler, "encoder_is_alive", return_value=True), \
         patch.object(stream_scheduler.time, "sleep", _sleep_n_times_then_sigterm(3)):

        mock_datetime_cls.now.side_effect = [
            now_segment1,
            now_segment2_boundary,
            now_segment2_after_restart,
        ]

        try:
            with pytest.raises(SystemExit):
                stream_scheduler.main()
        finally:
            signal.signal(signal.SIGTERM, original_sigterm_handler)

    # セグメント1で1回、境界をまたいで停止した後の次サイクルで1回、計2回作り直す。
    assert mock_create.call_count == 2
    assert mock_start.call_count == 2
    # 境界をまたいだときの停止 + SIGTERM 後の finally での後始末で計2回。
    assert mock_stop.call_count == 2


def _recording_sleep_then_sigterm(n, recorded):
    """time.sleep の差し替え用: 渡された秒数を recorded に記録し、n 回目の呼び出しで SIGTERM を配送する。"""

    def _sleep(seconds):
        recorded.append(seconds)
        if len(recorded) >= n:
            handler = signal.getsignal(signal.SIGTERM)
            handler(signal.SIGTERM, None)

    return _sleep


def test_main_backs_off_exponentially_after_consecutive_failures(monkeypatch):
    """create_broadcast が失敗し続けたら、待機間隔が POLL_INTERVAL_SECONDS を起点に
    倍々で伸び、MAX_BACKOFF_SECONDS で頭打ちになることを確認する。
    レート制限・1日あたりの配信開始数上限に当たったとき、毎分 API を叩き続けて
    状況を悪化させた実インシデントへのガード。固定2段階（一定回数で突然30分）の
    崖ではなく、連続失敗数に応じて滑らかに伸びることまで見る。
    """
    _set_required_env(monkeypatch)

    now = _dt(23, 0)
    sunset = _dt(19, 0)
    sunrise = _dt(5, 0, day=2)

    # k 回目の失敗直後の待機は 60 * 2**k を 1800 で頭打ちにしたもの。
    expected = [120, 240, 480, 960, 1800, 1800, 1800]
    recorded = []

    original_sigterm_handler = signal.getsignal(signal.SIGTERM)

    with patch.object(
             stream_scheduler, "current_night_sun_times", return_value=(sunset, sunrise)
         ), \
         patch("stream_scheduler.datetime.datetime") as mock_datetime_cls, \
         patch.object(stream_scheduler.youtube_broadcast, "build_youtube_client", return_value=MagicMock()), \
         patch.object(stream_scheduler.youtube_broadcast, "find_stream_id", return_value="stream-1"), \
         patch.object(
             stream_scheduler.youtube_broadcast,
             "create_broadcast",
             side_effect=RuntimeError("quota exceeded"),
         ) as mock_create, \
         patch.object(stream_scheduler, "start_encoder") as mock_start, \
         patch.object(stream_scheduler, "stop_encoder"), \
         patch.object(
             stream_scheduler.time, "sleep", _recording_sleep_then_sigterm(len(expected), recorded)
         ):

        mock_datetime_cls.now.return_value = now

        try:
            with pytest.raises(SystemExit):
                stream_scheduler.main()
        finally:
            signal.signal(signal.SIGTERM, original_sigterm_handler)

    assert mock_create.call_count == len(expected)
    mock_start.assert_not_called()
    assert recorded == expected


def test_main_resets_failure_count_after_successful_live(monkeypatch):
    """live に到達したら連続失敗数を 0 に戻し、その後の待機が指数バックオフの起点
    （POLL_INTERVAL_SECONDS）に戻ることを確認する。

    シナリオ: 3 回失敗 → 成功して live → encoder クラッシュで作り直し → 再び失敗が続く。
    リセットしない実装だと live 成功後も 480 秒待ち（encoder クラッシュ検知後も
    同じ）、再失敗も 4 回目の間隔（960 秒）から始まってしまう。再失敗の間隔が 120 秒から倍々で伸び直すことまで見て、
    カウント自体は再開していることも確認する。
    """
    _set_required_env(monkeypatch)

    now = _dt(23, 0)
    sunset = _dt(19, 0)
    sunrise = _dt(5, 0, day=2)

    create_side_effect = (
        [RuntimeError("quota exceeded")] * 3
        + ["bcast-1"]
        + [RuntimeError("quota exceeded")] * 3
    )
    expected = (
        [120, 240, 480]  # 最初の失敗: 倍々で伸びる
        + [60]  # live 成功（ここで 0 にリセットされ起点に戻る）
        + [60]  # encoder クラッシュ検知（失敗には数えない）
        + [120, 240, 480]  # 再失敗: リセット後なので起点から伸び直す
    )
    recorded = []

    rpicam_proc = MagicMock()
    ffmpeg_proc = MagicMock()

    original_sigterm_handler = signal.getsignal(signal.SIGTERM)

    with patch.object(
             stream_scheduler, "current_night_sun_times", return_value=(sunset, sunrise)
         ), \
         patch("stream_scheduler.datetime.datetime") as mock_datetime_cls, \
         patch.object(stream_scheduler.youtube_broadcast, "build_youtube_client", return_value=MagicMock()), \
         patch.object(stream_scheduler.youtube_broadcast, "find_stream_id", return_value="stream-1"), \
         patch.object(
             stream_scheduler.youtube_broadcast,
             "create_broadcast",
             side_effect=create_side_effect,
         ) as mock_create, \
         patch.object(stream_scheduler.youtube_broadcast, "wait_for_live", return_value=True), \
         patch.object(stream_scheduler, "start_encoder", return_value=(rpicam_proc, ffmpeg_proc)) as mock_start, \
         patch.object(stream_scheduler, "stop_encoder"), \
         patch.object(stream_scheduler, "encoder_is_alive", side_effect=[False]), \
         patch.object(
             stream_scheduler.time, "sleep", _recording_sleep_then_sigterm(len(expected), recorded)
         ):

        mock_datetime_cls.now.return_value = now

        try:
            with pytest.raises(SystemExit):
                stream_scheduler.main()
        finally:
            signal.signal(signal.SIGTERM, original_sigterm_handler)

    assert mock_create.call_count == len(create_side_effect)
    mock_start.assert_called_once()
    assert recorded == expected


def test_main_resets_failure_count_when_leaving_active_segment(monkeypatch):
    """夜間に失敗が続いたまま時間帯外（日中）に出たら連続失敗数を 0 に戻し、日中の
    待機が POLL_INTERVAL_SECONDS になる（バックオフしない）ことを確認する。

    実機で、夜間に一度も live にならず consecutive_failures が残ったまま日の出を
    迎え、日中は何も試行していないのに「N回連続で失敗したためバックオフします」が
    出続けたインシデントへのガード。リセットしない実装だと日中の待機が 480 秒のまま
    になる。
    """
    _set_required_env(monkeypatch)

    now_active = _dt(23, 0)  # 夜間（アクティブ）
    now_outside = _dt(6, 0, day=2)  # 日の出後（時間帯外）
    sunset = _dt(19, 0)
    sunrise = _dt(5, 0, day=2)

    expected = (
        [120, 240, 480]  # 夜間の失敗: 倍々で伸びる
        + [60, 60]  # 日中: 何も試行しないのでバックオフしない
    )
    recorded = []

    original_sigterm_handler = signal.getsignal(signal.SIGTERM)

    with patch.object(
             stream_scheduler, "current_night_sun_times", return_value=(sunset, sunrise)
         ), \
         patch("stream_scheduler.datetime.datetime") as mock_datetime_cls, \
         patch.object(stream_scheduler.youtube_broadcast, "build_youtube_client", return_value=MagicMock()), \
         patch.object(stream_scheduler.youtube_broadcast, "find_stream_id", return_value="stream-1"), \
         patch.object(
             stream_scheduler.youtube_broadcast,
             "create_broadcast",
             side_effect=RuntimeError("quota exceeded"),
         ) as mock_create, \
         patch.object(stream_scheduler, "start_encoder") as mock_start, \
         patch.object(stream_scheduler, "stop_encoder"), \
         patch.object(
             stream_scheduler.time, "sleep", _recording_sleep_then_sigterm(len(expected), recorded)
         ):

        mock_datetime_cls.now.side_effect = [now_active] * 3 + [now_outside] * 2

        try:
            with pytest.raises(SystemExit):
                stream_scheduler.main()
        finally:
            signal.signal(signal.SIGTERM, original_sigterm_handler)

    assert mock_create.call_count == 3
    mock_start.assert_not_called()
    assert recorded == expected


def _run_main_in_active_segment(wait_for_live_side_effect, sleep_fn, extra_patches=()):
    """常に夜間（アクティブなセグメント内）で main() を回し、SIGTERM で抜けるまで実行する。

    create_broadcast は呼ばれるたびに bcast-1, bcast-2, ... を返す。
    """
    now = _dt(23, 0)
    sunset = _dt(19, 0)
    sunrise = _dt(5, 0, day=2)
    broadcast_ids = (f"bcast-{i}" for i in range(1, 100))

    original_sigterm_handler = signal.getsignal(signal.SIGTERM)
    with patch.object(
             stream_scheduler, "current_night_sun_times", return_value=(sunset, sunrise)
         ), \
         patch("stream_scheduler.datetime.datetime") as mock_datetime_cls, \
         patch.object(stream_scheduler.youtube_broadcast, "build_youtube_client", return_value=MagicMock()), \
         patch.object(stream_scheduler.youtube_broadcast, "find_stream_id", return_value="stream-1"), \
         patch.object(
             stream_scheduler.youtube_broadcast,
             "create_broadcast",
             side_effect=lambda *a, **k: next(broadcast_ids),
         ), \
         patch.object(
             stream_scheduler.youtube_broadcast,
             "wait_for_live",
             side_effect=wait_for_live_side_effect,
         ), \
         patch.object(stream_scheduler, "start_encoder", return_value=(MagicMock(), MagicMock())), \
         patch.object(stream_scheduler, "stop_encoder"), \
         patch.object(stream_scheduler, "encoder_is_alive", return_value=True), \
         patch.object(stream_scheduler.time, "sleep", sleep_fn):
        mock_datetime_cls.now.return_value = now
        try:
            with pytest.raises(SystemExit):
                stream_scheduler.main()
        finally:
            signal.signal(signal.SIGTERM, original_sigterm_handler)


def test_main_deletes_broadcast_when_wait_for_live_times_out(monkeypatch):
    """wait_for_live が timeout (False) を返したら、作った broadcast を削除してから諦める。

    同じ persistent stream に bind された broadcast はキューとして積まれる。放棄した
    broadcast を残すと、次に作った broadcast はキューの前の分が消費されるまで live に
    ならず、また timeout して新しい broadcast を作る自己増殖ループに陥る（実機で観測:
    どの broadcast もきっかり 600 秒で終了し、作成からライブ開始まで約33分ずれていた）。
    """
    _set_required_env(monkeypatch)

    with patch.object(stream_scheduler.youtube_broadcast, "delete_broadcast") as mock_delete:
        _run_main_in_active_segment([False, True], _sleep_n_times_then_sigterm(2))

    # 1回目の bcast-1 だけが timeout で放棄されるので削除される。2回目の bcast-2 は
    # live になっているので削除しない。
    mock_delete.assert_called_once()
    assert mock_delete.call_args.args[1] == "bcast-1"


def test_main_deletes_broadcast_and_propagates_when_wait_for_live_raises(monkeypatch, capsys):
    """wait_for_live が例外を投げたときも作った broadcast を削除し、元の例外は外側の
    except まで伝播して（ログに残って）次のサイクルで再試行される。"""
    _set_required_env(monkeypatch)

    with patch.object(stream_scheduler.youtube_broadcast, "delete_broadcast") as mock_delete:
        _run_main_in_active_segment(
            [RuntimeError("wait_for_live network error"), True],
            _sleep_n_times_then_sigterm(2),
        )

    mock_delete.assert_called_once()
    assert mock_delete.call_args.args[1] == "bcast-1"
    assert "wait_for_live network error" in capsys.readouterr().err


def test_main_keeps_original_error_when_delete_after_wait_for_live_error_fails(
    monkeypatch, capsys
):
    """wait_for_live の例外後の削除自体が失敗しても、元の例外を潰さない。
    削除の失敗はログに残し、外側へは元の例外が伝播する。"""
    _set_required_env(monkeypatch)

    with patch.object(
        stream_scheduler.youtube_broadcast,
        "delete_broadcast",
        side_effect=RuntimeError("delete failed"),
    ) as mock_delete:
        _run_main_in_active_segment(
            [RuntimeError("wait_for_live network error")],
            _sleep_n_times_then_sigterm(1),
        )

    mock_delete.assert_called_once()
    err = capsys.readouterr().err
    assert "delete failed" in err
    # 外側の except がログするのは元の例外（削除の例外に置き換わっていない）。
    assert "error handling segment" in err
    error_line = next(l for l in err.splitlines() if "error handling segment" in l)
    assert "wait_for_live network error" in error_line


def test_main_deletes_stale_pending_broadcasts_before_creating(monkeypatch):
    """broadcast 作成前に、前回の異常終了等で取り残された pending broadcast を削除する。
    残骸がキューに残ると、新しく作る broadcast が live になるまで古い分の消費を待つことになる。
    """
    _set_required_env(monkeypatch)

    with patch.object(
             stream_scheduler.youtube_broadcast,
             "list_pending_broadcast_ids",
             return_value=["stale-1", "stale-2"],
         ) as mock_list, \
         patch.object(stream_scheduler.youtube_broadcast, "delete_broadcast") as mock_delete:
        deleted_before_wait = []

        def _wait(*args, **kwargs):
            # wait_for_live は create_broadcast の後に呼ばれる。この時点までに残骸の削除が
            # 済んでいることで「作成前に掃除した」ことを確認する。
            deleted_before_wait.extend(c.args[1] for c in mock_delete.call_args_list)
            return True

        _run_main_in_active_segment(_wait, _sleep_n_times_then_sigterm(1))

    mock_list.assert_called_once()
    assert deleted_before_wait == ["stale-1", "stale-2"]
    assert [c.args[1] for c in mock_delete.call_args_list] == ["stale-1", "stale-2"]
