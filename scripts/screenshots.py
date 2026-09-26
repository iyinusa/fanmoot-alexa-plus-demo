"""Photograph the demo for the README (W6-4).

    make screenshots                                   # https://alexaplus.fanmoot.com
    make screenshots URL=http://localhost:8090

Drives headless Chrome over the DevTools protocol, with nothing to install
beyond Chrome itself (found as ``make share-image`` finds it). It asks the
demo **one real question** (the first guided scenario, which is written from
what is on), so the pictures show a real answer, a real card
and a real trace, and it costs one turn (about a cent on Nova Pro).

Writes to ``docs/screenshots/``:

``welcome.png``       the first screen a visitor sees, 1440 × 900
``conversation.png``  an answer, its card and the trace beside it
``trace.png``         the same, with a ``tools/call`` payload opened
``phone.png``         the conversation at 390 × 844

Dark theme and reduced motion, so nothing is caught mid-animation. Shoot
against the production deployment, so the trace shows ``mcp.fanmoot.com``
and a Bedrock model answering.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import itertools
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from websockets.asyncio.client import ClientConnection, connect

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_share_image import find_chrome  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
QUESTION = "Who's playing in the Premier League in the next few days?"


class Page:
    """Just enough of the DevTools protocol: send a command, await its answer."""

    def __init__(self, socket: ClientConnection) -> None:
        self.socket = socket
        self.ids = itertools.count(1)

    async def send(self, method: str, **params: Any) -> dict[str, Any]:
        call_id = next(self.ids)
        await self.socket.send(json.dumps({"id": call_id, "method": method, "params": params}))
        while True:
            message = json.loads(await self.socket.recv())
            if message.get("id") == call_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return dict(message.get("result", {}))

    async def js(self, expression: str) -> Any:
        result = await self.send(
            "Runtime.evaluate", expression=expression, awaitPromise=True, returnByValue=True
        )
        return result.get("result", {}).get("value")

    async def until(self, expression: str, within: float = 30.0) -> None:
        deadline = time.monotonic() + within
        while time.monotonic() < deadline:
            if await self.js(expression):
                return
            await asyncio.sleep(0.25)
        raise TimeoutError(f"never true: {expression}")

    async def viewport(self, width: int, height: int, *, mobile: bool = False) -> None:
        await self.send(
            "Emulation.setDeviceMetricsOverride",
            width=width,
            height=height,
            deviceScaleFactor=2,
            mobile=mobile,
        )

    async def shoot(self, path: Path) -> None:
        await asyncio.sleep(0.8)  # fonts and the last paint
        shot = await self.send("Page.captureScreenshot", format="png")
        await asyncio.to_thread(path.write_bytes, base64.b64decode(shot["data"]))
        print(f"screenshots: wrote {path}")


def launch(chrome: str) -> tuple[subprocess.Popen[bytes], str]:
    """Headless Chrome, and the DevTools URL of its one page."""
    profile = tempfile.mkdtemp(prefix="fm-shots-")
    process = subprocess.Popen(  # noqa: S603 - our own Chrome, our own arguments
        [
            chrome,
            "--headless=new",
            "--remote-debugging-port=0",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--hide-scrollbars",
            "--mute-audio",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    port_file = Path(profile) / "DevToolsActivePort"
    for _ in range(100):
        if port_file.exists() and port_file.read_text().strip():
            break
        time.sleep(0.1)
    port = port_file.read_text().splitlines()[0]
    targets = httpx.get(f"http://127.0.0.1:{port}/json/list").json()
    return process, next(t for t in targets if t["type"] == "page")["webSocketDebuggerUrl"]


async def shoot(base: str, out: Path, page_url: str) -> None:
    async with connect(page_url, max_size=2**26) as socket:
        page = Page(socket)
        await page.send("Page.enable")
        await page.send(
            "Emulation.setEmulatedMedia",
            features=[
                {"name": "prefers-color-scheme", "value": "dark"},
                {"name": "prefers-reduced-motion", "value": "reduce"},
            ],
        )
        await page.viewport(1440, 900)

        # A first visit: the welcome screen.
        await page.send("Page.navigate", url=f"{base}/")
        await page.until("document.fonts.status === 'loaded' && !!document.querySelector('#ask')")
        await page.until("document.body.innerText.includes('mcp.fanmoot.com')", within=20)
        await page.shoot(out / "welcome.png")

        # A returning visitor, with the trace open, asks one question.
        await page.js(
            "localStorage.setItem('fm-intro', 'seen');"
            "localStorage.setItem('fm-trace-open', 'open');"
            "sessionStorage.clear()"
        )
        await page.send("Page.navigate", url=f"{base}/")
        await page.until("!!document.querySelector('#ask')")
        await asyncio.sleep(3)  # the MCP session opens and the ticker fills
        # The first guided scenario is written from what is actually on
        # (W4), so it photographs better than any fixed question on a quiet
        # day. The fixed question is the fallback.
        asked = await page.js(
            """(() => {
              const chip = [...document.querySelectorAll('button')]
                .find(b => b.textContent.includes('\u201c'));
              if (chip) chip.click();
              return chip ? chip.textContent : null;
            })()"""
        )
        if not asked:
            await page.js(
                f"""(() => {{
                  const ask = document.querySelector('#ask');
                  const set = Object.getOwnPropertyDescriptor(
                    Object.getPrototypeOf(ask), 'value').set;
                  set.call(ask, {json.dumps(QUESTION)});
                  ask.dispatchEvent(new Event('input', {{ bubbles: true }}));
                  document.querySelector('button[aria-label="Send"]').click();
                }})()"""
            )
        await page.until("!!document.querySelector('[aria-label=\"Stop the answer\"]')", 10)
        await page.until("!document.querySelector('[aria-label=\"Stop the answer\"]')", 90)
        await asyncio.sleep(2.5)  # the card renders in its sandbox
        await page.shoot(out / "conversation.png")

        opened = await page.js(
            """(() => {
              const row = [...document.querySelectorAll('button')]
                .find(b => b.textContent.includes('tools/call')
                    && !b.textContent.includes('TICKER') && !b.textContent.includes('ticker'));
              if (row) row.click();
              return !!row;
            })()"""
        )
        if opened:
            await page.shoot(out / "trace.png")
        else:
            print("screenshots: no tools/call row to open; trace.png not written")

        await page.viewport(390, 844, mobile=True)
        await page.shoot(out / "phone.png")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("url", nargs="?", default="https://alexaplus.fanmoot.com")
    parser.add_argument("--out", default=str(ROOT / "docs" / "screenshots"))
    args = parser.parse_args()
    chrome = find_chrome()
    if chrome is None:
        print("screenshots: no Chrome found; set CHROME=/path/to/chrome", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    process, page_url = launch(chrome)
    try:
        asyncio.run(shoot(args.url.rstrip("/"), out, page_url))
    finally:
        process.terminate()
        process.wait(timeout=10)
    return 0


if __name__ == "__main__":
    sys.exit(main())
