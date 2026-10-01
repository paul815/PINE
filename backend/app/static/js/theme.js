/**
 * Theme and font, applied before the first paint.
 *
 * The server already knows what the user chose, so it renders the answer into
 * this script's own tag and the page opens in the right colours instead of
 * flashing light and correcting itself. The `/api/settings` round trip each page
 * then makes is only there to catch a change made in another tab; the page calls
 * `applyAppearance(settings)` with what comes back.
 *
 * Loaded in <head>, before the stylesheets can paint anything:
 *
 *     <script src="{{ url_for('static', filename='js/theme.js') }}"
 *             data-theme="{{ init_theme }}"
 *             data-font-size="{{ init_font_size }}"
 *             data-font-family="{{ init_font_family }}"></script>
 *
 * `_applyTheme` and `_fontStacks` keep their names: the settings and onboarding
 * screens call them directly when the user picks something new.
 */
(function () {
  const FONT_STACKS = {
    system: "-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif",
    inter: "'Inter',sans-serif",
    'source-sans-3': "'Source Sans 3',sans-serif",
    'open-sans': "'Open Sans',sans-serif",
    roboto: "'Roboto',sans-serif",
  };
  // Matches the server's own default in `inject_appearance`. It used to be
  // 'system' on the recording screen alone, which is how one page could open in
  // a different typeface than the rest of the app.
  const DEFAULT_FONT = 'inter';

  function applyTheme(theme) {
    let t = theme;
    if (t === 'system') {
      t = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    document.documentElement.setAttribute('data-theme', t);
  }

  /** Theme, text size and typeface from one settings object. */
  function applyAppearance(settings) {
    const s = settings || {};
    const root = document.documentElement;
    applyTheme(s.theme || 'system');
    if (s.font_size) {
      root.style.setProperty('font-size', s.font_size + 'px', 'important');
    }
    const family = s.font_family || DEFAULT_FONT;
    root.style.setProperty('--font-body', FONT_STACKS[family] || FONT_STACKS[DEFAULT_FONT]);
  }

  const data = (document.currentScript && document.currentScript.dataset) || {};
  applyAppearance({
    theme: data.theme,
    font_size: data.fontSize,
    font_family: data.fontFamily,
  });
  // Only when the choice *is* "follow the system": a fixed light or dark theme
  // has nothing to follow.
  if (data.theme === 'system') {
    window.matchMedia('(prefers-color-scheme: dark)')
      .addEventListener('change', () => applyTheme('system'));
  }

  window._fontStacks = FONT_STACKS;
  window._applyTheme = applyTheme;
  window.applyAppearance = applyAppearance;
})();
