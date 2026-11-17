# Pac that Det(A)

Pac that Det(A) calculates the determinant of any matrix up to 10 x 10 with the Leibniz permutation formula. Enter integers, decimals or fractions, get an exact answer, and browse all 3.6 million terms page by page. Includes step-by-step explanations, CSV export and a cross-check against elimination.

A web app that computes the determinant of an n x n matrix (n from 1 to 10) with the Leibniz permutation formula and lets you look at every single term of the sum. The numbers are exact: integers, decimals and fractions go in, and the answer comes out as an exact fraction.

This file explains the whole app: the math, how to run it, how to use every control, the API, how it works inside, the limits and safeguards, and how to test and deploy it. To install and run the app step by step, start with `SETUP.md`. For the shorter design notes see `ARCHITECTURE.md`.

## Contents

1. [The idea in one minute](#the-idea-in-one-minute)
2. [Quick start](#quick-start)
3. [Using the app](#using-the-app)
4. [How the numbers are handled](#how-the-numbers-are-handled)
5. [How it works inside](#how-it-works-inside)
6. [API reference](#api-reference)
7. [Limits and performance](#limits-and-performance)
8. [Safeguards](#safeguards)
9. [Project layout](#project-layout)
10. [Testing](#testing)
11. [Deploying](#deploying)
12. [Changing the app](#changing-the-app)
13. [Privacy and offline use](#privacy-and-offline-use)
14. [Known limitations](#known-limitations)
15. [Troubleshooting](#troubleshooting)
16. [Glossary](#glossary)

## The idea in one minute

The Leibniz formula defines the determinant as a sum over every permutation of the column numbers:

    det(A) = sum over all permutations s of  sgn(s) * a(1,s1) * a(2,s2) * ... * a(n,sn)

For one permutation you pick one entry from each row so that no two picks share a column. You multiply the picks, then give the product a plus or minus sign. The sign is plus when the permutation has an even number of inversions and minus when it has an odd number. An inversion is a pair of rows where the earlier row picked a later column.

A 3 x 3 matrix has 6 permutations, a 6 x 6 has 720, and a 10 x 10 has 3,628,800. The app computes all of them on the server, keeps the result, and lets the page browse the terms 100 to 1,000 at a time. It also computes the determinant a second way (Gaussian elimination in exact fractions) and shows a "matches elimination" check, so a bug in either method would show up.

Leibniz is a teaching method, not a fast one. Elimination needs about n cubed steps; Leibniz needs n factorial terms. That gap is the point of the teaching panel in the app.

## Quick start

You need Python 3.10 or newer (the code uses 3.10 syntax; it was developed and tested on 3.13, and the Docker image uses 3.12).

    pip install -r requirements.txt
    uvicorn main:app --port 8000

Open http://localhost:8000. The page starts with a 6 x 6 example already calculated (determinant 1,986). On every load the page calculates once automatically, using the matrix saved in your browser if there is one, otherwise the example.

Run with exactly one worker. Calculations are kept in the memory of one process, so several workers would not see each other's results.

With Docker:

    docker build -t determinant-app .
    docker run -p 8000:8000 determinant-app

The Dockerfile was written but has not been built in the sandbox where this app was developed (no Docker daemon there). Build it once on your machine before relying on it.

## Using the app

The page has four areas: the matrix card on the left with the result card under it, the steps card on the right, and a collapsible "How the Leibniz formula works" panel at the bottom. The theme is Pac-Man: a black field, maze-blue borders, a yellow accent, and ghost colors for status (cyan for positive terms, red for negative, orange for warnings). There is no light theme.

### The matrix card

- **Size.** Type a number from 1 to 10 in the size box, or use the minus and plus buttons. Entries in the top-left block that still fits are kept, and new cells start at 0.
- **Randomize size.** Picks a random size from 2 to 10 (always different from the current one) and keeps your entries where they fit.
- **Randomize elements.** Keeps the current size and fills every cell with a random integer from -9 to 9. Size and elements are randomized separately on purpose.
- **Identity Matrix.** Fills the grid with 1 on the diagonal and 0 elsewhere.
- **Clear.** Sets every entry to 0.
- **Example.** Restores the built-in 6 x 6 example.
- **Undo and Redo.** Step through your edits (up to 100). Ctrl or Cmd with Z undoes, Ctrl or Cmd with Y or with Shift+Z redoes.
- **The grid.** Click a cell and type. Cells accept only digits, a dot, a slash and a minus, and only where they can still form a valid number: one leading minus, then digits, a single decimal point, or a fraction such as `3/4`. Any other keystroke (letters, spaces, a second minus, a second dot, a slash after a decimal) is ignored, and entries stop at 14 characters. The Up and Down arrow keys move between rows, and Enter moves to the next cell to the right. You can paste a block copied from a spreadsheet or text (rows on separate lines, values separated by tabs, commas, semicolons or spaces); it fills the grid starting at the cell you pasted into, up to 10 x 10.
- **Calculate determinant.** Sends the matrix to the server. Ctrl or Cmd with Enter does the same from anywhere on the page.
- **Out of date notice.** If you edit the matrix after calculating, the result card is marked "Out of date, press Calculate" so you never read an old answer as the current one.
- **Picked cells.** Select a row in the steps table and the entries that permutation picks light up in the grid, one per row.

### The result card

It sits under the matrix card and shows `det(A)` and the value, with chips for the matrix size, the number of permutations, the number of nonzero terms and whether the permutation sum matches elimination. When the value is a fraction, a line under it gives the decimal form, and the other way round.

If any entry was a fraction or decimal you also get a **Fractions / Decimals** switch. Fractions are exact. Decimals are rounded to 8 places and marked with an approximation sign. The switch changes how every number on the page is shown, not what was computed. If you typed decimals the page starts in decimal mode; otherwise fractions.

### Progress and cancel

If a calculation takes longer than about 0.3 seconds, a progress bar with a percentage appears under the Calculate button with a **Cancel** button. Cancel stops the work on the server and the page says "Calculation cancelled." A dense 10 x 10 takes a couple of seconds; most sizes are instant.

### The steps card

Each row is one permutation, in lexicographic order (1 2 3, 1 3 2, 2 1 3, and so on).

- **Columns.** Index, the permutation, its sign, the product of the picked entries, and the signed term. On a phone the product moves to a second line under each row.
- **Rows per page.** 100, 250, 500 or 1,000.
- **Show and Order.** Show keeps All terms, only Positive ones or only Negative ones (the sign of the term after the permutation sign is applied). Order sorts by Permutation (the default lexicographic order), Highest first or Lowest first. Any filter or sort lists nonzero terms only, so it locks the nonzero switch on and hides the subtotal strip, and the running total follows the order you are viewing. The first sorted or filtered view of a large matrix takes a few seconds while the server builds an index; see [Filter and sort](#filter-and-sort).
- **Nonzero terms only.** Most terms of a sparse matrix are zero. This switch hides them. A 6 x 6 example with 720 permutations has only 20 nonzero terms.
- **Pager.** First, Previous, a page box where you can type a page number, Next and Last. Under the table you see which terms are on screen and the **running total** through the end of the page. On the last page the running total equals the determinant.
- **Subtotal strip.** A bar of 40 slices of the whole permutation range. Each bar shows how much that slice adds to the sum (positive slices rise above a centre line, negative slices hang below it, with height by size). Click a bar to jump to that part of the list. It shows at a glance which stretches of terms matter.
- **Solution page.** After a calculation the result card has an Open the solution page button. solution.html shows the working in four steps: the formula, every nonzero term with its sign (cyan adds, red subtracts), the same terms with the signs simplified, and the positive and negative totals subtracted to give det(A). It reads the matrix the main page saved in localStorage (determinant.solution.v1, with the main page's own saved grid as a fallback), asks the server for the result again (answered from the cache when still stored) and fetches the nonzero terms in pages of 1,000. Up to 50,000 nonzero terms are written out in full; a matrix with more shows 24 terms at a time (all permutations in order) with First, Previous, Next, Last, jumps of 50 windows and a Go to term box, plus a subtotal per window and the number of windows that add up to det(A). An all-zero matrix says every term is 0. With nothing stored the page shows an empty state.
- **Matrix hover popup.** Hovering a row of the terms table (or moving to it with the keyboard) opens a small card with a picture of the calculated matrix, the entry that row's permutation picks in each matrix row lit (each matrix row's pick takes a ghost color in turn: red, pink, cyan, orange), the multiplication, and the signed term. It reads the matrix that was calculated, so edits you have not calculated yet do not change it. Escape or moving away closes it, and it is not shown on touch screens.
- **Go to permutation.** Type a permutation such as `2 3 1 4 5 6` (for matrices up to 9 x 9 you can leave out the spaces) and press Enter or Go. The table jumps to its page, selects the row and scrolls to it, and tells you "Permutation ... is number N of M". If the nonzero filter is on and that term is zero, the page tells you to turn the filter off. Inside a filtered or sorted view, a permutation that is not in the view (zero term or the other sign) opens the nearest page and says so.
- **Detail panel.** Select a row (click it, or move with the keyboard) and the panel spells out the term: the inversion count, the sign, every factor, and the product. A row whose term is zero says so.
- **Copy this page.** Copies the rows on screen as tab-separated text, ready to paste into a spreadsheet.
- **Download CSV.** Downloads every term (or only nonzero ones if the filter is on) in the number format you are viewing: exact fractions or decimals. A button label shows the row count. Large files take a while to start; see [Limits and performance](#limits-and-performance).

### The teaching panel

Open "How the Leibniz formula works" at the bottom. It contains:

- The four steps of the method in plain words.
- A walk through the selected term: which entries it picks, which row pairs are out of order, whether that count is even or odd, and the resulting sign.
- For matrices up to 3 x 3, every term written out with the sum.
- A table of n factorial against about n cubed for n = 1 to 10, with your current size highlighted, to show why this method gets slow.

### Keyboard summary

| Keys | Action |
|---|---|
| Ctrl or Cmd + Enter | Calculate |
| Ctrl or Cmd + Z | Undo matrix edit |
| Ctrl or Cmd + Y, or Ctrl or Cmd + Shift + Z | Redo |
| Up and Down arrows, Enter in the grid | Move between rows, and to the next cell on the right |
| Up and Down in the steps table | Previous and next row (selects it) |
| Home and End in the table | First and last row on the page |
| Page Up and Page Down in the table | Move about a screenful |
| Enter in the jump box | Go to that permutation |

### What is remembered

The matrix and your rows-per-page choice are saved in your browser (localStorage, key `determinant.v1`) shortly after each edit and restored on the next visit. Saved data is checked entry by entry and ignored if anything looks wrong. Undo history and results are not saved. If the server restarts or forgets a calculation (they are kept for an hour, and only the 8 most recent), the page quietly resubmits your matrix and carries on.

## How the numbers are handled

Entries can be typed three ways, with an optional leading minus:

| Form | Example |
|---|---|
| Integer | `3`, `-7` |
| Decimal | `0.25`, `.5`, `5.` |
| Fraction | `1/3`, `-3 / 4` |

Rules enforced by both the page and the server:

- Only the characters 0 to 9, dot, slash and minus are accepted in a cell, up to 14 characters. A typographic minus pasted from a document is converted to a normal minus.
- Each value must be between -1,000,000 and 1,000,000 and a fraction's denominator may not exceed 1,000,000. A decimal counts as a fraction over a power of ten, so a decimal with more than 6 places is rejected unless it reduces to a smaller denominator.
- Scientific notation (`1e3`), hex, underscores, non-ASCII digits, and division by zero are rejected. An empty cell counts as 0.
- Every error names the row and column, for example "row 1, column 2: use an integer, a decimal such as 0.25, or a fraction such as 1/3".

Nothing is ever rounded on the server. Decimals are converted to exact fractions (0.1 is 1/10, not a binary float), all arithmetic uses integers, and results travel as strings like `-11/4`. The browser parses those strings with BigInt, so even very large numerators display exactly. Only the Decimals display mode rounds, and it marks every rounded value.

## How it works inside

### The three pieces

1. **`main.py`** is the FastAPI backend. It parses and validates the matrix, does all the math, keeps results in memory and serves them.
2. **`static/`** is the frontend: `index.html` (markup), `style.css` (layout and the Pac-Man theme) and `script.js` (all behavior, using jQuery). `main.py` serves these files itself, so there is no build step.
3. **Tests** (`test_app.py` and `test_ui.py`) check the math against brute force and the page against a real browser.

### The life of a calculation

1. The page reads every cell as text and POSTs the matrix once, with a random `request_id` that lets it watch progress and cancel.
2. The server turns each cell into an exact fraction. It multiplies each row by the least common multiple of that row's denominators. That makes every entry an integer, so every term is an integer divided by one shared denominator and the whole walk can use plain integer arithmetic.
3. It walks the permutation tree depth first, row by row, in lexicographic order. A zero entry prunes its whole subtree, which is why sparse matrices are fast. In one pass it collects:
   - the determinant,
   - the 40 slice sums for the strip,
   - the running total at every 100th term ("checkpoints"), for all terms and for nonzero terms only,
   - an index of which terms are nonzero.
4. It computes the determinant again by Gaussian elimination in exact fractions and compares. The result of the comparison is the "matches elimination" chip.
5. It stores the calculation under an id that is a hash of the matrix and returns the summary (determinant, counts, strip). The summary contains no terms. Sending the same matrix again returns the stored result instantly.

### Serving pages of terms

To show page 3 of 100 rows the server does not walk from the start:

- It finds the first permutation of the page by **unranking**: converting the position number into a permutation with the factorial number system.
- It then steps forward with a next-permutation routine for 100 rows.
- The running total through the end of the page starts from the nearest checkpoint before it and adds at most 99 recomputed terms.

So the cost of a page is about the same whether it is the first or the 3,000th page.

### The nonzero filter

For "nonzero only", page 3 means "the 201st to 300th nonzero terms", so the server needs the ranks of the nonzero terms. For a dense matrix that list would be as long as 10 factorial. Instead the server stores whichever list is shorter, the nonzero ranks or the zero ranks (the complement), which is never more than half of all terms. With a complement it finds the k-th nonzero rank by binary search. This is why the filter works for every matrix with bounded memory.

### Progress and cancel

While a calculation runs, the server records its progress (the rank reached out of the total) at shallow levels of the walk, and checks a cancel flag at the same places. The page polls `GET /progress` about four times a second. Cancel sets the flag; the walk raises an exception and the original POST answers 409. Progress and cancel only work for the client address that started the job.

### Memory and concurrency

- At most 8 calculations are kept (least recently used leaves first), each for at most one hour.
- At most 2 calculations run at once; a third waits up to 30 seconds and then gets a 503.
- At most 2 exports stream at once. A 5-minute watchdog frees the slot of a client that stops reading, and a client that disconnects frees its slot immediately.
- A dense 10 x 10 keeps only about 2 MB in memory after the calculation.

### Filter and sort

The sorted and sign-filtered views need the actual term values, so they are built lazily: the first request with `sign` or `order` makes the server walk every nonzero term once, sort them, and keep only ranks (a compact array) and running-total checkpoints every 100 entries, not the terms. For a dense 10 x 10 that takes about 4 to 5 seconds and roughly 400 MB of temporary memory, one build at a time across all requests; afterwards every page, jump and running total is instant. Positive terms are a prefix of the sorted array, so each sign is a slice and Lowest first is the same slice read backwards. Positive and negative views in permutation order use their own rank lists. CSV and the other exports are unchanged and still list terms in permutation order.

### The frontend

`script.js` is organized around one shared `state` object. The main parts:

- Grid: building the inputs, reading them, sizing the layout from the matrix size so a 10 x 10 still fits.
- Calculate: the POST, the progress polling, the cancel button and the error handling.
- Rendering: the result card, the strip, the table (only one page of rows in the page at a time), the detail panel, the picked-cell highlighting and the teaching panel.
- Paging: loading pages, the nonzero switch, rows per page, the jump box, and the quiet retry after a 404.
- Conveniences: undo and redo, keyboard shortcuts, paste cleanup, localStorage, copy and CSV download.

The grid size (`state.n`) and the size of the last calculated matrix (`state.calcN`) are tracked separately, so editing the grid never relabels an older result.

## API reference

All endpoints are under `/api/determinant`. Responses are JSON unless noted. Numbers are exact strings: integers like `-2` or fractions like `-11/4`.

### POST /api/determinant

Calculates and stores a result.

Request body:

    {
      "matrix": [["1/2", "0.25"], ["3", "-4"]],
      "request_id": "r1abc23def"
    }

- `matrix`: a square list of lists, 1 to 10 rows. Each entry is a JSON integer, a JSON number, or a string in one of the three forms above. `NaN`, `Infinity`, booleans and null are rejected.
- `request_id` (optional): up to 64 characters from `A-Z a-z 0-9 _ -`. Needed for progress and cancel. Reusing an id that is currently running returns 409.

Response 200:

    {
      "calculation_id": "20bb28a7ec7d46b49806",
      "n": 2,
      "determinant": "-11/4",
      "total_permutations": 2,
      "nonzero_terms": 2,
      "nonzero_filter_available": true,
      "compute_ms": 0.1,
      "cross_check_passed": true,
      "has_fractions": true,
      "has_decimals": true,
      "default_page_size": 100,
      "max_page_size": 1000,
      "strip": { "slices": 2, "sums": ["-2", "-3/4"] }
    }

`strip.sums` has up to 40 slice subtotals that add up to the determinant.

### GET /api/determinant/steps

Returns one page of terms.

| Parameter | Meaning |
|---|---|
| `calculation_id` | required, from the POST |
| `page` | 1-based page number, default 1 |
| `limit` | rows per page, 1 to 1000, default 100 |
| `nonzero_only` | `true` to skip zero terms, default false |
| `sign` | `all` (default), `pos` or `neg`; keeps only positive or negative terms |
| `order` | `perm` (default), `desc` or `asc`; sorts by term value, highest or lowest first |
| `at_rank` | 0-based permutation position; returns the page containing it (inside the chosen view) and overrides `page` |

Response 200 (abbreviated):

    {
      "calculation_id": "20bb28a7ec7d46b49806",
      "page": 1, "limit": 100, "nonzero_only": false,
      "total_steps": 2, "total_pages": 1,
      "first_rank": 0, "last_rank": 1,
      "page_subtotal": "-11/4",
      "running_total": "-11/4",
      "steps": [
        { "index": 1, "permutation": [1, 2], "inversions": 0, "sign": 1,
          "factors": ["1/2", "-4"], "product": "-2", "term": "-2" },
        { "index": 2, "permutation": [2, 1], "inversions": 1, "sign": -1,
          "factors": ["1/4", "3"], "product": "3/4", "term": "-3/4" }
      ]
    }

Any `sign` other than `all` or `order` other than `perm` lists nonzero terms only, whatever `nonzero_only` says, and the response echoes `sign` and `order`. `page_subtotal` is the sum of the terms on this page. `running_total` is the sum of all terms through the end of this page in the list you are viewing, so with the nonzero filter it counts nonzero terms only (which gives the same total, since zero terms add nothing). Permutations are 1-based, `index` is the 1-based position in the full lexicographic list, and `factors` are the entries picked row by row. A page outside the range, or an id that expired, returns 404 with the message "calculation not found or expired; submit the matrix again" (for an id).

### GET /api/determinant/progress?request_id=...

Returns `{"state": "running", "progress": 0.37}` while that request is calculating, or `{"state": "unknown", "progress": 0.0}` if it is not (finished, never started, or started by another client).

### POST /api/determinant/cancel?request_id=...

Returns `{"cancelled": true}` if a running job of yours was told to stop, otherwise `{"cancelled": false}`. The cancelled POST then returns 409.

### GET /api/determinant/export/check?calculation_id=...

Returns `{"ok": true}` if an export could start now. Returns 404 for an unknown id and 429 if exports are busy or you are over the rate limit. The page calls this first so that an error appears inside the page instead of replacing it.

### GET /api/determinant/export

Streams a CSV file. Parameters: `calculation_id`, `nonzero_only` (default false), and `values` (`exact` or `decimal`, default `exact`; decimals are rounded to 8 places, useful because Excel turns fractions like `1/2` into dates).

    index,permutation,sign,factors,product,term
    1,1 2,1,1/2 x -4,-2,-2
    2,2 1,-1,1/4 x 3,3/4,-3/4

The file name is like `determinant-6x6-all-exact.csv`.

### Errors

| Status | Meaning |
|---|---|
| 404 | Unknown or expired calculation, or a page past the end |
| 405 | Method other than GET, HEAD or POST |
| 409 | Request id already in use, or the calculation was cancelled |
| 413 | POST body missing a length or larger than 32 KB |
| 422 | Invalid input. The body is `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` and never echoes the offending value |
| 429 | Too many calculations or exports, or exports busy |
| 503 | Server busy with other calculations for more than 30 seconds |

## Limits and performance

| Limit | Value | Why |
|---|---|---|
| Matrix size | 1 to 10 | 10 factorial is about 3.6 million terms; 11 factorial would need minutes and far more memory |
| Entry length | 14 characters | Keeps entries small |
| Entry value | -1,000,000 to 1,000,000 | Keeps products from growing without bound |
| Denominator | up to 1,000,000 | Bounds the shared denominator |
| Rows per page | up to 1,000 | Keeps responses small |
| Stored calculations | 8, one hour each | Bounds memory |
| Calculations at once | 2 | Protects the CPU |
| Uncached calculations | 30 per minute per client | Rate limit; a repeat of a stored matrix is free |
| Exports at once | 2, at most 5 minutes each | Bounds work and held connections |
| POST body | 32 KB | A 10 x 10 matrix needs far less |

Measured on a 2-core cloud sandbox:

- Dense 10 x 10 (3,628,800 terms): about 2 seconds to calculate; about 20 ms for a 1,000-row page. The calculation is single-threaded Python, so a similar laptop should be about the same.
- Dense 9 x 9: the CSV export of 362,880 rows (28 MB) took about 4 seconds. A dense 10 x 10 export should take roughly 40 seconds and about 300 MB for small integers. Matrices with large fractions make bigger files; one sampled worst case projected to about 1.6 GB.
- A matrix with a large share of zeros calculates much faster because zero entries prune whole subtrees.

## Safeguards

The server never trusts the page.

- **Input:** entry patterns are ASCII-only and must match the whole string. Strings over 64 characters are refused before any parsing. NaN, infinities, booleans and null are rejected. Ids must match `[A-Za-z0-9_-]+`.
- **Requests:** POST bodies over 32 KB get 413. Only GET, HEAD and POST are allowed. The interactive API docs are turned off.
- **Rate limiting:** 30 uncached calculations and 30 exports per minute per client address. Behind a proxy, run `uvicorn --proxy-headers` with a trusted `--forwarded-allow-ips`, otherwise every user looks like the proxy and shares one allowance.
- **Headers:** a Content-Security-Policy (scripts only from the app's own origin, no framing, no plugins), `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, a restrictive Permissions-Policy, `no-store` on API answers and `no-cache` on the page files so a deploy never mixes old and new code.
- **Page:** cells accept only digits, dot, slash and minus, and typing is further limited to text that can still become a valid entry (see The grid); pasting and saved data are filtered to the same characters. Pasted text can still be malformed, such as `1.2.3`, in which case the cell is marked invalid and Calculate is refused. Everything the server sends that reaches the page's HTML is a number or is escaped first.
- **Resources:** bounded store, bounded concurrency, bounded export time, and cancel support.

There are no user accounts. A calculation id is a hash of the matrix, not a secret, so anyone who sends the same matrix gets the same id. The app is meant for a single user or a small group.

## Project layout

    determinant-app/
      main.py               Backend: parsing, math, storage, API, security middleware
      requirements.txt      Runtime dependencies: fastapi, uvicorn, pydantic
      requirements-dev.txt  Adds pytest, httpx, playwright
      Dockerfile            python 3.12 slim image, one uvicorn worker
      ARCHITECTURE.md       Design notes and decisions
      README.md             This file
      test_app.py           API tests, brute-force comparison
      test_ui.py            Browser tests with Playwright
      SETUP.md              Step-by-step install and run guide
      static/
        index.html          Page markup
        solution.html       Solution page markup
        solution.js         Solution page behavior
        style.css           Layout and Pac-Man theme
        script.js           All frontend behavior
        jquery.min.js       jQuery, served locally

Every source file begins with a comment explaining what it does and how it fits with the others, and every function has a short comment or docstring.

## Testing

    pip install -r requirements-dev.txt
    playwright install chromium
    pytest

The suite has 114 tests and takes about 20 seconds.

- **`test_app.py`** checks the backend without a browser. It compares the app against brute force with `itertools.permutations` and `Fraction` on random matrices (including very sparse and fully dense ones, fractions and decimals): determinant, term order, signs, products, page subtotals, running totals on awkward page sizes, the nonzero filter, jumping to a rank, and CSV export. It also covers every sign and order combination against brute force (including running totals and `at_rank` pages), input validation, the hostile-input cases (NaN, Unicode digits, oversized bodies), security headers, the rate limiter, duplicate and foreign request ids, export slot cleanup and the busy server response.
- **`test_ui.py`** starts a real server and drives Chromium: the starting result, row selection, the nonzero switch, fraction and decimal input and the display switch, error display, undo and redo, keyboard shortcuts and row navigation, progress and cancel, recovery from an expired calculation, character filtering and paste cleanup, saved state, corrupt saved state, the teaching panel, jump to permutation, the Show and Order controls, character blocking while typing, the hover popup, the solution page, copy, CSV download, and a phone-width layout without sideways scrolling. These tests skip themselves if Playwright or its browser is not installed.

Not yet covered: Safari and Firefox (tests run in Chromium only), a screen reader pass, and an automated accessibility audit.

## Deploying

- Run one worker.
- Put it behind a reverse proxy that terminates HTTPS and passes the real client address; start uvicorn with `--proxy-headers` and `--forwarded-allow-ips` set to the proxy.
- Memory use is small (a few MB per stored calculation), so a small instance is enough. CPU matters for large matrices: one dense 10 x 10 occupies one core for about two seconds.
- To run more than one worker or more than one server, the in-memory store and job registry must move to a shared service such as Redis, because ids from one worker would not exist on another. The page would recover by resubmitting, but it would recompute each time.

## Changing the app

The limits live in constants at the top of `main.py` (backend) and `script.js` (page). Change them in both places or the two will disagree.

| To change | Where |
|---|---|
| Largest matrix | `MAX_N` in both files. Also check memory and time: each step up multiplies the term count by n. 11 would need roughly 20 to 30 seconds and a few hundred MB per dense calculation (estimate, not measured). |
| Entry length | `MAX_ENTRY_CHARS` in both files |
| Entry value and denominator limits | `MAX_ABS_ENTRY` and `MAX_DENOMINATOR` in `main.py` |
| Rows per page options | `MAX_PAGE_SIZE` in `main.py` and the buttons in `index.html` plus `LIMITS` in `script.js` |
| How many calculations are kept and for how long | `STORE_CAPACITY` and `STORE_TTL_SECONDS` |
| Rate limit, concurrency, export time | `RATE_LIMIT_PER_MINUTE`, `MAX_CONCURRENT_CALCULATIONS`, `MAX_CONCURRENT_EXPORTS`, `MAX_EXPORT_SECONDS`, `COMPUTE_WAIT_SECONDS` |
| Decimal places in Decimals mode | `DECIMAL_PLACES` in `main.py` and the matching rounding in `script.js` |
| Accent color and theme | The CSS variables at the top of `style.css` |
| The example matrix | `SAMPLE` in `script.js` (the test that expects determinant 1,986 would need updating) |

None of these are environment variables; they are edited in the source.

When you add a feature, add a test to `test_app.py` for the backend part and to `test_ui.py` for the page part, and run `pytest`. The brute-force comparison in `test_app.py` is the safety net for any change to the math.

## Privacy and offline use

- Nothing is sent to a third party. jQuery is served from the app itself and the page uses system fonts, so it works without internet access once the server is running.
- The server keeps recent matrices and results in memory only. Nothing is written to disk, and a restart clears everything.
- Your browser stores the current matrix and your rows-per-page choice in localStorage on your own device. Clearing site data removes them.
- The server logs requests like any web server. It does not log matrix contents unless you add that yourself.

## Known limitations

- Dark theme only. There is no light theme.
- Matrix size is capped at 10 by design (see the limits table).
- Storage is per process and small (8 results), so it suits one user or a small group, not a public service with many users.
- A calculation cannot be paused or resumed; Cancel stops it and you start again.
- Tests run in Chromium only. Safari (including iPhone) and Firefox have not been tested, and neither has a screen reader. The page has labels, keyboard support and a focus order, but no accessibility audit has been run.
- On a phone, a 10 x 10 grid has small cells (about 25 pixels wide), which are hard to tap precisely.
- After activating a subtotal-strip bar with the keyboard, focus moves to the page body because the strip is rebuilt; there are 40 tab stops in the strip before the table.
- A decimal with more than 6 places is rejected with a message about the denominator, which is not very clear.
- The CSV for a dense 10 x 10 is large (hundreds of MB, more with big fractions) and takes about a minute to produce.
- The Dockerfile has not been built or run.
- Only one change history exists: this document and `ARCHITECTURE.md`. There is no changelog or version number, and no license file has been chosen.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "calculation not found or expired" | Only 8 calculations are kept for an hour. The page normally resubmits for you; if you call the API directly, post the matrix again. |
| 429 on calculate | You made more than 30 uncached calculations in a minute. Wait a minute. |
| 429 on download | Two exports are already running, or you exported more than 30 times a minute. Try again shortly. |
| 503 on calculate | Other large calculations are running. Try again in a moment. |
| Excel shows dates in the CSV | Excel reads `1/2` as a date. Switch the page to Decimals before downloading. |
| Everyone shares a rate limit | The server sees only your proxy's address. Configure `--proxy-headers`. |
| Calculate takes several seconds | A dense 10 x 10 has 3.6 million terms. Use the progress bar and Cancel if needed. |
| Browser tests are skipped | Run `playwright install chromium`. |

## Glossary

- **Permutation:** an ordering of the column numbers 1 to n. It picks one entry from each row with no two in the same column.
- **Inversion:** a pair of rows where the earlier row picked a later column than the later row did. The parity of the inversion count gives the sign.
- **Term:** sign times the product of the picked entries. The determinant is the sum of all terms.
- **Rank:** the 0-based position of a permutation in lexicographic order.
- **Unranking:** turning a rank back into its permutation without listing the ones before it.
- **Running total:** the sum of all terms from the first through the end of the current page.
- **Checkpoint:** a saved running total at every 100th term so that running totals are cheap.
- **Strip:** the bar of 40 slice subtotals above the table.
- **Cross-check:** the second determinant computation by Gaussian elimination that must agree with the permutation sum.
