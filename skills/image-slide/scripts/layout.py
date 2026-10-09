"""キャンバスと下端の帯の寸法。プロンプト・合成・PPTX が同じ数字を使う。

1枚は 1920×1080（16:9）にそろえる。下端の帯（高さ 7%）は生成に空けさせ、
ロゴ・フッタの文字・ページ番号を上から載せる（C フォーマットと A ブランドのロゴ。ADR 0003）。

帯の中の置き方（html-slide と同じ並び・ADR 0008）:
    [フッタの文字 ……………  ページ番号（中央・出すときだけ）  ……………  ロゴ]
                          透かし（--watermark・中央下の端・全ページ）

フッタかページ番号があるページは、その行（ロゴも）を帯の中心から上げる（ロゴの上端＝帯の上端・html-slide とほぼ同じ高さ）。
下の端は透かしの場所で、中央の番号と重ならない。透かしの有無では動かさない。ロゴだけのページ（表紙・写真・
フッタの無いポスター）は帯の中心のまま（ADR 0010）。

ロゴは html-slide と同じ規則で置く（329×44 の枠に縮めて収める・右下 → 左下 → 置かない）。
寸法と規則の数字は html-slide の `scripts/deck.py` と同じにする（ずれていないかは tests/test_logic.py が見比べる）。
"""
from __future__ import annotations

W, H = 1920, 1080
BAND_RATIO = 0.07
BAND_H = round(H * BAND_RATIO)          # 76
BAND_TOP = H - BAND_H
SIDE = 120                              # 帯の中の左右の余白（html-slide の本文の余白と同じ）
TEXT_PX = 22                            # フッタ・ページ番号の文字の高さ（px）

# ロゴ（html-slide の deck.py と同じ数字）
LOGO_BOX_W, LOGO_BOX_H = 329, 44        # この枠に縦横比を保って収める。縮めるだけで引き伸ばさない
LOGO_RIGHT = SIDE                       # 右下のロゴの右端の余白（左下では左端の余白）
LOGO_MID_Y = BAND_TOP + BAND_H / 2      # 縦は下の文字の行（帯）の中心
LOGO_GAP = 28                           # ロゴとページ番号の間
LOGO_MIN_SIDE = 30                      # 短辺がこれ未満なら小さいと警告する
LOGO_RING = 16                          # 置けるかを測る、ロゴの枠の周りの幅（px）
LOGO_CONTRAST = 3.0                     # ロゴと下のコントラスト比がこれ未満なら読めないとみなす（WCAG の大きい文字の基準）
LOGO_STICKER_PAD = 12                   # 読めない所に敷くステッカーの、ロゴの周りの余白（px）
LOGO_STICKER_RADIUS = 10                # ステッカーの角の丸み（px）
LOGO_TOP_MID_Y = 46                     # 右上に置くときの縦の中心（44px のロゴが 24〜68px）
# --logo-position: 指定した角を先に試し、だめなら次へ。上下をまたいで逃がすのは右上 → 右下だけ。左上は選べない
LOGO_POSITIONS = {
    "bottom-right": ("bottom-right", "bottom-left"),
    "bottom-left": ("bottom-left", "bottom-right"),
    "top-right": ("top-right", "bottom-right"),
}
# 帯を頼まないページで置けるか（周りが1色か）: 代表色（中央値）から BAND_DIFF より離れた画素が BAND_MAX_SHARE 以下（帯の
# 検査と同じ基準）で、かつ LOGO_PLATE_SHARE 以上が代表色から LOGO_PLATE_DIFF 以内。置いたら、その色で地を敷く
LOGO_PLATE_DIFF = 8
LOGO_PLATE_SHARE = 0.9

# 生成の絵を 16:9 にそろえるときの許し（normalize）
RATIO_TOL = 0.02                        # この割合までの縦横比のずれは、そのまま伸縮する
CROP_SIDE_MAX = 0.06                    # 片側でこの割合までなら切る（プロンプトで端から 6% 空けさせている）

# 帯の検査（band_check）
BAND_DIFF = 40                          # 帯の代表色からこれだけ離れた画素を「物が描かれている」とみなす（0〜255）
BAND_MAX_SHARE = 0.006                  # その画素がこの割合を超えたら、帯が空いていない

