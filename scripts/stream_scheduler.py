"""レオパの活動時間（日没〜日の出）に合わせて YouTube Live 配信を自動開始・終了する常駐デーモン。"""

import datetime
import math


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
