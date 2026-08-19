(function () {
  var previewState = {};

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  function previewUrl(table) {
    return '/portal-admin/import/' + encodeURIComponent(table) + '/preview';
  }

  function commitUrl(table) {
    return '/portal-admin/import/' + encodeURIComponent(table) + '/commit';
  }

  function purgeUrl(table) {
    return '/portal-admin/import/' + encodeURIComponent(table) + '/purge';
  }

  document.querySelectorAll('.portal-import-preview-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var table = btn.getAttribute('data-table');
      var fileInput = document.querySelector('.portal-import-file[data-table="' + table + '"]');
      var previewEl = document.getElementById('preview-' + table);
      if (!fileInput || !fileInput.files || !fileInput.files[0]) {
        alert('Choose a CSV or Excel file first.');
        return;
      }
      var fd = new FormData();
      fd.append('file', fileInput.files[0]);
      btn.disabled = true;
      btn.textContent = 'Previewing…';
      fetch(previewUrl(table), { method: 'POST', body: fd, credentials: 'same-origin' })
        .then(function (r) {
          return r.text().then(function (text) {
            var data;
            try {
              data = text ? JSON.parse(text) : {};
            } catch (parseErr) {
              if (!r.ok) {
                var snippet = (text || '').replace(/\s+/g, ' ').trim().slice(0, 180);
                throw new Error(
                  'Preview request failed (' + r.status + ').'
                  + (snippet ? ' ' + snippet : ' Restart the server if you recently updated the app.')
                );
              }
              throw parseErr;
            }
            if (!r.ok) {
              data = data || {};
              data.ok = false;
              if (!data.error) {
                data.error = 'Preview request failed (' + r.status + ').';
              }
            }
            return data;
          });
        })
        .then(function (data) {
          btn.disabled = false;
          btn.textContent = 'Preview import';
          if (!data.ok) {
            previewEl.hidden = false;
            previewEl.innerHTML = '<p class="muted">' + (data.error || 'Preview failed.') + '</p>';
            return;
          }
          previewState[table] = data.rows || [];
          var html = '<p><strong>' + data.valid_count + '</strong> valid / ' + data.preview_count + ' previewed';
          if (data.import_resolver) {
            html += ' <span class="muted">(resolver ' + data.import_resolver + ')</span>';
          }
          if (data.truncated) html += ' (file truncated to 500 rows for preview)';
          html += '</p>';
          if (data.invalid_count) {
            html += '<p class="muted">' + data.invalid_count + ' row(s) have errors.</p>';
          }
          html += '<table class="data-table"><thead><tr><th>#</th><th>Status</th><th>Details</th></tr></thead><tbody>';
          (data.rows || []).slice(0, 20).forEach(function (row) {
            html += '<tr><td>' + row.row_num + '</td><td>' + (row.valid ? 'OK' : 'Error') + '</td><td>';
            if (row.errors && row.errors.length) {
              html += row.errors.join('; ');
            } else {
              html += JSON.stringify(row.data).slice(0, 120);
            }
            html += '</td></tr>';
          });
          html += '</tbody></table>';
          if (data.valid_count > 0) {
            html += '<button type="button" class="btn-primary btn-sm portal-import-commit-btn" data-table="' + table + '" style="margin-top:0.75rem">Commit ' + data.valid_count + ' row(s)</button>';
          }
          previewEl.innerHTML = html;
          previewEl.hidden = false;
          previewEl.querySelectorAll('.portal-import-commit-btn').forEach(bindCommit);
        })
        .catch(function (err) {
          btn.disabled = false;
          btn.textContent = 'Preview import';
          previewEl.hidden = false;
          previewEl.innerHTML = '<p class="muted">' + (err && err.message ? err.message : 'Network error during preview.') + '</p>';
        });
    });
  });

  function bindCommit(btn) {
    btn.addEventListener('click', function () {
      var table = btn.getAttribute('data-table');
      var rows = previewState[table] || [];
      var validRows = rows.filter(function (r) { return r.valid; });
      if (!validRows.length) {
        alert('No valid rows to import.');
        return;
      }
      if (!confirm('Import ' + validRows.length + ' row(s) into ' + table + '?')) return;
      btn.disabled = true;
      fetch(commitUrl(table), {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rows: validRows }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          btn.disabled = false;
          if (data.ok) {
            alert('Imported ' + data.inserted + ' row(s).');
            window.location.reload();
          } else {
            alert(data.error || 'Import failed.');
          }
        })
        .catch(function () {
          btn.disabled = false;
          alert('Network error during import.');
        });
    });
  }

  var purgeModal = document.getElementById('portal-purge-modal');
  var purgePhrase = document.getElementById('portal-purge-phrase');
  var purgeInput = document.getElementById('portal-purge-input');
  var purgeTable = null;

  document.querySelectorAll('.portal-import-purge-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      purgeTable = btn.getAttribute('data-table');
      var phrase = 'PURGE ' + purgeTable.toUpperCase();
      purgePhrase.textContent = phrase;
      purgeInput.value = '';
      purgeModal.hidden = false;
    });
  });

  var cancelBtn = document.getElementById('portal-purge-cancel');
  if (cancelBtn) {
    cancelBtn.addEventListener('click', function () {
      purgeModal.hidden = true;
      purgeTable = null;
    });
  }

  var confirmBtn = document.getElementById('portal-purge-confirm');
  if (confirmBtn) {
    confirmBtn.addEventListener('click', function () {
      if (!purgeTable) return;
      confirmBtn.disabled = true;
      fetch(purgeUrl(purgeTable), {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm_text: purgeInput.value }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          confirmBtn.disabled = false;
          purgeModal.hidden = true;
          if (data.ok) {
            alert('Deleted ' + data.deleted + ' row(s) from ' + data.table + '.');
            window.location.reload();
          } else {
            alert(data.error || 'Purge failed.');
          }
        })
        .catch(function () {
          confirmBtn.disabled = false;
          alert('Network error during purge.');
        });
    });
  }
})();