# 透かし（--watermark）。Gray-400・高さの 1.5%・下端から 1.2%（ADR 0010）。数字は html-slide の deck.py と同じ
WATERMARK_PX = 16                       # 文字の大きさ（1080 の 1.5%）
WATERMARK_COLOR = "#767676"             # Gray-536（DADS・白の地で 4.5:1）。html-slide と同じ。絵の上は測らない
WATERMARK_BOTTOM = 13                   # 文字のベースラインから画面の下端まで（1080 の 1.2%）
ROW_MID_RAISED = BAND_TOP + LOGO_BOX_H / 2   # フッタか番号があるページの下の行の中心（1026。ロゴの上端が帯の上端にそろう）


def row_mid(text_row: bool = False) -> float:
    """フッタ・ページ番号・ロゴの行の中心。text_row（フッタかページ番号がある）なら上げて、下の端を透かしに空ける。
    ロゴだけなら帯の中心（ロゴは角なので中央の透かしに届かない。上げると周りの絵や枠にかかる）。"""
    return ROW_MID_RAISED if text_row else LOGO_MID_Y


FOOTER_END_WITH_NO = W // 2 - 48       # ページ番号を出すときのフッタの右端（中央の番号の手前・html-slide と同じ 912）


def page_number_box(text_w: int, mid: float = LOGO_MID_Y) -> tuple[int, int, int, int]:
    """ページ番号の (x, y, w, h)。中央下（ロゴは角に置くので重ならない・html-slide と同じ）。mid は行の中心（row_mid）。"""
    return (W // 2 - text_w // 2, int(mid - TEXT_PX / 2), text_w, TEXT_PX)


def logo_geometry(w: int, h: int, mid: float = LOGO_MID_Y) -> dict:
    """ロゴの原本の寸法（透明な余白を切った後）→ 右下に置くときの {"x", "y", "w", "h", "scale", "warning"}。

    scale = min(1, 枠の幅/w, 枠の高さ/h)。枠より小さい原本は原寸のまま（にじむので引き伸ばさない）。
    html-slide の deck.logo_geometry と同じ計算（y だけ、それぞれの下の文字の行の中心）。mid は行の中心（row_mid）。
    """
    if w <= 0 or h <= 0:
        raise ValueError(f"ロゴの画像の大きさが読めない（{w}×{h}）")
    scale = min(1.0, LOGO_BOX_W / w, LOGO_BOX_H / h)
    lw, lh = max(1, round(w * scale)), max(1, round(h * scale))
    x, y = W - LOGO_RIGHT - lw, round(mid - lh / 2)
    warning = None
    if min(lw, lh) < LOGO_MIN_SIDE:
        why = ("原本が小さい（引き伸ばすとにじむので原寸のまま）" if scale == 1.0 and (w < LOGO_BOX_W and h < LOGO_BOX_H)
               else f"枠 {LOGO_BOX_W}×{LOGO_BOX_H} に縦横比を保って収めたため")
        warning = f"ロゴが {lw}×{lh}px と小さい（{why}）。横長のロゴか、大きい原本があればそちらを使う"
    return {"x": x, "y": y, "w": lw, "h": lh, "scale": scale, "warning": warning}


def logo_box(logo: dict, corner: str) -> tuple[int, int, int, int]:
    """角ごとのロゴの (x, y, w, h)。左下の x は本文の余白（文字の位置は絵からは分からない。html-slide で文字が無いページと同じ）。"""
    w, h = logo["w"], logo["h"]
    if corner == "bottom-left":
        return (LOGO_RIGHT, logo["y"], w, h)
    if corner == "top-right":
        return (logo["x"], round(LOGO_TOP_MID_Y - h / 2), w, h)
    return (logo["x"], logo["y"], w, h)


def footer_box(logo_w: int = 0, number: bool = False, mid: float = LOGO_MID_Y) -> tuple[int, int, int, int]:
    """フッタの文字の (x, y, w, h)。左端から。左下にロゴがあれば、ロゴの右から（html-slide と同じ）。
    右端は、ページ番号を出すなら中央の番号の手前、出さなければ帯の右から 1/4 の手前（右下のロゴに届かない）。"""
    x = SIDE + (logo_w + LOGO_GAP if logo_w else 0)
    end = FOOTER_END_WITH_NO if number else W * 3 // 4
    return (x, int(mid - TEXT_PX / 2), end - x, TEXT_PX)
