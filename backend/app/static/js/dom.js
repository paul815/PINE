/**
 * Escaping helpers for the strings the pages build markup out of.
 *
 * Both functions existed in five and four templates respectively, identical
 * except for the comment above them — this is the one copy. They stay global,
 * and keep their names, because the templates call them from inside template
 * literals on nearly every render.
 */

/**
 * HTML-escape a value, attribute-safe.
 *
 * Quotes are escaped as well as the angle brackets. The textContent→innerHTML
 * trick this replaced left both quote characters alone, so any value placed in
 * an attribute — data-tag-name="…" and its like — could close it and inject
 * markup or a handler.
 */
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

/**
 * Escape a value that lands in JavaScript that lands in an HTML attribute —
 * two parsers, two escapes.
 *
 * JSON closes off quotes and backslashes for the JS parser; esc() closes off the
 * attribute for the HTML parser, which unwraps its layer first and hands the JS
 * parser exactly the literal we wrote. esc() alone cannot do this job: it turns
 * ' into &#39;, and the HTML parser gives the apostrophe back before the JS
 * parser ever sees the string.
 */
function escJsAttr(s) {
  return esc(JSON.stringify(String(s ?? '')));
}
