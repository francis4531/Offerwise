"""Dynamic Open Graph preview card for shared risk checks.

Renders a 1200x630 PNG so a shared /r/<token> link unfurls into a rich,
provocative card in iMessage, Reddit, X, Facebook, etc. Self-contained:
fonts are bundled in static/fonts so this works identically in production.
"""
import io
import os
import textwrap
from PIL import Image, ImageDraw, ImageFont

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'fonts')


def _font(name, size):
    try:
        return ImageFont.truetype(os.path.join(_FONT_DIR, name), size)
    except Exception:
        try:
            return ImageFont.load_default(size=size)
        except Exception:
            return ImageFont.load_default()


def render_card(headline, address='', grade='?', exposure=0, risk_count=0):
    W, H = 1200, 630
    BG = (8, 13, 24)
    ORANGE = (255, 122, 48)
    AMBER = (245, 166, 35)
    TEXT = (238, 243, 251)
    MUTED = (159, 176, 201)
    DIM = (106, 124, 152)

    img = Image.new('RGB', (W, H), BG)
    d = ImageDraw.Draw(img)
    serif = lambda s: _font('DMSerifDisplay-Regular.ttf', s)
    bold = lambda s: _font('DejaVuSans-Bold.ttf', s)
    reg = lambda s: _font('DejaVuSans.ttf', s)
    M = 70

    d.rectangle([0, 0, W, 8], fill=ORANGE)
    d.text((M, 50), 'OfferWise', font=bold(38), fill=TEXT)
    d.text((M, 120), "WHAT'S HIDING AT THIS ADDRESS?", font=bold(23), fill=AMBER)

    y = 172
    for line in textwrap.wrap(headline or 'What is the seller not telling you?', width=30)[:3]:
        d.text((M, y), line, font=serif(60), fill=TEXT)
        y += 70

    y2 = 432
    d.text((M, y2), 'Grade ' + str(grade), font=bold(38), fill=ORANGE)
    try:
        exp = '${:,}'.format(int(exposure or 0))
    except Exception:
        exp = '$0'
    d.text((M + 250, y2), exp, font=bold(38), fill=TEXT)
    sub = '{} undisclosed risk(s) found'.format(risk_count)
    if address:
        sub += '   \u00b7   ' + (address[:46])
    d.text((M, y2 + 54), sub, font=reg(25), fill=MUTED)

    d.text((M, H - 64), 'Scan any address free  \u00b7  11 government databases  \u00b7  getofferwise.ai',
           font=reg(23), fill=DIM)

    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


if __name__ == '__main__':
    png = render_card(
        headline="This home sits in a flood zone FEMA never studied",
        address="128 Maple Avenue, San Jose, CA 95112",
        grade="D", exposure=47500, risk_count=3)
    open('/tmp/card_test.png', 'wb').write(png)
    print('wrote /tmp/card_test.png', len(png), 'bytes')
