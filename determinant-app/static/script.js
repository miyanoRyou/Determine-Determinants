/*
 * Frontend logic for the determinant viewer (jQuery, no build step).
 *
 * The page computes the determinant of an n x n matrix (n <= 10) with the
 * Leibniz permutation formula and lets the user inspect every term.
 *
 * Flow:
 *   1. buildGrid() creates the editable n x n grid of text inputs in #matrix.
 *      Entries stay strings (integers, decimals like 0.25, fractions like 1/3)
 *      so that nothing is rounded before the server sees it.
 *   2. calculate() reads the entries with readStrings() and POSTs them once to
 *      /api/determinant together with a client generated request_id. The reply
 *      holds the determinant as an exact "p/q" string, counts, and the strip
 *      data (40 slice subtotals) but no terms.
 *   3. loadPage() then fetches pages of terms on demand from
 *      GET /api/determinant/steps (calculation_id, page, limit, nonzero_only,
 *      at_rank). Only one page of rows is ever in the DOM (#steps-body).
 *   4. Selecting a row (click or keyboard) stores it in state.sel, highlights
 *      the picked cell of each matrix row (.picked, via refreshGrid), fills the
 *      detail panel (#detail) and the teaching panel (#learn-term, renderLearn).
 *
 * Other API use: GET /api/determinant/progress and POST
 * /api/determinant/cancel (progress bar and Cancel button, keyed by
 * request_id); GET /api/determinant/export/check followed by
 * GET /api/determinant/export (CSV download). If the server has forgotten a
 * calculation (404 "not found or expired") the page resubmits the matrix it
 * last calculated and retries, see recover().
 *
 * Numbers from the server are "p/q" strings of arbitrary size; they are parsed
 * and formatted with BigInt (fmt, fmtRat) and never converted to Number except
 * for bar heights in the strip (toFloat).
 *
 * Markup lives in index.html and layout in style.css. This file sets the CSS
 * variables --n, --gap, --left-w, --cell-font (layout) and --idx-w, --perm-w,
 * --term-w, --min-w, --table-h (renderSteps). Element ids used here: size,
 * size-down, size-up, size-mult, matrix, error, stale, hero-stale, result,
 * picked, picked-text, btn-* (calc, cancel, undo, redo, example, identity,
 * clear, rand-size, rand-elements, copy, export), progress, progress-fill,
 * progress-bar, progress-text, cancelled, viewer, loading, det-value,
 * det-alt, hero-label, chips, mode-wrap, mode-seg, mode-note, size-seg,
 * nz-switch, steps-body, table-box, range, running, pg-*, strip,
 * strip-caption, detail*, learn-term, learn-expansion, growth, jump-perm,
 * jump-go, jump-note, export-note, pop.
 */
