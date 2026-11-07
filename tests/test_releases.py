from datetime import date

from lutserii import posts, published
from lutserii.sources import releases as R

WIKI = """
== List ==
{| class="wikitable sortable"
|-
! Release date
! Title
! Platform(s)
! Type(s)
! Genre(s)
! Developer(s)
! Publisher(s)
! {{refh}}
|- id="October"
| October 29
| ''[[Phantom Blade Zero]]''
| WIN, PS5
| Original
| Action
| [[S-GAME]]
| S-GAME
| <ref>{{cite web|url=x}}</ref>
|-
| rowspan="2" | October 30
| ''[[Big Port]]''
| WIN, PS5
| Port
| RPG
| A
| B
|
|-
| ''[[Console Exclusive]]''
| PS5
| Original
| Action
| C
| D
|
|-
| October 30
| ''Indie Thing'' (JP)
| WIN
| Original
| Puzzle
| E
| F
|
|-
| October 30
| ''[[Switch Only]]''
| NS2
| Original
| RPG
| G
| H
|
|-
| October 30
| ''[[Obscure Game]]''
| WIN, XBX/S
| Original
| RPG
| G
| H
|
|}
"""


def test_parse_handles_rowspan_ports_regions_and_switch():
    got = {r.title: r for r in R.parse_wikitext(WIKI, 2026)}
    assert set(got) == {"Phantom Blade Zero", "Console Exclusive", "Obscure Game"}  # порт, регіональний і Switch відсіяно
    assert got["Phantom Blade Zero"].day == date(2026, 10, 29) and got["Phantom Blade Zero"].platforms == ("ПК", "PS5")
    assert got["Console Exclusive"].day == date(2026, 10, 30)  # дата з rowspan попереднього рядка
    assert got["Phantom Blade Zero"].notable


def test_only_anticipated_releases_are_picked():
    items = R.parse_wikitext(WIKI, 2026)
    popular = {R.norm_title(R.base_title("Phantom Blade Zero"))}
    assert [r.title for r in R.today_releases(items, date(2026, 10, 29), popular)] == ["Phantom Blade Zero"]
    # консольний ексклюзив-оригінал береться й без Steam; невідома кросплатформна гра ні
    assert [r.title for r in R.today_releases(items, date(2026, 10, 30), popular)] == ["Console Exclusive"]
    week = R.week_releases(items, date(2026, 10, 26), date(2026, 11, 1), popular)
    assert [r.title for r in week] == ["Phantom Blade Zero", "Console Exclusive"]


def test_posts_render_and_are_in_games_journal():
    text = posts.release_post("Phantom Blade Zero", date(2026, 10, 29), ("ПК", "PS5"), "Екшен.", ("Екшен",), "2 249₴", "https://store.steampowered.com/app/1/", "@lut_i_serii")
    assert "Сьогодні вийшла: Phantom Blade Zero" in text and "ПК · PS5" in text and "#реліз" in text and "#PS5" in text
    week = posts.release_week_post([(date(2026, 11, 3), "A", ("ПК",)), (date(2026, 11, 5), "B", ("PS5", "Xbox Series"))], date(2026, 11, 2), date(2026, 11, 8))
    assert "Релізи тижня: 2–8 листопада" in week and "3 листопада: <b>A</b>" in week
    assert "release" in published.TOPIC_KINDS["games"]


def test_every_task_is_accepted_on_the_command_line():
    import inspect

    from lutserii import main as app

    source = inspect.getsource(app.main)
    for name in app.TASKS:
        assert f'"{name}"' in source, name  # інакше argparse відхилить завдання
