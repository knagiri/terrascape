# YouTube Live 配信のトラブルシューティング

`scripts/stream.sh`（`rpicam-vid` → `ffmpeg` → YouTube Live RTMP）で起きた配信不具合と、その修正の記録。

## 症状の全体像

症状は 2 段階で現れた。

1. RTMP 接続はできるが、YouTube Studio の Stream Health に何も反映されない
2. Stream Health は改善したが、YouTube Studio が「ストリーミングを準備しています」のまま「ライブ」に遷移しない

どの段階でも ffmpeg / `rpicam-vid` 側にエラーは出ず、TCP レベルでも正常に見えた。原因は 3 つあり、順に修正した。

## 1. 音声トラックが無い（[#9](https://github.com/knagiri/terrascape/pull/9)）

- **症状**: RTMP 接続は確立するが、Stream Health にデータが一切反映されない
- **原因**: `rpicam-vid` の映像だけを FLV に載せており、音声トラックが無かった。YouTube Live は映像のみの RTMP を接続はするが、配信としては成立させない。TCP レベルでは ACK され続けるのでエラーにならない
- **修正**: ffmpeg の 2 本目の入力に `anullsrc`（無音ソース）を加え、AAC で多重化する。実音声は不要だが無音トラック自体は必須。`anullsrc` は終わらない入力なので `-shortest` を併せて付け、映像が EOF になったら ffmpeg も終了させる（systemd の `Restart=always` を効かせるため）

## 2. 映像のタイムスタンプ未設定による A/V 同期ずれ（[#10](https://github.com/knagiri/terrascape/pull/10)）

- **症状**: Stream Health が「要改善」。journal に `Timestamps are unset in a packet for stream 0.` の警告
- **原因**: pipe で渡す raw H.264 にはコンテナのタイムスタンプが無い。wallclock 基準で生成される `anullsrc` の音声と mux する際、映像側のタイムスタンプが正しく付かず同期がずれていた
- **修正**: 映像入力に `-use_wallclock_as_timestamps 1` を付け、映像もウォールクロック基準でタイムスタンプを生成させる

## 3. keyframe 間隔が未指定（[#11](https://github.com/knagiri/terrascape/pull/11)）

- **症状**: ffmpeg にエラーは無く YouTube 側にもデータが届いているが、「準備中」のまま「ライブ」に進まない
- **原因**: YouTube Live は 2 秒ごとの keyframe を推奨しているが、keyframe 間隔を指定していなかった。`--intra` の既定値は公式ドキュメント（60 フレーム）と実機の `rpicam-vid --help`（`0`）で食い違っており、既定値には頼れない
- **修正**: `rpicam-vid` に `--intra "$((STREAM_FPS * 2))"` を付け、fps に依らず 2 秒間隔に固定する

## 教訓

- ffmpeg 側にエラーが出ず TCP レベルで正常に見えても、YouTube 側で配信が成立するとは限らない。ローカルのログだけでは原因にたどり着けない
- 切り分けには、実機での短時間テスト配信と YouTube Studio 側（Stream Health・配信状態）の目視確認が必要だった
- `scripts/stream.sh` の `anullsrc`・`-use_wallclock_as_timestamps 1`・`--intra`・`-shortest` は一見些細だが、いずれも必須。安易に削除・簡略化しない。変更した場合は上記の手順で実機確認する
