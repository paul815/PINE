/**
 * The browser lease — what keeps the backend alive while a PINE page is open.
 *
 * PINE has no tray icon and no window of its own: the open tab *is* the running
 * application. So every page takes a lease with the supervisor, renews it on a
 * heartbeat, and gives it back when it goes away; the supervisor stops the
 * backend once the last lease is gone.
 *
 * All six pages used to carry their own copy of this, and the copies had drifted.
 * Five renewed from `setInterval`, which Chrome throttles to roughly once a
 * minute in a background tab — so a lease could expire under a page that was
 * still very much open, and did. Onboarding alone had the fix, because its
 * install step runs for many minutes with the tab in the background: a Worker,
 * whose timer the browser leaves alone. That is the version this module ships,
 * to all of them.
 *
 * From a page's <head>:
 *
 *     <script src="{{ url_for('static', filename='js/lease.js') }}"
 *             data-supervisor-port="{{ supervisor_port }}"
 *             data-supervisor-token="{{ supervisor_token }}"
 *             data-source="tags_page"></script>
 *
 * The heartbeat starts and the unload handlers arm themselves on load. Two
 * attributes turn off the parts a page does not want:
 *
 *   data-quit-on-unload="false"    closing the page releases the lease but does
 *                                  not stop the backend — onboarding hands off
 *                                  to a restart, and must not kill what it just
 *                                  installed.
 *   data-prompt-on-unload="false"  no "are you sure" on the way out.
 */
