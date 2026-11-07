from conftest import FakeResponse

from lutserii import channel
from lutserii.channel import run_setup
from lutserii.sources import gamerpower


class Bot:
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls = []

    def post(self, url, json=None, timeout=None, **kwargs):
        method = url.rsplit("/", 1)[1]
        self.calls.append((method, json))
        if method in self.fail:
            return FakeResponse({"ok": False, "description": "not enough rights"}, 400)
        return FakeResponse({"ok": True, "result": {"message_id": 77}})


def _ctx(make_ctx, bot, **cfg):
    ctx, _, logs = make_ctx({}, bot_token="T", chat_id="@lut_i_serii", **cfg)
    ctx.session = bot
    return ctx, logs


def test_description_fits_telegram_limit_and_pinned_post_has_tmdb_notice():
    assert len(channel.DESCRIPTION) <= 255
    assert "This product uses the TMDB API but is not endorsed or certified by TMDB." in channel.PINNED_TEXT


def test_setup_sets_description_pins_post_and_remembers_it(make_ctx):
    bot = Bot()
    ctx, _ = _ctx(make_ctx, bot)
    assert run_setup(ctx) == 2
    assert [m for m, _ in bot.calls] == ["setChatDescription", "sendMessage", "pinChatMessage"]
    assert bot.calls[2][1]["message_id"] == 77 and bot.calls[2][1]["disable_notification"] is True
    bot.calls.clear()
    assert run_setup(ctx) == 2  # вдруге пост редагуємо, а не дублюємо
    assert [m for m, _ in bot.calls] == ["setChatDescription", "editMessageText"]


def test_setup_reports_missing_rights_without_crashing(make_ctx):
    bot = Bot(fail={"setChatDescription", "sendMessage"})
    ctx, logs = _ctx(make_ctx, bot)
    assert run_setup(ctx) == 0
    assert any("право" in line for line in logs)


def test_setup_dry_run_sends_nothing(make_ctx):
    bot = Bot()
    ctx, logs = _ctx(make_ctx, bot, dry_run=True)
    assert run_setup(ctx) == 0 and bot.calls == []


def test_loot_giveaway_is_marked_and_capped(make_ctx, monkeypatch):
    from lutserii import main, posts
    from zoneinfo import ZoneInfo
    mk = lambda i, kind: gamerpower.Giveaway(i, f"G{i}", "N/A", "PC", "N/A", f"2026-10-2{i}", f"https://g/{i}", None, "", kind)
    monkeypatch.setattr(gamerpower, "fetch_giveaways",
                        lambda s, kind="game": [mk(1, "game")] if kind == "game" else [mk(2, "loot"), mk(3, "loot")])
    ctx, _, _ = make_ctx({}, max_free_per_run=4)
    ctx.editor = None
    sent = []
    ctx.publish = lambda kind, keys, text, photos=None, video=None: sent.append((keys, text))
    monkeypatch.setattr(main, "enrich_giveaway", lambda c, g: ([], ()))
    main.run_free(ctx)
    keys = [k[0] for k, _ in sent]
    assert keys == ["free:1", "free:3"] or keys == ["free:1", "free:2"]  # одна гра й лише один додатковий вміст
    assert "додатковий вміст" in sent[1][1] and "#лут" in sent[1][1]
    assert "додатковий вміст" not in sent[0][1]


def test_avatar_uploads_file_and_reports_errors(make_ctx, tmp_path, monkeypatch):
    from lutserii.channel import run_avatar
    monkeypatch.chdir(tmp_path)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "avatar.png").write_bytes(b"\x89PNG fake")

    class Bot2:
        def __init__(self, ok=True):
            self.ok, self.calls = ok, []

        def post(self, url, data=None, files=None, json=None, timeout=None, **kw):
            self.calls.append((url.rsplit("/", 1)[1], data, files))
            return FakeResponse({"ok": True} if self.ok else {"ok": False, "description": "no rights"}, 200 if self.ok else 400)

    ctx, _ = _ctx(make_ctx, Bot2())
    assert run_avatar(ctx) == 1
    method, data, files = ctx.session.calls[0]
    assert method == "setChatPhoto" and data["chat_id"] == "@lut_i_serii" and files["photo"][0] == "avatar.png"
    bad, _ = _ctx(make_ctx, Bot2(ok=False))
    try:
        run_avatar(bad)
    except RuntimeError as exc:
        assert "no rights" in str(exc)
    else:
        raise AssertionError("очікували помилку")
