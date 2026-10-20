#!/usr/bin/env python3
"""Liquidity Field: the live BTC-USD limit order book, drawn as particles.

Pulls the level-2 order book, the last hour of 1-minute candles and the 24h
stats from Coinbase Exchange (Kraken as fallback) and writes one
self-contained, animated SVG:

  * the price axis is fixed; every particle is resting liquidity at its level
  * the mid replays the last 60 minutes of BTC across that axis: below it the
    book is bid, above it ask, so a rising mid eats into the sell side and a
    falling mid into the buy side
  * the headline price, the wall distances, imbalance and depth all tick with
    the replayed mid; the tallest untouched columns are the bid and ask walls

Every replay-synchronised motion runs on the SVG's own SMIL clock, so nothing
drifts apart. Standard library only, so it runs on a bare GitHub Actions runner.

    python3 scripts/liquidity_field.py --out dist/liquidity-field.svg
    python3 scripts/liquidity_field.py --demo --out preview.svg   # offline
"""

from __future__ import annotations

import argparse
import base64
import bisect
import itertools
import json
import math
import random
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# ── canvas ──────────────────────────────────────────────────────────────────
W, H = 1200, 650
PAD = 40
CX = W / 2                      # centre of the price axis
HALF_W = CX - PAD               # px from the centre to the edge of the plotted book
BASE = 506                      # baseline of the liquidity field
HMAX = 200                      # tallest column
BW = 3                          # bucket width in px
PARTICLES = 5200                # resting-liquidity particles (walls add more)
RAIN = 80                       # incoming limit orders
SPARKS = 36                     # executions at the touch
EDGE = 110                      # width of the fade where the book leaves the card

# ── motion ──────────────────────────────────────────────────────────────────
HISTORY = 60                    # minutes replayed
LOOP = 32.0                     # seconds per replay loop
REPLAY_END = 84.0               # % of the loop spent replaying the hour
HOLD_END = 93.0                 # % at which the rewind starts
TWINKLE = (1.0, 0.9, 0.78, 0.66, 0.42, 0.35)   # particle alphas; each shimmers around its own level

# ── palette ─────────────────────────────────────────────────────────────────
BG0, BG1 = "#04060B", "#0A0F1A"
INK, MUTED, DIM, LINE = "#E8EDF4", "#8B96A8", "#4A5468", "#182030"
BTC = "#F7931A"
BID = ("#D2FFF4", "#36F1C6", "#12907E")   # highlight · body · shadow
ASK = ("#FFD6E4", "#FF4F8B", "#A1245A")

FONTS = Path(__file__).with_name("fonts")
MONO = "JBM,'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
DISPLAY = "IDL,'Inter Display',Inter,'Helvetica Neue',Arial,sans-serif"

UA = "grzresearch-liquidity-field/1.0 (+https://github.com/grzresearch)"


# ═══ market data ════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Market:
    source: str
    bids: list[tuple[float, float]]      # (price, size), best first
    asks: list[tuple[float, float]]
    closes: list[float]                  # 1m closes, oldest first
    open_24h: float
    high_24h: float
    low_24h: float
    volume_24h: float
    time: datetime


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def coinbase() -> Market:
    base = "https://api.exchange.coinbase.com/products/BTC-USD"
    book = _get(f"{base}/book?level=2")
    candles = sorted(_get(f"{base}/candles?granularity=60"))      # [t, lo, hi, open, close, vol]
    stats = _get(f"{base}/stats")
    return Market(
        source="COINBASE",
        bids=[(float(p), float(s)) for p, s, *_ in book["bids"]],
        asks=[(float(p), float(s)) for p, s, *_ in book["asks"]],
        closes=[float(c[4]) for c in candles[-HISTORY:]],
        open_24h=float(stats["open"]),
        high_24h=float(stats["high"]),
        low_24h=float(stats["low"]),
        volume_24h=float(stats["volume"]),
        time=datetime.now(timezone.utc),
    )


def kraken() -> Market:
    def result(endpoint: str):
        body = _get(f"https://api.kraken.com/0/public/{endpoint}")
        if body.get("error"):
            raise RuntimeError(body["error"])
        return next(v for k, v in body["result"].items() if k != "last")

    book = result("Depth?pair=XBTUSD&count=500")
    candles = result("OHLC?pair=XBTUSD&interval=1")                # [t, o, h, l, close, vwap, vol, n]
    tick = result("Ticker?pair=XBTUSD")
    return Market(
        source="KRAKEN",
        bids=[(float(p), float(s)) for p, s, _ in book["bids"]],
        asks=[(float(p), float(s)) for p, s, _ in book["asks"]],
        closes=[float(c[4]) for c in candles[-HISTORY:]],
        open_24h=float(tick["o"]),
        high_24h=float(tick["h"][1]),
        low_24h=float(tick["l"][1]),
        volume_24h=float(tick["v"][1]),
        time=datetime.now(timezone.utc),
    )


def demo() -> Market:
    """A synthetic but plausible book, for working on the design offline."""
    rng = random.Random(21)
    mid, closes = 81_500.0, []
    for _ in range(HISTORY):
        closes.append(mid)
        mid *= math.exp(rng.gauss(0, 0.0007))

    def side(sign: int) -> list[tuple[float, float]]:
        levels = []
        for i in range(4000):
            p = round(mid + sign * (0.005 + i * 0.6 + rng.random() * 0.5), 2)
            levels.append((p, rng.lognormvariate(-2.2, 1.4) * (1 + i / 900)))
        for _ in range(5):                                         # walls and round numbers
            j = rng.randrange(150, 3000)
            levels[j] = (levels[j][0], rng.uniform(15, 60))
        return levels

    return Market("DEMO", side(-1), side(+1), closes, closes[0] * 1.012,
                  max(closes) * 1.01, min(closes) * 0.99, 4_812.5, datetime.now(timezone.utc))


