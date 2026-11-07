import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def test_choose_track_filters_topics_and_does_not_repeat(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    import make_channel_short as m

    for name in ("a.mp3", "b.mp3", "c.mp3"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "tracks.json").write_text('{"a.mp3": {"topics": ["games"]}, "b.mp3": {"topics": ["anime", "series"]}}')
    monkeypatch.setattr(m, "MUSIC_DIR", tmp_path)
    day = date(2026, 10, 30)
    assert m.choose_track("games", None, day).name in ("a.mp3", "c.mp3")  # b тільки для аніме й серіалів
    assert m.choose_track("anime", None, day).name in ("b.mp3", "c.mp3")
    for i in range(6):  # той самий трек два дні поспіль не береться
        first = m.choose_track("series", None, day + timedelta(i))
        assert m.choose_track("series", first.name, day + timedelta(i)).name != first.name


def test_best_start_finds_the_loud_part(tmp_path):
    pytest.importorskip("numpy")
    if not shutil.which("ffmpeg"):
        pytest.skip("немає ffmpeg")
    import make_channel_short as m

    wav = tmp_path / "t.wav"
    # 12 с тиші з тихим шумом, 12 с гучного тону, 12 с тиші
    expr = "aevalsrc=0.01*random(0)|0.01*random(1):d=12 [a]; sine=f=440:d=12 [b]; aevalsrc=0.01*random(0)|0.01*random(1):d=12 [c]"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-filter_complex", f"{expr}; [a][b][c]concat=n=3:v=0:a=1", "-t", "36", str(wav)],
                   check=True)
    start = m.best_start(wav, 8.0)
    assert 11 <= start <= 20.5


def test_short_titles_are_clean():
    pytest.importorskip("numpy")
    import daily_short as d

    assert d.title_of("🎁 <b>Безкоштовно: Hexologic (Steam)</b>") == "Hexologic"
    assert d.title_of("🔥 <b>Серія Sniper Elite: знижки до 92% на 2 гри</b>") == "Sniper Elite"
    assert d.title_of("🆓 <b>Безкоштовні вихідні в Xbox: Free Play Days</b>") == "Free Play Days"
    assert d.title_of("🔥 <b>Marvels Midnight Suns Digital Edition — знижка 82%</b>") == "Marvels Midnight Suns"
    assert d.title_of("🔥 <b>Exoprimal: видання Deluxe — знижка 80%, тепер ₴459,79</b>") == "Exoprimal"
    assert d.title_of("🔥 <b>Gotham Knights: Deluxe — знижка 90%</b>") == "Gotham Knights"
    assert d.title_of("🚀 <b>Сьогодні вийшла: Phantom Blade Zero</b>") == "Phantom Blade Zero"
    assert d.title_of("🎁 <b>Безкоштовно (додатковий вміст): Dungeons And Dragons Online: Bigby’s Better Gift Coupon</b>") == "Dungeons And Dragons Online"
    assert d.title_of("🎬 <b>Монолог травниці (The Apothecary Diaries Season 3)</b>") == "Монолог травниці"
    assert d.title_of("🎙 <b>Істинне кохання: Альтернатива. Повне затемнення</b>") == "Істинне кохання: Альтернатива"


def test_privacy_switches_to_public_from_the_start_date():
    pytest.importorskip("numpy")
    import daily_short as d

    assert d.privacy_for(date(2026, 11, 1)) == "private"
    assert d.privacy_for(date(2026, 11, 2)) == "public"
    assert d.privacy_for(date(2026, 12, 1)) == "public"
    assert d.privacy_for(date(2026, 12, 1), "unlisted") == "unlisted"  # явна змінна головніша
    assert d.privacy_for(date(2026, 11, 1), None, "2026-10-30") == "public"


