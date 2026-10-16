"""Quick Slide — 常駐サーバと画面。

サーバ（`server/`）・アクセス層（`store/`）・DB（`db/`）。台本係（`writer/`）・描き係（`render/`）・ワーカー（`worker/`）。
エンジン（`skills/html-slide`・`skills/image-slide`）はこのパッケージから独立している。
"""

__all__ = ["__version__"]
# 製品の版（SemVer）。プラグインのマニフェスト 2 つと揃える。上げるのは tools/release.py（docs/RELEASING.md）
__version__ = "1.0.2"
