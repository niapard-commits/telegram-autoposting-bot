"""Читання публічних Telegram-каналів через їхню веб-сторінку t.me/s/<назва> (без акаунта і ключів)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

URL = "https://t.me/s/{name}"
TIMEOUT = 25
NAME = re.compile(r"^[A-Za-z0-9_]{4,64}$")


@dataclass(frozen=True)
class ChannelPost:
    id: int
    text: str
    date: datetime  # у UTC


@dataclass(frozen=True)
class Channel:
    title: str
    posts: tuple[ChannelPost, ...]


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.raw: list[dict[str, Any]] = []
        self._cur: dict[str, Any] | None = None
        self._text_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "meta" and a.get("property") == "og:title" and not self.title:
            self.title = a.get("content", "")
        if tag == "div" and a.get("data-post"):
            self._cur = {"post": a["data-post"], "text": [], "date": None}
            self.raw.append(self._cur)
            self._text_depth = 0
            return
        if self._cur is None:
            return
        if self._text_depth:
            if tag == "div":
                self._text_depth += 1
            elif tag == "br":
                self._cur["text"].append("\n")
            return
        classes = a.get("class", "").split()
        if tag == "div" and "tgme_widget_message_text" in classes:
            if self._cur["text"]:
                self._cur["text"].append("\n")
            self._text_depth = 1
        elif tag == "time" and self._cur["date"] is None and a.get("datetime"):
            self._cur["date"] = a["datetime"]

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self._text_depth:
            self._text_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._cur is not None and self._text_depth:
            self._cur["text"].append(data)


def parse_channel(markup: str) -> Channel:
    parser = _Parser()
    parser.feed(markup)
    parser.close()
    posts: list[ChannelPost] = []
    for item in parser.raw:
        try:
            post_id = int(str(item["post"]).rsplit("/", 1)[-1])
            date = datetime.fromisoformat(item["date"]).astimezone(timezone.utc)
        except (ValueError, TypeError):
            continue  # допис без дати чи номера нам не потрібен
        text = re.sub(r"[ \t]+", " ", "".join(item["text"])).strip()
        posts.append(ChannelPost(post_id, text, date))
    return Channel(parser.title.strip(), tuple(posts))


def fetch_channel(session: Any, name: str) -> Channel:
    if not NAME.match(name):
        raise ValueError(f"Недійсна назва каналу: {name!r}")
    resp = session.get(URL.format(name=name), timeout=TIMEOUT)
    resp.raise_for_status()
    return parse_channel(resp.text)
