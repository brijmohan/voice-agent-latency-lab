"""Generate the README banner, light and dark.

The banner is not decoration: it draws the quantity the project measures. Two speech lanes on
one timeline, the caller's ending, the agent's starting, and the gap between them bracketed
and labelled with the real measured median. Every number here comes from
REPORT-speech-to-speech.md and none of it is invented.

    python tools/make_banner.py

GitHub renders README SVG through an image proxy, so CSS media queries inside the file do not
work. Two files plus a <picture> element is the only reliable way to be theme-aware.
"""

import math
from pathlib import Path

W, H = 1000, 268
TTFA_MEDIAN = "1.577 s"          # pooled median TTFA, n = 115
CAPTION_RIGHT = "TTFA median, n = 115 turns"

LANE_X0, LANE_X1 = 118, 952
HUMAN_Y, AGENT_Y = 128, 196
BAR_W, BAR_GAP = 4, 3

# Where each lane speaks, as a fraction of the timeline. The gap between them is the subject.
HUMAN_END = 0.42
AGENT_START = 0.63

THEMES = {
    "light": dict(fg="#1f2328", muted="#59636e", human="#1f6feb", agent="#8c959f",
                  accent="#cf222e", rule="#d1d9e0"),
    "dark": dict(fg="#e6edf3", muted="#9198a1", human="#4493f8", agent="#6e7681",
                 accent="#f85149", rule="#3d444d"),
}


def envelope(t: float, seed: float = 0.0) -> float:
    """A speech-shaped amplitude in [0,1].

    Three modulations at different rates, because a single sine reads as texture rather than
    speech. The slow term is the phrase contour, the middle term cuts word boundaries down
    near silence, and the fast term is syllabic. The word-boundary dips are what make it
    legible as talking rather than as a pattern.
    """
    phrase = math.sin(math.pi * t) ** 0.45
    words = abs(math.sin(t * 4.3 + seed)) ** 0.55
    syllables = 0.5 + 0.5 * abs(math.sin(t * 19.0 + seed * 2))
    jitter = 0.88 + 0.12 * math.sin(t * 31.0 + seed * 5)
    return max(0.04, phrase * (0.22 + 0.78 * words) * syllables * jitter)


def bars(x0, x1, cy, colour, max_h, seed=0.0):
    out = []
    n = int((x1 - x0) // (BAR_W + BAR_GAP))
    for i in range(n):
        t = (i + 0.5) / n
        h = max(3.0, envelope(t, seed) * max_h)
        x = x0 + i * (BAR_W + BAR_GAP)
        out.append(f'<rect x="{x:.1f}" y="{cy - h / 2:.1f}" width="{BAR_W}" '
                   f'height="{h:.1f}" rx="1.6" fill="{colour}"/>')
    return "\n    ".join(out)


def build(theme: str) -> str:
    c = THEMES[theme]
    span = LANE_X1 - LANE_X0
    h_end = LANE_X0 + span * HUMAN_END
    a_start = LANE_X0 + span * AGENT_START
    mid = (h_end + a_start) / 2
    mono = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    sans = "-apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif"

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"
     viewBox="0 0 {W} {H}" role="img" aria-label="apresvous: measuring when a voice agent decides it is your turn. Median time to first audio 1.577 seconds over 115 turns.">
  <text x="118" y="46" font-family="{mono}" font-size="30" font-weight="600" fill="{c['fg']}"
        letter-spacing="-0.4">apresvous</text>
  <text x="118" y="72" font-family="{sans}" font-size="14.5" fill="{c['muted']}">measuring when a voice agent decides it is your turn</text>

  <text x="60" y="{HUMAN_Y + 5}" font-family="{mono}" font-size="12.5" fill="{c['muted']}" text-anchor="end">vous</text>
  <text x="60" y="{AGENT_Y + 5}" font-family="{mono}" font-size="12.5" fill="{c['muted']}" text-anchor="end">agent</text>

  <g>
    {bars(LANE_X0, h_end, HUMAN_Y, c['human'], 50, seed=0.0)}
  </g>
  <g>
    {bars(a_start, LANE_X1, AGENT_Y, c['agent'], 40, seed=1.7)}
  </g>

  <line x1="{h_end:.1f}" y1="{HUMAN_Y - 30}" x2="{h_end:.1f}" y2="{AGENT_Y + 30}"
        stroke="{c['rule']}" stroke-width="1" stroke-dasharray="3 3"/>
  <line x1="{a_start:.1f}" y1="{HUMAN_Y - 30}" x2="{a_start:.1f}" y2="{AGENT_Y + 30}"
        stroke="{c['rule']}" stroke-width="1" stroke-dasharray="3 3"/>

  <g stroke="{c['accent']}" stroke-width="1.6" fill="none">
    <path d="M{h_end:.1f} {(HUMAN_Y + AGENT_Y) / 2:.1f} h{(a_start - h_end):.1f}"/>
    <path d="M{h_end:.1f} {(HUMAN_Y + AGENT_Y) / 2 - 6:.1f} v12"/>
    <path d="M{a_start:.1f} {(HUMAN_Y + AGENT_Y) / 2 - 6:.1f} v12"/>
  </g>
  <text x="{mid:.1f}" y="{(HUMAN_Y + AGENT_Y) / 2 - 13:.1f}" font-family="{mono}" font-size="15"
        font-weight="600" fill="{c['accent']}" text-anchor="middle">{TTFA_MEDIAN}</text>

  <line x1="118" y1="234" x2="952" y2="234" stroke="{c['rule']}" stroke-width="1"/>
  <text x="118" y="256" font-family="{sans}" font-size="14" fill="{c['fg']}"
        font-style="italic">&#171;&#160;apr&#232;s vous&#160;&#187;</text>
  <text x="952" y="256" font-family="{mono}" font-size="12" fill="{c['muted']}"
        text-anchor="end">{CAPTION_RIGHT}</text>
</svg>
'''


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[1] / "docs"
    for theme in THEMES:
        p = out / f"banner-{theme}.svg"
        p.write_text(build(theme))
        print(f"wrote {p.relative_to(p.parents[1])}  ({p.stat().st_size} bytes)")
