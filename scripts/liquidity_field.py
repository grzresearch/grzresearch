#!/usr/bin/env python3
"""Liquidity Field: the live BTC-USD limit order book, drawn as particles.

Pulls the level-2 order book, the last hour of 1-minute candles and the 24h
stats from Coinbase Exchange (Kraken as fallback) and writes one
self-contained, animated SVG:

  * every particle is a slice of resting liquidity at its price level
  * the tallest column on each side is the bid wall / ask wall
  * the whole book replays the last 60 minutes of BTC in a loop, so both
    walls drift with the price exactly as the mid did

Standard library only, so it runs on a bare GitHub Actions runner.

    python3 scripts/liquidity_field.py --out dist/liquidity-field.svg
    python3 scripts/liquidity_field.py --demo --out preview.svg   # offline
"""

from __future__ import annotations

import argparse
import base64
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
CX = W / 2                      # the mid price lives here at "now"
HALF_W = CX - PAD               # px from mid to the edge of the visible book
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

# ── palette ─────────────────────────────────────────────────────────────────
BG0, BG1 = "#04060B", "#0A0F1A"
INK, MUTED, DIM, LINE = "#E8EDF4", "#8B96A8", "#4A5468", "#182030"
BTC = "#F7931A"
BID = ("#D2FFF4", "#36F1C6", "#12907E")   # near · mid · far from the touch
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
    side: str            # "bid" | "ask"
    k: int               # index, counted outward from the mid
    x0: float            # left edge in px at "now"
    volume: float = 0.0
    top: tuple[float, float] = (0.0, 0.0)   # largest single level (price, size)
    h: float = 0.0       # column height in px

    @property
    def xc(self) -> float:
        return self.x0 + BW / 2


