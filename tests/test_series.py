from datetime import datetime, timezone
from lutserii import posts
from lutserii.dubs import Release
from lutserii.main import dub_tags, find_media
from lutserii.sources import anilist, tvmaze

SHOW = {"id": 82, "name": "Game of Thrones", "url": "http://www.tvmaze.com/shows/82/game-of-thrones",
        "weight": 98, "genres": ["Drama", "Adventure", "Fantasy"],
        "summary": "<p>Nine noble families fight for control of the lands of Westeros.</p>",
        "image": {"medium": "http://static.tvmaze.com/m.jpg", "original": "http://static.tvmaze.com/o.jpg"}}
NO_ANIME = {"data": {"Page": {"media": []}}}


def test_pick_show_exact_title_only_and_https():
    info = tvmaze.pick_show(SHOW, "Game of Thrones")
    assert info.kind == "series" and info.id == 82 and info.popularity == 98 * tvmaze.WEIGHT_SCALE
    assert info.cover == "https://static.tvmaze.com/o.jpg" and info.url.startswith("https://")
    assert info.description == "Nine noble families fight for control of the lands of Westeros."
    assert tvmaze.pick_show(SHOW, "Game of Thrones Prequel") is None  # лише точна назва
    assert tvmaze.pick_show({}, "X Show") is None and tvmaze.pick_show("oops", "X Show") is None


def test_find_media_falls_back_to_tvmaze_and_caches(make_ctx):
    ctx, _, _ = make_ctx({anilist.API: NO_ANIME, tvmaze.API: SHOW})
    info = find_media(ctx, "Game of Thrones")
    assert info and info.kind == "series"
    assert find_media(ctx, "Game of Thrones") == info
    assert len(ctx.session.calls) == 2  # вдруге з кешу


def test_series_card_has_series_labels_and_tags():
    info = tvmaze.pick_show(SHOW, "Game of Thrones")
    r = Release(title="Гра престолів", season=1, episode=3, team="Дали буде дуб", channel="DaliBudeDub")
    text = posts.dub_card(r, info, "Знатні роди борються за владу.", "@lut_i_serii", True)
    assert "Про серіал" in text and "#серіали" in text and "#аніме" not in text
    assert "Фентезі" in text and "Дали буде дуб" in text
    assert dub_tags([info]) == ("#серіали",)
    assert dub_tags([]) == ("#аніме",)
    assert "#серіали" in posts.dub_digest([r], [], __import__("datetime").date(2026, 10, 28), tags=dub_tags([info]))


def test_site_is_kept_only_when_the_post_names_it():
    from datetime import date
    from lutserii import dubs
    post = "Брати 7 серія із 8 (серіал 2026) Українська озвучка від DubTeam. Дивитись онлайн - https://dubsite.example/serials/brati/"
    items = [{"id": "1", "title": "Брати", "episode": 7, "status": "released", "site": "https://dubsite.example/serials/brati/"},
             {"id": "1", "title": "Брати", "episode": 7, "status": "released", "site": "evil-site.com"},
             {"id": "1", "title": "Брати", "episode": 7, "status": "released", "site": "t.me/x"}]
    import json
    out = dubs.parse_extraction(json.dumps(items), {"1": date(2026, 10, 28)}, {"1": post})["1"]
    assert [r.site for r in out] == ["dubsite.example", None, None]


def test_card_shows_site_name_without_link():
    info = tvmaze.pick_show(SHOW, "Game of Thrones")
    r = Release(title="Брати", season=None, episode=7, team="DubTeam", site="dubsite.example")
    text = posts.dub_card(r, info, "Опис.", "", False)
    assert "🌐 Сайт: dubsite.example" in text and "href" not in text.split("🌐")[1].split("\n")[0]
    assert "🌐" not in posts.dub_card(r, info, "Опис.", "", False, show_site=False)


