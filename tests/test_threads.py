import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

HOOKS = json.loads((Path(__file__).resolve().parent.parent / "data/threads_hooks.json").read_text(encoding="utf-8"))


def test_hooks_fit_and_say_company_not_two():
    import threads_post as tp

    for pool, texts in HOOKS.items():
        assert len(texts) >= 6
        for topic in ("games",) if pool == "games" else ("anime", "series"):
            for t in texts:
                assert len(tp.compose_hook(topic, t)) <= 500
    assert not any("на двох" in t for texts in HOOKS.values() for t in texts)


def test_variants_rotate_without_early_repeats():
    import threads_post as tp

    state, hooks = {}, HOOKS["games"]
    picked = [tp.pick_variant("games", hooks, state, 1000 + i)[0] for i in range(len(hooks) * 2)]
    assert sorted(picked[: len(hooks)]) == list(range(len(hooks)))  # спершу всі по разу
    assert sorted(picked[len(hooks):]) == list(range(len(hooks)))


def test_refresh_reminder_once_per_round():
    import threads_post as tp

    state, hooks = {}, ["a", "b"]
    for i in range(4):
        tp.pick_variant("p", hooks, state, i)
    assert "по 2 разів" in tp.refresh_reminder("p", hooks, state)
    assert tp.refresh_reminder("p", hooks, state) is None  # повторно про те саме коло не нагадує


def test_discount_of():
    import threads_post as tp

    assert tp.discount_of("💰 <s>x</s> → <b>y</b> (−50%)") == 50
    assert tp.discount_of("знижка 67%, тепер") == 67
    assert tp.discount_of("Безкоштовно") is None


def test_compose_data_needs_two_titles_and_fits():
    pytest.importorskip("numpy")
    import threads_post as tp

    items = [{"text": f"🔥 <b>Game {n} — знижка {n * 10}%, тепер 100 ₴ у PS Store</b>\nщось", "photos": ["u"]} for n in (1, 2, 3)]
    text = tp.compose_data("games", items, {})
    assert text and "• Game 1" in text and "t.me/lut_i_serii" in text and text.endswith("#ігри") and len(text) <= 500
    assert tp.compose_data("games", items[:1], {}) is None


def test_publish_falls_back_to_text_when_image_rejected(monkeypatch):
    import threads_post as tp

    calls = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._b, self.text = code, body, json.dumps(body)

        def json(self):
            return self._b

    def fake_post(url, data=None, timeout=0):
        calls.append((url.rsplit("/", 1)[-1], data.get("media_type")))
        if url.endswith("/threads") and data["media_type"] == "IMAGE":
            return R(400, {"error": "bad image"})
        return R(200, {"id": "42"})

    monkeypatch.setattr(tp.requests, "post", fake_post)
    monkeypatch.setattr(tp.time, "sleep", lambda s: None)
    assert tp.publish({"user_id": "1", "access_token": "t"}, "hi", "http://img") == "42"
    assert ("threads", "TEXT") in calls and calls[-1][0] == "threads_publish"


def test_jpeg_url_converts_webp_only():
    import threads_post as tp

    assert tp.jpeg_url("https://x/a.jpg") == "https://x/a.jpg"
    assert tp.jpeg_url("https://x/a.png?v=1") == "https://x/a.png?v=1"
    conv = tp.jpeg_url("https://c.psprices.com/400/ab.webp")
    assert conv.startswith("https://wsrv.nl/?url=https%3A%2F%2Fc.psprices.com") and "output=jpg" in conv


def test_anime_and_series_have_separate_hook_pools():
    assert set(HOOKS) == {"games", "anime", "series"}
    assert not set(HOOKS["anime"]) & set(HOOKS["series"])
    assert all("серіал" not in h.lower() for h in HOOKS["anime"])
    assert all("аніме" not in h.lower() for h in HOOKS["series"])


def test_go_live_by_date():
    import threads_post as tp
    from datetime import date
    assert not tp.go_live(date(2026, 11, 1))
    assert tp.go_live(date(2026, 11, 2))
    assert tp.go_live(date(2026, 11, 1), "2026-10-31")
