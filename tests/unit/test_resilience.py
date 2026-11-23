"""Resilience building blocks shared by every service."""

import threading
import time

import pytest
import requests
from ase_common import (
    CircuitBreaker,
    CircuitOpen,
    RateLimiter,
    config,
    create_app,
    db_client,
    guarded_request,
    json_object,
    parse_limit,
    resilience,
    run_concurrently,
    service_identity,
    service_token,
)
from flask import g


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeResponse:
    def __init__(self, status=200):
        self.status_code = status


class ScriptedSession:
    """Answers with the next scripted outcome: a status code or an exception."""

    def __init__(self, *outcomes):
        self.outcomes, self.calls = list(outcomes), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        outcome = self.outcomes.pop(0) if self.outcomes else 200
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)


@pytest.fixture
def breaker(monkeypatch):
    clock = Clock()
    instance = CircuitBreaker("unit-target", failures=3, reset_after=10, clock=clock)
    monkeypatch.setitem(resilience._breakers, "unit-target", instance)
    instance.clock = clock
    return instance


# ------------------------------------------------------------------ circuit breaker
def test_the_circuit_opens_after_consecutive_failures_and_then_fails_fast(breaker):
    session = ScriptedSession(*[requests.ConnectionError("down")] * 3)
    for _ in range(3):
        with pytest.raises(requests.ConnectionError):
            guarded_request(session, "unit-target", "GET", "https://x/y")
    assert breaker.is_open
    with pytest.raises(CircuitOpen) as refused:
        guarded_request(session, "unit-target", "GET", "https://x/y")
    assert len(session.calls) == 3, "an open circuit must not touch the network"
    assert refused.value.retry_after >= 1


def test_a_success_resets_the_failure_count(breaker):
    session = ScriptedSession(
        requests.ConnectionError("x"), 200, requests.ConnectionError("x"), 200
    )
    for _ in range(4):
        try:
            guarded_request(session, "unit-target", "GET", "https://x/y")
        except requests.ConnectionError:
            pass
    assert not breaker.is_open


def test_half_open_lets_one_trial_through_and_closes_on_success(breaker):
    session = ScriptedSession(*[503] * 3, 200)
    for _ in range(3):
        guarded_request(session, "unit-target", "GET", "https://x/y")  # 503 counts as a failure
    assert breaker.is_open
    breaker.clock.now += 11
    assert guarded_request(session, "unit-target", "GET", "https://x/y").status_code == 200
    assert not breaker.is_open


def test_a_failed_trial_reopens_the_circuit(breaker):
    session = ScriptedSession(*[requests.ConnectionError("x")] * 4)
    for _ in range(3):
        with pytest.raises(requests.ConnectionError):
            guarded_request(session, "unit-target", "GET", "https://x/y")
    breaker.clock.now += 11
    with pytest.raises(requests.ConnectionError):
        guarded_request(session, "unit-target", "GET", "https://x/y")  # the trial fails
    with pytest.raises(CircuitOpen):
        guarded_request(session, "unit-target", "GET", "https://x/y")


def test_only_one_trial_runs_while_half_open(breaker):
    breaker.failure(), breaker.failure(), breaker.failure()
    breaker.clock.now += 11
    breaker.before_call()  # the trial
    with pytest.raises(CircuitOpen):
        breaker.before_call()  # everyone else keeps failing fast


@pytest.mark.parametrize("status", [200, 201, 400, 401, 403, 404, 409, 422, 429])
def test_client_errors_never_trip_the_breaker(breaker, status):
    session = ScriptedSession(*[status] * 10)
    for _ in range(10):
        guarded_request(session, "unit-target", "GET", "https://x/y")
    assert not breaker.is_open


def test_timeouts_are_explicit_and_separate_for_connect_and_read(breaker):
    session = ScriptedSession(200)
    guarded_request(session, "unit-target", "GET", "https://x/y")
    connect, read = session.calls[0][2]["timeout"]
    assert 0 < connect <= 3 and read >= connect


