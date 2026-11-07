import pytest
from conftest import FakeResponse, FakeSession
from test_main import deal_routes, raw_deal

from lutserii import main as app
from lutserii import posts, telegram
from lutserii.sources import cheapshark, steam
from lutserii.textutil import franchise_key, franchise_name


def test_franchise_name_and_key():
    assert franchise_name("Watch_Dogs: Legion") == "Watch Dogs" == franchise_name("Watch Dogs 2")
    assert franchise_key("Deus Ex: Mankind Divided") == franchise_key("Deus Ex: Human Revolution - Director's Cut")
    assert franchise_key("Dark Souls III") == franchise_key("Dark Souls: Remastered")
    assert franchise_key("Portal") != franchise_key("Quake")
    assert franchise_key("Up") == ""  # надто коротко, щоб довіряти збігу


def test_run_deals_groups_series_into_one_post(make_ctx):
    deals = [
        raw_deal("1", "Watch Dogs 2", rating="9.5", sale="5.00"),
        raw_deal("2", "Watch_Dogs: Legion", rating="9.0", sale="7.00"),
        raw_deal("3", "Watch Dogs", rating="8.5", sale="2.00"),
        raw_deal("4", "Other Game", rating="9.9", sale="3.00"),
        raw_deal("5", "Watch Dogs 2", rating="8.1", sale="6.00", store="7"),  # інший магазин
    ]
    ctx, sent, _ = make_ctx(deal_routes(deals), max_deals_per_run=5, max_deals_per_day=5,
                            chat_id="@lut_i_serii")
    n = app.run_deals(ctx)
    texts = [t for t, _ in sent]
    series = [t for t in texts if "Серія Watch Dogs" in t]
    assert n == len(texts)
    assert len(series) == 1
    text = series[0]
    assert "знижки до 80% на 3 гри у Steam" in text
    for title in ("Watch Dogs 2", "Watch_Dogs: Legion", ">Watch Dogs<"):
        assert title in text
    assert text.count("<blockquote>") == 1 and text.rstrip().endswith("@lut_i_serii")
    # усі частини серії позначено опублікованими
    assert ctx.db.was_posted("title:watch dogs 2") and ctx.db.was_posted("title:watch_dogs: legion")
    assert app.run_deals(ctx) <= 1  # повторно серію не постимо


def test_series_post_plural_forms():
    d = cheapshark.parse_deal(raw_deal("a", "Game 1"))
    t = posts.deal_series_post([d, d], "GOG", "Game")
    assert "на 2 гри у GOG" in t
    assert "на 5 ігор" in posts.deal_series_post([d] * 5, "", "Game")
    assert "на 1 гру" in posts.deal_series_post([d], "", "Game")


def test_steam_trailer_parsed_and_forced_to_https():
    payload = {"1": {"success": True, "data": {"movies": [
        {"mp4": {"480": "http://cdn.steam/x/movie480.mp4?t=1", "max": "http://cdn.steam/x/max.mp4"}}]}}}
    assert steam.parse_app(payload, "1").trailer == "https://cdn.steam/x/movie480.mp4?t=1"
    assert steam.parse_app({"1": {"success": True, "data": {}}}, "1").trailer == ""


class TgSession:
    def __init__(self, fail_url_send=False, video_bytes=b"x" * 10, fail_upload=False):
        self.calls = []
        self.fail_url_send = fail_url_send
        self.video_bytes = video_bytes
        self.fail_upload = fail_upload

    def post(self, url, json=None, data=None, files=None, timeout=None, **kw):
        method = url.rsplit("/", 1)[-1]
        self.calls.append(method + ("+upload" if files else ""))
        if method == "sendVideo" and files:
            return FakeResponse({"ok": not self.fail_upload, "description": "bad"})
        if method == "sendVideo" and self.fail_url_send:
            return FakeResponse({"ok": False, "description": "wrong file"})
        return FakeResponse({"ok": True})

    def get(self, url, timeout=None, **kw):
        self.calls.append("download")
        resp = FakeResponse({})
        resp.content = self.video_bytes
        return resp


