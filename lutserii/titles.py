"""Українські назви аніме з вашого файлу titles_ua.json (ручна добірка)."""

from __future__ import annotations

import json


def _norm(key: str) -> str:
    return key.strip().casefold()


def load_titles(path: str) -> dict[str, str]:
    """Читає {"108465": "Назва", "Original Title": "Назва"}; ключі на «_» ігноруються."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        print(f"Не вдалося прочитати {path} ({type(exc).__name__}); назви без перекладу.")
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        _norm(key): value.strip()
        for key, value in data.items()
        if isinstance(key, str)
        and isinstance(value, str)
        and value.strip()
        and not key.startswith("_")
    }


def lookup_title(titles: dict[str, str], media_id: int, title: str) -> str | None:
    """Шукає за номером AniList, потім за назвою так, як її показує бот."""
    return titles.get(str(media_id)) or titles.get(_norm(title))
