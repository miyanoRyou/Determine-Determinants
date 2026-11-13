"""API tests for the determinant backend (main.py), run through FastAPI's TestClient.

The calculation and steps endpoints are checked against a brute force reference built from
itertools.permutations and fractions.Fraction, so every determinant, term, sign, running total and
page boundary is compared exactly, with and without the nonzero filter. The remaining tests cover
input validation, security headers, size and rate limits, progress and cancel, and the CSV export.
The rate limiter is raised at import so the many calls here are not throttled; tests that exercise
the limiter install their own instance.
"""
import random
from fractions import Fraction
from itertools import permutations
from math import factorial

import pytest
from fastapi.testclient import TestClient

import main
from main import parse_entry, permutation_window, unrank_permutation

client = TestClient(main.app)
main.rate_limiter.limit = 10**6


def frac(s):
    """Parse a string such as "-3/4" into an exact Fraction for comparisons."""
    return Fraction(s)


def brute(a):
    """Reference implementation: list (rank, permutation, signed term, unsigned product) for all n! permutations in itertools order."""
    n = len(a)
    terms = []
    for rank, p in enumerate(permutations(range(n))):
        inv = sum(p[i] > p[j] for i in range(n) for j in range(i + 1, n))
        prod = Fraction(1)
        for i in range(n):
            prod *= a[i][p[i]]
        terms.append((rank, p, -prod if inv % 2 else prod, prod))
    return terms


def random_matrix(n, zero_rate=0.4):
    """Build an n x n matrix of entry strings mixing integers, decimals and fractions, with a given share of zeros."""
    pool = ["0", "1", "-2", "3", "1/2", "-3/4", "0.25", "-1.5", "7", "2/3", "0.125", "-5"]
    return [[("0" if random.random() < zero_rate else random.choice(pool)) for _ in range(n)] for _ in range(n)]


def post(m):
    """POST a matrix, assert a 200 response and return the JSON body."""
    r = client.post("/api/determinant", json={"matrix": m})
    assert r.status_code == 200, r.text
    return r.json()


def get(cid, **params):
    """GET one page from the steps endpoint with the given query parameters and return the raw response."""
    return client.get("/api/determinant/steps", params={"calculation_id": cid, **params})


