---
colors:
  light:
    bg: "hsl(220, 20%, 97%)"
    fg: "hsl(220, 25%, 10%)"
    card: "hsl(0, 0%, 100%)"
    muted: "hsl(220, 15%, 94%)"
    muted-fg: "hsl(220, 10%, 46%)"
    accent: "hsl(152, 60%, 42%)"
    accent-fg: "hsl(220, 25%, 10%)"
    accent-dim: "hsla(152, 60%, 42%, 0.10)"
    accent-bd: "hsla(152, 60%, 42%, 0.25)"
    accent-on-dim: "hsl(152, 60%, 27%)"
    accent-bd-solid: "hsl(152, 60%, 27%)"
    border: "hsl(220, 15%, 88%)"
    secondary: "hsl(220, 15%, 92%)"
    sunken: "hsl(220, 20%, 95%)"
    destr: "hsl(0, 72%, 51%)"
    destr-dim: "hsla(0, 72%, 51%, 0.10)"
    destr-bd: "hsla(0, 72%, 51%, 0.25)"
    warn: "hsl(38, 90%, 55%)"
    warn-dim: "hsla(38, 90%, 55%, 0.10)"
    warn-bd: "hsla(38, 90%, 55%, 0.25)"
  dark:
    bg: "hsl(220, 20%, 7%)"
    fg: "hsl(220, 15%, 93%)"
    card: "hsl(220, 16%, 11%)"
    muted: "hsl(220, 14%, 14%)"
    muted-fg: "hsl(220, 10%, 54%)"
    accent: "hsl(152, 55%, 45%)"
    accent-fg: "hsl(220, 25%, 10%)"
    accent-dim: "hsla(152, 55%, 45%, 0.12)"
    accent-bd: "hsla(152, 55%, 45%, 0.28)"
    accent-on-dim: "hsl(152, 55%, 45%)"
    accent-bd-solid: "hsl(152, 55%, 45%)"
    border: "hsl(220, 16%, 17%)"
    secondary: "hsl(220, 14%, 16%)"
    sunken: "hsl(220, 18%, 9%)"
    destr: "hsl(0, 62%, 60%)"
    destr-dim: "hsla(0, 62%, 60%, 0.12)"
    destr-bd: "hsla(0, 62%, 60%, 0.28)"
    warn: "hsl(38, 85%, 60%)"
    warn-dim: "hsla(38, 85%, 60%, 0.10)"
    warn-bd: "hsla(38, 85%, 60%, 0.25)"

typography:
  font-body: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
  font-mono: "'JetBrains Mono', monospace"
  scale:
    sm: "0.846rem"
    md: "0.923rem"
    lg: "1rem"

layout:
  radius: "6px"
  top-bar-h: "20px"
  left-header-pad-y: "6px"
  left-header-gap: "6px"
  search-input-h: "28px"

components:
  button:
    radius: "8px"
    gap: "6px"
    height:
      sm: "30px"
      md: "34px"
      lg: "38px"
    padding-x:
      sm: "10px"
      md: "14px"
      lg: "16px"
    icon-size:
      xs: "20px"
      sm: "22px"
      md: "28px"
    font-size:
      sm: "0.846rem"
      md: "0.923rem"
      lg: "1rem"
    focus-ring: "0 0 0 3px var(--accent-dim)"
    transition: "background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease, box-shadow 0.15s ease, opacity 0.15s ease, transform 0.15s ease"
  button-primary:
    backgroundColor: "hsl(152, 60%, 42%)"
    textColor: "#ffffff"
  button-primary-dark:
    backgroundColor: "hsl(152, 55%, 45%)"
    textColor: "#ffffff"
  button-secondary:
    backgroundColor: "hsl(220, 15%, 92%)"
    textColor: "hsl(220, 25%, 10%)"
  button-secondary-dark:
    backgroundColor: "hsl(220, 14%, 16%)"
    textColor: "hsl(220, 15%, 93%)"
  button-destructive:
    backgroundColor: "hsl(0, 72%, 51%)"
    textColor: "#ffffff"
  button-destructive-dark:
    backgroundColor: "hsl(0, 62%, 60%)"
    textColor: "#ffffff"
  button-muted:
    backgroundColor: "hsl(220, 15%, 94%)"
    textColor: "hsl(220, 10%, 46%)"
  button-muted-dark:
    backgroundColor: "hsl(220, 14%, 14%)"
    textColor: "hsl(220, 10%, 54%)"
