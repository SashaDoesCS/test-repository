/* theme.js — dark/light toggle, persisted in localStorage */
(function () {
  function apply(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('lg-theme', theme);
  }

  // On load: restore saved preference or default dark
  const saved = localStorage.getItem('lg-theme');
  apply(saved === 'light' ? 'light' : 'dark');

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.theme-toggle').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const current = document.documentElement.getAttribute('data-theme');
        apply(current === 'light' ? 'dark' : 'light');
      });
    });

    // Mark active nav link
    const path = location.pathname.split('/').pop() || 'index.html';
    document.querySelectorAll('.site-header nav a').forEach(function (a) {
      const href = a.getAttribute('href') || '';
      const hfile = href.split('/').pop();
      if (hfile === path || (path === '' && hfile === 'index.html')) {
        a.classList.add('active');
      }
    });
  });
})();
