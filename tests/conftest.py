import pytest
import requests

from lutserii.config import Config
from lutserii.db import StateDB
from lutserii.main import Context


class FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    @property
    def text(self):
        return self._data if isinstance(self._data, str) else ""

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeSession:
    """Підміна requests.Session: віддає заготовлені відповіді за адресою."""

    def __init__(self, routes):
        self.routes = routes  # url -> дані, FakeResponse або функція(params/json)
        self.calls = []

    def _respond(self, url, payload):
        self.calls.append((url, payload))
        route = self.routes[url]
        if callable(route):
            route = route(payload)
        return route if isinstance(route, FakeResponse) else FakeResponse(route)

    def get(self, url, params=None, timeout=None, **kwargs):
        return self._respond(url, params)

    def post(self, url, json=None, timeout=None, **kwargs):
        return self._respond(url, json)


@pytest.fixture
def db():
    database = StateDB(":memory:")
    yield database
    database.close()


@pytest.fixture
def make_ctx(db):
    def _make(routes=None, **cfg_overrides):
        sent = []
        logs = []
        ctx = Context(
            cfg=Config(**cfg_overrides),
            session=FakeSession(routes or {}),
            db=db,
            send=lambda text, photo: sent.append((text, photo)),
            pause=0,
            log=logs.append,
        )
        return ctx, sent, logs

    return _make


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    """Тести не пишуть файли (data/probe/…) у репозиторій."""
    monkeypatch.chdir(tmp_path)
