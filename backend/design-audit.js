/**
 * PINE Design Consistency Audit — Full
 * Usage (from repo root): node backend/design-audit.js [base-url]
 * Default base URL: http://localhost:3000
 */

const { chromium } = require('playwright');

// ─── CONFIG ────────────────────────────────────────────────────────────────

const BASE_URL = process.argv[2] || 'http://localhost:5000';

// Static routes — dynamic ones (project tags, recording) are discovered at runtime
const STATIC_ROUTES = [
  '/',
  '/settings',
];

// Will be populated by discoverRoutes()
let ROUTES = [];

const ELEMENTS = {
  // Typography
  'h1':                        { label: 'H1',         group: 'typography' },
  'h2':                        { label: 'H2',         group: 'typography' },
  'h3':                        { label: 'H3',         group: 'typography' },
  'p':                         { label: 'Body',       group: 'typography' },
  'label':                     { label: 'Label',      group: 'typography' },
  '[class*="title"]':          { label: '.title',     group: 'typography' },
  '[class*="subtitle"]':       { label: '.subtitle',  group: 'typography' },

  // Interactive
  'button':                    { label: 'Button',     group: 'interactive' },
  'input':                     { label: 'Input',      group: 'interactive' },
  'select':                    { label: 'Select',     group: 'interactive' },
  '[class*="btn"]':            { label: '.btn',       group: 'interactive' },
  '[class*="tag"]':            { label: '.tag',       group: 'interactive' },

  // Layout
  'main':                      { label: 'Main',       group: 'layout' },
  'nav':                       { label: 'Nav',        group: 'layout' },
  'header':                    { label: 'Header',     group: 'layout' },
  '[class*="container"]':      { label: '.container', group: 'layout' },
  '[class*="panel"]':          { label: '.panel',     group: 'layout' },
  '[class*="sidebar"]':        { label: '.sidebar',   group: 'layout' },
  '[class*="card"]':           { label: '.card',      group: 'layout' },
  '[class*="section"]':        { label: '.section',   group: 'layout' },
};

const STYLE_PROPS = [
  'font-family', 'font-size', 'font-weight', 'line-height',
  'letter-spacing', 'text-transform', 'color', 'white-space',
  'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
  'margin-top', 'margin-bottom', 'gap', 'row-gap', 'column-gap',
  'width', 'height', 'min-width', 'min-height',
  'background-color', 'border-radius', 'border', 'box-shadow',
  'display', 'flex-direction', 'align-items', 'justify-content',
  'overflow', 'overflow-x', 'overflow-y',
  'transition', 'transition-duration', 'transition-timing-function', 'animation',
];

// CSS custom properties to read from :root — replace with your actual tokens
const CSS_VARS = [
  '--color-primary', '--color-background', '--color-surface',
  '--color-text', '--color-text-secondary', '--color-border', '--color-accent',
  '--spacing-xs', '--spacing-sm', '--spacing-md', '--spacing-lg', '--spacing-xl',
  '--font-size-sm', '--font-size-md', '--font-size-lg', '--font-size-xl',
  '--radius-sm', '--radius-md', '--radius-lg',
  '--transition-fast', '--transition-base', '--transition-slow',
];

const SKIP_IN_DIFF = new Set([
  'color', 'background-color', 'margin-top', 'margin-bottom',
]);

// ─── HELPERS ───────────────────────────────────────────────────────────────

function truncate(str, len = 32) {
  if (!str) return '—';
  str = String(str);
  return str.length > len ? str.slice(0, len - 1) + '…' : str;
}

function rgbToHex(rgb) {
  const match = rgb.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (!match) return rgb;
  return '#' + [match[1], match[2], match[3]]
    .map(n => parseInt(n).toString(16).padStart(2, '0'))
    .join('').toUpperCase();
}

function normalizeColor(val) {
  if (!val || val === 'rgba(0, 0, 0, 0)' || val === 'transparent') return 'transparent';
  return rgbToHex(val);
}

function normalizeValue(prop, val) {
  if (!val) return '';
  val = val.trim();
  if (/^(0px\s*){2,}$/.test(val)) return '0px';
  if (prop.includes('color')) return normalizeColor(val);
  return val;
}

function isNoise(val) {
  return !val || val === 'normal' || val === '0px' || val === 'none' || val === '';
}

// ─── AUDIT: MAIN ELEMENT STYLES ───────────────────────────────────────────