def test_the_request_id_follows_the_call(breaker, key_dir):
    app = create_app("tester", required_keys=("service",))
    session = ScriptedSession(200)
    with app.test_request_context(headers={"X-Request-ID": "abcdef123456"}):
        app.preprocess_request()
        guarded_request(session, "unit-target", "GET", "https://x/y", headers={"A": "b"})
    headers = session.calls[0][2]["headers"]
    assert headers["X-Request-ID"] == "abcdef123456" and headers["A"] == "b"


def test_the_pooled_session_retries_only_what_is_safe():
    session = resilience.pooled_session()
    retry = session.get_adapter("https://x").max_retries
    assert retry.total == 2 and retry.connect == 2
    assert set(retry.allowed_methods) == {"GET"}, "a POST must never be replayed after a read error"
    assert 503 in retry.status_forcelist


# ------------------------------------------------------------------ catalogue cache
class CatalogueDb(db_client.DB):
    def __init__(self):
        super().__init__("https://db")
        self.reads, self.fail = 0, False

    def select(self, table, **kwargs):
        self.reads += 1
        if self.fail:
            raise requests.ConnectionError("db down")
        return [{"id": self.reads}]


def test_the_catalogue_is_served_stale_when_the_refresh_fails(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(db_client.time, "monotonic", lambda: now[0])
    db = CatalogueDb()
    assert db.catalogue() == [{"id": 1}]
    now[0] += config.CATALOGUE_TTL + 1
    db.fail = True
    assert db.catalogue() == [{"id": 1}], "stale data beats an outage for a rarely changing list"
    reads = db.reads
    now[0] += 1
    db.catalogue()
    assert db.reads == reads, "a failed refresh is not retried on every request"
    now[0] += config.CATALOGUE_RETRY + 1
    db.fail = False
    assert db.catalogue() == [{"id": db.reads}]


def test_the_first_catalogue_load_failing_is_an_error():
    db = CatalogueDb()
    db.fail = True
    with pytest.raises(requests.ConnectionError):
        db.catalogue()


def test_concurrent_cache_misses_cause_one_refresh():
    db = CatalogueDb()
    barrier = threading.Barrier(8)

    def reader():
        barrier.wait()
        db.catalogue()

    threads = [threading.Thread(target=reader) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert db.reads == 1


# ------------------------------------------------------------------ service tokens
def test_service_tokens_are_reused_then_renewed(key_dir, monkeypatch):
    now = [5000.0]
    monkeypatch.setattr(service_identity.time, "monotonic", lambda: now[0])
    first = service_token("players")
    assert service_token("players") == first
    assert service_token("teams") != first, "the audience is part of the token"
    now[0] += service_identity.SERVICE_TOKEN_REUSE + 1
    assert service_token("players") != first


def test_a_reused_token_always_has_enough_life_left():
    assert service_identity.SERVICE_TOKEN_TTL - service_identity.SERVICE_TOKEN_REUSE >= 15


# ------------------------------------------------------------------ rate limiter memory
def test_expired_keys_are_swept_so_the_table_stays_small():
    clock = Clock()
    limiter = RateLimiter(clock=clock)
    for number in range(500):
        limiter.check(f"client{number}", 5)
    assert len(limiter) == 500
    clock.now += 61
    limiter.check("someone-new", 5)
    assert len(limiter) == 1, "dead keys must not be kept until the hard cap"


def test_live_keys_are_kept_by_the_sweep():
    clock = Clock()
    limiter = RateLimiter(clock=clock)
    limiter.check("active", 1)
    clock.now += 30
    limiter.check("other", 1)
    clock.now += 31  # "active" is now 61 s old, "other" 31 s
    limiter.check("trigger", 1)
    assert limiter.check("other", 1) > 0, "a live counter must survive the sweep"


# ------------------------------------------------------------------ request helpers
@pytest.fixture
def helpers_app(key_dir):
    app = create_app("tester", required_keys=("service",))

    @app.post("/echo")
    def echo():
        return json_object()

    @app.get("/limit")
    def limit():
        return {"limit": parse_limit(default=10, maximum=50)}

    return app.test_client()


@pytest.mark.parametrize("body", ["[1]", '"text"', "7", "true"])
def test_a_json_body_that_is_not_an_object_is_a_400(helpers_app, body):
    response = helpers_app.post("/echo", data=body, content_type="application/json")
    assert response.status_code == 400 and response.json["error"] == "Bad Request"


def test_empty_and_valid_bodies_are_accepted(helpers_app):
    assert helpers_app.post("/echo").json == {}
    assert helpers_app.post("/echo", json={"a": 1}).json == {"a": 1}
    # a body that is not JSON is the client's mistake: 400, never silently treated as empty
    assert (
        helpers_app.post("/echo", data="not json", content_type="application/json").status_code
        == 400
    )


@pytest.mark.parametrize(
    "query, expected", [("", 10), ("?limit=3", 3), ("?limit=999", 50), ("?limit=1", 1)]
)
def test_limit_default_and_cap(helpers_app, query, expected):
    assert helpers_app.get(f"/limit{query}").json["limit"] == expected


@pytest.mark.parametrize("query", ["?limit=0", "?limit=-5", "?limit=abc", "?limit=1.5", "?limit="])
def test_a_bad_limit_is_always_a_400(helpers_app, query):
    assert helpers_app.get(f"/limit{query}").status_code == 400


# ------------------------------------------------------------------ request id & error mapping
def test_request_ids_are_created_echoed_and_validated(key_dir):
    app = create_app("tester", required_keys=("service",))

    @app.get("/rid")
    def rid():
        return {"rid": g.request_id}

    client = app.test_client()
    created = client.get("/rid")
    assert created.headers["X-Request-ID"] == created.json["rid"] and len(created.json["rid"]) == 16
    assert (
        client.get("/rid", headers={"X-Request-ID": "good-id-12345"}).json["rid"] == "good-id-12345"
    )
    forged = client.get("/rid", headers={"X-Request-ID": "bad id <script>"})
    assert forged.json["rid"] != "bad id <script>" and len(forged.json["rid"]) == 16
    assert client.get("/missing").headers["X-Request-ID"]  # also on error responses


def test_timeouts_are_504_open_circuits_are_503_and_other_failures_502(key_dir):
    app = create_app("tester", required_keys=("service",))

    @app.get("/timeout")
    def timeout():
        raise requests.ReadTimeout("slow")

    @app.get("/open")
    def open_circuit():
        raise CircuitOpen("db-manager", 7)

    @app.get("/refused")
    def refused():
        raise requests.ConnectionError("refused")

    client = app.test_client()
    assert client.get("/timeout").status_code == 504
    response = client.get("/open")
    assert response.status_code == 503 and int(response.headers["Retry-After"]) >= 7
    assert client.get("/refused").status_code == 502


# ------------------------------------------------------------------ concurrency helper
def test_calls_run_at_the_same_time_and_results_keep_their_order():
    started = time.perf_counter()
    results = run_concurrently(
        lambda: (time.sleep(0.2), "a")[1],
        lambda: (time.sleep(0.2), "b")[1],
        lambda: (time.sleep(0.2), "c")[1],
    )
    assert results == ["a", "b", "c"]
    assert time.perf_counter() - started < 0.5, "three 0.2 s calls must not take 0.6 s"


def test_the_first_failure_is_raised_after_every_call_finished():
    finished = []

    def slow_ok():
        time.sleep(0.1)
        finished.append("ok")

    def boom():
        raise ValueError("first failure")

    with pytest.raises(ValueError, match="first failure"):
        run_concurrently(boom, slow_ok)
    assert finished == ["ok"], "nothing may keep running in the background"


def test_a_single_call_runs_inline():
    assert run_concurrently(lambda: threading.current_thread().name) == [
        threading.current_thread().name
    ]


def test_the_request_context_travels_to_the_worker_thread(key_dir):
    app = create_app("tester", required_keys=("service",))
    with app.test_request_context(headers={"X-Request-ID": "ctx-id-12345"}):
        app.preprocess_request()
        seen = run_concurrently(lambda: g.request_id, lambda: g.request_id)
    assert seen == ["ctx-id-12345", "ctx-id-12345"]
