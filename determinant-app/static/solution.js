/* Solution page. Loads the matrix last calculated on the main page, asks the server for its result (answered from the
   server's cache when the matrix is still stored) and writes the determinant out as a four step solution:
   1 the formula, 2 every nonzero term with its sign, 3 the simplified signs, 4 the positive and negative terms added up.
   Up to FULL_LIMIT nonzero terms are written out in full, fetched in pages of PAGE. A matrix with more nonzero terms is
   shown in windows of WIN terms (all permutations in order) with a subtotal per window, because a text with millions of
   terms cannot be shown on one page. Values are exact strings from the server, formatted here with BigInt like script.js. */
$(function () {
  'use strict';

  var MINUS = '−';
  var SOLUTION_KEY = 'determinant.solution.v1';
  var MAIN_KEY = 'determinant.v1';
  var FULL_LIMIT = 50000;
  var PAGE = 1000;
  var WIN = 24;
  var BIG_JUMP = 50;
  var RENDER_CHUNK = 1500;

  // info: reply of POST /api/determinant. items: terms of the full expansion or of the current window. mode: 'frac' or
  // 'dec'. kind: 'full', 'window' or 'zero'. winPage: current 1-based window. winData: last steps reply in window mode.
  // renderToken lets a newer render cancel an older chunked one.
  var state = { matrix: null, info: null, items: [], mode: 'frac', kind: null, winPage: 1, winData: null, renderToken: 0 };

  // --- number formatting (same rules as script.js) ---
  function big(x) { return x.toLocaleString('en-US'); }

  function gcd(a, b) {
    if (a < 0n) a = -a;
    if (b < 0n) b = -b;
    while (b) { var t = a % b; a = b; b = t; }
    return a;
  }

  function parseRat(s) {
    var i = s.indexOf('/');
    return i < 0 ? { n: BigInt(s), d: 1n } : { n: BigInt(s.slice(0, i)), d: BigInt(s.slice(i + 1)) };
  }

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

  function fmt(s) {
    var r = parseRat(s);
    return fmtRat(r.n, r.d, state.mode);
  }

  // Formats the absolute value of a server string, used when the sign is written as an operator.
  function fmtAbs(s) {
    var r = parseRat(s);
    return fmtRat(r.n < 0n ? -r.n : r.n, r.d, state.mode);
  }

  // Adds the rational p/q string to the accumulator {n, d}, keeping it in lowest terms.
  function addTo(acc, r) {
    acc.n = acc.n * r.d + r.n * acc.d;
    acc.d = acc.d * r.d;
    var g = gcd(acc.n, acc.d);
    if (g > 1n) { acc.n /= g; acc.d /= g; }
  }

  function setStatus(msg) { $('#sol-status').text(msg).prop('hidden', !msg); }

  function showError(msg) { $('#sol-error').text(msg).prop('hidden', !msg); }

  function errorText(xhr) {
    var body = xhr.responseJSON;
    if (body && typeof body.detail === 'string') return body.detail;
    if (xhr.status === 429) return 'Too many calculations right now. Wait a moment and reload.';
    return 'The server could not be reached. Try again.';
  }

  // --- loading the matrix ---

  // Returns the stored matrix as an array of rows of strings, or null. The solution key is written when a
  // calculation succeeds; the main page's own saved grid is the fallback.
  function loadMatrix() {
    var keys = [SOLUTION_KEY, MAIN_KEY];
    for (var k = 0; k < keys.length; k++) {
      try {
        var raw = window.localStorage.getItem(keys[k]);
        if (!raw) continue;
        var m = JSON.parse(raw).m;
        var n = $.isArray(m) ? m.length : 0;
        if (n < 1 || n > 10) continue;
        var ok = true;
        for (var i = 0; i < n && ok; i++) {
          if (!$.isArray(m[i]) || m[i].length !== n) { ok = false; break; }
          for (var j = 0; j < n; j++) {
            if (typeof m[i][j] !== 'string' || m[i][j].length > 14) { ok = false; break; }
          }
        }
        if (ok) return m;
      } catch (e) { /* storage may be blocked or hold something else */ }
    }
    return null;
  }

  function init() {
    state.matrix = loadMatrix();
    if (!state.matrix) {
      $('#sol-empty').prop('hidden', false);
      return;
    }
    setStatus('Preparing the solution. A large matrix can take a few seconds.');
    $.ajax({
      url: '/api/determinant',
      method: 'POST',
      contentType: 'application/json',
      data: JSON.stringify({ matrix: state.matrix })
    }).done(function (res) {
      state.info = res;
      state.mode = res.has_decimals ? 'dec' : 'frac';
      $('#sol-mode').prop('hidden', !res.has_fractions);
      buildPage();
    }).fail(function (xhr) {
      setStatus('');
      showError(errorText(xhr));
    });
  }

  // --- page ---

  // Fills the parts that do not depend on the terms, then picks full, window or zero mode.
  function buildPage() {
    var r = state.info;
    $('#sol-chips').html($.map([
      r.n + ' × ' + r.n,
      big(r.total_permutations) + ' permutations',
      big(r.nonzero_terms) + ' nonzero terms',
      r.cross_check_passed ? 'Permutation sum matches elimination' : 'Permutation sum differs from elimination'
    ], function (c) { return '<span class="chip">' + c + '</span>'; }).join(''));
    $('#sol-formula').text('det(A) = Σ sign(σ) · a(1,σ1) · a(2,σ2) · … · a(' + r.n + ',σ' + r.n + '), over all ' + big(r.total_permutations) + ' permutations σ');
    $('#sol-steps').prop('hidden', false);
    $('#sol-det').text('det(A) = ' + fmt(r.determinant));
    if (r.nonzero_terms === 0) {
      state.kind = 'zero';
    } else if (r.nonzero_terms <= FULL_LIMIT) {
      state.kind = 'full';
    } else {
      state.kind = 'window';
    }
    loadTerms();
  }

  // Fetches what the current mode needs: every nonzero term page by page, one window, or nothing for the zero case.
  function loadTerms() {
    if (state.kind === 'zero') {
      setStatus('');
      $('#sol-title2').text('No nonzero terms');
      $('#sol-zero').text('Every one of the ' + big(state.info.total_permutations) + ' terms has a zero factor, so every term is 0 and nothing is left to write out.').prop('hidden', false);
      $('#sol-line1, #sol-line2').empty();
      $('#sol-sums').empty();
      $('#sol-eq').text('det(A) = ' + fmt(state.info.determinant));
      return;
    }
    if (state.kind === 'full') {
      state.items = [];
      fetchFull(1);
    } else {
      fetchWindow();
    }
  }

  function stepsUrl(page, limit, nonzero) {
    return '/api/determinant/steps?calculation_id=' + encodeURIComponent(state.info.calculation_id) +
      '&page=' + page + '&limit=' + limit + '&nonzero_only=' + (nonzero ? 'true' : 'false');
  }

  // The server forgets results after a while; resubmit the matrix once and retry with the new id.
  function withRecovery(request, retry) {
    request.fail(function (xhr) {
      if (xhr.status === 404) {
        $.ajax({ url: '/api/determinant', method: 'POST', contentType: 'application/json', data: JSON.stringify({ matrix: state.matrix }) })
          .done(function (res) { state.info = res; retry(); })
          .fail(function (x2) { setStatus(''); showError(errorText(x2)); });
      } else {
        setStatus('');
        showError(errorText(xhr));
      }
    });
  }

  function fetchFull(page) {
    var pages = Math.ceil(state.info.nonzero_terms / PAGE);
    setStatus('Collecting the nonzero terms: ' + big(state.items.length) + ' of ' + big(state.info.nonzero_terms));
    withRecovery($.getJSON(stepsUrl(page, PAGE, true)).done(function (res) {
      $.each(res.steps, function (i, s) { state.items.push({ sign: s.sign, product: s.product, term: s.term }); });
      if (page < pages) {
        fetchFull(page + 1);
      } else {
        $('#sol-title2').text(big(state.items.length) + ' nonzero terms of ' + big(state.info.total_permutations) + ' permutations');
        renderTerms();
      }
    }), function () { fetchFull(page); });
  }

  function fetchWindow() {
    var pages = Math.ceil(state.info.total_permutations / WIN);
    state.winPage = Math.min(Math.max(1, state.winPage), pages);
    setStatus('Loading terms…');
    withRecovery($.getJSON(stepsUrl(state.winPage, WIN, false)).done(function (res) {
      state.winData = res;
      state.items = $.map(res.steps, function (s) { return { sign: s.sign, product: s.product, term: s.term }; });
      var from = (res.page - 1) * WIN + 1;
      $('#sol-title2').text('Terms ' + big(from) + ' to ' + big(from + res.steps.length - 1) + ' of ' + big(res.total_steps) + ', in lexicographic order');
      $('#sol-win-note').text('With ' + big(res.total_steps) + ' terms the sum cannot be written on one page, so it is shown ' + WIN + ' terms at a time. Each window has its own subtotal, and the ' + big(res.total_pages) + ' subtotals add up to det(A).');
      $('#sol-win').prop('hidden', false);
      $('#win-first, #win-back-big, #win-prev').prop('disabled', res.page <= 1);
      $('#win-next, #win-fwd-big, #win-last').prop('disabled', res.page >= res.total_pages);
      renderTerms();
    }), fetchWindow);
  }

  // Draws steps 2 to 4 from state.items in chunks so a very long expansion does not freeze the page. A newer call
  // (for example a format change) cancels the chunks of an older one.
  function renderTerms() {
    var token = ++state.renderToken;
    var items = state.items;
    var $l1 = $('#sol-line1').empty();
    var $l2 = $('#sol-line2').empty();
    var lead = '<span class="sol-lead">det(A) =</span> ';
    $l1.append(lead);
    $l2.append(lead);
    $('#sol-sums').empty();
    $('#sol-eq').empty();
    var big1 = items.length > 400;
    $('#sol-line1, #sol-line2').toggleClass('sol-small', big1).toggleClass('sol-left', items.length > 24);
    var pos = { n: 0n, d: 1n };
    var neg = { n: 0n, d: 1n };
    var at = 0;

    function chunk() {
      if (token !== state.renderToken) return;
      var end = Math.min(items.length, at + RENDER_CHUNK);
      var f1 = document.createDocumentFragment();
      var f2 = document.createDocumentFragment();
      for (; at < end; at++) {
        var it = items[at];
        var t = parseRat(it.term);
        var cls = t.n < 0n ? 'neg' : (t.n === 0n ? 'zero' : 'pos');
        var p = parseRat(it.product);
        var body1 = '(' + (p.n < 0n ? '' : '+') + fmt(it.product) + ')';
        var op1 = at === 0 ? (it.sign < 0 ? MINUS : '') : (it.sign < 0 ? MINUS : '+');
        var op2 = at === 0 ? (t.n < 0n ? MINUS : '') : (t.n < 0n ? MINUS : '+');
        f1.appendChild(termNode(cls, op1, body1));
        f2.appendChild(termNode(cls, op2, fmtAbs(it.term)));
        if (t.n < 0n) addTo(neg, { n: -t.n, d: t.d }); else addTo(pos, t);
      }
      $l1[0].appendChild(f1);
      $l2[0].appendChild(f2);
      if (at < items.length) {
        setStatus('Writing the terms: ' + big(at) + ' of ' + big(items.length));
        window.requestAnimationFrame(chunk);
      } else {
        setStatus('');
        renderSums(pos, neg);
      }
    }
    chunk();
  }

  function termNode(cls, op, body) {
    var span = document.createElement('span');
    span.className = 'sol-term ' + cls;
    var o = document.createElement('span');
    o.className = 'sol-op';
    o.textContent = op;
    span.appendChild(o);
    span.appendChild(document.createTextNode(body));
    var frag = document.createDocumentFragment();
    frag.appendChild(span);
    frag.appendChild(document.createTextNode(' '));
    return frag;
  }

  // Step 4 once the terms are written. Full mode adds the positive and negative terms; window mode shows the window's
  // subtotal (from the server) and how many windows make up det(A).
  function renderSums(pos, neg) {
    var det = fmt(state.info.determinant);
    if (state.kind === 'full') {
      $('#sol-sums').html(
        sumBox('Positive terms together', fmtRat(pos.n, pos.d, state.mode), 'pos') +
        sumBox('Negative terms together', fmtRat(neg.n, neg.d, state.mode), 'neg'));
      $('#sol-eq').html('det(A) = ' + esc(fmtRat(pos.n, pos.d, state.mode)) + ' ' + MINUS + ' ' + esc(fmtRat(neg.n, neg.d, state.mode)) + ' = <strong>' + esc(det) + '</strong>');
    } else {
      var d = state.winData;
      var sub = parseRat(d.page_subtotal);
      $('#sol-sums').html(
        sumBox('Subtotal of this window', fmt(d.page_subtotal), sub.n < 0n ? 'neg' : 'pos') +
        sumBox('Windows in total', big(d.total_pages), ''));
      $('#sol-eq').html('det(A) = subtotal 1 + subtotal 2 + … + subtotal ' + esc(big(d.total_pages)) + ' = <strong>' + esc(det) + '</strong>');
    }
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; });
  }

  function sumBox(label, value, cls) {
    return '<div class="sol-sum"><div class="note">' + esc(label) + '</div><div class="mono ' + cls + '">' + esc(value) + '</div></div>';
  }

  // --- events ---

  function gotoWindow(page) {
    state.winPage = page;
    fetchWindow();
  }

  $('#sol-mode').on('click', 'button', function () {
    state.mode = $(this).data('mode');
    $('#sol-mode button').each(function () { $(this).attr('aria-pressed', String($(this).data('mode') === state.mode)); });
    $('#sol-det').text('det(A) = ' + fmt(state.info.determinant));
    if (state.kind !== 'zero') renderTerms();
    else $('#sol-eq').text('det(A) = ' + fmt(state.info.determinant));
  });

  $('#win-first').on('click', function () { gotoWindow(1); });
  $('#win-prev').on('click', function () { gotoWindow(state.winPage - 1); });
  $('#win-next').on('click', function () { gotoWindow(state.winPage + 1); });
  $('#win-last').on('click', function () { gotoWindow(Math.ceil(state.info.total_permutations / WIN)); });
  $('#win-back-big').on('click', function () { gotoWindow(state.winPage - BIG_JUMP); });
  $('#win-fwd-big').on('click', function () { gotoWindow(state.winPage + BIG_JUMP); });
  $('#win-goto').on('input', function () {
    var clean = this.value.replace(/[^0-9]/g, '');
    if (clean !== this.value) this.value = clean;
  }).on('change', function () {
    var v = parseInt(this.value, 10);
    if (v >= 1) gotoWindow(Math.ceil(Math.min(v, state.info.total_permutations) / WIN));
  });

  init();
});
