"""YouTube Live broadcast のライフサイクル管理（作成・bind・live確認）。

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

    contentDetails.enableAutoStart=True を設定しているため、手動での
    liveBroadcasts.transition() 呼び出しは不要（呼ぶとエラーになる。実機で
    確認済み: enableAutoStart=true の broadcast は stream が active になった
    時点で YouTube 側が自動的に live へ遷移させ、手動 transition はこの
    自動遷移と衝突して invalidTransition エラーになる）。呼び出し側は
    wait_for_live() で lifeCycleStatus が live になるのを待てばよい。
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
