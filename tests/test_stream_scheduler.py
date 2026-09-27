import datetime
import zoneinfo

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
