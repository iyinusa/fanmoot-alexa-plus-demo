/**
 * The faded pitch behind every screen of the app.
 *
 * The same markings as the landing page's hero and as `_field.html` behind the
 * server-rendered pages, so the app, its public pages and its front door read
 * as one place. Styling lives in `.fm-field` (app.css): fixed, masked to fade
 * out before the middle of the screen, and a few percent opaque, so it is felt
 * rather than read and never competes with a moot.
 *
 * Static on purpose. A moving background behind a feed people read for an
 * hour is a vestibular trigger for some of them and a distraction for the rest.
 */
export function FieldBackdrop() {
  return (
    <div className="fm-field" aria-hidden="true">
      <svg viewBox="0 0 1200 700" preserveAspectRatio="xMidYMid slice" focusable="false">
        <g fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="40" y="40" width="1120" height="620" rx="4" />
          <line x1="600" y1="40" x2="600" y2="660" />
          <circle cx="600" cy="350" r="92" />
          <circle cx="600" cy="350" r="3" fill="currentColor" />
          <rect x="40" y="200" width="160" height="300" />
          <rect x="1000" y="200" width="160" height="300" />
          <rect x="40" y="282" width="56" height="136" />
          <rect x="1104" y="282" width="56" height="136" />
          <path d="M200 290a70 70 0 0 1 0 120M1000 290a70 70 0 0 0 0 120" />
        </g>
      </svg>
    </div>
  )
}
