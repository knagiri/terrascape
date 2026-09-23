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
├── requirements.txt # Python 依存パッケージ（IR ライトデーモン用）
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

## 配信

```bash
chmod 600 .env
sudo systemctl enable --now terrascape-stream
```

`.env` の `YOUTUBE_STREAM_KEY` に YouTube Studio で取得したストリームキーを設定してから
有効化すること。ストリームキーを含むので、`.env` は `chmod 600` で本人以外から読めないようにしておく。
ログは `journalctl -u terrascape-stream -f` で確認できる。`scripts/stream.sh` は ffmpeg のログレベルを
`warning` に抑えて配信先 URL（ストリームキー入り）が journal に残らないようにしているが、
接続エラー時の warning/error ログや `ps` での起動コマンド確認では URL が見えうるため、
journal の共有・貼り付けは引き続き避けること。

## IR ライト自動点灯

日没から日の出まで、940nm 赤外線 LED を GPIO18 経由で PWM 点灯させる常駐サービス。回路の詳細は
`docs/hardware/ir-light-circuit.md` を参照。

### セットアップ

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`.env` に `IR_LIGHT_LATITUDE` / `IR_LIGHT_LONGITUDE` / `IR_LIGHT_TIMEZONE`（設置場所の緯度・経度・
タイムゾーン）を設定する。`IR_LIGHT_BRIGHTNESS`（0.0〜1.0のPWM duty cycle）は暫定値であり、
実機でカメラの映りと生体への影響を見ながら調整して決める。

```bash
sudo systemctl enable --now terrascape-ir-light
```

ログは `journalctl -u terrascape-ir-light -f` で確認できる。

`gpiozero` が実機の GPIO バックエンド（`lgpio` 等）を自動検出できない環境では、
`.venv/bin/pip install lgpio` 等の追加インストールが必要になる場合がある。
