(function () {
    var SPEED_PX = 42;

    function measureMarquee(wrap) {
        var track = wrap.querySelector('.app-brand-tagline-track');
        if (!track) return;

        var items = track.querySelectorAll('.app-brand-tagline-item');
        if (items.length < 2) return;

        var viewWidth = Math.round(wrap.getBoundingClientRect().width);
        if (!viewWidth) {
            requestAnimationFrame(function () { measureMarquee(wrap); });
            return;
        }
        if (wrap.dataset.marqueeViewWidth === String(viewWidth) && wrap.dataset.marqueeReady === '1') {
            return;
        }

        items[0].style.paddingRight = '0px';
        var textWidth = items[0].offsetWidth;
        items[0].style.paddingRight = viewWidth + 'px';

        var distance = textWidth + viewWidth;
        track.style.setProperty('--marquee-distance', '-' + distance + 'px');
        track.style.setProperty('--marquee-duration', (distance / SPEED_PX) + 's');
        wrap.dataset.marqueeViewWidth = String(viewWidth);
        wrap.dataset.marqueeReady = '1';
    }

    function initMarquees() {
        document.querySelectorAll('.app-brand-tagline').forEach(function (wrap) {
            measureMarquee(wrap);
            if (wrap.dataset.marqueeBound === '1') return;
            wrap.dataset.marqueeBound = '1';
            if (typeof ResizeObserver === 'function') {
                new ResizeObserver(function () { measureMarquee(wrap); }).observe(wrap);
            }
        });
    }

    document.addEventListener('DOMContentLoaded', initMarquees);
    window.addEventListener('load', initMarquees);
})();
