"""Render the share image, ``web/public/share/og.png`` (W5-6).

    make share-image

The picture a link to the demo unfurls into: 1200 x 630, the size Open Graph
and X's ``summary_large_image`` both crop to. It is drawn as HTML
(``scripts/share/og.html``) in the brand's own colours and Lato, loaded from
the files the demo serves, and photographed by headless Chrome's built-in
``--screenshot``. So there is nothing to install: any machine with Chrome can
redraw it, and a change to the brand shows up in the image the next time it
is made.

Chrome is found at ``$CHROME`` or in the usual places on macOS and Linux. The
result is committed. The Docker image never runs this, it copies the PNG.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "scripts" / "share" / "og.html"
TARGET = ROOT / "web" / "public" / "share" / "og.png"
SIZE = (1200, 630)

#: How long Chrome may take to draw it.
TIMEOUT_S = 45

_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)


def find_chrome() -> str | None:
    for candidate in (os.environ.get("CHROME"), *_CANDIDATES):
        if not candidate:
            continue
        if Path(candidate).is_file():
            return candidate
        found = shutil.which(candidate)
        if found:
            return found
    return None


def png_size(path: Path) -> tuple[int, int]:
    """Width and height from the IHDR chunk, so no imaging library is needed."""
    header = path.read_bytes()[:24]
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG")
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def _written(path: Path, process: subprocess.Popen[bytes]) -> bool:
    """True once ``path`` exists and has held one size for half a second."""
    deadline = time.monotonic() + TIMEOUT_S
    last = -1
    while time.monotonic() < deadline:
        size = path.stat().st_size if path.exists() else -1
        if size > 0 and size == last:
            return True
        if process.poll() is not None and size <= 0:
            return False
        last = size
        time.sleep(0.5)
    return False


def main() -> int:
    chrome = find_chrome()
    if chrome is None:
        print("No Chrome found. Set CHROME=/path/to/chrome and try again.", file=sys.stderr)
        return 1

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as profile:
        shot = Path(profile) / "og.png"
        # A fixed argument list, and Chrome found on this machine: nothing
        # from outside reaches the command line.
        chrome_process = subprocess.Popen(  # noqa: S603
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                # The template loads the served fonts and icon by relative path.
                "--allow-file-access-from-files",
                f"--user-data-dir={profile}",
                f"--window-size={SIZE[0]},{SIZE[1]}",
                f"--screenshot={shot}",
                TEMPLATE.as_uri(),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Headless Chrome on macOS writes the screenshot and then does not
        # exit. So the file is waited for, and Chrome is stopped once it has
        # stopped growing.
        try:
            if not _written(shot, chrome_process):
                print(f"Chrome drew nothing in {TIMEOUT_S} s.", file=sys.stderr)
                return 1
        finally:
            chrome_process.terminate()
            try:
                chrome_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                chrome_process.kill()
        if png_size(shot) != SIZE:
            print(f"Chrome drew {png_size(shot)}, not {SIZE}.", file=sys.stderr)
            return 1
        shutil.copyfile(shot, TARGET)

    print(f"wrote {TARGET.relative_to(ROOT)} ({TARGET.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