$(function () {
  // Client side limits. They mirror the server checks in main.py (parse_entry);
  // the server repeats every check and does not trust the client.
  var MAX_N = 10;
  var MAX_ENTRY_CHARS = 14;
  var MAX_ABS = 1000000n;
  var MAX_DEN = 1000000n;
  // Accepted entry shapes: integer, decimal (".5", "2.", "0.25") and fraction ("1/3").
  var INT_RE = /^([+-]?)(\d+)$/;
  var DEC_RE = /^([+-]?)(\d*)\.(\d*)$/;
  var FRAC_RE = /^([+-]?)(\d+)\s*\/\s*(\d+)$/;
  // Example matrix, used on first load without saved data and by the Example button.
  var SAMPLE = [
    ['3', '0', '2', '0', '1', '0'],
    ['0', '4', '0', '1', '0', '2'],
    ['1', '0', '5', '0', '0', '3'],
    ['0', '2', '0', '6', '1', '0'],
    ['2', '0', '0', '1', '4', '0'],
    ['0', '1', '3', '0', '0', '5']
  ];

  // localStorage key and limits for persistence and input filtering. BAD_CHAR
  // (no g flag, safe for repeated .test) detects, BAD_CHARS strips, anything
  // except digits, dot, slash and minus. LIMITS are the allowed rows per page.
  var STORE_KEY = 'determinant.v1';
  var BAD_CHAR = /[^0-9.\/-]/;
  var ENTRY_PREFIX_RE = /^-?(\d*|\d*\.\d*|\d+\/\d*)$/;
  var BAD_CHARS = /[^0-9.\/-]/g;
  var LIMITS = [100, 250, 500, 1000];
  var MAX_PASTE_CHARS = 5000;
  // Pending debounce timer for saveNow, see scheduleSave.
  var saveTimer = null;

  // Shared UI state.
  //   n          size of the editable grid (set by buildGrid). Changes as soon as
  //              the user resizes the grid, even before any calculation.
  //   calcN      size of the last calculated matrix (set from the server reply).
  //              Used by everything that describes the results (permutation
  //              length, teaching panel, jump box). It differs from n whenever the
  //              grid was resized after the last calculation.
  //   id         calculation_id of the last result, or null before the first one.
  //   src        JSON of the strings that were calculated; compared with the grid
  //              by isDirty() and resubmitted by recover(). null if never calculated.
  //   total      n! for the calculated matrix (all permutations).
  //   nzTerms    number of terms with a nonzero product.
  //   nzAvailable  whether the server can serve the nonzero-only filter.
  //   strip      {slices, sums} subtotals for the "where the sum comes from" bars.
  //   det        determinant as an exact "p/q" string.
  //   check      true if the permutation sum matched the elimination cross-check.
  //   hasFrac    result needs the fraction/decimal toggle (some value is not an integer).
  //   mode       'frac' or 'dec': how fmt() renders values. Starts as 'dec' when
  //              the input used decimals, otherwise 'frac'.
  //   nz         nonzero terms only filter is on.
  //   limit      rows per page (one of LIMITS).
  //   page       current 1-based page number.
  //   res        last steps response (one page of terms plus paging data).
  //   sel        the selected step object from res.steps, or null.
  //   xhr        in-flight steps (or recover) request, aborted when superseded.
  //   calcXhr    in-flight calculation request.
  //   rid        request_id of the calculation in flight, null when idle. Doubles
  //              as the stale guard for progress polling.
  //   cancelled  set by the Cancel button so the failed (aborted) request is
  //              reported as cancelled rather than as an error.
  //   poll, showTimer  progress polling interval and the delay before showing the bar.
  var state = {
    n: 6, id: null, src: null, total: 0, nzTerms: 0, nzAvailable: false, strip: null,
    hasFrac: false, mode: 'frac', nz: false, sign: 'all', order: 'perm', limit: 100, page: 1, res: null, sel: null,
    xhr: null, calcXhr: null, calcN: 0, rid: null, cancelled: false, poll: null, showTimer: null
  };
  // Undo history: JSON snapshots of the grid strings (max 100) and the index of the
  // current one. Not persisted.
  var hist = [];
  var hi = -1;

  // Cached lookups. MINUS is the typographic minus (U+2212) used for display only;
  // it is never sent to the server and is stripped again by plain() and cleanEntry().
  var $matrix = $('#matrix');
  var $error = $('#error');
  var MINUS = '−';

  // Euclid's algorithm on BigInt; the sign of the inputs is ignored.
  function gcd(a, b) {
    if (a < 0n) a = -a;
    if (b < 0n) b = -b;
    while (b) { var t = a % b; a = b; b = t; }
    return a;
  }

  // Parses one entry string into a reduced fraction {n, d} of BigInts, or null if
  // invalid. Empty means 0. Called by refreshGrid (invalid highlight) and calculate
  // (pre-flight check). Bounds are tested as num > MAX_ABS * den to avoid dividing.
  // A lone "." matches DEC_RE but has no digits, hence the extra test.
  function parseEntry(text) {
    var t = $.trim(text);
    if (t === '') return { n: 0n, d: 1n };
    if (t.length > MAX_ENTRY_CHARS) return null;
    var m, num, den, sign;
    if ((m = INT_RE.exec(t))) {
      num = BigInt(m[2]); den = 1n; sign = m[1];
    } else if ((m = DEC_RE.exec(t)) && (m[2] !== '' || m[3] !== '')) {
      num = BigInt((m[2] || '0') + m[3]); den = 10n ** BigInt(m[3].length); sign = m[1];
    } else if ((m = FRAC_RE.exec(t))) {
      num = BigInt(m[2]); den = BigInt(m[3]); sign = m[1];
      if (den === 0n) return null;
    } else {
      return null;
    }
    var g = gcd(num, den);
    if (g > 1n) { num /= g; den /= g; }
    if (num > MAX_ABS * den || den > MAX_DEN) return null;
    return { n: sign === '-' ? -num : num, d: den };
  }

  // Thousands-separated integer string (BigInt or Number).
  function big(x) { return x.toLocaleString('en-US'); }

  // Formats num/den for display in 'frac' or 'dec' mode (integers always print as
  // integers). Decimals are rounded half up to 8 places using only BigInt:
  // q = round(a * 10^8 / d) = floor((2 * a * 10^8 + d) / (2 * d)), with a >= 0 and the
  // sign applied afterwards. "exact" is true when a * 10^8 divides evenly by d, i.e.
  // the value needs at most 8 decimals; otherwise the result is prefixed with the
  // approximation sign. A nonzero value that rounds to zero prints as "~ 0" with the approximation sign (U+2248) in place of the tilde.
  // Trailing zeros of the fraction part are dropped.
  function fmtRat(num, den, mode) {
    var neg = num < 0n;
    var a = neg ? -num : num;
    var d = den;
    var g = gcd(a, d);
    if (g > 1n) { a /= g; d /= g; }
    var sign = neg && a !== 0n ? MINUS : '';
    if (d === 1n) return sign + big(a);
    if (mode === 'frac') return sign + big(a) + '/' + big(d);
    var scale = 100000000n;
    var q = (a * scale * 2n + d) / (d * 2n);
    if (q === 0n) return '≈ 0';
    var exact = (a * scale) % d === 0n;
    var frac = (q % scale).toString().padStart(8, '0').replace(/0+$/, '');
    return (exact ? '' : '≈ ') + sign + big(q / scale) + (frac ? '.' + frac : '');
  }

  // Parses a server "p/q" (or plain integer) string into BigInts.
  function parseRat(s) {
    var i = s.indexOf('/');
    return i < 0 ? { n: BigInt(s), d: 1n } : { n: BigInt(s.slice(0, i)), d: BigInt(s.slice(i + 1)) };
  }

  // Formats a server value string using the current display mode (state.mode).
  function fmt(s) {
    var r = parseRat(s);
    return fmtRat(r.n, r.d, state.mode);
  }

  // Lossy Number conversion, only used for the relative heights of strip bars.
  function toFloat(s) {
    var r = parseRat(s);
    var v = Number(r.n) / Number(r.d);
    return isFinite(v) ? v : 0;
  }

  // Shows msg in #error, or hides the box when msg is empty.
  function showError(msg) {
    $error.text(msg).prop('hidden', !msg);
  }

  // Turns a failed jQuery request into a message. Returns '' for requests we
  // aborted ourselves (superseded or cancelled), so callers show nothing. FastAPI
  // sends detail either as a string or, for 422, as a list of validation errors.
  function errorText(xhr) {
    if (xhr.statusText === 'abort') return '';
    var body = xhr.responseJSON;
    if (body && typeof body.detail === 'string') return body.detail;
    if (body && $.isArray(body.detail) && body.detail.length) {
      return body.detail[0].msg.replace(/^Value error, /, '');
    }
    return 'Request failed (' + (xhr.status || 'network error') + ').';
  }

  // Normalises typed or pasted text for a cell: unicode minus and dashes become "-",
  // disallowed characters are dropped, length is capped.
  function cleanEntry(v) {
    return String(v).replace(/[\u2212\u2013\u2014]/g, '-').replace(BAD_CHARS, '').slice(0, MAX_ENTRY_CHARS);
  }

  // Reads and validates the saved matrix and rows-per-page from localStorage.
  // Called once at startup. Returns null (start from the sample) if anything is
  // missing or odd; storage access itself may throw in private modes.
  function loadSaved() {
    try {
      var raw = window.localStorage.getItem(STORE_KEY);
      if (!raw || raw.length > 20000) return null;
      var d = JSON.parse(raw);
      var m = d && d.m;
      if (!$.isArray(m) || m.length < 1 || m.length > MAX_N) return null;
      for (var i = 0; i < m.length; i++) {
        if (!$.isArray(m[i]) || m[i].length !== m.length) return null;
        for (var j = 0; j < m.length; j++) {
          if (typeof m[i][j] !== 'string' || m[i][j].length > MAX_ENTRY_CHARS || BAD_CHAR.test(m[i][j])) return null;
        }
      }
      return { m: m, limit: $.inArray(d.limit, LIMITS) >= 0 ? d.limit : 100 };
    } catch (e) {
      return null;
    }
  }

  // Writes the current grid and rows-per-page to localStorage. Called by the
  // debounce below and on pagehide. Failures are ignored; persistence is optional.
  function saveNow() {
    clearTimeout(saveTimer);
    try {
      window.localStorage.setItem(STORE_KEY, JSON.stringify({ m: readStrings(), limit: state.limit }));
    } catch (e) {}
  }

  // Debounced save, so typing does not write on every keystroke.
  function scheduleSave() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(saveNow, 300);
  }

  // Reads the grid into an array of rows of trimmed strings, in row/column order
  // taken from each input's data-r and data-c. This is what gets sent to the server.
  function readStrings() {
    var rows = [];
    $matrix.find('.cell').each(function () {
      var r = +$(this).data('r');
      (rows[r] = rows[r] || [])[+$(this).data('c')] = $.trim($(this).val());
    });
    return rows;
  }

  // Sizes the matrix area for the current n by setting CSS variables: --left-w
  // (width of the left column), --gap, --n (grid columns) and --cell-font. The font
  // is the largest size, capped by base, at which the longest entry still fits a
  // cell; 0.62 is an approximate glyph width in em for the monospace font, 8 px is
  // cell padding, and 10 px is the floor. Called after any change that can alter
  // entry lengths or n, and on window resize.
  function layout() {
    var n = state.n;
    var leftW = n <= 6 ? 430 : n <= 8 ? 500 : 580;
    var gap = n <= 6 ? 6 : 5;
    var root = document.documentElement.style;
    root.setProperty('--left-w', leftW + 'px');
    root.setProperty('--gap', gap + 'px');
    $matrix.css('--n', n);

    var maxLen = 2;
    $matrix.find('.cell').each(function () {
      var l = $.trim($(this).val()).length;
      if (l > maxLen) maxLen = l;
    });
    var width = $matrix.width() || (leftW - 100);
    var cellPx = (width - gap * (n - 1)) / n;
    var base = n <= 6 ? 15 : n <= 8 ? 14 : 13;
    var font = Math.max(10, Math.min(base, Math.floor((cellPx - 8) / (maxLen * 0.62))));
    root.setProperty('--cell-font', font + 'px');
  }

  // Rebuilds the n x n inputs from values (missing entries become '0') and
  // updates the size controls. Writes state.n. Called by init, setSize, restore
  // (undo/redo) and the Example button.
  function buildGrid(n, values) {
    $matrix.empty();
    for (var i = 0; i < n; i++) {
      for (var j = 0; j < n; j++) {
        var v = values && values[i] && values[i][j] !== undefined ? values[i][j] : '0';
        $('<input>', {
          type: 'text',
          'class': 'cell',
          inputmode: 'text',
          maxlength: MAX_ENTRY_CHARS,
          spellcheck: 'false',
          autocomplete: 'off',
          'aria-label': 'Row ' + (i + 1) + ', column ' + (j + 1),
          'data-r': i,
          'data-c': j,
          value: v
        }).appendTo($matrix);
      }
    }
    state.n = n;
    $('#size').val(n).attr('aria-invalid', 'false');
    $('#size-mult').text('× ' + n);
    $('#size-down').prop('disabled', n <= 1);
    $('#size-up').prop('disabled', n >= MAX_N);
    layout();
    refreshGrid();
  }

  // True when the grid differs from the matrix of the last result (or there is
  // none). Dirty means the displayed result and selection no longer match the grid.
  function isDirty() {
    return state.src === null || JSON.stringify(readStrings()) !== state.src;
  }

  // Syncs everything that depends on grid contents: invalid/zero/picked classes on
  // cells, the "outdated" markers, and the "picked by permutation" line. The picked
  // highlight is shown only when the grid is not dirty, because state.sel belongs to
  // the calculated matrix. Permutation entries are 1-based columns, hence perm[r] - 1.
  // Called after edits, layout changes, row selection and result rendering.
  function refreshGrid() {
    var dirty = isDirty();
    var perm = !dirty && state.sel ? state.sel.permutation : null;
    $matrix.find('.cell').each(function () {
      var $c = $(this);
      var r = +$c.data('r');
      var c = +$c.data('c');
      var p = parseEntry($c.val());
      $c.toggleClass('invalid', p === null)
        .attr('aria-invalid', p === null ? 'true' : 'false')
        .toggleClass('zero', p !== null && p.n === 0n)
        .toggleClass('picked', !!perm && perm[r] - 1 === c);
    });
    $('#stale').prop('hidden', !(dirty && state.id));
    $('#result').toggleClass('outdated', dirty && !!state.id);
    $('#hero-stale').prop('hidden', !(dirty && state.id));
    var showPicked = !dirty && !!state.sel;
    $('#picked').prop('hidden', !showPicked);
    if (showPicked) {
      $('#picked-text').text('Picked by permutation #' + state.sel.index.toLocaleString('en-US') + ': ' + state.sel.permutation.join(' '));
    }
  }

  // Current grid as a JSON string, the unit stored in the undo history.
  function snapshot() { return JSON.stringify(readStrings()); }

  // Enables Undo/Redo buttons. Undo stays enabled at the first entry if the grid
  // has uncommitted edits (typed but not yet pushed to history).
  function updateUndo() {
    $('#btn-undo').prop('disabled', hi <= 0 && snapshot() === hist[hi]);
    $('#btn-redo').prop('disabled', hi >= hist.length - 1);
  }

  // Records the current grid in the undo history (dropping any redo tail) and
  // schedules a save. Called after each committed edit (change event, paste,
  // fill, resize, example). No-op if nothing changed.
  function pushHistory() {
    var snap = snapshot();
    if (hist[hi] === snap) { updateUndo(); scheduleSave(); return; }
    hist = hist.slice(0, hi + 1);
    hist.push(snap);
    if (hist.length > 100) hist.shift();
    hi = hist.length - 1;
    updateUndo();
    scheduleSave();
  }

  // Rebuilds the grid from a history snapshot, keeping keyboard focus on the same
  // (clamped) cell when the grid size differs. Used by undo and redo.
  function restore(snap) {
    var f = document.activeElement;
    var fr = f && $(f).is('.cell') ? $(f).data('r') : null;
    var fc = f && $(f).is('.cell') ? $(f).data('c') : null;
    var rows = JSON.parse(snap);
    showError('');
    buildGrid(rows.length, rows);
    if (fr !== null) $matrix.find('.cell[data-r="' + Math.min(fr, rows.length - 1) + '"][data-c="' + Math.min(fc, rows.length - 1) + '"]').focus();
    updateUndo();
  }

  // Ctrl/Cmd+Z and Undo button. Pushes pending edits first so they can be redone.
  function undo() {
    pushHistory();
    if (hi > 0) { hi--; restore(hist[hi]); }
  }

  // Ctrl/Cmd+Y, Ctrl/Cmd+Shift+Z and Redo button.
  function redo() {
    if (hi < hist.length - 1) { hi++; restore(hist[hi]); }
  }

  // Resizes the grid to k x k. With keep, entries in the overlapping top-left
  // block are preserved; the rest are '0'. Called by the size input, the +/- buttons
  // and the random size button.
  function setSize(k, keep) {
    var old = readStrings();
    var next = [];
    for (var i = 0; i < k; i++) {
      next[i] = [];
      for (var j = 0; j < k; j++) next[i][j] = keep && old[i] && old[i][j] !== undefined ? old[i][j] : '0';
    }
    showError('');
    buildGrid(k, next);
    pushHistory();
  }

  // Replaces every cell with fn(row, col), then refreshes and records history.
  // Used by the random elements, identity and clear buttons.
  function fillAll(fn) {
    $matrix.find('.cell').each(function () {
      $(this).val(fn(+$(this).data('r'), +$(this).data('c')));
    });
    showError('');
    layout();
    refreshGrid();
    pushHistory();
  }

  // Client side request id for one calculation. The POST does not return until the
  // calculation is finished, so the id must exist beforehand for the page to poll
  // progress or cancel that specific job; the server uses it as the job key.
  function newRid() {
    return 'r' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  // Hides and resets the progress bar and stops its timers. Called when a
  // calculation ends (always) and before starting a new one.
  function stopProgress() {
    clearTimeout(state.showTimer);
    clearInterval(state.poll);
    state.showTimer = state.poll = null;
    $('#progress').prop('hidden', true);
    $('#progress-fill').css('width', '0');
  }

  // Starts progress polling for the calculation with request id rid. The bar only
  // appears after 300 ms so fast calculations do not flash it, then polls every
  // 250 ms. A reply is ignored if a newer calculation has replaced this one
  // (state.rid !== rid). The value is capped at 99 so the bar never reads 100%
  // before the result arrives.
  function startProgress(rid) {
    stopProgress();
    state.showTimer = setTimeout(function () {
      $('#progress').prop('hidden', false);
      state.poll = setInterval(function () {
        $.getJSON('/api/determinant/progress', { request_id: rid }).done(function (r) {
          if (state.rid !== rid || r.state !== 'running') return;
          var pct = Math.min(99, Math.floor(r.progress * 100));
          $('#progress-fill').css('width', pct + '%');
          $('#progress-bar').attr('aria-valuenow', pct);
          $('#progress-text').text('Calculating ' + pct + '%');
        });
      }, 250);
    }, 300);
  }

  // Validates the grid, then POSTs it once to /api/determinant. Triggered by the
  // Calculate button, Ctrl/Cmd+Enter, and once at startup. Aborts any older
  // calculation or steps request. On success it replaces the whole result state
  // (id, src, calcN, strip, ...), renders the summary and loads page 1 with the
  // first row selected. On failure it shows the error, or the "cancelled" note if
  // the user pressed Cancel (an abort also lands in .fail).
  function calculate() {
    if ($('body').hasClass('busy')) return;
    showError('');
    $('#cancelled').prop('hidden', true);
    var m = readStrings();
    var bad = false;
    for (var i = 0; i < m.length && !bad; i++) {
      for (var j = 0; j < m.length; j++) {
        if (parseEntry(m[i][j]) === null) { bad = true; break; }
      }
    }
    if (bad) {
      refreshGrid();
      showError('Some entries are not valid. Use integers, decimals like 0.25, or fractions like 1/3 (up to 14 characters, denominator not zero, absolute value at most 1,000,000).');
      return;
    }
    if (state.calcXhr) state.calcXhr.abort();
    if (state.xhr) state.xhr.abort();

    $('body').addClass('busy');
    $('#btn-calc').prop('disabled', true).text('Calculating');
    var rid = newRid();
    state.rid = rid;
    state.cancelled = false;
    startProgress(rid);

    state.calcXhr = $.ajax({
      url: '/api/determinant',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({ matrix: m, request_id: rid })
    }).done(function (res) {
      state.id = res.calculation_id;
      state.src = JSON.stringify(m);
      state.n = res.n;
      state.calcN = res.n;
      state.total = res.total_permutations;
      state.nzTerms = res.nonzero_terms;
      state.nzAvailable = res.nonzero_filter_available;
      state.strip = res.strip;
      state.det = res.determinant;
      state.hasFrac = res.has_fractions;
      state.mode = res.has_decimals ? 'dec' : 'frac';
      state.nz = false;
      state.sign = 'all';
      state.order = 'perm';
      state.sel = null;
      state.res = null;
      state.check = res.cross_check_passed;
      saveSolution(m);
      renderResult();
      $('#viewer').prop('hidden', false);
      loadPage(1, { selectFirst: true });
    }).fail(function (xhr) {
      if (state.cancelled) {
        $('#cancelled').prop('hidden', false);
      } else {
        showError(errorText(xhr));
      }
    }).always(function () {
      stopProgress();
      state.rid = null;
      $('body').removeClass('busy');
      $('#btn-calc').prop('disabled', false).text('Calculate determinant');
    });
  }

  // Remembers the matrix of the last successful calculation for the solution page (solution.js), which reads it
  // from localStorage and asks the server for the result again. Failing to store it only disables that link's data.
  function saveSolution(m) {
    try {
      window.localStorage.setItem('determinant.solution.v1', JSON.stringify({ m: m }));
    } catch (e) { /* storage may be blocked; the solution page then shows its empty state */ }
  }

  // Draws the result card from state: determinant (font shrinks as the text gets
  // longer), the alternative representation, count chips, the fraction/decimal
  // toggle and the nonzero switch. Called after a calculation and when the display
  // mode changes. All strings placed with .html() here are built from numbers and
  // fixed text, not user input.
  function renderResult() {
    var det = fmt(state.det);
    $('#det-value').text(det).css('font-size', det.length <= 8 ? '' : det.length <= 12 ? '44px' : det.length <= 18 ? '34px' : det.length <= 28 ? '26px' : '18px');
    $('#hero-label').text('Determinant of the ' + state.calcN + ' × ' + state.calcN + ' matrix');
    var r = parseRat(state.det);
    if (r.d !== 1n) {
      $('#det-alt').text(state.mode === 'frac'
        ? 'As a decimal: ' + fmtRat(r.n, r.d, 'dec')
        : 'Exact fraction: ' + fmtRat(r.n, r.d, 'frac')).prop('hidden', false);
    } else {
      $('#det-alt').prop('hidden', true);
    }
    var chips = [
      big(state.total) + ' permutations',
      big(state.nzTerms) + ' nonzero terms',
      state.check ? 'Permutation sum matches elimination' : 'Permutation sum differs from elimination'
    ];
    $('#chips').html($.map(chips, function (c) { return '<span class="chip">' + c + '</span>'; }).join(''));

    $('#mode-wrap').prop('hidden', !state.hasFrac);
    $('#mode-seg button').each(function () {
      $(this).attr('aria-pressed', String($(this).data('mode') === state.mode));
    });
    $('#mode-note').text(state.mode === 'dec'
      ? 'Decimals marked with ≈ are rounded to 8 places. Fractions are exact.'
      : 'Fractions are shown in lowest terms.');

    $('#result').prop('hidden', false);
    $('#open-solution').prop('hidden', false);
    $('#steps-sub').text('Every one of the ' + big(state.total) + ' terms, one page at a time');

    syncViewControls();
  }

  // True when a sign filter or a sort is active. Those views list nonzero terms only, which the server enforces.
  function customView() {
    return state.sign !== 'all' || state.order !== 'perm';
  }

  // Brings the Show and Order buttons and the nonzero switch in line with state. An active filter or sort locks the switch on,
  // since zero terms have no sign and no place in a sort.
  function syncViewControls() {
    var locked = customView();
    $('#sign-seg button').each(function () { $(this).attr('aria-pressed', String($(this).data('sign') === state.sign)); });
    $('#order-seg button').each(function () { $(this).attr('aria-pressed', String($(this).data('order') === state.order)); });
    $('#nz-switch')
      .prop('disabled', !state.nzAvailable || locked)
      .attr('aria-checked', String(locked || (state.nz && state.nzAvailable)));
    $('#strip-wrap').prop('hidden', locked);
    $('#steps-sub').text(locked ? 'Nonzero terms only, filtered or sorted by value' : 'Every one of the ' + big(state.total) + ' terms, one page at a time');
  }

  // Called when the server no longer knows state.id (it keeps only a few results
  // for a limited time). Resubmits state.src, the matrix of the last calculation
  // (not any later grid edits), without a request_id, stores the new id and runs
  // retry. Used by loadPage and by the export check.
  function recover(retry) {
    $('#loading').prop('hidden', false);
    state.xhr = $.ajax({
      url: '/api/determinant',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({ matrix: JSON.parse(state.src) })
    }).done(function (res) {
      state.id = res.calculation_id;
      retry();
    }).fail(function (xhr) {
      $('#loading').prop('hidden', true);
      showError(errorText(xhr));
    });
  }

  // Fetches one page of terms and renders it. Triggered by paging controls, rows
  // per page, the nonzero switch, strip bars, jump to permutation, and after a
  // calculation. opts: selectFirst (select the first row), atRank (ask the server
  // for the page that contains this 0-based rank, overriding page), selectRank
  // (after loading, select that rank; step.index is 1-based, hence + 1), retried
  // (internal, limits the 404 recovery to one attempt). A newer call aborts the
  // previous request, whose callbacks then never run, so only the latest response
  // is applied. A 404 whose detail says the calculation is gone is recovered by
  // resubmitting; other 404s (such as page out of range) are shown as errors.
  // The loading indicator is left on while recovering.
  function loadPage(page, opts) {
    opts = opts || {};
    if (!state.id) return;
    var req = { calculation_id: state.id, page: page, limit: state.limit, nonzero_only: state.nz || customView() };
    if (state.sign !== 'all') req.sign = state.sign;
    if (state.order !== 'perm') req.order = state.order;
    if (opts.atRank !== undefined) req.at_rank = opts.atRank;

    if (state.xhr) state.xhr.abort();
    $('#loading').prop('hidden', false);

    var recovering = false;
    state.xhr = $.getJSON('/api/determinant/steps', req).done(function (res) {
      state.res = res;
      state.page = res.page;
      var found = null;
      if (opts.selectRank !== undefined) {
        $.each(res.steps, function (_, st) { if (st.index === opts.selectRank + 1) { found = st; return false; } });
        if (found) state.sel = found;
        if (!found) setJumpNote(customView() ? 'That permutation is not in the current view (zero term or the other sign). Showing the nearest page. Set Show to All and Order to Permutation to see it.' : 'That permutation has a zero term, so the nonzero filter hides it. Turn the filter off to see it.', true);
      } else if ((opts.selectFirst || !state.sel) && res.steps.length) {
        state.sel = res.steps[0];
      }
      renderSteps();
      var $body = $('#table-box .body');
      var $on = $body.find('.row.on');
      if (found && $on.length) {
        $body.scrollTop($on.offset().top - $body.offset().top + $body.scrollTop() - $body.innerHeight() / 2);
      } else {
        $body.scrollTop(0);
      }
    }).fail(function (xhr) {
      var detail = xhr.responseJSON && xhr.responseJSON.detail;
      var expired = xhr.status === 404 && typeof detail === 'string' && detail.indexOf('not found or expired') !== -1;
      if (expired && state.src && !opts.retried) {
        recovering = true;
        recover(function () { loadPage(page, $.extend({}, opts, { retried: true })); });
        return;
      }
      var msg = errorText(xhr);
      if (msg) showError(msg);
    }).always(function () {
      if (!recovering) $('#loading').prop('hidden', true);
    });
  }

  // Message line under the jump box; bad marks it as an error.
  function setJumpNote(text, bad) {
    $('#jump-note').text(text).toggleClass('bad', !!bad);
  }

  // 0-based lexicographic rank of permutation p (values 1..n) using the factorial
  // number system: for each position, the count of smaller later values times
  // (n - 1 - position)!. Matches the server's ordering of terms. Number is exact
  // here since 10! is small.
  function permRank(p) {
    var n = p.length;
    var fact = [1];
    for (var i = 1; i <= n; i++) fact.push(fact[i - 1] * i);
    var rank = 0;
    for (var a = 0; a < n; a++) {
      var smaller = 0;
      for (var b = a + 1; b < n; b++) if (p[b] < p[a]) smaller++;
      rank += smaller * fact[n - 1 - a];
    }
    return rank;
  }

  // Parses "2 1 3", "2,1,3" or, for n <= 9, the run-together "213" into an array
  // of numbers, or null unless it is a permutation of 1..n. With n = 10 digits
  // must be separated.
  function parsePerm(text, n) {
    var t = $.trim(text);
    var parts = /[ ,]/.test(t) ? t.split(/[ ,]+/) : (n <= 9 ? t.split('') : [t]);
    var p = $.map(parts, function (x) { return /^[0-9]+$/.test(x) ? parseInt(x, 10) : NaN; });
    if (p.length !== n) return null;
    var seen = {};
    for (var i = 0; i < n; i++) {
      if (!(p[i] >= 1 && p[i] <= n) || seen[p[i]]) return null;
      seen[p[i]] = true;
    }
    return p;
  }

  // Go button and Enter in the jump box. Ranks the typed permutation locally and
  // loads the page that contains it, selecting that row.
  function jumpToPermutation() {
    if (!state.id) return;
    var n = state.calcN;
    var p = parsePerm($('#jump-perm').val(), n);
    if (!p) {
      setJumpNote('Enter each number from 1 to ' + n + ' exactly once, separated by spaces.', true);
      return;
    }
    var rank = permRank(p);
    setJumpNote('Permutation ' + p.join(' ') + ' is number ' + big(rank + 1) + ' of ' + big(state.total) + '.', false);
    loadPage(1, { atRank: rank, selectRank: rank });
  }

  // Escapes text for HTML. Double quotes are covered because it is also used inside
  // title="..." attributes.
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  // One factor for display; negatives are parenthesised in a product.
  function fac(s) {
    var t = fmt(s);
    return t.charAt(0) === MINUS ? '(' + t + ')' : t;
  }

  // Renders the current page (state.res) as rows, the paging controls and the
  // column sizing, then the strip, detail panel, export label and matrix.
  // Called after each steps response and when the display mode changes.
  // Roving tabindex: only the selected row (or the first, if none is on this page)
  // has tabindex 0, so Tab enters the list once and arrow keys move within it, see
  // the keydown handler on #steps-body.
  // Column widths are computed from content and written to CSS variables used by
  // the grid-template-columns of the rows in style.css: 8.4 px is an approximate
  // monospace character width, 21 px per permutation digit plus padding, and
  // --min-w adds the fixed sign column (32), the minimum product column (100) and
  // gaps and padding (40 + 28) to the three computed widths.
  function renderSteps() {
    var res = state.res;
    if (!res) return;
    hidePop();
    var steps = res.steps;
    var html = new Array(steps.length);
    var termChars = 4;
    for (var i = 0; i < steps.length; i++) {
      var st = steps[i];
      var zero = st.product === '0';
      var tone = st.sign > 0 ? 'pos' : 'neg';
      var term = fmt(st.term);
      var expr = $.map(st.factors, fac).join(' × ') + ' = ' + fmt(st.product);
      if (term.length > termChars) termChars = term.length;
      var on = state.sel && state.sel.index === st.index;
      html[i] =
        '<button type="button" class="row' + (zero ? ' zero' : '') + (on ? ' on' : '') + '" data-i="' + i + '" tabindex="' + (on ? '0' : '-1') + '" aria-pressed="' + (on ? 'true' : 'false') + '">' +
        '<span class="r">' + st.index.toLocaleString('en-US') + '</span>' +
        '<span class="perm">' + st.permutation.join(' ') + '</span>' +
        '<span class="sg ' + tone + '">' + (st.sign > 0 ? '+' : MINUS) + '</span>' +
        '<span class="prod" title="' + esc(expr) + '">' + esc(expr) + '</span>' +
        '<span class="term ' + tone + '" title="' + esc(term) + '">' + esc(term) + '</span>' +
        '</button>';
    }
    $('#steps-body').html(html.join(''));
    if (!$('#steps-body .row.on').length) $('#steps-body .row').first().attr('tabindex', '0');

    var n = state.calcN;
    var permW = Math.max(104, n * 21 + 14);
    var termW = Math.min(360, Math.max(96, Math.round(termChars * 8.4 + 14)));
    var root = document.documentElement.style;
    var idxW = steps.length
      ? Math.max(64, Math.round(steps[steps.length - 1].index.toLocaleString('en-US').length * 8.4 + 24))
      : 64;
    root.setProperty('--idx-w', idxW + 'px');
    root.setProperty('--perm-w', permW + 'px');
    root.setProperty('--term-w', termW + 'px');
    root.setProperty('--min-w', (idxW + permW + 32 + 100 + termW + 40 + 28) + 'px');
    root.setProperty('--table-h', (state.limit <= 100 ? 420 : state.limit <= 250 ? 480 : 560) + 'px');

    var from = (res.page - 1) * res.limit + 1;
    $('#range').text('Showing ' + big(steps.length ? from : 0) + ' to ' + big(steps.length ? from + steps.length - 1 : 0) + ' of ' + big(res.total_steps));
    $('#running').text('Running total through this page: ' + fmt(res.running_total) + (customView() ? ' (in this view\'s order)' : ''));
    $('#pg-input').val(res.page).css('width', Math.max(52, String(res.total_pages).length * 10 + 28) + 'px');
    $('#pg-total').text(big(res.total_pages));
    $('#pg-first, #pg-prev').prop('disabled', res.page <= 1);
    $('#pg-next, #pg-last').prop('disabled', res.page >= res.total_pages);

    renderStrip();
    renderDetail();
    exportLabel();
    refreshGrid();
  }

  // Draws the 40-bar "where the sum comes from" strip from state.strip. Bar b covers
  // ranks [start, ceil((b + 1) * total / slices)); this must stay identical to the
  // slice boundaries computed by the server. Bars point up for positive and down
  // for negative subtotals, scaled to the largest magnitude, with a 2 px minimum so
  // a nonzero subtotal stays visible. A bar is highlighted if it overlaps the
  // rank range of the current page. Clicking a bar loads the page at its start.
  function renderStrip() {
    var s = state.strip;
    var res = state.res;
    if (!s) return;
    var N = state.total;
    var SL = s.slices;
    var vals = $.map(s.sums, toFloat);
    var maxAbs = 0;
    $.each(vals, function (_, v) { if (Math.abs(v) > maxAbs) maxAbs = Math.abs(v); });
    var endOf = function (b) { return Math.ceil((b + 1) * N / SL); };
    var html = [];
    for (var b = 0; b < SL; b++) {
      var start = b === 0 ? 0 : endOf(b - 1);
      var end = endOf(b);
      var on = res && res.first_rank !== null && start <= res.last_rank && end > res.first_rank;
      var v = vals[b];
      var h = v === 0 || maxAbs === 0 ? 0 : Math.max(2, Math.round(Math.abs(v) / maxAbs * 28));
      html.push(
        '<button type="button" class="bar' + (on ? ' on' : '') + '" data-start="' + start + '" aria-label="' +
        'Permutations ' + big(start + 1) + ' to ' + big(end) + ', subtotal ' + esc(fmt(s.sums[b])) + '">' +
        '<span class="half up">' + (v > 0 ? '<i style="height:' + h + 'px"></i>' : '') + '</span>' +
        '<span class="half down">' + (v < 0 ? '<i style="height:' + h + 'px"></i>' : '') + '</span>' +
        '</button>');
    }
    $('#strip').html(html.join(''));
    var per = Math.ceil(N / SL);
    $('#strip-caption').text('Where the sum comes from, across all ' + big(N) + ' permutations' +
      (per > 1 ? ' (each bar covers about ' + big(per) + ')' : ''));
  }

  // Index pairs (i, j), i < j, with p[i] > p[j]. Their count is the number of
  // inversions, whose parity gives the sign of the term.
  function inversionPairs(p) {
    var out = [];
    for (var i = 0; i < p.length; i++) {
      for (var j = i + 1; j < p.length; j++) {
        if (p[i] > p[j]) out.push([i, j]);
      }
    }
    return out;
  }

  // Fills the teaching panel: the walk through the selected term, the full list of
  // terms for matrices up to 3 x 3 (only when the unfiltered page holds all of
  // them), and the n! versus about n^3 table, with the current calcN marked.
  // Called from renderDetail and at startup.
  function renderLearn() {
    var $term = $('#learn-term');
    var $exp = $('#learn-expansion');
    var st = state.sel;
    if (st && state.id) {
      var pairs = inversionPairs(st.permutation);
      var shown = $.map(pairs.slice(0, 10), function (q) {
        return 'rows ' + (q[0] + 1) + ' and ' + (q[1] + 1) + ' (' + st.permutation[q[0]] + ' comes before ' + st.permutation[q[1]] + ')';
      });
      var inv = st.inversions;
      var html = '<h3>The selected term, #' + st.index.toLocaleString('en-US') + '</h3>' +
        '<p>Permutation ' + st.permutation.join(' ') + ' picks ' +
        $.map(st.permutation, function (c, i) { return 'a(' + (i + 1) + ',' + c + ')'; }).join(', ') + '.</p>' +
        '<p>' + (inv === 0
          ? 'No pair of rows is out of order, so there are 0 inversions.'
          : 'Out-of-order pairs: ' + shown.join('; ') + (pairs.length > shown.length ? '; and ' + (pairs.length - shown.length) + ' more' : '') + '.') + '</p>' +
        '<p>' + inv + ' inversion' + (inv === 1 ? '' : 's') + ' is ' + (inv % 2 === 0 ? 'even, so the sign is +' : 'odd, so the sign is ' + MINUS) + '.</p>' +
        '<p>Product ' + esc(fmt(st.product)) + ' with that sign gives the term ' + esc(fmt(st.term)) + '.</p>';
      $term.html(html);
    } else {
      $term.empty();
    }

    if (state.id && state.calcN <= 3 && state.res && !state.nz && !customView() && state.res.total_steps === state.total) {
      var rows = $.map(state.res.steps, function (t) {
        var tone = t.sign > 0 ? 'pos' : 'neg';
        var names = $.map(t.permutation, function (c, i) { return 'a(' + (i + 1) + ',' + c + ')'; }).join(' · ');
        return '<div class="ex-row' + (t.product === '0' ? ' zero' : '') + '"><span class="' + tone + '">' + (t.sign > 0 ? '+' : MINUS) + '</span>' +
          '<span>' + names + '</span><span>' + esc($.map(t.factors, fac).join(' × ')) + ' = ' + esc(fmt(t.product)) + '</span>' +
          '<span>term ' + esc(fmt(t.term)) + '</span></div>';
      });
      $exp.html('<h3>Every term of this ' + state.calcN + ' × ' + state.calcN + ' matrix</h3>' + rows.join('') +
        '<div class="ex-sum">Sum of all terms = ' + esc(fmt(state.det)) + '</div>');
    } else if (state.id && state.calcN <= 3 && (state.nz || customView())) {
      $exp.html('<p class="note">Turn off "Nonzero terms only" and reset Show and Order to see every term written out.</p>');
    } else {
      $exp.empty();
    }

    var g = [];
    var f = 1;
    for (var n = 1; n <= MAX_N; n++) {
      f *= n;
      g.push('<div' + (n === state.calcN ? ' class="now"' : '') + '>n=' + n + ': ' + f.toLocaleString('en-US') + ' terms, about ' + (n * n * n).toLocaleString('en-US') + ' steps by elimination</div>');
    }
    $('#growth').html(g.join(''));
  }

  // Fills the detail panel for state.sel (permutation, inversions, sign, each
  // factor, product, term) and refreshes the teaching panel. Called after a page
  // renders and when a row is selected.
  function renderDetail() {
    var st = state.sel;
    renderLearn();
    $('#detail').prop('hidden', !st);
    if (!st) return;
    var zero = st.product === '0';
    $('#detail-title').text('Selected term #' + st.index.toLocaleString('en-US'));
    $('#detail-meta').text('σ = ' + st.permutation.join(' ') + ', ' + st.inversions + ' inversions, sign ' + (st.sign > 0 ? '+' : MINUS));
    var chips = $.map(st.factors, function (f, i) {
      var v = fmt(f);
      return '<span' + (f === '0' ? ' class="z"' : '') + '>a(' + (i + 1) + ',' + st.permutation[i] + ') = ' + esc(v) + '</span>';
    });
    $('#detail-chips').html(chips.join(''));
    var tone = zero ? 'var(--zero)' : st.sign > 0 ? 'var(--pos)' : 'var(--neg)';
    $('#detail-line').html('Product ' + esc(fmt(st.product)) + ', term <b style="color:' + tone + '">' + esc(fmt(st.term)) + '</b>');
    $('#detail-zero').prop('hidden', !zero);
  }

  // Size controls. The size input applies a typed size on change; a size outside
  // 1..MAX_N is flagged and not applied.
  $('#size').on('change', function () {
    var v = $.trim($(this).val());
    var k = /^\d+$/.test(v) ? parseInt(v, 10) : -1;
    if (k < 1 || k > MAX_N) {
      $(this).attr('aria-invalid', 'true');
      showError('Size must be a whole number from 1 to ' + MAX_N + '.');
      return;
    }
    setSize(k, true);
  });
  $('#size-down').on('click', function () { setSize(Math.max(1, state.n - 1), true); });
  $('#size-up').on('click', function () { setSize(Math.min(MAX_N, state.n + 1), true); });

  // Toolbar buttons: random size (always different from the current one), random
  // elements in -9..9, identity, clear, calculate, example, undo and redo.
  $('#btn-rand-size').on('click', function () {
    var k;
    do { k = 2 + Math.floor(Math.random() * (MAX_N - 1)); } while (k === state.n);
    setSize(k, true);
  });
  $('#btn-rand-elements').on('click', function () {
    fillAll(function () { return String(Math.floor(Math.random() * 19) - 9); });
  });
  $('#btn-identity').on('click', function () {
    fillAll(function (r, c) { return r === c ? '1' : '0'; });
  });
  $('#btn-clear').on('click', function () {
    fillAll(function () { return '0'; });
  });
  $('#btn-calc').on('click', calculate);
  $('#btn-example').on('click', function () {
    showError('');
    buildGrid(SAMPLE.length, SAMPLE);
    pushHistory();
  });
  $('#btn-undo').on('click', undo);
  $('#btn-redo').on('click', redo);

  // Cancel button for a running calculation: marks it cancelled, tells the server to
  // stop the job with the same request id, and aborts the local request.
  $('#btn-cancel').on('click', function () {
    if (!state.rid || !state.calcXhr) return;
    state.cancelled = true;
    $.post('/api/determinant/cancel?request_id=' + encodeURIComponent(state.rid));
    state.calcXhr.abort();
  });

  // Keyboard shortcuts. Ctrl/Cmd+Enter calculates from anywhere. Undo/redo
  // (Z, Y, Shift+Z) apply only when focus is in the matrix, the toolbar, the size
  // control or on the body, so they do not hijack text editing in other inputs.
  $(document).on('keydown', function (e) {
    var mod = e.ctrlKey || e.metaKey;
    if (!mod) return;
    var key = e.key.toLowerCase();
    if (key === 'enter') {
      e.preventDefault();
      calculate();
      return;
    }
    var t = e.target;
    var scoped = t === document.body || $(t).closest('#matrix, .toolbar, .size-control').length > 0;
    if (!scoped) return;
    if (key === 'z' && !e.shiftKey) { e.preventDefault(); undo(); }
    else if (key === 'y' || (key === 'z' && e.shiftKey)) { e.preventDefault(); redo(); }
  });

  // Input sanitising while typing: block insertion of disallowed characters before
  // they reach the field. Paste is excluded here and handled by the paste handler.
  $matrix.on('beforeinput', '.cell', function (e) {
    var oe = e.originalEvent;
    if (oe && typeof oe.data === 'string' && oe.inputType && oe.inputType.indexOf('insert') === 0 &&
        oe.inputType !== 'insertFromPaste') {
      // Block the keystroke unless the text it would produce could still grow into a valid entry
      // (an optional minus, then digits, a decimal, or a fraction), so stray characters and
      // repeated minus, dot or slash signs never appear. Deleting is always allowed.
      var el = e.target;
      var next = el.value.slice(0, el.selectionStart) + oe.data + el.value.slice(el.selectionEnd);
      if (BAD_CHAR.test(oe.data) || next.length > MAX_ENTRY_CHARS || !ENTRY_PREFIX_RE.test(next)) e.preventDefault();
    }
  });

  // After every edit: remove anything that slipped through (drag and drop, IME,
  // autofill) while keeping the caret in place, then update layout, validity marks,
  // undo button and the pending save.
  $matrix.on('input', '.cell', function () {
    var v = this.value;
    var c = cleanEntry(v);
    if (c !== v) {
      var pos = Math.max(0, (this.selectionStart || c.length) - (v.length - c.length));
      this.value = c;
      if (this.setSelectionRange) this.setSelectionRange(pos, pos);
    }
    layout();
    refreshGrid();
    updateUndo();
    scheduleSave();
  });

  // The size and page number boxes accept digits only.
  $('#size, #pg-input').on('input', function () {
    var c = this.value.replace(/[^0-9]/g, '');
    if (c !== this.value) this.value = c;
  });

  // Commit point for undo history: the change event fires when a cell loses focus
  // after an edit.
  $matrix.on('change', '.cell', pushHistory);

  // Select the whole entry on focus so typing replaces it.
  $matrix.on('focus', '.cell', function () { this.select(); });

  // Moves focus between cells: Up and Down change row, Enter moves right and wraps to
  // the start of the next row.
  $matrix.on('keydown', '.cell', function (e) {
    var map = { ArrowUp: [-1, 0], ArrowDown: [1, 0], Enter: [0, 1] };
    var d = map[e.key];
    if (!d) return;
    e.preventDefault();
    var nr = +$(this).data('r') + d[0];
    var nc = +$(this).data('c') + d[1];
    if (nc >= state.n) { nc = 0; nr += 1; }
    if (nr < 0 || nr >= state.n) return;
    $matrix.find('.cell[data-r="' + nr + '"][data-c="' + nc + '"]').focus();
  });

  // Paste handling. A single value is inserted at the selection like normal typing
  // (after cleaning). Multi-cell text (spreadsheet rows split on tabs, commas,
  // semicolons or spaces) fills the grid from the focused cell down and right,
  // clipped to the grid. Input is capped in length and size before parsing.
  $matrix.on('paste', '.cell', function (e) {
    var text = String((e.originalEvent.clipboardData || window.clipboardData).getData('text')).slice(0, MAX_PASTE_CHARS);
    var rows = $.trim(text).split(/\r?\n/).slice(0, MAX_N);
    var parsed = $.map(rows, function (line) { return [$.trim(line).split(/[\t,; ]+/).slice(0, MAX_N)]; });
    if (parsed.length === 1 && parsed[0].length === 1) {
      e.preventDefault();
      var one = cleanEntry(parsed[0][0]);
      var el = this;
      var cur = el.value;
      var a = el.selectionStart === null ? cur.length : el.selectionStart;
      var b = el.selectionEnd === null ? cur.length : el.selectionEnd;
      el.value = (cur.slice(0, a) + one + cur.slice(b)).slice(0, MAX_ENTRY_CHARS);
      $(el).trigger('input');
      $(el).trigger('change');
      return;
    }
    e.preventDefault();
    var r0 = +$(this).data('r');
    var c0 = +$(this).data('c');
    $.each(parsed, function (i, row) {
      $.each(row, function (j, v) {
        $matrix.find('.cell[data-r="' + (r0 + i) + '"][data-c="' + (c0 + j) + '"]').val(cleanEntry(v));
      });
    });
    layout();
    refreshGrid();
    pushHistory();
  });

  // Fraction/decimal toggle: re-renders the result and the visible page.
  $('#mode-seg').on('click', 'button', function () {
    state.mode = $(this).data('mode');
    renderResult();
    renderSteps();
  });

  // Rows per page choice: stored for persistence and reloads page 1.
  $('#size-seg').on('click', 'button', function () {
    state.limit = +$(this).data('size');
    scheduleSave();
    $('#size-seg button').each(function () {
      $(this).attr('aria-pressed', String(+$(this).data('size') === state.limit));
    });
    loadPage(1);
  });

  // Nonzero terms only switch (disabled when the server reports it unavailable).
  $('#nz-switch').on('click', function () {
    if (!state.nzAvailable) return;
    state.nz = !state.nz;
    $(this).attr('aria-checked', String(state.nz));
    loadPage(1);
  });

  // Show (sign filter) and Order (sort) buttons. Changing either reloads page 1 and selects its first row. The first sorted
  // or filtered request for a large matrix makes the server build an index, which takes a few seconds, so the loading
  // indicator stays visible meanwhile.
  function changeView(sign, order) {
    if (!state.id || (sign === state.sign && order === state.order)) return;
    state.sign = sign;
    state.order = order;
    state.sel = null;
    setJumpNote('', false);
    syncViewControls();
    loadPage(1, { selectFirst: true });
  }
  $('#sign-seg').on('click', 'button', function () { changeView($(this).data('sign'), state.order); });
  $('#order-seg').on('click', 'button', function () { changeView(state.sign, $(this).data('order')); });

  // Makes $row the selected step: updates classes and the roving tabindex, the
  // detail panel and the matrix highlight. focus is true for keyboard navigation,
  // which also scrolls the row into view.
  function selectRow($row, focus) {
    var st = state.res.steps[+$row.data('i')];
    state.sel = st;
    $('#steps-body .row.on').removeClass('on').attr('aria-pressed', 'false');
    $('#steps-body .row').attr('tabindex', '-1');
    $row.addClass('on').attr('aria-pressed', 'true').attr('tabindex', '0');
    if (focus) {
      $row[0].focus();
      if ($row[0].scrollIntoView) $row[0].scrollIntoView({ block: 'nearest' });
    }
    renderDetail();
    refreshGrid();
  }

  // Row selection by click, and keyboard navigation in the list: arrows, Home, End,
  // and PageUp/PageDown (about one visible screen, assuming 40 px rows).
  $('#steps-body').on('click', 'button.row', function () { selectRow($(this), false); });

  $('#steps-body').on('keydown', 'button.row', function (e) {
    var $rows = $('#steps-body .row');
    var i = $rows.index(this);
    var page = Math.max(1, Math.floor($('#steps-body').innerHeight() / 40) - 1);
    var next = {
      ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: $rows.length - 1,
      PageDown: i + page, PageUp: i - page
    }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    next = Math.max(0, Math.min($rows.length - 1, next));
    selectRow($rows.eq(next), true);
  });

  // Hover popup. While the pointer (or keyboard focus) is on a row of the terms
  // table, #pop shows a small picture of the calculated matrix with the entry that
  // row's permutation picks in each matrix row lit, the multiplication, and the
  // signed term. It is a convenience copy of what the detail panel and the matrix
  // highlight already show, so it is hidden from assistive technology and does
  // not take pointer events. It uses state.src (the matrix that was calculated),
  // not the grid, so it stays correct while the grid has unsaved edits.
  var popSrc = null;
  var popM = null;

  // The calculated matrix as strings, parsed once per calculation.
  function popMatrix() {
    if (popSrc !== state.src) {
      popSrc = state.src;
      try { popM = JSON.parse(state.src); } catch (e) { popM = null; }
    }
    return popM;
  }

  function hidePop() { $('#pop').prop('hidden', true); }

  // Fills and positions the popup for step st near the viewport point (x, y). The
  // card is placed 18 px down and right of the point and clamped to the window.
  // The size estimate only needs to be close: 150 px for the text lines plus the
  // matrix rows. Cell size shrinks with n so a 10 x 10 picture stays small.
  function showPop(st, x, y) {
    var m = popMatrix();
    if (!m || !st) return;
    var n = m.length;
    var cell = n <= 5 ? 44 : n <= 8 ? 32 : 26;
    var font = n <= 5 ? 13 : n <= 8 ? 11 : 10;
    var width = Math.max(260, n * cell + (n - 1) * 3 + 32);
    var height = 150 + n * (cell + 3);
    var cells = [];
    for (var i = 0; i < n; i++) {
      for (var j = 0; j < n; j++) {
        var v = $.trim(m[i][j]) || '0';
        cells.push('<span class="pc' + (st.permutation[i] - 1 === j ? ' on g' + (i % 4) : '') + '">' + esc(v) + '</span>');
      }
    }
    var tone = st.product === '0' ? 'zero' : st.sign > 0 ? 'pos' : 'neg';
    var expr = n <= 6
      ? $.map(st.factors, fac).join(' × ') + ' = ' + fmt(st.product)
      : 'Product of the ' + n + ' highlighted entries = ' + fmt(st.product);
    var html =
      '<div class="pop-head"><span class="mono">#' + st.index.toLocaleString('en-US') + '  ' + st.permutation.join(' ') + '</span>' +
      '<b class="' + tone + '">' + (st.sign > 0 ? 'plus' : 'minus') + '</b></div>' +
      '<div class="pop-grid" style="grid-template-columns:repeat(' + n + ',' + cell + 'px);--pc:' + cell + 'px;--pf:' + font + 'px">' + cells.join('') + '</div>' +
      '<div class="pop-expr mono">' + esc(expr) + '</div>' +
      '<div class="pop-term">Term for this matrix: <b class="mono ' + tone + '">' + esc(fmt(st.term)) + '</b></div>';
    var left = Math.max(8, Math.min(x + 18, window.innerWidth - width - 8));
    var top = Math.max(8, Math.min(y + 18, window.innerHeight - height - 8));
    $('#pop').html(html).css({ left: left + 'px', top: top + 'px', width: width + 'px' }).prop('hidden', false);
  }

  function popStep(el) { return state.res && state.res.steps[+$(el).data('i')]; }

  $('#steps-body')
    .on('mousemove', 'button.row', function (e) { showPop(popStep(this), e.clientX, e.clientY); })
    .on('focusin', 'button.row', function () {
      var r = this.getBoundingClientRect();
      showPop(popStep(this), r.left + r.width / 2, r.top);
    })
    .on('focusout', hidePop)
    .on('scroll', hidePop)
    .on('mouseleave', hidePop);
  $(document).on('keydown', function (e) { if (e.key === 'Escape') hidePop(); }).on('touchstart', hidePop);

  // Clicking a strip bar jumps to the page that contains the start of its range.
  $('#strip').on('click', 'button.bar', function () {
    loadPage(1, { atRank: +$(this).data('start') });
  });

  // Jump to permutation: the Go button, and Enter in the box, which accepts only
  // digits, spaces and commas.
  $('#jump-go').on('click', jumpToPermutation);
  $('#jump-perm').on('input', function () {
    var c = this.value.replace(/[^0-9 ,]/g, '');
    if (c !== this.value) this.value = c;
  }).on('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); jumpToPermutation(); }
  });

  // Value as plain ASCII for copy/paste: ASCII minus, no approximation sign, no
  // thousands separators, so spreadsheets parse it as a number.
  function plain(s) {
    return fmt(s).replace(new RegExp(MINUS, 'g'), '-').replace('≈ ', '').replace(/,/g, '');
  }

  // The visible page as tab-separated text with a header row.
  function stepsText() {
    var rows = [['index', 'permutation', 'sign', 'factors', 'product', 'term'].join('\t')];
    $.each(state.res.steps, function (_, st) {
      rows.push([st.index, st.permutation.join(' '), st.sign > 0 ? '+1' : '-1', $.map(st.factors, plain).join(' x '), plain(st.product), plain(st.term)].join('\t'));
    });
    return rows.join('\n');
  }

  // Copies text to the clipboard and returns a promise. Uses the async clipboard API
  // where available, else a temporary textarea and execCommand.
  function copyText(text) {
    var d = $.Deferred();
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(d.resolve, d.reject);
    } else {
      var ta = $('<textarea readonly>').css({ position: 'fixed', top: '-1000px' }).val(text).appendTo('body');
      ta[0].select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (e) {}
      ta.remove();
      if (ok) d.resolve(); else d.reject();
    }
    return d.promise();
  }

  // Copy this page button.
  $('#btn-copy').on('click', function () {
    if (!state.res) return;
    var count = state.res.steps.length;
    copyText(stepsText()).done(function () {
      $('#export-note').text('Copied ' + big(count) + ' rows, tab separated, ready to paste into a spreadsheet.');
    }).fail(function () {
      $('#export-note').text('Your browser blocked copying. Use Download CSV instead.');
    });
  });

  // Starts the CSV download by clicking a temporary link to the export endpoint.
  // The response is an attachment, so the page stays in place. Values are decimals
  // or exact fractions depending on the current display mode.
  function startDownload() {
    var q = $.param({ calculation_id: state.id, nonzero_only: state.nz, values: state.mode === 'dec' ? 'decimal' : 'exact' });
    var a = document.createElement('a');
    a.href = '/api/determinant/export?' + q;
    a.rel = 'noopener';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  // Download CSV button. First calls the export check endpoint so that a busy or
  // rate limited server is reported in the page instead of navigating to an error.
  // An expired calculation is recovered once, then the check is repeated.
  $('#btn-export').on('click', function () {
    if (!state.id) return;
    var rows = state.nz ? state.nzTerms : state.total;
    $('#export-note').text(rows > 500000
      ? 'Preparing ' + big(rows) + ' rows. Large files can take a minute to start finishing, and the browser shows the download when it begins.'
      : 'Downloading ' + big(rows) + ' rows.');
    var check = function () {
      $.getJSON('/api/determinant/export/check', { calculation_id: state.id }).done(startDownload).fail(function (xhr) {
        if (xhr.status === 404 && state.src && !check.retried) {
          check.retried = true;
          recover(check);
        } else {
          $('#export-note').text(errorText(xhr));
        }
      });
    };
    check();
  });

  // Updates the download button text with the row count for the current filter.
  function exportLabel() {
    var rows = state.nz ? state.nzTerms : state.total;
    $('#btn-export').text('Download CSV (' + big(rows) + ' rows)');
  }

  // Paging controls. The page box accepts a typed page, clamped to the valid range.
  $('#pg-first').on('click', function () { loadPage(1); });
  $('#pg-prev').on('click', function () { loadPage(state.page - 1); });
  $('#pg-next').on('click', function () { loadPage(state.page + 1); });
  $('#pg-last').on('click', function () { loadPage(state.res.total_pages); });
  $('#pg-input').on('change', function () {
    var p = parseInt($(this).val().replace(/[^\d]/g, ''), 10);
    if (isNaN(p)) { $(this).val(state.page); return; }
    loadPage(Math.min(Math.max(1, p), state.res.total_pages));
  });

  // Recompute matrix sizing when the window width changes.
  $(window).on('resize', layout);

  // Startup: restore saved matrix and rows-per-page (else the sample), seed the undo
  // history, render the empty teaching panel, and run an initial calculation so the
  // page opens with a result.
  var saved = loadSaved();
  if (saved) {
    state.limit = saved.limit;
    $('#size-seg button').each(function () {
      $(this).attr('aria-pressed', String(+$(this).data('size') === state.limit));
    });
    buildGrid(saved.m.length, saved.m);
  } else {
    buildGrid(6, SAMPLE);
  }
  pushHistory();
  $(window).on('pagehide', saveNow);
  renderLearn();
  calculate();
});
