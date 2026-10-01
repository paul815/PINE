/* ==========================================================================
   PINE — code colour palette
   --------------------------------------------------------------------------
   Single source of truth for the twelve swatches a user can pick for a code,
   and for the five legacy category tokens that older projects still store
   instead of a hex value.

   These three lists used to live, byte-identical, in recording.html,
   tags.html and manage_tags.html. Three copies of the same data is three
   chances for the same code to render in a different colour depending on
   which screen you are looking at.

   Loaded as a classic script before each page's inline script, so the
   top-level `const`s below are visible to it.
   ========================================================================== */

const TAG_PALETTE = [
  '#f04f4f', '#34c97a', '#f5a623', '#5da8e0', '#a0a0b8', '#e91e63',
  '#9c27b0', '#00bcd4', '#8bc34a', '#ff5722', '#607d8b', '#795548'
];

/* pain / insight / delight / confusion / follow-up — the category tokens
   PINE shipped before codes carried their own colour. */
const TAG_TOKEN_COLORS = {
  pain: '#f04f4f',
  ins:  '#34c97a',
  del:  '#f5a623',
  conf: '#5da8e0',
  fu:   '#a0a0b8'
};

const TAG_COLOR_FALLBACK = '#a0a0b8';

/**
 * Resolve a stored code colour to a hex string safe to put in a style attr.
 * Strict hex validation on purpose — never let an arbitrary stored string
 * reach the DOM as CSS.
 */
function tagColor(c) {
  if (typeof c === 'string' && /^#[0-9a-fA-F]{3,8}$/.test(c)) return c;
  return TAG_TOKEN_COLORS[c] || TAG_COLOR_FALLBACK;
}
