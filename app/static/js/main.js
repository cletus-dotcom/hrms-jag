// Main JavaScript file for HRMS

document.addEventListener('DOMContentLoaded', function() {
    // Auto-dismiss alerts after 5 seconds
    const alerts = document.querySelectorAll('.alert:not(.alert-dismissible)');
    alerts.forEach(alert => {
        setTimeout(() => {
            alert.style.transition = 'opacity 0.5s';
            alert.style.opacity = '0';
            setTimeout(() => alert.remove(), 500);
        }, 5000);
    });
    
    // Handle dropdown menus - keep open on hover
    const dropdowns = document.querySelectorAll('.nav-dropdown');
    dropdowns.forEach(dropdown => {
        const link = dropdown.querySelector('.nav-link');
        // Prevent default only if it's a hash link
        if (link && link.getAttribute('href') === '#') {
            link.addEventListener('click', function(e) {
                e.preventDefault();
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
