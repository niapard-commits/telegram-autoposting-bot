"""Short каналу: кожен пост каналу — окремий повноекранний кадр (картинка гри/аніме/серіалу на весь екран, коротка
плашка з назвою, ціною й головним — у вільному місці), без голосу, з музикою.

Пости будуються тими самими функціями, що й у каналі (lutserii.posts), із реальних даних (data/probe/sites).
Використання: python scripts/make_channel_short.py out.mp4 [--mock-images]
Потрібні: playwright (chromium), ffmpeg, requests."""

from __future__ import annotations

import argparse
import base64
import html as htmllib
import json
import io
import re
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT / "scripts"))
import make_music  # noqa: E402
from lutserii import main as app  # noqa: E402
from lutserii import posts  # noqa: E402
from lutserii.config import Config  # noqa: E402
from lutserii.db import StateDB  # noqa: E402
from lutserii.sources import consoles  # noqa: E402

PROBE = ROOT / "data/probe/sites"
FPS = 30
CSS_W, CSS_H = 540, 960  # ×2 = 1080×1920
HEADER_H = 50
HEADER_TOP = 60
LIST_TOP = HEADER_TOP + HEADER_H + 8
SAFE_LEFT, SAFE_W, SAFE_BOTTOM = 32, 388, 668  # YouTube Shorts на високих екранах зрізає з боків ~17 px кадру
MOVE = 0.8  # с: плавний перехід до наступного кадру
ZOOM_PER_SEC = 0.007  # повільне наближення картинки
TEXT_DELAY, TEXT_IN = 0.9, 0.55  # с: спершу видно саму картинку, потім з'являється підпис
OUTRO = 3.0  # с: фінальна заставка (мінімум)
MIN_TOTAL, MAX_TOTAL = 20.0, 30.0  # тривалість ролика залежить від кількості матеріалу
HOLD_MIN, HOLD_MAX = 3.0, 8.0  # с на один пост


def data_uri(url: str | None, mock: str | None = None) -> str | None:
    try:
        if not url and not mock:
            return None
        if mock:
            from PIL import Image, ImageDraw
            im = Image.new("RGB", (800, 800), mock)
            ImageDraw.Draw(im).ellipse((150, 150, 650, 650), outline="white", width=12)
            buf = io.BytesIO()
            im.save(buf, "PNG")
            raw, kind = buf.getvalue(), "image/png"
        else:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw, kind = resp.read(), resp.headers.get_content_type() or "image/jpeg"
        return f"data:{kind};base64,{base64.b64encode(raw).decode()}"
    except Exception as exc:
        print(f"Картинку не завантажено ({type(exc).__name__}): {url}", file=sys.stderr)
        return None


COVER_MAX_UPSCALE = 2.4  # більше збільшувати картинку на весь екран не можна: розмито, тоді розмите тло + чітка картинка