(function () {
  const data = (document.currentScript && document.currentScript.dataset) || {};

  // One id per tab, kept in sessionStorage so a reload renews the same lease
  // instead of opening a second one the supervisor would have to time out.
  const LEASE_KEY = 'pine_tab_lease_id';
  // Renewal interval. The supervisor's lease timeout is a multiple of this (see
  // LEASE_TIMEOUT_SECONDS in supervisor.py): the point of the gap is to survive
  // a machine that went to sleep, a request that lost its race with a restart,
  // or a laptop lid closed over lunch, without the page having to prove itself
  // every few seconds.
  const HEARTBEAT_MS = 5000;
  // How long after a click that navigates we keep treating the unload as
  // internal. Long enough for the next page to arrive and take over the lease.
  const NAV_GRACE_MS = 2000;

  const source = data.source || 'page';
  const quitOnUnload = data.quitOnUnload !== 'false';
  const promptOnUnload = data.promptOnUnload !== 'false';

  let url = data.supervisorPort ? 'http://pine.localhost:' + data.supervisorPort : '';
  let token = data.supervisorToken || '';

  let ticker = null;
  let internalNavigation = false;
  let suppressPrompt = false;
  let quitStarted = false;

  function newId() {
    return (crypto?.randomUUID?.()
      || ('lease-' + Date.now() + '-' + Math.random().toString(36).slice(2)));
  }

  const id = (function () {
    try {
      const existing = sessionStorage.getItem(LEASE_KEY);
      if (existing) return existing;
      const next = newId();
      sessionStorage.setItem(LEASE_KEY, next);
      return next;
    } catch (e) {
      // Private mode, or storage the browser will not hand over: a lease that
      // does not survive the reload still keeps this page's backend alive.
      return newId();
    }
  })();

  /**
   * POST to the supervisor. `keepalive` is for calls made while the page is
   * going away: the browser is allowed to finish them after the document is
   * gone, and no-cors keeps a preflight from being the thing that never
   * completes.
   *
   * no-cors also drops every header that is not CORS-safelisted, the token
   * among them — so an unload-time call has to carry the token in the body, or
   * the supervisor answers 403 and the release is lost. Opaque responses have
   * no status, so nothing about that failure was visible from here.
   */
  function post(path, payload, keepalive) {
    const body = Object.assign({}, payload || {});
    if (keepalive) body.token = token;
    return fetch(url + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Pine-Supervisor-Token': token },
      body: JSON.stringify(body),
      keepalive: !!keepalive,
      mode: keepalive ? 'no-cors' : 'cors',
    });
  }

  function heartbeat() {
    post('/lease/heartbeat', { lease_id: id }, false).catch(() => {});
  }

  function stop() {
    if (!ticker) return;
    try { ticker.terminate(); } catch (e) {}
    ticker = null;
  }

  function start() {
    heartbeat();
    stop();
    try {
      // The timer lives in a Worker because a background tab's own timers are
      // throttled; the Worker's are not.
      const blob = new Blob(
        ['setInterval(() => postMessage("hb"), ' + HEARTBEAT_MS + ');'],
        { type: 'application/javascript' }
      );
      ticker = new Worker(URL.createObjectURL(blob));
      ticker.onmessage = heartbeat;
    } catch (e) {
      // No Worker (or no blob URLs): a throttled heartbeat beats none.
      const timer = setInterval(heartbeat, HEARTBEAT_MS);
      ticker = { terminate: () => clearInterval(timer) };
    }
  }

  function shutdownPayload(reason) {
    return { reason: reason || 'unknown', source: source, lease_id: id };
  }

  function release() {
    stop();
    try { post('/lease/release', { lease_id: id }, true); } catch (e) {}
  }

  /**
   * Hand the lease back and ask for the backend to stop. Three ways out, because
   * this runs while the page is being torn down and any one of them may already
   * be unavailable: a beacon (survives the document), a keepalive fetch, and the
   * supervisor's own /shutdown for the case where the backend is the thing that
   * is wedged.
   */
  function releaseAndQuit() {
    if (quitStarted) return;
    quitStarted = true;
    release();
    const payload = shutdownPayload('tab_or_page_closed');
    try {
      const body = new Blob([JSON.stringify(payload)], { type: 'application/json' });
      navigator.sendBeacon('/api/quit', body);
    } catch (e) {}
    try {
      fetch('/api/quit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        keepalive: true,
        mode: 'no-cors',
      });
    } catch (e) {}
    try { post('/shutdown', payload, true); } catch (e) {}
  }

  function markInternalNavigation() {
    internalNavigation = true;
    setTimeout(() => { internalNavigation = false; }, NAV_GRACE_MS);
  }

  function suppressUnloadPrompt() {
    suppressPrompt = true;
  }

  /** Point the lease at a different supervisor — see onboarding's handoff. */
  function setSupervisor(supervisorUrl, supervisorToken) {
    if (supervisorUrl) url = supervisorUrl;
    if (supervisorToken) token = supervisorToken;
  }

  function eventElement(event) {
    const raw = event && event.target;
    if (!raw) return null;
    if (raw instanceof Element) return raw;
    return raw.parentElement instanceof Element ? raw.parentElement : null;
  }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') heartbeat();
  });

  // A click that leads somewhere inside PINE is not the user leaving: the next
  // page picks the same lease up. Read off the markup rather than wrapping every
  // navigation, because the pages navigate from href, from onclick and from
  // window.location alike.
  document.addEventListener('click', (event) => {
    const target = eventElement(event);
    if (!target) return;
    const link = target.closest('a[href]');
    if (link) {
      const href = link.getAttribute('href') || '';
      if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
        markInternalNavigation();
      }
      return;
    }
    const withOnclick = target.closest('[onclick]');
    if (!withOnclick) return;
    const handlerText = (withOnclick.getAttribute('onclick') || '').toLowerCase();
    if (handlerText.includes('window.location')) {
      markInternalNavigation();
    }
    if (handlerText.includes('fetch(\'/api/quit') || handlerText.includes('fetch("/api/quit')) {
      suppressUnloadPrompt();
    }
  }, true);

  if (promptOnUnload) {
    window.addEventListener('beforeunload', (event) => {
      if (suppressPrompt || internalNavigation) return;
      const msg = 'Close PINE tab and stop all background processing?';
      event.preventDefault();
      event.returnValue = msg;
      return msg;
    });
  }

  window.addEventListener('pagehide', () => {
    if (internalNavigation) return;
    if (quitOnUnload) releaseAndQuit();
    else release();
  });

  start();

  window.PineLease = {
    id: id,
    get url() { return url; },
    get token() { return token; },
    post: post,
    heartbeat: heartbeat,
    start: start,
    stop: stop,
    release: release,
    releaseAndQuit: releaseAndQuit,
    shutdownPayload: shutdownPayload,
    markInternalNavigation: markInternalNavigation,
    suppressUnloadPrompt: suppressUnloadPrompt,
    setSupervisor: setSupervisor,
  };
})();
