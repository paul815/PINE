const axe = require('axe-core');
const { chromium } = require('playwright');

const BASE_URL = process.argv[2] || process.env.PINE_AUDIT_BASE_URL || 'http://127.0.0.1:5000';
const STATIC_ROUTES = ['/', '/settings'];
const THEMES = ['light', 'dark'];
const AUDIT_RULES = ['color-contrast'];
const SETTLE_MS = 1500;

function toArray(value) {
  return Array.isArray(value) ? value : [];
}

function cleanText(value, maxLength = 80) {
  if (!value) {
    return '';
  }

  const normalized = String(value).replace(/\s+/g, ' ').trim();
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength - 3)}...` : normalized;
}

function describeNode(details, target) {
  if (details) {
    const bits = [details.tag];
    if (details.role) {
      bits.push(`role=${details.role}`);
    }
    if (details.classes) {
      bits.push(`class=${details.classes}`);
    }
    if (details.isControl) {
      bits.push('interactive');
    }
    return bits.join(' ');
  }

  return target || '<unresolved>';
}

function formatRatio(value) {
  if (value == null || value === '') {
    return '';
  }

  return String(value).replace(/:1$/, '');
}

function extractContrastData(node) {
  const checks = [...toArray(node.any), ...toArray(node.all), ...toArray(node.none)];
  for (const check of checks) {
    if (check && check.data && typeof check.data === 'object') {
      return check.data;
    }
  }
  return {};
}

async function discoverRoutes() {
  const routes = [...STATIC_ROUTES];

  try {
    const response = await fetch(`${BASE_URL}/api/projects`);
    if (!response.ok) {
      return routes;
    }

    const data = await response.json();
    const projects = Array.isArray(data)
      ? data
      : [...toArray(data.active), ...toArray(data.archived)];

    if (!projects.length) {
      return routes;
    }

    const project = projects[0];
    routes.push(`/project/${project.id}/tags`);

    const recordingsResponse = await fetch(`${BASE_URL}/api/projects/${project.id}/recordings`);
    if (!recordingsResponse.ok) {
      return routes;
    }

    const recordings = await recordingsResponse.json();
    if (Array.isArray(recordings) && recordings.length) {
      routes.push(`/project/${project.id}/recording/${recordings[0].id}`);
    }
  } catch (error) {
    console.warn(`[warn] Could not discover dynamic routes: ${error.message}`);
  }

  return routes;
}

async function applyTheme(page, theme) {
  await page.evaluate((nextTheme) => {
    if (typeof window._applyTheme === 'function') {
      window._applyTheme(nextTheme);
    }
    document.documentElement.setAttribute('data-theme', nextTheme);
  }, theme);
  await page.waitForTimeout(150);
}

async function inspectTarget(page, target) {
  if (!target) {
    return null;
  }

  try {
    return await page.evaluate((selector) => {
      function firstOpaqueBackground(element) {
        let current = element;
        while (current) {
          const style = window.getComputedStyle(current);
          const value = style.backgroundColor;
          if (value && value !== 'rgba(0, 0, 0, 0)' && value !== 'transparent') {
            return value;
          }
          current = current.parentElement;
        }
        return window.getComputedStyle(document.body).backgroundColor;
      }

      function cleanClassName(value) {
        return value.replace(/\s+/g, '.').replace(/^\./, '');
      }

      const element = document.querySelector(selector);
      if (!element) {
        return null;
      }

      const style = window.getComputedStyle(element);
      const className = typeof element.className === 'string'
        ? cleanClassName(element.className)
        : '';
      const label = element.getAttribute('aria-label') || '';
      const text = (element.innerText || element.textContent || '').replace(/\s+/g, ' ').trim();
      const controlSelector = [
        'button',
        '[role="button"]',
        'a',
        'input',
        'select',
        'textarea',
        '[class*="btn"]',
        '[class*="chip"]',
        '[class*="tab"]'
      ].join(',');

      return {
        tag: element.tagName.toLowerCase(),
        role: element.getAttribute('role') || '',
        classes: className,
        label,
        text: text.slice(0, 80),
        color: style.color,
        backgroundColor: firstOpaqueBackground(element),
        isControl: element.matches(controlSelector) || Boolean(element.closest(controlSelector))
      };
    }, target);
  } catch (_) {
    return null;
  }
}

async function auditPage(page, route, theme) {
  const url = new URL(route, BASE_URL).toString();

  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
  await page.waitForTimeout(SETTLE_MS);
  await applyTheme(page, theme);
  await page.addScriptTag({ content: axe.source });

  const axeResults = await page.evaluate(async (rules) => {
    return window.axe.run(document, {
      runOnly: {
        type: 'rule',
        values: rules
      },
      resultTypes: ['violations']
    });
  }, AUDIT_RULES);

  const issues = [];
  for (const violation of axeResults.violations) {
    for (const node of violation.nodes) {
      const target = Array.isArray(node.target) && node.target.length ? node.target[0] : '';
      const details = await inspectTarget(page, target);
      const contrast = extractContrastData(node);

      issues.push({
        ruleId: violation.id,
        help: violation.help,
        target,
        summary: node.failureSummary ? cleanText(node.failureSummary, 140) : '',
        details,
        fgColor: contrast.fgColor || details?.color || '',
        bgColor: contrast.bgColor || details?.backgroundColor || '',
        contrastRatio: contrast.contrastRatio,
        expectedContrastRatio: contrast.expectedContrastRatio,
        text: cleanText(details?.text || details?.label || '', 80)
      });
    }
  }

  return { route, theme, issues };
}

async function prepareForPageClose(page) {
  try {
    await page.evaluate(() => {
      if (typeof window._markInternalNavigation === 'function') {
        window._markInternalNavigation();
      }
    });
  } catch (_) {
    // Page may already be gone or may not define the helper.
  }
}

function printRouteResult(result) {
  const heading = `[${result.theme}] ${result.route}`;
  if (!result.issues.length) {
    console.log(`${heading}: pass`);
    return;
  }

  console.log(`${heading}: ${result.issues.length} contrast issue(s)`);
  for (const issue of result.issues) {
    const label = issue.text ? ` "${issue.text}"` : '';
    const nodeDescription = describeNode(issue.details, issue.target);
    const ratio = issue.contrastRatio != null && issue.expectedContrastRatio != null
      ? ` contrast ${formatRatio(issue.contrastRatio)}:1 < ${formatRatio(issue.expectedContrastRatio)}:1`
      : '';

    console.log(`  - [${issue.ruleId}] ${nodeDescription}${label}${ratio}`);
    if (issue.fgColor || issue.bgColor) {
      console.log(`    colors: fg=${issue.fgColor || 'n/a'} bg=${issue.bgColor || 'n/a'}`);
    }
    if (issue.summary) {
      console.log(`    ${issue.summary}`);
    } else if (issue.help) {
      console.log(`    ${issue.help}`);
    }
    if (issue.target) {
      console.log(`    target: ${issue.target}`);
    }
  }
}

(async () => {
  const routes = await discoverRoutes();
  console.log(`Auditing ${BASE_URL}`);
  console.log(`Routes: ${routes.join(', ')}`);
  console.log(`Themes: ${THEMES.join(', ')}`);
  if (routes.length === STATIC_ROUTES.length) {
    console.log('Dynamic project routes were not discovered; auditing static pages only.');
  }
  console.log('');

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });

  const results = [];

  try {
    for (const theme of THEMES) {
      for (const route of routes) {
        const page = await context.newPage();
        try {
          results.push(await auditPage(page, route, theme));
        } finally {
          await prepareForPageClose(page);
          await page.close();
        }
      }
    }
  } finally {
    await browser.close();
  }

  let totalIssues = 0;
  for (const result of results) {
    totalIssues += result.issues.length;
    printRouteResult(result);
  }

  console.log('');
  if (totalIssues === 0) {
    console.log('Contrast audit passed.');
    return;
  }

  console.error(`Contrast audit failed with ${totalIssues} issue(s).`);
  process.exitCode = 1;
})().catch((error) => {
  console.error(`Contrast audit failed to run: ${error.message}`);
  process.exitCode = 1;
});
