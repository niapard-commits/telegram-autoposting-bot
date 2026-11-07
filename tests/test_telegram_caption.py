from conftest import FakeResponse, FakeSession

from lutserii import telegram
from lutserii.telegram import visible_len


def test_visible_len_ignores_tags_and_decodes_entities():
    assert visible_len('<b>Привіт</b> <a href="https://x.io/a-very-long-link">світ</a> &amp; ок') == len("Привіт світ & ок")


def test_album_is_sent_when_markup_is_long_but_visible_text_fits():
    link = '<a href="https://t.me/' + "x" * 60 + '">аб</a>'
    text = "а" * 900 + link * 3  # розмітка > 1024, видимий текст ≈ 906
    assert len(text) > telegram.CAPTION_LIMIT and visible_len(text) <= telegram.CAPTION_LIMIT
    calls = []

    class Session(FakeSession):
        def post(self, url, json=None, timeout=None, **kw):
            calls.append(url.rsplit("/", 1)[-1])
            return FakeResponse({"ok": True})

    telegram.send_post(Session({}), "tok", "1", text, ["https://a/1.jpg", "https://a/2.jpg"])
    assert calls == ["sendMediaGroup"]


def test_share_markup_and_button_in_payloads(monkeypatch):
    from lutserii import telegram
    markup = telegram.share_markup("@lut_i_serii")
    assert markup["inline_keyboard"][0][0]["url"].startswith("https://t.me/share/url?url=https%3A%2F%2Ft.me%2Flut_i_serii")
    assert telegram.share_markup("") is None
    sent = []
    monkeypatch.setattr(telegram, "_call", lambda session, token, method, payload: sent.append((method, payload)) or {"ok": True})
    monkeypatch.setattr(telegram, "SHARE", markup)
    telegram.send_message(None, "t", "@c", "текст", "https://x/p.jpg")
    telegram.send_message(None, "t", "@c", "текст")
    assert all(p["reply_markup"] == markup for _, p in sent) and len(sent) == 2
    sent.clear()
    monkeypatch.setattr(telegram, "SHARE", None)
    telegram.send_message(None, "t", "@c", "текст")
    assert "reply_markup" not in sent[0][1]
