
from maxbot.uploads import Uploader

BASE = "http://test.max"


class FakeClient:
    """Минимальный двойник MaxClient для Uploader."""

    def __init__(self):
        self.upload_calls = []

    async def get_upload_slot(self, kind):
        self.kind = kind
        return "http://upload.test/u", None

    async def upload_to_url(self, url, path, token_hint=None):
        self.upload_calls.append((url, path, token_hint))
        return f"TOK{len(self.upload_calls)}"


async def test_upload_caches_by_path(tmp_path):
    f = tmp_path / "img.png"
    f.write_bytes(b"\x89PNG" * 10)
    fc = FakeClient()
    up = Uploader(fc)
    t1 = await up.upload(str(f), "image")
    t2 = await up.upload(str(f), "image")
    assert t1 == t2 == "TOK1" and len(fc.upload_calls) == 1


async def test_upload_invalidated_on_file_change(tmp_path):
    f = tmp_path / "img.png"
    f.write_bytes(b"a")
    fc = FakeClient()
    up = Uploader(fc)
    await up.upload(str(f), "image")
    f.write_bytes(b"bb")  # size изменился
    await up.upload(str(f), "image")
    assert len(fc.upload_calls) == 2
