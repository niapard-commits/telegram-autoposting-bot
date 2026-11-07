import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from conftest import FakeResponse

from lutserii import main as app
from lutserii import posts
from lutserii.sources import anilist
from lutserii.titles import load_titles, lookup_title

KYIV = ZoneInfo("Europe/Kyiv")


def write_titles(tmp_path, data):
    path = tmp_path / "titles.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_load_and_lookup(tmp_path):
    path = write_titles(tmp_path, {
        "_note": "ігнорується",
        "108465": "Реінкарнація безробітного",
        "Frieren": "  Фрірен  ",
        "Empty": "   ",
        "Bad": 5,
    })
    titles = load_titles(path)
    assert lookup_title(titles, 108465, "Anything") == "Реінкарнація безробітного"
    assert lookup_title(titles, 1, "frieren") == "Фрірен"  # без урахування регістру
    assert lookup_title(titles, 1, "Empty") is None
    assert lookup_title(titles, 1, "_note") is None


def test_load_titles_missing_or_broken(tmp_path):
    assert load_titles(str(tmp_path / "nope.json")) == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{не json", encoding="utf-8")
    assert load_titles(str(broken)) == {}
    listing = tmp_path / "list.json"
    listing.write_text("[1, 2]", encoding="utf-8")
    assert load_titles(str(listing)) == {}


def test_repo_titles_file_is_valid():
    assert load_titles("data/titles_ua.json") == {}  # лише інструкція, назв ще немає


def test_display_title():
    ep = anilist.Episode(1, 1, "Mushoku Tensei", 1, "", ua_title="Реінкарнація безробітного")
    assert ep.display_title == "Реінкарнація безробітного (Mushoku Tensei)"
    assert anilist.Episode(1, 1, "Original", 1, "").display_title == "Original"


def test_streaming_links_parsed_and_filtered():
    media = {"externalLinks": [
        {"site": "Official Site", "url": "https://x/1", "type": "INFO"},
        {"site": "Crunchyroll", "url": "https://crunchyroll.example/1", "type": "STREAMING"},
        {"site": "crunchyroll", "url": "https://crunchyroll.example/2", "type": "STREAMING"},
        {"site": "Insecure", "url": "http://x/2", "type": "STREAMING"},
        {"site": "Netflix", "url": "https://netflix.example/1", "type": "STREAMING"},
        {"site": "Hulu", "url": "https://hulu.example/1", "type": "STREAMING"},
        {"site": "Tubi", "url": "https://tubi.example/1", "type": "STREAMING"},
    ]}
    assert anilist._streaming(media) == (
        ("Crunchyroll", "https://crunchyroll.example/1"),
        ("Netflix", "https://netflix.example/1"),
    )  # Hulu й Tubi не показуємо: в Україні вони зазвичай недоступні


def test_anime_card_shows_streaming_and_ua_title():
    now = int(datetime(2026, 10, 27, 5, 0, tzinfo=timezone.utc).timestamp())
    ep = anilist.Episode(
        now + 3600, 3, "Orig <T>", 1, "https://anilist.co/anime/1", ua_title="Назва",
        streaming=(("Crunchyroll", "https://c.example/1?a=1&b=2"),),
    )
    text = posts.anime_card(ep, KYIV, now)
    assert "Назва (Orig &lt;T&gt;)" in text
    assert '📺 Де дивитись: <a href="https://c.example/1?a=1&amp;b=2">Crunchyroll</a>' in text
    plain = posts.anime_card(anilist.Episode(now + 3600, 3, "T", 1, ""), KYIV, now)
    assert "Де дивитись" not in plain


def test_fetch_airing_falls_back_without_links():
    seen = []

    def route(payload):
        seen.append("externalLinks" in payload["query"])
        if "externalLinks" in payload["query"]:
            return FakeResponse({"errors": [{"message": "Cannot query field"}]}, 400)
        return {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "airingSchedules": [
            {"airingAt": 100, "episode": 1, "media": {
                "id": 1, "siteUrl": "u", "format": "TV", "countryOfOrigin": "JP",
                "isAdult": False, "popularity": 1,
                "title": {"romaji": "R", "english": None, "native": None}}}]}}}

    from conftest import FakeSession

    session = FakeSession({anilist.API: route})
    episodes = anilist.fetch_airing(session, 0, 1)
    assert [e.title for e in episodes] == ["R"]
    assert seen == [True, False]  # одразу після збою пішли без посилань, далі вже без них


def test_fetch_airing_rate_limit_raises():
    from conftest import FakeSession

    session = FakeSession({anilist.API: FakeResponse({"errors": [{"message": "Too Many"}]}, 429)})
    try:
        anilist.fetch_airing(session, 0, 1)
    except RuntimeError as exc:
        assert "забагато" in str(exc)
    else:
        raise AssertionError("очікували RuntimeError")
    assert len(session.calls) == 1


def test_run_anime_uses_titles_and_reports_missing(make_ctx, tmp_path):
    now = datetime(2026, 10, 27, 5, 0, tzinfo=timezone.utc).timestamp()

    def item(offset, ep, popularity, title, media_id):
        return {"airingAt": int(now) + offset, "episode": ep, "media": {
            "id": media_id, "siteUrl": f"https://anilist.co/anime/{media_id}", "format": "TV",
            "countryOfOrigin": "JP", "isAdult": False, "popularity": popularity,
            "title": {"romaji": title, "english": None, "native": None}}}

    page = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "airingSchedules": [
        item(3600, 1, 90000, "Known Show", 1), item(7200, 2, 60000, "Unknown Show", 2)]}}}
    path = write_titles(tmp_path, {"1": "Відома назва"})
    ctx, sent, logs = make_ctx({anilist.API: page}, titles_path=path, anime_highlights=1, anime_digest=True)
    ctx.clock = lambda: now
    assert app.run_anime(ctx) == 2
    assert "Відома назва (Known Show)" in sent[0][0]
    assert "Unknown Show" in sent[0][0]
    missing = [line for line in logs if "Немає української назви" in line]
    assert len(missing) == 1 and "Unknown Show" in missing[0] and "номер 2" in missing[0]


def test_fix_terms_uses_konoha_in_all_cases():
    from lutserii.textutil import fix_terms
    assert fix_terms("Він повертається до Коноги, але Коногу охороняють.") == "Він повертається до Конохи, але Коноху охороняють."
    assert fix_terms("Коноха і Конохи без змін") == "Коноха і Конохи без змін"
