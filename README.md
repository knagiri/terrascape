# Terrascape

Raspberry Pi とカメラで、テラリウムの様子を配信・記録するためのソフトウェア。

## 構成（ハードウェア）
Raspberry Pi 4 Model B
Raspberry Pi Camera Module 3 NoIR Wide
940nm 赤外線 LED ライト（夜間撮影用）

## リポジトリ構成

```
terrascape/
├── README.md
├── .gitignore
├── .env.example    # .env の雛形。コピーして値を埋める
├── requirements.txt # Python 依存パッケージ（IR ライトデーモン・配信スケジューラ用）
├── scripts/        # 実行スクリプト
└── systemd/        # systemd unit ファイル
```

## セットアップ（Raspberry Pi 上）

```bash
git clone <repo-url> ~/terrascape
cd ~/terrascape
cp .env.example .env
# .env を編集して必要な値を埋める
sudo ln -s ~/terrascape/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
```

各サービスの有効化方法はサービスごとの節を参照。

## 配信（日没〜日の出のスケジュール配信）

```bash
chmod 600 .env
python3 -m venv --system-site-packages .venv   # IR ライトと共通の venv（作成済みなら不要）
.venv/bin/pip install -r requirements.txt
sudo systemctl enable --now terrascape-stream-scheduler
```

`.env` に以下を設定してから有効化すること。

- `YOUTUBE_STREAM_KEY`: YouTube Studio で取得したストリームキー
- `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` / `YOUTUBE_REFRESH_TOKEN`: 下記「YouTube Data API の
  OAuth 初回セットアップ」で一度だけ発行する
- `IR_LIGHT_LATITUDE` / `IR_LIGHT_LONGITUDE` / `IR_LIGHT_TIMEZONE`: 日没・日の出の計算に IR ライトと
  同じ設置場所の値を使う

日没〜日の出の間だけ自動的に配信を開始・終了する。`MAX_SEGMENT_HOURS`（既定10時間）を
超える夜は、均等な長さの複数本の配信に自動分割される。

ストリームキーや OAuth の秘密情報を含むので、`.env` は `chmod 600` で本人以外から読めないようにしておく。
ログは `journalctl -u terrascape-stream-scheduler -f` で確認できる。スケジューラは ffmpeg のログレベルを
`warning` に抑えて配信先 URL（ストリームキー入り）が journal に残らないようにしているが、
接続エラー時の warning/error ログや `ps` での起動コマンド確認では URL が見えうるため、
journal の共有・貼り付けは引き続き避けること。

手動での短時間テスト配信には、スケジューラを介さず `scripts/stream.sh` を直接実行できる
（`.env` を読み込んだ上で `./scripts/stream.sh` を実行する）。

以前の常時配信 unit（`terrascape-stream`）で運用していた環境では、有効化の前に旧 unit を止めて
リンクを外し、新しい unit をリンクしておく（新規セットアップでは「セットアップ」節のリンクで足りる）:

```bash
sudo systemctl disable --now terrascape-stream
sudo rm /etc/systemd/system/terrascape-stream.service
sudo ln -s ~/terrascape/systemd/terrascape-stream-scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### YouTube Data API の OAuth 初回セットアップ

配信スケジューラが YouTube Data API で broadcast を作成するための refresh token を一度だけ発行する。
Google Cloud Console で YouTube Data API v3 を有効化し、OAuth クライアント（種類: デスクトップアプリ）を
作成してから、ブラウザを開けるマシン（Pi である必要はない）で実行する。

```bash
python3 -m venv /tmp/oauth-venv && /tmp/oauth-venv/bin/pip install google-auth-oauthlib
/tmp/oauth-venv/bin/python scripts/youtube_oauth_setup.py
```

client id / client secret は起動後に対話入力する（環境変数 `YOUTUBE_CLIENT_ID` /
`YOUTUBE_CLIENT_SECRET` があればそれを使う）。client secret が shell history や `ps` に残らないよう、
コマンドライン引数では渡さない。ブラウザでの認可後に表示される refresh token を、client id /
client secret と合わせて `.env` の `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET` /
`YOUTUBE_REFRESH_TOKEN` に設定する。

## IR ライト自動点灯

日没から日の出まで、940nm 赤外線 LED を GPIO18 経由で PWM 点灯させる常駐サービス。回路の詳細は
`docs/hardware/ir-light-circuit.md` を参照。

### セットアップ

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -r requirements.txt
```

`--system-site-packages` が必須。Raspberry Pi OS は GPIO の PWM 制御に使う `lgpio` バックエンドを
`python3-lgpio`（apt パッケージ）としてシステム Python 側に用意しており、通常の venv だとこれを
継承できず `gpiozero` が PWM 非対応の `NativeFactory` にフォールバックして `PWMLED` の生成時に
`PinPWMUnsupported` 例外になる（実機で確認済み）。PyPI の `lgpio` パッケージを venv 内に pip
install する方法は、Python 3.13 環境では C 拡張のビルドが失敗するため使えない。

`.env` に `IR_LIGHT_LATITUDE` / `IR_LIGHT_LONGITUDE` / `IR_LIGHT_TIMEZONE`（設置場所の緯度・経度・
タイムゾーン）を設定する。`IR_LIGHT_BRIGHTNESS`（0.0〜1.0のPWM duty cycle）は暫定値であり、
実機でカメラの映りと生体への影響を見ながら調整して決める。

```bash
sudo systemctl enable --now terrascape-ir-light
```

ログは `journalctl -u terrascape-ir-light -f` で確認できる。
