/**
 * Scroll-reveal utility for elements with class="reveal".
 *
 * Progressive enhancement by construction: .reveal elements are visible
 * by default (see main.css). Only after this script confirms both
 * IntersectionObserver support AND that the user has not requested
 * reduced motion does it add .reveal-armed to <html>, which is what
 * actually switches elements to their hidden-until-visible state. A
 * visitor with JS disabled, an old browser, or reduced-motion enabled
 * always sees full content immediately — there's nothing to retrofit.
 */
(function () {
  const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (prefersReducedMotion || !('IntersectionObserver' in window)) return;

  document.addEventListener('DOMContentLoaded', () => {
    const items = document.querySelectorAll('.reveal');
    if (!items.length) return;

    document.documentElement.classList.add('reveal-armed');

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('reveal-visible');
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.15, rootMargin: '0px 0px -40px 0px' }
    );

    items.forEach((el) => observer.observe(el));
  });
})();
