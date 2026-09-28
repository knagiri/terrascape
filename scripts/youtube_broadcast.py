"""YouTube Live broadcast のライフサイクル管理（作成・bind・testing遷移・live確認）。

認証済みの youtube クライアント（google-api-python-client の Resource オブジェクト）を
呼び出し側から受け取り、このモジュール自身は認証方法を知らない設計にする。
"""

import datetime
import time

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


def build_youtube_client(client_id, client_secret, refresh_token):
    """refresh token から認証済み YouTube Data API v3 クライアントを構築する。"""
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds)


def find_stream_id(youtube, stream_key):
    """persistent stream key に対応する liveStream リソースの id を返す。見つからなければ None。"""
    request = youtube.liveStreams().list(part="cdn", mine=True)
    while request is not None:
        response = request.execute()
        for item in response.get("items", []):
            if item["cdn"]["ingestionInfo"]["streamName"] == stream_key:
                return item["id"]
        request = youtube.liveStreams().list_next(request, response)
    return None


def create_broadcast(youtube, stream_id, title, privacy_status):
    """broadcast を作成し、stream に bind する。broadcast id を返す。

    testing への遷移はここでは行わない。YouTube API は bind した stream が active
    （エンコーダが実際にデータを送り始めている状態）でないと transition(testing) を
    拒否するため（実機で Invalid transition エラーを確認済み）、stream が active に
    なってから transition_to_testing() を別途呼び出すこと。
    """
    insert_response = (
        youtube.liveBroadcasts()
        .insert(
            part="snippet,contentDetails,status",
            body={
                "snippet": {
                    "title": title,
                    "scheduledStartTime": datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                },
                "contentDetails": {
                    "enableAutoStart": True,
                    "enableAutoStop": True,
                },
                "status": {
                    "privacyStatus": privacy_status,
                    "selfDeclaredMadeForKids": False,
                },
            },
        )
        .execute()
    )
    broadcast_id = insert_response["id"]

    youtube.liveBroadcasts().bind(id=broadcast_id, part="id", streamId=stream_id).execute()

    return broadcast_id


def transition_to_testing(youtube, broadcast_id):
    """broadcast を testing 状態へ遷移させる。呼び出し前に、bind した stream が
    active になっていることを wait_for_stream_active() 等で確認しておくこと。"""
    youtube.liveBroadcasts().transition(
        broadcastStatus="testing", id=broadcast_id, part="status"
    ).execute()


def get_stream_status(youtube, stream_id):
    """liveStream の現在の status.streamStatus を返す。"""
    response = youtube.liveStreams().list(part="status", id=stream_id).execute()
    items = response.get("items", [])
    if not items:
        raise ValueError(f"stream {stream_id} not found")
    return items[0]["status"]["streamStatus"]


def wait_for_stream_active(
    youtube, stream_id, timeout_seconds, poll_interval_seconds, sleep_fn=time.sleep
):
    """stream が active になるまでポーリングする。timeout で False を返す。"""
    deadline = time.monotonic() + timeout_seconds
    while True:
        if get_stream_status(youtube, stream_id) == "active":
            return True
        if time.monotonic() >= deadline:
            return False
        sleep_fn(poll_interval_seconds)


def get_lifecycle_status(youtube, broadcast_id):
    """broadcast の現在の status.lifeCycleStatus を返す。"""
    response = youtube.liveBroadcasts().list(part="status", id=broadcast_id).execute()
    items = response.get("items", [])
    if not items:
        raise ValueError(f"broadcast {broadcast_id} not found")
    return items[0]["status"]["lifeCycleStatus"]


def wait_for_live(
    youtube, broadcast_id, timeout_seconds, poll_interval_seconds, sleep_fn=time.sleep
):
    """broadcast が live になるまでポーリングする。timeout で False を返す。"""
    deadline = time.monotonic() + timeout_seconds
    while True:
        if get_lifecycle_status(youtube, broadcast_id) == "live":
            return True
        if time.monotonic() >= deadline:
            return False
        sleep_fn(poll_interval_seconds)
