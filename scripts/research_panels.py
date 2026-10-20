#!/usr/bin/env python3
"""Research panels: the three fields, each as the equation that defines it.

  01  DeFi                 constant-product AMM        x · y = k
  02  Market microstructure Hawkes order flow           λ(t) = μ + Σ α e^{−β(t−tᵢ)}
  03  Exotic derivatives    up-and-out barrier, MC      dSₜ = μSₜ dt + σSₜ dWₜ

Every curve is computed here (a real hyperbola, a simulated Hawkes process, a
Monte Carlo of GBM paths), typeset in Computer Modern via the KaTeX fonts and
written as one animated SVG. Deterministic, standard library only.

    python3 scripts/research_panels.py --out assets/research.svg
"""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

from liquidity_field import ASK, BG0, BG1, BID, BTC, INK, LINE, MONO, MUTED, f, font_face, text

W, H = 1200, 380
GAP = 24
PW = (W - 2 * GAP) / 3                       # panel width
PX0, PX1 = 22, PW - 22                       # plot box, panel-local
PY0, PY1 = 116, 300
VIOLET = "#A08CFF"

MATH = "KTM,'KaTeX_Math','Latin Modern Math','Cambria Math','Times New Roman',serif"
MAIN = "KTR,'KaTeX_Main','Latin Modern Roman','Cambria Math','Times New Roman',serif"

# a formula is a list of (text, font, level): font "i" math italic, "r" roman
LEVELS = {0: (1.0, 0.0), "sup": (0.68, -0.42), "sub": (0.68, 0.24), "supsub": (0.5, -0.28)}


def formula(x: float, y: float, size: float, tokens: list[tuple], fill: str = INK) -> str:
    spans = []
    for s, font, *level in tokens:
        scale, shift = LEVELS[level[0] if level else 0]
        spans.append(f'<tspan font-family="{MATH if font == "i" else MAIN}" font-size="{f(size * scale)}" '
                     f'y="{f(y + shift * size)}">{s}</tspan>')
    return f'<text x="{f(x)}" y="{f(y)}" fill="{fill}">{"".join(spans)}</text>'


def polyline(pts: list[tuple[float, float]]) -> str:
    return "M" + "L".join(f"{f(x)} {f(y)}" for x, y in pts)


