from unittest.mock import MagicMock

import youtube_broadcast


def _make_youtube_mock():
    return MagicMock()


def test_find_stream_id_matches_stream_key():
    youtube = _make_youtube_mock()
    youtube.liveStreams().list.return_value.execute.return_value = {
        "items": [
            {"id": "stream-1", "cdn": {"ingestionInfo": {"streamName": "other-key"}}},
            {"id": "stream-2", "cdn": {"ingestionInfo": {"streamName": "my-key"}}},
        ]
    }
    youtube.liveStreams().list_next.return_value = None

    result = youtube_broadcast.find_stream_id(youtube, "my-key")

    assert result == "stream-2"


def test_find_stream_id_returns_none_when_not_found():
    youtube = _make_youtube_mock()
    youtube.liveStreams().list.return_value.execute.return_value = {"items": []}
    youtube.liveStreams().list_next.return_value = None

    result = youtube_broadcast.find_stream_id(youtube, "missing-key")

    assert result is None


def test_create_broadcast_inserts_and_binds():
    youtube = _make_youtube_mock()
    youtube.liveBroadcasts().insert.return_value.execute.return_value = {"id": "bcast-1"}

    broadcast_id = youtube_broadcast.create_broadcast(
        youtube, stream_id="stream-2", title="Terrascape Live", privacy_status="unlisted"
    )

    assert broadcast_id == "bcast-1"
    insert_kwargs = youtube.liveBroadcasts().insert.call_args.kwargs
    assert insert_kwargs["body"]["contentDetails"]["enableAutoStart"] is True
    assert insert_kwargs["body"]["contentDetails"]["enableAutoStop"] is True
    assert insert_kwargs["body"]["status"]["privacyStatus"] == "unlisted"

    youtube.liveBroadcasts().bind.assert_called_with(
        id="bcast-1", part="id", streamId="stream-2"
    )
    # enableAutoStart=True の broadcast は stream が active になると YouTube 側が
    # 自動で live へ遷移させるので、手動の transition は呼ばない（呼ぶとこの自動遷移と
    # 衝突して invalidTransition エラーになる）。
    youtube.liveBroadcasts().transition.assert_not_called()


def test_get_lifecycle_status_returns_status_field():
    youtube = _make_youtube_mock()
    youtube.liveBroadcasts().list.return_value.execute.return_value = {
        "items": [{"status": {"lifeCycleStatus": "live"}}]
    }

    result = youtube_broadcast.get_lifecycle_status(youtube, "bcast-1")

    assert result == "live"


def test_wait_for_live_returns_true_once_live():
    youtube = _make_youtube_mock()
    youtube.liveBroadcasts().list.return_value.execute.side_effect = [
        {"items": [{"status": {"lifeCycleStatus": s}}]} for s in ["ready", "liveStarting", "live"]
    ]
    sleep_calls = []

    result = youtube_broadcast.wait_for_live(
        youtube, "bcast-1", timeout_seconds=100, poll_interval_seconds=1,
        sleep_fn=sleep_calls.append,
    )

    assert result is True
    assert len(sleep_calls) == 2  # live になる前に2回ポーリング待機した


def test_wait_for_live_returns_false_on_timeout(monkeypatch):
    youtube = _make_youtube_mock()
    youtube.liveBroadcasts().list.return_value.execute.return_value = {
        "items": [{"status": {"lifeCycleStatus": "ready"}}]
    }
    call_count = {"n": 0}

    def fake_sleep(_seconds):
        call_count["n"] += 1
        if call_count["n"] > 5:
            raise AssertionError("timeout で止まらずポーリングし続けている")

    times = iter([0, 1, 2, 3, 200])  # 4回目のチェックで timeout_seconds=100 を超える

    def fake_monotonic():
        return next(times, 999)

    # モジュールが参照する time.monotonic だけを差し替える。monkeypatch なら
    # テスト終了時に自動で元に戻るため、例外発生時の復元漏れが起きない。
    monkeypatch.setattr(youtube_broadcast.time, "monotonic", fake_monotonic)

    result = youtube_broadcast.wait_for_live(
        youtube, "bcast-1", timeout_seconds=100, poll_interval_seconds=1,
        sleep_fn=fake_sleep,
    )

    assert result is False


def test_delete_broadcast_deletes_by_id():
    youtube = _make_youtube_mock()

    youtube_broadcast.delete_broadcast(youtube, "bcast-1")

    youtube.liveBroadcasts().delete.assert_called_once_with(id="bcast-1")
    youtube.liveBroadcasts().delete.return_value.execute.assert_called_once()


def test_list_pending_broadcast_ids_follows_pages_with_upcoming_filter():
    youtube = _make_youtube_mock()
    first_request = MagicMock()
    second_request = MagicMock()
    first_request.execute.return_value = {"items": [{"id": "old-1"}, {"id": "old-2"}]}
    second_request.execute.return_value = {"items": [{"id": "old-3"}]}
    youtube.liveBroadcasts().list.return_value = first_request
    youtube.liveBroadcasts().list_next.side_effect = [second_request, None]

    result = youtube_broadcast.list_pending_broadcast_ids(youtube)

    assert result == ["old-1", "old-2", "old-3"]
    # broadcastStatus と mine は同時指定できない（実機で incompatibleParameters になる）ので
    # mine は渡さない。
    youtube.liveBroadcasts().list.assert_called_once_with(
        part="id", broadcastStatus="upcoming"
    )