async function auditPage(page, url) {
  await page.goto(url, { waitUntil: 'networkidle', timeout: 15000 }).catch(() => {});

  return await page.evaluate(({ ELEMENTS, STYLE_PROPS, CSS_VARS }) => {
    const rootStyle = window.getComputedStyle(document.documentElement);
    const cssVarValues = {};
    for (const v of CSS_VARS) {
      const val = rootStyle.getPropertyValue(v).trim();
      if (val) cssVarValues[v] = val;
    }

    const rows = [];
    for (const [selector, meta] of Object.entries(ELEMENTS)) {
      const els = [...document.querySelectorAll(selector)].slice(0, 3);
      if (els.length === 0) continue;
      for (const el of els) {
        const computed = window.getComputedStyle(el);
        const styles = {};
        for (const prop of STYLE_PROPS) {
          styles[prop] = computed.getPropertyValue(prop).trim();
        }
        rows.push({
          selector: meta.label,
          group: meta.group,
          text: (el.textContent || '').trim().slice(0, 40),
          styles,
        });
      }
    }

    return { rows, cssVarValues };
  }, { ELEMENTS, STYLE_PROPS, CSS_VARS });
}

// ─── AUDIT: BUTTON DEEP ────────────────────────────────────────────────────

async function auditButtons(page) {
  return await page.evaluate(() => {
    const BUTTON_PROPS = [
      'width', 'height', 'min-width', 'min-height',
      'padding-top', 'padding-right', 'padding-bottom', 'padding-left',
      'display', 'flex-direction', 'align-items', 'justify-content', 'gap',
      'font-size', 'font-weight', 'letter-spacing', 'text-transform', 'white-space',
      'border-radius', 'border', 'background-color', 'color',
      'transition', 'transition-duration',
    ];

    const CHILD_PROPS = [
      'width', 'height', 'margin-right', 'margin-left',
      'display', 'align-self', 'flex-shrink',
    ];

    const buttons = [...document.querySelectorAll('button, [class*="btn"], [role="button"]')];
    return buttons.slice(0, 10).map(btn => {
      const computed = window.getComputedStyle(btn);
      const rect = btn.getBoundingClientRect();
      const styles = {};
      for (const prop of BUTTON_PROPS) {
        styles[prop] = computed.getPropertyValue(prop).trim();
      }

      const children = [...btn.children].map(child => {
        const cc = window.getComputedStyle(child);
        const cr = child.getBoundingClientRect();
        const childStyles = {};
        for (const prop of CHILD_PROPS) {
          childStyles[prop] = cc.getPropertyValue(prop).trim();
        }
        return {
          tag: child.tagName.toLowerCase(),
          text: (child.textContent || '').trim().slice(0, 20),
          rect: { width: Math.round(cr.width), height: Math.round(cr.height) },
          styles: childStyles,
        };
      });

      return {
        text: (btn.textContent || '').trim().slice(0, 40),
        rect: { width: Math.round(rect.width), height: Math.round(rect.height) },
        styles,
        children,
      };
    });
  });
}

// ─── AUDIT: HOVER & FOCUS STATES ──────────────────────────────────────────

async function auditStates(page) {
  const STATE_PROPS = [
    'background-color', 'color', 'border', 'box-shadow',
    'outline', 'outline-offset', 'outline-color', 'outline-width',
    'transform', 'opacity', 'transition-duration',
  ];

  const buttons = await page.$$('button, [class*="btn"], [role="button"]');
  const results = [];

  for (const btn of buttons.slice(0, 5)) {
    const label = await btn.evaluate(el => (el.textContent || '').trim().slice(0, 30));
    const isDisabled = await btn.evaluate(el =>
      el.disabled || el.getAttribute('aria-disabled') === 'true'
    );

    const defaultStyles = await btn.evaluate((el, props) => {
      const c = window.getComputedStyle(el);
      const s = {};
      for (const p of props) s[p] = c.getPropertyValue(p).trim();
      return s;
    }, STATE_PROPS);

    await btn.hover();
    await page.waitForTimeout(150);
    const hoverStyles = await btn.evaluate((el, props) => {
      const c = window.getComputedStyle(el);
      const s = {};
      for (const p of props) s[p] = c.getPropertyValue(p).trim();
      return s;
    }, STATE_PROPS);

    await btn.focus();
    await page.waitForTimeout(150);
    const focusStyles = await btn.evaluate((el, props) => {
      const c = window.getComputedStyle(el);
      const s = {};
      for (const p of props) s[p] = c.getPropertyValue(p).trim();
      return s;
    }, STATE_PROPS);

    const hoverDiff = {};
    const focusDiff = {};
    for (const prop of STATE_PROPS) {
      if (hoverStyles[prop] !== defaultStyles[prop])
        hoverDiff[prop] = { from: defaultStyles[prop], to: hoverStyles[prop] };
      if (focusStyles[prop] !== defaultStyles[prop])
        focusDiff[prop] = { from: defaultStyles[prop], to: focusStyles[prop] };
    }

    results.push({ label, isDisabled, hoverDiff, focusDiff });
  }

  return results;
}