def load_market(offline: bool) -> Market:
    if offline:
        return demo()
    errors = []
    for fetch in (coinbase, kraken):
        try:
            return fetch()
        except Exception as exc:                                   # noqa: BLE001 - try the next venue
            errors.append(f"{fetch.__name__}: {exc}")
    raise SystemExit("no market data · " + " · ".join(errors))


# ═══ microstructure ═════════════════════════════════════════════════════════

@dataclass
class Bucket:
    x0: float                            # left edge in px on the fixed price axis
    kind: str = ""                       # "bid" / "ask": never crossed by the mid; "zone": crossed
    volume: float = 0.0
    top: tuple[float, float] = (0.0, 0.0)   # largest single level (price, size)
    h: float = 0.0                       # column height in px

    @property
    def xc(self) -> float:
        return self.x0 + BW / 2


class Scene:
    """Everything the renderer needs, derived once from a market snapshot.

    The book is read as one side-agnostic liquidity landscape: what rests below
    the mid is bid, what rests above it is ask. Replaying the hour moves only
    the mid, so the side of every level the mid crosses flips with it.
    """

    def __init__(self, m: Market):
        self.m = m
        (bb, qb), (ba, qa) = m.bids[0], m.asks[0]
        self.mid = (bb + ba) / 2
        self.spread = ba - bb
        self.microprice = (bb * qa + ba * qb) / (qa + qb)

        # the replay: one mid per minute, oldest first, ending on the live mid
        self.prices = m.closes[-HISTORY:] + [self.mid]
        lo, hi = min(self.prices), max(self.prices)
        self.center = math.sqrt(lo * hi)                            # axis centred on the hour's range
        half = math.log(hi / lo) / 2
        self.R = min(0.03, max(0.008, math.ceil(2.6 * half / 0.0025) * 0.0025))
        self.px = HALF_W / self.R                                   # px per unit of log-price
        self.track = [self.x(p) for p in self.prices]               # where the mid stands, minute by minute

        rets = [math.log(b / a) for a, b in itertools.pairwise(self.prices)]
        mean = sum(rets) / len(rets)
        self.sigma = math.sqrt(sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1) * 525_600)

        self.buckets = self._bucketise()
        zone_lo, zone_hi = min(self.track), max(self.track)
        for b in self.buckets:
            b.kind = "bid" if b.x0 + BW <= zone_lo else "ask" if b.x0 >= zone_hi else "zone"
        plotted = [b for b in self.buckets if PAD <= b.xc <= W - PAD]
        vref = max(b.volume for b in plotted) or 1.0
        for b in self.buckets:
            b.h = HMAX * min(1.0, shape(b.volume / vref))
        self.edges = list(itertools.accumulate((b.volume for b in self.buckets), initial=0.0))

        # walls: the largest columns the replayed mid never touches, so they keep their side
        self.walls = {}
        for side in ("bid", "ask"):
            pool = [b for b in plotted if b.kind == side and b.volume > 0 and PAD + 60 <= b.xc <= W - PAD - 60]
            if pool:
                self.walls[side] = max(pool, key=lambda b: b.volume)

        # depth either side of the moving mid, within ±R
        levels = sorted(m.bids + m.asks)
        self._prices = [p for p, _ in levels]
        self._cum = list(itertools.accumulate((s for _, s in levels), initial=0.0))
        self.depth = [self.depth_at(p) for p in self.prices]        # [(bid, ask)] per minute

    def x(self, price: float) -> float:
        return CX + math.log(price / self.center) * self.px

    def cumulative(self, x: float) -> float:
        """Liquidity resting left of pixel x, read off the bucket edges."""
        j = min(max(x / BW, 0.0), len(self.buckets))
        i = min(int(j), len(self.buckets) - 1)
        return self.edges[i] + (j - i) * self.buckets[i].volume

    def depth_at(self, mid: float) -> tuple[float, float]:
        def total(lo: float, hi: float) -> float:
            return self._cum[bisect.bisect_left(self._prices, hi)] - self._cum[bisect.bisect_right(self._prices, lo)]
        lo, hi = mid * math.exp(-self.R), mid * math.exp(self.R)
        return total(lo, mid), total(mid, hi)

    def _bucketise(self) -> list[Bucket]:
        out = [Bucket(j * BW) for j in range(math.ceil(W / BW))]
        for levels, beyond in ((self.m.bids, lambda x: x < 0), (self.m.asks, lambda x: x >= W)):
            for price, size in levels:                              # sorted outward from the touch
                x = self.x(price)
                if beyond(x):
                    break
                if not 0 <= x < W:
                    continue
                b = out[int(x // BW)]
                b.volume += size
                if size > b.top[1]:
                    b.top = (price, size)
        return out


def shape(u: float) -> float:
    """Log-like height curve: walls stand tall without flattening the rest."""
    return math.log1p(90 * u) / math.log1p(90)


# ═══ svg primitives ═════════════════════════════════════════════════════════

def f(v: float) -> str:
    """Compact number: one decimal, no trailing zeros, no leading zero."""
    s = f"{v:.1f}".rstrip("0").rstrip(".")
    return s.replace("0.", ".", 1) if s.startswith(("0.", "-0.")) else (s if s != "-0" else "0")


def t(v: float) -> str:
    """A SMIL keyTime."""
    return f"{v:.5f}".rstrip("0").rstrip(".") or "0"


def text(x, y, s, size, fill=INK, anchor="start", weight=400, ls=0.0, family=MONO, extra="") -> str:
    attrs = [f'x="{f(x)}"', f'y="{f(y)}"', f'font-size="{size}"', f'fill="{fill}"']
    if anchor != "start":
        attrs.append(f'text-anchor="{anchor}"')
    if weight != 400:
        attrs.append(f'font-weight="{weight}"')
    if ls:
        attrs.append(f'letter-spacing="{ls}"')
    if family != MONO:
        attrs.append(f'font-family="{family}"')
    return f'<text {" ".join(attrs)}{extra}>{s}</text>'


def mono_width(s: str, size: float, ls: float = 0.0) -> float:
    """JetBrains Mono advances exactly 0.6 em per glyph."""
    return len(s) * (0.6 * size + ls)


def dots(points: list[tuple[float, float]]) -> str:
    """Thousands of particles as one path of round-capped, near-zero-length strokes."""
    if not points:
        return ""
    cells = sorted((round(x * 10), round(y * 10)) for x, y in points)
    parts, (px, py) = [f"M{f(cells[0][0] / 10)} {f(cells[0][1] / 10)}h.1"], cells[0]
    for cx, cy in cells[1:]:
        dx, dy = (cx - px - 1) / 10, (cy - py) / 10                 # h.1 moved us 0.1 right
        parts.append(f"m{f(dx)} {f(dy)}h.1")
        px, py = cx, cy
    return "".join(parts)


def money(p: float, decimals: int = 2) -> str:
    return f"{p:,.{decimals}f}"


def signed(v: float, fmt: str) -> str:
    return format(v, fmt).replace("-", "−")


def twinkle_name(alpha: float) -> str:
    return f"tw{round(alpha * 100)}"


def twinkle(alpha: float, dur: float, delay: float = 0.0) -> str:
    return f"animation:{twinkle_name(alpha)} {dur:.2f}s ease-in-out -{delay:.2f}s infinite"


def font_face(family: str, file: str, weight: int) -> str:
    data = base64.b64encode((FONTS / file).read_bytes()).decode()
    return (f"@font-face{{font-family:{family};font-weight:{weight};"
            f"src:url(data:font/woff2;base64,{data}) format('woff2')}}")


def smooth(values: list[float], radius: int) -> list[float]:
    kernel = [math.exp(-0.5 * (i / (radius / 2)) ** 2) for i in range(-radius, radius + 1)]
    out = []
    for i in range(len(values)):
        acc = wsum = 0.0
        for j, w in enumerate(kernel):
            k = i + j - radius
            if 0 <= k < len(values):
                acc += values[k] * w
                wsum += w
        out.append(acc / wsum)
    return out


# ═══ scene → svg ════════════════════════════════════════════════════════════

class Renderer:
    def __init__(self, scene: Scene, seed: int = 7):
        self.s = scene
        self.rng = random.Random(seed)
        n = len(scene.prices)
        self.kt = [REPLAY_END / 100 * i / (n - 1) for i in range(n)]   # keyTime of each minute
        self.clips = 0
        self.labels: list[tuple[float, float, float, float]] = []      # boxes the rain must not cross

    # ── the replay clock ────────────────────────────────────────────────────
    def replay(self, attr: str, values: list[str], translate: bool = False) -> str:
        """Glide through one value per minute, hold on NOW, then ease back for the rewind."""
        kt = self.kt + [HOLD_END / 100, 1.0]
        vals = values + [values[-1], values[0]]
        splines = ["0 0 1 1"] * (len(kt) - 2) + [".7 0 .3 1"]
        return self._smil(attr, vals, kt, "spline", translate, splines)

    def steps(self, attr: str, values: list[str], translate: bool = False, rewind: str | None = None) -> str:
        """Jump to one value per minute (and optionally a rewind value) without interpolating."""
        kt = self.kt + ([HOLD_END / 100] if rewind is not None else [])
        vals = values + ([rewind] if rewind is not None else [])
        return self._smil(attr, vals, kt, "discrete", translate)

    @staticmethod
    def _smil(attr, values, key_times, mode, translate, splines=None) -> str:
        tag = "animateTransform" if translate else "animate"
        extra = ' type="translate"' if translate else ""
        if splines:
            extra += f' keySplines="{";".join(splines)}"'
        return (f'<{tag} attributeName="{attr}"{extra} dur="{LOOP}s" repeatCount="indefinite" calcMode="{mode}" '
                f'keyTimes="{";".join(t(k) for k in key_times)}" values="{";".join(values)}"/>')

    def ticker(self, lines: list[str], window: tuple[float, float, float, float], lh: float,
               rewind_line: str | None = None, attrs: str = "") -> str:
        """Stacked text lines behind a fixed window, stepping one line per minute."""
        x, y, w, h = window
        self.clips += 1
        cid = f"tk{self.clips}"
        rows = lines + ([rewind_line] if rewind_line is not None else [])
        values = [f"0 {f(-i * lh)}" for i in range(len(lines))]
        rewind = f"0 {f(-len(lines) * lh)}" if rewind_line is not None else None
        return (f'<clipPath id="{cid}"><rect x="{f(x)}" y="{f(y)}" width="{f(w)}" height="{f(h)}"/></clipPath>'
                f'<g clip-path="url(#{cid})"><g{attrs}>{"".join(rows)}'
                f'{self.steps("transform", values, translate=True, rewind=rewind)}</g></g>')

    # ── assembly ────────────────────────────────────────────────────────────
    def svg(self) -> str:
        s = self.s
        walls = " and ".join(f"{side} wall {money(w.top[0])}" for side, w in s.walls.items()) or "no walls"
        return "\n".join([
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
            f'font-family="{MONO}" role="img" aria-labelledby="t d">',
            '<title id="t">GRZ_Research · live BTC-USD limit order book</title>',
            f'<desc id="d">Live {s.m.source} level-2 order book for BTC-USD rendered as a particle field on a '
            f'fixed price axis; mid {money(s.mid)} USD, {walls}. The mid replays the last {HISTORY} minutes: '
            f'liquidity below it is bid, above it ask.</desc>',
            self.defs(),
            self.backdrop(),
            '<g clip-path="url(#field)">',
            self.dust(),
            self.grid(),
            self.book(),
            self.edge_fades(),
            "</g>",
            self.axis(),
            self.header(),
            self.headline(),
            self.stats(),
            f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="18" fill="none" stroke="#1B2333"/>',
            "</svg>",
        ])

    # ── defs: fonts, keyframes, gradients, the moving bid/ask boundary ──────
    def defs(self) -> str:
        track = [f(x) for x in self.s.track]
        # stroke-opacity, not opacity: animating opacity forces an offscreen layer per path per frame
        twinkles = "".join(f"@keyframes {twinkle_name(a)}{{0%,100%{{stroke-opacity:{a:g}}} 50%{{stroke-opacity:{a * .35:.3f}}}}}"
                           for a in TWINKLE)
        css = f"""
{font_face("JBM", "jbm-400.woff2", 400)}
{font_face("JBM", "jbm-700.woff2", 700)}
{font_face("IDL", "inter-300.woff2", 300)}
{twinkles}
@keyframes fall{{0%{{transform:translateY(var(--d,-120px));opacity:0}} 18%{{opacity:1}} 90%{{opacity:.75}} 100%{{transform:translateY(0);opacity:0}}}}
@keyframes rise{{from{{transform:translateY(0)}} to{{transform:translateY(-26px)}}}}
@keyframes pulse{{0%,100%{{opacity:.25}} 50%{{opacity:1}}}}
@keyframes blink{{0%,55%{{opacity:1}} 56%,100%{{opacity:0}}}}
@keyframes spark{{0%{{opacity:0;transform:translate(0,0)}} 12%{{opacity:1}} 100%{{opacity:0;transform:translate(var(--sx),var(--sy))}}}}
.fall{{animation:fall 2.4s cubic-bezier(.55,0,1,.45) infinite}}
.spark{{animation:spark 1.8s cubic-bezier(.1,.6,.3,1) infinite}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important}}}}
"""
        grads = []
        for name, (near, base, _) in (("bid", BID), ("ask", ASK)):
            grads.append(f'<linearGradient id="haze-{name}" x1="0" y1="0" x2="0" y2="1">'
                         f'<stop offset="0" stop-color="{base}" stop-opacity=".16"/>'
                         f'<stop offset="1" stop-color="{base}" stop-opacity="0"/></linearGradient>')
            grads.append(f'<linearGradient id="drop-{name}" x1="0" y1="0" x2="0" y2="1">'
                         f'<stop offset="0" stop-color="{base}" stop-opacity="0"/>'
                         f'<stop offset="1" stop-color="{near}"/></linearGradient>')
            grads.append(f'<linearGradient id="wall-{name}" x1="0" y1="0" x2="0" y2="1">'
                         f'<stop offset="0" stop-color="{near}" stop-opacity=".0"/>'
                         f'<stop offset=".25" stop-color="{base}" stop-opacity=".22"/>'
                         f'<stop offset="1" stop-color="{base}" stop-opacity=".05"/></linearGradient>')
            grads.append(f'<radialGradient id="glow-{name}"><stop offset="0" stop-color="{base}" stop-opacity=".11"/>'
                         f'<stop offset="1" stop-color="{base}" stop-opacity="0"/></radialGradient>')
        return f"""<defs>
<style>{css}</style>
<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{BG1}"/><stop offset="1" stop-color="{BG0}"/></linearGradient>
<radialGradient id="glow-mid"><stop offset="0" stop-color="{BTC}" stop-opacity=".12"/><stop offset="1" stop-color="{BTC}" stop-opacity="0"/></radialGradient>
<radialGradient id="beam-glow"><stop offset="0" stop-color="{BTC}" stop-opacity=".38"/><stop offset=".45" stop-color="{BTC}" stop-opacity=".1"/><stop offset="1" stop-color="{BTC}" stop-opacity="0"/></radialGradient>
<radialGradient id="heat"><stop offset="0" stop-color="#FFE9CC" stop-opacity=".2"/><stop offset="1" stop-color="#FFE9CC" stop-opacity="0"/></radialGradient>
<linearGradient id="beam" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{BTC}" stop-opacity="0"/><stop offset=".55" stop-color="{BTC}"/><stop offset="1" stop-color="#FFE3BF"/></linearGradient>
<linearGradient id="edge-l" x1="0" y1="0" x2="1" y2="0"><stop offset=".2" stop-color="#fff"/><stop offset="1" stop-color="#000"/></linearGradient>
<linearGradient id="edge-r" x1="1" y1="0" x2="0" y2="0"><stop offset=".2" stop-color="#fff"/><stop offset="1" stop-color="#000"/></linearGradient>
<mask id="fade-l" maskUnits="userSpaceOnUse" x="0" y="0" width="{EDGE}" height="{H}"><rect width="{EDGE}" height="{H}" fill="url(#edge-l)"/></mask>
<mask id="fade-r" maskUnits="userSpaceOnUse" x="{W - EDGE}" y="0" width="{EDGE}" height="{H}"><rect x="{W - EDGE}" width="{EDGE}" height="{H}" fill="url(#edge-r)"/></mask>
{"".join(grads)}
<pattern id="dotgrid" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="12" cy="12" r=".7" fill="#1A2232"/></pattern>
<clipPath id="field"><rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="17"/></clipPath>
<clipPath id="plot"><rect y="{BASE - HMAX - 14}" width="{W}" height="{HMAX + 15}"/></clipPath>
<clipPath id="below-mid"><rect width="{track[0]}" height="{H}">{self.replay("width", track)}</rect></clipPath>
<clipPath id="above-mid"><rect x="{track[0]}" width="{W}" height="{H}">{self.replay("x", track)}</rect></clipPath>
</defs>"""

    # ── backdrop ────────────────────────────────────────────────────────────
    def backdrop(self) -> str:
        return (f'<g id="backdrop"><rect width="{W}" height="{H}" rx="18" fill="url(#bg)"/>'
                f'<rect width="{W}" height="{H}" rx="18" fill="url(#dotgrid)" opacity=".8"/>'
                f'<ellipse cx="{CX}" cy="{BASE}" rx="520" ry="250" fill="url(#glow-mid)"/>'
                f'<ellipse cx="{CX - 330}" cy="{BASE - 20}" rx="420" ry="230" fill="url(#glow-bid)"/>'
                f'<ellipse cx="{CX + 330}" cy="{BASE - 20}" rx="420" ry="230" fill="url(#glow-ask)"/></g>')

    def edge_fades(self) -> str:
        """Dissolve the book into the card edges by repainting the backdrop over thin
        strips; far cheaper than masking the whole animated book every frame."""
        return '<use href="#backdrop" mask="url(#fade-l)"/><use href="#backdrop" mask="url(#fade-r)"/>'

    def dust(self) -> str:
        out = []
        for i, (size, alpha, n) in enumerate(((0.9, 0.22, 140), (1.4, 0.14, 90), (2.2, 0.07, 40))):
            pts = [(self.rng.uniform(0, W), self.rng.uniform(150, H - 60)) for _ in range(n)]
            out.append(f'<path d="{dots(pts)}" stroke="#9DB2D3" stroke-opacity="{alpha}" stroke-width="{size}" '
                       f'stroke-linecap="round" style="animation:rise {22 + 7 * i}s ease-in-out -{4 * i}s infinite alternate"/>')
        return "".join(out)

    def grid(self) -> str:
        out = [f'<line x1="{PAD}" x2="{W - PAD}" y1="{BASE}" y2="{BASE}" stroke="#2A3346"/>']
        for frac in (0.25, 0.5, 0.75, 1.0):
            y = BASE - HMAX * frac
            out.append(f'<line x1="{PAD}" x2="{W - PAD}" y1="{f(y)}" y2="{f(y)}" stroke="{LINE}" stroke-dasharray="1 7"/>')
        for _, x in self.ticks():
            out.append(f'<line x1="{f(x)}" x2="{f(x)}" y1="{BASE - HMAX}" y2="{BASE}" stroke="{LINE}" stroke-dasharray="2 6"/>')
        return "".join(out)

    # ── the book: haze, depth, particles, walls, flow, mid ──────────────────
    def book(self) -> str:
        return "".join([self.haze(), self.depth_lines(), self.particles(), self.walls(), self.rain(), self.mid()])

    @staticmethod
    def sided(bid: str, ask: str) -> str:
        """Content split by the moving mid: `bid` shows below it, `ask` above it."""
        return f'<g clip-path="url(#below-mid)">{bid}</g><g clip-path="url(#above-mid)">{ask}</g>'

    def haze(self) -> str:
        bs = self.s.buckets
        hs = smooth([b.h for b in bs], 5)
        d = ("M0 " + str(BASE) + "L" + "L".join(f"{f(b.xc)} {f(BASE - h * 0.9)}" for b, h in zip(bs, hs))
             + f"L{W} {BASE}Z")
        return self.sided(f'<path d="{d}" fill="url(#haze-bid)"/>', f'<path d="{d}" fill="url(#haze-ask)"/>')

    def depth_lines(self) -> str:
        """Cumulative depth out from the moving mid: the classic exchange depth chart.

        Depth at x is |F(x) − F(mid)| with F the liquidity left of x, so each side is
        one static curve that only slides vertically as the mid moves.
        """
        s = self.s
        zone_lo, zone_hi = min(s.track), max(s.track)
        scale = 0.92 * HMAX / max(s.cumulative(zone_hi) - s.cumulative(PAD),
                                  s.cumulative(W - PAD) - s.cumulative(zone_lo), 1e-9)
        lines = {}
        for name, sign, (lo, hi) in (("bid", 1, (0, zone_hi)), ("ask", -1, (zone_lo, W))):
            xs = [b.x0 for b in s.buckets if lo - BW <= b.x0 <= hi] + [min(hi, W)]
            d = "M" + "L".join(f"{f(x)} {f(sign * scale * s.cumulative(x))}" for x in xs)
            lift = [f"0 {f(BASE - sign * scale * s.cumulative(x))}" for x in s.track]
            color = (BID if name == "bid" else ASK)[1]
            lines[name] = (f'<g clip-path="url(#plot)"><g>'
                           f'<path d="{d}" fill="none" stroke="{color}" stroke-opacity=".10" stroke-width="6" stroke-linejoin="round"/>'
                           f'<path d="{d}" fill="none" stroke="{color}" stroke-opacity=".55" stroke-width="1.1" '
                           f'stroke-linejoin="round" stroke-dasharray="3 3"/>'
                           f'{self.replay("transform", lift, translate=True)}</g></g>')
        return self.sided(lines["bid"], lines["ask"])

    def particles(self) -> str:
        rng, s = self.rng, self.s
        density = PARTICLES / sum(b.h for b in s.buckets)
        layers: dict[tuple, list] = {}
        caps: dict[str, list] = {}
        for b in s.buckets:
            if b.volume <= 0:
                continue
            for _ in range(max(1, round(b.h * density))):
                u = rng.random()
                x, y = b.x0 + rng.random() * BW, BASE - 1.2 - (b.h - 1.2) * u ** 0.9
                tier = 0 if u > 0.8 else 2 if u < 0.3 and rng.random() < 0.5 else 1   # column tops glow
                layers.setdefault((b.kind, tier, rng.random() < 0.16, rng.randrange(3)), []).append((x, y))
            if b.h > 3:
                caps.setdefault(b.kind, []).append((b.xc, BASE - b.h))

        def paint(kind: str, side: str) -> str:
            palette, out = BID if side == "bid" else ASK, []
            for (k, tier, coarse, group), pts in sorted(layers.items()):
                if k != kind:
                    continue
                width, alpha = (2.3, 0.42) if coarse else (1.25, TWINKLE[1 + tier])
                dur = 2.6 + 1.1 * group + 0.4 * tier + (tier + group) % 3 * 0.37
                out.append(f'<path d="{dots(pts)}" stroke="{palette[tier]}" stroke-opacity="{alpha:g}" '
                           f'stroke-width="{width}" stroke-linecap="round" style="{twinkle(alpha, dur, dur * group / 3)}"/>')
            if caps.get(kind):
                out.append(f'<path d="{dots(caps[kind])}" stroke="{palette[0]}" stroke-width="1.7" '
                           f'stroke-linecap="round" style="{twinkle(1.0, 3.3)}"/>')
            return "".join(out)

        return paint("bid", "bid") + paint("ask", "ask") + self.sided(paint("zone", "bid"), paint("zone", "ask"))

    def walls(self) -> str:
        rng, out = self.rng, []
        for name, wall in self.s.walls.items():
            near, base, _ = BID if name == "bid" else ASK
            x, top = wall.xc, BASE - wall.h
            core = [(x + rng.gauss(0, 1.1), BASE - wall.h * rng.random() ** 0.8) for _ in range(int(wall.h * 1.6))]
            halo = [(x + rng.gauss(0, 4.5), BASE - wall.h * rng.random() ** 1.2) for _ in range(int(wall.h * 0.5))]
            out.append(f'<rect x="{f(x - 9)}" y="{f(top - 30)}" width="18" height="{f(wall.h + 30)}" fill="url(#wall-{name})" '
                       f'style="animation:pulse 3.6s ease-in-out infinite"/>')
            out.append(f'<path d="{dots(halo)}" stroke="{base}" stroke-opacity=".35" stroke-width="2.6" stroke-linecap="round" '
                       f'style="{twinkle(0.35, 2.9, 1)}"/>')
            out.append(f'<path d="{dots(core)}" stroke="{near}" stroke-width="1.5" stroke-linecap="round" '
                       f'style="{twinkle(1.0, 2.1)}"/>')
            out.append(self.wall_label(name, wall))
        return "".join(out)

    def wall_label(self, name: str, wall: Bucket) -> str:
        """Price and size, plus the wall's distance from the replayed mid, ticking each minute."""
        near, base, _ = BID if name == "bid" else ASK
        x, top = wall.xc, BASE - wall.h
        title, ts, tls = f"{name.upper()} WALL", 12, 1.5
        dists = [signed((wall.top[0] / p - 1) * 100, "+.2f") + "%" for p in self.s.prices]
        head = title + "  " + dists[0]
        value_plain = f"{money(wall.top[0])} · {wall.volume:,.1f} BTC"
        width = max(mono_width(head, ts, tls), mono_width(value_plain, 15))
        outward = -1 if name == "bid" else 1
        if (outward < 0 and x - 14 - width < PAD) or (outward > 0 and x + 14 + width > W - PAD):
            outward = -outward                                      # keep the label on the canvas
        left = x + 14 if outward > 0 else x - 14 - width
        ty = top - 22
        dx = left + mono_width(title + "  ", ts, tls)
        lines = [text(dx, ty - 14 + i * 18, d, ts, MUTED, weight=700, ls=tls) for i, d in enumerate(dists)]
        box = (left - 6, ty - 30, left + width + 6, ty + 9)
        self.labels.append(box)
        return (f'<rect x="{f(box[0])}" y="{f(box[1])}" width="{f(box[2] - box[0])}" height="{f(box[3] - box[1])}" '
                f'rx="5" fill="{BG0}" opacity=".72"/>'
                f'<path d="M{f(x)} {f(top - 4)}V{f(ty - 4)}h{f(outward * 8)}" fill="none" stroke="{base}" stroke-opacity=".7"/>'
                f'<circle cx="{f(x)}" cy="{f(top)}" r="2.6" fill="{near}"/>'
                + text(left, ty - 14, title, ts, base, weight=700, ls=tls)
                + self.ticker(lines, (dx - 2, ty - 28, mono_width(dists[0], ts, tls) + 6, 18), 18)
                + text(left, ty + 4, f'{money(wall.top[0])} <tspan fill="{MUTED}">· {wall.volume:,.1f} BTC</tspan>', 15, INK))

    def rain(self) -> str:
        """Fresh limit orders falling onto levels that keep their side all hour."""
        rng, out = self.rng, []
        pool = [b for b in self.s.buckets if b.volume > 0 and b.kind != "zone" and PAD <= b.xc <= W - PAD]
        if not pool:
            return ""
        for b in rng.choices(pool, [b.volume ** 0.6 for b in pool], k=RAIN):
            land = BASE - b.h
            drop = min(110, land - (BASE - HMAX + 8))          # never above the field: labels live there
            if drop < 24:
                continue
            x = b.x0 + rng.random() * BW
            if any(x0 - 4 <= x <= x1 + 4 and land - drop - 11 <= y1 and land >= y0 for x0, y0, x1, y1 in self.labels):
                continue
            dur = rng.uniform(1.8, 3.6)
            out.append(f'<rect class="fall" x="{f(x - 0.5)}" y="{f(land - 11)}" width="1" height="11" '
                       f'fill="url(#drop-{b.kind})" style="--d:-{f(drop)}px;animation-duration:{dur:.2f}s;'
                       f'animation-delay:-{rng.random() * dur:.2f}s"/>')
        return "".join(out)

    def mid(self) -> str:
        """The mid price: a BTC-orange beam that walks the hour across the fixed axis."""
        s, top = self.s, BASE - HMAX - 12
        tag_y, tag_w = top - 26, 86
        ages = [f'−{len(s.prices) - 1 - i}m' for i in range(len(s.prices) - 1)]
        lines = [text(CX, tag_y + 16 + i * 20, a, 13, MUTED, "middle") for i, a in enumerate(ages)]
        lines.append(text(CX, tag_y + 16 + len(ages) * 20, "NOW", 13, BTC, "middle", 700, 1))
        rewind = text(CX, tag_y + 16 + len(s.prices) * 20, "◂◂ 60m", 13, MUTED, "middle")
        glow = self.replay("opacity", [".55"] * (len(s.prices) - 1) + ["1"])
        walk = self.replay("transform", [f"{f(x - CX)} 0" for x in s.track], translate=True)
        return (f'<g><ellipse cx="{CX}" cy="{f((top + BASE) / 2)}" rx="30" ry="{f((BASE - top) / 2 + 30)}" '
                f'fill="url(#beam-glow)" opacity=".55">{glow}</ellipse>'
                f'<ellipse cx="{CX}" cy="{BASE - 6}" rx="80" ry="28" fill="url(#heat)"/>'
                f'<rect x="{CX - 0.8}" y="{top}" width="1.6" height="{BASE - top}" fill="url(#beam)"/>'
                f'<rect x="{CX - tag_w / 2}" y="{tag_y}" width="{tag_w}" height="23" rx="11.5" fill="#0B111C" '
                f'stroke="{BTC}" stroke-opacity=".55"/>'
                + self.ticker(lines, (CX - tag_w / 2, tag_y, tag_w, 23), 20, rewind)
                + f'<path d="M{CX} {BASE - 5}l5 5-5 5-5-5z" fill="{BTC}"/>'
                + self.sparks() + walk + "</g>")

    def sparks(self) -> str:
        """Executions at the touch, spraying into both sides of the book."""
        rng, out = self.rng, []
        for i in range(SPARKS):
            side = -1 if i % 2 else 1
            near = (BID if side < 0 else ASK)[0]
            dur = rng.uniform(1.2, 2.4)
            out.append(f'<circle class="spark" cx="{CX}" cy="{BASE - 2}" r="{rng.choice((0.9, 1.2, 1.6))}" fill="{near}" '
                       f'style="--sx:{f(side * rng.uniform(25, 95))}px;--sy:{f(-rng.uniform(0, 34))}px;'
                       f'animation-duration:{dur:.2f}s;animation-delay:-{rng.random() * dur:.2f}s"/>')
        return "".join(out)

    # ── axis ────────────────────────────────────────────────────────────────
    def ticks(self) -> list[tuple[float, float]]:
        s = self.s
        lo, hi = s.center * math.exp(-s.R), s.center * math.exp(s.R)
        step = next(st for st in (10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000)
                    if (hi - lo) / st <= 10)
        out, p = [], math.ceil(lo / step) * step
        while p <= hi:
            x = s.x(p)
            if PAD + 215 < x < W - PAD - 215:                     # corners carry the side labels
                out.append((p, x))
            p += step
        return out

    def axis(self) -> str:
        y, ticks = BASE + 24, self.ticks()
        out = [text(x, y, money(p, 0), 12, MUTED, "middle") for p, x in ticks]
        out.append('<g stroke="#2A3346">'
                   + "".join(f'<line x1="{f(x)}" x2="{f(x)}" y1="{BASE}" y2="{BASE + 6}"/>' for _, x in ticks)
                   + "</g>")
        out.append(text(PAD, y, "◂ BIDS · LIMIT BUY", 12, BID[1], weight=700, ls=1.2))
        out.append(text(W - PAD, y, "LIMIT SELL · ASKS ▸", 12, ASK[1], "end", 700, 1.2))
        return "".join(out)

    # ── header & headline ───────────────────────────────────────────────────
    def header(self) -> str:
        s = self.s
        stamp = s.m.time.strftime("%d %b %Y · %H:%M UTC").upper()
        name, size, track = "GRZ_RESEARCH", 30, 2.5
        cursor = PAD + mono_width(name, size, track) + 6
        return "".join([
            text(PAD, 66, name, size, INK, weight=700, ls=track),
            f'<rect x="{f(cursor)}" y="42" width="13" height="28" fill="{BTC}" style="animation:blink 1.1s step-end infinite"/>',
            text(PAD, 96, f'M.Sc. Finance <tspan fill="{BTC}">·</tspan> AI / ML Specialist', 15, "#C5CEDA"),
            text(PAD, 120, f'DeFi <tspan fill="{BTC}">·</tspan> Market Microstructure <tspan fill="{BTC}">·</tspan> '
                           f'Exotic Derivatives Research', 15, MUTED),
            f'<circle cx="{W - PAD - 132}" cy="57" r="4.5" fill="{BID[1]}" style="animation:pulse 1.6s ease-in-out infinite"/>',
            text(W - PAD - 120, 62, "LIVE", 14, BID[1], weight=700, ls=2),
            text(W - PAD, 62, "BTC-USD", 14, INK, "end", 700, 1),
            text(W - PAD, 84, f"{s.m.source} L2 · {stamp}", 12, MUTED, "end", ls=0.5),
            self.sparkline(),
        ])

    def sparkline(self) -> str:
        s = self.s
        p, n = s.prices, len(s.prices)
        lo, hi = min(p), max(p)
        span = (hi - lo) or 1
        ox, floor = W - PAD - SPARK_W, 132
        pts = [(ox + SPARK_W * i / (n - 1), floor - SPARK_H * (v - lo) / span) for i, v in enumerate(p)]
        up = p[-1] >= p[0]
        color = BID[1] if up else ASK[1]
        line = "M" + "L".join(f"{f(x)} {f(y)}" for x, y in pts)
        area = line + f"L{f(pts[-1][0])} {floor}L{f(pts[0][0])} {floor}Z"
        change = (p[-1] / p[0] - 1) * 100
        x0, y0 = pts[0]
        moves = self.replay("transform", [f"{f(x - x0)} {f(y - y0)}" for x, y in pts], translate=True)
        fade = (f'<animate attributeName="opacity" dur="{LOOP}s" repeatCount="indefinite" values="1;1;0;0;1" '
                f'keyTimes="0;{t(HOLD_END / 100)};{t(HOLD_END / 100 + 0.012)};.99;1"/>')
        return (f'<linearGradient id="sl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{color}" stop-opacity=".25"/>'
                f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient>'
                f'<path d="{area}" fill="url(#sl)"/>'
                f'<path d="{line}" fill="none" stroke="{color}" stroke-width="1.4" stroke-linejoin="round"/>'
                + text(ox - 10, floor - 2, f'60M <tspan fill="{color}">{signed(change, "+.2f")}%</tspan>', 11, MUTED, "end", ls=0.8)
                + f'<g transform="translate({f(x0)} {f(y0)})"><g>'
                  f'<circle r="7" fill="{BTC}" opacity=".25"/><circle r="2.8" fill="#FFF3E0"/>{moves}{fade}</g></g>')

    def headline(self) -> str:
        """The replayed mid: it snaps to each new print, then spins back through the hour on rewind."""
        s = self.s
        change = (s.mid / s.m.open_24h - 1) * 100
        color = BID[1] if change >= 0 else ASK[1]
        arrow = "▲" if change >= 0 else "▼"
        base, lh, snap = 206, 70, 0.004 / LOOP
        n = len(s.prices)
        kt, values, splines = [0.0], ["0 0"], []
        for i in range(1, n):
            kt += [self.kt[i], self.kt[i] + snap]
            values += [f"0 {-(i - 1) * lh}", f"0 {-i * lh}"]
            splines += ["0 0 1 1", "0 0 1 1"]
        kt += [HOLD_END / 100, 1.0]
        values += [f"0 {-(n - 1) * lh}", "0 0"]
        splines += ["0 0 1 1", ".6 0 .4 1"]
        lines = "".join(f'<text x="{CX}" y="{base + i * lh}">{money(p)}</text>' for i, p in enumerate(s.prices))
        return "".join([
            text(CX, 152, "BTC / USD", 12, MUTED, "middle", ls=5),
            f'<clipPath id="quote"><rect x="{CX - 330}" y="{base - 52}" width="660" height="68"/></clipPath>'
            f'<g clip-path="url(#quote)"><g font-family="{DISPLAY}" font-size="58" font-weight="300" fill="#F4F7FB" '
            f'text-anchor="middle" letter-spacing="1" style="font-variant-numeric:tabular-nums">{lines}'
            + self._smil("transform", values, kt, "spline", True, splines) + "</g></g>",
            text(CX, 232, f'<tspan fill="{color}">{arrow} {signed(change, "+.2f")}%</tspan>  24H   '
                          f'<tspan fill="{DIM}">H</tspan> {money(s.m.high_24h, 0)}  <tspan fill="{DIM}">L</tspan> '
                          f'{money(s.m.low_24h, 0)}', 13, MUTED, "middle"),
        ])

    # ── quant strip ─────────────────────────────────────────────────────────
    def stats(self) -> str:
        s = self.s
        band = f"±{s.R * 100:g}%"
        top, cw = BASE + 50, (W - 2 * PAD) / 6
        obi = [(b - a) / max(1e-9, b + a) for b, a in s.depth]

        def ticking(cell: int, values: list[str]) -> str:
            x = PAD + cell * cw + 18
            lines = [text(x, top + 58 + i * 26, v, 19, INK) for i, v in enumerate(values)]
            return self.ticker(lines, (x - 2, top + 38, cw - 24, 26), 26)

        cells = [
            ("MICROPRICE", text(PAD, top + 58, money(s.microprice), 19, INK)),
            ("SPREAD · USD", text(PAD + cw + 18, top + 58, f"{s.spread:,.2f}", 19, INK)),
            (f"IMBALANCE {band}", ticking(2, [f'<tspan fill="{BID[1] if v >= 0 else ASK[1]}">{signed(v, "+.3f")}</tspan>'
                                             for v in obi])),
            ("σ 1H · ANNUALISED", text(PAD + 3 * cw + 18, top + 58, f"{s.sigma * 100:.1f}%", 19, INK)),
            (f"DEPTH {band} · BTC", ticking(4, [f'<tspan fill="{BID[1]}">{b:,.0f}</tspan><tspan fill="{DIM}"> / </tspan>'
                                               f'<tspan fill="{ASK[1]}">{a:,.0f}</tspan>' for b, a in s.depth])),
            ("24H VOLUME · BTC", text(PAD + 5 * cw + 18, top + 58, f"{s.m.volume_24h:,.0f}", 19, INK)),
        ]
        out = [f'<line x1="{PAD}" x2="{W - PAD}" y1="{top}" y2="{top}" stroke="{LINE}"/>']
        for i, (label, value) in enumerate(cells):
            x = PAD + i * cw + (0 if i == 0 else 18)
            if i:
                out.append(f'<line x1="{f(PAD + i * cw)}" x2="{f(PAD + i * cw)}" y1="{top + 18}" y2="{top + 64}" stroke="{LINE}"/>')
            out.append(text(x, top + 30, label, 11.5, "#5E6A7E", ls=1.2, weight=700))
            out.append(value)
        return "".join(out)


SPARK_W, SPARK_H = 168, 30


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("dist/liquidity-field.svg"))
    ap.add_argument("--demo", action="store_true", help="render a synthetic book, no network")
    args = ap.parse_args()

    scene = Scene(load_market(args.demo))
    svg = Renderer(scene).svg()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(svg, encoding="utf-8")
    walls = " · ".join(f"{side} wall {money(w.top[0])} ({w.volume:.1f})" for side, w in scene.walls.items())
    print(f"{scene.m.source} mid {money(scene.mid)} · axis {money(scene.center)} ±{scene.R:.2%} · "
          f"{walls} · {len(svg) / 1024:.0f} KiB → {args.out}")


if __name__ == "__main__":
    main()