class Panels:
    def __init__(self, seed: int = 11):
        self.rng = random.Random(seed)

    def svg(self) -> str:
        panels = [self.amm(), self.hawkes(), self.barrier()]
        body = "".join(f'<g transform="translate({f(i * (PW + GAP))} 0)">{p}</g>' for i, p in enumerate(panels))
        return "\n".join([
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
            f'font-family="{MONO}" role="img" aria-labelledby="t d">',
            '<title id="t">GRZ_Research · research focus</title>',
            '<desc id="d">Three animated panels: a constant-product AMM curve (DeFi), a simulated Hawkes '
            'order-flow intensity (market microstructure) and Monte Carlo paths of an up-and-out barrier '
            'option (exotic derivatives).</desc>',
            self.defs(),
            body,
            "</svg>",
        ])

    def defs(self) -> str:
        css = f"""
{font_face("JBM", "jbm-400.woff2", 400)}
{font_face("JBM", "jbm-700.woff2", 700)}
{font_face("KTM", "katex-math.woff2", 400)}
{font_face("KTR", "katex-main.woff2", 400)}
.draw{{stroke-dasharray:1;stroke-dashoffset:0;animation:draw 10s cubic-bezier(.45,0,.25,1) infinite}}
@keyframes draw{{0%{{stroke-dashoffset:1;opacity:1}} 55%{{stroke-dashoffset:0}} 90%{{opacity:1}} 98%,100%{{stroke-dashoffset:0;opacity:0}}}}
.scan{{animation:scan 9s linear infinite}}
@keyframes scan{{0%{{transform:translateX(0);opacity:1}} 70%{{transform:translateX({f(PX1 - PX0)}px);opacity:1}} 78%,100%{{transform:translateX({f(PX1 - PX0)}px);opacity:0}}}}
.cycle{{animation:cycle 9s linear infinite}}
@keyframes cycle{{0%{{opacity:0}} 3%,88%{{opacity:1}} 97%,100%{{opacity:0}}}}
@keyframes pulse{{0%,100%{{opacity:.35}} 50%{{opacity:1}}}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important}}}}
"""
        grads = "".join(
            f'<linearGradient id="fill-{n}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{c}" stop-opacity=".28"/>'
            f'<stop offset="1" stop-color="{c}" stop-opacity="0"/></linearGradient>'
            for n, c in (("mint", BID[1]), ("btc", BTC), ("violet", VIOLET)))
        return (f'<defs><style>{css}</style>'
                f'<linearGradient id="card" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{BG1}"/>'
                f'<stop offset="1" stop-color="{BG0}"/></linearGradient>'
                f'<pattern id="dotgrid" width="24" height="24" patternUnits="userSpaceOnUse">'
                f'<circle cx="12" cy="12" r=".7" fill="#1A2232"/></pattern>{grads}</defs>')

    # ── shared chrome ───────────────────────────────────────────────────────
    def card(self, index: str, field: str, model: str, accent: str, caption: str) -> str:
        return (f'<rect x=".5" y=".5" width="{f(PW - 1)}" height="{H - 1}" rx="16" fill="url(#card)" stroke="#1B2333"/>'
                f'<rect x=".5" y=".5" width="{f(PW - 1)}" height="{H - 1}" rx="16" fill="url(#dotgrid)" opacity=".7"/>'
                + text(PX0, 38, f'<tspan fill="{accent}">{index}</tspan>  {field}', 12, INK, weight=700, ls=2)
                + text(PX1, 38, model, 10.5, MUTED, "end", ls=1.2)
                + f'<line x1="{PX0}" x2="{f(PX1)}" y1="54" y2="54" stroke="{LINE}"/>'
                + text(PX0, 346, caption, 12, MUTED))

    def axes(self, xlabel: str, ylabel: str, right: float = PX1) -> str:
        return (f'<path d="M{PX0} {PY0 - 6}V{PY1}H{f(right)}" fill="none" stroke="#2A3346"/>'
                + formula(right - 10, PY1 + 18, 15, [(xlabel, "i")], MUTED)
                + formula(PX0 + 6, PY0 + 4, 15, [(ylabel, "i")], MUTED))

    # ── 01 · constant-product AMM ───────────────────────────────────────────
    def amm(self) -> str:
        k, lo, hi = 4.0, 0.0, 5.2
        sx = lambda v: PX0 + (PX1 - PX0) * (v - lo) / (hi - lo)   # noqa: E731
        sy = lambda v: PY1 - (PY1 - PY0) * (v - lo) / (hi - lo)   # noqa: E731

        def curve(kk: float, a: float, b: float, n: int = 90) -> list[tuple[float, float]]:
            xs = [a * (b / a) ** (i / (n - 1)) for i in range(n)]          # log spacing hugs the knee
            return [(sx(x), sy(kk / x)) for x in xs if kk / x <= hi]

        out = [self.card("01", "DEFI", "CONSTANT-PRODUCT AMM", BID[1], "liquidity · price impact · MEV"),
               formula(PX0, 92, 27, [("x", "i"), (" ⋅ ", "r"), ("y", "i"), (" = ", "r"), ("k", "i")])]
        out.append(self.axes("x", "y"))
        for kk, alpha in ((1.6, 0.18), (9.0, 0.18)):                       # shallower / deeper pools
            out.append(f'<path d="{polyline(curve(kk, kk / hi, hi))}" fill="none" stroke="{BID[1]}" '
                       f'stroke-opacity="{alpha}" stroke-dasharray="2 4"/>')
        main = curve(k, k / hi, hi)
        out.append(f'<path d="{polyline(main)}" fill="none" stroke="{BID[1]}" stroke-width="6" stroke-opacity=".1"/>'
                   f'<path d="{polyline(main)}" fill="none" stroke="{BID[1]}" stroke-width="1.8"/>')

        (xa, xb) = (1.35, 3.3)
        ya, yb = k / xa, k / xb
        A, B = (sx(xa), sy(ya)), (sx(xb), sy(yb))
        out.append(f'<g stroke="{MUTED}" stroke-opacity=".55" stroke-dasharray="2 3" fill="none">'
                   f'<path d="M{f(A[0])} {PY1}V{f(A[1])}H{PX0}"/><path d="M{f(B[0])} {PY1}V{f(B[1])}H{PX0}"/></g>')
        # Δx along the x-axis, Δy along the y-axis
        out.append(f'<path d="M{f(A[0])} {PY1 - 8}H{f(B[0])}M{PX0 + 8} {f(A[1])}V{f(B[1])}" stroke="{BTC}" stroke-width="1.4"/>'
                   + formula((A[0] + B[0]) / 2 - 10, PY1 - 16, 15, [("Δ", "i"), ("x", "i")], BTC)
                   + formula(PX0 + 16, (A[1] + B[1]) / 2 + 5, 15, [("Δ", "i"), ("y", "i")], BTC))
        for (x, y), label in ((A, "A"), (B, "B")):
            out.append(f'<circle cx="{f(x)}" cy="{f(y)}" r="3.2" fill="{BG0}" stroke="{BID[0]}" stroke-width="1.4"/>'
                       + text(x + 8, y - 8, label, 11, BID[0], weight=700))
        # a swap travelling A → B → A with its tangent (the marginal price) riding along
        seg = curve(k, xa, xb, 40)
        out.append(f'<path id="swap" d="{polyline(seg)}" fill="none"/>'
                   f'<g><line x1="-46" x2="46" stroke="{BTC}" stroke-width="1.2" stroke-opacity=".8"/>'
                   f'<circle r="8" fill="{BTC}" opacity=".22"/><circle r="3.6" fill="#FFF1DE"/>'
                   f'<animateMotion dur="7s" repeatCount="indefinite" rotate="auto" calcMode="spline" '
                   f'keyPoints="0;1;1;0;0" keyTimes="0;.42;.5;.92;1" '
                   f'keySplines=".6 0 .4 1;0 0 1 1;.6 0 .4 1;0 0 1 1"><mpath href="#swap"/></animateMotion></g>')
        out.append(formula(PX1 - 92, PY0 + 22, 16, [("p", "i"), (" = ", "r"), ("y", "i"), ("/", "r"), ("x", "i")], MUTED))
        return "".join(out)

    # ── 02 · Hawkes order flow ──────────────────────────────────────────────
    def hawkes(self) -> str:
        mu, alpha, beta, T = 0.45, 1.05, 1.7, 36.0
        events = self.simulate_hawkes(mu, alpha, beta, T)

        def lam(t: float) -> float:
            return mu + sum(alpha * math.exp(-beta * (t - ti)) for ti in events if ti < t)

        peak = max(lam(t + 1e-9) for t in events) * 1.12
        sx = lambda t: PX0 + 4 + (PX1 - PX0 - 8) * t / T          # noqa: E731
        sy = lambda v: PY1 - (PY1 - PY0) * v / peak                # noqa: E731
        pts, t = [], 0.0
        for ti in events + [T]:                                     # decay to each event, then jump
            n = max(2, int((ti - t) * 6))
            pts += [(sx(t + (ti - t) * j / n), sy(lam(t + (ti - t) * j / n))) for j in range(n + 1)]
            if ti < T:
                pts.append((sx(ti), sy(lam(ti + 1e-9))))
            t = ti
        line = polyline(pts)
        area = line + f"L{f(PX1 - 4)} {PY1}L{PX0 + 4} {PY1}Z"

        out = [self.card("02", "MICROSTRUCTURE", "HAWKES ORDER FLOW", BTC, "order flow · clustering · price impact"),
               formula(PX0, 92, 25, [("λ", "i"), ("(", "r"), ("t", "i"), (")", "r"), (" = ", "r"), ("μ", "i"),
                                     (" + ", "r"), ("Σ", "r"), ("i", "i", "sub"), (" α", "i"), ("e", "i"),
                                     ("−", "r", "sup"), ("β", "i", "sup"), ("(", "r", "sup"), ("t", "i", "sup"),
                                     ("−", "r", "sup"), ("t", "i", "sup"), ("i", "i", "supsub"), (")", "r", "sup")])]
        out.append(self.axes("t", "λ"))
        out.append(f'<line x1="{PX0}" x2="{f(PX1)}" y1="{f(sy(mu))}" y2="{f(sy(mu))}" stroke="{BTC}" stroke-opacity=".45" stroke-dasharray="3 4"/>'
                   + formula(PX1 + 4, sy(mu) + 5, 14, [("μ", "i")], BTC))
        clip_w = PX1 - PX0
        out.append(f'<clipPath id="reveal"><rect x="{PX0}" y="{PY0 - 20}" width="{f(clip_w)}" height="{PY1 - PY0 + 30}">'
                   f'<animate attributeName="width" values="0;{f(clip_w)};{f(clip_w)}" keyTimes="0;.7;1" dur="9s" repeatCount="indefinite"/>'
                   f'</rect></clipPath>')
        out.append(f'<g class="cycle"><g clip-path="url(#reveal)">'
                   f'<path d="{area}" fill="url(#fill-btc)"/>'
                   f'<path d="{line}" fill="none" stroke="{BTC}" stroke-width="1.6" stroke-linejoin="round"/></g>')
        for ti in events:                                           # each arrival lights up as the scan passes
            x, at = sx(ti), 0.7 * ti / T
            out.append(f'<g opacity="0"><line x1="{f(x)}" x2="{f(x)}" y1="{PY1}" y2="{PY1 - 9}" stroke="{INK}" stroke-opacity=".7"/>'
                       f'<circle cx="{f(x)}" cy="{f(sy(lam(ti + 1e-9)))}" r="2.4" fill="#FFF1DE"/>'
                       f'<animate attributeName="opacity" values="0;0;1;1" keyTimes="0;{at:.4f};{min(at + 0.01, 0.99):.4f};1" '
                       f'dur="9s" repeatCount="indefinite"/></g>')
        out.append(f'<g class="scan"><line x1="{PX0}" x2="{PX0}" y1="{PY0 - 8}" y2="{PY1}" stroke="{BTC}" stroke-opacity=".6"/>'
                   f'<circle cx="{PX0}" cy="{PY1}" r="3" fill="{BTC}"/></g></g>')
        out.append(text(PX0 + 26, PY0 + 4, f"branching α/β = {alpha / beta:.2f} · {len(events)} events", 11, MUTED))
        return "".join(out)

    def simulate_hawkes(self, mu: float, alpha: float, beta: float, T: float) -> list[float]:
        """Ogata thinning: exact event times of a self-exciting point process."""
        rng, t, events = self.rng, 0.0, []
        while True:
            bound = mu + sum(alpha * math.exp(-beta * (t - ti)) for ti in events) + alpha
            t += rng.expovariate(bound)
            if t >= T:
                return events
            lam = mu + sum(alpha * math.exp(-beta * (t - ti)) for ti in events)
            if rng.random() * bound <= lam:
                events.append(t)

    # ── 03 · up-and-out barrier, Monte Carlo ────────────────────────────────
    def barrier(self) -> str:
        rng = self.rng
        s0, mu, sigma, steps, n_paths, B = 100.0, 0.04, 0.55, 110, 26, 150.0
        dt = 1.0 / steps
        paths = []
        for _ in range(n_paths):
            s, path = s0, [s0]
            for _ in range(steps):
                s *= math.exp((mu - 0.5 * sigma ** 2) * dt + sigma * math.sqrt(dt) * rng.gauss(0, 1))
                path.append(s)
            paths.append(path)
        lo, hi = 45.0, 170.0
        plot_r = PX1 - 54                                            # leave room for the terminal density
        sx = lambda i: PX0 + 4 + (plot_r - PX0 - 4) * i / steps     # noqa: E731
        sy = lambda v: PY1 - (PY1 - PY0) * (min(max(v, lo), hi) - lo) / (hi - lo)  # noqa: E731

        out = [self.card("03", "EXOTIC DERIVATIVES", "UP-AND-OUT · MC", VIOLET,
                         "path-dependence · stochastic vol · Monte Carlo"),
               formula(PX0, 92, 25, [("d", "i"), ("S", "i"), ("t", "i", "sub"), (" = ", "r"), ("μ", "i"), ("S", "i"),
                                     ("t", "i", "sub"), (" d", "i"), ("t", "i"), (" + ", "r"), ("σ", "i"), ("S", "i"),
                                     ("t", "i", "sub"), (" d", "i"), ("W", "i"), ("t", "i", "sub")])]
        out.append(self.axes("t", "S", plot_r))
        out.append(f'<line x1="{PX0}" x2="{f(plot_r)}" y1="{f(sy(B))}" y2="{f(sy(B))}" stroke="{ASK[1]}" stroke-dasharray="4 4" '
                   f'stroke-opacity=".8"/>' + formula(plot_r + 6, sy(B) + 5, 15, [("B", "i")], ASK[1]))
        out.append(f'<line x1="{PX0}" x2="{f(plot_r)}" y1="{f(sy(s0))}" y2="{f(sy(s0))}" stroke="{MUTED}" stroke-opacity=".25"/>'
                   + formula(plot_r + 6, sy(s0) + 5, 14, [("S", "i"), ("0", "r", "sub")], MUTED))
        survivors = []
        for j, path in enumerate(paths):
            hit = next((i for i, v in enumerate(path) if v >= B), None)
            shown = path if hit is None else path[: hit + 1]
            pts = [(sx(i), sy(v)) for i, v in enumerate(shown)]
            if hit is not None:                                      # end exactly on the barrier
                frac = (B - path[hit - 1]) / (path[hit] - path[hit - 1])
                pts[-1] = (sx(hit - 1 + frac), sy(B))
            delay = j * 0.11
            color, alpha = (VIOLET, 0.75) if hit is None else (ASK[1], 0.9)
            out.append(f'<path d="{polyline(pts)}" pathLength="1" class="draw" fill="none" stroke="{color}" '
                       f'stroke-opacity="{alpha}" stroke-width="1.1" style="animation-delay:{delay:.2f}s"/>')
            if hit is None:
                survivors.append(path[-1])
            else:                                                    # knocked out: mark the touch
                x, y = pts[-1]
                at = 0.55 * (len(shown) - 1) / (len(path) - 1)     # ≈ when the stroke reaches the barrier
                out.append(f'<g opacity="0" stroke="{ASK[0]}" stroke-width="1.6" stroke-linecap="round">'
                           f'<path d="M{f(x - 3.5)} {f(y - 3.5)}l7 7m0-7l-7 7"/>'
                           f'<animate attributeName="opacity" values="0;0;1;1;0;0" '
                           f'keyTimes="0;{at:.3f};{at + 0.02:.3f};.9;.98;1" dur="10s" begin="{delay:.2f}s" repeatCount="indefinite"/></g>')
        out.append(self.terminal_density(survivors, plot_r + 4, sy))
        knocked = n_paths - len(survivors)
        out.append(text(PX0 + 26, PY0 + 4, f"{knocked}/{n_paths} paths knocked out", 11, MUTED))
        return "".join(out)

    @staticmethod
    def terminal_density(values: list[float], x0: float, sy) -> str:
        """Gaussian KDE of surviving S_T, drawn sideways against the right edge."""
        if len(values) < 2:
            return ""
        m = sum(values) / len(values)
        sd = math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1)) or 1.0
        bw = 1.06 * sd * len(values) ** -0.2
        grid = [45 + i * (150 - 45) / 60 for i in range(61)]
        dens = [sum(math.exp(-0.5 * ((g - v) / bw) ** 2) for v in values) for g in grid]
        peak = max(dens)
        pts = [(x0 + 40 * d / peak, sy(g)) for g, d in zip(grid, dens)]
        d = polyline([(x0, sy(grid[0]))] + pts + [(x0, sy(grid[-1]))])
        return (f'<path d="{d}Z" fill="url(#fill-violet)" stroke="{VIOLET}" stroke-width="1.1" stroke-opacity=".8"/>'
                + text(x0 + 2, PY1 + 16, "p(S_T)", 10, MUTED))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("assets/research.svg"))
    args = ap.parse_args()
    svg = Panels().svg()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(svg, encoding="utf-8")
    print(f"{len(svg) / 1024:.0f} KiB → {args.out}")


if __name__ == "__main__":
    main()
