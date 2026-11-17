"""Browser tests for the frontend (static/script.js), driven by Playwright against a real server.

A module-scoped fixture starts uvicorn running main:app on a free port and a second one launches
Chromium. The module is skipped when playwright is not installed, and tests are skipped when the browser
cannot be launched. Tests cover calculating and formatting, input filtering and paste handling, undo and
redo, keyboard navigation, cancel, recovery from an expired calculation, jump to a permutation,
CSV download and copy, saved state, the teaching panel and the phone layout.
"""
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

ZEROS = [["0"] * 3 for _ in range(3)]


def free_port():
    """Ask the OS for an unused TCP port for the test server."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def base_url():
    """Start uvicorn on a free port for the module, wait until it answers, and stop it afterwards."""
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "warning"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            urllib.request.urlopen(url + "/", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("server did not start")
    yield url
    proc.terminate()
    proc.wait()


@pytest.fixture(scope="module")
def browser():
    """Launch headless Chromium once per module; skip the tests if it cannot start."""
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:
            pytest.skip(f"browser unavailable: {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url):
    """Open the app in a desktop sized page and wait for the first page of steps (the default 6 x 6 matrix)."""
    pg = browser.new_page(viewport={"width": 1280, "height": 900}, bypass_csp=True)
    pg.goto(base_url)
    pg.wait_for_selector("#steps-body .row")
    yield pg
    pg.close()


def fill(pg, values):
    """Resize the grid to len(values) and type each value into its cell."""
    n = len(values)
    pg.fill("#size", str(n))
    pg.press("#size", "Enter")
    pg.wait_for_selector(f'.cell[data-r="{n - 1}"][data-c="{n - 1}"]')
    for i, row in enumerate(values):
        for j, v in enumerate(row):
            pg.fill(f'.cell[data-r="{i}"][data-c="{j}"]', v)


def calc(pg):
    """Click Calculate and wait until the busy state ends and step rows are shown."""
    pg.click("#btn-calc")
    pg.wait_for_function("!document.body.classList.contains('busy')")
    pg.wait_for_selector("#steps-body .row")


def test_initial_determinant_and_selection(page):
    """The sample matrix loads with determinant 1,986 and selecting a row highlights one picked entry per row in the matrix."""
    assert page.inner_text("#det-value") == "1,986"
    page.click("#steps-body .row >> nth=3")
    assert page.locator("#matrix .cell.picked").count() == 6
    assert page.locator("#steps-body .row.on").count() == 1


def test_nonzero_toggle(page):
    """The nonzero switch reloads the list with only the nonzero terms and sets aria-checked."""
    page.click("#nz-switch")
    page.wait_for_function("document.querySelectorAll('#steps-body .row').length === 20")
    assert page.get_attribute("#nz-switch", "aria-checked") == "true"


def test_fraction_input_and_modes(page):
    """Decimal and fraction entries stay exact and the display toggles between decimals and fractions."""
    fill(page, [["1/2", "0.25"], ["3", "-4"]])
    calc(page)
    assert page.inner_text("#det-value") == "−2.75"
    page.click('#mode-seg button[data-mode="frac"]')
    assert page.inner_text("#det-value") == "−11/4"


def test_invalid_entry_shows_error(page):
    """An invalid entry (a fraction with zero denominator) shows the error message and marks the cell aria-invalid."""
    page.fill('.cell[data-r="0"][data-c="0"]', "1/0")
    page.click("#btn-calc")
    assert page.is_visible("#error")
    assert page.get_attribute('.cell[data-r="0"][data-c="0"]', "aria-invalid") == "true"


def test_undo_redo(page):
    """Undo and redo work from the buttons and from Ctrl+Z and Ctrl+Y."""
    cell = '.cell[data-r="0"][data-c="0"]'
    page.fill(cell, "9")
    page.press(cell, "Tab")
    page.click("#btn-undo")
    assert page.input_value(cell) == "3"
    page.click("#btn-redo")
    assert page.input_value(cell) == "9"
    page.keyboard.press("Control+z")
    assert page.input_value(cell) == "3"
    page.keyboard.press("Control+y")
    assert page.input_value(cell) == "9"


def test_ctrl_enter_calculates(page):
    """Ctrl+Enter recalculates from a cell and clears the stale warning."""
    cell = '.cell[data-r="0"][data-c="0"]'
    page.fill(cell, "4")
    page.press(cell, "Control+Enter")
    page.wait_for_function("document.querySelector('#det-value').textContent !== '1,986'")
    page.wait_for_function("!document.body.classList.contains('busy')")
    assert page.is_hidden("#stale")


def test_row_keyboard_navigation(page):
    """Arrow, Home and End keys move focus and selection between step rows, with a single roving tabindex."""
    page.focus("#steps-body .row >> nth=0")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.activeElement.dataset.i") == "2"
    assert page.locator("#steps-body .row.on").get_attribute("data-i") == "2"
    page.keyboard.press("End")
    assert page.evaluate("document.activeElement.dataset.i") == "99"
    page.keyboard.press("Home")
    assert page.evaluate("document.activeElement.dataset.i") == "0"
    assert page.locator('#steps-body .row[tabindex="0"]').count() == 1


def test_cancel_slow_calculation(page):
    """A 10 x 10 calculation shows the progress block, and Cancel stops it and shows the notice."""
    page.click("#btn-rand-size")
    page.fill("#size", "10")
    page.press("#size", "Enter")
    page.wait_for_selector('.cell[data-r="9"][data-c="9"]')
    for i in range(10):
        for j in range(10):
            page.fill(f'.cell[data-r="{i}"][data-c="{j}"]', str((i * 7 + j * 3) % 9 + 1))
    page.click("#btn-calc")
    page.wait_for_selector("#progress:not([hidden])", timeout=3000)
    page.click("#btn-cancel")
    page.wait_for_selector("#cancelled:not([hidden])")
    assert page.is_enabled("#btn-calc")


def test_recovers_from_expired_calculation(page, base_url):
    """A 404 from the steps endpoint makes the page resubmit the matrix and retry without showing an error."""
    first = page.evaluate("document.querySelector('#steps-body .row').dataset.i")
    assert first == "0"
    page.route("**/api/determinant/steps*", lambda route: handle_expire(route), times=1)
    page.click("#pg-next")
    page.wait_for_function("document.querySelector('#range').textContent.startsWith('Showing 101')")
    assert page.is_hidden("#error")


def handle_expire(route):
    """Route handler that answers a steps request with the expired-calculation 404."""
    route.fulfill(
        status=404,
        content_type="application/json",
        body='{"detail":"calculation not found or expired; submit the matrix again"}',
    )


def test_phone_shows_product_without_overflow(browser, base_url):
    """At 390 px wide the product column is visible under each permutation and the page does not scroll sideways."""
    pg = browser.new_page(viewport={"width": 390, "height": 800})
    pg.goto(base_url)
    pg.wait_for_selector("#steps-body .row")
    assert pg.is_visible("#steps-body .row >> nth=0 >> .prod")
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    pg.close()


def test_cells_reject_unneeded_characters(page):
    """Typing keeps only digits, dot, slash and minus, caps entries at 14 characters, and keeps the size box digits only."""
    cell = '.cell[data-r="0"][data-c="0"]'
    page.click(cell)
    page.keyboard.press("Control+a")
    page.keyboard.type("a1e3<b>/ 2x+")
    assert page.input_value(cell) == "13/2"
    page.keyboard.press("Control+a")
    page.keyboard.type("1" * 30)
    assert len(page.input_value(cell)) == 14
    page.keyboard.press("Control+a")
    page.keyboard.type("--1..2//3-")
    assert page.input_value(cell) == "-1.23"
    page.keyboard.press("Control+a")
    page.keyboard.type("/1\u00bd\u0663\U0001f600")
    assert page.input_value(cell) == "1"
    page.fill("#size", "1a")
    assert page.input_value("#size") == "1"


def test_paste_is_sanitised(page):
    """Pasting a multi-row clipboard fills the grid from the clipboard text with unwanted characters removed."""
    page.evaluate(
        r"""() => {
        const el = document.querySelector('.cell[data-r="0"][data-c="0"]');
        el.focus();
        const dt = new DataTransfer();
        dt.setData('text', '1<script>\t2/3\n-4\t5.5x');
        el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
    }"""
    )
    vals = [page.input_value(f'.cell[data-r="{r}"][data-c="{c}"]') for r, c in ((0, 0), (0, 1), (1, 0), (1, 1))]
    assert vals == ["1", "2/3", "-4", "5.5"]


def test_matrix_persists_across_reload(page, base_url):
    """The matrix and rows-per-page choice survive a reload, and Example restores the sample matrix."""
    page.fill('.cell[data-r="0"][data-c="0"]', "7/3")
    page.press('.cell[data-r="0"][data-c="0"]', "Tab")
    page.click('#size-seg button[data-size="250"]')
    page.wait_for_timeout(500)
    page.reload()
    page.wait_for_selector("#steps-body .row")
    assert page.input_value('.cell[data-r="0"][data-c="0"]') == "7/3"
    assert page.get_attribute('#size-seg button[data-size="250"]', "aria-pressed") == "true"
    page.click("#btn-example")
    assert page.input_value('.cell[data-r="0"][data-c="0"]') == "3"


def test_corrupt_saved_data_is_ignored(browser, base_url):
    """Invalid saved data in localStorage is discarded and the default matrix loads."""
    pg = browser.new_page()
    pg.goto(base_url)
    pg.evaluate("localStorage.setItem('determinant.v1', JSON.stringify({m: [['<img src=x>']], limit: 5}))")
    pg.reload()
    pg.wait_for_selector("#steps-body .row")
    assert pg.input_value('.cell[data-r="0"][data-c="0"]') == "3"
    pg.close()


def test_teaching_panel(page):
    """The teaching panel explains the selected term, expands a 3 x 3 matrix into 6 terms, and marks the current size in the growth table."""
    page.click("#learn-details summary")
    assert "inversion" in page.inner_text("#learn-term")
    page.fill("#size", "3")
    page.press("#size", "Enter")
    for i in range(3):
        for j in range(3):
            page.fill(f'.cell[data-r="{i}"][data-c="{j}"]', str(i + j + (1 if i == j else 0)))
    calc(page)
    assert page.locator("#learn-expansion .ex-row").count() == 6
    assert "Sum of all terms" in page.inner_text("#learn-expansion")
    assert page.locator("#growth div.now").count() == 1


def test_no_console_errors_under_csp(browser, base_url):
    """Loading the page under the real Content-Security-Policy logs no console or page errors."""
    pg = browser.new_page()
    errors = []
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(base_url)
    pg.wait_for_selector("#steps-body .row")
    assert errors == []
    pg.close()


def test_jump_to_permutation(page):
    """Go to permutation selects the typed permutation, shows its detail and reports its position."""
    page.fill("#jump-perm", "2 3 1 4 5 6")
    page.click("#jump-go")
    page.wait_for_function("document.querySelector('#steps-body .row.on') !== null && document.querySelector('#steps-body .row.on .perm').textContent === '2 3 1 4 5 6'")
    assert page.inner_text("#detail-title") == "Selected term #" + page.inner_text("#steps-body .row.on .r")
    assert "number 145 of 720" in page.inner_text("#jump-note")


def test_jump_to_last_and_other_page(page):
    """Jumping to the last permutation loads the right page and selects the row; Enter in the box also works."""
    page.fill("#jump-perm", "654321")
    page.press("#jump-perm", "Enter")
    page.wait_for_function("document.querySelector('#steps-body .row.on .perm')?.textContent === '6 5 4 3 2 1'")
    assert page.input_value("#pg-input") == "8"


def test_jump_rejects_bad_input(page):
    """Wrong length, repeats, out of range values and text give an error note, and the box strips separators."""
    for text in ["1 2 3", "1 1 2 3 4 5", "1 2 3 4 5 7", "abc"]:
        page.fill("#jump-perm", text)
        page.click("#jump-go")
        assert "exactly once" in page.inner_text("#jump-note")
    page.fill("#jump-perm", "1 2 3 4 5 6")
    page.fill("#jump-perm", "1;2")
    assert page.input_value("#jump-perm") == "12"


def test_jump_hidden_by_nonzero_filter(page):
    """With the nonzero filter on, jumping to a zero term says the term is not in the list."""
    page.click("#nz-switch")
    page.wait_for_function("document.querySelectorAll('#steps-body .row').length === 20")
    page.fill("#jump-perm", "654321")
    page.click("#jump-go")
    page.wait_for_function("document.querySelector('#jump-note').textContent.includes('zero term')")


def test_sign_filter_and_sort_controls(page):
    """Show and Order reload the list: Positive lists only positive terms, Highest first sorts them, and the nonzero switch locks on."""
    page.click("#sign-seg button[data-sign='pos']")
    page.wait_for_function("!document.querySelector('#range').textContent.endsWith(' of 720')")
    pos_total = int(page.inner_text("#range").split(" of ")[1].replace(",", ""))
    assert 0 < pos_total < 20
    page.click("#sign-seg button[data-sign='neg']")
    page.wait_for_function("document.querySelector('#loading').hidden")
    neg_total = int(page.inner_text("#range").split(" of ")[1].replace(",", ""))
    page.click("#sign-seg button[data-sign='pos']")
    page.wait_for_function("document.querySelector('#loading').hidden")
    assert pos_total + neg_total == 20
    assert page.get_attribute("#sign-seg button[data-sign='pos']", "aria-pressed") == "true"
    assert page.get_attribute("#nz-switch", "aria-checked") == "true"
    assert page.is_disabled("#nz-switch")
    assert not page.is_visible("#strip-wrap")
    page.click("#order-seg button[data-order='desc']")
    page.wait_for_function("document.querySelector('#running').textContent.includes('order')")
    terms = page.eval_on_selector_all("#steps-body .row", "els => els.map(e => e.textContent)")
    assert len(terms) > 1
    page.click("#sign-seg button[data-sign='all']")
    page.click("#order-seg button[data-order='perm']")
    page.wait_for_function("document.querySelector('#range').textContent.endsWith(' of 720')")
    assert not page.is_disabled("#nz-switch")
    assert page.is_visible("#strip-wrap")


def test_download_csv(page):
    """Download CSV delivers a file with the expected name and one line per term plus the header."""
    with page.expect_download() as info:
        page.click("#btn-export")
    dl = info.value
    assert dl.suggested_filename == "determinant-6x6-all-exact.csv"
    raw = open(dl.path(), "rb").read().decode("utf-8")
    assert raw.count("\r\n") == 721 and raw.startswith("index,permutation")


def test_download_nonzero_decimal(page):
    """The CSV filename reflects the nonzero filter and the decimal display mode."""
    page.fill('.cell[data-r="0"][data-c="0"]', "0.5")
    page.click("#btn-calc")
    page.wait_for_function("!document.body.classList.contains('busy')")
    page.click("#nz-switch")
    page.wait_for_function("document.querySelectorAll('#steps-body .row').length > 0 && document.querySelectorAll('#steps-body .row').length < 100")
    with page.expect_download() as info:
        page.click("#btn-export")
    assert info.value.suggested_filename == "determinant-6x6-nonzero-decimal.csv"


def test_copy_page(browser, base_url):
    """Copy this page puts the visible rows on the clipboard as tab separated text."""
    ctx = browser.new_context(permissions=["clipboard-read", "clipboard-write"], bypass_csp=True)
    pg = ctx.new_page()
    pg.goto(base_url)
    pg.wait_for_selector("#steps-body .row")
    pg.click("#btn-copy")
    pg.wait_for_function("document.querySelector('#export-note').textContent.startsWith('Copied 100 rows')")
    text = pg.evaluate("navigator.clipboard.readText()")
    lines = text.split("\n")
    assert len(lines) == 101 and lines[0].split("\t")[0] == "index"
    assert lines[1].split("\t")[1] == "1 2 3 4 5 6"
    ctx.close()


def test_labels_follow_calculated_size_not_grid_size(page):
    """After resizing the grid without recalculating, headings and the teaching panel still describe the calculated matrix."""
    page.fill('.cell[data-r="0"][data-c="0"]', "1/2")
    calc(page)
    page.click("#size-down")
    page.click("#size-down")
    page.click("#size-down")
    page.click('#mode-seg button[data-mode="dec"]')
    assert "6 × 6" in page.inner_text("#chips")
    page.click("#steps-body .row >> nth=2")
    assert "Every term" not in page.inner_text("#learn-expansion")
    assert page.locator("#growth div.now").text_content().startswith("n=6")


def test_pasted_typographic_minus_is_kept(page):
    """A pasted typographic minus sign is converted to an ASCII minus."""
    page.evaluate(
        r"""() => {
        const el = document.querySelector('.cell[data-r="0"][data-c="0"]');
        el.focus();
        const dt = new DataTransfer();
        dt.setData('text', '\u22123');
        el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
    }"""
    )
    assert page.input_value('.cell[data-r="0"][data-c="0"]') == "-3"


def test_download_error_stays_on_page(page):
    """A refused export check shows its message in the page and does not navigate away."""
    page.route("**/api/determinant/export/check*", lambda route: route.fulfill(
        status=429, content_type="application/json", body='{"detail":"other exports are still running; try again shortly"}'))
    page.click("#btn-export")
    page.wait_for_function("document.querySelector('#export-note').textContent.includes('still running')")
    assert "/api/determinant/export" not in page.url
    assert page.is_visible("#steps-body .row")


def test_hover_popup_shows_matrix_with_picked_entries(page):
    """Hovering a term row opens a matrix picture whose lit cells match the permutation; Escape and leaving close it."""
    calc(page)
    page.hover("#steps-body .row >> nth=3")
    page.wait_for_selector("#pop:not([hidden])")
    row_perm = page.locator("#steps-body .row >> nth=3 >> .perm").inner_text().split()
    n = len(row_perm)
    assert page.locator("#pop .pc").count() == n * n
    lit = page.locator("#pop .pc.on").count()
    assert lit == n
    cells = page.locator("#pop .pc")
    for i, col in enumerate(row_perm):
        assert "on" in cells.nth(i * n + int(col) - 1).get_attribute("class")
    box = page.locator("#pop").bounding_box()
    vp = page.viewport_size
    assert box["x"] >= 0 and box["y"] >= 0
    assert box["x"] + box["width"] <= vp["width"] and box["y"] + box["height"] <= vp["height"]
    page.keyboard.press("Escape")
    assert page.locator("#pop").is_hidden()
    page.hover("#steps-body .row >> nth=4")
    page.wait_for_selector("#pop:not([hidden])")
    page.mouse.move(5, 5)
    page.wait_for_selector("#pop", state="hidden")


def test_hover_popup_uses_calculated_matrix_not_edited_grid(page):
    """Editing the grid after calculating does not change the popup, which keeps showing the calculated entries."""
    page.fill('.cell[data-r="0"][data-c="0"]', "7")
    calc(page)
    page.fill('.cell[data-r="0"][data-c="0"]', "9")
    page.hover("#steps-body .row >> nth=0")
    page.wait_for_selector("#pop:not([hidden])")
    texts = page.locator("#pop .pc").all_inner_texts()
    assert texts[0] == "7"


def solution_text(pg, selector):
    """Text of an element on the solution page."""
    return pg.locator(selector).inner_text()


def test_solution_page_shows_every_nonzero_term(page, base_url):
    """The link from the result opens the solution page for the default matrix: all 20 nonzero terms, and det(A) = positives - negatives."""
    page.click("#open-solution")
    page.wait_for_url("**/solution.html")
    page.wait_for_selector("#sol-line2 .sol-term")
    page.wait_for_function("document.querySelectorAll('#sol-line1 .sol-term').length === 20")
    assert page.locator("#sol-line2 .sol-term").count() == 20
    assert "1,986" in solution_text(page, "#sol-det")
    assert "20 nonzero terms of 720 permutations" in solution_text(page, "#sol-title2")
    sums = page.locator("#sol-sums .sol-sum .mono").all_inner_texts()
    pos = int(sums[0].replace(",", ""))
    neg = int(sums[1].replace(",", "").replace("−", "-"))
    assert pos - neg == 1986
    assert page.locator("#sol-win").is_hidden()
    page.click("text=Back to the matrix")
    page.wait_for_selector("#steps-body .row")


def test_solution_page_zero_matrix(page):
    """An all-zero matrix has no nonzero terms: the page says so and shows det(A) = 0."""
    fill(page, [["0", "0"], ["0", "0"]])
    calc(page)
    page.click("#open-solution")
    page.wait_for_selector("#sol-zero:not([hidden])")
    assert "det(A) = 0" in solution_text(page, "#sol-det")
    assert page.locator("#sol-line1 .sol-term").count() == 0


def test_solution_page_windows_for_a_huge_expansion(browser, base_url):
    """A dense 9 x 9 has 362,880 nonzero terms, so the page shows windows of 24 and moves between them."""
    pg = browser.new_page(viewport={"width": 1280, "height": 900}, bypass_csp=True)
    pg.goto(base_url + "/solution.html")
    ones = [["1"] * 9 for _ in range(9)]
    pg.evaluate("m => localStorage.setItem('determinant.solution.v1', JSON.stringify({m}))", ones)
    pg.reload()
    pg.wait_for_selector("#sol-win:not([hidden])", timeout=60000)
    pg.wait_for_function("document.querySelectorAll('#sol-line1 .sol-term').length === 24")
    assert "Terms 1 to 24 of 362,880" in solution_text(pg, "#sol-title2")
    pg.click("#win-next")
    pg.wait_for_function("document.querySelector('#sol-title2').innerText.indexOf('Terms 25 to 48') === 0")
    pg.fill("#win-goto", "362880")
    pg.press("#win-goto", "Enter")
    pg.wait_for_function("document.querySelector('#sol-title2').innerText.indexOf('Terms 362,857 to 362,880') === 0")
    assert pg.locator("#win-next").is_disabled()
    assert "det(A) = 0" in solution_text(pg, "#sol-det")
    pg.close()


def test_solution_page_without_a_matrix(browser, base_url):
    """With nothing stored the solution page shows an empty state instead of failing."""
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(base_url + "/solution.html")
    pg.wait_for_selector("#sol-empty:not([hidden])")
    assert pg.locator("#sol-steps").is_hidden()
    ctx.close()


def test_solution_page_runs_under_the_content_security_policy(browser, base_url):
    """With the real CSP active the page still loads its script and renders the default example."""
    ctx = browser.new_context()
    pg = ctx.new_page()
    errors = []
    pg.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    pg.goto(base_url)
    pg.wait_for_selector("#steps-body .row")
    pg.click("#open-solution")
    pg.locator("#sol-line1 .sol-term").nth(19).wait_for()
    pg.wait_for_selector("#sol-sums .sol-sum")
    assert pg.locator("#sol-line1 .sol-term").count() == 20
    assert errors == []
    ctx.close()
