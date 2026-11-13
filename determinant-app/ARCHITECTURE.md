# ADR-001: Leibniz determinant viewer with paginated step API

Status: Proposed
Date: 2026-11-10

## Context

The app computes det(A) for an n x n matrix (n <= 10) with the Leibniz formula and lets the user inspect every one of the n! terms (3,628,800 at n = 10). Entries may be integers, decimals or fractions and must stay exact. Returning all terms in one response or one DOM is not possible, so steps are generated on demand, a page at a time.

## Decision

FastAPI serves the API and the static jQuery frontend from one origin.

1. Calculation, POST /api/determinant. Entries (strings, ints or floats) are parsed into exact Fractions (limits: 14 characters, absolute value and denominator at most 1,000,000). Each row is scaled by the LCM of its denominators so the whole sum runs on Python integers, and every term shares one denominator. A depth-first walk over rows, skipping zero entries, emits the nonzero terms in lexicographic permutation order and records, in the same pass: the determinant, 40 equal slices of the permutation range with their subtotals (the "where the sum comes from" strip), prefix sums every 100 terms for the running total, and an index for the nonzero-only filter. The index stores whichever is shorter, the sorted ranks of the nonzero terms or the sorted ranks of the zero terms (the complement), so it never holds more than about half of n! entries and the filter works for every matrix. A binary search on the complement list finds the k-th nonzero rank. The result is cross-checked against fraction-based Gaussian elimination.
2. Step server, GET /api/determinant/steps with calculation_id, page, limit (at most 1000), nonzero_only and at_rank. The start permutation is found by unranking in the factorial number system, then advanced with next-lexicographic-permutation. With the filter on, ranks come from the index described above. at_rank returns the page containing a given permutation (used by the strip). Each response carries the page subtotal and the running total through the page, computed from the nearest stored prefix sum plus fewer than 100 recomputed terms.
3. Frontend (jQuery). Holds one page of rows in the DOM. Values arrive as exact "p/q" strings and are formatted in the browser as fractions or decimals (8 places, marked with an approximation sign when rounded). Selecting a step highlights its picked entries in the matrix and opens a detail panel with every factor.

## Data flow

    grid -> validate -> POST /api/determinant
        -> parse to Fractions -> scale rows -> DFS (det, strip, prefix sums, nonzero ranks) -> cross-check
        -> store[id]; response {id, determinant, counts, strip}
    UI: result card, then GET steps?page=1
        -> unrank/advance or ranks[...] -> signed products -> {steps[], subtotal, running_total, rank range}
    Prev / Next / jump / rows per page / nonzero switch / strip bar -> GET steps (previous request aborted)

## Options considered

Slicing itertools.permutations with islice: simple, but reaching page k costs O(k * limit) skipped items on every request. Unranking costs O(n^2) regardless of page; tests assert the same order as itertools.

Stateless GET (matrix in the query string): works across workers but needs long URLs and re-validation per page. Rejected in favor of an id.

Computing the strip and running totals on demand: needs a full scan per request (seconds at 10 x 10). Rejected in favor of collecting them during the single calculation pass.

## Consequences

Easier: constant memory per page request, random access to any page, exact arithmetic, idempotent POST (same matrix returns the cached result).
Harder: the store is per process (8 calculations, one hour), so run one worker or move it to Redis. If an id has expired or been evicted, the steps endpoint returns 404 and the page silently resubmits the matrix it last calculated, then retries the request once.
Slow calculations: the client sends a request_id with the POST and polls GET /api/determinant/progress?request_id= about every 250 ms (progress is rank / total, updated at shallow nodes of the walk). POST /api/determinant/cancel?request_id= sets a flag the walk checks at the same nodes, and the original POST then returns 409. The UI shows the progress bar after 300 ms and offers Cancel.
Measured (2 vCPU container): dense 10 x 10 in about 1.8 s; any 1000-row page in about 20 ms.

## Export and jump to a permutation

GET /api/determinant/export streams a CSV (index, permutation, sign, factors, product, term) for every term or only the nonzero ones, in exact fractions or in decimals rounded to 8 places (use decimals if Excel turns fractions like 1/2 into dates). Rows are produced in chunks of 2,000 from the same window() the steps endpoint uses, so memory stays flat. A dense 9 x 9 (362,880 rows, 28 MB) took about 4 s; a dense 10 x 10 should be around 40 s and 300 MB for small integers, and several times larger for matrices with big fractions (a sampled worst case projected to about 1.6 GB). The stream is an async generator that runs each chunk in a worker thread, so a client that disconnects frees its slot at once, and a 5-minute watchdog frees the slot of a client that stops reading. The page calls /api/determinant/export/check first so that a busy or rate-limited server shows a message in the page instead of replacing it with an error body. At most 2 exports run at once and each client is rate limited. The Copy this page button puts the visible rows on the clipboard as tab-separated text.
"Go to permutation" ranks the typed permutation in the browser (factorial number system) and asks for the page containing that rank with at_rank. With the nonzero filter on, a permutation whose term is zero is not in the list, and the page says so.

## Safeguards

Input: cells accept only digits, dot, slash and minus, at most 14 characters. Typing, pasting and restored data are filtered the same way, and pasted grids are capped at 10 x 10 and 5,000 characters. The size and page boxes accept digits only. The server repeats every check and does not trust the client: entry patterns are ASCII-only and anchored (so non-ASCII digits, odd whitespace, underscores and hex are rejected), raw strings over 64 characters are refused before parsing, ids must match [A-Za-z0-9_-]+, and the matrix must be square with n at most 10.
Requests: POST bodies over 32 KB, or without a Content-Length, get 413; methods other than GET, HEAD and POST get 405; each client address is limited to 30 uncached calculations a minute (429), and cached results are free. At most two calculations run at once; a request waits up to 30 s for a slot and then gets 503. A request_id that is already in use gets 409, and progress and cancel only work for the client address that started the job. Non-finite numbers and booleans in the JSON are rejected with 422, and 422 bodies leave out the offending input. Static files are served with no-cache so a deploy never pairs an old script with a new API. Interactive docs are disabled.
Responses: Content-Security-Policy (scripts only from the same origin, no framing, no plugins), nosniff, no-referrer, a restrictive Permissions-Policy, and no-store on API responses. Run behind a proxy that sets the real client address (uvicorn --proxy-headers with a trusted forwarded-allow-ips) or the rate limit will see only the proxy.

## Saved state and teaching panel

The matrix and rows-per-page choice are saved to localStorage (key determinant.v1) shortly after each edit and restored on load. Saved data is validated entry by entry and discarded if anything is off. The Example button restores the sample matrix. Undo history is not saved.
The "How the Leibniz formula works" panel explains the four steps, walks through the selected term (its chosen entries, the out-of-order pairs, the parity and the sign), writes out every term for matrices up to 3 x 3, and shows n! against about n^3 elimination steps for n up to 10.

## Run

    pip install -r requirements.txt
    uvicorn main:app --port 8000
    open http://localhost:8000

    pip install -r requirements-dev.txt
    playwright install chromium
    pytest              # API tests (test_app.py) and browser tests (test_ui.py)

    docker build -t determinant-app .
    docker run -p 8000:8000 determinant-app

Keyboard: Ctrl or Cmd with Enter calculates; Ctrl or Cmd with Z, Y (or Shift+Z) undo and redo matrix edits; Up, Down, Home, End, PageUp, PageDown move through the step rows; arrow keys and Enter move between matrix cells.
