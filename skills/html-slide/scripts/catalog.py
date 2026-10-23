#!/usr/bin/env python3
"""レイアウトの一覧（カタログ）を作る。型と置き方を1枚ずつ組み、PNG に撮って名前を添えた一覧画像にする。

    python3 catalog.py --out <dir> [--only image|text|<型の名前>] [--theme <name|yaml>]

<dir>/catalog.json（一覧の台本）・<dir>/html/deck.html・<dir>/png/slide-NN.png・<dir>/catalog-01.png … を出す。
入口の html_slide.py からは build_catalog() を呼ぶ。中身の文字は短い架空のダミー（見出しは主張の形）。
画像は examples/images・examples/assets の絵を使う（全面の表紙と章扉は本物の s01-image.jpg）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

sys.dont_write_bytecode = True   # 置いた場所（配る物の中・ほかの環境からのリンク先）に __pycache__ を残さない
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import deck  # noqa: E402
import deck_art as ART  # noqa: E402
import deck_types as T  # noqa: E402
import outputs as OUT  # noqa: E402

SKILL = pathlib.Path(__file__).resolve().parent.parent
IMG = SKILL / "examples" / "images"
ASSET = SKILL / "examples" / "assets"
SHOWN_KEYS = ("layout", "text_side", "tone", "split", "side", "accent", "arrow", "highlight", "current", "steps", "fit", "motif")


def im(name: str, **kw) -> dict:
    """見本の絵（examples/images か assets）を、絶対パスの画像の書き方にする。"""
    p = IMG / name if (IMG / name).exists() else ASSET / name
    return {"path": str(p), "prompt": "カタログ用の見本の絵"} | kw


HERO = im("s01-image.jpg")


def V(type_: str, spec: dict, note: str = "", image: bool = False) -> dict:
    """置き方1つ: 名前（型 / 見た目を変えるキー・補足）と、台本の1枚。"""
    opts = [f"{k}: {str(spec[k]).lower() if isinstance(spec[k], bool) else spec[k]}" for k in SHOWN_KEYS
            if k in spec and isinstance(spec[k], (str, int, bool))]     # big-number の side（配列）などは出さない
    name = type_ + (" / " + "・".join(opts + ([note] if note else [])) if opts or note else "")
    return {"name": name, "image": image, "slide": {"type": type_} | spec}


def variants() -> list[dict]:
    """全部の型と、見た目が大きく変わる置き方。"""
    cards3 = [{"label": "ルール1", "title": "25分で閉じる", "body": "次の予定との間に5分の余白をつくる"},
              {"label": "ルール2", "title": "議題は前日に出す", "body": "議題の無い会議は開かない"},
              {"label": "ルール3", "title": "決めたら閉じる", "body": "決定・担当・期限を3分で書く", "accent": True}]
    flow5 = [{"label": "課題", "title": "長い", "body": "週11.5時間"}, {"label": "原因", "title": "見直さない", "body": "定例が続く"},
             {"label": "解決", "title": "ルール", "body": "3つだけ"}, {"label": "効果", "title": "半分に", "body": "週5時間"},
             {"label": "定着", "title": "続ける", "body": "月1回見直す"}]
    side = [{"left": {"label": "いま", "title": "集まってから考える", "points": ["議題は当日", "60分を使い切る"]},
             "right": {"label": "これから", "title": "決めることを持ち寄る", "points": ["議題は前日", "25分で閉じる"]}}]
    bars = [{"label": "2023年", "value": 8.2}, {"label": "2024年", "value": 9.6}, {"label": "2025年", "value": 10.9},
            {"label": "2026年", "value": 11.5, "highlight": True}]
    members = [{"name": "佐藤 美咲", "role": "取りまとめ", "note": "全体の進め方"}, {"name": "田中 健", "role": "設計部の担当", "note": "議題づくり"},
               {"name": "鈴木 陽子", "role": "営業部の担当", "note": "集計"}]
    gal = [im("s04-image1.png"), im("s04-image2.png"), im("s04-image3.png"), im("s05-image.png")]
    caps = ["設計部: 確認会を25分に", "営業部: 読み合わせを文書に", "総務部: 連絡会を掲示に", "前日に1行で議題を書く"]
    head = {"headline": "会議は**3つのルール**で短くできる"}
    out = [
        V("cover", {"label": "社内提案", "title": "会議の時間を**半分**にする", "subtitle": "3か月の試行のご提案", "meta": ["2026年10月", "架空の会社"]}),
        V("cover", {"title": "会議の時間を**半分**にする", "subtitle": "3か月の試行のご提案", "image": im("cover.png")}, "右に画像", True),
        V("cover", {"layout": "hero", "title": "会議の時間を**半分**にする", "subtitle": "3か月の試行のご提案", "image": HERO}, "左・light（既定）", True),
        V("cover", {"layout": "hero", "text_side": "right", "title": "会議の時間を**半分**にする", "image": im("s01-image.jpg", focus="left")}, "", True),
        V("cover", {"layout": "hero", "tone": "dark", "title": "会議の時間を**半分**にする", "subtitle": "3か月の試行のご提案", "image": HERO}, "", True),
        *[V("cover", {"layout": "graphic", "motif": m, "label": "社内提案", "title": "会議の時間を**半分**にする",
                      "subtitle": "3か月の試行のご提案"}, "画像なしで図形で飾る" if m == "circles" else "") for m in ART.MOTIFS],
        V("agenda", {"items": ["いま何が起きているか", "なぜ減らないのか", "3つのルール", "試行の計画"]}),
        V("agenda", {"items": ["いま何が起きているか", "なぜ減らないのか", "3つのルール", "試行の計画"], "highlight": 2}),
        V("section", {"title": "いま会議に何が起きているか", "subtitle": "数字で見る現状"}),
        V("section", {"title": "どう変えるか", "subtitle": "3つのルール", "image": im("s07-image.png")}, "右に画像", True),
        V("section", {"layout": "graphic", "motif": "bands", "title": "どう変えるか", "subtitle": "3つのルール"}, "画像なしで図形で飾る"),
        V("section", {"layout": "hero", "title": "現場で何を変えたか", "image": HERO}, "左・light（既定）", True),
        V("section", {"layout": "hero", "tone": "dark", "title": "現場で何を変えたか", "image": im("s03-image.png", focus="right")}, "", True),
        V("statement", {"kicker": "まとめ", "headline": "会議を減らすのではなく、**仕事の時間を取り戻す**", "body": "決めることを短く決め、考える時間を増やす"}),
        V("summary", head | {"points": [{"value": "週11.5時間", "title": "会議は3年で1.4倍に増えた"},
                                         {"value": "3つ", "title": "ルールは3つで足りる"}, {"value": "4週", "title": "先行チームは半分になった"}]}, "根拠3つ"),
        V("summary", head | {"points": [{"title": "会議は3年で1.4倍に増えた", "body": "増えたのは定例"},
                                         {"title": "先行チームは4週で半分になった", "body": "週6.0→2.8時間"}]}, "根拠2つ"),
        V("big-number", {"headline": "社員は週に**11.5時間**を会議に使っている", "value": "11.5", "unit": "時間／週", "label": "1人あたりの会議時間",
                         "side": [{"value": "29%", "text": "勤務時間のうち会議の割合"}, {"value": "62%", "text": "出なくてよかった会議の割合"}]}, "補足あり"),
        V("big-number", {"headline": "社員は週に**11.5時間**を会議に使っている", "value": "11.5", "unit": "時間／週", "label": "1人あたりの会議時間"}, "補足なし"),
        V("kpi", {"headline": "目標は、会議の時間を**半分**にすること", "items": [
            {"label": "会議の時間", "value": "5.8", "unit": "時間／週", "delta": "いまの半分", "trend": "down", "accent": True},
            {"label": "1回の長さ", "value": "25", "unit": "分", "delta": "いまは60分", "trend": "down"},
            {"label": "出なくてよかった", "value": "30", "unit": "%以下", "delta": "いまは62%", "trend": "down"}]}, "3つ"),
        V("kpi", {"headline": "売上と利益は、どちらも**計画を上回った**", "items": [
            {"label": "売上高", "value": "1,240", "unit": "億円", "delta": "前年比 +8%", "trend": "up", "accent": True},
            {"label": "営業利益", "value": "96", "unit": "億円", "delta": "前年比 +12%", "trend": "up"},
            {"label": "利益率", "value": "7.7", "unit": "%", "delta": "0.3ポイント上昇", "trend": "up"},
            {"label": "受注残", "value": "880", "unit": "億円", "delta": "横ばい", "trend": "flat"}]}, "4つ"),
        V("kpi", {"headline": "会議の時間は、**週6時間**から半分になった", "items": [
            {"label": "会議の時間", "value": "2.8", "unit": "時間／週", "delta": "前は6.0", "trend": "down", "accent": True},
            {"label": "1回の長さ", "value": "26", "unit": "分", "delta": "前は48分", "trend": "down"},
            {"label": "全社の会議の時間", "value": "5.8", "unit": "時間／週", "reference": True},
            {"label": "全社の1回の長さ", "value": "60", "unit": "分", "delta": "前は62分", "trend": "down", "reference": True}]}, "参考つき"),
        V("kpi", {"headline": "4週目は、**3つの数字**がそろって下がった", "image": im("s12-image.png"), "items": [
            {"label": "会議の時間", "value": "2.8", "unit": "時間／週", "delta": "前は6.0", "trend": "down", "accent": True},
            {"label": "1回の長さ", "value": "26", "unit": "分", "delta": "前は48分", "trend": "down"},
            {"label": "出なくてよかった", "value": "21", "unit": "%", "delta": "前は58%", "trend": "down"}]}, "写真の帯", True),
        V("cards", head | {"items": cards3[:2]}, "2枚"),
        V("cards", head | {"items": cards3}, "3枚"),
        V("cards", head | {"items": cards3 + [{"label": "ルール4", "title": "月1回見直す", "body": "定例を続けるか決める"}]}, "4枚"),
        V("cards", head | {"items": [c | {"image": im(f"s08-item{i + 1}.png")} for i, c in enumerate(cards3)]}, "画像つき", True),
        V("flow", {"headline": "原因は、定例が**見直されない仕組み**にある", "items": flow5[:3]}, "3段"),
        V("flow", {"headline": "原因は、定例が**見直されない仕組み**にある", "items": flow5}, "5段"),
        V("compare", {"headline": "定例会議の進め方は、こう変わる"} | side[0]),
        V("compare", {"headline": "2つの案は、手間と効果で分かれる", "accent": "left", "arrow": False} | side[0]),
        V("compare", {"headline": "確認会は、**持ち寄って決める**会に変わった",
                      "left": side[0]["left"] | {"points": ["議題は当日"], "image": im("s13-left.png")},
                      "right": side[0]["right"] | {"points": ["議題は前日"], "image": im("s13-right.png")}}, "ビフォー・アフターの写真", True),
        V("matrix", {"headline": "候補を比べ、**右上の2つ**に絞った", "x": {"label": "始めやすさ", "low": "準備が要る", "high": "すぐできる"},
                     "y": {"label": "浮く時間", "low": "小さい", "high": "大きい"}, "focus": 2,
                     "quadrants": [{"title": "効くが準備が要る"}, {"title": "すぐ効く"}, {"title": "後回し"}, {"title": "手軽だが小さい"}],
                     "items": [{"label": "25分で閉じる", "x": 80, "y": 75, "accent": True}, {"label": "議題を前日に", "x": 62, "y": 62, "accent": True},
                               {"label": "会議のない日", "x": 20, "y": 80}]}),
        V("matrix", {"headline": "近くて安いのは、**公民館だけ**", "x": {"label": "安さ"}, "y": {"label": "近さ"},
                     "items": [{"label": "公民館", "x": 85, "y": 85, "accent": True}, {"label": "駅前の会議室", "x": 15, "y": 80}],
                     "callout": {"title": "選ばれる位置", "text": "予約の手段さえあれば選ばれる"}}, "右に要点"),
        V("bar-chart", {"headline": "会議の時間は**3年で1.4倍**に増えた", "bars": bars, "unit": "時間", "max": 14,
                        "note": "1人あたり週の会議時間（架空）"}),
        V("bar-chart", {"headline": "会議の時間は**3年で1.4倍**に増えた", "bars": bars, "unit": "時間", "max": 14,
                        "callout": {"value": "1.4倍", "text": "増えたのは定例会議"}}, "右に要点"),
        V("line-chart", {"headline": "先行チームは**4週で半分以下**になった", "labels": ["前", "1週", "2週", "3週", "4週"], "unit": "時間",
                         "series": [{"values": [6.0, 4.6, 3.9, 3.1, 2.8]}], "max": 8}, "1本"),
        V("line-chart", {"headline": "東の予約は、**1年で4倍**に増えた", "labels": [f"{m}月" for m in range(1, 13)], "unit": "件",
                         "series": [{"name": "東", "values": [10, 12, 15, 14, 18, 22, 25, 24, 28, 31, 35, 40], "highlight": True},
                                    {"name": "西", "values": [8, 9, 9, 11, 12, 12, 14, 15, 15, 16, 18, 19]},
                                    {"name": "北", "values": [5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 11]}],
                         "callout": {"value": "4倍", "text": "東だけが伸びた"}}, "3本・右に要点"),
        V("table", {"headline": "最初に変える会議は、**効果と手間**で選ぶ", "columns": ["会議", "参加者", "浮く時間", "手間"],
                    "rows": [["週次定例", "12人", "週360分", "低"], ["進捗会議", "8人", "週180分", "低"], ["全体報告", "60人", "週620分", "高"]]}),
        V("table", {"headline": "最初に変える会議は、**効果と手間**で選ぶ", "columns": ["会議", "参加者", "浮く時間", "手間"], "highlight": 3,
                    "rows": [["週次定例", "12人", "週360分", "低"], ["進捗会議", "8人", "週180分", "低"], ["全体報告", "60人", "週620分", "高"]]}),
        V("table", {"headline": "返信までの日数は、**3 部署とも 2 日を切った**", "columns": ["部署", "先月", "今月"],
                    "rows": [["受付", "3.1 日", "1.4 日"], ["配送", "2.6 日", "1.8 日※1"], ["請求", "2.9 日", "1.2 日"]],
                    "source": "返信までの日数は、受け付けてから最初に返信するまでの営業日の平均",
                    "footnote": "※1 月末に休業日が 2 日あったため、その日の受付分を除いて計算"}, "source と脚注"),
        V("bullets", {"headline": "現場へのお願いは**3つだけ**", "items": [{"title": "予定表に議題を書く", "body": "1行でよい"},
                                                                    {"title": "25分で閉じる", "body": "残りは文書に"}, {"title": "週1回答える", "body": "2問だけ"}]}),
        V("bullets", {"headline": "現場へのお願いは**3つだけ**", "items": ["予定表に議題を書く", "25分で閉じる", "週1回答える"], "image": im("calendar.png")}, "画像（右・1/3 既定）", True),
        V("bullets", {"headline": "現場へのお願いは**3つだけ**", "items": ["予定表に議題を書く", "25分で閉じる", "週1回答える"],
                      "image": im("s09-image.png"), "side": "left", "split": "2/5"}, "", True),
        V("bullets", {"headline": "現場へのお願いは**2つだけ**", "items": ["予定表に議題を書く", "25分で閉じる"],
                      "image": im("s09-image.png"), "split": "1/2"}, "", True),
        V("timeline", {"headline": "3か月で試し、**効果を測ってから**広げる", "current": 2, "items": [
            {"when": "10月", "title": "準備", "body": "対象を選ぶ"}, {"when": "11月", "title": "試行", "body": "2部署で"},
            {"when": "12月", "title": "測る", "body": "前と比べる"}, {"when": "1月", "title": "判断", "body": "広げるか決める"}]}),
        V("team", {"headline": "試行は、**3人**が各部署と一緒に進める", "members": members}, "頭文字"),
        V("team", {"headline": "試行は、**6人**で進める", "members": members + [{"name": "高橋", "role": "開発"}, {"name": "伊藤", "role": "営業"},
                                                                               {"name": "Alex Kim", "initials": "AK", "role": "デザイン"}]}, "6人・2段"),
        V("team", {"headline": "試行は、**3人**が各部署と一緒に進める", "members": [m | {"image": im("s06-image.png")} for m in members]}, "写真", True),
        V("photo", {"headline": "会議室を、**決める場所**に変える", "body": "共有は事前の文書で済ませる", "image": im("meeting.png")}, "全面（既定）", True),
    ]
    for lay in ("left", "right"):
        for sp in ("1/2", "2/5", "1/3"):
            out.append(V("photo", {"layout": lay, "split": sp, "headline": "議題は前日に、**1行で**書く", "body": "書けない会議は開かない",
                                   "image": im("s05-image.png")}, "", True))
    out.append(V("photo", {"layout": "band", "kicker": "現場の変化", "headline": "午後の会議室は、**空いている時間**が増えた",
                           "body": "4週目は、午後の予約が試行前の半分ほどになった（架空）", "image": im("s11-image.png")}, "", True))
    for n in (2, 3, 4):
        out.append(V("gallery", {"headline": f"{n}つの部署で、**一番長い定例**から始めた",
                                 "items": [{"image": gal[i], "caption": caps[i]} for i in range(n)]}, f"{n}枚", True))
    out.append(V("gallery", {"layout": "collage", "headline": "朝の連絡会を、**掲示と文書**に置き換えた",
                             "items": [{"image": im("s14-image1.png"), "caption": "連絡は掲示板に"},
                                       {"image": im("s14-image2.png"), "caption": "詳しくは文書で"},
                                       {"image": im("s14-image3.png"), "caption": "朝の会議室が空いた"}]}, "大1枚＋小2枚", True))
    out += [
        V("annotate", {"headline": "会議室の**3か所**を変えた", "image": im("meeting.png"),
                       "items": [{"x": 30, "y": 20, "title": "席を減らした", "body": "8席を6席に"},
                                 {"x": 70, "y": 50, "title": "画面を置いた", "body": "資料はその場で映す"},
                                 {"x": 45, "y": 85, "title": "時計を置いた", "body": "25分が見える"}]}, "写真に印", True),
        V("annotate", {"fit": "contain", "headline": "予定表の**3か所**を変える", "image": im("s15-image.png"),
                       "items": [{"x": 9, "y": 23, "title": "議題の欄に1行"}, {"x": 44, "y": 17, "title": "枠は25分"},
                                 {"x": 44, "y": 75, "title": "空いた時間を押さえる"}]}, "図に印", True),
        V("figure", {"headline": "午後に**考える時間**がまとまって空いた", "image": im("s07-image.png"), "caption": "予定表のイメージ（見本の絵）"}, "", True),
        V("quote", {"kicker": "現場の声", "quote": "25分で終わると、次の準備ができる。", "by": "参加者", "position": "設計部（架空）"}),
        V("quote", {"layout": "full", "quote": "時間を決めると、話す前に考えるようになった。", "by": "参加者", "position": "営業部（架空）",
                    "image": im("s10-image.png")}, "", True),
        V("quote", {"layout": "portrait", "quote": "議題が前日に届くので、迷う時間がなくなった。", "by": "参加者", "position": "設計部（架空）",
                    "image": im("s06-image.png")}, "", True),
        V("closing", {"headline": "まずは11月、**2つの部署**で試させてください", "items": [{"title": "試行の承認", "body": "本日ご判断ください"},
                                                                                     {"title": "部署の決定", "body": "10月中旬まで"}],
                      "contact": {"name": "経営企画部", "lines": ["内線 1234（架空）"]}}, "次の一手と連絡先"),
        V("closing", {"headline": "ご意見は**11月末まで**お寄せください", "contact": {"name": "経営企画部", "lines": ["内線 1234（架空）"]}}, "連絡先だけ"),
        V("closing", {"headline": "次は1月から、\n全部署に広げたい", "items": [{"title": "全社展開の承認", "body": "1月にご判断ください"}],
                      "contact": {"name": "経営企画部", "lines": ["内線 1234（架空）"]}, "image": im("s16-image.png")}, "右に写真", True),
    ]
    eq = [{"icon": "clock", "title": "30分で切る", "body": "延長しない"}, {"icon": "list", "title": "議題は前日に", "body": "1行で書く"},
          {"icon": "users", "title": "立って話す", "body": "10分の会議"}, {"icon": "doc", "title": "記録は1枚", "body": "決めたことだけ"}]
    res = {"icon": "flag", "title": "半分の会議時間", "body": "先行チームで実測"}
    hub = [{"icon": "building", "title": "設計部", "body": "確認会を25分に", "tag": "4週目"},
           {"icon": "chat", "title": "営業部", "body": "読み合わせを文書に", "tag": "2週目"},
           {"icon": "mail", "title": "総務部", "body": "連絡会を掲示に", "tag": "準備中"},
           {"icon": "chart", "title": "集計", "body": "毎週金曜に共有", "tag": "事務局"},
           {"icon": "users", "title": "現場の声", "body": "週1回の3問", "tag": "全員"},
           {"icon": "shield", "title": "労務", "body": "残業との突き合わせ", "tag": "月1回"}]
    pairs = [{"icon": "doc", "label": "議題", "before": "当日に口頭で", "after": "前日に1行で"},
             {"icon": "clock", "label": "時間", "before": "終わるまで続ける", "after": "30分で切る"},
             {"icon": "users", "label": "出席", "before": "関係者を全員", "after": "決める人だけ"},
             {"icon": "check", "label": "決定", "before": "持ち帰って検討", "after": "その場で決める"}]
    out += [
        V("equation", {"headline": "短い会議 ＝ 30分＋議題＋立って話す", "items": eq[:2], "result": res}, "要素2つ"),
        V("equation", {"headline": "短い会議 ＝ 30分＋議題＋立って話す", "items": eq[:3], "result": res,
                       "takeaway": "3つがそろうと、会議は半分になる"}, "要素3つ・締めの帯"),
        V("equation", {"headline": "短い会議は、4つのルールの足し算", "items": eq, "result": res}, "要素4つ"),
        V("hub", {"headline": "事務局が、3つの部署の試行をつなぐ", "center": {"icon": "hub", "title": "事務局", "body": "ルールと集計"},
                  "items": hub[:3]}, "周り3つ"),
        V("hub", {"headline": "事務局が、3つの部署の試行をつなぐ", "center": {"icon": "hub", "title": "事務局", "body": "ルールと集計"},
                  "items": hub[:4], "takeaway": "集計は毎週金曜、全員に共有する"}, "周り4つ・締めの帯"),
        V("hub", {"headline": "事務局が、関わる人と仕組みをつなぐ", "center": {"icon": "hub", "title": "事務局"}, "items": hub}, "周り6つ"),
        V("pairs", {"headline": "会議は、2つの直しで短くなる", "items": pairs[:2]}, "2組"),
        V("pairs", {"headline": "会議は、3つの直しで短くなる", "items": pairs[:3], "takeaway": "どれも、今日から変えられる"}, "3組・締めの帯"),
        V("pairs", {"headline": "会議は、4つの直しで短くなる", "items": pairs, "bad_label": "いま", "good_label": "これから"}, "4組・札を替える"),
        V("notice", {"headline": "試行の間、**3つのこと**を知っておいてください", "items": [
            {"kind": "info", "title": "試行は11月から、2部署で始める", "body": "ほかの部署は結果を見てから決める"},
            {"kind": "warning", "title": "25分で終わらなくても、会議は閉じる", "points": ["残りは次回か文書に", "議題が無い会議は開かない"]},
            {"kind": "error", "title": "議事録に個人名は書かない", "body": "役割で書き換える"}]}, "3つ・種類つき"),
        V("notice", {"headline": "問い合わせの返信が遅れた件", "items": [
            {"kind": "error", "label": "課題", "title": "返信に平均 3 日かかっていた"},
            {"kind": "warning", "label": "原因", "title": "担当の割り振りが決まっていなかった"},
            {"kind": "info", "label": "対策", "title": "受付の時点で担当を自動で割り振る"},
            {"kind": "success", "label": "アクション", "title": "来月から割り振りの表を運用する", "body": "月末に件数と返信までの日数を見直す"}]}, "4つ・課題→原因→対策→アクション"),
        V("notice", {"headline": "問い合わせの返信が遅れた件", "label_style": "chip", "items": [
            {"kind": "error", "label": "課題", "title": "返信に平均 3 日かかっていた"},
            {"kind": "warning", "label": "原因", "title": "担当の割り振りが決まっていなかった"},
            {"kind": "info", "label": "対策", "title": "受付の時点で担当を自動で割り振る"},
            {"kind": "success", "label": "アクション", "title": "来月から割り振りの表を運用する", "body": "月末に件数と返信までの日数を見直す"}]}, "4つ・label を札で"),
        V("notice", {"headline": "設定は**完了**している", "items": [{"kind": "success", "title": "次の画面から使える", "body": "やり直しは要らない"}]}, "1つ"),
        V("deflist", {"headline": "この資料で使う言葉は、**4つ**だけ", "items": [
            {"term": "定例会議", "body": "曜日と時間を決めて繰り返す会議"}, {"term": "25分ルール", "body": "30分の枠でも25分で閉じる"},
            {"term": "議題", "body": "話す内容と決めたいことを1行ずつ"}, {"term": "3行の記録", "body": "決定・担当・期限だけの議事録"}]}),
        V("steps", {"headline": "申請は5つの段で進み、**いまは2段目**", "current": 2, "items": [
            {"title": "申込", "body": "フォームに入力"}, {"title": "確認", "body": "担当が確かめる"}, {"title": "承認", "body": "部長が決裁"},
            {"title": "手配", "body": "物品を発注"}, {"title": "完了", "body": "受け取って報告"}]}, "横"),
        V("steps", {"headline": "会議は4つの段で進み、**いまは3段目**", "layout": "column", "current": 3, "items": [
            {"title": "前日までに議題を出す", "body": "予定表に1行ずつ"}, {"title": "事前に資料を読む", "body": "当日は質問から"},
            {"title": "25分で決めて閉じる", "body": "残りは次回へ"}, {"title": "3行の記録を共有する", "body": "その日のうちに"}]}, "縦"),
        V("cards", {"headline": "会議は**3つのルール**で短くできる", "items": [{"icon": "clock", "title": "30分で切る", "body": "延長しない"},
                    {"icon": "list", "title": "議題は前日に", "body": "1行で書く"}, {"icon": "users", "title": "立って話す", "body": "10分の会議"}],
                    "takeaway": "3つを同時に始める"}, "アイコン・締めの帯"),
        V("bullets", {"headline": "現場へのお願いは**3つだけ**", "items": [{"icon": "calendar", "title": "予定表に議題を書く"},
                      {"icon": "clock", "title": "30分で閉じる"}, {"icon": "chat", "title": "週1回の3問に答える"}]}, "アイコン"),
    ]
    return out


def select(vs: list[dict], only: str | None) -> list[dict]:
    if only in (None, "", "all"):
        return vs
    if only == "image":
        return [v for v in vs if v["image"]]
    if only == "text":
        return [v for v in vs if not v["image"]]
    if only in T.TYPES:
        return [v for v in vs if v["slide"]["type"] == only]
    raise T.DeckError(f"--only は image / text / 型の名前（{', '.join(T.TYPES)}）のどれか: {only}")


def catalog_script(only: str | None = None) -> tuple[dict, list[str]]:
    """(一覧の台本, 各スライドの名前の列)。slides の順は variants の順。名前は台本には書かない。"""
    vs = select(variants(), only)
    return {"title": "html-slide レイアウト一覧", "footer": "レイアウト一覧（中身は架空のダミー）", "mode": "talk",
            "slides": [v["slide"] for v in vs]}, [v["name"] for v in vs]


def contact_sheets(pngs: list[pathlib.Path], names: list[str], out_dir: pathlib.Path, title: str) -> list[pathlib.Path]:
    """縮小した PNG に名前を添え、4列×4段で一覧画像に並べる（収まらなければ複数枚）。並べ方は資料のサムネイルと同じ（outputs.sheets）。"""
    paths = []
    for i, sheet in enumerate(OUT.sheets(pngs, names, title), 1):
        p = out_dir / f"catalog-{i:02d}.png"
        sheet.save(p, optimize=True)
        paths.append(p)
    return paths


def build_catalog(out_dir, only: str | None = None, theme: str | None = None) -> list[pathlib.Path]:
    """一覧を作る。<out_dir>/catalog.json・html/・png/・catalog-NN.png。一覧画像のパスの列を返す。誤りは DeckError。"""
    out_dir = pathlib.Path(out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    sc, names = catalog_script(only)
    if not sc["slides"]:
        raise T.DeckError(f"一覧に入る置き方が無い（--only {only}）")
    script = out_dir / "catalog.json"
    script.write_text(json.dumps(sc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out_dir / "catalog-names.json").write_text(json.dumps(names, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    html, info = deck.build_html(script, out_dir / "html", theme=theme)
    png_dir = out_dir / "png"
    for old in png_dir.glob("slide-*.png"):
        old.unlink()
    import browser as B
    node, _ = B.node()
    if not node or B.missing():
        raise RuntimeError(f"{'・'.join(B.missing())}。{B.SETUP_HINT}")
    r = subprocess.run([node, str(SKILL / "scripts" / "snapshot.mjs"), str(html), str(png_dir)], capture_output=True,
                       text=True, env=B.child_env(), encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"snapshot.mjs が失敗した:\n{r.stderr[-2000:]}")
    warn = [ln for ln in r.stdout.splitlines() if ln.startswith("警告")]
    if warn:
        print("\n".join(warn), file=sys.stderr)
    pngs = sorted(png_dir.glob("slide-*.png"))
    if len(pngs) != len(names):
        raise RuntimeError(f"撮った枚数 {len(pngs)} と置き方の数 {len(names)} が合わない")
    th = pathlib.Path(info["theme"]).stem
    title = f"html-slide レイアウト一覧・{len(names)} 種（テーマ: {th}" + (f"・{only}" if only else "") + "）"
    return contact_sheets(pngs, names, out_dir, title)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="レイアウトの一覧（カタログ）を作る")
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", help="image / text / 型の名前")
    ap.add_argument("--theme")
    a = ap.parse_args(argv)
    try:
        paths = build_catalog(a.out, a.only, a.theme)
    except T.DeckError as e:
        print(f"catalog: {e}", file=sys.stderr)
        return 2
    for p in paths:
        print(p)
    return 0


if __name__ == "__main__":
    for _out in (sys.stdout, sys.stderr):   # Windows の既定（cp932・\r\n）でなく UTF-8・\n で出す。読むのはエージェントとサーバ
        _out.reconfigure(encoding="utf-8", newline="\n")
    sys.exit(main())
