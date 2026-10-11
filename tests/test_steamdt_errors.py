import json
from email.message import Message
from urllib.error import URLError

import pytest

from steamdt import fetcher


def test_网络失败不再伪装成空候选(monkeypatch):
    def fail(*args, **kwargs):
        raise URLError("connection refused")

    monkeypatch.setattr(fetcher, "urlopen", fail)
    with pytest.raises(RuntimeError, match="connection refused"):
        fetcher.fetch_steamdt_data()


@pytest.mark.parametrize("payload, message", [
    ({"success": False, "errorCode": 403, "errorMsg": "access denied"}, "access denied"),
    ({"success": False, "errorCode": 100}, "100"),
    ([], "格式"),
])
def test_接口失败保留原因(monkeypatch, payload, message):
    class Response:
        headers = Message()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(payload).encode()

    monkeypatch.setattr(fetcher, "urlopen", lambda *a, **k: Response())
    with pytest.raises(RuntimeError, match=message):
        fetcher.fetch_steamdt_data(attempts=1)


def test_成功的空列表仍是正常无候选(monkeypatch):
    class Response:
        headers = Message()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"success":true,"data":[]}'

    monkeypatch.setattr(fetcher, "urlopen", lambda *a, **k: Response())
    assert fetcher.fetch_steamdt_data() == []
