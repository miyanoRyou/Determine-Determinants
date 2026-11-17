"""FastAPI backend for the Leibniz determinant viewer.

The app computes det(A) of an n x n matrix (n <= 10) as the sum over all n!
permutations of sign * product of the picked entries, and lets the frontend
(static/script.js) inspect every term through a paginated API.

Request flow:
  1. POST /api/determinant receives the matrix once. Each cell goes through
     parse_entry into an exact Fraction, build_calculation walks the terms and
     returns a Calculation, which is cross-checked against gaussian_determinant
     and kept in the CalculationStore under a calculation_id (a hash of the
     matrix). Sending the same matrix again returns the cached response.
  2. GET /api/determinant/steps?calculation_id=... returns one page of terms.
     window() produces the permutations for the page and prefix_scaled gives
     the running total before it, so a page costs about the same wherever it is.
     The frontend never sends the matrix again; it only asks for pages by id.
  3. GET /api/determinant/progress and POST /api/determinant/cancel let the
     client watch and stop a slow POST, using the request_id it sent with it.
  4. GET /api/determinant/export/check and GET /api/determinant/export stream
     all terms (or only the nonzero ones) as CSV, built from the same window().
  5. The static frontend is mounted at "/" last so it never shadows /api.
  A middleware wraps every request: it rejects unsupported methods and oversized
  POST bodies and adds the security and cache headers. Calculation, export and
  rate-limit limits are the constants below; the store, job registry, export
  slots and rate limiter are process-wide objects created after their classes.

Everything is held in process memory, so run a single worker.
"""

import asyncio
import hashlib
import math
import re
import threading
import time
from array import array
from bisect import bisect_left, bisect_right
from collections import OrderedDict
from dataclasses import dataclass, field
from fractions import Fraction
from math import factorial, lcm
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictFloat, StrictInt, field_validator

# Largest supported matrix size; n! terms at n = 10 is about 3.6 million, which bounds time and memory.
MAX_N = 10
# Largest page a client may request, so one response stays small.
MAX_PAGE_SIZE = 1000
# Page size advertised to the frontend.
DEFAULT_PAGE_SIZE = 100
# Longest cell text accepted after trimming whitespace.
MAX_ENTRY_CHARS = 14
# Largest absolute cell value, which keeps the exact integer products from growing without bound.
MAX_ABS_ENTRY = 10**6
# Largest cell denominator, which bounds the row scaling factors and the shared denominator.
MAX_DENOMINATOR = 10**6
# Calculations kept at once; each holds rank lists and checkpoints, so this bounds memory.
STORE_CAPACITY = 8
# Age after which a stored calculation is treated as expired.
STORE_TTL_SECONDS = 3600
# Calculations that may run at the same time, so a burst cannot saturate the CPU.
MAX_CONCURRENT_CALCULATIONS = 2
# Running-total checkpoints are saved every this many terms, so a running total needs fewer than this many recomputed terms.
CHECKPOINT_EVERY = 100
# Rows at or above this depth update progress and check for cancellation, which keeps that overhead negligible.
PROGRESS_DEPTH = 2
# Number of equal slices of the permutation range reported for the distribution strip.
STRIP_SLICES = 40

# Entry patterns are ASCII-only (re.ASCII with explicit [0-9]) and used with fullmatch, so Unicode digits and stray text are rejected.
INT_RE = re.compile(r"([+-]?)([0-9]+)", re.ASCII)
DEC_RE = re.compile(r"([+-]?)([0-9]*)\.([0-9]*)", re.ASCII)
FRAC_RE = re.compile(r"([+-]?)([0-9]+)[ \t]*/[ \t]*([0-9]+)", re.ASCII)
ID_PATTERN = r"^[A-Za-z0-9_-]+$"

# Largest accepted POST body, checked against Content-Length.
MAX_BODY_BYTES = 32 * 1024
# Raw strings longer than this are refused before any trimming or regex work, so huge inputs cost nothing.
MAX_RAW_ENTRY_CHARS = 64
# Uncached calculations (and, separately, exports) allowed per client address per minute.
RATE_LIMIT_PER_MINUTE = 30
# Terms formatted per worker-thread step while streaming a CSV, which keeps memory flat.
EXPORT_CHUNK = 2000
# Watchdog time after which an export slot is freed even if the client stopped reading.
MAX_EXPORT_SECONDS = 300
# How long a request waits for a calculation slot before getting 503.
COMPUTE_WAIT_SECONDS = 30
# Exports that may stream at the same time.
MAX_CONCURRENT_EXPORTS = 2
# Places kept when an export is written in decimals instead of exact fractions.
DECIMAL_PLACES = 8

# Strict types stop pydantic coercing values (for example true or 2.0 into an int); parse_entry does the real interpretation.
Entry = StrictInt | StrictFloat | str


def parse_entry(v: Entry) -> tuple[Fraction, bool]:
    """Turn one cell into an exact (Fraction, was_written_as_decimal) pair.
    Accepts ints, finite floats, and strings holding an integer, decimal or fraction; a blank string is zero.
    Raises ValueError with a user-facing message. Used by MatrixRequest validation and by calculate.
    """
    if isinstance(v, bool):
        raise ValueError("not a number")
    if isinstance(v, int):
        value, decimal = Fraction(v), False
    elif isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("not a finite number")
        # repr gives the shortest decimal text, so 0.1 becomes exactly 1/10 rather than the binary float value.
        value, decimal = Fraction(repr(v)), True
    else:
        if len(v) > MAX_RAW_ENTRY_CHARS:
            raise ValueError(f"longer than {MAX_ENTRY_CHARS} characters")
        t = v.strip(" \t\r\n")
        if t == "":
            return Fraction(0), False
        if len(t) > MAX_ENTRY_CHARS:
            raise ValueError(f"longer than {MAX_ENTRY_CHARS} characters")
        # The sign group is read here; the digit groups of each pattern are unsigned.
        sign = -1 if t[0] == "-" else 1
        if m := INT_RE.fullmatch(t):
            value, decimal = Fraction(sign * int(m[2])), False
        # A decimal needs at least one digit, so a lone dot is rejected.
        elif (m := DEC_RE.fullmatch(t)) and (m[2] or m[3]):
            digits = m[3] or ""
            value = Fraction(sign * int((m[2] or "0") + digits), 10 ** len(digits))
            decimal = True
        elif m := FRAC_RE.fullmatch(t):
            if int(m[3]) == 0:
                raise ValueError("denominator is zero")
            value, decimal = Fraction(sign * int(m[2]), int(m[3])), False
        else:
            raise ValueError("use an integer, a decimal such as 0.25, or a fraction such as 1/3")
    if abs(value) > MAX_ABS_ENTRY:
        raise ValueError(f"absolute value above {MAX_ABS_ENTRY:,}")
    if value.denominator > MAX_DENOMINATOR:
        raise ValueError(f"denominator above {MAX_DENOMINATOR:,}")
    return value, decimal