def test_premium_flag_needs_the_post_to_say_so_and_shows_in_posts():
    import json
    from datetime import date
    from lutserii import dubs
    post = "Закохаю до божевілля 9 серія Всього 10 серій ❗️Для підписників❗️"
    items = [{"id": "1", "title": "Закохаю до божевілля", "episode": 9, "status": "released", "premium": True}]
    out = dubs.parse_extraction(json.dumps(items), {"1": date(2026, 10, 28)}, {"1": post})["1"]
    assert out[0].premium
    plain = "Закохаю до божевілля 9 серія Всього 10 серій"
    assert not dubs.parse_extraction(json.dumps(items), {"1": date(2026, 10, 28)}, {"1": plain})["1"][0].premium
    assert "🔒 для підписників" in posts.dub_digest([out[0]], [], date(2026, 10, 28))


TMDB_SEARCH = {"results": [{"id": 95396, "name": "Тихоокеанський форпост", "original_name": "Severance", "overview": "",
                            "poster_path": "/abc.jpg", "popularity": 120.5, "genre_ids": [18, 9648, 10765]}]}


def test_tmdb_pick_matches_original_or_local_name_only():
    from lutserii.sources import tmdb
    info = tmdb.pick_tv(TMDB_SEARCH, "Severance")
    assert info.kind == "series" and info.cover == "https://image.tmdb.org/t/p/w780/abc.jpg"
    assert info.genres == ("Drama", "Mystery", "Sci-Fi", "Fantasy") and info.url.endswith("/95396")
    assert tmdb.pick_tv(TMDB_SEARCH, "Тихоокеанський форпост").id == 95396
    assert tmdb.pick_tv(TMDB_SEARCH, "Sever") is None and tmdb.pick_tv("x", "Severance") is None


def test_find_media_uses_tmdb_first_with_ukrainian_fallback_title(make_ctx):
    from lutserii.sources import tmdb
    detail = {"overview": "Employees split their memories between work and life."}
    routes = {anilist.API: NO_ANIME, f"{tmdb.API}/search/tv": TMDB_SEARCH, f"{tmdb.API}/tv/95396": detail}
    ctx, _, _ = make_ctx(routes, tmdb_api_key="abc123")
    info = find_media(ctx, "Невідома назва", "Тихоокеанський форпост")
    assert info.id == 95396 and "memories" in info.description  # опис підтягнуто англійський, бо українського немає
    assert all(tvmaze.API != c[0] for c in ctx.session.calls)  # TVMaze не знадобився


def test_tmdb_status_without_and_with_key(make_ctx):
    from lutserii import main
    from lutserii.sources import tmdb
    ctx, _, _ = make_ctx({})
    assert "не підключено" in main.tmdb_status(ctx)
    ctx2, _, _ = make_ctx({f"{tmdb.API}/search/tv": {"results": [{"name": "Пуститися берега"}]}}, tmdb_api_key="k")
    assert "ключ працює" in main.tmdb_status(ctx2)


def test_busy_team_does_not_crowd_out_the_others():
    from datetime import date, datetime, timezone
    from lutserii import dubs
    anime = [Release(title=f"Аніме {i}", episode=i + 1, team="Рідний Голос") for i in range(12)]
    series = [Release(title="Брати", episode=7, team="DubTeam"), Release(title="Квест Віжена", episode=5, team="Цікава Ідея")]
    released, _ = dubs.select_items(anime + series, date(2026, 10, 29), datetime(2026, 10, 29, 12, tzinfo=timezone.utc),
                                    lambda key: False, 6)
    teams = [r.team for r in released]
    assert "DubTeam" in teams and "Цікава Ідея" in teams and len(released) == 6


def test_extract_works_in_chunks_and_keeps_unparsed_posts_for_later():
    import json
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from lutserii import dubs
    from lutserii.sources.tgchannel import ChannelPost
    tz = ZoneInfo("Europe/Kyiv")
    posts_ = [ChannelPost(id=i, text=f"Серіал {i} серія {i}", date=datetime(2026, 10, 28, 10, tzinfo=timezone.utc))
              for i in range(1, 13)]
    calls = []

    class Ed:
        def ask(self, system, prompt, max_tokens, as_json=False):
            calls.append(prompt)
            if len(calls) == 3:
                return None  # ліміт на третій порції
            ids = [int(x) for x in __import__("re").findall(r'id="(\d+)"', prompt)]
            return json.dumps([{"id": str(i), "title": f"Серіал {i}", "episode": i, "status": "released"} for i in ids])

    out = dubs.extract(Ed(), posts_, tz)
    assert len(calls) == 3 and sorted(out) == list(range(1, 9))  # 2 порції по 4; дев'ятий-дванадцятий лишилися на потім
    assert dubs.extract(type("N", (), {"ask": lambda *a, **k: None})(), posts_[:2], tz) is None