def fetch_image(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read()
        return raw if len(raw) > 2000 else None
    except Exception:
        return None


def art_candidates(urls: list[str]) -> list[str]:
    """Адреси від кращих до гірших: для Steam вертикальна обкладинка 1200×1800, для AniList найбільша версія, далі як є."""
    out: list[str] = []
    for u in urls:
        m = re.search(r"/steam/apps/(\d+)/header", u)
        if m:
            out.append(f"https://cdn.cloudflare.steamstatic.com/steam/apps/{m.group(1)}/library_600x900_2x.jpg")
        if "/cover/large/" in u:
            out.append(u.replace("/cover/large/", "/cover/extraLarge/"))
        out.append(u)
    return list(dict.fromkeys(out))


def cover_scale(w: int, h: int) -> float:
    """У скільки разів треба збільшити картинку, щоб закрити весь кадр 1080×1920."""
    return max(1080 / w, 1920 / h)


ART_W, ART_H = 1166, 2074  # кадр 1080×1920 у пікселях полотна (браузер показує полотно цілком)
POSTER_W, POSTER_TOP = ART_W, 50  # вертикальна картинка: на всю ширину, одразу під системним рядком; низ закриває плашка з текстом
POSTER_FILL = 1.14  # на скільки можна збільшити вертикальну обкладинку, щоб закрити низ кадру (замість порожнього розмитого місця)
PLATE_TOP = 520  # css-висота, з якої починається плашка з текстом (для довгих текстів вона росте вгору)
HY_ZONE = (118, round(PLATE_TOP * ART_H / CSS_H))  # вільна від плашки частина полотна для широкої картинки
HY_H, HY_MAX_ZOOM = 760, 2.4  # широка картинка: чітка смуга заввишки до HY_H по центру вільної зони, з країв обрізається не більше чверті


def art_mode(w: int, h: int) -> str:
    """poster — вертикальна картинка цілком (краї й літери не обрізаються), wide — широка: чітка смуга на розмитому тлі."""
    return "poster" if w <= h else "wide"


def wide_scale(w: int, h: int) -> float:
    """У скільки разів збільшується широка картинка: щонайменше до ширини кадру, вище — до смуги заввишки HY_H."""
    return max(ART_W / w, min(HY_H / h, HY_MAX_ZOOM))


def quality_scale(w: int, h: int) -> float:
    """У скільки разів картинку доведеться збільшити в обраному режимі."""
    return POSTER_W / w if w <= h else wide_scale(w, h)


def _backdrop(im):
    """Розмите затемнене тло на весь кадр із самої картинки."""
    from PIL import Image, ImageEnhance, ImageFilter

    w, h = im.size
    k = max(ART_W / w, ART_H / h)
    tiny = im.resize((max(8, round(w * k / 22)), max(8, round(h * k / 22))), Image.BILINEAR)
    cw, ch = ART_W // 22 + 1, ART_H // 22 + 1
    left = max((tiny.width - cw) // 2, 0)
    tiny = tiny.crop((left, 0, left + min(tiny.width, cw), min(tiny.height, ch)))
    bg = tiny.resize((ART_W, ART_H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(8))
    bg = ImageEnhance.Brightness(bg).enhance(0.65)
    return ImageEnhance.Color(bg).enhance(1.25)


def _feather(size: tuple[int, int], top: float, bottom: float):
    from PIL import Image

    w, h = size
    col = Image.new("L", (1, h))
    for y in range(h):
        t = y / max(h - 1, 1)
        col.putpixel((0, y), round(255 * max(0.0, min(1.0, t / top if top else 1.0, (1 - t) / bottom if bottom else 1.0))))
    return col.resize((w, h))


def palette(im) -> dict[str, str]:
    """Кольори плашки з тону картинки: темний фон-градієнт (c1, c2) і яскравий акцент (ac) для підписів."""
    import colorsys

    from PIL import Image

    small = im.convert("RGB").resize((48, 72), Image.BILINEAR).quantize(colors=6, method=Image.MEDIANCUT)
    pal = small.getpalette()
    best, score = None, 0.0
    for count, idx in small.getcolors():
        r, g, b = pal[idx * 3: idx * 3 + 3]
        h, sat, val = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        points = count * (0.25 + sat) * (1.0 if 0.2 < val else 0.3)
        if points > score:
            best, score = (h, sat, val), points
    h, sat, _ = best or (0.6, 0.0, 0.0)
    grey = sat < 0.12
    sat = 0.1 if grey else min(max(sat, 0.35), 0.75)

    def hexa(hh: float, ss: float, vv: float) -> str:
        r, g, b = colorsys.hsv_to_rgb(hh, ss, vv)
        return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"

    # плашка починається там, де закінчується картинка: її колір береться з того місця обкладинки, щоб перехід був непомітний
    w, ih = im.size
    k = ART_W / w if w <= ih else 1.0
    y0, y1 = (880 - POSTER_TOP) / (k * ih), (1130 - POSTER_TOP) / (k * ih)
    if w <= ih and y0 < 0.97:
        band = im.convert("RGB").crop((0, round(max(y0, 0) * ih), w, max(round(min(y1, 1.0) * ih), round(max(y0, 0) * ih) + 2))).resize((1, 1), Image.BOX).getpixel((0, 0))
        bh, bs, bv = colorsys.rgb_to_hsv(*(c / 255 for c in band))
        v1 = min(max(bv * 0.5, 0.1), 0.26)
        s1 = min(bs * 0.9, 0.7)
        return {"c1": hexa(bh, s1, v1), "c2": hexa(bh, min(s1 * 1.1, 0.8), v1 * 0.45),
                "ac": "#ffc43d" if grey else hexa(h, min(sat * 0.7, 0.62), 1.0)}
    return {"c1": hexa(h, sat * 0.85, 0.27), "c2": hexa(h, sat, 0.12),
            "ac": "#ffc43d" if grey else hexa(h, min(sat * 0.7, 0.62), 1.0)}


SC = ART_H / CSS_H  # пікселів полотна на один css-піксель
TEXT_MIN_TOP = 400  # вище за це текст не піднімаємо: там обличчя й герої
TEXT_BOTTOM = SAFE_BOTTOM - 10  # нижній край тексту (нижче — інтерфейс YouTube)


def full_art(im, pal):
    """Обкладинка на всю ширину, від самого верху; якщо вона нижча за кадр — продовження з дзеркально розмитого низу
    (власний напис обкладинки ховається в розмитій «тіні»). Широкі картинки — як у відео: смуга по центру вільної зони."""
    from PIL import Image, ImageEnhance, ImageFilter

    w, h = im.size
    if w > h:
        return compose_art(im, "wide", pal)
    k = max(ART_W / w, min(ART_H / h, ART_W / w * POSTER_FILL))  # трохи збільшуємо, щоб кадр заповнився майже весь (з країв зникає до ~7%)
    fg = im.resize((round(w * k), round(h * k)), Image.LANCZOS)
    left = (fg.width - ART_W) // 2
    fg = fg.crop((left, 0, left + ART_W, min(fg.height, ART_H)))
    if fg.height >= ART_H:
        return fg
    canvas = Image.new("RGB", (ART_W, ART_H), pal["c2"])
    canvas.paste(fg, (0, 0))
    n = ART_H - fg.height
    band = fg.crop((0, fg.height - max(40, round(fg.height * 0.07)), ART_W, fg.height))
    tail = band.resize((ART_W, n + 60), Image.BILINEAR).filter(ImageFilter.GaussianBlur(22))
    tail = ImageEnhance.Brightness(tail).enhance(0.88)
    canvas.paste(tail, (0, fg.height - 30), _feather(tail.size, 0.5, 0.0))
    return canvas


def gray_small(canvas):
    """Сіра мініатюра полотна (1/3 розміру) для пошуку спокійного місця під текст."""
    import numpy as np
    from PIL import Image

    return np.asarray(canvas.convert("L").resize((ART_W // 3, ART_H // 3), Image.BILINEAR), dtype=np.float32) / 255


def band_score(gray, top_css: float, height_css: float) -> float:
    """Наскільки «галаслива» смуга під текстом: дрібні деталі й великі контрасти (менше = спокійніше)."""
    import numpy as np

    k = SC / 3
    x0, x1 = round(SAFE_LEFT * k), round((SAFE_LEFT + SAFE_W) * k)
    y0 = max(0, round(top_css * k))
    y1 = min(gray.shape[0], max(y0 + 4, round((top_css + height_css) * k)))
    g = gray[y0:y1, x0:x1]
    return float(np.abs(np.diff(g, axis=1)).mean() + np.abs(np.diff(g, axis=0)).mean()) * 2.0 + float(g.std()) * 0.6


def best_band(gray, height_css: float) -> tuple[float, float]:
    """(верх, оцінка) найспокійнішої смуги для тексту заввишки height_css між TEXT_MIN_TOP і безпечним низом; трохи віддає перевагу нижчим."""
    bottom_top = TEXT_BOTTOM - height_css
    best = (bottom_top, band_score(gray, bottom_top, height_css))
    t = bottom_top
    while t >= TEXT_MIN_TOP:
        score = band_score(gray, t, height_css) + 0.00035 * (bottom_top - t)
        if score < best[1]:
            best = (t, score)
        t -= 4
    return best


def art_dict(im) -> dict:
    """Готовий кадр картинки для сторінки: {uri, w, h, pal}."""
    pal = palette(im)
    frame = full_art(im, pal)
    buf = io.BytesIO()
    frame.save(buf, "JPEG", quality=90)
    return {"uri": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), "w": ART_W, "h": ART_H, "cover": False, "pal": pal, "gray": gray_small(frame)}


def compose_art(im, mode: str, pal: dict[str, str] | None = None):
    """Готує кадр заздалегідь, щоб браузер не розмивав і не масштабував важкі картинки на кожному з сотень кадрів."""
    from PIL import Image

    im = im.convert("RGB")
    w, h = im.size
    if mode == "poster":  # на всю ширину; фон — колір картинки (видно лише смужку зверху), низ закриє плашка з текстом
        bg = Image.new("RGB", (ART_W, ART_H), (pal or {}).get("c2", "#0c0e18"))
        k = min(POSTER_W / w, (ART_H - POSTER_TOP) / h)
        fg = im.resize((round(w * k), round(h * k)), Image.LANCZOS)
        bg.paste(fg, ((ART_W - fg.width) // 2, POSTER_TOP), _feather(fg.size, 0.01, 0.0))
        return bg
    bg = _backdrop(im)
    k = wide_scale(w, h)
    fg = im.resize((max(ART_W, round(w * k)), round(h * k)), Image.LANCZOS)
    left = (fg.width - ART_W) // 2
    fg = fg.crop((left, 0, left + ART_W, fg.height))
    top = HY_ZONE[0] + max(0, (HY_ZONE[1] - HY_ZONE[0] - fg.height) // 2)
    bg.paste(fg, (0, top), _feather(fg.size, 0.10, 0.25))
    return bg


def pick_image(urls: list[str], mock: str | None = None):
    """Найкраща картинка поста (PIL RGB). Вертикальні (обкладинки) краще за широкі,
    бо влазять цілком; широка береться, лише якщо вертикальної немає."""
    from PIL import Image, ImageDraw

    best: dict[str, tuple[float, bytes] | None] = {"poster": None, "wide": None}
    candidates = [None] if mock else art_candidates(urls)[:6]
    for url in candidates:
        if mock:
            im = Image.new("RGB", (1200, 1800), mock)
            ImageDraw.Draw(im).ellipse((200, 500, 1000, 1300), outline="white", width=14)
            buf = io.BytesIO()
            im.save(buf, "JPEG")
            raw = buf.getvalue()
        else:
            raw = fetch_image(url)
        if not raw:
            continue
        try:
            with Image.open(io.BytesIO(raw)) as probe:
                w, h = probe.size
        except Exception:
            continue
        mode, scale = art_mode(w, h), quality_scale(w, h)
        if best[mode] is None or scale < best[mode][0]:
            best[mode] = (scale, raw)
        if mode == "poster" and scale <= COVER_MAX_UPSCALE:
            break
    chosen = best["poster"] if best["poster"] and best["poster"][0] <= 3.2 else (best["wide"] or best["poster"])
    if chosen is None:
        return None
    try:
        with Image.open(io.BytesIO(chosen[1])) as im:
            return im.convert("RGB")
    except Exception as exc:
        print(f"Картинку не оброблено ({type(exc).__name__})", file=sys.stderr)
        return None


def load_art(urls: list[str], mock: str | None = None) -> dict | None:
    """Найкраща картинка поста, одразу підготовлена на весь кадр: {uri, w, h, pal}."""
    im = pick_image(urls, mock)
    return art_dict(im) if im is not None else None


def telegram_html(text: str) -> str:
    """HTML-підмножина Telegram -> HTML для макета: переноси рядків і підсвітка хештегів."""
    body = text.replace("\n", "<br>")
    body = re.sub(r"</blockquote><br>", "</blockquote>", body)
    body = re.sub(r"(?<![\w&])(#[\wА-Яа-яІіЇїЄєҐґ]+)", r'<span class="tag">\1</span>', body)
    return body


KICKERS = {"🔥": "🔥 Знижка", "🎁": "🎁 Безкоштовно", "🆓": "🆓 Безкоштовно", "🚀": "🚀 Вийшла сьогодні", "🎙": "🎙 Озвучка українською",
           "🎬": "🎬 Аніме", "📺": "📺 Нова серія", "🟢": "🟢 Xbox Game Pass"}
MAX_CHIPS, MAX_ITEMS, BLURB_CHARS = 2, 3, 170
DROP_CHIPS = ('🏷', '📡', '🏆', '🌐')  # жанри, канал, «історичний мінімум» на кадрі не потрібні: текст має лишати місце картинці


def plain(fragment: str) -> str:
    """Рядок Telegram-HTML без тегів і сутностей."""
    return htmllib.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def esc(text: str) -> str:
    return htmllib.escape(text, quote=False)


def clip(text: str, limit: int) -> str:
    """Скорочує за реченням, інакше за словом."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    ends = [m.end() for m in re.finditer(r"[.!?…](?=\s)", cut)]
    if ends and ends[-1] >= limit * 0.55:
        return cut[: ends[-1]].strip()
    return cut.rsplit(" ", 1)[0].rstrip(" ,;:—-") + "…"


def parse_post(text: str) -> dict:
    """Розбирає пост каналу на частини для кадру: назва, підпис, знижка/ціна, головні факти, короткий опис або список."""
    quotes = re.findall(r"<blockquote>(.*?)</blockquote>", text, flags=re.S)
    outside = re.sub(r"<blockquote>.*?</blockquote>", "", text, flags=re.S)
    lines = [ln for ln in outside.splitlines() if ln.strip()]
    head_raw = plain(lines[0]) if lines else ""
    mark = head_raw[:1] if head_raw[:1] in KICKERS else ""
    head = re.sub(r"^[^\w\"«(]+", "", head_raw)
    q_lines = [ln for q in quotes for ln in q.splitlines() if ln.strip()]

    post: dict = {"kicker": KICKERS.get(mark, ""), "title": head, "sub": "", "pct": "", "price": "", "old": "", "alt": "",
                  "chips": [], "blurb": "", "items": [], "free": False}

    # ціна й знижка
    pct = re.search(r"знижк[аи]\s+(до\s+)?(\d+)\s*%", head) or re.search(r"\(\s*[−-](\d+)\s*%\s*\)", " ".join(q_lines))
    if pct:
        post["pct"] = ("до " if pct.re.groups == 2 and pct.group(1) else "") + "−" + pct.group(pct.re.groups) + "%"
    now_head = re.search(r"тепер\s+(₴[\d\s ,]+?)(?:\s+(?:у|в)\s|$)", head)
    for ln in q_lines:
        if ln.startswith("💰"):
            old = re.search(r"<s>(.*?)</s>", ln)
            new = re.search(r"→\s*<b>(.*?)</b>", ln)
            uah = re.search(r"≈\s*(₴[\d\s ,]+?)\s", ln + " ")
            post["old"] = plain(old.group(1)) if old else ""
            first = plain(new.group(1)) if new else ""
            if first.lower().startswith("безкоштовно"):
                post["free"] = True
            elif now_head:
                post["price"], post["alt"] = now_head.group(1).strip(), first
            elif uah:
                post["price"], post["alt"] = uah.group(1).strip(), first
            else:
                post["price"] = first
    if not post["price"] and now_head:
        post["price"] = now_head.group(1).strip()
    if mark in ("🎁", "🆓") and not post["price"]:
        post["free"] = True

    # назва
    title = head
    if mark == "🔥":
        title = re.sub(r"\s+—\s+знижк.*$", "", title)
        title = re.sub(r":\s+знижк.*$", "", title)
        title = re.sub(r"^Серія\s+", "", title)
    elif mark in ("🎁", "🆓"):
        extra = re.match(r"Безкоштовно\s*\(([^)]+)\)\s*:\s*", title)
        if extra:
            post["kicker"] = "🎁 Безкоштовно · " + extra.group(1)
        title = re.sub(r"^Безкоштовно(?:\s*\([^)]*\))?\s*:\s*", "", title)
        title = re.sub(r"\s+—\s+безкоштовно.*$", "", title, flags=re.I)
    elif mark == "🚀":
        title = re.sub(r"^Сьогодні вийшла:\s*", "", title)
    elif mark == "🎙":
        title, _, date_part = title.partition(" · ")
        post["sub"] = date_part
        if title.startswith("Озвучка українською"):  # добірка за день
            post["kicker"] = "🎙 Щойно вийшло"
    paren = re.match(r"^(.*?)\s*\(([^()]+)\)\s*$", title)
    if paren:
        title = paren.group(1)
        if re.search(r"[A-Za-zА-Яа-яІіЇїЄє]", paren.group(2)):  # оригінальна назва; ієрогліфи на кадрі не потрібні
            post["sub"] = paren.group(2)
    post["title"] = title.strip()

    # магазин у підписі
    store = next((plain(ln)[1:].strip() for ln in q_lines if ln.startswith("🏪")), "")
    if not store and mark in ("🔥", "🎁", "🆓"):
        m = re.search(r"\s(?:у|в)\s+([A-ZА-ЯІЇЄҐ][^—]*?)\s*$", head)
        store = m.group(1) if m else ""
    if store:
        post["kicker"] = (post["kicker"] + " · " + re.sub(r"\s*\([^)]*\)$", "", store)).strip(" ·")

    # список (добірки): рядки з «—» або «🔹»
    items = [re.sub(r"^(?:—|🔹)\s*", "", plain(ln)) for ln in q_lines if re.match(r"^(?:—|🔹)", plain(ln))]
    if len(items) >= 2:
        post["items"] = items
        if mark == "🟢":
            intro = next((plain(ln) for ln in lines[1:] if not re.match(r"^[^\w]*(?:#|@|https?)", plain(ln))), "")
            post["sub"] = intro.rstrip(":")
    else:
        # факти: усе з плашки, крім ціни, магазину; плюс «⏳ …» і «🏆 …» поза плашкою
        for ln in q_lines:
            t = plain(ln)
            if ln.startswith(("💰", "🏪")) or t.startswith("🌐") or not t:
                continue
            post["chips"].append(re.sub(r"^(🎧)\s*(?:Озвучка|Українською):\s*", r"\1 ", t))
        for ln in lines[1:]:
            t = plain(ln)
            if t.startswith(("⏳", "🏆")):
                post["chips"].append(t)
        post["chips"] = [clip(re.sub(r"\s+—\s+«[^»]*»", "", c), 40) for c in post["chips"] if not c.startswith(DROP_CHIPS)][:MAX_CHIPS]
        for blk in re.split(r"\n\s*\n", outside.strip())[1:]:
            t = plain(blk)
            if t and not re.match(r"^[^\w]{0,3}(?:#|@|👉|📺|📖|⏳|🏆|<)", t) and not t.startswith(("Дані:", "👉")):
                post["blurb"] = clip(" ".join(t.split()), BLURB_CHARS)
                break
    return post


def title_class(title: str) -> str:
    n = len(title)
    return "t1" if n <= 14 else "t2" if n <= 26 else "t3"


def slide_html(post: dict) -> str:
    """Плашка з текстом поверх картинки."""
    parts = []
    if post["kicker"]:
        parts.append(f'<div class="kicker">{esc(post["kicker"])}</div>')
    parts.append(f'<h2 class="{title_class(post["title"])}">{esc(post["title"])}</h2>')
    if post["sub"]:
        parts.append(f'<div class="sub">{esc(post["sub"])}</div>')
    if post["pct"] or post["price"] or post["free"]:
        badge = f'<span class="pct">{esc(post["pct"])}</span>' if post["pct"] else ""
        if post["free"]:
            badge = '<span class="pct free">Безкоштовно</span>'
        price = f'<span class="now">{esc(post["price"])}</span>' if post["price"] else ""
        old = f'<s>{esc(post["old"])}</s>' if post["old"] else ""
        alt = f'<span class="alt">{esc(post["alt"])}</span>' if post["alt"] and post["alt"] != post["price"] else ""
        parts.append(f'<div class="price">{badge}{price}<span class="olds">{old}{alt}</span></div>')
    if post["items"]:
        shown = post["items"][:MAX_ITEMS]
        more = len(post["items"]) - len(shown)
        li = "".join(f"<li>{esc(clip(t, 52))}</li>" for t in shown)
        parts.append(f'<ul>{li}</ul>' + (f'<div class="more">…і ще {more}</div>' if more > 0 else ""))
    if post["chips"]:
        parts.append('<div class="chips">' + "".join(f"<span>{esc(c)}</span>" for c in post["chips"]) + "</div>")
    return "".join(parts)


TOPICS = {
    "games": {"pill": "Знижки та роздачі щодня", "tag": "Знижки на ігри та консолі,<br>роздачі, серіали українською", "track": 4},
    "free": {"pill": "Безкоштовні ігри щодня", "tag": "Роздачі ігор для ПК<br>та консолей щодня", "track": 4},
    "anime": {"pill": "Нові серії аніме щодня", "tag": "Розклад нових серій аніме<br>та озвучка українською", "track": 3},
    "series": {"pill": "Серіали українською: коли вийде серія", "tag": "Нові серії та озвучка українською,<br>без спойлерів", "track": 10},
}
MAX_POSTS = 6
MUSIC_TOPIC = {"free": "games"}  # ролик про роздачі бере треки ігрової теми


def capture(topic: str) -> list[tuple[str, list[str]]]:
    """Запускає справжні завдання бота (як у каналі) у режимі перегляду й збирає готові пости замість надсилання."""
    ctx = app.build_context(Config.from_env(dry_run=True))
    got: list[tuple[str, list[str]]] = []
    ctx.preview = lambda text, photos: got.append((text, list(photos)))
    ctx.preview_video = lambda text, video, photos: got.append((text, list(photos)))
    ctx.pause = 0
    try:
        for name in {"anime": ["anime"], "series": ["dubs", "series"], "free": ["free"]}[topic]:
            try:
                app.TASKS[name](ctx)
            except Exception as exc:  # одне завдання не повинно ламати ролик
                print(f"{name}: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        ctx.db.close()
    seen, out = set(), []
    for text, photos in got:
        if text not in seen and "Перегляд" not in text[:12]:
            seen.add(text)
            out.append((text, photos))
    with_photo = [m for m in out if m[1]]
    return (with_photo + [m for m in out if not m[1]])[:MAX_POSTS]


def build_messages(mock: bool, topic: str = "games") -> list[tuple[str, dict | None]]:
    colors = ["#7a3b2e", "#2e7a55", "#3b46a0", "#7a2e6a"]
    if topic != "games":
        captured = capture(topic)
        if len(captured) < 2:
            raise SystemExit(f"Замало реальних постів для теми «{topic}»: {len(captured)}")
        return [(t, load_art(ph, colors[i % 4] if mock and ph else None) if ph else None) for i, (t, ph) in enumerate(captured)]
    cfg = Config()
    deals = consoles.parse_deals((PROBE / "psprices.com_region_ua_rss_discounts.html").read_text(encoding="utf-8"))
    picked = app.pick_console_deals(deals, cfg, StateDB(":memory:"))[:3]
    ann = consoles.parse_announcements(consoles.parse_feed(
        (PROBE / "blog.playstation.com_category_ps_plus_feed.html").read_text(encoding="utf-8")))
    ps_plus = next((a for a in ann if a.kind == "psplus"), None)
    msgs = []
    if ps_plus:
        msgs.append((posts.subscription_post(ps_plus, cfg.signature), ps_plus.image))
    msgs += [(posts.console_deal_post(d, cfg.signature), d.image) for d in picked]
    return [(text, load_art([img] if img else [], colors[i % 4] if mock else None)) for i, (text, img) in enumerate(msgs)]


def page_html(messages: list[tuple[str, dict | None]], topic: str = "games") -> str:
    """Кожне повідомлення — повноекранний кадр: картинка на весь екран, плашка з текстом у вільному місці."""
    avatar = data_uri(None, None)
    avatar_file = ROOT / "assets/avatar.png"
    if avatar_file.exists():
        avatar = "data:image/png;base64," + base64.b64encode(avatar_file.read_bytes()).decode()
    faces = "".join(
        f'@font-face{{font-family:"InterApp";font-weight:{w};src:url(data:font/otf;base64,'
        + base64.b64encode((ROOT / f"assets/fonts/Inter-{n}.otf").read_bytes()).decode() + ")}"
        for n, w in (("Medium", 400), ("Bold", 700))
    )
    slides = []
    for i, (text, art) in enumerate(messages):
        pic = f'<div class="art"><img class="bg" src="{art["uri"]}"></div>' if art else '<div class="art"></div>'
        pal = (art or {}).get("pal") or {}
        rgb = ",".join(str(int(pal["c2"].lstrip("#")[k:k + 2], 16)) for k in (0, 2, 4)) if pal else "6,8,16"
        style = f' style="--c1:{pal["c1"]};--c2:{pal["c2"]};--ac:{pal["ac"]};--rgb:{rgb}"' if pal else f' style="--rgb:{rgb}"'
        slides.append(f'<section class="slide" id="s{i}"{style}>{pic}<div class="shade"></div><div class="scrim"></div>'
                      f'<div class="info"><div class="in">{slide_html(parse_post(text))}</div></div></section>')
    return f"""<!doctype html><meta charset="utf-8"><style>
{faces}
*{{box-sizing:border-box;margin:0}}
body{{width:{CSS_W}px;height:{CSS_H}px;overflow:hidden;background:#0b0f19;font-family:InterApp,Inter,"Segoe UI",Roboto,sans-serif;color:#f5f5f5;position:relative}}
/* Безпечна зона YouTube Shorts: зверху ~58px (іконки), знизу від ~670px (назва й канал), праворуч від ~445px (кнопки) */
.slide{{position:absolute;inset:0;opacity:0;overflow:hidden}}
.art{{position:absolute;inset:0;overflow:hidden;background:radial-gradient(circle at 50% 30%,#2a2f55,#0b0f19)}}
.art .bg{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;transform-origin:50% 30%;transform:scale(calc(1 + var(--z,0)))}}
.shade{{position:absolute;inset:0;background:linear-gradient(to bottom,rgba(6,8,16,.55) 0,rgba(6,8,16,0) 15%)}}
.scrim{{position:absolute;left:0;width:{CSS_W}px;top:calc(var(--ty,520px) - 90px);height:calc(var(--th,100px) + 200px);pointer-events:none;opacity:var(--e,0);
  background:linear-gradient(180deg,rgba(var(--rgb),0) 0,rgba(var(--rgb),.76) 90px,rgba(var(--rgb),.76) calc(100% - 110px),rgba(var(--rgb),0) 100%)}}
.info{{position:absolute;left:0;width:{CSS_W}px;top:var(--ty,520px);padding:0 {SAFE_LEFT}px;opacity:var(--e,0);transform:translateY(calc((1 - var(--e,0)) * 40px))}}
/* картинка на весь екран; текст стоїть у найспокійнішій смузі (місце підбирає render), під ним лише м'яке затемнення кольору картинки */
.info .in{{width:{SAFE_W}px}}
.kicker{{font-size:12.5px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--ac,#ffc43d);margin-bottom:4px}}
h2{{font-weight:700;line-height:1.06;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;}}
h2.t1{{font-size:38px}} h2.t2{{font-size:31px}} h2.t3{{font-size:25px;line-height:1.12}}
.sub{{font-size:13px;color:#cfd6e4;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.price{{display:flex;align-items:center;gap:10px;margin-top:8px}}
.pct{{background:#ff3b4e;color:#fff;font-weight:700;font-size:22px;padding:3px 10px;border-radius:9px;text-shadow:none;white-space:nowrap}}
.pct.free{{background:#22c55e;font-size:17px;text-transform:uppercase;letter-spacing:.03em;padding:5px 11px}}
.now{{font-size:32px;font-weight:700;color:#fff}}
.olds{{display:flex;flex-direction:column;font-size:13px;color:#b3bccd;line-height:1.25}} .olds s{{opacity:.85}}
.blurb{{font-size:13.5px;line-height:1.4;margin-top:10px;color:#e4e9f2;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:4;overflow:hidden}}
.chips{{display:flex;flex-wrap:nowrap;gap:6px;margin-top:9px}}
.chips span{{background:rgba(255,255,255,.15);border-radius:999px;padding:4px 10px;font-size:12px;text-shadow:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}}
ul{{list-style:none;padding:0;margin-top:8px;display:flex;flex-direction:column;gap:4px}}
li{{font-size:14px;line-height:1.2;background:rgba(255,255,255,.12);border-radius:9px;padding:5px 10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-shadow:none}}
.more{{font-size:12.5px;color:#cfd6e4;margin-top:5px}}
#outro{{position:absolute;top:0;left:0;width:{CSS_W}px;height:{CSS_H}px;padding:0 24px {CSS_H - SAFE_BOTTOM}px;background:#0b121add;z-index:9;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;opacity:0;text-align:center}}
#outro img{{width:110px;height:110px;border-radius:50%;object-fit:cover}}
#outro h1{{font-size:32px;color:#ffc43d}} #outro p{{font-size:20px}} #outro small{{font-size:18px;color:#6ab3f3}}
</style>
{''.join(slides)}
<div id="outro"><img src="{avatar}"><h1>Лут і Серія</h1><p>{TOPICS[topic]["tag"]}</p><small>@lut_i_serii</small></div>
<script>window.setTops=(a)=>{{a.forEach(([i,t,h])=>{{const el=document.getElementById('s'+i);el.style.setProperty('--ty',t+'px');el.style.setProperty('--th',h+'px');}});}};window.setFrame=(ops,zs,es,o)=>{{document.querySelectorAll('.slide').forEach((el,i)=>{{el.style.opacity=ops[i];el.style.setProperty('--z',zs[i]);el.style.setProperty('--e',es[i]);}});document.getElementById('outro').style.opacity=o;}}</script>"""


MUSIC_DIR = ROOT / "assets/music"
AUDIO = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}


def load_tracks() -> dict:
    """assets/music/tracks.json: {файл: {credit, topics}}; треки без запису доступні для всіх тем."""
    try:
        return json.loads((MUSIC_DIR / "tracks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def choose_track(topic: str, last: str | list[str] | None = None, today: date | None = None) -> Path | None:
    """Трек для теми: лише дозволені для неї, випадковий, але не з останніх використаних (last: ім'я або список імен,
    найновіші в кінці). Із заданою датою вибір відтворюваний (для тестів), без неї кожного разу новий."""
    import random

    meta = load_tracks()
    files = sorted(f for f in MUSIC_DIR.glob("*") if f.suffix.lower() in AUDIO)
    music_topic = MUSIC_TOPIC.get(topic, topic)
    files = [f for f in files if music_topic in meta.get(f.name, {}).get("topics", list(TOPICS))]
    if not files:
        return None
    recent = [last] if isinstance(last, str) else list(last or [])
    pool = [f for f in files if f.name not in recent]
    if not pool:  # усі вже були: беремо будь-який, крім найостаннішого
        pool = [f for f in files if f.name != (recent[-1] if recent else None)] or files
    rng = random.Random((today.toordinal() * 7 + list(TOPICS).index(topic))) if today else random.SystemRandom()
    return rng.choice(pool)


def pick_music(arg: str | None) -> Path | None:
    """Файл із --music або перший придатний трек із теки assets/music (для ручних запусків)."""
    if arg and Path(arg).is_file():
        return Path(arg)
    folder = Path(arg) if arg else MUSIC_DIR
    files = sorted(f for f in folder.glob("*") if f.suffix.lower() in AUDIO) if folder.is_dir() else []
    return files[date.today().isocalendar().week % len(files)] if files else None


def best_start(path: Path, total: float, avoid=(), vary: bool = False) -> float:
    """Де в треку починати: енергійний відрізок потрібної довжини, зсунутий на найближчий сильний удар
    (щоб музика вступала на «раз», а не з тихого вступу). Без vary це найенергійніший відрізок; з vary вибирається
    випадковий із кількох майже таких самих гучних, що не збігається з вже використаними (avoid, секунди)."""
    import numpy as np

    sr, hop = 11025, 551  # крок аналізу 0,05 с
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype="<f4")
    frames = len(x) // hop
    if frames * hop / sr <= total + 1:
        return 0.0
    rms = np.sqrt((x[: frames * hop].reshape(frames, hop) ** 2).mean(axis=1))
    flux = np.maximum(np.diff(rms, prepend=rms[0]), 0)
    win = int(total / 0.05)
    csum = np.concatenate([[0.0], np.cumsum(rms)])
    scores = (csum[win:] - csum[:-win]) / win  # середня гучність вікна, що починається з кадру i
    top = float(scores.max())
    chosen: list[int] = []
    for i in np.argsort(-scores):
        if scores[i] < 0.8 * top:
            break
        if any(abs(int(i) - c) < win * 0.6 for c in chosen) or any(abs(int(i) - int(a / 0.05)) < win * 0.5 for a in avoid):
            continue
        chosen.append(int(i))
        if len(chosen) >= 8:
            break
    if not chosen:
        chosen = [int(np.argmax(scores))]
    import random

    best = random.choice(chosen) if vary else chosen[0]
    lo, hi = max(best - 20, 0), min(best + 20, len(flux) - 1)  # ±1 с навколо
    start = lo + int(np.argmax(flux[lo : hi + 1]))
    return round(min(start * 0.05, frames * 0.05 - total - 0.5), 2)


def timing(messages) -> tuple[list[float], float]:
    """Скільки секунд тримати кожен пост і скільки триває заставка: ролик 20–30 с залежно від матеріалу."""
    def read_time(html_text: str) -> float:  # довший текст — довше на екрані
        chars = len(re.sub(r"<[^>]+>", "", html_text))
        return min(max(2.5 + chars / 80, 3.5), 6.5)

    holds = [read_time(m[0]) for m in messages]
    n = len(holds)
    fixed = (n - 1) * MOVE
    total = sum(holds) + fixed + OUTRO
    if total > MAX_TOTAL:
        k = (MAX_TOTAL - fixed - OUTRO) / sum(holds)
        holds = [max(h * k, HOLD_MIN) for h in holds]
    elif total < MIN_TOTAL:
        k = (MIN_TOTAL - fixed - OUTRO) / sum(holds)
        holds = [min(h * k, HOLD_MAX) for h in holds]
    outro = max(OUTRO, min(MIN_TOTAL - sum(holds) - fixed, 6.0))
    return holds, outro


def ease(t: float) -> float:
    return t * t * (3 - 2 * t)


def render(messages, topic: str, out: Path, music_arg: str | None = None, music_start: float | None = None, retro: bool = False, music_avoid=()) -> tuple[Path, float] | None:
    """Малює кадри з постами каналу й записує відео з музикою у out."""
    from playwright.sync_api import sync_playwright

    html = page_html(messages, topic)
    out.parent.mkdir(parents=True, exist_ok=True)
    music = out.with_suffix(".wav")
    n = len(messages)
    holds, outro_len = timing(messages)
    total = sum(holds) + (n - 1) * MOVE + outro_len
    source = None if retro else pick_music(music_arg)
    if source:
        if music_start is None:
            music_start = best_start(source, total, avoid=music_avoid or (), vary=True)
        print(f"Музика: {source.name}, з {music_start} с", file=sys.stderr)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(music_start), "-i", str(source), "-t", f"{total:.2f}",
             "-af", f"afade=t=in:d=0.3,afade=t=out:st={total - 1.5:.2f}:d=1.5,loudnorm=I=-18:TP=-2",
             "-ar", "44100", "-ac", "2", str(music)], check=True)
    else:  # власний чіптюн (ретро-тематика або порожня бібліотека)
        import make_tracks  # запасна доріжка, поки в assets/music немає справжніх треків
        number = 1 if retro else TOPICS[topic]["track"]
        make_music.write_wav(str(music), make_tracks.TRACKS[number - 1][2](total))
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-c:v", "mjpeg", "-framerate", str(FPS), "-i", "-",
         "-i", str(music), "-shortest",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", str(out)],
        stdin=subprocess.PIPE,
    )
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": CSS_W, "height": CSS_H}, device_scale_factor=2)
        page.set_content(html)
        page.wait_for_timeout(800)
        heights = page.evaluate("() => [...document.querySelectorAll('.slide .in')].map(e => e.offsetHeight)")
        tops = []
        for i, ((_, art), hgt) in enumerate(zip(messages, heights)):
            top = best_band(art["gray"], hgt)[0] if art and art.get("gray") is not None else TEXT_BOTTOM - hgt
            tops.append([i, round(top), hgt])
        print("Місця тексту (css px):", [t[1] for t in tops], file=sys.stderr)
        page.evaluate("(a) => window.setTops(a)", tops)
        n_fade = int(MOVE * FPS)
        life = [0.0] * n  # скільки секунд кадр уже на екрані: від цього залежать повільне наближення й поява тексту
        frames = []  # (непрозорість кожного кадру, наближення, поява тексту, непрозорість заставки)

        def snap(i: int, alpha: float, outro: float = 0.0) -> None:
            ops = [1.0 if j < i else 0.0 for j in range(n)]
            ops[i] = alpha
            texts = [round(ease(min(max(t - TEXT_DELAY, 0.0) / TEXT_IN, 1.0)), 4) for t in life]
            if i and alpha < 1.0:
                texts[i - 1] = round(1.0 - min(1.0, alpha * 2), 4)  # текст попереднього кадру зникає раніше, тексти не накладаються
            frames.append((ops, [round(ZOOM_PER_SEC * min(t, 12), 4) for t in life], texts, outro))

        for i in range(n):
            if i:
                for k in range(n_fade):
                    life[i] += 1 / FPS
                    life[i - 1] += 1 / FPS  # попередній ще видно, він теж продовжує рух
                    snap(i, ease((k + 1) / n_fade))
            for _ in range(int(holds[i] * FPS)):
                life[i] += 1 / FPS
                snap(i, 1.0)
        for k in range(int(outro_len * FPS)):
            life[n - 1] += 1 / FPS
            snap(n - 1, 1.0, min(1.0, k / (0.5 * FPS)))
        for ops, zs, es, outro in frames:
            page.evaluate("([a,z,e,o]) => window.setFrame(a,z,e,o)", [ops, zs, es, outro])
            ff.stdin.write(page.screenshot(type="jpeg", quality=93))
        browser.close()
    ff.stdin.close()
    ff.wait()
    music.unlink(missing_ok=True)
    print(f"Готово: {out} ({len(frames) / FPS:.1f} с, постів: {len(messages)})")
    return (source, music_start) if source else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--topic", choices=list(TOPICS), default="games", help="games (знижки), anime або series (реальні пости каналу)")
    parser.add_argument("--mock-images", action="store_true", help="підставні картинки (немає доступу до магазинів)")
    parser.add_argument("--music", help="файл або тека з музикою (за замовчуванням assets/music)")
    parser.add_argument("--music-start", type=float, default=None, help="з якої секунди брати трек (за замовчуванням: найенергійніше місце)")
    parser.add_argument("--retro", action="store_true", help="ретро-тематика: власний чіптюн замість бібліотеки")
    args = parser.parse_args()
    messages = build_messages(args.mock_images, args.topic)
    chosen = args.music or (None if args.retro else (str(choose_track(args.topic) or "") or None))
    render(messages, args.topic, Path(args.out), chosen, args.music_start, args.retro)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
