document.addEventListener('DOMContentLoaded', function () {
    var menuToggle = document.getElementById('menuToggle');
    var sidebar = document.getElementById('sidebar');
    var body = document.body;
    var desktopMq = window.matchMedia('(min-width: 992px)');

    function isDesktop() {
        return desktopMq.matches;
    }

    function closeSidebar() {
        if (!sidebar) return;
        sidebar.classList.remove('visible');
        body.classList.remove('no-scroll', 'sidebar-open');
    }

    function openSidebar() {
        if (!sidebar) return;
        sidebar.classList.add('visible');
        body.classList.add('no-scroll', 'sidebar-open');
    }

    if (menuToggle && sidebar) {
        menuToggle.addEventListener('click', function (e) {
            e.stopPropagation();
            if (isDesktop()) return;
            if (sidebar.classList.contains('visible')) closeSidebar();
            else openSidebar();
        });

        document.addEventListener('click', function (e) {
            if (isDesktop()) return;
            if (!sidebar.classList.contains('visible')) return;
            if (!sidebar.contains(e.target) && !menuToggle.contains(e.target)) {
                closeSidebar();
            }
        });

        desktopMq.addEventListener('change', function () {
            if (isDesktop()) closeSidebar();
        });
    }

    document.querySelectorAll('.sidebar-section-toggle').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var targetId = btn.getAttribute('data-target');
            var sub = targetId ? document.getElementById(targetId) : null;
            if (!sub) return;
            var willOpen = sub.classList.contains('is-collapsed');
            sub.classList.toggle('is-collapsed', !willOpen);
            btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
            var chevron = btn.querySelector('.sidebar-chevron');
            if (chevron) chevron.textContent = willOpen ? '▴' : '▾';
        });
    });
});