def test_send_video_by_url():
    s = TgSession()
    telegram.send_video_post(s, "t", "1", "текст", "https://v/1.mp4", ["https://p/1.jpg"])
    assert s.calls == ["sendVideo"]


def test_send_video_falls_back_to_upload_then_photos():
    s = TgSession(fail_url_send=True)
    telegram.send_video_post(s, "t", "1", "текст", "https://v/1.mp4", ["https://p/1.jpg"])
    assert s.calls == ["sendVideo", "download", "sendVideo+upload"]
    s = TgSession(fail_url_send=True, fail_upload=True)
    telegram.send_video_post(s, "t", "1", "текст", "https://v/1.mp4", ["https://p/1.jpg", "https://p/2.jpg"])
    assert s.calls[-1] == "sendMediaGroup"
    s = TgSession(fail_url_send=True, video_bytes=b"x" * (telegram.MAX_VIDEO_BYTES + 1))
    telegram.send_video_post(s, "t", "1", "текст", "https://v/1.mp4", ["https://p/1.jpg"])
    assert "sendVideo+upload" not in s.calls and s.calls[-1] == "sendPhoto"


def test_publish_uses_video_when_enabled(make_ctx):
    ctx, sent, _ = make_ctx({})
    videos = []
    ctx.send_video = lambda text, video, photos: videos.append((text, video, photos))
    ctx.publish("deal", ["k1"], "t", ["p"], "https://v/1.mp4")
    assert videos == [("t", "https://v/1.mp4", ["p"])] and sent == []
    ctx2, sent2, _ = make_ctx({}, game_video=False)
    ctx2.send_video = lambda *a: pytest.fail("відео вимкнено")
    ctx2.publish("deal", ["k2"], "t", ["p"], "https://v/1.mp4")
    assert sent2 == [("t", ["p"])]


def test_editions_of_one_game_are_not_a_series(make_ctx):
    deals = [
        raw_deal("1", "Back 4 Blood: Deluxe Edition", rating="9.0", sale="7.20", store="1"),
        raw_deal("2", "Back 4 Blood: Ultimate Edition", rating="9.5", sale="8.00", store="1"),
    ]
    ctx, sent, _ = make_ctx(deal_routes(deals), max_deals_per_run=5, max_deals_per_day=5)
    assert app.run_deals(ctx) == 1
    assert "Серія" not in sent[0][0] and "Ultimate Edition" in sent[0][0]
    assert ctx.db.was_posted("title:back4blood")
    assert app.run_deals(ctx) == 0  # друге видання не виходить окремим постом


def test_whole_sentences_never_cut_mid_sentence():
    from lutserii.textutil import whole_sentences

    text = "Перше речення. Друге речення довше. Третє речення без кінця"
    assert whole_sentences(text, 40) == "Перше речення. Друге речення довше."
    assert whole_sentences(text, 20) == "Перше речення."
    assert whole_sentences("Дуже довге єдине речення без можливості вмістити.", 10) is None
    assert whole_sentences("Без крапки в кінці", 100) == "Без крапки в кінці"  # цілий опис, не обрізка
    assert whole_sentences("Перше. Друге без крапки в кінці", 20) == "Перше."


def test_check_channels_reports_ok_empty_and_error(make_ctx, tmp_path):
    from lutserii.sources import tgchannel

    page = ('<div class="tgme_widget_message" data-post="ok_chan/5"><div class="tgme_widget_message_text">x</div>'
            '<time datetime="2026-10-27T14:00:00+00:00"></time></div>')
    routes = {
        "https://t.me/s/ok_chan": page,
        "https://t.me/s/empty_chan": "<html></html>",
        "https://t.me/s/bad_chan": FakeResponse("", 404),
    }
    path = tmp_path / "ch.txt"
    path.write_text("ok_chan\nempty_chan\nbad_chan\n", encoding="utf-8")
    ctx, _, _ = make_ctx(routes, dub_channels_path=str(path))
    results, report = app.check_channels(ctx)
    assert [r["ok"] for r in results] == [True, True, False]
    assert "✅ ok_chan" in report and "⚠️ empty_chan" in report and "❌ bad_chan" in report and "404" in report
