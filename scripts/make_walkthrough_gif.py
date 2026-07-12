#!/usr/bin/env python3
"""
make_walkthrough_gif.py — v5.89.290

Builds an animated product walkthrough GIF by driving the REAL OfferWise UI in a
headless browser. Nothing is mocked: it serves the actual static surfaces
(index-v2.html, try.html, sample-analysis.html) and screenshots them, so every frame
is a page the product genuinely ships. The report frames come from
static/sample-analysis.html — the same "real analysis, no login" sample buyers see.

Usage:  python3 scripts/make_walkthrough_gif.py [--out PATH] [--port 8899]

Output: an optimized GIF of the user journey:
    landing -> "what is the seller not telling you" -> drop a document ->
    the analysis: executive summary -> critical issues -> the disclosure
    contradictions (the moat) -> strategic options
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
STATIC = os.path.join(REPO, 'static')

# (page, scroll_y, hold_frames, caption) — hold_frames repeats a frame to pause on it.
SHOTS = [
    ('index-v2.html',        0,    5, 'A buyer lands: "What is the seller not telling you?"'),
    ('index-v2.html',        620,  3, 'One promise: drop your documents, get the contradictions'),
    ('try.html',             0,    5, 'No signup — drop an inspection report'),
    ('sample-analysis.html', 0,    4, 'The analysis'),
    ('sample-analysis.html', 700,  4, 'Executive summary + the offer number'),
    ('sample-analysis.html', 1450, 5, 'Critical issues, priced'),
    ('sample-analysis.html', 2500, 4, 'Seller Transparency Report'),
    ('sample-analysis.html', 3100, 6, 'THE MOAT: disclosure inconsistencies'),
    ('sample-analysis.html', 3800, 4, 'Predicted hidden issues'),
    ('sample-analysis.html', 4500, 4, 'Strategic options -> a defensible offer'),
]


def capture(port: int, width: int, height: int, outdir: str):
    from playwright.sync_api import sync_playwright
    frames = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=['--no-sandbox','--disable-dev-shm-usage'])
        page = browser.new_page(viewport={'width': width, 'height': height})
        last_page = None
        for i, (fname, y, hold, caption) in enumerate(SHOTS):
            if fname != last_page:
                page.goto(f'http://localhost:{port}/{fname}', wait_until='domcontentloaded', timeout=20000)
                page.wait_for_timeout(1500)  # let fonts/CSS settle; networkidle never fires offline (CDN assets hang)
                page.wait_for_timeout(700)
                last_page = fname
            page.evaluate(f'window.scrollTo({{top:{y}, behavior:"instant"}})')
            page.wait_for_timeout(400)
            path = os.path.join(outdir, f'frame_{i:02d}.png')
            page.screenshot(path=path)
            frames.append((path, hold, caption))
            print(f'  captured {fname} @ y={y}  — {caption}')
        browser.close()
    return frames


def build_gif(frames, out_path: str, width: int):
    from PIL import Image
    imgs = []
    for path, hold, _caption in frames:
        im = Image.open(path).convert('RGB')
        # scale down for a sane GIF size
        w, h = im.size
        nh = int(h * (width / w))
        im = im.resize((width, nh), Image.LANCZOS)
        im = im.convert('P', palette=Image.ADAPTIVE, colors=128)
        for _ in range(hold):
            imgs.append(im)
    if not imgs:
        raise SystemExit('no frames captured')
    imgs[0].save(out_path, save_all=True, append_images=imgs[1:],
                 duration=450, loop=0, optimize=True)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='/mnt/user-data/outputs/OfferWise_Walkthrough.gif')
    ap.add_argument('--port', type=int, default=8899)
    ap.add_argument('--width', type=int, default=900, help='GIF width in px')
    args = ap.parse_args()

    srv = subprocess.Popen([sys.executable, '-m', 'http.server', str(args.port)],
                           cwd=STATIC, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.5)
        tmp = '/tmp/ow_frames'
        os.makedirs(tmp, exist_ok=True)
        print('Capturing the real product surfaces…')
        frames = capture(args.port, 1280, 800, tmp)
        print('Building GIF…')
        out = build_gif(frames, args.out, args.width)
        size = os.path.getsize(out) / 1e6
        print(f'✅ {out}  ({size:.1f} MB, {sum(h for _, h, _ in frames)} frames)')
    finally:
        srv.terminate()


if __name__ == '__main__':
    main()
