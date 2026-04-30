// Main JavaScript file for HRMS

document.addEventListener('DOMContentLoaded', function() {
    const headerBar = document.querySelector('.header-bar');
    const landingTopnav = document.querySelector('.landing-topnav');

    function attachAutoHideOnScroll(el, hiddenClass, opts) {
        if (!el) return;
        const hideDelayMs = (opts && opts.hideDelayMs) || 260;
        const minScrollDiff = (opts && opts.minScrollDiff) || 8;
        const keepVisibleUntilY = (opts && opts.keepVisibleUntilY) || 20;

        let lastScrollY = window.scrollY || window.pageYOffset;
        let hideTimeout = null;

        window.addEventListener('scroll', function () {
            const y = window.scrollY || window.pageYOffset;
            if (y < keepVisibleUntilY) {
                if (hideTimeout) {
                    clearTimeout(hideTimeout);
                    hideTimeout = null;
                }
                el.classList.remove(hiddenClass);
                lastScrollY = y;
                return;
            }

            const diff = y - lastScrollY;
            if (Math.abs(diff) < minScrollDiff) return;

            if (diff > 0) {
                if (hideTimeout) clearTimeout(hideTimeout);
                hideTimeout = window.setTimeout(function () {
                    el.classList.add(hiddenClass);
                    hideTimeout = null;
                }, hideDelayMs);
            } else {
                if (hideTimeout) {
                    clearTimeout(hideTimeout);
                    hideTimeout = null;
                }
                el.classList.remove(hiddenClass);
            }

            lastScrollY = y;
        }, { passive: true });
    }

    function isPublicPage() {
        return Boolean(document.querySelector('.landing-page, .public-login'));
    }

    function titleize(seg) {
        if (!seg) return '';
        const cleaned = seg.replace(/[-_]+/g, ' ').trim();
        if (!cleaned) return '';
        return cleaned.replace(/\b\w/g, c => c.toUpperCase());
    }

    function buildBreadcrumbs() {
        if (!headerBar || isPublicPage()) return;
        const userDropdown = headerBar.querySelector('.user-dropdown');
        if (!userDropdown) return;
        const userInfo = userDropdown.querySelector('.user-info');
        if (!userInfo) return;

        const path = (window.location.pathname || '/').split('?')[0].split('#')[0];
        const segs = path.split('/').filter(Boolean);
        if (segs.length === 0) return;

        const LABELS = {
            'dashboard': 'Dashboard',
            'employee-dashboard': 'Dashboard',
            'employees': 'Employees',
            'departments': 'Departments',
            'positions': 'Positions',
            'users': 'Users',
            'salary-grades': 'Salary Grades',
            'status-of-appointment': 'Status of Appointment',
            'leave-settings': 'Leave Settings',
            'leave-credits': 'Leave Credits',
            'leave-online': 'Leave',
            'dtr-records': 'DTR Records',
            'dtr-upload': 'DTR Upload',
            'dtr-justifications': 'DTR Justifications',
            'payroll': 'Payroll',
        };

        const items = [];
        let accum = '';
        for (let i = 0; i < segs.length; i++) {
            accum += '/' + segs[i];
            const raw = segs[i];
            const label = LABELS[raw] || (raw.match(/^\d+$/) ? `#${raw}` : titleize(raw));
            items.push({ label, url: accum });
        }

        const crumbs = document.createElement('div');
        crumbs.className = 'header-breadcrumbs header-breadcrumbs--visible header-breadcrumbs--nav';

        items.forEach((it, idx) => {
            const isLast = idx === items.length - 1;
            if (!isLast) {
                const a = document.createElement('a');
                a.href = it.url;
                a.textContent = it.label;
                crumbs.appendChild(a);
                const sep = document.createElement('span');
                sep.className = 'breadcrumb-sep';
                sep.textContent = '›';
                crumbs.appendChild(sep);
            } else {
                const cur = document.createElement('span');
                cur.textContent = it.label;
                crumbs.appendChild(cur);
            }
        });
        // Place breadcrumbs under the session username, bottom-right of navbar
        userInfo.insertAdjacentElement('afterend', crumbs);
    }

    function syncHeaderBarHeight() {
        if (!headerBar) return;
        document.documentElement.style.setProperty('--header-bar-height', headerBar.offsetHeight + 'px');
    }
    if (headerBar) {
        syncHeaderBarHeight();
        window.addEventListener('resize', syncHeaderBarHeight, { passive: true });
        window.addEventListener('load', syncHeaderBarHeight, { passive: true });

        attachAutoHideOnScroll(headerBar, 'header-bar--hidden', { hideDelayMs: 380, minScrollDiff: 8, keepVisibleUntilY: 20 });
    }

    // Landing page public navbar
    attachAutoHideOnScroll(landingTopnav, 'landing-topnav--hidden', { hideDelayMs: 220, minScrollDiff: 6, keepVisibleUntilY: 8 });
    buildBreadcrumbs();

    // Auto-dismiss alerts after 5 seconds
    const alerts = document.querySelectorAll('.alert:not(.alert-dismissible)');
    alerts.forEach(alert => {
        setTimeout(() => {
            alert.style.transition = 'opacity 0.5s';
            alert.style.opacity = '0';
            setTimeout(() => alert.remove(), 500);
        }, 5000);
    });
    
    // Nav dropdowns: click to toggle (works on mobile); hover still supported via CSS
    const navDropdowns = document.querySelectorAll('.nav-dropdown');
    navDropdowns.forEach(dropdown => {
        const link = dropdown.querySelector(':scope > .nav-link');
        const menu = dropdown.querySelector(':scope > .dropdown-menu');
        if (!link || !menu) return;

        // If the dropdown trigger is a hash link, treat it as a toggle button.
        if ((link.getAttribute('href') || '') === '#') {
            link.setAttribute('role', 'button');
            link.setAttribute('aria-haspopup', 'menu');
            link.setAttribute('aria-expanded', 'false');

            link.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();

                // Close other open nav dropdowns first
                document.querySelectorAll('.nav-dropdown .dropdown-menu.show').forEach(other => {
                    if (other !== menu) other.classList.remove('show');
                });

                const isOpen = menu.classList.contains('show');
                menu.classList.toggle('show', !isOpen);
                link.setAttribute('aria-expanded', String(!isOpen));
            });
        }
    });
    
    // Handle user dropdown - prevent default on click and keep menu open
    const userDropdowns = document.querySelectorAll('.user-dropdown');
    userDropdowns.forEach(dropdown => {
        const link = dropdown.querySelector('.user-info');
        if (link && link.getAttribute('href') === '#') {
            link.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                // Toggle dropdown visibility
                const menu = dropdown.querySelector('.dropdown-menu');
                if (menu) {
                    const isVisible = window.getComputedStyle(menu).display === 'block' || menu.classList.contains('show');
                    if (isVisible) {
                        menu.style.display = 'none';
                        menu.classList.remove('show');
                    } else {
                        // Close any open nav dropdowns so menus don't overlap
                        document.querySelectorAll('.nav-dropdown .dropdown-menu.show').forEach(other => other.classList.remove('show'));
                        menu.style.display = 'block';
                        menu.classList.add('show');
                    }
                }
            });
        }
        
        // Keep dropdown open when hovering over it
        dropdown.addEventListener('mouseenter', function() {
            const menu = dropdown.querySelector('.dropdown-menu');
            if (menu) {
                menu.style.display = 'block';
                menu.classList.add('show');
            }
        });
        
        dropdown.addEventListener('mouseleave', function() {
            const menu = dropdown.querySelector('.dropdown-menu');
            if (menu) {
                // Small delay to allow moving to menu
                setTimeout(() => {
                    if (!dropdown.matches(':hover') && !menu.matches(':hover')) {
                        menu.style.display = 'none';
                        menu.classList.remove('show');
                    }
                }, 150);
            }
        });
    });
    
    // Close dropdowns when clicking outside
    document.addEventListener('click', function(e) {
        if (!e.target.closest('.nav-dropdown')) {
            document.querySelectorAll('.nav-dropdown .dropdown-menu.show').forEach(menu => {
                menu.classList.remove('show');
                const trigger = menu.closest('.nav-dropdown')?.querySelector(':scope > .nav-link');
                if (trigger) trigger.setAttribute('aria-expanded', 'false');
            });
        }
        if (!e.target.closest('.user-dropdown')) {
            const openMenus = document.querySelectorAll('.user-dropdown .dropdown-menu.show');
            openMenus.forEach(menu => {
                menu.style.display = 'none';
                menu.classList.remove('show');
            });
        }
    });
    
    // Form validation
    const forms = document.querySelectorAll('form');
    forms.forEach(form => {
        form.addEventListener('submit', function(e) {
            const requiredFields = form.querySelectorAll('[required]');
            let isValid = true;
            
            requiredFields.forEach(field => {
                if (!field.value.trim()) {
                    isValid = false;
                    field.style.borderColor = '#f44336';
                } else {
                    field.style.borderColor = '';
                }
            });
            
            if (!isValid) {
                e.preventDefault();
                alert('Please fill in all required fields.');
            }
        });
    });
    
    // Clickable table rows
    const clickableRows = document.querySelectorAll('.clickable-row');
    clickableRows.forEach(row => {
        row.addEventListener('click', function(e) {
            // Don't navigate if clicking on action buttons
            if (!e.target.closest('.actions-cell')) {
                const href = this.getAttribute('data-href');
                if (href) {
                    window.location.href = href;
                }
            }
        });
    });
    
    // Table search functionality - Users table
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.addEventListener('input', function(e) {
            const searchTerm = e.target.value.toLowerCase().trim();
            const tableRows = document.querySelectorAll('#usersTable .table-row');
            
            tableRows.forEach(row => {
                const searchData = row.getAttribute('data-search') || '';
                if (searchData.includes(searchTerm)) {
                    row.classList.remove('hidden');
                } else {
                    row.classList.add('hidden');
                }
            });
            
            // Show "No results" message if all rows are hidden
            const visibleRows = Array.from(tableRows).filter(row => !row.classList.contains('hidden'));
            const tbody = document.getElementById('tableBody');
            const existingNoResults = tbody.querySelector('.no-results');
            
            if (visibleRows.length === 0 && searchTerm !== '') {
                if (!existingNoResults) {
                    // Remove existing no-data row if it exists (but not no-results)
                    const existingNoData = tbody.querySelector('.no-data:not(.no-results)');
                    if (existingNoData) {
                        existingNoData.style.display = 'none';
                    }
                    
                    // Create new no-results row
                    const noResultsRow = document.createElement('tr');
                    noResultsRow.className = 'no-data no-results';
                    noResultsRow.innerHTML = '<td colspan="7" class="no-data">No users found matching your search</td>';
                    tbody.appendChild(noResultsRow);
                }
            } else {
                // Remove no-results row if it exists
                if (existingNoResults) {
                    existingNoResults.remove();
                }
                // Show existing no-data row if it was hidden
                const existingNoData = tbody.querySelector('.no-data:not(.no-results)');
                if (existingNoData) {
                    existingNoData.style.display = '';
                }
            }
        });
    }
    
    // Table search functionality - Employees table
    const searchInputEmployees = document.getElementById('searchInputEmployees');
    const deptFilterEmployees = document.getElementById('departmentFilterEmployees');
    if (searchInputEmployees) {
        const applyEmployeesFilters = () => {
            const searchTerm = (searchInputEmployees.value || '').toLowerCase().trim();
            const deptId = deptFilterEmployees ? (deptFilterEmployees.value || '').trim() : '';
            const tableRows = document.querySelectorAll('#employeesTable .table-row');
            const tbody = document.getElementById('employeesTableBody');
            const existingNoResults = tbody ? tbody.querySelector('.no-results') : null;

            tableRows.forEach(row => {
                const searchData = (row.getAttribute('data-search') || '').toLowerCase();
                const rowDeptId = (row.getAttribute('data-department-id') || '').trim();
                const okSearch = !searchTerm || searchData.includes(searchTerm);
                const okDept = !deptId || rowDeptId === deptId;
                if (okSearch && okDept) {
                    row.classList.remove('hidden');
                } else {
                    row.classList.add('hidden');
                }
            });

            const visibleRows = Array.from(tableRows).filter(row => !row.classList.contains('hidden'));

            if (tbody) {
                const colSpan = tbody.getAttribute('data-cols') || '6';
                const shouldShowNoResults = visibleRows.length === 0 && (searchTerm !== '' || deptId !== '');

                if (shouldShowNoResults) {
                    if (!existingNoResults) {
                        const existingNoData = tbody.querySelector('.no-data:not(.no-results)');
                        if (existingNoData) {
                            existingNoData.style.display = 'none';
                        }
                        const noResultsRow = document.createElement('tr');
                        noResultsRow.className = 'no-data no-results';
                        noResultsRow.innerHTML = `<td colspan="${colSpan}" class="no-data">No employees found matching your filters</td>`;
                        tbody.appendChild(noResultsRow);
                    }
                } else {
                    if (existingNoResults) {
                        existingNoResults.remove();
                    }
                    const existingNoData = tbody.querySelector('.no-data:not(.no-results)');
                    if (existingNoData) {
                        existingNoData.style.display = '';
                    }
                }
            }
        };

        searchInputEmployees.addEventListener('input', applyEmployeesFilters);
        if (deptFilterEmployees) {
            deptFilterEmployees.addEventListener('change', applyEmployeesFilters);
        }
    }
    
    // Table search functionality - Departments table
    const searchInputDepartments = document.getElementById('searchInputDepartments');
    if (searchInputDepartments) {
        searchInputDepartments.addEventListener('input', function(e) {
            const searchTerm = e.target.value.toLowerCase().trim();
            const tableRows = document.querySelectorAll('#departmentsTable .table-row');
            
            tableRows.forEach(row => {
                const searchData = row.getAttribute('data-search') || '';
                if (searchData.includes(searchTerm)) {
                    row.classList.remove('hidden');
                } else {
                    row.classList.add('hidden');
                }
            });
            
            // Show "No results" message if all rows are hidden
            const visibleRows = Array.from(tableRows).filter(row => !row.classList.contains('hidden'));
            const tbody = document.getElementById('departmentsTableBody');
            const existingNoResults = tbody.querySelector('.no-results');
            
            if (visibleRows.length === 0 && searchTerm !== '') {
                if (!existingNoResults) {
                    // Remove existing no-data row if it exists (but not no-results)
                    const existingNoData = tbody.querySelector('.no-data:not(.no-results)');
                    if (existingNoData) {
                        existingNoData.style.display = 'none';
                    }
                    
                    // Create new no-results row
                    const noResultsRow = document.createElement('tr');
                    noResultsRow.className = 'no-data no-results';
                    noResultsRow.innerHTML = '<td colspan="5" class="no-data">No departments found matching your search</td>';
                    tbody.appendChild(noResultsRow);
                }
            } else {
                // Remove no-results row if it exists
                if (existingNoResults) {
                    existingNoResults.remove();
                }
                // Show existing no-data row if it was hidden
                const existingNoData = tbody.querySelector('.no-data:not(.no-results)');
                if (existingNoData) {
                    existingNoData.style.display = '';
                }
            }
        });
    }
});
