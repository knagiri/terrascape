# Terrascape

Raspberry Pi とカメラで、テラリウムの様子を配信・記録するためのソフトウェア。

## 構成
Raspberry Pi 4 Model B
Raspberry Pi Camera Module 3 NoIR Wide
940nm 赤外線 LED ライト（夜間撮影用）

## 配信

```bash
cp .env.example .env
sudo systemctl enable --now terrascape-stream
```

`.env` の `YOUTUBE_STREAM_KEY` に YouTube Studio で取得したストリームキーを設定してから
有効化すること。ログは `journalctl -u terrascape-stream -f` で確認できる。