@pytest.mark.parametrize("n", [1, 2, 3, 5, 7])
def test_window_matches_itertools(n):
    """permutation_window and unrank_permutation must reproduce itertools lexicographic order for every start rank."""
    expected = list(permutations(range(n)))
    assert [tuple(p) for p in permutation_window(n, 0, factorial(n))] == expected
    for k in range(0, factorial(n), max(1, factorial(n) // 40)):
        assert tuple(unrank_permutation(n, k)) == expected[k]


@pytest.mark.parametrize(
    "text,value,decimal",
    [("3", "3", False), ("-3", "-3", False), ("0.25", "1/4", True), (".5", "1/2", True), ("5.", "5", True),
     ("-0.125", "-1/8", True), ("1/3", "1/3", False), (" -3 / 4 ", "-3/4", False), ("6/4", "3/2", False),
     ("", "0", False), ("+2", "2", False)],
)
def test_parse_valid(text, value, decimal):
    """Integers, decimals and fractions parse to the exact Fraction, and the decimal flag is set only for decimal input."""
    assert parse_entry(text) == (frac(value), decimal)


@pytest.mark.parametrize("text", ["abc", "1/0", "1e3", "1.2.3", "--1", "1/", "/2", ".", "12345678901234567", "1000001", "1/1000001", "\u0663", "\u0661/\u0662", "1\u00a0/2", "1" + " " * 70, "3\n4", "0x10", "1_0"])
def test_parse_invalid(text):
    """Malformed, out of range, non-ASCII, overlong or whitespace-tricky entries raise ValueError."""
    with pytest.raises(ValueError):
        parse_entry(text)


def test_float_json_entries():
    """JSON floats are converted through their decimal text, so 0.1 is exactly 1/10 and not a binary float."""
    d = post([[0.1, 0.2], [0.3, 0.4]])
    assert d["determinant"] == "-1/50"
    assert d["has_decimals"] is True


@pytest.mark.parametrize("seed", range(12))
def test_against_bruteforce(seed):
    """Determinant, counts, strip sums and every page of steps (all limits, both filter settings) equal the brute force results."""
    random.seed(seed)
    n = random.randint(1, 6)
    m = random_matrix(n, [0.0, 0.05, 0.4, 0.9][seed % 4])
    a = [[parse_entry(v)[0] for v in row] for row in m]
    terms = brute(a)
    det = sum(t[2] for t in terms)

    d = post(m)
    assert frac(d["determinant"]) == det
    assert d["total_permutations"] == factorial(n)
    nz = [t for t in terms if t[3] != 0]
    assert d["nonzero_terms"] == len(nz)
    assert sum(frac(s) for s in d["strip"]["sums"]) == det

    cid = d["calculation_id"]
    for nonzero_only in (False, True):
        view = nz if nonzero_only else terms
        for limit in (1, 7, 100, 250):
            pages = max(1, -(-len(view) // limit))
            seen = 0
            for page in range(1, pages + 1):
                r = get(cid, page=page, limit=limit, nonzero_only=nonzero_only)
                assert r.status_code == 200, r.text
                body = r.json()
                assert body["total_pages"] == pages and body["total_steps"] == len(view)
                chunk = view[(page - 1) * limit : page * limit]
                assert [s["index"] for s in body["steps"]] == [t[0] + 1 for t in chunk]
                assert [frac(s["term"]) for s in body["steps"]] == [t[2] for t in chunk]
                assert [frac(s["product"]) for s in body["steps"]] == [t[3] for t in chunk]
                assert [tuple(s["permutation"]) for s in body["steps"]] == [tuple(c + 1 for c in t[1]) for t in chunk]
                assert frac(body["page_subtotal"]) == sum((t[2] for t in chunk), Fraction(0))
                assert frac(body["running_total"]) == sum((t[2] for t in view[: page * limit]), Fraction(0))
                seen += len(body["steps"])
            assert seen == len(view)


def test_factors_and_sign_fields():
    """Each step reports its chosen factors, its sign and its inversion count."""
    m = [["1/2", "0.25"], ["3", "-4"]]
    d = post(m)
    body = get(d["calculation_id"], limit=10).json()
    assert [s["factors"] for s in body["steps"]] == [["1/2", "-4"], ["1/4", "3"]]
    assert [s["sign"] for s in body["steps"]] == [1, -1]
    assert [s["inversions"] for s in body["steps"]] == [0, 1]


def test_running_total_with_unaligned_limit_and_checkpoints():
    """Running totals stay exact when the page start is not a multiple of the stored prefix-sum spacing."""
    random.seed(99)
    m = [[str(random.randint(-3, 3)) for _ in range(7)] for _ in range(7)]
    a = [[parse_entry(v)[0] for v in row] for row in m]
    terms = brute(a)
    d = post(m)
    for limit, page in [(37, 3), (250, 11), (999, 5), (101, 50)]:
        body = get(d["calculation_id"], limit=limit, page=page).json()
        assert frac(body["running_total"]) == sum((t[2] for t in terms[: page * limit]), Fraction(0))
    last = get(d["calculation_id"], limit=1000, page=-(-5040 // 1000)).json()
    assert frac(last["running_total"]) == frac(d["determinant"])


def test_at_rank_locates_page():
    """at_rank returns the page that contains the given permutation rank, with and without the nonzero filter."""
    random.seed(5)
    m = random_matrix(6)
    a = [[parse_entry(v)[0] for v in row] for row in m]
    terms = brute(a)
    nz_ranks = [t[0] for t in terms if t[3] != 0]
    d = post(m)
    cid = d["calculation_id"]
    for rank in (0, 100, 359, 719):
        body = get(cid, at_rank=rank, limit=50).json()
        assert body["page"] == rank // 50 + 1
        body = get(cid, at_rank=rank, limit=5, nonzero_only=True).json()
        import bisect
        assert body["page"] == min(bisect.bisect_left(nz_ranks, rank) // 5 + 1, body["total_pages"])


def test_all_zero_matrix():
    """A matrix of zeros gives determinant 0 and an empty, single-page nonzero view."""
    d = post([["0", "0"], ["0", "0"]])
    assert d["determinant"] == "0" and d["nonzero_terms"] == 0
    body = get(d["calculation_id"], nonzero_only=True).json()
    assert body["steps"] == [] and body["total_pages"] == 1 and body["running_total"] == "0"


def test_single_entry():
    """A 1 x 1 matrix works and its only term is the entry itself."""
    d = post([["-7/3"]])
    assert d["determinant"] == "-7/3"
    body = get(d["calculation_id"]).json()
    assert body["steps"][0]["term"] == "-7/3"


def test_rank_list_kinds_and_filter_always_available():
    """The nonzero index stores the shorter list (nonzero ranks or the complement) and the filter returns the right rows in both cases."""
    random.seed(11)
    dense = [[str(random.randint(1, 9)) for _ in range(6)] for _ in range(6)]
    dense[2][3] = "0"
    sparse = [[("0" if random.random() < 0.7 else "2") for _ in range(6)] for _ in range(6)]
    kinds = {}
    for name, m in (("dense", dense), ("sparse", sparse)):
        d = post(m)
        assert d["nonzero_filter_available"] is True
        kinds[name] = main.store.get(d["calculation_id"]).rank_kind
        a = [[parse_entry(v)[0] for v in row] for row in m]
        nz = [t for t in brute(a) if t[3] != 0]
        body = get(d["calculation_id"], nonzero_only=True, limit=1000).json()
        assert [s["index"] for s in body["steps"]] == [t[0] + 1 for t in nz[:1000]]
    assert kinds == {"dense": "zero", "sparse": "nz"}


def test_progress_and_cancel_endpoints():
    """Unknown request ids are reported as unknown, bad ids are rejected, and a finished job is removed from the job table."""
    r = client.get("/api/determinant/progress", params={"request_id": "nope"})
    assert r.json() == {"state": "unknown", "progress": 0.0}
    assert client.post("/api/determinant/cancel", params={"request_id": "nope"}).json() == {"cancelled": False}
    assert client.get("/api/determinant/progress", params={"request_id": "bad id!"}).status_code == 422
    d = client.post("/api/determinant", json={"matrix": [[1, 2], [3, 5]], "request_id": "abc123"})
    assert d.status_code == 200
    assert "abc123" not in main.jobs


def test_build_reports_progress_and_honors_cancel():
    """build_calculation ends at progress 1.0 and raises Cancelled when the cancel flag is already set."""
    a = [[Fraction(random.randint(1, 9)) for _ in range(6)] for _ in range(6)]
    job = main.Job()
    main.build_calculation(a, job)
    assert job.progress == 1.0
    cancelled = main.Job()
    cancelled.cancel.set()
    with pytest.raises(main.Cancelled):
        main.build_calculation(a, cancelled)


def test_errors():
    """Ragged, oversized, empty and unparsable matrices, unknown ids, out of range pages and oversized limits give 422 or 404."""
    r = client.post("/api/determinant", json={"matrix": [[1, 2], [3]]})
    assert r.status_code == 422
    assert client.post("/api/determinant", json={"matrix": [[1] * 11] * 11}).status_code == 422
    assert client.post("/api/determinant", json={"matrix": []}).status_code == 422
    r = client.post("/api/determinant", json={"matrix": [["1", "x"], ["3", "4"]]})
    assert r.status_code == 422 and "row 1, column 2" in r.text
    assert client.post("/api/determinant", json={"matrix": [["1/0"]]}).status_code == 422
    assert get("nope").status_code == 404
    d = post([[1, 2], [3, 4]])
    assert get(d["calculation_id"], page=3, limit=1).status_code == 404
    assert get(d["calculation_id"], limit=1001).status_code == 422


def test_security_headers_and_hidden_docs():
    """Responses carry CSP and nosniff, API replies are no-store, interactive docs are off and unsupported methods get 405."""
    r = client.get("/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    api = client.post("/api/determinant", json={"matrix": [[1]]})
    assert api.headers["cache-control"] == "no-store"
    assert client.delete("/api/determinant").status_code == 405


def test_body_size_and_id_limits():
    """Bodies over the size limit or with a bad Content-Length get 413, and ids with path characters are rejected."""
    big = {"matrix": [["1"]], "pad": "x" * 40000}
    assert client.post("/api/determinant", json=big).status_code == 413
    assert client.post("/api/determinant", content=b'{"matrix": [[1]]}', headers={"content-type": "application/json", "content-length": "abc"}).status_code == 413
    assert get("../etc/passwd").status_code == 422
    assert get("a b").status_code == 422


def test_rate_limit():
    """RateLimiter allows its quota per key, and the API answers 429 for new calculations while cached ones stay free."""
    limiter = main.RateLimiter(3, window=60)
    assert [limiter.allow("a") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("b")
    old = main.rate_limiter
    main.rate_limiter = main.RateLimiter(1)
    try:
        assert client.post("/api/determinant", json={"matrix": [[11, 2], [3, 5]]}).status_code == 200
        assert client.post("/api/determinant", json={"matrix": [[13, 2], [3, 5]]}).status_code == 429
        assert client.post("/api/determinant", json={"matrix": [[11, 2], [3, 5]]}).status_code == 200
    finally:
        main.rate_limiter = old


def parse_csv(text):
    """Parse CSV text from the export endpoint into a list of dict rows."""
    import csv, io
    return list(csv.DictReader(io.StringIO(text)))


@pytest.mark.parametrize("nonzero_only", [False, True])
@pytest.mark.parametrize("values", ["exact", "decimal"])
def test_export_matches_steps(nonzero_only, values):
    """Exported CSV rows equal the steps endpoint rows, in exact and decimal modes, with and without the nonzero filter."""
    random.seed(21)
    m = random_matrix(5, 0.4)
    d = post(m)
    r = client.get("/api/determinant/export", params={"calculation_id": d["calculation_id"], "nonzero_only": nonzero_only, "values": values})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "determinant-5x5" in r.headers["content-disposition"]
    rows = parse_csv(r.text)
    steps_body = get(d["calculation_id"], limit=1000, nonzero_only=nonzero_only).json()
    assert len(rows) == steps_body["total_steps"]
    total = Fraction(0)
    for row, st in zip(rows, steps_body["steps"]):
        assert int(row["index"]) == st["index"]
        assert row["permutation"] == " ".join(map(str, st["permutation"]))
        assert int(row["sign"]) == st["sign"]
        if values == "exact":
            assert row["term"] == st["term"] and row["product"] == st["product"]
            assert row["factors"].split(" x ") == st["factors"]
            total += Fraction(row["term"])
        else:
            assert abs(Fraction(row["term"]) - Fraction(st["term"])) <= Fraction(1, 10**8)
            assert "/" not in r.text
    if values == "exact":
        assert total == Fraction(d["determinant"])


def test_export_chunks_and_decimal_str(monkeypatch):
    """Export stays complete and ordered when split into small chunks, and decimal_str rounds to 8 places."""
    monkeypatch.setattr(main, "EXPORT_CHUNK", 7)
    d = post([[2, 1, 0, 3], [1, 1, 4, 0], [0, 5, 1, 1], [3, 0, 2, 1]])
    r = client.get("/api/determinant/export", params={"calculation_id": d["calculation_id"]})
    assert [int(x["index"]) for x in parse_csv(r.text)] == list(range(1, 25))
    assert main.decimal_str(Fraction(-1, 3)) == "-0.33333333"
    assert main.decimal_str(Fraction(1, 200000000)) == "0"
    assert main.decimal_str(Fraction(5, 2)) == "2.5"
    assert main.decimal_str(Fraction(-7)) == "-7"


def test_export_errors_and_concurrency():
    """Export rejects unknown ids and bad options, and returns 429 while all export slots are taken, freeing them afterwards."""
    assert client.get("/api/determinant/export", params={"calculation_id": "nope"}).status_code == 404
    assert client.get("/api/determinant/export/check", params={"calculation_id": "nope"}).status_code == 404
    d = post([[1, 2], [3, 4]])
    cid = d["calculation_id"]
    assert client.get("/api/determinant/export", params={"calculation_id": cid, "values": "hex"}).status_code == 422
    assert client.get("/api/determinant/export", params={"calculation_id": "a b"}).status_code == 422
    assert client.get("/api/determinant/export/check", params={"calculation_id": cid}).json() == {"ok": True}
    held = [main.export_slots.acquire() for _ in range(main.MAX_CONCURRENT_EXPORTS)]
    try:
        assert all(held)
        assert client.get("/api/determinant/export", params={"calculation_id": cid}).status_code == 429
        assert client.get("/api/determinant/export/check", params={"calculation_id": cid}).status_code == 429
    finally:
        for h in held:
            main.export_slots.release(h)
    assert main.export_slots.free() == main.MAX_CONCURRENT_EXPORTS
    assert client.get("/api/determinant/export", params={"calculation_id": cid}).status_code == 200
    assert client.get("/api/determinant/export", params={"calculation_id": cid}).status_code == 200
    assert main.export_slots.free() == main.MAX_CONCURRENT_EXPORTS


def test_export_slot_freed_when_client_abandons_stream():
    """Closing the export stream early releases the export slot at once."""
    import asyncio

    d = post([[random.randint(1, 9) for _ in range(7)] for _ in range(7)])
    calc = main.store.get(d["calculation_id"])

    async def run():
        """Take a slot, read two chunks of the stream, then close it early like a disconnecting client."""
        token = main.export_slots.acquire()
        stream = main.export_stream(calc, False, False, token)
        await stream.__anext__()
        await stream.__anext__()
        await stream.aclose()

    asyncio.run(run())
    assert main.export_slots.free() == main.MAX_CONCURRENT_EXPORTS


def test_export_slot_watchdog_frees_stuck_slot():
    """The watchdog releases a slot held past its timeout, and a late release is harmless."""
    slots = main.ExportSlots(1, 0.2)
    token = slots.acquire()
    assert token is not None and slots.acquire() is None
    import time

    time.sleep(0.5)
    assert token.released and slots.free() == 1
    slots.release(token)
    assert slots.free() == 1


@pytest.mark.parametrize("body", [b'{"matrix":[[NaN]]}', b'{"matrix":[[Infinity]]}', b'{"matrix":[[-Infinity]]}', b'{"matrix":[[1e400]]}', b'{"matrix":[[true]]}', b'{"matrix":[[null]]}'])
def test_nonfinite_and_bool_entries_are_422_with_headers(body):
    """NaN, infinities, huge floats, booleans and null are rejected with a 422 that still has the security headers."""
    r = client.post("/api/determinant", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text
    assert "content-security-policy" in r.headers
    assert isinstance(r.json()["detail"], list)


def test_duplicate_request_id_and_cancel_ownership():
    """A reused request id gets 409, and only the client that started a job can see its progress or cancel it."""
    job = main.Job(owner="testclient")
    main.jobs["dup1"] = job
    try:
        r = client.post("/api/determinant", json={"matrix": [[2, 3], [5, 7]], "request_id": "dup1"})
        assert r.status_code == 409
        assert main.jobs["dup1"] is job
        assert client.get("/api/determinant/progress", params={"request_id": "dup1"}).json()["state"] == "running"
        job.owner = "someone-else"
        assert client.get("/api/determinant/progress", params={"request_id": "dup1"}).json()["state"] == "unknown"
        assert client.post("/api/determinant/cancel", params={"request_id": "dup1"}).json() == {"cancelled": False}
        assert not job.cancel.is_set()
        job.owner = "testclient"
        assert client.post("/api/determinant/cancel", params={"request_id": "dup1"}).json() == {"cancelled": True}
    finally:
        main.jobs.pop("dup1", None)


def test_busy_server_returns_503(monkeypatch):
    """When every calculation slot is taken, a new request times out with 503."""
    monkeypatch.setattr(main, "COMPUTE_WAIT_SECONDS", 0.1)
    held = [main.compute_slots.acquire() for _ in range(main.MAX_CONCURRENT_CALCULATIONS)]
    try:
        r = client.post("/api/determinant", json={"matrix": [[17, 2], [3, 5]]})
        assert r.status_code == 503
    finally:
        for _ in held:
            main.compute_slots.release()


def test_static_cache_header_and_rate_limiter_peek():
    """Static files are served no-cache, and RateLimiter.peek checks the quota without consuming it."""
    assert client.get("/").headers["cache-control"] == "no-cache"
    limiter = main.RateLimiter(1)
    assert limiter.peek("k")
    assert limiter.allow("k")
    assert not limiter.peek("k")