// ─── AUDIT: ICONS ─────────────────────────────────────────────────────────

async function auditIcons(page) {
  return await page.evaluate(() => {
    const icons = [...document.querySelectorAll('svg, img[class*="icon"], [class*="icon"]')];
    return icons.slice(0, 20).map(el => {
      const c = window.getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      const parent = el.parentElement;
      const parentText = (parent?.textContent || '').trim().slice(0, 30);
      const fill   = el.getAttribute('fill')   || c.getPropertyValue('fill').trim()   || '—';
      const stroke = el.getAttribute('stroke') || c.getPropertyValue('stroke').trim() || '—';
      return {
        tag: el.tagName.toLowerCase(),
        parentContext: parentText,
        rect: { width: Math.round(rect.width), height: Math.round(rect.height) },
        fill,
        stroke,
        marginRight: c.getPropertyValue('margin-right').trim(),
        marginLeft:  c.getPropertyValue('margin-left').trim(),
        alignSelf:   c.getPropertyValue('align-self').trim(),
      };
    });
  });
}

// ─── AUDIT: FOCUS RINGS ───────────────────────────────────────────────────

async function auditFocusRings(page) {
  const FOCUS_PROPS = [
    'outline', 'outline-color', 'outline-width', 'outline-offset', 'outline-style',
    'box-shadow',
  ];

  const focusable = await page.$$('button, input, select, textarea, a, [tabindex]');
  const results = [];

  for (const el of focusable.slice(0, 10)) {
    const label = await el.evaluate(e =>
      (e.textContent || e.placeholder || e.getAttribute('aria-label') || e.tagName).trim().slice(0, 30)
    );

    const defaultStyles = await el.evaluate((e, props) => {
      const c = window.getComputedStyle(e);
      const s = {};
      for (const p of props) s[p] = c.getPropertyValue(p).trim();
      return s;
    }, FOCUS_PROPS);

    await el.focus();
    await page.waitForTimeout(100);

    const focusStyles = await el.evaluate((e, props) => {
      const c = window.getComputedStyle(e);
      const s = {};
      for (const p of props) s[p] = c.getPropertyValue(p).trim();
      return s;
    }, FOCUS_PROPS);

    const diff = {};
    for (const prop of FOCUS_PROPS) {
      if (focusStyles[prop] !== defaultStyles[prop])
        diff[prop] = { from: defaultStyles[prop], to: focusStyles[prop] };
    }

    results.push({ label, hasFocusRing: Object.keys(diff).length > 0, diff });
  }

  return results;
}

// ─── ANALYSIS ──────────────────────────────────────────────────────────────

function buildSummary(allData) {
  const grouped = {};
  for (const [route, { rows }] of Object.entries(allData)) {
    for (const row of rows) {
      if (!grouped[row.selector]) grouped[row.selector] = { group: row.group, props: {} };
      for (const [prop, rawVal] of Object.entries(row.styles)) {
        const val = normalizeValue(prop, rawVal);
        if (!grouped[row.selector].props[prop]) grouped[row.selector].props[prop] = new Map();
        const existing = grouped[row.selector].props[prop].get(val) || new Set();
        existing.add(route);
        grouped[row.selector].props[prop].set(val, existing);
      }
    }
  }
  return grouped;
}

function buildCssVarSummary(allData) {
  const result = {};
  for (const [route, { cssVarValues }] of Object.entries(allData)) {
    for (const [varName, val] of Object.entries(cssVarValues)) {
      if (!result[varName]) result[varName] = new Map();
      const existing = result[varName].get(val) || new Set();
      existing.add(route);
      result[varName].set(val, existing);
    }
  }
  return result;
}

// ─── REPORT ────────────────────────────────────────────────────────────────

