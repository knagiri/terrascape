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
