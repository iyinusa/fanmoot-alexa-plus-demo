// Mirrors readTheme() in src/lib/theme.ts. Loaded as a blocking file in <head>
// rather than inline so the Content-Security-Policy can say `script-src 'self'`
// with no exceptions (M15-5); tiny and first, because the bundle arrives after
// first paint and a light-theme fan should not see a dark flash on every load.
// "system" is no attribute at all.
try {
  var t = localStorage.getItem('fm.theme')
  if (t === 'light') document.documentElement.setAttribute('data-theme', 'light')
  else if (t === 'system') document.documentElement.removeAttribute('data-theme')
} catch (e) {}