class Scene:
    """Everything the renderer needs, derived once from a market snapshot."""

    def __init__(self, m: Market):
        self.m = m
        (bb, qb), (ba, qa) = m.bids[0], m.asks[0]
        self.mid = (bb + ba) / 2
        self.spread = ba - bb
        self.microprice = (bb * qa + ba * qb) / (qa + qb)

        # the replayed path, as log-distance of each past mid from the mid now
        path = m.closes[-HISTORY:] + [self.mid]
        self.prices = path
        self.path = [math.log(p / self.mid) for p in path]
        swing = max(abs(r) for r in self.path)
        self.R = min(0.03, max(0.008, math.ceil(2.4 * swing / 0.0025) * 0.0025))
        self.px = HALF_W / self.R                                   # px per unit of log-return
        self.shift = [r * self.px for r in self.path]

        rets = [math.log(b / a) for a, b in zip(path, path[1:])]
        mean = sum(rets) / len(rets)
        self.sigma = math.sqrt(sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1) * 525_600)

        self.buckets = self._bucketise(swing)
        visible = [b for b in self.buckets if self.is_visible(b)]
        self.depth = {s: sum(b.volume for b in visible if b.side == s) for s in ("bid", "ask")}
        self.imbalance = (self.depth["bid"] - self.depth["ask"]) / max(1e-9, sum(self.depth.values()))

        vref = max(b.volume for b in visible)
        for b in self.buckets:
            b.h = HMAX * min(1.0, shape(b.volume / vref))
        self.walls = {s: max((b for b in visible if b.side == s), key=lambda b: b.volume)
                      for s in ("bid", "ask")}

    def x(self, price: float) -> float:
        return CX + math.log(price / self.mid) * self.px

    @staticmethod
    def is_visible(b: Bucket) -> bool:
        return b.k * BW < HALF_W

    def _bucketise(self, swing: float) -> list[Bucket]:
        reach = (self.R + swing) * self.px + 2 * PAD                 # cover the replay's travel
        n = int(reach // BW) + 1
        out = {
            "bid": [Bucket("bid", k, CX - (k + 1) * BW) for k in range(n)],
            "ask": [Bucket("ask", k, CX + k * BW) for k in range(n)],
        }
        for side, levels in (("bid", self.m.bids), ("ask", self.m.asks)):
            for price, size in levels:
                k = int(abs(self.x(price) - CX) // BW)
                if k >= n:
                    break                                           # levels are sorted outward
                b = out[side][k]
                b.volume += size
                if size > b.top[1]:
                    b.top = (price, size)
        return out["bid"] + out["ask"]


def shape(u: float) -> float:
    """Log-like height curve: walls stand tall without flattening the rest."""
    return math.log1p(90 * u) / math.log1p(90)


# ═══ svg primitives ═════════════════════════════════════════════════════════

def f(v: float) -> str:
    """Compact number: one decimal, no trailing zeros, no leading zero."""
    s = f"{v:.1f}".rstrip("0").rstrip(".")
    return s.replace("0.", ".", 1) if s.startswith(("0.", "-0.")) else (s if s != "-0" else "0")


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


def font_face(family: str, file: str, weight: int) -> str:
    data = base64.b64encode((FONTS / file).read_bytes()).decode()
    return (f"@font-face{{font-family:{family};font-weight:{weight};"
            f"src:url(data:font/woff2;base64,{data}) format('woff2')}}")


# ═══ scene → svg ════════════════════════════════════════════════════════════

class Renderer:
    def __init__(self, scene: Scene, seed: int = 7):
        self.s = scene
        self.rng = random.Random(seed)
        n = len(scene.shift)
        self.stops = [REPLAY_END * i / (n - 1) for i in range(n)]   # keyframe % per minute

    # ── assembly ────────────────────────────────────────────────────────────
    def svg(self) -> str:
        s = self.s
        return "\n".join([
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
            f'font-family="{MONO}" role="img" aria-labelledby="t d">',
            "<title id=\"t\">GRZ_Research · live BTC-USD limit order book</title>",
            f'<desc id="d">Live {s.m.source} level-2 order book for BTC-USD rendered as a particle field. '
            f'Mid {money(s.mid)} USD; bid wall {money(s.walls["bid"].top[0])}, ask wall '
            f'{money(s.walls["ask"].top[0])}. The book replays the last {HISTORY} minutes of price action.</desc>',
            self.defs(),
            self.backdrop(),
            '<g clip-path="url(#field)">',
            self.dust(),
            self.grid(),
            f'<g id="book">{self.book()}</g>',
            self.edge_fades(),
            "</g>",
            self.axis(),
            self.header(),
            self.headline(),
            self.stats(),
            f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="18" fill="none" stroke="#1B2333"/>',
            "</svg>",
        ])

    # ── defs: fonts, keyframes, gradients ───────────────────────────────────
    def defs(self) -> str:
        s = self.s
        replay = " ".join(f"{f(p)}%{{transform:translateX({f(dx)}px)}}" for p, dx in zip(self.stops, s.shift))
        cursor = " ".join(f"{f(p)}%{{transform:translate({f(x)}px,{f(y)}px)}}"
                          for p, (x, y) in zip(self.stops, self.spark_points()))
        x0, y0 = self.spark_points()[0]
        window = self.stops[1] - self.stops[0]                    # % of the loop one minute lasts
        css = f"""
{font_face("JBM", "jbm-400.woff2", 400)}
{font_face("JBM", "jbm-700.woff2", 700)}
{font_face("IDL", "inter-300.woff2", 300)}
#book{{animation:replay {LOOP}s linear infinite}}
@keyframes replay{{{replay} {HOLD_END}%{{transform:translateX(0);animation-timing-function:cubic-bezier(.7,0,.3,1)}} 100%{{transform:translateX({f(s.shift[0])}px)}}}}
.cur{{animation:cursor {LOOP}s linear infinite}}
@keyframes cursor{{{cursor} {HOLD_END}%{{transform:translate(0,0);opacity:1}} 95%{{opacity:0}} 99.9%{{opacity:0;transform:translate({f(x0)}px,{f(y0)}px)}} 100%{{opacity:1;transform:translate({f(x0)}px,{f(y0)}px)}}}}
.tk{{opacity:0;animation:tick {LOOP}s step-end infinite}}
@keyframes tick{{0%{{opacity:1}} {f(window)}%{{opacity:0}} 100%{{opacity:0}}}}
.now{{animation:now {LOOP}s step-end infinite}}
@keyframes now{{0%{{opacity:0}} {REPLAY_END}%{{opacity:1}} 100%{{opacity:1}}}}
.arrive{{animation:arrive {LOOP}s ease-out infinite}}
@keyframes arrive{{0%,{REPLAY_END - 1}%{{opacity:.55}} {REPLAY_END + 1.5}%{{opacity:1}} {HOLD_END}%{{opacity:.75}} 100%{{opacity:.55}}}}
@keyframes tw{{0%,100%{{opacity:1;transform:translateY(0)}} 50%{{opacity:.35;transform:translateY(-1.6px)}}}}
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
        for name, (near, base, far) in (("bid", BID), ("ask", ASK)):
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
<linearGradient id="beam" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{BTC}" stop-opacity="0"/><stop offset=".55" stop-color="{BTC}"/><stop offset="1" stop-color="#FFE3BF"/></linearGradient>
<linearGradient id="edge-l" x1="0" y1="0" x2="1" y2="0"><stop offset=".2" stop-color="#fff"/><stop offset="1" stop-color="#000"/></linearGradient>
<linearGradient id="edge-r" x1="1" y1="0" x2="0" y2="0"><stop offset=".2" stop-color="#fff"/><stop offset="1" stop-color="#000"/></linearGradient>
<mask id="fade-l" maskUnits="userSpaceOnUse" x="0" y="0" width="{EDGE}" height="{H}"><rect width="{EDGE}" height="{H}" fill="url(#edge-l)"/></mask>
<mask id="fade-r" maskUnits="userSpaceOnUse" x="{W - EDGE}" y="0" width="{EDGE}" height="{H}"><rect x="{W - EDGE}" width="{EDGE}" height="{H}" fill="url(#edge-r)"/></mask>
{"".join(grads)}
<pattern id="dotgrid" width="24" height="24" patternUnits="userSpaceOnUse"><circle cx="12" cy="12" r=".7" fill="#1A2232"/></pattern>
<clipPath id="field"><rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="17"/></clipPath>
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

    # ── the book: haze, depth, particles, walls, mid beam, flow ─────────────
    def book(self) -> str:
        return "".join([self.haze(), self.depth_lines(), self.particles(), self.walls(),
                        self.rain(), self.beam(), self.sparks()])

    def side(self, name: str) -> list[Bucket]:
        return [b for b in self.s.buckets if b.side == name]

    def haze(self) -> str:
        out = []
        for name in ("bid", "ask"):
            bs = self.side(name)
            hs = smooth([b.h for b in bs], 5)
            pts = [(CX, BASE)] + [(b.xc, BASE - h * 0.9) for b, h in zip(bs, hs)] + [(bs[-1].xc, BASE)]
            d = "M" + "L".join(f"{f(x)} {f(y)}" for x, y in pts) + "Z"
            out.append(f'<path d="{d}" fill="url(#haze-{name})"/>')
        return "".join(out)

    def depth_lines(self) -> str:
        """Cumulative depth, the classic exchange depth chart, as a fine contour."""
        visible = {n: sum(b.volume for b in self.side(n) if self.s.is_visible(b)) for n in ("bid", "ask")}
        ref = max(visible.values())
        out = []
        for name, (_, base, _) in (("bid", BID), ("ask", ASK)):
            cum, pts = 0.0, [(CX, BASE)]
            for b in self.side(name):
                cum += b.volume
                y = BASE - min(HMAX * 1.05, HMAX * 0.96 * cum / ref)
                edge = b.x0 if name == "bid" else b.x0 + BW
                pts.append((edge, y))
            d = "M" + "L".join(f"{f(x)} {f(y)}" for x, y in pts)
            out.append(f'<path d="{d}" fill="none" stroke="{base}" stroke-opacity=".10" stroke-width="6" stroke-linejoin="round"/>'
                       f'<path d="{d}" fill="none" stroke="{base}" stroke-opacity=".55" stroke-width="1.1" stroke-linejoin="round" stroke-dasharray="3 3"/>')
        return "".join(out)

    def particles(self) -> str:
        rng, s = self.rng, self.s
        total_h = sum(b.h for b in s.buckets)
        density = PARTICLES / total_h
        layers: dict[tuple, list] = {}
        caps: dict[str, list] = {"bid": [], "ask": []}
        for b in s.buckets:
            if b.volume <= 0:
                continue
            n = max(1, round(b.h * density))
            for _ in range(n):
                x = b.x0 + rng.random() * BW
                y = BASE - 1.2 - (b.h - 1.2) * rng.random() ** 0.9
                coarse = rng.random() < 0.16
                tier = min(2, int(abs(x - CX) / HALF_W / 0.26))     # brighter near the touch
                layers.setdefault((b.side, tier, coarse, rng.randrange(3)), []).append((x, y))
            if b.h > 3:
                caps[b.side].append((b.xc, BASE - b.h))
        out = []
        for (name, tier, coarse, group), pts in sorted(layers.items()):
            color = (BID if name == "bid" else ASK)[tier]
            width, alpha = (2.3, 0.42) if coarse else (1.25, 0.9 - 0.12 * tier)
            dur = 2.6 + 1.1 * group + 0.4 * tier + rng.random()
            out.append(f'<path d="{dots(pts)}" stroke="{color}" stroke-opacity="{round(alpha, 2)}" '
                       f'stroke-width="{width}" stroke-linecap="round" '
                       f'style="animation:tw {dur:.2f}s ease-in-out -{rng.random() * dur:.2f}s infinite"/>')
        for name, pts in caps.items():
            out.append(f'<path d="{dots(pts)}" stroke="{(BID if name == "bid" else ASK)[0]}" stroke-width="1.7" '
                       f'stroke-linecap="round" style="animation:tw 3.3s ease-in-out infinite"/>')
        return "".join(out)

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
                       f'style="animation:tw 2.9s ease-in-out -1s infinite"/>')
            out.append(f'<path d="{dots(core)}" stroke="{near}" stroke-width="1.5" stroke-linecap="round" '
                       f'style="animation:tw 2.1s ease-in-out infinite"/>')
            out.append(self.wall_label(name, wall))
        return "".join(out)

    def wall_label(self, name: str, wall: Bucket) -> str:
        near, base, _ = BID if name == "bid" else ASK
        x, top = wall.xc, BASE - wall.h
        outward = -1 if name == "bid" else 1
        if (name == "bid" and x - 210 < PAD) or (name == "ask" and x + 210 > W - PAD):
            outward = -outward                                      # keep the label on the canvas
        anchor = "end" if outward < 0 else "start"
        tx, ty = x + outward * 14, top - 22
        if abs(x - CX) < 90:                                        # don't collide with the ticker
            ty = top + 44
        dist = (wall.top[0] / self.s.mid - 1) * 100
        title = f"{'BID' if name == 'bid' else 'ASK'} WALL"
        return (f'<path d="M{f(x)} {f(top - 4)}V{f(ty - 4)}h{f(outward * 8)}" fill="none" stroke="{base}" stroke-opacity=".7"/>'
                f'<circle cx="{f(x)}" cy="{f(top)}" r="2.6" fill="{near}"/>'
                + text(tx, ty - 14, f'{title}  <tspan fill="{MUTED}">{dist:+.2f}%</tspan>', 12, base, anchor, 700, 1.5)
                + text(tx, ty + 4, f'{money(wall.top[0])} <tspan fill="{MUTED}">· {wall.volume:,.1f} BTC</tspan>',
                       15, INK, anchor))

    def rain(self) -> str:
        """Fresh limit orders falling onto their price level."""
        rng, out = self.rng, []
        pool = [b for b in self.s.buckets if b.volume > 0 and self.s.is_visible(b)]
        weights = [b.volume ** 0.6 for b in pool]
        for b in rng.choices(pool, weights, k=RAIN):
            land = BASE - b.h
            drop = min(110, land - (BASE - HMAX - 20))
            if drop < 24:
                continue
            x = b.x0 + rng.random() * BW
            dur = rng.uniform(1.8, 3.6)
            out.append(f'<rect class="fall" x="{f(x - 0.5)}" y="{f(land - 11)}" width="1" height="11" '
                       f'fill="url(#drop-{b.side})" style="--d:-{f(drop)}px;animation-duration:{dur:.2f}s;'
                       f'animation-delay:-{rng.random() * dur:.2f}s"/>')
        return "".join(out)

    def beam(self) -> str:
        """The mid price: a BTC-orange beam carrying the replay ticker."""
        s, top = self.s, BASE - HMAX - 12
        tag_y = top - 26
        ticks = []
        step = LOOP * (self.stops[1] - self.stops[0]) / 100
        for i, price in enumerate(s.prices[:-1]):
            ago = len(s.prices) - 1 - i
            ticks.append(text(CX, tag_y + 16, f'<tspan fill="{MUTED}">−{ago}m</tspan> {money(price)}', 13, INK, "middle",
                              extra=f' class="tk" style="animation-delay:{i * step:.3f}s"'))
        ticks.append(text(CX, tag_y + 16, f'<tspan fill="{BTC}" font-weight="700">NOW</tspan> {money(s.mid)}', 13, INK,
                          "middle", extra=' class="now"'))
        return (f'<ellipse cx="{CX}" cy="{f((top + BASE) / 2)}" rx="30" ry="{f((BASE - top) / 2 + 30)}" fill="url(#beam-glow)" class="arrive"/>'
                f'<rect x="{CX - 0.8}" y="{top}" width="1.6" height="{BASE - top}" fill="url(#beam)"/>'
                f'<rect x="{CX - 74}" y="{tag_y}" width="148" height="23" rx="11.5" fill="#0B111C" stroke="{BTC}" stroke-opacity=".55"/>'
                + "".join(ticks)
                + f'<path d="M{CX} {BASE - 5}l5 5-5 5-5-5z" fill="{BTC}"/>')

    def sparks(self) -> str:
        """Executions at the touch, spraying into the side they consumed."""
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
        lo, hi = s.mid * math.exp(-s.R), s.mid * math.exp(s.R)
        step = next(st for st in (10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000)
                    if (hi - lo) / st <= 10)
        first = math.ceil(lo / step) * step
        out = []
        p = first
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
        cursor = PAD + len(name) * (0.6 * size + track) + 6         # JetBrains Mono advances 0.6 em
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

    def spark_points(self) -> list[tuple[float, float]]:
        """Sparkline vertices, relative to the sparkline's 'now' point."""
        p = self.s.prices
        lo, hi = min(p), max(p)
        span = (hi - lo) or 1
        n = len(p)
        pts = [(-SPARK_W + SPARK_W * i / (n - 1), -SPARK_H * (v - lo) / span) for i, v in enumerate(p)]
        x1, y1 = pts[-1]
        return [(x - x1, y - y1) for x, y in pts]

    def sparkline(self) -> str:
        s = self.s
        up = s.prices[-1] >= s.prices[0]
        color = BID[1] if up else ASK[1]
        rel = self.spark_points()
        ox = W - PAD
        lo_y = 132
        # anchor so the sparkline's floor sits on lo_y
        floor = max(y for _, y in rel)
        oy = lo_y - floor
        pts = [(ox + x, oy + y) for x, y in rel]
        line = "M" + "L".join(f"{f(x)} {f(y)}" for x, y in pts)
        area = line + f"L{f(pts[-1][0])} {lo_y}L{f(pts[0][0])} {lo_y}Z"
        change = (s.prices[-1] / s.prices[0] - 1) * 100
        return (f'<linearGradient id="sl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{color}" stop-opacity=".25"/>'
                f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient>'
                f'<path d="{area}" fill="url(#sl)"/>'
                f'<path d="{line}" fill="none" stroke="{color}" stroke-width="1.4" stroke-linejoin="round"/>'
                + text(ox - SPARK_W - 10, lo_y - 2, f'60M <tspan fill="{color}">{change:+.2f}%</tspan>', 11, MUTED, "end", ls=0.8)
                + f'<g transform="translate({f(ox)} {f(oy)})"><g class="cur">'
                  f'<circle r="7" fill="{BTC}" opacity=".25"/><circle r="2.8" fill="#FFF3E0"/></g></g>')

    def headline(self) -> str:
        s = self.s
        change = (s.mid / s.m.open_24h - 1) * 100
        color = BID[1] if change >= 0 else ASK[1]
        arrow = "▲" if change >= 0 else "▼"
        return "".join([
            text(CX, 152, "BTC / USD", 12, MUTED, "middle", ls=5),
            text(CX, 206, money(s.mid), 58, "#F4F7FB", "middle", 300, 1, DISPLAY),
            text(CX, 232, f'<tspan fill="{color}">{arrow} {change:+.2f}%</tspan>  24H   '
                          f'<tspan fill="{DIM}">H</tspan> {money(s.m.high_24h, 0)}  <tspan fill="{DIM}">L</tspan> '
                          f'{money(s.m.low_24h, 0)}', 13, MUTED, "middle"),
        ])

    # ── quant strip ─────────────────────────────────────────────────────────
    def stats(self) -> str:
        s = self.s
        rng_pct = f"±{s.R * 100:g}%"
        obi_color = BID[1] if s.imbalance >= 0 else ASK[1]
        cells = [
            ("MICROPRICE", money(s.microprice), INK),
            ("SPREAD · USD", f"{s.spread:,.2f}", INK),
            (f"IMBALANCE {rng_pct}", f"{s.imbalance:+.3f}", obi_color),
            ("σ 1H · ANNUALISED", f"{s.sigma * 100:.1f}%", INK),
            (f"DEPTH {rng_pct} · BTC", f'<tspan fill="{BID[1]}">{s.depth["bid"]:,.0f}</tspan>'
                                     f'<tspan fill="{DIM}"> / </tspan><tspan fill="{ASK[1]}">{s.depth["ask"]:,.0f}</tspan>', INK),
            ("24H VOLUME · BTC", f"{s.m.volume_24h:,.0f}", INK),
        ]
        top, cw = BASE + 50, (W - 2 * PAD) / len(cells)
        out = [f'<line x1="{PAD}" x2="{W - PAD}" y1="{top}" y2="{top}" stroke="{LINE}"/>']
        for i, (label, value, color) in enumerate(cells):
            x = PAD + i * cw + (0 if i == 0 else 18)
            if i:
                out.append(f'<line x1="{f(PAD + i * cw)}" x2="{f(PAD + i * cw)}" y1="{top + 18}" y2="{top + 64}" stroke="{LINE}"/>')
            out.append(text(x, top + 30, label, 11.5, "#5E6A7E", ls=1.2, weight=700))
            out.append(text(x, top + 58, value, 19, color))
        return "".join(out)


SPARK_W, SPARK_H = 168, 30


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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("dist/liquidity-field.svg"))
    ap.add_argument("--demo", action="store_true", help="render a synthetic book, no network")
    args = ap.parse_args()

    scene = Scene(load_market(args.demo))
    svg = Renderer(scene).svg()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(svg, encoding="utf-8")
    walls = scene.walls
    print(f"{scene.m.source} mid {money(scene.mid)} · range ±{scene.R:.2%} · "
          f"bid wall {money(walls['bid'].top[0])} ({walls['bid'].volume:.1f}) · "
          f"ask wall {money(walls['ask'].top[0])} ({walls['ask'].volume:.1f}) · "
          f"{len(svg) / 1024:.0f} KiB → {args.out}")


if __name__ == "__main__":
    main()
