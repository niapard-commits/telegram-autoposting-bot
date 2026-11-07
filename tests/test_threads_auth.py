import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import threads_auth as ta  # noqa: E402


class Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


def _setup(monkeypatch, tmp_path, responses, secret="  secret\n"):
    calls, notes = [], []

    def fake_get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        return responses[url.rsplit("/", 1)[-1]]

    monkeypatch.setattr(ta.requests, "get", fake_get)
    monkeypatch.setattr(ta.tp, "notify", lambda text: notes.append(text))
    monkeypatch.setattr(ta.tp, "TOKEN_FILE", tmp_path / "token.json")
    monkeypatch.setenv("THREADS_SHORT_TOKEN", " tok \n")
    if secret is None:
        monkeypatch.delenv("THREADS_APP_SECRET", raising=False)
    else:
        monkeypatch.setenv("THREADS_APP_SECRET", secret)
    return calls, notes


def test_exchange_ok_uses_long_token_and_strips_secret(monkeypatch, tmp_path):
    calls, notes = _setup(monkeypatch, tmp_path, {
        "access_token": Resp(200, {"access_token": "LONG", "expires_in": 5184000}),
        "me": Resp(200, {"id": "42", "username": "lutserii"})})
    assert ta.main() == 0
    assert calls[0][1]["client_secret"] == "secret" and calls[0][1]["access_token"] == "tok"
    saved = json.loads((tmp_path / "token.json").read_text(encoding="utf-8"))
    assert saved["access_token"] == "LONG" and saved["user_id"] == "42"
    assert "не потрібен" not in notes[-1]


def test_exchange_fails_but_token_works_is_saved_as_is(monkeypatch, tmp_path):
    _, notes = _setup(monkeypatch, tmp_path, {
        "access_token": Resp(400, {"error": {"message": "Invalid Client Secret"}}),
        "me": Resp(200, {"id": "42", "username": "lutserii"})})
    assert ta.main() == 0
    saved = json.loads((tmp_path / "token.json").read_text(encoding="utf-8"))
    assert saved["access_token"] == "tok" and saved["expires_at"] > 0
    assert "збережено як є" in notes[-1]


def test_missing_secret_still_works(monkeypatch, tmp_path):
    _, notes = _setup(monkeypatch, tmp_path, {
        "me": Resp(200, {"id": "42", "username": "lutserii"})}, secret=None)
    assert ta.main() == 0
    assert "не задано" in notes[-1]


def test_invalid_token_reports_both_errors(monkeypatch, tmp_path):
    _, notes = _setup(monkeypatch, tmp_path, {
        "access_token": Resp(400, {"error": "secret"}),
        "me": Resp(401, {"error": "bad token"})})
    assert ta.main() == 1
    assert not (tmp_path / "token.json").exists()
    assert "Обмін на довгий токен" in notes[-1]
