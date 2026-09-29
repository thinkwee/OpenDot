"""Dev helper: screenshot the UI.  usage: shot.py <hash> <out.png> [w h] [wait_s]"""

import asyncio
import glob
import os
import sys

from playwright.async_api import async_playwright

TOKEN = open("data/access_token").read().strip()


async def main() -> None:
    h, out = sys.argv[1], sys.argv[2]
    w, hh = (int(sys.argv[3]), int(sys.argv[4])) if len(sys.argv) > 4 else (1440, 900)
    wait = float(sys.argv[5]) if len(sys.argv) > 5 else 2.5
    exe = sorted(glob.glob(os.path.expanduser(
        "~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")))[-1]
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path=exe)
        ctx = await b.new_context(viewport={"width": w, "height": hh}, device_scale_factor=1)
        await ctx.add_init_script(f"localStorage.setItem('dot_token', '{TOKEN}')")
        pg = await ctx.new_page()
        await pg.goto(f"http://127.0.0.1:7878/#/{h}")
        await pg.wait_for_timeout(wait * 1000)
        await pg.screenshot(path=out)
        await b.close()


asyncio.run(main())
