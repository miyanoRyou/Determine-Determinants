# Pac that Det(A): panel questions and answers

Likely questions from a panel, with answers based on how the app is built. Section 5 lists the weak points a panelist may probe. Section 6 maps each step of the algorithm to its function in `main.py`.

## 1. The method

**Why use the Leibniz formula when Gaussian elimination is far faster?**
Leibniz needs n! terms, while elimination needs about n^3 steps. For 10 x 10 that is 3,628,800 terms against roughly 1,000 steps. The app is a teaching tool: Leibniz shows where every part of the determinant comes from. Elimination runs alongside it as a cross-check, so the app uses both for different purposes.

**How do you know the answer is correct?**
Three ways. The permutation sum is compared with an independent elimination result, and the page shows "matches elimination". The tests compare the backend against a brute-force computation with `itertools.permutations` and exact fractions on random matrices, including sparse, dense, fraction and decimal ones. Sorted and filtered views are also checked against brute force.

**How does it decide which computations to do?**
It builds every possible way of picking one number from each row so that no two picks share a column. Each such way is a permutation of the column numbers, and each one becomes one term of the determinant.

For a 3 x 3 matrix, row 1 can pick column 1, 2 or 3, row 2 can pick any column row 1 did not take (2 options), and row 3 gets the one column left (1 option). That gives 3 x 2 x 1 = 6 choices, or n! for an n x n matrix. The program tries them in a fixed order, taking the lowest available column first and moving up, so the 6 choices come out as 1 2 3, 1 3 2, 2 1 3, 2 3 1, 3 1 2, 3 2 1. Here "2 3 1" means row 1 picks column 2, row 2 picks column 3 and row 3 picks column 1. This is lexicographic order, the order the table shows. Used columns are tracked with a bitmask, a single number whose bits record which columns are taken, so checking whether a column is free is one step.

For every complete choice it multiplies the picked entries, counts the inversions to get the sign (even is plus, odd is minus), and adds the signed product to the running total. The determinant is the sum of all those signed products.

The one shortcut is skipping zeros. If an entry picked for some row is 0, the product is 0 whatever the later rows pick, so the program stops that branch immediately and never visits the permutations under it. A sparse matrix therefore needs far fewer than n! multiplications, while a matrix with no zeros needs all of them. The last two rows are completed directly, since only two columns remain. The code is `build_calculation` and its inner `walk` function in `main.py`.

**How is the sign of each term determined?**
By the parity of the inversion count, meaning the number of row pairs where the earlier row picked a later column. Even gives plus, odd gives minus. The search counts the unused columns to the left of each pick as it goes, and the sum of those counts is the inversion count.

**Why is 10 x 10 the limit?**
10! is about 3.6 million terms. A dense 10 x 10 takes roughly 2 seconds to build. The first sorted view needs about 4 to 5 seconds and around 400 MB of temporary memory. At 11 x 11 the term count grows 11 times, which would push memory and time up quickly. The limit keeps the app responsive on ordinary hardware.

## 2. Numbers and accuracy

**How do you avoid floating-point errors?**
Nothing uses floats. Integers, decimals and fractions are parsed into exact fractions, each row is scaled by the least common multiple of its denominators so every entry is an integer, and the calculation uses arbitrary-precision integers. The result is an exact fraction. Decimal display is rounded only for viewing and is marked as approximate.

**What input is accepted?**
Integers, decimals (up to 6 places) and fractions like `1/3`, with an optional minus sign. Typing, pasting and drag and drop are limited to text that can still become a valid entry, so letters, a second minus or dot, a zero denominator and values past the limits never appear. An unfinished entry such as `-` is reverted when the cell loses focus. The server validates everything again, because it never trusts the page.

## 3. Performance and design

**How can it show 3.6 million terms without storing them all?**
It stores ranks, not terms. A rank is a permutation's position in lexicographic order. To show a page, the server converts the rank back into a permutation using factorials (the factorial number system), then steps to the next permutations for the rest of the page. Running totals are saved every 100 terms so a page's total needs only a short recompute.

**What does the nonzero filter cost in memory?**
It keeps whichever list is shorter, the nonzero ranks or the zero ranks (the complement), so it never stores more than half of all terms. Finding the k-th nonzero term then uses a binary search.

**How does sorting work, and what does it cost?**
On the first request for a sorted or sign-filtered view, the server walks all nonzero terms, sorts them by value, and keeps only the ranks plus checkpoints, not the terms. After that, paging, jumping and running totals are instant. The cost is the one-time build and its temporary memory, and builds run one at a time. Zero terms are left out of sorted and sign-filtered views, since they have no sign and no place in a sort.

