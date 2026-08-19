document.addEventListener('DOMContentLoaded', function () {
  var modal = document.getElementById('generateDtrModal');
  var monthEl = document.getElementById('dtrGenMonth');
  var yearEl = document.getElementById('dtrGenYear');
  if (monthEl && yearEl) {
    var now = new Date();
    monthEl.value = String(now.getMonth() + 1);
    yearEl.value = String(now.getFullYear());
    var quincenaElInit = document.getElementById('dtrGenQuincena');
    if (quincenaElInit) {
      quincenaElInit.value = now.getDate() <= 15 ? '1' : '2';
    }
  }
  if (modal) {
    modal.addEventListener('click', function (e) {
      if (e.target === modal) modal.style.display = 'none';
    });
  }

  var holidayCalHost = document.getElementById('dtrHolidayCalHost');
  var holidayTa = document.getElementById('dtrHolidayDatesText');
  var suspensionTa = document.getElementById('dtrSuspensionDatesText');
  var holidayAddSelectedBtn = document.getElementById('dtrHolidayAddSelectedBtn');
  var suspensionAddSelectedBtn = document.getElementById('dtrSuspensionAddSelectedBtn');
  var holidayClearPickBtn = document.getElementById('dtrHolidayClearPickBtn');
  var quincenaEl = document.getElementById('dtrGenQuincena');
  var holidayPicked = new Set();

  function dtrPad2(n) {
    return String(n).padStart(2, '0');
  }
  function dtrLastDayOfMonth(year, month1to12) {
    return new Date(year, month1to12, 0).getDate();
  }
  function dtrYmd(year, month1to12, day) {
    return year + '-' + dtrPad2(month1to12) + '-' + dtrPad2(day);
  }
  function dtrHolidayPeriodBounds() {
    var y = parseInt(yearEl && yearEl.value, 10);
    var m = parseInt(monthEl && monthEl.value, 10);
    if (!y || !m || m < 1 || m > 12) return null;
    var last = dtrLastDayOfMonth(y, m);
    var q = (quincenaEl && quincenaEl.value) || '1';
    if (q === '2') {
      return { min: dtrYmd(y, m, 16), max: dtrYmd(y, m, last) };
    }
    return { min: dtrYmd(y, m, 1), max: dtrYmd(y, m, 15) };
  }
  function dtrTokensFromTextarea(ta) {
    if (!ta || !ta.value) return [];
    return ta.value
      .split(/[\n,;]+/)
      .map(function (s) { return s.trim(); })
      .filter(Boolean);
  }
  function dtrAllSpecialDayTokens() {
    return dtrTokensFromTextarea(holidayTa).concat(dtrTokensFromTextarea(suspensionTa));
  }
  function dtrTokenDatePart(token) {
    var m = token.match(/^(\d{4}-\d{2}-\d{2}|\d{2}\/\d{2}\/\d{4})/);
    return m ? m[1] : token;
  }
  function dtrSelectedHalfDay() {
    var el = document.querySelector('input[name="dtrSpecialDayHalf"]:checked');
    return el ? (el.value || '') : '';
  }
  function dtrFormatSpecialDayToken(ymd, half) {
    return half ? (ymd + ' ' + half) : ymd;
  }
  function dtrListedDatesInPeriod() {
    var b = dtrHolidayPeriodBounds();
    var listed = new Set();
    if (!b) return listed;
    dtrAllSpecialDayTokens().forEach(function (t) {
      var d = dtrTokenDatePart(t);
      if (d >= b.min && d <= b.max) listed.add(d);
    });
    return listed;
  }
  function dtrAppendTokenToTextarea(ta, token) {
    if (!ta) return false;
    var existing = dtrTokensFromTextarea(ta);
    if (existing.indexOf(token) !== -1) return false;
    ta.value = ta.value.replace(/\s+$/, '');
    if (ta.value.length) ta.value += '\n';
    ta.value += token;
    return true;
  }
  function renderHolidayMiniCal() {
    if (!holidayCalHost) return;
    var y = parseInt(yearEl && yearEl.value, 10);
    var m = parseInt(monthEl && monthEl.value, 10);
    var b = dtrHolidayPeriodBounds();
    if (!y || !m || m < 1 || m > 12 || !b) {
      holidayCalHost.innerHTML = '';
      return;
    }
    var last = dtrLastDayOfMonth(y, m);
    var firstDow = new Date(y, m - 1, 1).getDay();
    var listed = dtrListedDatesInPeriod();
    holidayPicked.forEach(function (ymd) {
      if (ymd < b.min || ymd > b.max) holidayPicked.delete(ymd);
    });

    var dow = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    var html = '<table class="generate-dtr-holiday-cal-table" role="grid" aria-label="Holiday and work suspension days for selected quincena">';
    html += '<thead><tr>';
    for (var h = 0; h < 7; h++) html += '<th scope="col">' + dow[h] + '</th>';
    html += '</tr></thead><tbody>';
    var day = 1;
    var row = 0;
    while (day <= last) {
      html += '<tr>';
      for (var c = 0; c < 7; c++) {
        if (row === 0 && c < firstDow) {
          html += '<td class="generate-dtr-holiday-cal-empty"></td>';
        } else if (day > last) {
          html += '<td class="generate-dtr-holiday-cal-empty"></td>';
        } else {
          var ymd = dtrYmd(y, m, day);
          var inRange = ymd >= b.min && ymd <= b.max;
          var isListed = listed.has(ymd);
          var cls = 'generate-dtr-holiday-cal-day';
          if (!inRange) cls += ' generate-dtr-holiday-cal-out';
          if (isListed) cls += ' generate-dtr-holiday-cal-listed';
          if (holidayPicked.has(ymd)) cls += ' generate-dtr-holiday-cal-picked';
          var label = 'Day ' + day + ', ' + ymd;
          if (inRange) {
            html += '<td><button type="button" class="' + cls + '" data-ymd="' + ymd + '" aria-pressed="' +
              (holidayPicked.has(ymd) ? 'true' : 'false') + '" aria-label="' + label + (isListed ? ', already in a list' : '') + '">' + day + '</button></td>';
          } else {
            html += '<td><span class="' + cls + '" aria-hidden="true">' + day + '</span></td>';
          }
          day++;
        }
      }
      html += '</tr>';
      row++;
    }
    html += '</tbody></table>';
    holidayCalHost.innerHTML = html;
    holidayCalHost.querySelectorAll('button.generate-dtr-holiday-cal-day').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var ymd = this.getAttribute('data-ymd');
        if (!ymd) return;
        if (holidayPicked.has(ymd)) holidayPicked.delete(ymd);
        else holidayPicked.add(ymd);
        this.classList.toggle('generate-dtr-holiday-cal-picked', holidayPicked.has(ymd));
        this.setAttribute('aria-pressed', holidayPicked.has(ymd) ? 'true' : 'false');
      });
    });
  }
  function addPickedToTextarea(ta) {
    if (!ta || !holidayPicked.size) return;
    var half = dtrSelectedHalfDay();
    var sorted = Array.from(holidayPicked).sort();
    sorted.forEach(function (ymd) {
      dtrAppendTokenToTextarea(ta, dtrFormatSpecialDayToken(ymd, half));
    });
    holidayPicked.clear();
    renderHolidayMiniCal();
  }

  if (holidayCalHost && holidayTa && holidayAddSelectedBtn) {
    holidayAddSelectedBtn.addEventListener('click', function () { addPickedToTextarea(holidayTa); });
    if (suspensionAddSelectedBtn && suspensionTa) {
      suspensionAddSelectedBtn.addEventListener('click', function () { addPickedToTextarea(suspensionTa); });
    }
    if (holidayClearPickBtn) {
      holidayClearPickBtn.addEventListener('click', function () {
        holidayPicked.clear();
        renderHolidayMiniCal();
      });
    }
    [monthEl, yearEl, quincenaEl].forEach(function (el) {
      if (el) el.addEventListener('change', renderHolidayMiniCal);
    });
    if (yearEl) yearEl.addEventListener('input', renderHolidayMiniCal);
    holidayTa.addEventListener('input', renderHolidayMiniCal);
    if (suspensionTa) suspensionTa.addEventListener('input', renderHolidayMiniCal);
    renderHolidayMiniCal();
  }

  var wsList = document.getElementById('dtrWorkweekSegmentsList');
  var wsAddBtn = document.getElementById('dtrAddWorkweekSegment');
  var wsJsonEl = document.getElementById('dtrWorkweekSegmentsJson');
  var wsTemplate = document.getElementById('dtrWorkweekSegmentTemplate');

  function dtrAppliesGroupsOverlap(a, b) {
    a = (a || 'all').toLowerCase();
    b = (b || 'all').toLowerCase();
    if (a === 'all' || b === 'all') return true;
    return a === b;
  }
  function dtrRangesOverlap(s1, e1, s2, e2) {
    return s1 <= e2 && s2 <= e1;
  }
  function dtrWorkweekSegmentRows() {
    if (!wsList) return [];
    return Array.prototype.slice.call(wsList.querySelectorAll('.generate-dtr-workweek-segment'));
  }
  function dtrReadWorkweekSegmentsFromDom() {
    return dtrWorkweekSegmentRows().map(function (row) {
      return {
        model: (row.querySelector('.dtr-ws-model') || {}).value || '',
        applies_to: (row.querySelector('.dtr-ws-applies') || {}).value || 'all',
        start_date: (row.querySelector('.dtr-ws-start') || {}).value || '',
        end_date: (row.querySelector('.dtr-ws-end') || {}).value || ''
      };
    }).filter(function (s) { return s.model; });
  }
  function dtrSyncWorkweekSegmentsJson() {
    if (!wsJsonEl) return;
    wsJsonEl.value = JSON.stringify(dtrReadWorkweekSegmentsFromDom());
  }
  function dtrValidateWorkweekSegments() {
    var segs = dtrReadWorkweekSegmentsFromDom();
    if (!segs.length) return null;
    var b = dtrHolidayPeriodBounds();
    if (!b) return 'Select a valid period before adding workweek segments.';
    for (var i = 0; i < segs.length; i++) {
      var s = segs[i];
      if (!s.start_date || !s.end_date) {
        return 'Workweek segment ' + (i + 1) + ': start and end dates are required.';
      }
      if (s.end_date < s.start_date) {
        return 'Workweek segment ' + (i + 1) + ': end date must be on or after start date.';
      }
      if (s.start_date < b.min || s.end_date > b.max) {
        return 'Workweek segment ' + (i + 1) + ' must fall within ' + b.min + ' to ' + b.max + '.';
      }
      for (var j = i + 1; j < segs.length; j++) {
        var o = segs[j];
        if (dtrAppliesGroupsOverlap(s.applies_to, o.applies_to) &&
            dtrRangesOverlap(s.start_date, s.end_date, o.start_date, o.end_date)) {
          return 'Workweek segments ' + (i + 1) + ' and ' + (j + 1) + ' overlap for the same employee group.';
        }
      }
    }
    return null;
  }
  function dtrRefreshWorkweekSegmentLabels() {
    dtrWorkweekSegmentRows().forEach(function (row, idx) {
      var title = row.querySelector('.generate-dtr-workweek-segment-title');
      if (title) title.textContent = 'Segment ' + (idx + 1);
      row.setAttribute('data-segment-index', String(idx));
    });
  }
  function dtrApplyWorkweekPeriodBoundsToRow(row) {
    var b = dtrHolidayPeriodBounds();
    if (!b || !row) return;
    var startEl = row.querySelector('.dtr-ws-start');
    var endEl = row.querySelector('.dtr-ws-end');
    if (startEl) {
      startEl.min = b.min;
      startEl.max = b.max;
      if (!startEl.value) startEl.value = b.min;
    }
    if (endEl) {
      endEl.min = b.min;
      endEl.max = b.max;
      if (!endEl.value) endEl.value = b.max;
    }
  }
  function dtrApplyWorkweekPeriodBoundsAll() {
    dtrWorkweekSegmentRows().forEach(dtrApplyWorkweekPeriodBoundsToRow);
  }
  function dtrBindWorkweekSegmentRow(row) {
    if (!row) return;
    var removeBtn = row.querySelector('.generate-dtr-workweek-segment-remove');
    if (removeBtn) {
      removeBtn.addEventListener('click', function () {
        row.remove();
        dtrRefreshWorkweekSegmentLabels();
        dtrSyncWorkweekSegmentsJson();
      });
    }
    row.querySelectorAll('.dtr-ws-model, .dtr-ws-applies, .dtr-ws-start, .dtr-ws-end').forEach(function (el) {
      el.addEventListener('change', dtrSyncWorkweekSegmentsJson);
      el.addEventListener('input', dtrSyncWorkweekSegmentsJson);
    });
  }
  function dtrAddWorkweekSegment() {
    if (!wsList || !wsTemplate || !wsTemplate.content) return;
    var row = wsTemplate.content.firstElementChild.cloneNode(true);
    dtrApplyWorkweekPeriodBoundsToRow(row);
    dtrBindWorkweekSegmentRow(row);
    wsList.appendChild(row);
    dtrRefreshWorkweekSegmentLabels();
    dtrSyncWorkweekSegmentsJson();
  }
  if (wsAddBtn) {
    wsAddBtn.addEventListener('click', dtrAddWorkweekSegment);
  }
  [monthEl, yearEl, quincenaEl].forEach(function (el) {
    if (el) el.addEventListener('change', dtrApplyWorkweekPeriodBoundsAll);
  });
  if (yearEl) yearEl.addEventListener('input', dtrApplyWorkweekPeriodBoundsAll);

  var genForm = document.getElementById('generateDtrForm');
  var genOverlay = document.getElementById('dtr-generate-status');
  if (genForm && genOverlay) {
    function showGenOverlay() {
      genOverlay.classList.add('is-visible');
      genOverlay.setAttribute('aria-hidden', 'false');
    }
    function hideGenOverlay() {
      genOverlay.classList.remove('is-visible');
      genOverlay.setAttribute('aria-hidden', 'true');
    }
    genForm.addEventListener('submit', function (e) {
      e.preventDefault();
      if (typeof dtrSyncWorkweekSegmentsJson === 'function') {
        var wsErr = dtrValidateWorkweekSegments();
        if (wsErr) {
          alert(wsErr);
          return;
        }
        dtrSyncWorkweekSegmentsJson();
      }
      var submitBtn = genForm.querySelector('button[type="submit"]');
      if (submitBtn) submitBtn.disabled = true;
      showGenOverlay();
      fetch(genForm.action, {
        method: 'POST',
        body: new FormData(genForm),
        credentials: 'same-origin',
        redirect: 'follow'
      })
        .then(function (res) {
          if (res.ok) {
            window.location.href = res.url || genForm.getAttribute('data-dashboard-url') || '/';
            return;
          }
          hideGenOverlay();
          if (submitBtn) submitBtn.disabled = false;
          alert('DTR generation could not be completed. Please check your inputs and try again.');
        })
        .catch(function (err) {
          hideGenOverlay();
          if (submitBtn) submitBtn.disabled = false;
          alert('Network error. Please try again.');
        });
    });
  }
});
