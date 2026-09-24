"""``/sandbox`` — the proxy the server's ``ui://`` views render inside (W2-6, HAD-8).

The MCP Apps extension (``2026-01-26``, SEP-1865) tells a *web* host to render
a view through a double iframe: an outer **sandbox proxy** on an origin that is
not the host's, and inside it the view itself. This is the outer one.

**A different origin without a second domain.** The shell frames this page
with ``sandbox="allow-scripts allow-forms"`` and no ``allow-same-origin``, so
the browser gives it an opaque origin of its own: it cannot read the shell's
DOM, its storage, or the socket that holds the visitor's session, even though
it is served from the same host. The view inside it is further boxed by an
inner iframe with the ``sandbox`` the spec names.

**The handshake is the spec's**, word for word:

1. this page → shell   ``ui/notifications/sandbox-proxy-ready``
2. shell → this page   ``ui/notifications/sandbox-resource-ready``
   ``{html, csp}`` — the HTML read over MCP, and the resource's ``_meta.ui.csp``
3. this page loads the HTML into the inner iframe and from then on relays
   every JSON-RPC message between the view and the shell, except its own
   ``ui/notifications/sandbox-*``.

**Two policies, and both apply.** This response's CSP is the outer bound —
inline script and style, images and fonts over https, nothing else — and an
inner ``srcdoc`` document inherits it. The proxy then writes the view's *own*
policy into the view's ``<head>``, built from the domains the resource
declared with the spec's template. A browser enforces both, so a view gets
exactly what its server declared: Fanmoot's views declare one image origin for
crests and no ``connectDomains``, so from inside them nothing can be fetched
at all. That is "MUST NOT allow undeclared domains", enforced by the browser
rather than promised.

``allow-forms`` is the one permission past the spec's two: the thread view's
reply composer is a ``<form>``, and a sandboxed document without it never
fires ``submit``. ``form-action 'none'`` keeps that from ever navigating.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

#: The outer bound. The view's declared policy is written inside it (above).
#: ``frame-ancestors 'self'``: only the shell may frame this.
_CSP = (
    "default-src 'none'; script-src 'unsafe-inline' https:; style-src 'unsafe-inline' https:; "
    "img-src data: https:; font-src data: https:; media-src data: https:; connect-src https:; "
    "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
)

_HEADERS = {
    "Content-Security-Policy": _CSP,
    # The middleware adds DENY to every HTML page; this is the one page that
    # exists to be framed, by the shell and nothing else.
    "X-Frame-Options": "SAMEORIGIN",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cache-Control": "no-cache",
}

# Written by hand, kept small, and ES5 so it reads the same as the views'
# own bridge. Every value that crosses in is data: the HTML goes into
# `srcdoc`, never into this document.
PROXY_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Fanmoot view sandbox</title>
<style>
html, body { margin: 0; padding: 0; background: transparent; overflow: hidden; }
iframe { display: block; width: 100%; height: 100vh; border: 0; background: transparent; }
</style>
</head>
<body>
<script>
(function () {
  'use strict';
  var PREFIX = 'ui/notifications/sandbox-';
  var DOMAIN = /^https:\\/\\/(\\*\\.)?[a-z0-9-]+(\\.[a-z0-9-]+)*(:\\d{1,5})?$/i;
  var inner = null;

  function domains(value) {
    if (!Array.isArray(value)) return '';
    return value.filter(function (d) { return typeof d === 'string' && DOMAIN.test(d); }).join(' ');
  }

  /* The spec's template, from the resource's declared domains. */
  function policy(csp) {
    csp = csp || {};
    var res = domains(csp.resourceDomains);
    var frames = domains(csp.frameDomains);
    var base = domains(csp.baseUriDomains);
    return [
      "default-src 'none'",
      "script-src 'self' 'unsafe-inline' " + res,
      "style-src 'self' 'unsafe-inline' " + res,
      "connect-src 'self' " + domains(csp.connectDomains),
      "img-src 'self' data: " + res,
      "font-src 'self' " + res,
      "media-src 'self' data: " + res,
      'frame-src ' + (frames || "'none'"),
      "object-src 'none'",
      'base-uri ' + (base || "'self'"),
      "form-action 'none'"
    ].join('; ');
  }

  function withPolicy(html, csp) {
    var meta = '<meta http-equiv="Content-Security-Policy" content="' +
      policy(csp).replace(/"/g, '&quot;') + '">';
    var head = /<head[^>]*>/i.exec(html);
    if (!head) return meta + html;
    var at = head.index + head[0].length;
    return html.slice(0, at) + meta + html.slice(at);
  }

  function internal(data) {
    return typeof data.method === 'string' && data.method.indexOf(PREFIX) === 0;
  }

  window.addEventListener('message', function (event) {
    var data = event.data;
    if (!data || data.jsonrpc !== '2.0') return;

    if (event.source === window.parent) {
      if (data.method === PREFIX + 'resource-ready') {
        if (inner) return; /* one view per proxy */
        var params = data.params || {};
        inner = document.createElement('iframe');
        inner.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-forms');
        var title = typeof params.title === 'string' ? params.title : 'Fanmoot card';
        inner.setAttribute('title', title);
        inner.srcdoc = withPolicy(String(params.html || ''), params.csp);
        document.body.appendChild(inner);
        return;
      }
      if (internal(data)) return;
      if (inner && inner.contentWindow) inner.contentWindow.postMessage(data, '*');
      return;
    }

    if (inner && event.source === inner.contentWindow && !internal(data)) {
      window.parent.postMessage(data, '*');
    }
  });

  window.parent.postMessage({ jsonrpc: '2.0', method: PREFIX + 'proxy-ready', params: {} }, '*');
})();
</script>
</body>
</html>
"""


@router.api_route("/sandbox", methods=["GET", "HEAD"], include_in_schema=False)
async def sandbox() -> HTMLResponse:
    return HTMLResponse(PROXY_HTML, headers=_HEADERS)
