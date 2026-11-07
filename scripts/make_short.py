"""Збирає вертикальне відео (YouTube Shorts, 1080x1920) «Топ-3 знижки» з власної графіки та голосу edge-tts.

Використання: python scripts/make_short.py items.json out.mp4 [--voice uk-UA-OstapNeural]
Якщо edge-tts не встановлено або недоступний, відео збирається без голосу (для перегляду макета).
Усі кадри намальовані самі (без постерів і кліпів із ігор), тож авторські права не порушуються."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
W, H = 1080, 1920
FONT_BOLD = ROOT / "assets/fonts/Inter-Bold.otf"
FONT_MED = ROOT / "assets/fonts/Inter-Medium.otf"
AVATAR = ROOT / "assets/avatar.png"
PAUSE = 0.5  # тиша після кожної фрази, с
MIN_SCENE = 2.5
TOP, BOTTOM = (28, 22, 64), (92, 38, 110)  # градієнт фону
ACCENT, TEXT, MUTED = (255, 196, 61), (255, 255, 255), (190, 186, 220)


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def gradient() -> Image.Image:
    img = Image.new("RGB", (W, H))
    px = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        px.line([(0, y), (W, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    return img


def load_cover(url: str | None) -> Image.Image | None:
    """Обкладинка гри з магазину; якщо не вдалося завантажити, сцена малюється без неї."""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return Image.open(io.BytesIO(resp.read())).convert("RGB")
    except Exception as exc:
        print(f"Обкладинку не завантажено ({type(exc).__name__}): {url}", file=sys.stderr)
        return None


def background(cover: Image.Image | None = None) -> Image.Image:
    """Розмита й притемнена обкладинка на весь кадр (або градієнт) і логотип каналу."""
    base = gradient()
    if cover is not None:
        scale = max(W / cover.width, H / cover.height)
        big = cover.resize((round(cover.width * scale), round(cover.height * scale)))
        left, top = (big.width - W) // 2, (big.height - H) // 2
        blurred = big.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(38))
        base = Image.blend(blurred, base, 0.55)
    if AVATAR.exists():
        logo = Image.open(AVATAR).convert("RGBA").resize((130, 130))
        mask = Image.new("L", (130, 130), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 129, 129), fill=255)
        base.paste(logo, (W // 2 - 65, 70), mask)
    return base


def paste_card(img: Image.Image, cover: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Обкладинка із заокругленими кутами й тінню, вписана в прямокутник box (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    scale = min((x1 - x0) / cover.width, (y1 - y0) / cover.height)
    card = cover.resize((round(cover.width * scale), round(cover.height * scale)))
    x, y = x0 + ((x1 - x0) - card.width) // 2, y0 + ((y1 - y0) - card.height) // 2
    mask = Image.new("L", card.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, card.width - 1, card.height - 1), radius=36, fill=255)
    shadow = Image.new("RGBA", (card.width + 120, card.height + 120), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((60, 70, card.width + 60, card.height + 70), radius=40, fill=(0, 0, 0, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(26))
    img.paste(shadow, (x - 60, y - 60), shadow)
    img.paste(card, (x, y), mask)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=fnt) <= max_w or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    return lines + [line] if line else lines


def centered(draw, y: int, text: str, fnt, fill, max_w: int = W - 160, gap: int = 14) -> int:
    for line in wrap(draw, text, fnt, max_w):
        w = draw.textlength(line, font=fnt)
        draw.text(((W - w) / 2, y), line, font=fnt, fill=fill)
        y += fnt.size + gap
    return y


def money(value: float) -> str:
    return f"{int(value + 0.5):,}".replace(",", " ") + " ₴"


def scene_hook(covers: list[Image.Image | None]) -> Image.Image:
    img = background(next((c for c in covers if c), None))
    d = ImageDraw.Draw(img)
    y = centered(d, 330, "ТОП-3 ЗНИЖКИ", font(FONT_BOLD, 118), ACCENT, max_w=W - 80)
    y = centered(d, y + 10, "на консолях цього тижня", font(FONT_BOLD, 64), TEXT)
    centered(d, y + 20, "PS Store та Xbox · Україна", font(FONT_MED, 50), MUTED)
    size, gap = 310, 30
    x = (W - (3 * size + 2 * gap)) // 2
    for cover in covers[:3]:
        if cover is not None:
            paste_card(img, cover, (x, 900, x + size, 900 + size))
        x += size + gap
    return img


def scene_deal(n: int, item: dict, cover: Image.Image | None) -> Image.Image:
    img = background(cover)
    d = ImageDraw.Draw(img)
    if cover is not None:
        paste_card(img, cover, (100, 250, W - 100, 1030))
    # номер на кутку обкладинки
    d.ellipse((60, 215, 200, 355), fill=ACCENT)
    nf = font(FONT_BOLD, 96)
    d.text((130 - d.textlength(str(n), font=nf) / 2, 235), str(n), font=nf, fill=(40, 24, 70))
    y = centered(d, 1060, item["title"], font(FONT_BOLD, 74), TEXT, max_w=W - 120, gap=12)
    # бейдж знижки зліва, ціни справа
    row = max(y + 30, 1250)
    badge, bf = f'−{item["discount"]}%', font(FONT_BOLD, 120)
    bw = d.textlength(badge, font=bf)
    d.rounded_rectangle((70, row, 70 + bw + 80, row + 190), radius=46, fill=ACCENT)
    d.text((110, row + 22), badge, font=bf, fill=(40, 24, 70))
    old_f, new_f = font(FONT_MED, 64), font(FONT_BOLD, 150)
    old, new = money(item["old"]), money(item["new"])
    ow, nw = d.textlength(old, font=old_f), d.textlength(new, font=new_f)
    ox, ny = W - 70 - ow, row - 10
    d.text((ox, ny), old, font=old_f, fill=MUTED)
    d.line([(ox - 6, ny + 44), (ox + ow + 6, ny + 44)], fill=(255, 110, 110), width=7)
    d.text((W - 70 - nw, ny + 76), new, font=new_f, fill=TEXT)
    platforms = item["platforms"].replace("XOne", "Xbox One").replace("XSX", "Xbox Series")
    centered(d, 1700, f'{item["store"]} · {platforms} · Україна', font(FONT_MED, 46), MUTED)
    return img


def scene_outro(covers: list[Image.Image | None]) -> Image.Image:
    img = background(next((c for c in covers if c), None))
    d = ImageDraw.Draw(img)
    y = centered(d, 560, "Знижки та роздачі", font(FONT_BOLD, 96), TEXT)
    y = centered(d, y, "щодня в Telegram", font(FONT_BOLD, 96), TEXT)
    y = centered(d, y + 50, "Лут і Серія", font(FONT_BOLD, 150), ACCENT)
    centered(d, y + 40, "@lut_i_serii", font(FONT_MED, 64), MUTED)
    return img


def spoken_money(value: float) -> str:
    return f"{int(value + 0.5)} гривень"


def narration(items: list[dict]) -> list[str]:
    lines = ["Три знижки на консолях до вихідних."]
    for n, it in zip(("Перша", "Друга", "Третя"), items):
        lines.append(f'{n}: {it["spoken"]}. Мінус {it["discount"]} відсотків, лише {spoken_money(it["new"])}.')
    lines.append("Більше знижок щодня в Телеграмі: Лут і Серія.")
    return lines


async def synth(texts: list[str], voice: str, folder: Path) -> list[Path]:
    import edge_tts

    paths = []
    for i, text in enumerate(texts):
        out = folder / f"voice{i}.mp3"
        await edge_tts.Communicate(text, voice, rate="+5%").save(str(out))
        paths.append(out)
    return paths


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(out.strip())


def run(*cmd: str) -> None:
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("items")
    parser.add_argument("out")
    parser.add_argument("--voice", default="uk-UA-OstapNeural")
    parser.add_argument("--frames-only", action="store_true", help="лише зберегти кадри PNG поруч із відео")
    args = parser.parse_args()
    items = json.loads(Path(args.items).read_text(encoding="utf-8"))[:3]
    covers = [load_cover(it.get("image")) for it in items]
    scenes = [scene_hook(covers)] + [scene_deal(i + 1, it, covers[i]) for i, it in enumerate(items)] + [scene_outro(covers)]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.frames_only:
        for i, scene in enumerate(scenes):
            scene.save(out.with_name(f"{out.stem}_{i}.png"))
        return 0
    texts = narration(items)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        try:
            voices = asyncio.run(synth(texts, args.voice, folder))
        except Exception as exc:  # без голосу відео все одно збираємо, щоб побачити макет
            print(f"Голос недоступний ({type(exc).__name__}): збираю без звуку", file=sys.stderr)
            voices = []
        parts = []
        for i, scene in enumerate(scenes):
            png = folder / f"scene{i}.png"
            scene.save(png)
            length = max(MIN_SCENE, duration(voices[i]) + PAUSE) if voices else MIN_SCENE + 1
            part = folder / f"part{i}.mp4"
            frames = int(length * 30) + 1
            zoom = (f"scale=2160:-1,zoompan=z='min(zoom+0.0007,1.07)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                    f":d={frames}:s={W}x{H}:fps=30,fade=t=in:st=0:d=0.25,format=yuv420p")
            cmd = ["ffmpeg", "-y", "-i", str(png)]
            if voices:
                cmd += ["-i", str(voices[i]), "-af", "apad", "-shortest"]
            else:
                cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-shortest"]
            cmd += ["-t", f"{length:.2f}", "-vf", zoom, "-r", "30",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-ar", "44100", "-ac", "1", str(part)]
            run(*cmd)
            parts.append(part)
        listing = folder / "list.txt"
        listing.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
        run("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out))
    print(f"Готово: {out} ({duration(out):.1f} с, голос: {'так' if voices else 'ні'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