def test_timing_keeps_short_between_20_and_30_seconds():
    pytest.importorskip("numpy")
    import make_channel_short as m

    for n in range(2, m.MAX_POSTS + 1):
        for chars in (30, 250, 900):
            msgs = [("x" * chars, None)] * n
            holds, outro = m.timing(msgs)
            total = sum(holds) + (n - 1) * m.MOVE + outro
            assert 19.5 <= total <= 30.5, (n, chars, total)
            assert all(m.HOLD_MIN - 0.01 <= h <= m.HOLD_MAX + 0.01 for h in holds)


def test_tiktok_caption_is_ready_to_paste():
    import daily_short as ds
    texts = ["🎁 <b>Безкоштовно: Hexologic (Steam)</b>", "🔥 <b>Gotham Knights: Deluxe — знижка 90%</b>"]
    caption = ds.tiktok_caption("games", texts, "Музика: Autor")
    assert caption.startswith("Знижки на ігри: Hexologic, Gotham Knights")
    assert "https://t.me/lut_i_serii" in caption and "#ігри" in caption and "#українською" in caption
    assert caption.endswith("Музика: Autor")
    assert len(caption) <= 900


def test_arrange_keeps_same_image_posts_apart():
    import daily_short as ds
    card = {"id": "card", "photos": ["a"]}
    digest = {"id": "digest", "photos": ["a"]}
    other = {"id": "other", "photos": ["c"]}
    order = [i["id"] for i, _ in ds.arrange([card, digest, other])]
    assert order == ["card", "other", "digest"]
    # у добірки є запасна обкладинка: беремо її, пост не лишається без картинки
    digest2 = {"id": "digest", "photos": ["a", "b"]}
    pairs = ds.arrange([card, digest2])
    assert [ph for _, ph in pairs] == ["a", "b"]
    # лише два пости з однаковою єдиною картинкою: картинка є в обох
    assert all(ph == "a" for _, ph in ds.arrange([card, digest]))


def test_free_games_short_is_separate_from_deals():
    pytest.importorskip("numpy")
    import daily_short as ds
    import make_channel_short as m
    from lutserii import published

    assert set(published.TOPIC_KINDS["deals"]).isdisjoint(published.TOPIC_KINDS["free"])
    assert set(published.TOPIC_KINDS["deals"]) | set(published.TOPIC_KINDS["free"]) == set(published.TOPIC_KINDS["games"])
    assert ds.KINDS_TOPIC["games"] == "deals" and "free" in m.TOPICS
    assert ds.make_title("free", ["🎁 <b>Безкоштовно: Hexologic (Steam)</b>"], "2 листопада").startswith("Безкоштовні ігри сьогодні: Hexologic")


def test_choose_track_skips_recent_list_and_varies(tmp_path, monkeypatch):
    pytest.importorskip("numpy")
    import make_channel_short as m

    for name in ("a.mp3", "b.mp3", "c.mp3", "d.mp3"):
        (tmp_path / name).write_bytes(b"x")
    monkeypatch.setattr(m, "MUSIC_DIR", tmp_path)
    day = date(2026, 11, 3)
    for i in range(10):
        assert m.choose_track("games", ["a.mp3", "b.mp3"], day + timedelta(i)).name in ("c.mp3", "d.mp3")
    picks = {m.choose_track("games", None, day + timedelta(i)).name for i in range(20)}
    assert len(picks) >= 3  # не один і той самий трек щодня
    assert m.choose_track("games", ["a.mp3", "b.mp3", "c.mp3", "d.mp3"], day).name != "d.mp3"  # усі були: не найостанніший


def test_best_start_varies_and_avoids_used_segments(tmp_path):
    pytest.importorskip("numpy")
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg")
    import make_channel_short as m

    wav = tmp_path / "t.wav"  # три однаково гучні шматки з тишею між ними
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "aevalsrc='if(lt(mod(t,30),10),0.8*sin(2*PI*220*t),0.01*sin(2*PI*220*t))':d=90:s=11025", str(wav)], check=True)
    first = m.best_start(wav, 8.0)
    again = m.best_start(wav, 8.0, avoid=[first])
    assert abs(again - first) >= 4  # той самий відрізок вдруге не береться
    starts = {m.best_start(wav, 8.0, vary=True) for _ in range(30)}
    assert len(starts) >= 2


