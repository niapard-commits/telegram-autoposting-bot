"""Лабораторія макетів Shorts: для справжніх обкладинок із журналу постів малює кадр у кількох варіантах
і надсилає картинками (не відео) власнику в приват, щоб обрати, як виглядатиме текст на різних обкладинках.

Варіанти:
  P    плашка в колір низу обкладинки (поточна)
  F    обкладинка на весь екран, під текстом лише м'яке затемнення; текст притиснутий до низу безпечної зони
  A    те саме, але бот сам шукає найспокійнішу смугу для тексту (де найменше дрібних деталей і великих літер)
  G    низ обкладинки перетворюється на «матове скло» (розмиття + затемнення): власний напис обкладинки не заважає
  AG   автоматичне місце + матове скло
  AUTO вибір бота: A, якщо знайшлася спокійна смуга, інакше AG
Запуск: python scripts/layout_lab.py [--per-topic 3] [--out out/lab] (в Telegram, якщо є TELEGRAM_BOT_TOKEN і PREVIEW_CHAT_ID)."""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import daily_short as ds  # noqa: E402
import make_channel_short as m  # noqa: E402

from lutserii import published  # noqa: E402
from lutserii.config import Config  # noqa: E402
from lutserii.db import StateDB  # noqa: E402

SC, TEXT_BOTTOM = m.SC, m.TEXT_BOTTOM
CALM_LIMIT = 0.075  # «спокійна» смуга: оцінка нижча за це (див. band_score); підбирається за надісланими прикладами
WINDOW_HOURS = 96
BLANK = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"

VARIANTS = [
    ("P", "1 · плашка (як зараз)"),
    ("F", "2 · на весь екран + затемнення"),
    ("A", "3 · на весь екран, авто-місце"),
    ("G", "4 · матове скло знизу"),
    ("AG", "5 · авто-місце + матове скло"),
    ("AUTO", "6 · вибір бота"),
]


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def frost(canvas, top_css: float):
    """«Матове скло»: від top_css донизу картинка розмивається й темнішає, перехід плавний."""
    from PIL import Image, ImageEnhance, ImageFilter

    y0 = max(0, round(top_css * SC))
    glass = ImageEnhance.Brightness(canvas.filter(ImageFilter.GaussianBlur(26))).enhance(0.55)
    mask = Image.new("L", (1, m.ART_H), 0)
    ramp = 150
    for y in range(y0, m.ART_H):
        mask.putpixel((0, y), min(255, round(255 * (y - y0) / ramp)))
    return Image.composite(glass, canvas, mask.resize((m.ART_W, m.ART_H)))


def band_score(canvas, top_css: float, height_css: float) -> float:
    return m.band_score(m.gray_small(canvas), top_css, height_css)


def best_band(canvas, height_css: float) -> tuple[float, float]:
    return m.best_band(m.gray_small(canvas), height_css)


OVERRIDE = """
.info{top:var(--ty)!important;bottom:auto!important;background:none!important;padding:0 32px!important;opacity:1!important;transform:none!important}
.info::before{display:none!important}
.scrim{position:absolute;left:0;width:540px;pointer-events:none}
"""


def variant_html(text: str, art_uri: str, pal: dict, top: float, height: float, scrim: bool) -> str:
    html = m.page_html([(text, {"uri": art_uri, "pal": pal})])
    r, g, b = hex_rgb(pal["c2"])
    extra = OVERRIDE
    shade = '<div class="shade"></div>'
    if scrim:
        extra += (f".scrim{{top:{top - 90:.0f}px;height:{height + 200:.0f}px;background:linear-gradient(180deg,rgba({r},{g},{b},0) 0,"
                  f"rgba({r},{g},{b},.76) 90px,rgba({r},{g},{b},.76) calc(100% - 110px),rgba({r},{g},{b},0) 100%)}}")
        shade += '<div class="scrim"></div>'
    html = html.replace("</style>", extra + "</style>", 1).replace('<div class="shade"></div>', shade, 1)
    return html.replace('<section class="slide" id="s0"', f'<section class="slide" id="s0" data-top="{top:.0f}"', 1).replace(
        'style="--c1', f'style="--ty:{top:.0f}px;--c1', 1)


