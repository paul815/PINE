/* ── Save feedback: the single toast + inline save-status implementation ──
 *
 * Classic script (not a module) so templates keep calling toast() and
 * showSaveStatus() as bare globals, exactly as the inline copies did.
 * Must be loaded BEFORE each template's inline <script>: a top-level
 * `function toast()` in a template would otherwise shadow the global.
 *
 * Channel contract — one user action produces exactly one of these:
 *   showSaveStatus(anchorEl, state, retryFn)  autosave on a field
 *   showSectionSaved(sectionId, state)        autosave on a settings section
 *   toast(msg, isErr)                         off-screen results, and ALL errors
 */
(function () {
  'use strict';

  var SAVED_HOLD_MS = 1200;   // then a 0.25s CSS fade-out
  var TOAST_MS = 2000;
  var TOAST_ERR_MS = 4000;

  var TEXT = {
    saving: 'Saving…',
    saved: 'Saved',
    error: 'Couldn’t save',
    retry: 'Retry'
  };

  // ── Toast ──
  var _toastTimer;

  function toast(msg, isErr) {
    var el = document.getElementById('toast');
    if (!el) return;
    el.textContent = msg;
    el.classList.toggle('err', !!isErr);
    el.classList.add('show');
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout(function () { el.classList.remove('show'); },
      isErr ? TOAST_ERR_MS : TOAST_MS);
  }

  // ── Inline save status ──

  /* anchorEl may be the .save-status span itself (pre-rendered in markup, as in
     settings sections) or a container to find/create one in (as in field labels). */
  function resolveSpan(anchorEl) {
    if (!anchorEl || !anchorEl.classList) return null;
    if (anchorEl.classList.contains('save-status')) return anchorEl;

    var span = anchorEl.querySelector('.save-status');
    if (span) return span;

    span = document.createElement('span');
    span.className = 'save-status';
    span.setAttribute('role', 'status');
    span.setAttribute('aria-live', 'polite');
    // Keep the status ahead of a section's remove button so the button stays last.
    var removeBtn = anchorEl.querySelector('.remove-section-btn');
    if (removeBtn) anchorEl.insertBefore(span, removeBtn);
    else anchorEl.appendChild(span);
    return span;
  }

  /* state: 'saving' | 'saved' | 'error'.
     Booleans are accepted for the older ok/!ok call sites: true -> 'saved'. */
  function showSaveStatus(anchorEl, state, retryFn) {
    var span = resolveSpan(anchorEl);
    if (!span) return;

    if (state === true || state === undefined) state = 'saved';
    else if (state === false) state = 'error';

    clearTimeout(span._hideTimer);
    span.className = 'save-status';

    if (state === 'saving') {
      span.setAttribute('aria-live', 'polite');
      span.textContent = TEXT.saving;
      span.classList.add('is-visible');
      return;
    }

    if (state === 'saved') {
      span.setAttribute('aria-live', 'polite');
      span.textContent = TEXT.saved;
      span.classList.add('is-visible');
      span._hideTimer = setTimeout(function () {
        span.classList.remove('is-visible');
      }, SAVED_HOLD_MS);
      return;
    }

    // Errors are not dismissed on a timer — they stay until a retry succeeds.
    span.setAttribute('aria-live', 'assertive');
    span.textContent = TEXT.error;
    span.classList.add('is-error');
    if (typeof retryFn === 'function') {
      span.appendChild(document.createTextNode(' · '));
      var a = document.createElement('a');
      a.textContent = TEXT.retry;
      a.setAttribute('role', 'button');
      a.tabIndex = 0;
      a.onclick = function (e) { e.preventDefault(); retryFn(); };
      a.onkeydown = function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); retryFn(); }
      };
      span.appendChild(a);
    }
  }

  /* Settings groups have no single field, so the status hangs off the section title. */
  function showSectionSaved(sectionId, state, retryFn) {
    var sec = sectionId ? document.getElementById(sectionId) : null;
    if (!sec) return;
    var span = sec.querySelector('.save-status');
    if (!span) return;
    showSaveStatus(span, state === undefined ? 'saved' : state, retryFn);
  }

  /* Page-level autosaves (annotations, whole tag/theme lists) mutate state from many
     places at once and have no single field to point at, so they report in the top bar. */
  function showPageSaved(state, retryFn) {
    var span = document.getElementById('pageSaveStatus');
    if (!span) return;
    showSaveStatus(span, state === undefined ? 'saved' : state, retryFn);
  }

  window.toast = toast;
  window.showSaveStatus = showSaveStatus;
  window.showSectionSaved = showSectionSaved;
  window.showPageSaved = showPageSaved;
})();