def rat(x: Fraction) -> str:
    """Format a Fraction as 'p' or 'p/q'; this is the exact string form used in every API value."""
    return str(x.numerator) if x.denominator == 1 else f"{x.numerator}/{x.denominator}"


class MatrixRequest(BaseModel):
    """Body of POST /api/determinant: a square matrix of cells plus an optional request_id used for progress and cancel."""
    matrix: list[list[Entry]] = Field(min_length=1, max_length=MAX_N)
    request_id: str | None = Field(default=None, max_length=64, pattern=ID_PATTERN)

    @field_validator("matrix")
    @classmethod
    def validate_matrix(cls, m):
        """Require a square matrix and check every cell with parse_entry.
        Errors name the row and column; the offending input is not echoed.
        """
        n = len(m)
        for i, row in enumerate(m):
            if len(row) != n:
                raise ValueError(f"matrix must be square; expected {n} columns in every row")
            for j, v in enumerate(row):
                try:
                    parse_entry(v)
                except ValueError as e:
                    raise ValueError(f"row {i + 1}, column {j + 1}: {e}") from None
        return m


class Strip(BaseModel):
    """The strip: exact subtotals of STRIP_SLICES equal slices of the permutation range."""
    slices: int
    sums: list[str]


class DeterminantResponse(BaseModel):
    """Result of a calculation: the determinant, term counts, flags for the frontend's number formatting, and the strip."""
    calculation_id: str
    n: int
    determinant: str
    total_permutations: int
    nonzero_terms: int
    nonzero_filter_available: bool
    compute_ms: float
    cross_check_passed: bool
    has_fractions: bool
    has_decimals: bool
    default_page_size: int
    max_page_size: int
    strip: Strip


class Step(BaseModel):
    """One term of the Leibniz sum: permutation (1-based), inversion count, sign, the picked entries, their product and the signed term."""
    index: int
    permutation: list[int]
    inversions: int
    sign: int
    factors: list[str]
    product: str
    term: str


class StepsResponse(BaseModel):
    """One page of terms plus the page subtotal and the running total through the end of the page."""
    calculation_id: str
    page: int
    limit: int
    nonzero_only: bool
    total_steps: int
    total_pages: int
    first_rank: int | None
    last_rank: int | None
    page_subtotal: str
    running_total: str
    steps: list[Step]
    # Filter and order that produced this page; any filter or sort lists nonzero terms only.
    sign: str = "all"
    order: str = "perm"


@dataclass
class Calculation:
    """Everything kept for one matrix so pages can be served without recomputing.
    All sums are integers over the shared denominator denom (each row of scaled is the row times the LCM of its denominators).
    Ranks are lexicographic positions among the total = n! permutations; created is used for the TTL.
    """
    n: int
    total: int
    scaled: list[list[int]]
    denom: int
    cell_str: list[list[str]]
    # Either the sorted ranks of the nonzero terms (rank_kind 'nz') or of the zero terms ('zero'), whichever is shorter.
    rank_list: array
    rank_kind: str
    nz_count: int
    slice_sums: list[int]
    # Checkpoints: entry k is the scaled sum of the terms before term k * CHECKPOINT_EVERY, over all ranks or over nonzero terms only.
    prefix_all: list[int]
    prefix_nz: list[int]
    det_scaled: int
    created: float
    # Built on first use by get_rank_view (sorted and sign-filtered views); view_lock makes concurrent requests build it once.
    view: "RankView | None" = None
    view_lock: threading.Lock = field(default_factory=threading.Lock)


def unrank_permutation(n: int, k: int) -> list[int]:
    """Return the k-th permutation of 0..n-1 in lexicographic order (0-based).
    Uses the factorial number system: each digit of k selects an index into the remaining values. Costs O(n^2).
    """
    pool = list(range(n))
    perm = []
    for i in range(n, 0, -1):
        f = factorial(i - 1)
        idx, k = divmod(k, f)
        perm.append(pool.pop(idx))
    return perm


def advance_permutation(p: list[int]) -> bool:
    """Step p in place to the next lexicographic permutation; return False if p was the last one.
    Standard next-permutation: swap the last ascent with the next larger tail value, then reverse the tail.
    """
    i = len(p) - 2
    while i >= 0 and p[i] >= p[i + 1]:
        i -= 1
    if i < 0:
        return False
    j = len(p) - 1
    while p[j] <= p[i]:
        j -= 1
    p[i], p[j] = p[j], p[i]
    p[i + 1 :] = reversed(p[i + 1 :])
    return True


def permutation_window(n: int, start: int, count: int):
    """Yield up to count permutations beginning at rank start.
    Unranks once, then advances one step at a time, so the cost does not depend on how deep the page is.
    """
    perm = unrank_permutation(n, start)
    for _ in range(count):
        yield perm[:]
        if not advance_permutation(perm):
            return


def count_inversions(p: list[int]) -> int:
    """Count pairs that are out of order; the parity of this count decides the sign of the term."""
    return sum(1 for i in range(len(p)) for j in range(i + 1, len(p)) if p[i] > p[j])


