import pytest

from bootstrap_keys import desired_payload, reconcile, wait_ready

CLIENTS = [
    {"alias": "rag-app", "key_env": "RAG_KEY", "models": ["chat", "embed"], "rpm_limit": 120},
    {"alias": "kagent", "key_env": "KAGENT_KEY", "models": ["chat"], "rpm_limit": 30},
]
ENV = {"RAG_KEY": "sk-rag", "KAGENT_KEY": "sk-kagent"}


class FakeLiteLLM:
    """Simule l'API de gestion des clés de LiteLLM."""

    def __init__(self, existing=()):
        self.keys = {k: {} for k in existing}
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path.split("?")[0]))
        if path.startswith("/key/info"):
            key = path.split("key=")[1]
            return (200, {}) if key in self.keys else (404, {})
        if path == "/key/generate":
            if body["key"] in self.keys:
                return 400, {"error": "exists"}
            self.keys[body["key"]] = body
            return 200, body
        if path == "/key/update":
            self.keys[body["key"]] = body
            return 200, body
        return 404, {}


def test_first_run_creates_all_keys():
    api = FakeLiteLLM()
    assert reconcile(api, CLIENTS, ENV) == {"rag-app": "created", "kagent": "created"}
    assert api.keys["sk-rag"]["rpm_limit"] == 120


def test_second_run_is_idempotent_update():
    api = FakeLiteLLM()
    reconcile(api, CLIENTS, ENV)
    assert reconcile(api, CLIENTS, ENV) == {"rag-app": "updated", "kagent": "updated"}
    assert ("POST", "/key/generate") not in api.calls[4:]


def test_quota_change_is_applied():
    api = FakeLiteLLM(existing=["sk-rag", "sk-kagent"])
    changed = [{**CLIENTS[0], "rpm_limit": 10}, CLIENTS[1]]
    reconcile(api, changed, ENV)
    assert api.keys["sk-rag"]["rpm_limit"] == 10


def test_missing_key_is_rejected():
    with pytest.raises(ValueError, match="RAG_KEY"):
        reconcile(FakeLiteLLM(), CLIENTS, {"KAGENT_KEY": "sk-kagent"})


def test_payload_only_contains_declared_limits():
    payload = desired_payload({"alias": "a", "models": ["chat"]}, "sk-a")
    assert "rpm_limit" not in payload and payload["key_alias"] == "a"


def test_wait_ready_retries_then_succeeds():
    answers = iter([OSError(), (503, {}), (200, {})])

    def call(method, path, body=None):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    wait_ready(call, attempts=5, delay=0, sleep=lambda _s: None)


def test_wait_ready_gives_up():
    with pytest.raises(TimeoutError):
        wait_ready(lambda *a, **k: (503, {}), attempts=2, delay=0, sleep=lambda _s: None)
