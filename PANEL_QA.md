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
Integers, decimals (up to 6 places) and fractions like `1/3`, with an optional minus sign. Typing is limited to text that can still become a valid entry, so letters, a second minus or a second dot never appear, and entries stop at 14 characters. The server validates everything again, because it never trusts the page.

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
- A pasted entry like `1.2.3` can still get in. It is marked invalid and blocked on Calculate.
- A dense 10 x 10 CSV is hundreds of MB and takes about a minute to produce.
- The first sorted view of a dense 10 x 10 takes several seconds and uses about 400 MB temporarily.

**How did you test it?**
114 automated tests in about 20 seconds. Backend tests compare against brute force and cover validation, security headers, rate limiting and edge cases such as empty views. Browser tests drive a real Chromium through the interface: input blocking, paging, filtering and sorting, the hover popup, the solution page, and a phone-width layout.

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
