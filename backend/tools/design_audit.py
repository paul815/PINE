#!/usr/bin/env python3
"""
design_audit.py — PINE Design Consistency Audit

Parses all Jinja2 templates, extracts CSS rules for heading/section/label
classes, and reports mismatches in font-size, font-weight, and other
visual properties across pages.

Run from repo root with the venv active:
    python backend/tools/design_audit.py
"""

import re
import sys
from pathlib import Path
from typing import Optional

# ── Configuration ─────────────────────────────────────────────────────────────

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
BASE_FONT_SIZE_PX = 16  # browser default

# Semantic role → CSS classes that fill that role on each page.
#
# Group classes that should look the *same* across pages.
# Classes in different visual contexts (e.g. tiny topbar label vs big step
# heading) should be in separate roles or omitted to avoid false positives.
# Edit freely — this is the main thing to tune.
ROLE_MAP: dict[str, list[str]] = {
    # Section headings used as group titles inside a content area.
    # main uses .section-title; settings + tags use .sec-title.
    "section heading": [
        "sec-title",     # settings, tags: section group header
        "section-title", # main: field-group label
    ],
    # Modal / overlay dialog titles — should look consistent across pages.
    "modal title": [
        "modal-title",   # recording, main: modal dialog heading
    ],
    # The app-name badge in the shared top bar (main / settings / tags).
    "topbar app name": [
        "tb-name",       # main, settings, tags
    ],
    # Supporting hint / description text beneath labels.
    # These are in different visual contexts so font-size variation is expected;
    # flag it anyway so you can decide if it's intentional.
    "hint / description text": [
        "panel-desc",    # onboarding: paragraph under step title (~13 px)
        "row-sub",       # settings: hint line under row label (~15.9 px)
        "tag-sub",       # tags: usage-count subtitle (~14.6 px)
    ],
    # Compact uppercase metadata labels (small, spaced, often muted).
    "compact uppercase label": [
        "panel-eye",     # onboarding: step kicker above panel-title
        "part-info-lbl", # recording: participant detail field label
    ],
}

# Properties compared for consistency within each role
COMPARE_PROPS = [
    "font-size",
    "font-weight",
    "color",
    "letter-spacing",
    "text-transform",
    "line-height",
]

# ── CSS helpers ───────────────────────────────────────────────────────────────

def strip_jinja(text: str) -> str:
    """Remove Jinja2 template expressions so they don't confuse the CSS parser."""
    text = re.sub(r'\{%-?.*?-?%\}', '', text, flags=re.DOTALL)
    text = re.sub(r'\{\{.*?\}\}', '"JINJA"', text, flags=re.DOTALL)
    return text


def extract_style_blocks(html: str) -> str:
    """Return concatenated content of all <style> blocks."""
    return '\n'.join(re.findall(r'<style[^>]*>(.*?)</style>', html, re.DOTALL))


def strip_css_comments(css: str) -> str:
    return re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)


def normalize_font_size(value: str, html_rem_scale: float = 1.0) -> str:
    """Resolve rem/em to px for consistent comparison."""
    v = value.strip()
    try:
        if v.endswith('rem'):
            px = float(v[:-3]) * BASE_FONT_SIZE_PX * html_rem_scale
            return f"{px:.1f}px"
        if v.endswith('em'):
            px = float(v[:-2]) * BASE_FONT_SIZE_PX
            return f"{px:.1f}px"
        if v.endswith('px'):
            return f"{float(v[:-2]):.1f}px"
    except ValueError:
        pass
    return v