function printReport(allData, grouped, cssVarSummary, buttonData, stateData, iconData, focusRingData) {
  const RESET   = '\x1b[0m';
  const BOLD    = '\x1b[1m';
  const RED     = '\x1b[31m';
  const GREEN   = '\x1b[32m';
  const YELLOW  = '\x1b[33m';
  const CYAN    = '\x1b[36m';
  const DIM     = '\x1b[2m';
  const MAGENTA = '\x1b[35m';

  const routes = Object.keys(allData);
  let totalIssues = 0;

  console.log(`\n${BOLD}╔══════════════════════════════════════════════════════════╗${RESET}`);
  console.log(`${BOLD}║           PINE — Design Consistency Audit                ║${RESET}`);
  console.log(`${BOLD}╚══════════════════════════════════════════════════════════╝${RESET}\n`);
  console.log(`${DIM}Pages audited: ${routes.join(', ')}${RESET}\n`);

  // ── 1. CSS Variables ──
  console.log(`${BOLD}── 1. CSS Design Tokens ────────────────────────────────────${RESET}\n`);
  const varIssues = Object.entries(cssVarSummary).filter(([, m]) => m.size > 1);
  if (varIssues.length === 0) {
    console.log(`  ${GREEN}✓ All detected CSS variables consistent${RESET}`);
  } else {
    for (const [varName, valueMap] of varIssues) {
      totalIssues++;
      console.log(`  ${RED}✗ ${varName}${RESET}`);
      for (const [val, routeSet] of valueMap) {
        console.log(`    ${DIM}${[...routeSet].join(', ')}${RESET}  →  ${YELLOW}${val}${RESET}`);
      }
    }
  }

  // ── 2–4. Element styles by group ──
  const groupDefs = [
    { key: 'typography',  label: '2. Typography' },
    { key: 'interactive', label: '3. Interactive Elements' },
    { key: 'layout',      label: '4. Layout & Spacing' },
  ];

  for (const { key, label } of groupDefs) {
    console.log(`\n${BOLD}── ${label} ${'─'.repeat(Math.max(0, 44 - label.length))}${RESET}\n`);
    const entries = Object.entries(grouped).filter(([, v]) => v.group === key);
    if (entries.length === 0) { console.log(`  ${DIM}(no elements found)${RESET}`); continue; }

    for (const [selector, { props }] of entries) {
      const issues = [];
      for (const [prop, valueMap] of Object.entries(props)) {
        if (SKIP_IN_DIFF.has(prop)) continue;
        const nonNoise = [...valueMap.entries()].filter(([v]) => !isNoise(v));
        if (nonNoise.length > 1) issues.push({ prop, valueMap: new Map(nonNoise) });
      }
      const status = issues.length === 0
        ? `${GREEN}✓ consistent${RESET}`
        : `${RED}✗ ${issues.length} issue(s)${RESET}`;
      console.log(`  ${BOLD}${selector.padEnd(16)}${RESET}  ${status}`);
      for (const { prop, valueMap } of issues) {
        totalIssues++;
        const isAnim = prop.includes('transition') || prop.includes('animation');
        console.log(`    ↳ ${isAnim ? MAGENTA : YELLOW}${prop}${RESET}`);
        for (const [val, routeSet] of valueMap) {
          const display = prop.includes('color') ? normalizeColor(val) : val;
          console.log(`        ${DIM}${[...routeSet].join(', ')}${RESET}  →  ${display}`);
        }
      }
    }
  }

  // ── 5. Button Deep Audit ──
  console.log(`\n${BOLD}── 5. Button Deep Audit ────────────────────────────────────${RESET}\n`);
  for (const [route, buttons] of Object.entries(buttonData)) {
    if (buttons.length === 0) continue;
    console.log(`  ${CYAN}${route}${RESET}`);
    const uniqueSizes = new Set(buttons.map(b => `${b.rect.width}×${b.rect.height}`));
    for (const btn of buttons) {
      const pad = [
        btn.styles['padding-top'], btn.styles['padding-right'],
        btn.styles['padding-bottom'], btn.styles['padding-left'],
      ].join(' ');
      console.log(`    ${BOLD}"${truncate(btn.text, 24)}"${RESET}  ${btn.rect.width}×${btn.rect.height}px  pad: ${pad}`);
      console.log(`      flex: ${btn.styles['display']} | align: ${btn.styles['align-items']} | justify: ${btn.styles['justify-content']} | gap: ${btn.styles['gap'] || '—'}`);
      for (const child of btn.children) {
        const info = child.tag === 'svg'
          ? `svg ${child.rect.width}×${child.rect.height}px  margin-r: ${child.styles['margin-right'] || '—'}`
          : `${child.tag} "${truncate(child.text, 16)}"  ${child.rect.width}×${child.rect.height}px`;
        console.log(`      ${DIM}↳ ${info}${RESET}`);
      }
    }
    if (uniqueSizes.size > 1) {
      totalIssues++;
      console.log(`    ${RED}✗ Inconsistent button sizes: ${[...uniqueSizes].join(', ')}${RESET}`);
    }
    console.log('');
  }

  // ── 6. Hover & Focus States ──
  console.log(`${BOLD}── 6. Hover & Focus States ─────────────────────────────────${RESET}\n`);
  for (const [route, states] of Object.entries(stateData)) {
    if (states.length === 0) continue;
    console.log(`  ${CYAN}${route}${RESET}`);
    const noHover = states.filter(s => Object.keys(s.hoverDiff).length === 0 && !s.isDisabled);
    const noFocus = states.filter(s => Object.keys(s.focusDiff).length === 0 && !s.isDisabled);
    if (noHover.length > 0) {
      totalIssues++;
      console.log(`    ${RED}✗ No hover effect: ${noHover.map(s => `"${s.label}"`).join(', ')}${RESET}`);
    }
    if (noFocus.length > 0) {
      totalIssues++;
      console.log(`    ${RED}✗ No focus effect: ${noFocus.map(s => `"${s.label}"`).join(', ')}${RESET}`);
    }
    for (const state of states) {
      const hk = Object.keys(state.hoverDiff);
      const fk = Object.keys(state.focusDiff);
      if (hk.length === 0 && fk.length === 0) continue;
      console.log(`    ${BOLD}"${state.label}"${RESET}`);
      if (hk.length > 0) {
        console.log(`      ${YELLOW}hover:${RESET}`);
        for (const prop of hk) {
          const { from, to } = state.hoverDiff[prop];
          const f = prop.includes('color') ? normalizeColor(from) : truncate(from, 24);
          const t = prop.includes('color') ? normalizeColor(to) : to;
          console.log(`        ${prop}: ${DIM}${f}${RESET} → ${t}`);
        }
      }
      if (fk.length > 0) {
        console.log(`      ${MAGENTA}focus:${RESET}`);
        for (const prop of fk) {
          const { from, to } = state.focusDiff[prop];
          const f = prop.includes('color') ? normalizeColor(from) : truncate(from, 24);
          const t = prop.includes('color') ? normalizeColor(to) : to;
          console.log(`        ${prop}: ${DIM}${f}${RESET} → ${t}`);
        }
      }
    }
    console.log('');
  }

  // ── 7. Icons ──
  console.log(`${BOLD}── 7. Icons ────────────────────────────────────────────────${RESET}\n`);
  for (const [route, icons] of Object.entries(iconData)) {
    if (icons.length === 0) continue;
    console.log(`  ${CYAN}${route}${RESET}`);
    const sizes = new Set(icons.map(i => `${i.rect.width}×${i.rect.height}`));
    const fills = new Set(icons.map(i => i.fill).filter(f => f && f !== '—' && f !== 'none'));
    if (sizes.size > 1) {
      totalIssues++;
      console.log(`    ${RED}✗ Inconsistent icon sizes: ${[...sizes].join(', ')}${RESET}`);
    } else {
      console.log(`    ${GREEN}✓ Icon sizes consistent: ${[...sizes][0] || '—'}${RESET}`);
    }
    if (fills.size > 1) {
      totalIssues++;
      console.log(`    ${RED}✗ Inconsistent icon fills: ${[...fills].map(normalizeColor).join(', ')}${RESET}`);
    }
    for (const icon of icons) {
      const margin = [icon.marginLeft, icon.marginRight].filter(Boolean).join(' / ') || '—';
      console.log(`    ${DIM}↳ ${icon.tag} ${icon.rect.width}×${icon.rect.height}px  fill: ${normalizeColor(icon.fill)}  margin: ${margin}  ctx: "${truncate(icon.parentContext, 24)}"${RESET}`);
    }
    console.log('');
  }

  // ── 8. Focus Rings ──
  console.log(`${BOLD}── 8. Focus Rings ──────────────────────────────────────────${RESET}\n`);
  for (const [route, rings] of Object.entries(focusRingData)) {
    if (rings.length === 0) continue;
    console.log(`  ${CYAN}${route}${RESET}`);
    const missing = rings.filter(r => !r.hasFocusRing);
    if (missing.length > 0) {
      totalIssues++;
      console.log(`    ${RED}✗ No focus ring: ${missing.map(r => `"${r.label}"`).join(', ')}${RESET}`);
    }
    for (const ring of rings.filter(r => r.hasFocusRing)) {
      console.log(`    ${GREEN}✓ "${ring.label}"${RESET}`);
      for (const [prop, { from, to }] of Object.entries(ring.diff)) {
        const f = prop.includes('color') ? normalizeColor(from) : truncate(from, 24);
        const t = prop.includes('color') ? normalizeColor(to) : to;
        console.log(`      ${DIM}${prop}:${RESET} ${f} → ${t}`);
      }
    }
    console.log('');
  }

  // ── 9. Animations & Transitions ──
  console.log(`${BOLD}── 9. Animations & Transitions ─────────────────────────────${RESET}\n`);
  const animProps = ['transition', 'transition-duration', 'transition-timing-function', 'animation'];
  let animIssuesFound = false;
  for (const [selector, { props }] of Object.entries(grouped)) {
    for (const prop of animProps) {
      if (!props[prop]) continue;
      const nonNoise = [...props[prop].entries()].filter(([v]) => !isNoise(v));
      if (nonNoise.length > 1) {
        animIssuesFound = true;
        totalIssues++;
        console.log(`  ${BOLD}${selector}${RESET}  ${MAGENTA}↳ ${prop}${RESET}`);
        for (const [val, routeSet] of nonNoise) {
          console.log(`    ${DIM}${[...routeSet].join(', ')}${RESET}  →  ${val}`);
        }
      }
    }
  }
  if (!animIssuesFound) {
    console.log(`  ${GREEN}✓ Transitions and animations are consistent${RESET}`);
  }

  // ── Summary ──
  console.log(`\n${BOLD}── Summary ──────────────────────────────────────────────────${RESET}`);
  if (totalIssues === 0) {
    console.log(`  ${GREEN}${BOLD}✓ No inconsistencies found across ${routes.length} page(s).${RESET}`);
  } else {
    console.log(`  ${RED}${BOLD}✗ ${totalIssues} inconsistency group(s) found across ${routes.length} page(s).${RESET}`);
    console.log(`  ${DIM}Each ↳ line shows which pages differ and what value they use.${RESET}`);
  }
  console.log('');
}

