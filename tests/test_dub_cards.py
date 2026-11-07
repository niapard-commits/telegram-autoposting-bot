from datetime import date

from conftest import FakeSession  # noqa: F401
from lutserii import posts
from lutserii.dubs import Release
from lutserii.main import find_media, run_dubs
from lutserii.sources import anilist
from lutserii.sources.anilist import AnimeInfo
from test_dubs import StubEditor, channels_file, make, raw  # noqa: F401

SEARCH = {"data": {"Page": {"media": [
    {"id": 7, "siteUrl": "https://anilist.co/anime/7", "isAdult": False, "synonyms": [],
     "popularity": 55000, "genres": ["Drama", "Mystery"], "description": "A maid solves court mysteries.",
     "title": {"romaji": "Kusuriya no Hitorigoto", "english": "The Apothecary Diaries"},
     "coverImage": {"extraLarge": "https://img/xl.jpg"}},
]}}}

ANSWER = raw([{"id": "101", "title": "Щоденник медика", "season": 3, "episode": 5,
               "status": "released", "original": "The Apothecary Diaries"}])


class BlurbEditor(StubEditor):
    def __init__(self, answer, blurb="Служниця розплутує загадки імператорського двору."):
        super().__init__(answer)
        self.text = blurb

    def blurb(self, kind, title, source_text, cache_key):
        return self.text


def _ctx(make_ctx, channels_file, editor_blurb=True, **kw):
    ctx, sent, logs = make(make_ctx, answer=ANSWER, dub_channels_path=channels_file, **kw)
    ctx.session.routes[anilist.API] = SEARCH
    if editor_blurb:
        ctx.editor = BlurbEditor(ANSWER)
    return ctx, sent, logs


def test_find_media_returns_description_and_caches(make_ctx):
    ctx, _, _ = make_ctx({anilist.API: SEARCH})
    info = find_media(ctx, "The Apothecary Diaries")
    assert info.id == 7 and info.popularity == 55000 and info.genres == ("Drama", "Mystery")
    assert "court mysteries" in info.description and info.url.endswith("/7")
    assert find_media(ctx, "The Apothecary Diaries") == info
    assert len(ctx.session.calls) == 1


def test_run_dubs_posts_digest_then_card(make_ctx, channels_file):
    ctx, sent, _ = _ctx(make_ctx, channels_file, chat_id="@lut_i_serii", dub_card_links=True)
    assert run_dubs(ctx) == 1
    assert len(sent) == 2
    digest, card = sent
    assert digest[1] == ["https://img/xl.jpg"] and "Озвучка українською" in digest[0]
    assert card[1] == ["https://img/xl.jpg"]
    assert "Служниця розплутує" in card[0] and "3 сезон, 5 серія" in card[0]
    assert '🎧 Озвучка: <a href="https://t.me/DaliBudeDub">Дали буде дуб</a>' in card[0] and "Дивитись озвучку" not in card[0] and "Драма" in card[0]
    assert "https://t.me/DaliBudeDub" in card[0] and "https://anilist.co/anime/7" in card[0]
    assert run_dubs(ctx) == 0 and len(sent) == 2  # вдруге нічого


def test_card_skipped_without_blurb(make_ctx, channels_file):
    ctx, sent, _ = _ctx(make_ctx, channels_file, editor_blurb=False)
    ctx.editor.blurb = lambda *a, **k: None
    assert run_dubs(ctx) == 1
    assert len(sent) == 1  # лише добірка: картка без опису не виходить


def test_highlights_limit_and_zero(make_ctx, channels_file):
    ctx, sent, _ = _ctx(make_ctx, channels_file, dub_highlights=0)
    run_dubs(ctx)
    assert len(sent) == 1


def test_digest_fits_caption_limit():
    items = [Release(f"Дуже довга назва проєкту номер {i} з гучним продовженням", 2, i, team=f"Команда {i % 3}")
             for i in range(1, 16)]
    text, rel, up = posts.dub_digest_fit(items, [], date(2026, 10, 28), "@lut_i_serii")
    assert posts.visible_len(text) <= 1000
    assert 0 < len(rel) < len(items)
    assert {r.team for r in rel} == {"Команда 0", "Команда 1", "Команда 2"}  # кожна команда лишилася в добірці
    all_text, rel2, _ = posts.dub_digest_fit(items[:2], [], date(2026, 10, 28))
    assert rel2 == items[:2]


def test_dub_card_announced_without_links():
    r = Release("Назва", None, 4, status="announced", date="2026-10-28", time="18:00",
                team="Starfall", channel="starfall_ua")
    text = posts.dub_card(r, AnimeInfo(1, "c", genres=("Action",)), "Опис.", "@x", links=False)
    assert "🕒 Сьогодні о 18:00: 4 серія" in text and "t.me" not in text and "Екшн" in text


def test_long_dub_titles_are_shortened_in_digest():
    long = Release("Божевільна Ніа Лістон: історія непереможної боговбивці, яка переродилася у хворобливу панянку", None, 4, team="Т")
    text = posts.dub_digest([long], [], date(2026, 10, 28))
    assert "…</b> — 4 серія" in text and "панянку" not in text


def test_dub_extraction_goes_to_gemini_with_claude_as_backup(make_ctx, channels_file):
    class Down(StubEditor):
        def ask(self, *a, **k):
            return None

    ctx, _, logs = _ctx(make_ctx, channels_file)
    gemini = StubEditor(ANSWER)
    ctx.extractor = gemini
    from lutserii.main import extract_dubs
    posts_ = [object()]
    import lutserii.main as m
    seen = []
    real = m.dubs.extract
    m.dubs.extract = lambda editor, fresh, tz: seen.append(editor) or ({1: []} if editor is gemini else None)
    try:
        assert extract_dubs(ctx, posts_, None) == {1: []} and seen == [gemini]
        seen.clear()
        ctx.extractor = Down(ANSWER)
        m.dubs.extract = lambda editor, fresh, tz: seen.append(editor) or ({2: []} if editor is ctx.editor else None)
        assert extract_dubs(ctx, posts_, None) == {2: []} and seen == [ctx.extractor, ctx.editor]
        assert any("Gemini не вдався" in line for line in logs)
    finally:
        m.dubs.extract = real