DEAL_POST = """🔥 <b>Soundfall — знижка 95%, тепер ₴68 у Humble Store</b>

Soundfall — це рольовий підземельний бойовик, де кожна дія прив&#x27;язана до ритму. Граєте самі або з друзями, до чотирьох гравців локально чи онлайн. Збирайте здобич і б&#x27;ються точно в такт.

<blockquote>💰 <s>$29.99</s> → <b>$1.49</b> (−95%) · ≈ ₴68 за курсом НБУ
🏪 Humble Store
⭐ Відгуки в Steam: 73%</blockquote>

⏳ До 9 листопада, 20:00
🏆 Історичний мінімум ціни

👉 <a href="https://example.com">Забрати в Humble Store</a>

#знижка #HumbleStore
@lut_i_serii"""


def test_parse_post_deal_has_short_overlay_fields():
    pytest.importorskip("numpy")
    import make_channel_short as m

    p = m.parse_post(DEAL_POST)
    assert (p["title"], p["pct"], p["price"], p["old"], p["alt"]) == ("Soundfall", "−95%", "₴68", "$29.99", "$1.49")
    assert p["kicker"] == "🔥 Знижка · Humble Store"
    assert len(p["blurb"]) <= m.BLURB_CHARS + 1 and "прив'язана" in p["blurb"]
    assert p["chips"] == ["⭐ Відгуки в Steam: 73%", "⏳ До 9 листопада, 20:00"]  # лише головне: більше тексту — менше картинки
    html = m.slide_html(p)
    assert "забрати" not in html.lower() and "#знижка" not in html and "lut_i_serii" not in html  # посилання й хештеги не на кадрі
    assert "прив'язана" not in html and "blurb" not in html  # опис гри на кадрі не показується: він закриває героїв


def test_parse_post_free_digest_and_series():
    pytest.importorskip("numpy")
    import make_channel_short as m

    free = m.parse_post("🎁 <b>Безкоштовно (додатковий вміст): Super Animal Royale Bundle Key</b>\n\n<blockquote>🖥 PC</blockquote>\n\n👉 <a href=\"x\">Забрати</a>")
    assert free["free"] and free["title"] == "Super Animal Royale Bundle Key" and "додатковий вміст" in free["kicker"]
    digest = m.parse_post("🟢 <b>Нове в Xbox Game Pass</b>\n\nНезабаром у каталозі:\n\n<blockquote>— Escape Academy 2\n— Thronefall\n— Warhammer Survivors</blockquote>\n\n#GamePass")
    assert digest["items"] == ["Escape Academy 2", "Thronefall", "Warhammer Survivors"] and digest["sub"] == "Незабаром у каталозі"
    series = m.parse_post("📺 <b>Кохання під прикриттям <i>(나의 유죄인간)</i></b>\n\nКан Дже-хі стежить за кожним його кроком.\n\n<blockquote>🆕 Вийшла: 1 сезон, 6 серія</blockquote>")
    assert series["title"] == "Кохання під прикриттям"  # ієрогліфів на кадрі немає
    dub = m.parse_post("🎙 <b>Озвучка українською · 4 листопада</b>\n\n<blockquote>🔹 <b>Загублені</b>\n🔹 <b>Вітролом</b></blockquote>")
    assert dub["kicker"] == "🎙 Щойно вийшло" and dub["sub"] == "4 листопада" and len(dub["items"]) == 2


def test_art_candidates_prefer_big_vertical_images():
    pytest.importorskip("numpy")
    import make_channel_short as m

    steam = "https://cdn.cloudflare.steamstatic.com/steam/apps/1608700/header.jpg"
    got = m.art_candidates([steam, "https://x/shot.jpg"])
    assert got[0].endswith("/apps/1608700/library_600x900_2x.jpg") and got[1:] == [steam, "https://x/shot.jpg"]
    assert m.art_candidates(["https://s4.anilist.co/file/cover/large/a.jpg"])[0].endswith("/cover/extraLarge/a.jpg")
    assert m.cover_scale(1200, 1800) < m.COVER_MAX_UPSCALE < m.cover_scale(460, 215)  # заголовок Steam замалий на весь екран