def parse_css(css: str) -> list[tuple[list[str], dict[str, str]]]:
    """
    Lightweight CSS parser.
    Returns [(selectors_list, {prop: value})] ignoring @-rules wrappers
    (but still parsing the rules inside them).
    """
    css = strip_css_comments(css)
    # Flatten @-rule wrappers: keep inner content, drop the @-rule line itself
    # e.g.  @media (...) { .foo { color: red } }  →  .foo { color: red }
    # We do this by stripping @-rule preambles before each inner block level.
    css = re.sub(r'@[^{]+\{', '', css)   # drop "@media (...) {" lines
    # Remove any now-unbalanced closing braces at top level is tricky;
    # instead just parse all { } pairs naively.

    results = []
    for m in re.finditer(r'([^{@][^{]*?)\s*\{([^{}]*)\}', css):
        raw_selectors = m.group(1).strip()
        declarations  = m.group(2).strip()
        if not raw_selectors or not declarations:
            continue
        props: dict[str, str] = {}
        for decl in declarations.split(';'):
            if ':' in decl:
                prop, _, val = decl.partition(':')
                p, v = prop.strip(), val.strip()
                if p:
                    props[p] = v
        if not props:
            continue
        selectors = [s.strip() for s in raw_selectors.split(',') if s.strip()]
        results.append((selectors, props))
    return results


def rules_for_class(
    parsed: list[tuple[list[str], dict[str, str]]],
    class_name: str,
) -> Optional[dict[str, str]]:
    """
    Return merged CSS properties for a given class name.
    Only considers direct (non-contextual) rules — i.e. selectors that have
    no parent segment (`.foo { }`, not `.parent .foo { }`).
    This avoids contextual overrides like `.reset-section .sec-title { color: red }`
    polluting the base style comparison.
    Later rules win (same as CSS cascade).
    """
    merged: dict[str, str] = {}
    for selectors, props in parsed:
        for sel in selectors:
            # Strip pseudo-classes / pseudo-elements
            sel_clean = re.sub(r'::?[\w-]+(\([^)]*\))?', '', sel)
            segments = [s for s in re.split(r'[\s>~+]+', sel_clean) if s.strip()]
            if not segments:
                continue
            # Only accept rules where the class is the sole selector segment
            # (skip descendant / child / sibling contextual rules)
            if len(segments) != 1:
                continue
            classes = re.findall(r'\.([\w-]+)', segments[0])
            if class_name in classes:
                merged.update(props)
    return merged if merged else None


def get_html_rem_scale(css: str) -> float:
    """Read `html { font-size: X }` to get the rem multiplier."""
    m = re.search(r'\bhtml\b[^{]*\{[^}]*font-size:\s*([^;]+)', css)
    if m:
        v = m.group(1).strip()
        try:
            if v.endswith('rem'):
                return float(v[:-3])
            if v.endswith('px'):
                return float(v[:-2]) / BASE_FONT_SIZE_PX
        except ValueError:
            pass
    return 1.0


# ── Data collection ───────────────────────────────────────────────────────────

def collect() -> dict[str, dict[str, Optional[dict[str, str]]]]:
    """
    Returns {template_stem: {css_class: {prop: value | None}}}.
    font-size values are normalised to px.
    """
    templates = sorted(TEMPLATES_DIR.glob("*.html"))
    if not templates:
        print(f"ERROR: no templates found in {TEMPLATES_DIR}", file=sys.stderr)
        sys.exit(1)

    all_classes = {c for cs in ROLE_MAP.values() for c in cs}
    data: dict[str, dict[str, Optional[dict[str, str]]]] = {}

    for tmpl in templates:
        name = tmpl.stem
        html  = strip_jinja(tmpl.read_text(encoding="utf-8"))
        css   = extract_style_blocks(html)
        scale = get_html_rem_scale(css)
        rules = parse_css(css)

        # Normalise font-sizes in parsed rules
        for _, props in rules:
            if "font-size" in props:
                props["font-size"] = normalize_font_size(props["font-size"], scale)

        data[name] = {cls: rules_for_class(rules, cls) for cls in all_classes}

    return data


# ── Reporting ─────────────────────────────────────────────────────────────────

_RESET  = "\033[0m"
_YELLOW = "\033[33m"
_GREEN  = "\033[32m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"

def _col(text: str, code: str) -> str:
    return code + text + _RESET if sys.stdout.isatty() else text


