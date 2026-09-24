/**
 * Motion helpers the CSS and Framer Motion's `reducedMotion="user"` cannot
 * reach (W5-2): a script-driven smooth scroll ignores `scroll-behavior`, so
 * it asks here first.
 */
export function prefersReducedMotion(): boolean {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
}

/** `smooth`, unless the visitor asked for less motion. */
export function scrollBehavior(): ScrollBehavior {
  return prefersReducedMotion() ? 'auto' : 'smooth'
}
