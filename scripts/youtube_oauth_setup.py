#!/usr/bin/env python3
"""YouTube Data API の OAuth 認可を一度だけ手動で行い、refresh token を発行するツール。

ブラウザが開けるマシン（Pi である必要はない）で実行する。表示された refresh token を
対象機の .env の YOUTUBE_REFRESH_TOKEN に貼り付ける。
"""

import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube"]


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <client_id> <client_secret>", file=sys.stderr)
        sys.exit(1)

    client_id, client_secret = sys.argv[1], sys.argv[2]
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    # prompt="consent" を明示する: 同じ Google アカウント・同じ client で認可をやり直すと、
    # Google は既に付与済みとみなして refresh_token を返さないことがある
    # （access_type=offline は既定で付くが prompt はそれだけでは consent にならない）。
    # 常に consent 画面を強制することで refresh_token の取りこぼしを防ぐ。
    credentials = flow.run_local_server(port=0, prompt="consent")

    if not credentials.refresh_token:
        print(
            "エラー: refresh_token が取得できませんでした。"
            "Google アカウントの設定でこのアプリのアクセス権を一度取り消してから再実行してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    print("認可に成功しました。以下を .env の YOUTUBE_REFRESH_TOKEN に設定してください:")
    print(credentials.refresh_token)


if __name__ == "__main__":
    main()
