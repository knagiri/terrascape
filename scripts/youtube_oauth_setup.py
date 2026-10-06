#!/usr/bin/env python3
"""YouTube Data API の OAuth 認可を手動で行い、refresh token を発行するツール。

同意画面が「テスト中」の OAuth アプリでは refresh token が 7 日で失効するため、初回だけでなく
失効のたびに再実行する（手順は README を参照）。表示された refresh token を対象機の .env の
YOUTUBE_REFRESH_TOKEN に貼り付ける。

ブラウザは自動起動せず、認可 URL を標準出力に表示する。それを手元のブラウザで開いて認可する。
認可後のリダイレクト先はスクリプトが待ち受ける localhost:8765 なので、SSH 先など別マシンで
実行する場合は、先に手元から ssh -L 8765:localhost:8765 <user>@<host> でトンネルを張っておく。

client id / client secret は環境変数 YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET から読み、
未設定なら対話入力させる。コマンドライン引数で受け取らないのは、shell history や
ps から client secret が見えてしまうため。
"""

import getpass
import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube"]
# SSH 先などブラウザを開けない環境でも使えるよう、ポートを固定して
# ssh -L でトンネルできるようにする（port=0 のランダムポートだと事前にトンネルを張れない）。
LOCAL_SERVER_PORT = 8765


def main():
    if len(sys.argv) != 1:
        print(
            f"usage: {sys.argv[0]}\n"
            "  YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET を環境変数で渡すか、"
            "起動後の対話入力で指定する",
            file=sys.stderr,
        )
        sys.exit(1)

    client_id = os.environ.get("YOUTUBE_CLIENT_ID") or input("YOUTUBE_CLIENT_ID: ").strip()
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET") or getpass.getpass(
        "YOUTUBE_CLIENT_SECRET: "
    ).strip()
    if not client_id or not client_secret:
        print("エラー: client id と client secret の両方が必要です。", file=sys.stderr)
        sys.exit(1)

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
    # open_browser=False: ブラウザの自動起動を試みると、headless 環境では
    # webbrowser.Error（could not locate runnable browser）でクラッシュする。
    # 代わりに認可 URL を標準出力に表示させ、手元のブラウザで開いてもらう。
    credentials = flow.run_local_server(
        port=LOCAL_SERVER_PORT, prompt="consent", open_browser=False
    )

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