---

## Overview

Pine is a macOS desktop app for audio recording and transcription. The UI is calm and focused — built for long work sessions where distraction must be minimal. The palette is cool gray (hsl 220) with a green accent (`hsl(152, …)`). Both light and dark themes are first-class: the app ships with a theme toggle and respects user preference.

The app layout is a two-panel design: a narrow left sidebar for navigation and a main content area for recordings, transcripts, and settings.

## Colors

All color usage goes through semantic CSS custom properties (`var(--token)`), never raw hex or HSL literals in component code.

| Token | Role |
|---|---|
| `--bg` | Page/window background |
| `--fg` | Primary text |
| `--card` | Surface cards, panels, dialogs |
| `--muted` | Subtle backgrounds (hover states, secondary panels) |
| `--muted-fg` | Secondary/placeholder text |
| `--accent` | Primary brand green — buttons, active states, links |
| `--accent-fg` | Text on accent backgrounds (always white) |
| `--accent-dim` | Translucent accent for focus rings, hover tints |
| `--accent-bd` | Accent-colored borders |
| `--border` | Default dividers and input borders |
| `--secondary` | Secondary button backgrounds |
| `--sunken` | Recessed areas (code blocks, input backgrounds) |
| `--destr` | Destructive actions (delete, reset) |
| `--destr-dim` | Translucent destructive for hover tints |
| `--destr-bd` | Destructive-colored borders |
| `--warn` | Warning states |
| `--warn-dim` | Translucent warning for tints |
| `--warn-bd` | Warning-colored borders |

Semantic colors travel in triplets: a solid color (`--accent`), a translucent fill (`--accent-dim`, 10–12% opacity), and a border (`--accent-bd`, 25–28% opacity). Always use all three together when building a new colored interactive surface.

## Typography

**Body font** (`--font-body`): system font stack — `-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`. Renders natively on macOS without loading a web font.

**Mono font** (`--font-mono`): JetBrains Mono — used exclusively for transcript text and timestamps. Bundled as WOFF2.

**Font size scale** — three steps only:
- `0.846rem` — small labels, metadata, sidebar items
- `0.923rem` — default UI text (most elements)
- `1rem` — headings, primary actions

The user can override the body font family and font size at runtime through Settings. Components must use `var(--font-body)` so the override propagates.

## Layout

The app shell is a fixed-height window (no scroll on the outer frame). Layout uses CSS flexbox and grid.

- **Left sidebar**: fixed-width, contains navigation and project list
- **Main panel**: fills remaining space, scrolls internally
- **Top bar**: `20px` height — minimal traffic-light zone on macOS
- **Left header**: `6px` vertical padding, `6px` gap between elements
- Global border-radius for surfaces: `6px` (buttons use `8px`)

## Components

### Buttons

All interactive controls inherit from a unified base class. Over 40 semantic button classes (`.btn-primary`, `.btn-outline`, `.btn-delete-confirm`, `.seg-chip`, `.rtab`, etc.) share the same geometry tokens.

**Size variants:**

| Size | Height | Padding-X | Font |
|---|---|---|---|
| sm | 30px | 10px | 0.846rem |
| md (default) | 34px | 14px | 0.923rem |
| lg | 38px | 16px | 1rem |

**Icon-only buttons**: use icon-size tokens (`20px` xs / `22px` sm / `28px` md) — width equals height.

**Focus ring**: `box-shadow: 0 0 0 3px var(--accent-dim)` — never use `outline` directly.

**Transition**: all interactive state changes animate at `0.15s ease` across background, border, color, box-shadow, opacity, and transform.

## Do's and Don'ts

**Do:**
- Always use `var(--token)` — never hardcode colors, radii, or font sizes
- Pair semantic colors with their dim + border siblings when building colored states
- Use `var(--font-body)` on all text elements so the user's font preference applies
- Apply focus rings via `box-shadow: var(--btn-focus-ring)` — not browser default outline

**Don't:**
- Don't use `hsl(...)` or `#hex` literals in component CSS — they break theme switching
- Don't add new one-off font sizes outside the three-step scale
- Don't create new button classes without inheriting from the unified base selector list in `button-system.css`
- Don't use `outline` for focus indicators — it conflicts with the macOS window chrome
