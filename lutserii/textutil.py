"""Дрібні утиліти для очищення й скорочення тексту."""

from __future__ import annotations

import html
import re

_BR = re.compile(r"<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")
_SOURCE = re.compile(r"\s*[\(\[](?:Source|Written by)[^)\]]*[\)\]]", re.I)
_SPACES = re.compile(r"\s+")
_CYRILLIC = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")
_LETTER = re.compile(r"[^\W\d_]")


_EDITION = re.compile(
    r"[\s:\-–—]*(?:the\s+)?"
    r"(?:(?:ultimate|deluxe|digital|gold|platinum|premium|standard|complete|definitive|"
    r"game of the year|goty|anniversary|collector'?s|special|enhanced|legendary|director'?s cut)\s+)+"
    r"(?:edition|bundle|collection|pack)\s*$",
    re.I,
)


def base_title(title: str) -> str:
    """«Back 4 Blood: Ultimate Edition» -> «Back 4 Blood» (щоб знайти базову гру)."""
    stripped = _EDITION.sub("", title.strip()).strip(" :-–—")
    return stripped or title.strip()


def norm_title(title: str) -> str:
    """Порівняльний вигляд назви: без регістру, розділових знаків і пробілів."""
    return re.sub(r"[\W_]+", "", title.casefold())


def clean_text(raw: str | None) -> str:
    """Прибирає HTML-теги, сутності та підписи джерел; стискає пробіли."""
    text = _BR.sub(" ", raw or "")
    text = _TAG.sub("", text)
    text = html.unescape(text)
    text = _SOURCE.sub("", text)
    return _SPACES.sub(" ", text).strip()


def shorten(text: str, limit: int) -> str:
    """Скорочує до limit символів, бажано по закінченню речення, інакше по слову."""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    for sep in (". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx >= limit * 0.5:
            return cut[: idx + 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(",;:- ") + "…"


def has_cyrillic(text: str) -> bool:
    """Чи текст здебільшого кирилицею (щоб не слати англійський опис у український канал)."""
    letters = _LETTER.findall(text)
    if not letters:
        return False
    return len(_CYRILLIC.findall(text)) / len(letters) > 0.5


def titles_match(a: str, b: str) -> bool:
    """Назви збігаються: точно або одна є початком іншої («… Season 2»); коротка схожість не рахується."""
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return False
    short, long_ = sorted((na, nb), key=len)
    return na == nb or (len(short) >= 8 and long_.startswith(short))


_SUBTITLE = re.compile(r"\s*[:–—]\s+|\s+-\s+")
_TRAILING = re.compile(
    r"\s+(?:\d{1,2}|ii|iii|iv|vi|vii|viii|ix|x|remastered|remake|redux|hd|definitive|enhanced)\s*$", re.I
)


def franchise_name(title: str) -> str:
    """«Watch_Dogs: Legion» і «Watch Dogs 2» -> «Watch Dogs»: спільна назва серії ігор."""
    name = _SUBTITLE.split(base_title(title).replace("_", " "), 1)[0].strip()
    while True:
        shorter = _TRAILING.sub("", name).strip()
        if shorter == name or not shorter:
            return name
        name = shorter


def franchise_key(title: str) -> str:
    """Порівняльний ключ серії; порожній, якщо назва надто коротка, щоб довіряти збігу."""
    key = norm_title(franchise_name(title))
    return key if len(key) >= 4 else ""


_SENTENCE = re.compile(r".+?(?:[.!?…]+(?=\s|$)|$)", re.S)


def whole_sentences(text: str, limit: int) -> str | None:
    """Лише цілі речення, що вміщаються в limit; None, якщо не вмістилося навіть перше (краще без опису, ніж з обрізаним)."""
    text = text.strip()
    if len(text) <= limit:
        return text or None  # опис цілком вміщається: це не обрізка
    out = ""
    for match in _SENTENCE.finditer(text):
        sentence = match.group(0).strip()
        if not sentence:
            continue
        if sentence[-1] not in ".!?…":
            break  # хвіст без крапки: найімовірніше, обірваний
        candidate = f"{out} {sentence}".strip()
        if len(candidate) > limit:
            break
        out = candidate
    return out or None


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
# Лише ознаки реклами й ін'єкцій. Звичайні сюжетні слова («ставки», «казино», «модель», «інструкції»)
# сюди не входять: в описах аніме й ігор вони трапляються у цілком чесному сенсі.
_SPAM = re.compile(
    r"https?:|t\.me|www\.|@|#|`|\bhttp|\bignore\b|previous\s+instruction|system\s+prompt|"
    r"\bпідписуй|\bпідпишись|\bпідписатис|\bпромокод|\bперейд[іи]\s+за|\bнатисни\b|\bпереходь\b|"
    r"\bігноруй|\bмовна\s+модел|\bяк\s+(?:ші|ai)\b",
    re.I,
)


def sanitize_source(text: str) -> str:
    """Текст із зовнішнього джерела перед тим, як віддати його ШІ: без керівних і невидимих символів
    та без кутових дужок (щоб його не вдалося видати за наші службові теги)."""
    text = _CONTROL.sub(" ", text).replace("<", " ").replace(">", " ")
    return _SPACES.sub(" ", text).strip()


def looks_like_spam_or_injection(text: str) -> bool:
    """Посилання, @згадки, хештеги, заклики й службові слова, яких у нашому тексті бути не може."""
    return bool(_SPAM.search(text))


def numbers_in(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def grounded_in(text: str, source: str) -> bool:
    """Усі числа з text є в source: ШІ не вигадав роки, ціни й кількості."""
    return numbers_in(text) <= numbers_in(source)


_KONOHA = re.compile(r"\bКоног(?=[аиіуо])")


def fix_terms(text: str) -> str:
    """Усталені українські написання: команди озвучення пишуть «Коноха», а не «Конога» (відмінки зберігаються)."""
    return _KONOHA.sub("Конох", text)