**Why does the server keep results in memory instead of a database?**
It keeps the app simple and fast for one user or a small group: the 8 most recent results are kept for an hour. If a result is gone, the page quietly resubmits the matrix. The trade-off is that the app must run with one worker.

## 4. Security and reliability

**How is the app protected from abuse?**
Request size limits (bodies over 32 KB are refused), strict input validation (ASCII-only patterns, strings over 64 characters refused, NaN and infinities rejected), a rate limit of 30 uncached calculations and 30 exports per minute per client address, a Content-Security-Policy that allows scripts only from the app's own origin, `no-store` on API answers, bounded storage, bounded export time, and cancel support for long calculations.

**What happens if the server restarts and a result is gone?**
The page resubmits the matrix and continues, because results are kept only in memory.

## 5. Limitations and testing

**What are the weaknesses?**

- It must run with one worker, since results live in one process's memory.
- There are no user accounts, so it suits one user or a small group, not a public service.
- Tests cover Chromium only. Safari, Firefox and screen readers were not tested.
- The Dockerfile has never been built or run.
- A dense 10 x 10 CSV is hundreds of MB and takes about a minute to produce.
- The first sorted view of a dense 10 x 10 takes several seconds and uses about 400 MB temporarily.

**How did you test it?**
115 automated tests in about 20 seconds. Backend tests compare against brute force and cover validation, security headers, rate limiting and edge cases such as empty views. Browser tests drive a real Chromium through the interface: input blocking, paging, filtering and sorting, the hover popup, the solution page, and a phone-width layout.

**What would you improve next?**
Support for larger matrices using a faster method alongside Leibniz, shared storage to allow several workers, accounts or per-user limits, cross-browser testing, and an accessibility audit.

## 6. Where each step lives in main.py

Line numbers are from the current file and will shift if the code changes.

| Step | Function (line) |
|---|---|
| Choosing permutations, skipping zeros | `build_calculation` (336), nested `walk` (406), nested `emit` (371); the last two rows are finished directly in `walk` at the `row == n - 2` branch |
| Sign and product | `walk` carries `prod` and `parity`; `count_inversions` (293); `term_scaled` (486) |
| Exact arithmetic | `parse_entry` (102) with `INT_RE`, `DEC_RE`, `FRAC_RE` (76 to 78); row scaling at the start of `build_calculation`; `rat` (146) |
| Rank to permutation | `unrank_permutation` (252), `advance_permutation` (265), `permutation_window` (282) |
| Nonzero filter | `nonzero_ranks` (494), `nonzero_position` (524), `window` (533), `prefix_scaled` (545) |
| Sorting and sign filter | `collect_terms` (576), `build_rank_view` (635), `checkpoint_sums` (622), `get_rank_view` (671), `ViewSeq` (685), `make_view` (744), `RankView` (559) |
| Cross-check | `gaussian_determinant` (298), compared with the permutation sum in `calculate` (928) |
| Endpoints | `calculate` (928), `steps` (1033) |
| Safeguards | `CalculationStore` (756), `RateLimiter` (847), `guard` middleware (910), `Job` (327) and `Cancelled` (321) for progress and cancel |

## 7. More questions

### Complexity and correctness

**What is the time and memory complexity?**
Time is O(n! x n) in the worst case, a matrix with no zeros, because every permutation is visited and each term takes about n multiplications. Zero entries prune branches, so sparse matrices do much less work. Memory for the nonzero filter is at most n!/2 ranks stored as 4-byte integers, about 7 MB for a dense 10 x 10. The sorted views are the expensive part, with about 400 MB temporarily during the first build.

**Is the elimination cross-check really independent? What if both methods share a bug?**
They share only the parsed input. The permutation sum works on row-scaled integers, while elimination works directly on exact fractions with row swaps and pivoting, so a scaling or sign bug in one would not appear in the other. Both could still be wrong if parsing were wrong, which is why the tests also compare against brute force built from plain `itertools.permutations`, and why parsing has its own tests.

**What happens with edge cases such as a 1 x 1 matrix, an all-zero matrix, or a singular matrix?**
A 1 x 1 matrix has one term, the entry itself. An all-zero matrix has no nonzero terms, so the determinant is 0, the nonzero list is empty, and the solution page says every term is 0. A singular matrix just gives a determinant of 0, which the elimination cross-check also reports. All three are covered by tests.

**Why does the app scale rows by a least common multiple instead of using fractions throughout?**
Integer arithmetic is faster and simpler than fraction arithmetic across millions of terms, since fractions would need a gcd on every multiplication. Scaling a row multiplies the determinant by the scale factor, so the app divides by the product of the factors once at the end and the result stays exact.

