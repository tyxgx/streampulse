/**
 * Site-wide JavaScript entry point.
 * Bootstrap's bundle already handles navbar collapse/dropdown behavior.
 * Page-specific scripts (e.g. chatbot UI) live in their own app static/
 * directories and are loaded via {% block extra_js %}.
 */
(function () {
  document.addEventListener('DOMContentLoaded', () => {
    const nav = document.querySelector('.site-navbar');
    if (!nav) return;

    const SCROLL_THRESHOLD = 24;
    const updateNavbarState = () => {
      nav.classList.toggle('navbar-scrolled', window.scrollY > SCROLL_THRESHOLD);
    };

    updateNavbarState();
    window.addEventListener('scroll', updateNavbarState, { passive: true });
  });
})();
