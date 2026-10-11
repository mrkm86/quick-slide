import os


class _Chromium:
    @property
    def executable_path(self) -> str:
        return os.environ.get("FAKE_PLAYWRIGHT_CHROMIUM", "")


class _Playwright:
    chromium = _Chromium()


class sync_playwright:  # noqa: N801 - 本物の名前に合わせる
    def __enter__(self) -> _Playwright:
        return _Playwright()

    def __exit__(self, *exc) -> bool:
        return False