def test_vertical_art_is_shown_whole_not_cropped():
    pytest.importorskip("numpy")
    from PIL import Image
    import make_channel_short as m

    assert m.art_mode(600, 900) == "poster" and m.art_mode(400, 400) == "poster"
    assert m.art_mode(1920, 1080) == "wide" and m.art_mode(460, 215) == "wide"
    assert m.wide_scale(1920, 1080) < 1 and m.wide_scale(460, 215) < 3  # широка велика не обрізається сильніше за чверть ширини
    im = Image.new("RGB", (600, 900), (10, 10, 10))
    for x in range(600):  # «логотип» на всю ширину внизу обкладинки
        for y in range(780, 840):
            im.putpixel((x, y), (255, 255, 255))
    frame = m.compose_art(im, "poster", m.palette(im))
    assert frame.size == (m.ART_W, m.ART_H)
    row = m.POSTER_TOP + round(810 * m.POSTER_W / 600)
    assert frame.getpixel((6, row))[0] > 200 and frame.getpixel((m.ART_W - 7, row))[0] > 200  # краї літер на місці, нічого не обрізано
    assert m.compose_art(im, "poster", {"c2": "#102030"}).getpixel((5, 5)) == (16, 32, 48)  # зверху фон у тон картинки


def test_palette_follows_the_picture_colours():
    pytest.importorskip("numpy")
    from PIL import Image
    import make_channel_short as m

    orange = m.palette(Image.new("RGB", (60, 90), (230, 120, 20)))
    assert orange["c1"] > orange["c2"] and orange["c1"][1:3] >= "20" and orange["ac"] != "#ffc43d"  # теплий фон і акцент у тон
    grey = m.palette(Image.new("RGB", (60, 90), (120, 120, 120)))
    assert grey["ac"] == "#ffc43d" and len({grey["c1"][1:3], grey["c1"][3:5], grey["c1"][5:7]}) <= 2  # сіра картинка: нейтральна плашка


DIGEST_A = {"kind": "dub", "ts": 3, "photos": ["a1", "a2"], "text": "🎙 <b>Озвучка українською · 4 листопада</b>\n\n✅ <b>Щойно вийшло</b>\n\n<blockquote>🔹 <b>Загублені</b> — 1 сезон\n🔹 <b>Вітролом</b></blockquote>\n\n#озвучка"}
DIGEST_B = {"kind": "dub", "ts": 2, "photos": ["a2", "b1"], "text": "🎙 <b>Озвучка українською · 4 листопада</b>\n\n<blockquote>🔹 <b>Вітролом</b>\n🔹 <b>Слідопит</b> — 4 сезон, 5 серія</blockquote>"}
CARD = {"kind": "dub-card", "ts": 1, "photos": ["c1"], "text": "🎙 <b>Чорна Конюшина</b>\n\nОпис.\n\n<blockquote>✅ Вийшла озвучка</blockquote>"}


def test_digests_of_the_day_merge_into_one_frame_and_name_the_titles():
    import daily_short as ds

    merged = ds.merge_digests([DIGEST_A, CARD, DIGEST_B])
    assert [i["kind"] for i in merged] == ["dub", "dub-card"]
    assert merged[0]["text"].count("🔹") == 3 and merged[0]["photos"] == ["a1", "a2", "b1"]  # без повторів, по порядку
    assert ds.merge_digests([DIGEST_A, CARD]) == [DIGEST_A, CARD]  # одна добірка лишається як є
    names = ds.names_of([DIGEST_A["text"], CARD["text"]])
    assert names[:3] == ["Загублені", "Вітролом", "Чорна Конюшина"]  # у назві ролика тайтли, а не «Озвучка українською»
    assert "Озвучка українською" not in ds.make_title("series", [DIGEST_A["text"], CARD["text"]], "4 листопада")