def test_channel_file_site_column_and_default_site(tmp_path):
    from lutserii import dubs
    f = tmp_path / "ch.txt"
    f.write_text("# коментар\ndubteam | DubTeam | https://www.dubsite.example/\nother | Інша команда\n", encoding="utf-8")
    assert dubs.load_channels(str(f)) == [("dubteam", "DubTeam"), ("other", "Інша команда")]
    assert dubs.load_channel_sites(str(f)) == {"dubteam": "dubsite.example"}
    rel = [Release(title="Брати", episode=7), Release(title="Інший", episode=1, site="othersite.example")]
    out = dubs.with_team(rel, "DubTeam", "dubteam", "dubsite.example")
    assert [r.site for r in out] == ["dubsite.example", "othersite.example"]


def test_premium_is_set_by_the_post_wording_not_by_the_model():
    import json
    from datetime import date
    from lutserii import dubs
    d = {"1": date(2026, 10, 28)}
    item = [{"id": "1", "title": "Брати", "episode": 7, "status": "released"}]
    sub = dubs.parse_extraction(json.dumps(item), d, {"1": "Брати 7 серія ❗️Для підписників❗️"})["1"][0]
    donate = dubs.parse_extraction(json.dumps(item), d, {"1": "Брати 7 серія. Підтримай нас, стань підписником на Boosty"})["1"][0]
    assert sub.premium and not donate.premium


def test_truncated_answer_splits_the_chunk_in_half():
    import json
    import re
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    from lutserii import dubs
    from lutserii.sources.tgchannel import ChannelPost
    posts_ = [ChannelPost(id=i, text=f"Серіал {i} серія {i}", date=datetime(2026, 10, 28, 10, tzinfo=timezone.utc))
              for i in range(1, 5)]

    class Ed:
        last_error = ""

        def __init__(self):
            self.sizes = []

        def ask(self, system, prompt, max_tokens, as_json=False):
            ids = [int(x) for x in re.findall(r'id="(\d+)"', prompt)]
            self.sizes.append(len(ids))
            if len(ids) > 2:
                self.last_error = "TruncatedOutput"
                return None
            self.last_error = ""
            return json.dumps([{"id": str(i), "title": f"Серіал {i}", "episode": i, "status": "released"} for i in ids])

    ed = Ed()
    out = dubs.extract(ed, posts_, ZoneInfo("Europe/Kyiv"))
    assert ed.sizes == [4, 2, 2] and sorted(out) == [1, 2, 3, 4]


def test_releases_with_covers_come_first_inside_a_team_and_series_first():
    from datetime import date, datetime, timezone
    from lutserii import dubs
    rel = [Release(title="Невідоме", episode=1, team="DubTeam"), Release(title="Аніме", episode=2, team="DubTeam"),
           Release(title="Серіал", episode=3, team="DubTeam")]
    score = {"Невідоме": 0, "Аніме": 1, "Серіал": 2}
    out, _ = dubs.select_items(rel, date(2026, 10, 29), datetime(2026, 10, 29, 12, tzinfo=timezone.utc),
                               lambda k: False, 2, lambda r: score[r.title])
    assert [r.title for r in out] == ["Серіал", "Аніме"]


AIRING = {"results": [
    {"id": 1, "popularity": 300.0, "genre_ids": [18], "original_language": "en"},
    {"id": 4, "popularity": 500.0, "genre_ids": [18], "original_language": "ja"},   # інша мова оригіналу
    {"id": 2, "popularity": 900.0, "genre_ids": [10767], "original_language": "en"},   # ток-шоу відсікаємо
    {"id": 3, "popularity": 10.0, "genre_ids": [18], "original_language": "en"},       # непопулярний
]}