def uri(img) -> str:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def render_stills(posts, out_dir: Path):
    """posts: [(название, text, PIL image)] -> [(название, [(label, PIL image)], подпись)]"""
    from PIL import Image
    from playwright.sync_api import sync_playwright

    result = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": m.CSS_W, "height": m.CSS_H}, device_scale_factor=2)

        def shot(html: str):
            page.set_content(html)
            page.wait_for_timeout(250)
            page.evaluate("() => window.setFrame([1],[0],[1],0)")
            return Image.open(io.BytesIO(page.screenshot(type="png"))).convert("RGB")

        for name, text, im in posts:
            pal = m.palette(im)
            shot(variant_html(text, BLANK, pal, 0, 0, False))
            height = page.evaluate("() => document.querySelector('.in').offsetHeight")
            base = m.full_art(im, pal)
            bottom_top = TEXT_BOTTOM - height
            auto_top, score = best_band(base, height)
            calm = score <= CALM_LIMIT

            plate_art = m.compose_art(im, m.art_mode(*im.size), pal)
            tiles = {
                "P": shot(m.page_html([(text, {"uri": uri(plate_art), "pal": pal})])),
                "F": shot(variant_html(text, uri(base), pal, bottom_top, height, True)),
                "A": shot(variant_html(text, uri(base), pal, auto_top, height, True)),
                "G": shot(variant_html(text, uri(frost(base, bottom_top - 60)), pal, bottom_top, height, False)),
                "AG": shot(variant_html(text, uri(frost(base, auto_top - 60)), pal, auto_top, height, False)),
            }
            tiles["AUTO"] = tiles["A"] if calm else tiles["AG"]
            labels = dict(VARIANTS)
            labels["AUTO"] = "6 · вибір бота: " + ("авто-місце (3)" if calm else "скло + авто-місце (5)")
            caption = f"{name}\nсмуга: {auto_top:.0f}px, оцінка {score:.3f} ({'спокійна' if calm else 'галаслива'}), висота тексту {height:.0f}px"
            result.append((name, [(labels[k], tiles[k]) for k, _ in VARIANTS], caption))
        browser.close()
    return result


def sheet(pair):
    """Дві плитки поруч із підписами: 720×1280 кожна."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(m.ROOT / "assets/fonts/Inter-Bold.otf"), 26)
    out = Image.new("RGB", (720 * len(pair) + 8 * (len(pair) - 1), 1280), "#000")
    for i, (label, img) in enumerate(pair):
        tile = img.resize((720, 1280), Image.LANCZOS)
        d = ImageDraw.Draw(tile)
        tw = d.textlength(label, font=font)
        d.rounded_rectangle((12, 1226, 12 + tw + 24, 1270), radius=10, fill=(0, 0, 0, 200))
        d.text((24, 1232), label, font=font, fill="#fff")
        out.paste(tile, (i * 728, 0))
    return out


def send_album(images: list[bytes], caption: str) -> None:
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("PREVIEW_CHAT_ID")
    if not (token and chat):
        return
    media = [{"type": "photo", "media": f"attach://p{i}", **({"caption": caption[:1000]} if i == 0 else {})} for i in range(len(images))]
    files = {f"p{i}": (f"p{i}.jpg", data, "image/jpeg") for i, data in enumerate(images)}
    resp = requests.post(f"https://api.telegram.org/bot{token}/sendMediaGroup", data={"chat_id": chat, "media": json.dumps(media)}, files=files, timeout=120)
    if resp.status_code != 200:
        print(f"Альбом не надіслано: {resp.status_code} {resp.text[:200]}")


def collect(per_topic: int, total: int):
    """Реальні пости з журналу: по кілька з кожної теми, з різними обкладинками."""
    cfg = Config.from_env()
    now = int(time.time())
    db = StateDB(cfg.db_path)
    try:
        found = []
        for topic in ("series", "anime", "free", "games"):
            items = ds.merge_digests(published.recent(db, ds.KINDS_TOPIC.get(topic, topic), now - WINDOW_HOURS * 3600))
            used, n = set(), 0
            for item in items:
                if n >= per_topic or not item["photos"] or item["photos"][0] in used:
                    continue
                im = m.pick_image(list(item["photos"]))
                if im is None:
                    continue
                used.add(item["photos"][0])
                found.append((f"{topic}: {m.parse_post(item['text'])['title']}", item["text"], im))
                n += 1
    finally:
        db.close()
    return found[:total]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-topic", type=int, default=2)
    parser.add_argument("--total", type=int, default=8)
    parser.add_argument("--out", default="out/lab")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    posts = collect(args.per_topic, args.total)
    print(f"Постів з картинками: {len(posts)}")
    for name, tiles, caption in render_stills(posts, out):
        print(caption.replace("\n", " | "))
        sheets = []
        for k in range(0, len(tiles), 2):
            buf = io.BytesIO()
            sheet(tiles[k: k + 2]).save(buf, "JPEG", quality=88)
            sheets.append(buf.getvalue())
        safe = "".join(c if c.isalnum() else "_" for c in name)[:40]
        for i, data in enumerate(sheets):
            (out / f"{safe}_{i}.jpg").write_bytes(data)
        send_album(sheets, "🧪 Макети: " + caption)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
