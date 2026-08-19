/**
 * Wraps tables inside app content for horizontal scroll on narrow viewports.
 */
(function () {
    function needsWrap(table) {
        if (!table || table.closest('.table-container, .table-scroll-container, .table-responsive-wrap')) {
            return false;
        }
        if (table.closest('.jo-cos-payslip-sheet, .payslip-table, .change-password-modal-dialog')) {
            return false;
        }
        return Boolean(table.closest('.app-main-content, .app-page'));
    }

    function wrapTables() {
        document.querySelectorAll('.app-main-content table, .app-page table').forEach(function (table) {
            if (!needsWrap(table)) return;
            var wrap = document.createElement('div');
            wrap.className = 'table-responsive-wrap';
            wrap.setAttribute('tabindex', '0');
            wrap.setAttribute('role', 'region');
            wrap.setAttribute('aria-label', 'Scrollable table');
            table.parentNode.insertBefore(wrap, table);
            wrap.appendChild(table);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', wrapTables);
    } else {
        wrapTables();
    }
})();