**Why are decimals limited to 6 places?**
A decimal like 0.123456 becomes a fraction with denominator 10^6. Long decimals produce huge denominators, which slow every term. The limit keeps the integer sizes manageable. The error message for a longer decimal is not very clear, and that is a known limitation.

### Design choices

**Why FastAPI and Python for the backend?**
Python has built-in arbitrary-precision integers and exact `Fraction`, which this app depends on. FastAPI gives request validation through Pydantic and a simple, fast API with little code. The trade-off is that pure Python is slower than a compiled language for the permutation walk, which is why the app prunes zero branches and finishes the last two rows directly.

**Why do the calculation on the server instead of in the browser?**
Browsers can do exact arithmetic with BigInt, but 3.6 million terms in the page's single thread would freeze the interface. On the server the work runs once, results are paged, and the browser only renders one page of rows at a time. The server also provides progress, cancel and a rate limit.

**Why jQuery and not a modern framework?**
The page has a small number of fixed regions, so a framework would add a build step without much benefit. jQuery is served locally, so the page needs no internet connection after it is installed, and the backend serves the frontend files itself with no build step.

**Why page the results instead of showing all terms at once?**
A 10 x 10 has 3.6 million rows, which no browser can hold in the page. The app loads 100 to 1,000 rows at a time, so the page stays fast regardless of matrix size.

**Why is the solution page a separate page?**
It writes out the full working in four steps, which for a large matrix needs the whole width. It reads the calculated matrix from the browser's localStorage and asks the server again, so it works on its own. Up to 50,000 nonzero terms are written out in full. Beyond that it shows 24 terms at a time with navigation and a subtotal for each window.

**What does the hover popup show?**
It shows the calculated matrix with the entries that permutation picks lit, one per row in a ghost color, along with the multiplication and the signed term. It reads the matrix that was calculated, so unsaved edits do not change it, and it is hidden on touch screens.

### Reliability and concurrency

**How do progress and cancel work?**
A calculation that runs longer than about 0.3 seconds shows a progress bar. The page polls a progress endpoint using a request id. The search checks progress and a cancel flag only near the top levels of the search tree, which keeps the cost negligible. Cancel sets the flag and the search stops with a `Cancelled` exception.

**What if two users calculate the same matrix?**
The calculation id is a hash of the matrix, so the second request is answered from the cache and gets the same id. This is also why the id is not a secret.

**What if many users calculate large matrices at once?**
Concurrency is bounded and the server answers 503 when it is busy. Sorted-index builds run one at a time so their temporary memory does not stack up. There is also a rate limit of 30 uncached calculations a minute per client address. The app is designed for one user or a small group, so heavy simultaneous use is outside its design.

**Why must it run with one worker?**
Results, progress jobs, the rate limiter and export slots live in one process's memory. With several workers a follow-up request could reach a worker that has never seen the id and return 404.

### Privacy and usability

**Is user data stored or sent anywhere?**
The matrix is sent only to the app's own server, kept in memory for up to an hour (8 results at most), and not written to disk. The browser saves the matrix, the rows-per-page choice and the solution page's matrix in localStorage. There are no accounts, analytics or external requests.

**Does the app work offline?**
Once the server is running, yes. The page loads no outside scripts or fonts that it depends on, and jQuery is bundled. It does need the server running, which can be on the same computer.

**How accessible is it?**
The page has labels, keyboard support and a sensible focus order, and the table can be navigated with the arrow, Home, End and Page keys. However, no screen reader testing or automated accessibility audit has been done, and the subtotal strip adds 40 tab stops before the table.

**Why a Pac-Man theme?**
It is a visual choice that keeps the page high-contrast on a dark background. Positive terms are cyan and negative terms are red, so they can be told apart quickly, and the hover popup colors each row's pick in a different ghost color so the picks are easy to follow.

### Testing and deployment

**What do the tests not cover?**
Safari and Firefox, screen readers, an automated accessibility audit, very long-running load tests, and the Docker image.

**How would you deploy it?**
Run one Uvicorn worker, either directly or in the Docker image, behind a reverse proxy if it is public. Behind a proxy, start Uvicorn with `--proxy-headers` and a trusted `--forwarded-allow-ips` so the rate limiter sees each visitor's address. Because there are no accounts, only expose it to trusted users.

**What would break first if the app became popular?**
Memory and CPU. Each large calculation and each sorted index takes seconds and hundreds of MB, and the in-memory store works only in one process. Scaling up would need shared storage and a queue for large jobs, or a faster algorithm for large matrices alongside the Leibniz view.
