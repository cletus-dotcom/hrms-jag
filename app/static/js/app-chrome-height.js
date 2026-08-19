/**
 * Syncs --app-chrome-offset to the measured topbar height so main content
 * never slides under a wrapped / multi-row header on mobile.
 */
(function () {
    function syncChromeOffset() {
        var topbar = document.querySelector('.app-topbar');
        if (!topbar) return;
        var h = Math.ceil(topbar.getBoundingClientRect().height);
        if (h > 0) {
            document.documentElement.style.setProperty('--app-chrome-offset', h + 'px');
            document.documentElement.style.setProperty('--app-topbar-height', h + 'px');
        }
    }

    function init() {
        syncChromeOffset();
        window.addEventListener('resize', syncChromeOffset);
        if (typeof ResizeObserver !== 'undefined') {
            var topbar = document.querySelector('.app-topbar');
            if (topbar) {
                var ro = new ResizeObserver(syncChromeOffset);
                ro.observe(topbar);
            }
        }
        document.fonts && document.fonts.ready && document.fonts.ready.then(syncChromeOffset);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
