/* ============================================================
   A.I.S.I.A.P. — shared front-end behavior
   ============================================================ */
(function () {
    'use strict';

    /* Sidebar (mobile) */
    function toggleSidebar(open) {
        var sb = document.getElementById('sidebar');
        var bd = document.getElementById('sidebarBackdrop');
        if (!sb) return;
        if (open === undefined) { open = !sb.classList.contains('open'); }
        sb.classList.toggle('open', open);
        if (bd) { bd.style.display = open ? 'block' : 'none'; }
        document.body.style.overflow = open ? 'hidden' : '';
    }

    document.addEventListener('click', function (e) {
        if (e.target.closest && e.target.closest('#sidebarToggle')) { toggleSidebar(); }
        if (e.target.id === 'sidebarBackdrop') { toggleSidebar(false); }
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { toggleSidebar(false); }
    });

    /* User dropdown + notifications */
    document.addEventListener('click', function (e) {
        var dd = document.getElementById('userDropdown');
        if (dd) {
            if (e.target.closest && e.target.closest('#userChip')) {
                dd.classList.toggle('open');
                e.stopPropagation();
            } else if (!dd.contains(e.target)) { dd.classList.remove('open'); }
        }
        var np = document.getElementById('notifPanel');
        if (np) {
            if (e.target.closest && e.target.closest('#notifBtn')) {
                np.classList.toggle('open');
                e.stopPropagation();
            } else if (!np.contains(e.target)) { np.classList.remove('open'); }
        }
    });

    /* Flash auto dismiss */
    document.querySelectorAll('.flash').forEach(function (el) {
        setTimeout(function () {
            el.style.transition = 'opacity .4s ease, transform .4s ease';
            el.style.opacity = '0';
            el.style.transform = 'translateY(-4px)';
            setTimeout(function () { el.remove(); }, 400);
        }, 5000);
    });
    /* Generic live search (input.js-search + table#target) */
    document.querySelectorAll('input.js-search').forEach(function (input) {
        // Live search for tables on same page
        input.addEventListener('input', function () {
            var targetId = input.getAttribute('data-target');
            var table = document.getElementById(targetId);
            if (!table) return;
            var q = input.value.toLowerCase().trim();
            var rows = table.querySelectorAll('tbody tr');
            var visible = 0;
            rows.forEach(function (row) {
                var match = row.textContent.toLowerCase().indexOf(q) !== -1;
                row.style.display = match ? '' : 'none';
                if (match) { visible++; }
            });
            var empty = document.getElementById(targetId + 'Empty');
            if (empty) { empty.style.display = visible === 0 ? 'block' : 'none'; }
        });

        // Global search on Enter key
        input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                var query = input.value.trim();
                if (query) {
                    // If we're already on interviews page, just filter locally
                    if (window.location.pathname === '/interviews' || window.location.pathname.startsWith('/interviews')) {
                        // Trigger the local filter
                        input.dispatchEvent(new Event('input'));
                    } else {
                        // Navigate to interviews page with search query
                        window.location.href = '/interviews?q=' + encodeURIComponent(query);
                    }
                }
            }
        });
    });

    /* Confirm prompts */
    document.querySelectorAll('a[data-confirm], form[data-confirm]').forEach(function (el) {
        el.addEventListener('click', function (e) {
            var msg = el.getAttribute('data-confirm') || 'Are you sure?';
            if (el.tagName === 'FORM') {
                if (!e.target.closest('button')) { return; }
            }
            if (!window.confirm(msg)) { e.preventDefault(); e.stopPropagation(); }
        });
    });
    /* Toast helper */
    window.aisiapToast = function (msg, type) {
        type = type || 'ok';
        var host = document.querySelector('.toast-host');
        if (!host) {
            host = document.createElement('div');
            host.className = 'toast-host';
            document.body.appendChild(host);
        }
        var toast = document.createElement('div');
        toast.className = 'toast ' + (type === 'err' ? 'err' : 'ok');
        var icon = type === 'err'
            ? '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>'
            : '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>';
        toast.innerHTML = icon + '<span></span>';
        toast.querySelector('span').textContent = msg;
        host.appendChild(toast);
        setTimeout(function () {
            toast.classList.add('out');
            setTimeout(function () { toast.remove(); }, 320);
        }, 3800);
    };

    /* Copy helper */
    window.aisiapCopy = function (text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(function () {
                window.aisiapToast('Copied: ' + text);
            }, function () { window.prompt('Copy this value:', text); });
        } else {
            window.prompt('Copy this value:', text);
        }
    };

    /* Sparkline builder for KPI cards */
    window.aisiapSpark = function (el, values, color) {
        if (!el || !values || !values.length) return;
        var w = 96, h = 34;
        var max = Math.max.apply(null, values), min = Math.min.apply(null, values);
        var range = (max - min) || 1, pad = 2;
        var pts = values.map(function (v, i) {
            var x = pad + (i * (w - pad * 2)) / (values.length - 1);
            var y = h - pad - ((v - min) / range) * (h - pad * 2);
            return [x.toFixed(1), y.toFixed(1)];
        });
        var line = pts.map(function (p) { return p.join(','); }).join(' ');
        var area = pts.map(function (p) { return p.join(','); }).join(' ') + ' ' + (w - pad) + ',' + h + ' ' + pad + ',' + h;
        el.innerHTML =
            '<svg viewBox="0 0 ' + w + ' ' + h + '" width="' + w + '" height="' + h + '" preserveAspectRatio="none">' +
            '<polygon points="' + area + '" fill="' + (color || '#3b82f6') + '" opacity="0.14"/>' +
            '<polyline points="' + line + '" fill="none" stroke="' + (color || '#3b82f6') + '" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>' +
            '</svg>';
    };
})();

    /* ---------- Theme toggle (light / dark) ---------- */
    function aisiapApplyTheme(t) {
        document.documentElement.setAttribute('data-theme', t);
        try { localStorage.setItem('aisiap-theme', t); } catch (e) {}
    }
    document.addEventListener('click', function (e) {
        if (!(e.target.closest && e.target.closest('#themeToggle'))) return;
        var cur = document.documentElement.getAttribute('data-theme');
        aisiapApplyTheme(cur === 'light' ? 'dark' : 'light');
    });