def _mismatch_props(
    rows: list[tuple[str, str, dict[str, str]]],
) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """
    Return [(prop, [(template, class, value)])] for props that differ
    across the supplied rows.
    """
    mismatches = []
    for prop in COMPARE_PROPS:
        entries = [
            (tmpl, cls, props[prop])
            for tmpl, cls, props in rows
            if props.get(prop)
        ]
        unique_vals = {v for _, _, v in entries}
        if len(unique_vals) > 1:
            mismatches.append((prop, entries))
    return mismatches


def print_report(data: dict[str, dict[str, Optional[dict[str, str]]]]) -> int:
    templates = sorted(data.keys())
    all_classes = {c for cs in ROLE_MAP.values() for c in cs}
    has_mismatch = False

    print()
    print(_col("═" * 68, _BOLD))
    print(_col("  PINE — Design Consistency Audit", _BOLD))
    print(_col("═" * 68, _BOLD))
    print()

    for role, classes in ROLE_MAP.items():
        rows: list[tuple[str, str, dict[str, str]]] = []
        for tmpl in templates:
            for cls in classes:
                props = data[tmpl].get(cls)
                if props:
                    rows.append((tmpl, cls, props))

        if not rows:
            continue

        print(_col(f"▸ {role}", _BOLD))
        c0, c1, c2, c3, c4, c5, c6 = 12, 16, 10, 7, 26, 14, 12
        hdr = (
            f"  {'template':<{c0}}  {'class':<{c1}}  "
            f"{'font-size':<{c2}}  {'weight':<{c3}}  "
            f"{'color':<{c4}}  {'tracking':<{c5}}  "
            f"{'transform':<{c6}}"
        )
        print(_col(hdr, _DIM))
        print("  " + "─" * (c0 + c1 + c2 + c3 + c4 + c5 + c6 + 12))

        for tmpl, cls, props in rows:
            fs  = props.get("font-size",       "—")
            fw  = props.get("font-weight",     "—")
            col = props.get("color",           "—")
            ls  = props.get("letter-spacing",  "—")
            tt  = props.get("text-transform",  "—")
            print(
                f"  {tmpl:<{c0}}  .{cls:<{c1-1}}  "
                f"{fs:<{c2}}  {fw:<{c3}}  "
                f"{col:<{c4}}  {ls:<{c5}}  "
                f"{tt}"
            )

        mismatches = _mismatch_props(rows)
        if mismatches:
            has_mismatch = True
            for prop, entries in mismatches:
                vals = "  vs  ".join(
                    f"{tmpl}:{cls}={_col(v, _YELLOW)}"
                    for tmpl, cls, v in entries
                )
                print(_col(f"  ⚠  {prop}: {vals}", _YELLOW))
        print()

    # ── Unmapped heading-like classes ─────────────────────────────────────────
    kw = re.compile(r'title|heading|lbl|label|sub|sec[-_]|section|header|name|desc|kicker', re.I)
    unmapped = []
    for tmpl in templates:
        for cls, props in sorted(data[tmpl].items()):
            if props and cls not in all_classes and kw.search(cls):
                unmapped.append((tmpl, cls, props))

    if unmapped:
        print(_col("▸ Unmapped heading-like classes (consider adding to ROLE_MAP)", _DIM))
        for tmpl, cls, props in unmapped:
            line = "  " + tmpl + "  ." + cls
            bits = [f"{p}:{props[p]}" for p in COMPARE_PROPS if props.get(p)]
            if bits:
                line += "  →  " + "  ".join(bits)
            print(line)
        print()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("─" * 68)
    if has_mismatch:
        print(_col("RESULT: ⚠  Inconsistencies detected — see ⚠ lines above.", _YELLOW))
    else:
        print(_col("RESULT: ✓  No inconsistencies found in tracked roles.", _GREEN))
    print()
    return 1 if has_mismatch else 0


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    data = collect()
    sys.exit(print_report(data))


if __name__ == "__main__":
    main()
