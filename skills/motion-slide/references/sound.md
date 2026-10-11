# 効果音と BGM

> 経緯: 2026-10-06。音楽を生成するモデルを手元で動かす案は取り下げ、**MIDI で書いて macOS 内蔵の音源で鳴らす**に決めた。

## 効果音（何もしなくても鳴る）

エンジンが Web Audio で合成する（音のファイルを持たない・HTML は増えない）。鳴る所は [`authoring.md`](authoring.md) の「拍で付け外しする属性」。
フックからは `x.sfx("ping")`。種類: pop（出る）・ping（照らす）・whoosh（場面・飛ぶ）・swish（線）・pen（手書きの丸）・buzz（否定）・tick（数え上げ）・tap（拍）。
`gap`（ms）で連打を間引く。ブラウザの決まりで、**最初のクリックかキーの後**から鳴る。
鳴らした音は `MS.state.sfx` に `{n: 名前, t: performance.now()}` で残る。`MS.renderSfx(名前)` はその音を 44.1kHz の音のデータで返す（動画に重ねるとき、ページと同じ音を時刻どおりに並べる）。

## BGM（任意）

自動再生（P）のときだけ流れる。手で進めるときは鳴らない。曲は**自動再生の時刻表に合わせて書く**ので、拍の山と曲の山が合う。
手で飛ばしたり戻したりしても、エンジンが曲の位置を拍に合わせ直す（1.2 秒以上ずれたら）。

### 作り方

1. 時刻表を出す: `python3 -I scripts/timeline.py <deck.html>` → 拍ごとの開始の秒・終わりの秒
2. `<出力先>/bgm/compose.py` を書く（Claude が作曲する。`scripts/bgm/midi.py` の `Song` を使う）
   - 山・抜け・アクセントを、時刻表の秒に置く（例: 結論の拍でティンパニ、数え上げの拍で細かい音、まとめで主題）
   - 楽器は General MIDI の番号（0 ピアノ・8 セレスタ・89 パッド・35 ベース・46 ハープ・47 ティンパニ・55 オーケストラヒット・ch9 は打楽器）
   - 書き方の見本は `scripts/bgm/midi.py` の `Song` の使い方（`tests/run.sh` の MIDI の試験に最小の例がある）
3. `python3 -I <出力先>/bgm/compose.py <出力先>/bgm` → `events.json` と `song.mid`
4. `bash scripts/bgm/render.sh <出力先>/bgm` → `bgm.m4a`（音量 -20〜-18 LUFS が目安。小さければ第2引数で上げ幅 dB を足す）
5. `build.py <deck.html> --bgm <出力先>/bgm/bgm.m4a`

### 知っておくこと

| | |
|---|---|
| **Claude は音を直接出せない** | 書けるのは音符（MIDI）とコード。鳴らすのは手元の音源 |
| **大きさ** | 2分の曲で m4a が約 2MB。base64 で HTML に入るので、HTML は約 2.8MB になる。MIDI 自体は数KB |
| **音色** | Apple の DLS 音源（General MIDI）は少し古い音。良くしたいならサウンドフォント（要ダウンロード・入れる前に確認） |
| **macOS 専用** | `render.swift` は AVAudioEngine の DLS シンセを使う。scan-server（Linux）では鳴らせない |
| **loudnorm を使わない** | 強弱が潰れた（実測）。`volume` と `alimiter` で合わせる |
