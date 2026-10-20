#!/usr/bin/env python3
"""Liquidity Field: the live BTC-USD limit order book, drawn as particles.

Pulls the level-2 order book, the last hour of 1-minute candles and the 24h
stats from Coinbase Exchange (Kraken as fallback) and writes one
self-contained, animated SVG:

  * the price axis is fixed; each side of the book is a pile of particles
    stacked level by level up to its cumulative depth curve, never above it
  * the mid replays the last 60 minutes of BTC across that axis: below it the
    book is bid, above it ask, so a rising mid eats into the sell side and a
    falling mid into the buy side, and both piles re-stack from the new mid
  * the headline price, the wall distances, imbalance and depth all tick with
    the replayed mid; the largest untouched levels are the bid and ask walls,
    standing as cliffs in the depth curve

Every replay-synchronised motion is a CSS animation on the same 32 s clock. Each
animated element's resting style is the live state, so visitors who prefer
reduced motion see the book as it is now. Standard library only, so it runs on
a bare GitHub Actions runner.

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
from datetime import datetime, timedelta, timezone
from functools import cached_property
from pathlib import Path

# ── canvas ──────────────────────────────────────────────────────────────────
W, H = 1200, 650
PAD = 40
CX = W / 2                      # centre of the price axis
HALF_W = CX - PAD               # px from the centre to the edge of the plotted book
BASE = 506                      # baseline of the liquidity field
HMAX = 200                      # tallest pile
BW = 3                          # bucket width in px
PARTICLES = 5200                # particles under the depth curves at "now"
EDGE = 110                      # width of the fade where the book leaves the card
LABEL_Y = 262                   # wall callouts sit in a fixed row above the field
TAG_Y = 298                     # and the mid's quote in the row below them

# ── motion ──────────────────────────────────────────────────────────────────
HISTORY = 60                    # minutes replayed
LOOP = 32                       # seconds per replay loop
REPLAY_END = 84.0               # % of the loop spent replaying the hour
HOLD_END = 93.0                 # % at which the rewind starts
REWIND = (0.7, 0.0, 0.3, 1.0)   # cubic-bezier of the rewind
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
    closes: list[float]                  # close of each of the last HISTORY whole minutes, oldest first
    open_24h: float
    high_24h: float
    low_24h: float
    volume_24h: float
    time: datetime


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def minute_grid(candles: list[tuple[int, float]], now: datetime) -> list[float]:
    """Closes of the last HISTORY completed minutes, oldest first.

    Venues skip minutes without trades and include the minute still in progress;
    keying on the candle start times fixes both, so '−5m' really is five minutes ago.
    """
    closes = dict(candles)
    current = int(now.timestamp()) // 60 * 60
    grid, last = [], None
    for start in range(current - 60 * HISTORY, current, 60):
        last = closes.get(start, last)
        grid.append(last)
    first = next((c for c in grid if c is not None), None)
    if first is None:
        raise ValueError("no candles in the last hour")
    return [first if c is None else c for c in grid]


def _checked(m: Market) -> Market:
    if not m.bids or not m.asks:
        raise ValueError("order book has an empty side")
    return m


def coinbase() -> Market:
    base = "https://api.exchange.coinbase.com/products/BTC-USD"
    now = datetime.now(timezone.utc)
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    span = f"start={(now - timedelta(minutes=HISTORY + 5)).strftime(stamp)}&end={now.strftime(stamp)}"
    book = _get(f"{base}/book?level=2")
    candles = _get(f"{base}/candles?granularity=60&{span}")      # explicit span skips a 5-min CDN cache
    stats = _get(f"{base}/stats")
    return _checked(Market(
        source="COINBASE",
        bids=[(float(p), float(s)) for p, s, *_ in book["bids"]],
        asks=[(float(p), float(s)) for p, s, *_ in book["asks"]],
        closes=minute_grid([(int(c[0]), float(c[4])) for c in candles], now),   # [t, lo, hi, open, close, vol]
        open_24h=float(stats["open"]),
        high_24h=float(stats["high"]),
        low_24h=float(stats["low"]),
        volume_24h=float(stats["volume"]),
        time=now,
    ))


def kraken() -> Market:
    def result(endpoint: str):
        body = _get(f"https://api.kraken.com/0/public/{endpoint}")
        if body.get("error"):
            raise RuntimeError(body["error"])
        return next(v for k, v in body["result"].items() if k != "last")

    now = datetime.now(timezone.utc)
    book = result("Depth?pair=XBTUSD&count=500")
    candles = result("OHLC?pair=XBTUSD&interval=1")                # [t, o, h, l, close, vwap, vol, n]
    tick = result("Ticker?pair=XBTUSD")
    return _checked(Market(
        source="KRAKEN",
        bids=[(float(p), float(s)) for p, s, _ in book["bids"]],
        asks=[(float(p), float(s)) for p, s, _ in book["asks"]],
        closes=minute_grid([(int(c[0]), float(c[4])) for c in candles], now),
        open_24h=float(tick["o"]),
        high_24h=float(tick["h"][1]),
        low_24h=float(tick["l"][1]),
        volume_24h=float(tick["v"][1]),
        time=now,
    ))


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
        self.R = min(0.25, max(0.008, math.ceil(2.6 * half / 0.0025) * 0.0025,
                               half * HALF_W / (HALF_W - EDGE - 20)))   # the mid never enters the fades
        self.px = HALF_W / self.R                                   # px per unit of log-price
        self.track = [self.x(p) for p in self.prices]               # where the mid stands, minute by minute

        rets = [math.log(b / a) for a, b in zip(self.prices, self.prices[1:])]
        mean = sum(rets) / max(1, len(rets))
        self.sigma = math.sqrt(sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1) * 525_600)

        self.buckets = self._bucketise()
        zone_lo, zone_hi = min(self.track), max(self.track)
        for b in self.buckets:
            b.kind = "bid" if b.x0 + BW <= zone_lo else "ask" if b.x0 >= zone_hi else "zone"
        plotted = [b for b in self.buckets if PAD <= b.xc <= W - PAD]
        self.edges = list(itertools.accumulate((b.volume for b in self.buckets), initial=0.0))

        # walls: the largest levels the replayed mid never touches, so they keep their side
        self.walls = {}
        for side in ("bid", "ask"):
            pool = [b for b in plotted if b.kind == side and b.volume > 0 and PAD + 60 <= b.xc <= W - PAD - 60]
            if pool:
                self.walls[side] = max(pool, key=lambda b: b.volume)

        # depth either side of the moving mid, within a band the venue's book actually covers
        covered = min(math.log(self.mid / m.bids[-1][0]), math.log(m.asks[-1][0] / self.mid))
        self.band = max(min(self.R, covered), 1e-4)
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
        """Size resting in (mid·e^−band, mid] and (mid, mid·e^band): a level at the mid counts once, as bid."""
        def upto(price: float) -> float:
            return self._cum[bisect.bisect_right(self._prices, price)]
        lo, hi = mid * math.exp(-self.band), mid * math.exp(self.band)
        return upto(mid) - upto(lo), upto(hi) - upto(mid)

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


# ═══ svg primitives ═════════════════════════════════════════════════════════

def f(v: float) -> str:
    """Compact number: one decimal, no trailing zeros, no leading zero."""
    s = f"{v:.1f}".rstrip("0").rstrip(".")
    return s.replace("0.", ".", 1) if s.startswith(("0.", "-0.")) else (s if s != "-0" else "0")


def pct(p: float) -> str:
    """A keyframe selector."""
    return (f"{p:.4f}".rstrip("0").rstrip(".") or "0") + "%"


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


def ease(u: float, curve: tuple[float, float, float, float] = REWIND) -> float:
    """CSS cubic-bezier(x1, y1, x2, y2) at time fraction u."""
    x1, y1, x2, y2 = curve

    def bez(s: float, a: float, b: float) -> float:
        return 3 * a * s * (1 - s) ** 2 + 3 * b * s * s * (1 - s) + s ** 3
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if bez(mid, x1, x2) < u else (lo, mid)
    return bez((lo + hi) / 2, y1, y2)


def simplify(points: list[tuple[float, tuple[float, ...]]], tol: float) -> list[tuple[float, tuple[float, ...]]]:
    """Drop keyframes that linear interpolation already reproduces within `tol` in every
    series (Douglas–Peucker), so animations sharing the result stay locked together."""
    keep, todo = {0, len(points) - 1}, [(0, len(points) - 1)]
    while todo:
        a, b = todo.pop()
        (ta, va), (tb, vb) = points[a], points[b]
        worst, at = tol, None
        for i in range(a + 1, b):
            ti, vi = points[i]
            w = (ti - ta) / (tb - ta)
            err = max(abs(v - (lo + (hi - lo) * w)) for v, lo, hi in zip(vi, va, vb))
            if err > worst:
                worst, at = err, i
        if at is not None:
            keep.add(at)
            todo += [(a, at), (at, b)]
    return [points[i] for i in sorted(keep)]


def font_face(family: str, file: str, weight: int) -> str:
    data = base64.b64encode((FONTS / file).read_bytes()).decode()
    return (f"@font-face{{font-family:{family};font-weight:{weight};"
            f"src:url(data:font/woff2;base64,{data}) format('woff2')}}")


# ═══ scene → svg ════════════════════════════════════════════════════════════

class Renderer:
    def __init__(self, scene: Scene, seed: int = 7):
        self.s = scene
        self.rng = random.Random(seed)
        n = len(scene.prices)
        self.stops = [REPLAY_END * i / (n - 1) for i in range(n)]   # keyframe % of each minute
        self.css: dict[str, str] = {}                              # replay keyframes, registered while drawing
        self.clips = 0
        self.wall_labels: dict[str, str] = {}
        F = scene.cumulative
        self.zone = (min(scene.track), max(scene.track))             # the stretch the mid crosses
        lo, hi = self.zone
        self.scale = 0.92 * HMAX / max(F(hi) - F(PAD), F(W - PAD) - F(lo), 1e-9)   # px per BTC of depth
        now = scene.track[-1]
        area = self.scale * BW * sum(abs(F(b.xc) - F(now)) for b in scene.buckets if PAD <= b.xc <= W - PAD)
        self.density = PARTICLES / max(area, 1.0)                    # particles per px² under the curves

    # ── the replay clock ────────────────────────────────────────────────────
    # Every replay-synced motion is a CSS animation of LOOP seconds: the hour plays over
    # 0–REPLAY_END %, holds on NOW until HOLD_END %, then eases back for the rewind. A
    # class's resting style is the live state, which reduced-motion visitors see.
    def animation(self, name: str, frames: list[tuple[float, str]], rest: str, timing: str = "linear") -> str:
        """Register keyframes plus a class that runs them; `rest` is the live state."""
        if name not in self.css:
            body = " ".join(f"{pct(p)}{{{decl}}}" for p, decl in frames)
            self.css[name] = f"@keyframes {name}{{{body}}}.{name}{{{rest};animation:{name} {LOOP}s {timing} infinite}}"
        return name

    def glide(self, name: str, decls: list[str], timing: str = "linear") -> str:
        """One declaration per minute, hold on the last, ease back to the first for the rewind."""
        rewind = f";animation-timing-function:cubic-bezier({','.join(f'{v:g}' for v in REWIND)})"
        frames = list(zip(self.stops, decls)) + [(HOLD_END, decls[-1] + rewind), (100, decls[0])]
        return self.animation(name, frames, decls[-1], timing)

    @cached_property
    def journey(self) -> list[tuple[float, float, float, float]]:
        """(keyframe %, mid x, bid lift, ask lift): the keyframes every motion locked to the mid shares."""
        pts = [(p, (x, BASE - self.curve("bid", x), BASE - self.curve("ask", x))) for p, x in self.path_samples()]
        return [(p, *v) for p, v in simplify(pts, 0.3)]

    def along(self, name: str, decl) -> str:
        """An animation keyed on the journey; decl maps (x, bid lift, ask lift) to a declaration."""
        x = self.s.track[-1]
        now = (x, BASE - self.curve("bid", x), BASE - self.curve("ask", x))
        return self.animation(name, [(p, decl(*v)) for p, *v in self.journey], decl(*now))

    def walk(self) -> str:
        """The mid's horizontal journey across the fixed axis."""
        return self.along("mo", lambda x, lb, la: f"transform:translateX({f(x)}px)")

    def frame(self, side: str) -> str:
        """A side's frame: it counter-travels the clip (so the book stays put) and lifts so
        the depth curve meets the baseline exactly at the moving mid."""
        return self.along(f"frame-{side}", lambda x, lb, la: f"transform:translate({f(-x)}px,{f(lb if side == 'bid' else la)}px)")

    def stepper(self, lh: float) -> str:
        """Jump one line of height lh per minute; spin back through the hour on rewind."""
        n = len(self.s.prices)
        return self.glide(f"tick{f(lh)}", [f"transform:translateY({f(-i * lh)}px)" for i in range(n)], "step-end")

    def ticker(self, lines: list[str], window: tuple[float, float, float, float], lh: float) -> str:
        """Stacked text lines behind a fixed window, stepping one line per minute."""
        x, y, w, h = window
        self.clips += 1
        cid = f"tk{self.clips}"
        return (f'<clipPath id="{cid}"><rect x="{f(x)}" y="{f(y)}" width="{f(w)}" height="{f(h)}"/></clipPath>'
                f'<g clip-path="url(#{cid})"><g class="{self.stepper(lh)}">{"".join(lines)}</g></g>')

    def path_samples(self) -> list[tuple[float, float]]:
        """(keyframe %, mid x) at every bucket edge the mid crosses, plus the eased rewind.

        Between these samples the mid moves linearly and the depth F is linear too, so
        a lift keyed on them keeps each depth curve touching the baseline at the mid.
        """
        track, stops = self.s.track, self.stops
        out = []
        for x0, x1, p0, p1 in zip(track, track[1:], stops, stops[1:]):
            out.append((p0, x0))
            if x1 != x0:
                edges = range(math.floor(min(x0, x1) / BW) + 1, math.ceil(max(x0, x1) / BW))
                for e in sorted((k * BW for k in edges), reverse=x1 < x0):
                    out.append((p0 + (e - x0) / (x1 - x0) * (p1 - p0), e))
        out += [(stops[-1], track[-1]), (HOLD_END, track[-1])]
        steps = 90
        for k in range(1, steps + 1):
            u = k / steps
            out.append((HOLD_END + u * (100 - HOLD_END), track[-1] + ease(u) * (track[0] - track[-1])))
        return out

    def lift(self, side: str) -> str:
        """Only the vertical part of a side's frame, for what sits outside the side clip."""
        return self.along(f"lift-{side}", lambda x, lb, la: f"transform:translateY({f(lb if side == 'bid' else la)}px)")

    # ── assembly ────────────────────────────────────────────────────────────
    def svg(self) -> str:
        s = self.s
        walls = " and ".join(f"{side} wall {money(w.top[0])}" for side, w in s.walls.items()) or "no walls"
        body = [
            self.backdrop(),
            '<g clip-path="url(#field)">',
            self.dust(),
            self.grid(),
            self.book(),
            self.edge_fades(),
            self.overlays(),
            "</g>",
            self.axis(),
            self.header(),
            self.headline(),
            self.stats(),
            f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="18" fill="none" stroke="#1B2333"/>',
        ]
        return "\n".join([
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
            f'font-family="{MONO}" role="img" aria-labelledby="t d">',
            '<title id="t">GRZ_Research · live BTC-USD limit order book</title>',
            f'<desc id="d">Live {s.m.source} level-2 order book for BTC-USD as two piles of particles under the '
            f'cumulative depth curves, on a fixed price axis; mid {money(s.mid)} USD, {walls}. The mid replays the '
            f'last {HISTORY} minutes: liquidity below it is bid, above it ask.</desc>',
            self.defs(),
            *body,
            "</svg>",
        ])

    # ── defs: fonts, keyframes, gradients ───────────────────────────────────
    def defs(self) -> str:
        # stroke-opacity, not opacity: animating opacity forces an offscreen layer per path per frame
        twinkles = "".join(f"@keyframes {twinkle_name(a)}{{0%,100%{{stroke-opacity:{a:g}}} 50%{{stroke-opacity:{a * .35:.3f}}}}}"
                           for a in TWINKLE)
        css = f"""
{font_face("JBM", "jbm-400.woff2", 400)}
{font_face("JBM", "jbm-700.woff2", 700)}
{font_face("IDL", "inter-300.woff2", 300)}
{twinkles}
{"".join(self.css.values())}
@keyframes rise{{from{{transform:translateY(0)}} to{{transform:translateY(-26px)}}}}
@keyframes pulse{{0%,100%{{opacity:.25}} 50%{{opacity:1}}}}
@keyframes blink{{0%,55%{{opacity:1}} 56%,100%{{opacity:0}}}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important}}}}
"""
        grads = []
        for name, (near, base, _) in (("bid", BID), ("ask", ASK)):
            grads.append(f'<linearGradient id="wall-{name}" x1="0" y1="0" x2="0" y2="1">'
                         f'<stop offset="0" stop-color="{near}" stop-opacity=".0"/>'
                         f'<stop offset=".08" stop-color="{base}" stop-opacity=".22"/>'
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
<clipPath id="plot"><rect x="{-2 * W}" y="{BASE - HMAX - 14}" width="{4 * W}" height="{HMAX + 15}"/></clipPath>
<clipPath id="left-of"><rect x="{-2 * W}" width="{2 * W}" height="{H}"/></clipPath>
<clipPath id="right-of"><rect width="{2 * W}" height="{H}"/></clipPath>
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
            pts = [(self.rng.uniform(0, W), self.rng.uniform(16, BASE - HMAX - 50)) for _ in range(n // 2)]
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

    # ── the book: each side piled up to its cumulative depth curve ──────────
    #
    # Depth at pixel x is |F(x) − F(mid)|, F being the liquidity resting left of x.
    # So each side is drawn once in its own frame, where its depth curve is the static
    # ±F(x), and the frame only lifts as the mid moves: curve, particles and walls
    # stay exact, and nothing ever pokes above the line.
    def book(self) -> str:
        self.wall_labels = {side: self.wall_label(side, wall) for side, wall in self.s.walls.items()}
        piles = {side: self.pile(side) for side in ("bid", "ask")}
        sizes = sorted(v for pile in piles.values() for *_, v in pile) or [0.0]
        cut = (sizes[int(len(sizes) * 0.35)], sizes[int(len(sizes) * 0.75)])
        return self.sided(self.stack("bid", piles["bid"], cut), self.stack("ask", piles["ask"], cut))

    def overlays(self) -> str:
        """Wall callouts and the mid ride above the edge fades; the mid's quote on top."""
        return "".join(self.wall_labels.values()) + self.mid()

    def sided(self, bid: str, ask: str) -> str:
        """Split at the moving mid: the clip travels with the mid, and each side's frame
        counter-travels inside it, so the book stays put and only the boundary moves."""
        return "".join(f'<g class="{self.walk()}"><g clip-path="url(#{clip})">{content}</g></g>'
                       for clip, content in (("left-of", bid), ("right-of", ask)))

    def curve(self, side: str, x: float) -> float:
        """The side's depth curve in its own frame."""
        return (1 if side == "bid" else -1) * self.scale * self.s.cumulative(x)

    def reach(self, side: str) -> tuple[float, float, float, float]:
        """x-range a side can occupy, and the band of its frame that is ever on screen."""
        lo, hi = self.zone
        ends = (self.curve(side, lo), self.curve(side, hi))
        x_from, x_to = (0.0, hi) if side == "bid" else (lo, float(W))
        return x_from, x_to, min(ends) - HMAX - 14, max(ends)

    def pile(self, side: str) -> list[tuple[float, float, float]]:
        """Particles filling the area under the depth curve, each tagged with the size of
        the price level whose stratum it sits in (levels stack outward from the mid)."""
        s, rng, sign = self.s, self.rng, (1 if side == "bid" else -1)
        x_from, x_to, ceiling, floor = self.reach(side)
        out = []
        for b in s.buckets:
            if not x_from - BW < b.x0 < x_to:
                continue
            top = max(min(self.curve(side, b.x0), self.curve(side, b.x0 + BW)), ceiling)
            if floor <= top:
                continue
            expected = self.density * BW * (floor - top)
            for _ in range(int(expected) + (rng.random() < expected % 1)):
                x, y = b.x0 + rng.random() * BW, top + rng.random() * (floor - top)
                if y < self.curve(side, x) + 1.2:
                    continue                                        # never above the line, not even by a radius
                k = bisect.bisect_right(s.edges, sign * y / self.scale) - 1
                out.append((x, y, s.buckets[min(max(k, 0), len(s.buckets) - 1)].volume))
        return out

    def stack(self, side: str, pile: list[tuple[float, float, float]], cut: tuple[float, float]) -> str:
        rng, s = self.rng, self.s
        palette = BID if side == "bid" else ASK
        near, body, _ = palette
        x_from, x_to, _, floor = self.reach(side)
        xs = [b.x0 for b in s.buckets if x_from - BW < b.x0 < x_to] + [x_to]
        line = "M" + "L".join(f"{f(x)} {f(self.curve(side, x))}" for x in xs)

        layers: dict[tuple, list] = {}
        for x, y, v in pile:                                        # big levels glow, small ones recede
            tier = 0 if v >= cut[1] else 1 if v >= cut[0] else 2
            layers.setdefault((tier, rng.random() < 0.14, rng.randrange(3)), []).append((x, y))
        out = [f'<path d="{line}L{f(x_to)} {f(floor)}L{f(xs[0])} {f(floor)}Z" fill="{body}" fill-opacity=".07"/>']
        for (tier, coarse, group), pts in sorted(layers.items()):
            width, alpha = (2.3, 0.42) if coarse else (1.25, TWINKLE[1 + tier])
            dur = 2.6 + 1.1 * group + 0.4 * tier + (tier + group) % 3 * 0.37
            out.append(f'<path d="{dots(pts)}" stroke="{palette[tier]}" stroke-opacity="{alpha:g}" '
                       f'stroke-width="{width}" stroke-linecap="round" style="{twinkle(alpha, dur, dur * group / 3)}"/>')
        crest = [(x, self.curve(side, x) + 0.8 + rng.random() * 2.5) for x in (b + rng.random() * BW for b in xs[:-1])]
        out.append(f'<path d="{dots(crest)}" stroke="{near}" stroke-width="1.5" stroke-linecap="round" '
                   f'style="{twinkle(1.0, 3.3)}"/>')
        out.append(f'<path d="{line}" fill="none" stroke="{body}" stroke-opacity=".14" stroke-width="6" stroke-linejoin="round"/>'
                   f'<path d="{line}" fill="none" stroke="{body}" stroke-width="1.3" stroke-linejoin="round"/>')
        if side in s.walls:
            out.append(self.wall(side, s.walls[side], floor))
        return f'<g clip-path="url(#plot)"><g class="{self.frame(side)}">{"".join(out)}</g></g>'

    def wall(self, side: str, wall: Bucket, floor: float) -> str:
        """A wall is a cliff in the depth curve: light it, and raise a pillar to the baseline."""
        near, _, _ = BID if side == "bid" else ASK
        c0, c1 = self.curve(side, wall.x0), self.curve(side, wall.x0 + BW)
        top, x = min(c0, c1), wall.xc
        return (f'<rect x="{f(x - 9)}" y="{f(top)}" width="18" height="{f(floor - top)}" fill="url(#wall-{side})" '
                f'style="animation:pulse 3.6s ease-in-out infinite"/>'
                f'<path d="M{f(wall.x0)} {f(c0)}L{f(wall.x0 + BW)} {f(c1)}" stroke="{near}" stroke-opacity=".35" stroke-width="7"/>'
                f'<path d="M{f(wall.x0)} {f(c0)}L{f(wall.x0 + BW)} {f(c1)}" stroke="{near}" stroke-width="2.2"/>')

    def wall_label(self, side: str, wall: Bucket) -> str:
        """A callout pinned above the field: the wall's price, its distance from the replayed mid
        and Σ, the BTC resting from the mid out to and including the wall (the pile's height
        there). Both tick each minute; only the leader stretches to follow the cliff."""
        s = self.s
        near, base, _ = BID if side == "bid" else ASK
        x, outer = wall.xc, (wall.x0 if side == "bid" else wall.x0 + BW)
        top = min(self.curve(side, wall.x0), self.curve(side, wall.x0 + BW))   # cliff top, in the side's frame
        title, ts, tls, vs = f"{side.upper()} WALL", 12, 1.5, 15
        dists = [signed((wall.top[0] / p - 1) * 100, "+.2f") + "%" for p in s.prices]
        sums = [f"Σ {abs(s.cumulative(t) - s.cumulative(outer)):,.1f} BTC" for t in s.track]
        price = f"{money(wall.top[0])} · "
        width = max(mono_width(f"{title}  {max(dists, key=len)}", ts, tls), mono_width(price + max(sums, key=len), vs))
        outward = -1 if side == "bid" else 1
        if (outward < 0 and x - 14 - width < PAD) or (outward > 0 and x + 14 + width > W - PAD):
            outward = -outward                                      # keep the label on the canvas
        left = x + 14 if outward > 0 else x - 14 - width
        y1, y2 = LABEL_Y, LABEL_Y + 18                              # title and value baselines
        box = (left - 6, y1 - 16, left + width + 6, y2 + 9)
        stub = (box[1] + box[3]) / 2
        dx, vx = left + mono_width(title + "  ", ts, tls), left + mono_width(price, vs)
        self.clips += 1
        lead = f"lead{self.clips}"
        return (f'<clipPath id="{lead}"><rect x="{f(x - 4)}" y="{f(stub)}" width="8" height="{f(BASE - stub)}"/></clipPath>'
                f'<g clip-path="url(#{lead})"><g class="{self.lift(side)}">'
                f'<path d="M{f(x)} {f(top)}V{f(top - 2 * H)}" stroke="{base}" stroke-opacity=".7"/>'
                f'<circle cx="{f(x)}" cy="{f(top)}" r="2.6" fill="{near}"/></g></g>'
                f'<rect x="{f(box[0])}" y="{f(box[1])}" width="{f(box[2] - box[0])}" height="{f(box[3] - box[1])}" '
                f'rx="5" fill="{BG0}" opacity=".72"/>'
                f'<path d="M{f(x)} {f(stub)}H{f(box[0] if outward > 0 else box[2])}" stroke="{base}" stroke-opacity=".7"/>'
                + text(left, y1, title, ts, base, weight=700, ls=tls)
                + self.ticker([text(dx, y1 + i * 18, d, ts, MUTED, weight=700, ls=tls) for i, d in enumerate(dists)],
                              (dx - 2, y1 - 13, mono_width(max(dists, key=len), ts, tls) + 6, 17), 18)
                + text(left, y2, money(wall.top[0]), vs, INK) + text(left + mono_width(money(wall.top[0]) + " ", vs), y2, "·", vs, MUTED)
                + self.ticker([text(vx, y2 + i * 22, v, vs, MUTED) for i, v in enumerate(sums)],
                              (vx - 2, y2 - 15, mono_width(max(sums, key=len), vs) + 6, 20), 22))

    def mid(self) -> str:
        """The mid price: a BTC-orange beam that walks the hour across the fixed axis,
        carrying the price it stands at."""
        prices = self.s.prices
        tag_w = mono_width(max((money(p) for p in prices), key=len), 13) + 22
        top = TAG_Y + 22
        arrive = self.animation("arrive", [(0, "opacity:.55"), (self.stops[-2], "opacity:.55"),
                                           (self.stops[-1], "opacity:1"), (HOLD_END, "opacity:.8"), (100, "opacity:.55")],
                                "opacity:.8")
        quotes = [text(0, TAG_Y + 16 + i * 20, money(p), 13, INK, "middle") for i, p in enumerate(prices)]
        return f'<g class="{self.walk()}">' + (
            f'<ellipse class="{arrive}" cy="{f((top + BASE) / 2)}" rx="30" ry="{f((BASE - top) / 2 + 30)}" fill="url(#beam-glow)"/>'
            f'<ellipse cy="{BASE - 6}" rx="80" ry="28" fill="url(#heat)" style="animation:pulse 2.2s ease-in-out infinite"/>'
            f'<rect x="-.8" y="{top}" width="1.6" height="{BASE - top}" fill="url(#beam)"/>'
            f'<rect x="{f(-tag_w / 2)}" y="{TAG_Y}" width="{f(tag_w)}" height="22" rx="11" fill="#0B111C" '
            f'stroke="{BTC}" stroke-opacity=".6"/>'
            + self.ticker(quotes, (-tag_w / 2, TAG_Y, tag_w, 22), 20)
            + f'<path d="M0 {BASE - 5}l5 5-5 5-5-5z" fill="{BTC}"/></g>')

    # ── axis ────────────────────────────────────────────────────────────────
    def ticks(self) -> list[tuple[float, float]]:
        s = self.s
        lo, hi = s.center * math.exp(-s.R), s.center * math.exp(s.R)
        step = next((st for st in (10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000)
                     if (hi - lo) / st <= 10), 10 ** math.ceil(math.log10((hi - lo) / 10)))
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
        color = BID[1] if p[-1] >= p[0] else ASK[1]
        line = "M" + "L".join(f"{f(x)} {f(y)}" for x, y in pts)
        area = line + f"L{f(pts[-1][0])} {floor}L{f(pts[0][0])} {floor}Z"
        change = (p[-1] / p[0] - 1) * 100
        moves = [f"transform:translate({f(x)}px,{f(y)}px)" for x, y in pts]
        cursor = self.animation("cursor", list(zip(self.stops, moves)) + [
            (HOLD_END, moves[-1] + ";opacity:1"), (94.5, moves[-1] + ";opacity:0"),
            (99.9, moves[0] + ";opacity:0"), (100, moves[0] + ";opacity:1")], moves[-1])
        return (f'<linearGradient id="sl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{color}" stop-opacity=".25"/>'
                f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient>'
                f'<path d="{area}" fill="url(#sl)"/>'
                f'<path d="{line}" fill="none" stroke="{color}" stroke-width="1.4" stroke-linejoin="round"/>'
                + text(ox - 10, floor - 2, f'60M <tspan fill="{color}">{signed(change, "+.2f")}%</tspan>', 11, MUTED, "end", ls=0.8)
                + f'<g class="{cursor}"><circle r="7" fill="{BTC}" opacity=".25"/><circle r="2.8" fill="#FFF3E0"/></g>')

    def headline(self) -> str:
        """The replayed mid with the minute it printed, and its change on the day."""
        s = self.s
        base, lh, n = 206, 70, len(s.prices)
        prices = "".join(f'<text x="{CX}" y="{base + i * lh}">{money(p)}</text>' for i, p in enumerate(s.prices))
        # Inter Display's tabular figures run ~0.62 em, separators ~0.3 em, plus 1 px tracking
        widest = max((money(p) for p in s.prices), key=len)
        half = (sum(0.62 if ch.isdigit() else 0.3 for ch in widest) * 58 + len(widest)) / 2
        ages = [text(CX + half + 16, base - 22 + i * 20, f"−{n - 1 - i}m", 13, MUTED) for i in range(n - 1)]
        ages.append(text(CX + half + 16, base - 22 + (n - 1) * 20, "NOW", 13, BTC, weight=700, ls=1))
        days = []
        for i, p in enumerate(s.prices):
            change = (p / s.m.open_24h - 1) * 100
            color, arrow = (BID[1], "▲") if change >= 0 else (ASK[1], "▼")
            days.append(text(CX, 232 + i * 18, f'<tspan fill="{color}">{arrow} {signed(change, "+.2f")}%</tspan>  24H   '
                                               f'<tspan fill="{DIM}">H</tspan> {money(s.m.high_24h, 0)}  '
                                               f'<tspan fill="{DIM}">L</tspan> {money(s.m.low_24h, 0)}', 13, MUTED, "middle"))
        return "".join([
            text(CX, 148, "BTC / USD", 12, MUTED, "middle", ls=5),
            f'<clipPath id="quote"><rect x="{CX - 330}" y="{base - 46}" width="660" height="56"/></clipPath>'
            f'<g clip-path="url(#quote)"><g class="{self.stepper(lh)}" font-family="{DISPLAY}" font-size="58" '
            f'font-weight="300" fill="#F4F7FB" text-anchor="middle" letter-spacing="1" '
            f'style="font-variant-numeric:tabular-nums">{prices}</g></g>',
            self.ticker(ages, (CX + half + 14, base - 36, 60, 18), 20),
            self.ticker(days, (CX - 300, 219, 600, 17), 18),
        ])

    # ── quant strip ─────────────────────────────────────────────────────────
    def stats(self) -> str:
        s = self.s
        band = f"±{f'{s.band * 100:.2f}'.rstrip('0').rstrip('.')}%"
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