// ─── ROUTE DISCOVERY ────────────────────────────────────────────────────────

async function discoverRoutes() {
  const routes = [...STATIC_ROUTES];
  try {
    const res = await fetch(`${BASE_URL}/api/projects`);
    const projects = await res.json();
    if (Array.isArray(projects) && projects.length > 0) {
      const proj = projects[0];
      routes.push(`/project/${proj.id}/tags`);
      // Find a recording in this project
      const recRes = await fetch(`${BASE_URL}/api/projects/${proj.id}`);
      const projDetail = await recRes.json();
      const recordings = projDetail.recordings || [];
      if (recordings.length > 0) {
        routes.push(`/project/${proj.id}/recording/${recordings[0].id}`);
      }
    }
  } catch (e) {
    console.log(`  Warning: could not discover dynamic routes (${e.message})`);
  }
  return routes;
}

// ─── MAIN ──────────────────────────────────────────────────────────────────

(async () => {
  ROUTES = await discoverRoutes();
  console.log(`  Routes to audit: ${ROUTES.join(', ')}\n`);

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });

  const allData       = {};
  const buttonData    = {};
  const stateData     = {};
  const iconData      = {};
  const focusRingData = {};

  for (const route of ROUTES) {
    const url = BASE_URL + route;
    process.stdout.write(`  Auditing ${url} … `);
    const page = await context.newPage();
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
      // Give JS time to render after DOM is ready
      await page.waitForTimeout(1500);
      allData[route]       = await auditPage(page, url);
      buttonData[route]    = await auditButtons(page);
      stateData[route]     = await auditStates(page);
      iconData[route]      = await auditIcons(page);
      focusRingData[route] = await auditFocusRings(page);
      console.log('done');
    } catch (e) {
      console.log(`failed (${e.message})`);
      allData[route]       = { rows: [], cssVarValues: {} };
      buttonData[route]    = [];
      stateData[route]     = [];
      iconData[route]      = [];
      focusRingData[route] = [];
    }
    await page.close();
  }

  await browser.close();

  const grouped       = buildSummary(allData);
  const cssVarSummary = buildCssVarSummary(allData);
  printReport(allData, grouped, cssVarSummary, buttonData, stateData, iconData, focusRingData);
})();