def _detail(show_id, air):
    return {"name": "Пінгвін", "original_name": "The Penguin", "overview": "Кримінальна історія.", "popularity": 300,
            "poster_path": "/p.jpg", "genres": [{"id": 80}, {"id": 18}], "networks": [{"name": "HBO"}],
            "last_episode_to_air": {"season_number": 1, "episode_number": 5, "name": "Хто там", "air_date": air}}


def _series_ctx(make_ctx, air, **kw):
    from lutserii.sources import tmdb
    routes = {f"{tmdb.API}/tv/airing_today": AIRING, f"{tmdb.API}/tv/1": _detail(1, air)}
    return make_ctx(routes, tmdb_api_key="k", **kw)


def test_run_series_posts_popular_new_episode_with_dub_info(make_ctx):
    from lutserii import main
    ctx, sent, _ = _series_ctx(make_ctx, "2026-10-29")
    ctx.clock = lambda: datetime(2026, 10, 29, 12, tzinfo=timezone.utc).timestamp()
    ctx.db.set_cache("dubteam:dubteam", "DubTeam")
    ctx.db.set_cache("dubpost3:dubteam:10", '[{"title": "Пінгвін", "season": 1, "episode": 4, "status": "released", "original": "The Penguin", "site": "dubsite.example"}]')
    assert main.run_series(ctx) == 1
    text, photo = sent[0]
    assert "Пінгвін" in text and "The Penguin" in text and "1 сезон, 5 серія" in text and "HBO" in text
    assert "🎧 Українською: DubTeam, 1 сезон, 4 серія (dubsite.example)" in text.replace('<a href="https://t.me/dubteam">DubTeam</a>', "DubTeam") and "#серіали" in text
    assert photo == ["https://image.tmdb.org/t/p/w780/p.jpg"]
    assert main.run_series(ctx) == 0  # вдруге ту саму серію не публікуємо


def test_run_series_skips_old_episodes_and_without_key(make_ctx):
    from lutserii import main
    ctx, sent, _ = _series_ctx(make_ctx, "2026-10-20")
    ctx.clock = lambda: datetime(2026, 10, 29, 12, tzinfo=timezone.utc).timestamp()
    assert main.run_series(ctx) == 0 and sent == []
    none_ctx, _, _ = make_ctx({})
    assert main.run_series(none_ctx) == 0


def test_series_without_ukrainian_dub_is_not_posted(make_ctx):
    from lutserii import main
    ctx, sent, _ = _series_ctx(make_ctx, "2026-10-29")
    ctx.clock = lambda: datetime(2026, 10, 29, 12, tzinfo=timezone.utc).timestamp()
    assert main.run_series(ctx) == 0 and sent == []  # без озвучки пост не потрібен
    assert main.dub_lines_for(ctx, {"thepenguin"}) == []


def test_upcoming_episode_dated_today_is_preferred_over_stale_last_episode():
    from lutserii.sources import tmdb
    from conftest import FakeSession
    detail = _detail(1, "2026-10-20")
    detail["next_episode_to_air"] = {"season_number": 1, "episode_number": 6, "name": "Новий", "air_date": "2026-10-29"}
    session = FakeSession({f"{tmdb.API}/tv/1": detail})
    show = tmdb.fetch_airing_show(session, "k", 1, "2026-10-29")
    assert show.episode == 6 and show.air_date == "2026-10-29"
    assert tmdb.fetch_airing_show(session, "k", 1, "2026-10-25").episode == 5  # майбутня ще не вийшла










def test_dub_card_for_site_detected_release_names_site_only():
    r = Release(title="Тед Лассо", team="DubTeam", site="DubTeam", batch="2026-10-29")
    info = anilist.AnimeInfo(id=1, cover="https://x/c.jpg", popularity=1, genres=("Comedy",), description="d", url="", title="Ted Lasso", kind="series")
    text = posts.dub_card(r, info, "Опис.", "")
    assert "Нова серія українською" in text and "🌐 Дивитись: DubTeam" in text and "Озвучка:" not in text