def gaussian_determinant(a: list[list[Fraction]]) -> Fraction:
    """Determinant by exact Gaussian elimination with row swaps, used only to cross-check the Leibniz sum.
    Independent of the permutation code, so agreement is meaningful.
    """
    m = [row[:] for row in a]
    n = len(m)
    det = Fraction(1)
    for col in range(n):
        pivot = next((r for r in range(col, n) if m[r][col] != 0), None)
        if pivot is None:
            return Fraction(0)
        if pivot != col:
            m[col], m[pivot] = m[pivot], m[col]
            det = -det
        det *= m[col][col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f:
                for c in range(col, n):
                    m[r][c] -= f * m[col][c]
    return det


class Cancelled(Exception):
    """Raised inside a calculation when its Job has been cancelled; calculate turns it into a 409."""
    pass


@dataclass
class Job:
    """Registry entry for a running calculation, found by request_id.
    The walk writes progress (0..1) and polls cancel; owner is the client address, so only that client can see or cancel it.
    """
    progress: float = 0.0
    cancel: threading.Event = field(default_factory=threading.Event)
    owner: str = ""


def build_calculation(a: list[list[Fraction]], job: Job | None = None) -> Calculation:
    """Run the single Leibniz pass over the matrix a and return its Calculation.
    One walk records the determinant, strip slice sums, checkpoints and the nonzero index together.
    Updates job.progress and raises Cancelled if job is cancelled. Called by calculate.
    """
    n = len(a)
    # Scale each row by the LCM of its denominators so every entry is an integer.
    # A scaled row multiplies the determinant by its factor, so det(A) = det_scaled / denom where denom is the product of the factors.
    row_scale = [lcm(*(v.denominator for v in row)) for row in a]
    scaled = [[int(v * row_scale[i]) for v in row] for i, row in enumerate(a)]
    denom = math.prod(row_scale)
    total = factorial(n)
    fact = [factorial(i) for i in range(n)]
    # Columns are tracked as bits: used is the set already picked, full is every column.
    full = (1 << n) - 1

    slices = min(STRIP_SLICES, total)
    # slice_end[b] is the exclusive end rank of strip slice b (ceiling division, so slices are as even as possible).
    slice_end = [-(-(b + 1) * total // slices) for b in range(slices)]
    slice_sums = [0] * slices
    prefix_all: list[int] = []
    prefix_nz: list[int] = []
    # Cap on either rank list. Nonzero and zero ranks together number total, so the shorter one always fits; a list that would pass the cap is dropped (None).
    half = total // 2 + 1
    nz_ranks: array | None = array("I")
    zero_ranks: array | None = array("I")

    running = 0
    count = 0
    last_rank = -1
    slice_i = 0
    next_slice = slice_end[0]
    next_cp = 0
    next_nz = 0

    def emit(rank: int, term: int) -> None:
        """Record one nonzero term. walk calls this in strictly increasing rank order, which is what lets
        the slice sums, checkpoints and both rank lists be built in this single pass.
        """
        nonlocal running, count, last_rank, slice_i, next_slice, next_cp, next_nz, nz_ranks, zero_ranks
        # Move to the strip slice that contains this rank.
        while rank >= next_slice:
            slice_i += 1
            next_slice = slice_end[slice_i]
        # Save a checkpoint for every boundary up to this rank; running does not include this term yet, so it is the sum of the terms before the boundary.
        while next_cp <= rank:
            prefix_all.append(running)
            next_cp += CHECKPOINT_EVERY
        # Same checkpoints for the nonzero-only view, counted in nonzero terms.
        if count == next_nz:
            prefix_nz.append(running)
            next_nz += CHECKPOINT_EVERY
        running += term
        slice_sums[slice_i] += term
        # Ranks skipped since the previous nonzero term had zero terms; record them in the zero list.
        if zero_ranks is not None and rank > last_rank + 1:
            if len(zero_ranks) + rank - last_rank - 1 > half:
                zero_ranks = None
            else:
                zero_ranks.extend(range(last_rank + 1, rank))
        last_rank = rank
        if nz_ranks is not None:
            if count >= half:
                nz_ranks = None
            else:
                nz_ranks.append(rank)
        count += 1

    last = scaled[n - 1]

    def walk(row: int, used: int, prod: int, parity: int, rank: int) -> None:
        """Depth-first search over rows, choosing an unused column per row and skipping zero entries so zero terms are never visited.
        prod and parity are the product and inversion parity so far; rank is the rank of the first permutation under this node.
        The last two rows are finished directly.
        """
        # Progress and cancel are checked only near the root, which keeps the cost negligible.
        if job is not None and row <= PROGRESS_DEPTH:
            job.progress = rank / total
            if job.cancel.is_set():
                raise Cancelled
        r = scaled[row]
        if row == n - 2:
            # Two columns remain. Taking the lower one for this row gives the permutation at rank, the higher one gives rank + 1,
            # and the swap adds one inversion, so the sign flips.
            rem = full ^ used
            low = (rem & -rem).bit_length() - 1
            high = rem.bit_length() - 1
            v = r[low] * last[high]
            if v:
                emit(rank, -prod * v if parity else prod * v)
            v = r[high] * last[low]
            if v:
                emit(rank + 1, prod * v if parity else -prod * v)
            return
        f = fact[n - 1 - row]
        for c in range(n):
            bit = 1 << c
            if used & bit:
                continue
            v = r[c]
            if v:
                # added is the number of unused columns below c. It is the factorial-number-system digit for this row (rank grows by added * (n-1-row)!)
                # and also the new inversions c creates with later rows, so its low bit updates the sign parity.
                added = (~used & (bit - 1)).bit_count()
                walk(row + 1, used | bit, prod * v, parity ^ (added & 1), rank + added * f)

    # walk reads scaled[n - 2], so the 1 x 1 case is handled directly.
    if n == 1:
        if scaled[0][0]:
            emit(0, scaled[0][0])
    else:
        walk(0, 0, 1, 0, 0)

    # Pad so there is a checkpoint for every page start; trailing zero terms add nothing, so they repeat the final total.
    while len(prefix_all) <= (total - 1) // CHECKPOINT_EVERY:
        prefix_all.append(running)

    # Ranks after the last nonzero term are all zero terms.
    if zero_ranks is not None and last_rank + 1 < total:
        if len(zero_ranks) + total - last_rank - 1 > half:
            zero_ranks = None
        else:
            zero_ranks.extend(range(last_rank + 1, total))

    # Keep whichever index is shorter (the nonzero list on a tie).
    if nz_ranks is not None and (zero_ranks is None or len(nz_ranks) <= len(zero_ranks)):
        rank_kind, rank_list = "nz", nz_ranks
    else:
        rank_kind, rank_list = "zero", zero_ranks

    if job is not None:
        job.progress = 1.0

    return Calculation(
        n=n,
        total=total,
        scaled=scaled,
        denom=denom,
        cell_str=[[rat(v) for v in row] for row in a],
        rank_list=rank_list,
        rank_kind=rank_kind,
        nz_count=count,
        slice_sums=slice_sums,
        prefix_all=prefix_all,
        prefix_nz=prefix_nz,
        det_scaled=running,
        created=time.time(),
    )


def term_scaled(calc: Calculation, perm: list[int]) -> int:
    """Signed scaled-integer term for one permutation; divide by calc.denom for the true value."""
    prod = 1
    for i, c in enumerate(perm):
        prod *= calc.scaled[i][c]
    return -prod if count_inversions(perm) & 1 else prod


def nonzero_ranks(calc: Calculation, offset: int, count: int):
    """Yield count ranks of nonzero terms starting at the offset-th nonzero term.
    With a 'nz' index this is a slice. With a 'zero' index, a binary search finds the offset-th rank that is not in the zero list,
    then a forward scan skips zero ranks.
    """
    if calc.rank_kind == "nz":
        yield from calc.rank_list[offset : offset + count]
        return
    zeros = calc.rank_list
    target = offset + 1
    lo, hi = 0, calc.total - 1
    while lo < hi:
        mid = (lo + hi) // 2
        # mid + 1 - bisect_right(zeros, mid) is how many nonzero ranks lie in 0..mid.
        if mid + 1 - bisect_right(zeros, mid) >= target:
            hi = mid
        else:
            lo = mid + 1
    rank = lo
    p = bisect_left(zeros, rank)
    produced = 0
    while produced < count and rank < calc.total:
        if p < len(zeros) and zeros[p] == rank:
            p += 1
        else:
            yield rank
            produced += 1
        rank += 1


def nonzero_position(calc: Calculation, rank: int) -> int:
    """How many nonzero terms come before rank, i.e. its position in the nonzero-only list.
    Counts directly from a 'nz' index, or as rank minus the zero ranks below it from a 'zero' index.
    """
    if calc.rank_kind == "nz":
        return bisect_left(calc.rank_list, rank)
    return rank - bisect_left(calc.rank_list, rank)


def window(calc: Calculation, nonzero: bool, offset: int, count: int):
    """Yield (rank, permutation) for count terms starting at position offset of the full list or of the nonzero-only list.
    The one source of terms for the steps endpoint, prefix_scaled and the export.
    """
    if nonzero:
        for rank in nonzero_ranks(calc, offset, count):
            yield rank, unrank_permutation(calc.n, rank)
    else:
        for i, perm in enumerate(permutation_window(calc.n, offset, count)):
            yield offset + i, perm


def prefix_scaled(calc: Calculation, nonzero: bool, start: int) -> int:
    """Scaled sum of the terms before position start in the chosen view.
    Starts from the nearest checkpoint and recomputes the fewer than CHECKPOINT_EVERY terms in between.
    """
    k = start // CHECKPOINT_EVERY
    table = calc.prefix_nz if nonzero else calc.prefix_all
    s = table[k]
    offset = k * CHECKPOINT_EVERY
    for _, perm in window(calc, nonzero, offset, start - offset):
        s += term_scaled(calc, perm)
    return s


@dataclass
class RankView:
    """Ranks of the nonzero terms arranged for the sorted and sign-filtered views of the steps endpoint.
    by_term holds every nonzero term's rank ordered by term value, highest first, ties by rank; its first `positive`
    entries are the positive terms and the rest the negative ones, so each sign is a contiguous slice and the lowest-to-highest
    order is the same slice read backwards. pos_ranks and neg_ranks hold the same ranks of one sign in rank order for the
    permutation-order views. Each *_cum list holds the running scaled sum at every CHECKPOINT_EVERY entries of its array
    (entry q is the sum of the first q * CHECKPOINT_EVERY terms), so a running total only recomputes a short stretch.
    """
    by_term: array
    by_term_cum: list[int]
    positive: int
    pos_ranks: array
    pos_cum: list[int]
    neg_ranks: array
    neg_cum: list[int]


def collect_terms(calc: Calculation) -> tuple[array, list[int]]:
    """Walk every nonzero term once, like the walk in build_calculation, and return (ranks, signed scaled terms) in rank order.
    Kept separate from the first pass so ordinary calculations never pay for the memory this needs.
    """
    n = calc.n
    scaled = calc.scaled
    fact = [factorial(i) for i in range(n)]
    full = (1 << n) - 1
    ranks = array("I")
    terms: list[int] = []
    if n == 1:
        if scaled[0][0]:
            ranks.append(0)
            terms.append(scaled[0][0])
        return ranks, terms
    last = scaled[n - 1]

    def walk(row: int, used: int, prod: int, parity: int, rank: int) -> None:
        r = scaled[row]
        if row == n - 2:
            rem = full ^ used
            low = (rem & -rem).bit_length() - 1
            high = rem.bit_length() - 1
            v = r[low] * last[high]
            if v:
                ranks.append(rank)
                terms.append(-prod * v if parity else prod * v)
            v = r[high] * last[low]
            if v:
                ranks.append(rank + 1)
                terms.append(prod * v if parity else -prod * v)
            return
        f = fact[n - 1 - row]
        for c in range(n):
            bit = 1 << c
            if used & bit:
                continue
            v = r[c]
            if v:
                added = (~used & (bit - 1)).bit_count()
                walk(row + 1, used | bit, prod * v, parity ^ (added & 1), rank + added * f)

    walk(0, 0, 1, 0, 0)
    return ranks, terms


def checkpoint_sums(terms) -> list[int]:
    """Running sums at every CHECKPOINT_EVERY entries of terms, with one extra entry so index len // CHECKPOINT_EVERY always exists."""
    cum: list[int] = []
    running = 0
    for k, t in enumerate(terms):
        if k % CHECKPOINT_EVERY == 0:
            cum.append(running)
        running += t
    while len(cum) < len(terms) // CHECKPOINT_EVERY + 1:
        cum.append(running)
    return cum


def build_rank_view(calc: Calculation) -> RankView:
    """Collect all nonzero terms, sort them by value and keep only ranks and checkpoints (the terms themselves are dropped).
    For a dense 10 x 10 this holds about 3.6 million big integers for a few seconds, so builds run one at a time (see get_rank_view).
    """
    ranks, terms = collect_terms(calc)
    order = sorted(range(len(terms)), key=terms.__getitem__, reverse=True)
    by_term = array("I", (ranks[i] for i in order))
    sorted_terms = [terms[i] for i in order]
    # Terms are sorted highest first, so the positive ones are a prefix; find where it ends by binary search.
    lo, hi = 0, len(sorted_terms)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_terms[mid] > 0:
            lo = mid + 1
        else:
            hi = mid
    by_term_cum = checkpoint_sums(sorted_terms)
    del sorted_terms, order
    pos_ranks = array("I")
    neg_ranks = array("I")
    pos_terms: list[int] = []
    neg_terms: list[int] = []
    for r, t in zip(ranks, terms):
        if t > 0:
            pos_ranks.append(r)
            pos_terms.append(t)
        else:
            neg_ranks.append(r)
            neg_terms.append(t)
    return RankView(by_term, by_term_cum, lo, pos_ranks, checkpoint_sums(pos_terms), neg_ranks, checkpoint_sums(neg_terms))


# Rank views are built one at a time, which bounds the temporary memory of several large builds happening together.
rank_view_build_lock = threading.Lock()


def get_rank_view(calc: Calculation) -> RankView:
    """Return the calculation's RankView, building it on first use. Concurrent first requests wait and share one build."""
    with calc.view_lock:
        if calc.view is None:
            with rank_view_build_lock:
                calc.view = build_rank_view(calc)
        return calc.view


def term_of_rank(calc: Calculation, rank: int) -> int:
    """Signed scaled term of the permutation with this rank."""
    return term_scaled(calc, unrank_permutation(calc.n, rank))


class ViewSeq:
    """A read-only list of term ranks for one view: a slice [lo, hi) of a ranks array, optionally read backwards.
    by_term says the array is ordered by term value (highest first) rather than by rank. Provides the length, the ranks
    of a page, the scaled sum of the first i terms of the view, and where a given rank sits (or would sit).
    """
    def __init__(self, calc: Calculation, ranks: array, cum: list[int], lo: int, hi: int, reverse: bool, by_term: bool):
        self.calc = calc
        self.ranks = ranks
        self.cum = cum
        self.lo = lo
        self.hi = hi
        self.reverse = reverse
        self.by_term = by_term

    def __len__(self) -> int:
        return self.hi - self.lo

    def page_ranks(self, offset: int, count: int) -> list[int]:
        """Ranks at positions offset .. offset + count - 1 of the view."""
        if not self.reverse:
            return list(self.ranks[self.lo + offset : self.lo + offset + count])
        top = self.hi - offset
        return list(reversed(self.ranks[top - count : top]))

    def _sum_first(self, k: int) -> int:
        """Scaled sum of the first k terms of the underlying array, from the nearest checkpoint."""
        q = k // CHECKPOINT_EVERY
        s = self.cum[q]
        for r in self.ranks[q * CHECKPOINT_EVERY : k]:
            s += term_of_rank(self.calc, r)
        return s

    def prefix(self, i: int) -> int:
        """Scaled sum of the first i terms in this view's order."""
        if not self.reverse:
            return self._sum_first(self.lo + i) - self._sum_first(self.lo)
        return self._sum_first(self.hi) - self._sum_first(self.hi - i)

    def position(self, rank: int) -> int:
        """Position of rank in the view, or where it would be inserted if its term is not in the view (zero or the other sign)."""
        size = len(self)
        if not self.by_term:
            return bisect_left(self.ranks, rank, self.lo, self.hi) - self.lo
        target = (-term_of_rank(self.calc, rank), rank)
        lo, hi = self.lo, self.hi
        while lo < hi:
            mid = (lo + hi) // 2
            r = self.ranks[mid]
            if (-term_of_rank(self.calc, r), r) < target:
                lo = mid + 1
            else:
                hi = mid
        q = lo - self.lo
        if not self.reverse:
            return q
        present = q < size and self.ranks[self.lo + q] == rank
        return size - 1 - q if present else size - q


def make_view(calc: Calculation, sign: str, order: str) -> ViewSeq:
    """The ViewSeq for a sign filter ('all', 'pos', 'neg') and an order ('perm', 'desc', 'asc'); the plain all and perm view is not a ViewSeq."""
    rv = get_rank_view(calc)
    if order == "perm":
        if sign == "pos":
            return ViewSeq(calc, rv.pos_ranks, rv.pos_cum, 0, len(rv.pos_ranks), False, False)
        return ViewSeq(calc, rv.neg_ranks, rv.neg_cum, 0, len(rv.neg_ranks), False, False)
    total = len(rv.by_term)
    lo, hi = {"all": (0, total), "pos": (0, rv.positive), "neg": (rv.positive, total)}[sign]
    return ViewSeq(calc, rv.by_term, rv.by_term_cum, lo, hi, order == "asc", True)


class CalculationStore:
    """In-memory store of Calculations by id: least recently used entries are evicted beyond capacity,
    and an entry older than ttl seconds counts as missing. Thread-safe.
    """
    def __init__(self, capacity: int, ttl: float):
        """Create an empty store holding at most capacity entries, each valid for ttl seconds."""
        self._items: OrderedDict[str, Calculation] = OrderedDict()
        self._capacity = capacity
        self._ttl = ttl
        self._lock = threading.Lock()

    def put(self, key: str, item: Calculation) -> None:
        """Insert or refresh an entry as most recently used and evict the oldest ones over capacity."""
        with self._lock:
            self._items[key] = item
            self._items.move_to_end(key)
            while len(self._items) > self._capacity:
                self._items.popitem(last=False)

    def get(self, key: str) -> Calculation | None:
        """Return the entry, marking it recently used, or None if missing or past its TTL (age is measured from creation, not last use)."""
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            if time.time() - item.created > self._ttl:
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return item


# Process-wide state. responses caches finished POST results so the same matrix is answered at once (only while its Calculation is still stored).
# compute_slots limits concurrent calculations; jobs maps request_id to the running Job.
store = CalculationStore(STORE_CAPACITY, STORE_TTL_SECONDS)
responses: OrderedDict[str, DeterminantResponse] = OrderedDict()
responses_lock = threading.Lock()
compute_slots = threading.BoundedSemaphore(MAX_CONCURRENT_CALCULATIONS)
jobs: dict[str, Job] = {}
jobs_lock = threading.Lock()

class ExportToken:
    """Handle for one held export slot; released marks it freed and timer is its watchdog."""
    def __init__(self) -> None:
        """Start unreleased and without a watchdog; ExportSlots.acquire attaches the timer."""
        self.released = False
        self.timer: threading.Timer | None = None


class ExportSlots:
    """Counting limiter for concurrent exports. Each acquired slot gets a watchdog timer that frees it after max_seconds,
    so a client that stops reading cannot hold a slot forever.
    """
    def __init__(self, size: int, max_seconds: float):
        """Allow size concurrent exports, each force-released after max_seconds."""
        self.size = size
        self.max_seconds = max_seconds
        self.active = 0
        self.lock = threading.Lock()

    def free(self) -> int:
        """Number of slots currently available."""
        with self.lock:
            return self.size - self.active

    def acquire(self) -> ExportToken | None:
        """Take a slot and start its watchdog; return None if all slots are busy."""
        with self.lock:
            if self.active >= self.size:
                return None
            self.active += 1
        token = ExportToken()
        token.timer = threading.Timer(self.max_seconds, self.release, (token,))
        token.timer.daemon = True
        token.timer.start()
        return token

    def release(self, token: ExportToken) -> None:
        """Free the token's slot and stop its watchdog. Safe to call more than once (the stream and the watchdog may both call it)."""
        with self.lock:
            if token.released:
                return
            token.released = True
            self.active -= 1
        if token.timer is not None:
            token.timer.cancel()


export_slots = ExportSlots(MAX_CONCURRENT_EXPORTS, MAX_EXPORT_SECONDS)


class RateLimiter:
    """Sliding-window limiter keeping recent hit times per key (the client address, or an export key). Thread-safe."""
    def __init__(self, limit: int, window: float = 60.0):
        """Allow limit hits per key within any window seconds; last_prune throttles cleanup of idle keys."""
        self.limit = limit
        self.window = window
        self.hits: dict[str, list[float]] = {}
        self.lock = threading.Lock()
        self.last_prune = 0.0

    def peek(self, key: str) -> bool:
        """Report whether key is under the limit without recording a hit; used by the export pre-check."""
        now = time.monotonic()
        with self.lock:
            return sum(1 for t in self.hits.get(key, ()) if now - t < self.window) < self.limit

    def allow(self, key: str) -> bool:
        """Record a hit and return True if key is under the limit, otherwise return False without recording."""
        now = time.monotonic()
        with self.lock:
            recent = [t for t in self.hits.get(key, ()) if now - t < self.window]
            if len(recent) >= self.limit:
                self.hits[key] = recent
                return False
            recent.append(now)
            self.hits[key] = recent
            # Occasionally drop keys with no recent hits so the table cannot grow without bound.
            if len(self.hits) > 10_000 and now - self.last_prune > 5.0:
                self.last_prune = now
                for k in [k for k, v in self.hits.items() if not v or now - v[-1] >= self.window]:
                    del self.hits[k]
            return True


rate_limiter = RateLimiter(RATE_LIMIT_PER_MINUTE)


def client_of(request: Request) -> str:
    """Client address used for rate limits and job ownership. Behind a proxy the server must be run with proxy headers or this is the proxy's address."""
    return request.client.host if request.client else "unknown"


# Interactive docs and the OpenAPI schema are disabled.
app = FastAPI(title="Pac that Det(A)", docs_url=None, redoc_url=None, openapi_url=None)

@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """Return 422 with only the location, message and type of each error, leaving out the offending input so user data is never echoed back."""
    detail = [{"loc": list(e.get("loc", ())), "msg": str(e.get("msg", "invalid value")), "type": str(e.get("type", ""))} for e in exc.errors()]
    return JSONResponse({"detail": detail}, status_code=422)


# Headers added to every response. The CSP allows scripts only from the same origin and forbids framing and plugins.
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


@app.middleware("http")
async def guard(request: Request, call_next):
    """Middleware run on every request. Rejects methods other than GET, HEAD and POST (405) and POST bodies that are
    missing Content-Length or too large (413), then adds the security headers and Cache-Control (no-store for the API, no-cache for static files).
    """
    if request.method not in ("GET", "HEAD", "POST"):
        return JSONResponse({"detail": "method not allowed"}, status_code=405, headers=SECURITY_HEADERS)
    if request.method == "POST":
        length = request.headers.get("content-length")
        if length is None or not length.isdigit() or int(length) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "request body missing length or too large"}, status_code=413, headers=SECURITY_HEADERS)
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    response.headers.setdefault("Cache-Control", "no-store" if request.url.path.startswith("/api/") else "no-cache")
    return response


@app.post("/api/determinant", response_model=DeterminantResponse)
def calculate(req: MatrixRequest, request: Request) -> DeterminantResponse:
    """POST /api/determinant. Parse the matrix, answer from cache if the same matrix is still stored, otherwise rate limit,
    register the optional Job, wait for a compute slot, build the Calculation, cross-check it and store it.
    """
    parsed = [[parse_entry(v) for v in row] for row in req.matrix]
    a = [[p[0] for p in row] for row in parsed]
    n = len(a)
    has_fractions = any(v.denominator != 1 for row in a for v in row)
    has_decimals = any(p[1] for row in parsed for p in row)
    # The id is a hash of the exact entries plus the decimal flag, so equal matrices share one calculation.
    key = hashlib.sha256(
        ("|".join(",".join(rat(v) for v in row) for row in a) + f"#{int(has_decimals)}").encode()
    ).hexdigest()[:20]

    with responses_lock:
        cached = responses.get(key)
    if cached is not None and store.get(key) is not None:
        return cached

    # Cached results returned above cost nothing; only real calculations count against the limit.
    client = client_of(request)
    if not rate_limiter.allow(client):
        raise HTTPException(429, "too many calculations; wait a minute and try again")

    job = None
    if req.request_id:
        job = Job(owner=client)
        with jobs_lock:
            if req.request_id in jobs:
                raise HTTPException(409, "request id already in use")
            jobs[req.request_id] = job
    try:
        if not compute_slots.acquire(timeout=COMPUTE_WAIT_SECONDS):
            raise HTTPException(503, "the server is busy with other calculations; try again shortly")
        try:
            if job is not None and job.cancel.is_set():
                raise Cancelled
            started = time.perf_counter()
            calc = build_calculation(a, job)
            elapsed = (time.perf_counter() - started) * 1000
        finally:
            compute_slots.release()
    except Cancelled:
        raise HTTPException(409, "calculation cancelled") from None
    finally:
        if job is not None:
            with jobs_lock:
                # Remove only our own job, in case the id was reused meanwhile.
                if jobs.get(req.request_id) is job:
                    del jobs[req.request_id]

    # Divide out the shared denominator, then compare with the independent elimination result.
    det = Fraction(calc.det_scaled, calc.denom)
    if det != gaussian_determinant(a):
        raise HTTPException(500, "internal consistency check failed")

    response = DeterminantResponse(
        calculation_id=key,
        n=n,
        determinant=rat(det),
        total_permutations=calc.total,
        nonzero_terms=calc.nz_count,
        nonzero_filter_available=True,
        compute_ms=round(elapsed, 1),
        cross_check_passed=True,
        has_fractions=has_fractions,
        has_decimals=has_decimals,
        default_page_size=DEFAULT_PAGE_SIZE,
        max_page_size=MAX_PAGE_SIZE,
        strip=Strip(slices=len(calc.slice_sums), sums=[rat(Fraction(s, calc.denom)) for s in calc.slice_sums]),
    )
    store.put(key, calc)
    with responses_lock:
        responses[key] = response
        responses.move_to_end(key)
        while len(responses) > STORE_CAPACITY * 4:
            responses.popitem(last=False)
    return response


REQUEST_ID_QUERY = Query(min_length=1, max_length=64, pattern=ID_PATTERN)


@app.get("/api/determinant/progress")
def progress(request: Request, request_id: str = REQUEST_ID_QUERY) -> dict:
    """GET /api/determinant/progress. Fraction done for a running job owned by this client, or state 'unknown' if there is none."""
    with jobs_lock:
        job = jobs.get(request_id)
    if job is None or job.owner != client_of(request):
        return {"state": "unknown", "progress": 0.0}
    return {"state": "running", "progress": round(job.progress, 4)}


@app.post("/api/determinant/cancel")
def cancel(request: Request, request_id: str = REQUEST_ID_QUERY) -> dict:
    """POST /api/determinant/cancel. Set the job's cancel flag; the walk notices at its next check and the original POST then returns 409."""
    with jobs_lock:
        job = jobs.get(request_id)
    if job is None or job.owner != client_of(request):
        return {"cancelled": False}
    job.cancel.set()
    return {"cancelled": True}


@app.get("/api/determinant/steps", response_model=StepsResponse)
def steps(
    calculation_id: str = Query(min_length=1, max_length=64, pattern=ID_PATTERN),
    page: int = Query(1, ge=1),
    limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    nonzero_only: bool = False,
    at_rank: int | None = Query(None, ge=0),
    sign: str = Query("all", pattern="^(all|pos|neg)$"),
    order: str = Query("perm", pattern="^(perm|desc|asc)$"),
) -> StepsResponse:
    """GET /api/determinant/steps. Return one page of terms from a stored calculation, optionally only nonzero ones.
    sign ('all', 'pos', 'neg') keeps only positive or negative terms (the term after its permutation sign is applied) and
    order ('perm', 'desc', 'asc') sorts by term value, highest or lowest first. Either one lists nonzero terms only, whatever nonzero_only says.
    The first sorted or filtered request for a calculation builds an index, which takes a few seconds for a dense 10 x 10.
    at_rank replaces page with the page that contains that permutation rank (or would contain it, for a term the view leaves out).
    404 if the id has expired or the page is out of range.
    """
    calc = store.get(calculation_id)
    if calc is None:
        raise HTTPException(404, "calculation not found or expired; submit the matrix again")
    seq = make_view(calc, sign, order) if (sign != "all" or order != "perm") else None
    if seq is not None:
        nonzero_only = True
        view_total = len(seq)
    else:
        view_total = calc.nz_count if nonzero_only else calc.total
    total_pages = max(1, -(-view_total // limit))
    if at_rank is not None:
        # The target is a position in the chosen view; a term the view leaves out lands where its neighbours are.
        if seq is not None:
            position = seq.position(at_rank)
        else:
            position = nonzero_position(calc, at_rank) if nonzero_only else min(at_rank, calc.total - 1)
        page = min(position // limit + 1, total_pages)
    if page > total_pages:
        raise HTTPException(404, f"page out of range; last page is {total_pages}")

    start = (page - 1) * limit
    count = max(0, min(limit, view_total - start))
    out: list[Step] = []
    subtotal = 0
    first_rank = last_rank = None

    if seq is not None:
        rows = ((rank, unrank_permutation(calc.n, rank)) for rank in seq.page_ranks(start, count))
    else:
        rows = window(calc, nonzero_only, start, count)
    for rank, perm in rows:
        inv = count_inversions(perm)
        sign_value = -1 if inv & 1 else 1
        prod = 1
        for i, c in enumerate(perm):
            prod *= calc.scaled[i][c]
        product = Fraction(prod, calc.denom)
        subtotal += sign_value * prod
        if first_rank is None:
            first_rank = rank
        last_rank = rank
        out.append(
            Step(
                index=rank + 1,
                permutation=[c + 1 for c in perm],
                inversions=inv,
                sign=sign_value,
                factors=[calc.cell_str[i][c] for i, c in enumerate(perm)],
                product=rat(product),
                term=rat(sign_value * product),
            )
        )

    # Running total through this page: the total before the page plus this page's subtotal, in the order of the chosen view.
    if not view_total:
        running = 0
    elif seq is not None:
        running = seq.prefix(start) + subtotal
    else:
        running = prefix_scaled(calc, nonzero_only, start) + subtotal

    return StepsResponse(
        calculation_id=calculation_id,
        page=page,
        limit=limit,
        nonzero_only=nonzero_only,
        total_steps=view_total,
        total_pages=total_pages,
        first_rank=first_rank,
        last_rank=last_rank,
        page_subtotal=rat(Fraction(subtotal, calc.denom)),
        running_total=rat(Fraction(running, calc.denom)),
        steps=out,
        sign=sign,
        order=order,
    )


def decimal_str(x: Fraction) -> str:
    """Format a Fraction rounded to DECIMAL_PLACES places with trailing zeros removed; integers stay exact and a value that rounds to zero has no minus sign."""
    if x.denominator == 1:
        return str(x.numerator)
    scale = 10**DECIMAL_PLACES
    q = round(abs(x) * scale)
    text = f"{q // scale}.{q % scale:0{DECIMAL_PLACES}d}".rstrip("0").rstrip(".")
    return "-" + text if x < 0 and q else text


def export_lines(calc: Calculation, nonzero: bool, decimal: bool, token: ExportToken):
    """Synchronous generator yielding CSV text in chunks of EXPORT_CHUNK terms taken from window().
    Stops early if the token has been released (client gone or watchdog fired).
    """
    fmt = decimal_str if decimal else rat
    cells = [[decimal_str(Fraction(v)) if decimal else v for v in row] for row in calc.cell_str]
    total = calc.nz_count if nonzero else calc.total
    for offset in range(0, total, EXPORT_CHUNK):
        if token.released:
            return
        lines = []
        for rank, perm in window(calc, nonzero, offset, min(EXPORT_CHUNK, total - offset)):
            sign = -1 if count_inversions(perm) & 1 else 1
            prod = 1
            for i, c in enumerate(perm):
                prod *= calc.scaled[i][c]
            product = Fraction(prod, calc.denom)
            lines.append(
                f"{rank + 1},{' '.join(str(c + 1) for c in perm)},{sign},"
                f"{' x '.join(cells[i][c] for i, c in enumerate(perm))},"
                f"{fmt(product)},{fmt(sign * product)}\r\n"
            )
        yield "".join(lines)


async def export_stream(calc: Calculation, nonzero: bool, decimal: bool, token: ExportToken):
    """Async generator that streams the CSV header and then each chunk from export_lines, running every chunk in a worker thread
    so the event loop stays free. Being async, it is cancelled at an await when the client disconnects, and the finally block frees the slot.
    """
    try:
        yield "index,permutation,sign,factors,product,term\r\n"
        lines = export_lines(calc, nonzero, decimal, token)
        sentinel = object()
        while True:
            chunk = await asyncio.to_thread(next, lines, sentinel)
            if chunk is sentinel:
                break
            yield chunk
    finally:
        export_slots.release(token)


def export_target(request: Request, calculation_id: str) -> tuple[Calculation, str]:
    """Look up the calculation (404 if gone) and return it with this client's export rate-limit key, which is separate from the calculation limit."""
    calc = store.get(calculation_id)
    if calc is None:
        raise HTTPException(404, "calculation not found or expired; submit the matrix again")
    return calc, "export:" + client_of(request)


@app.get("/api/determinant/export/check")
def export_check(
    request: Request,
    calculation_id: str = Query(min_length=1, max_length=64, pattern=ID_PATTERN),
) -> dict:
    """GET /api/determinant/export/check. Tell the page whether an export would be accepted right now, without starting one or using up the rate limit, so a refusal can be shown in the page."""
    _, key = export_target(request, calculation_id)
    if not rate_limiter.peek(key):
        raise HTTPException(429, "too many exports; wait a minute and try again")
    if export_slots.free() < 1:
        raise HTTPException(429, "other exports are still running; try again shortly")
    return {"ok": True}


@app.get("/api/determinant/export")
async def export(
    request: Request,
    calculation_id: str = Query(min_length=1, max_length=64, pattern=ID_PATTERN),
    nonzero_only: bool = False,
    values: str = Query("exact", pattern="^(exact|decimal)$"),
):
    """GET /api/determinant/export. Rate limit, take an export slot (429 if none) and stream the CSV as an attachment.
    The slot is released by export_stream when the stream ends or is dropped, or by the watchdog.
    """
    calc, key = export_target(request, calculation_id)
    if not rate_limiter.allow(key):
        raise HTTPException(429, "too many exports; wait a minute and try again")
    token = export_slots.acquire()
    if token is None:
        raise HTTPException(429, "other exports are still running; try again shortly")
    name = f"determinant-{calc.n}x{calc.n}-{'nonzero' if nonzero_only else 'all'}-{values}.csv"
    return StreamingResponse(
        export_stream(calc, nonzero_only, values == "decimal", token),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# Mounted last so the API routes above take precedence over the static catch-all.
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
