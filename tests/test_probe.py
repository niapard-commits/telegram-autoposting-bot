import json

from conftest import FakeResponse

from lutserii import main as app
from lutserii import probe


class ItadSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None, **kw):
        self.calls.append(("GET", url, params))
        if url.endswith("/games/lookup/v1") and params.get("title"):
            return FakeResponse({"found": True, "game": {"id": "uuid-1", "title": "Portal 2"}})
        if url.endswith("/deals/v2"):
            return FakeResponse({"list": [{"id": f"live-{i}"} for i in range(10)]})
        return FakeResponse({"found": False})

    def post(self, url, params=None, json=None, timeout=None, **kw):
        self.calls.append(("POST", url, params, json))
        return FakeResponse([{"id": "uuid-1", "deals": [{"price": {"amount": 1, "currency": "UAH"}}]}])


def test_probe_writes_shrunk_responses_without_key(tmp_path):
    out = tmp_path / "probe" / "itad.json"
    session = ItadSession()
    results = probe.run_probe(session, "SECRETKEY", str(out))
    text = out.read_text(encoding="utf-8")
    assert "SECRETKEY" not in text
    data = json.loads(text)
    assert len(data["calls"]) == len(results) == 10
    assert any(c["request"] == "POST /games/prices/v3" and c["body_sent"] == ["uuid-1"] for c in data["calls"])
    deals = next(c for c in data["calls"] if c["request"] == "GET /deals/v2")
    assert len(deals["response"]["list"]) == 3  # скорочено


def test_probe_without_key_and_network_error(tmp_path):
    out = tmp_path / "itad.json"
    assert probe.run_probe(ItadSession(), "", str(out)) == [{"error": "ITAD_API_KEY не задано"}]

    class Boom:
        def get(self, *a, **k):
            raise ConnectionError("boom")

    results = probe.run_probe(Boom(), "K", str(out))
    assert all(r.get("error") == "ConnectionError" for r in results)


def test_probe_task_reports(make_ctx, monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "OUT", str(tmp_path / "itad.json"))
    monkeypatch.setattr(probe, "run_probe", lambda s, k, p=None: [{"status": 200}, {"status": 404}])
    ctx, _, logs = make_ctx({}, itad_api_key="K")
    previews = []
    ctx.preview = lambda t, p: previews.append(t)
    assert app.run_task(ctx, "probe") == []
    assert "200, 404" in previews[0]
