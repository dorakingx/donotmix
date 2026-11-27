/* DoNotMix dashboard — vanilla JS, no build step, no network beyond the local API.
 * Every string that comes from a report, proposal or audit entry is inserted with
 * textContent (via h()/svg()), never innerHTML. */
(function () {
  'use strict';

  // ---------------------------------------------------------------- helpers
  const SVGNS = 'http://www.w3.org/2000/svg';

  function append(node, kids) {
    for (const k of kids) {
      if (k === null || k === undefined || k === false) continue;
      if (Array.isArray(k)) { append(node, k); continue; }
      node.appendChild(k instanceof Node ? k : document.createTextNode(String(k)));
    }
    return node;
  }

  function setAttrs(node, attrs) {
    if (!attrs) return;
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') node.setAttribute('class', v);
      else if (k === 'text') node.textContent = String(v);
      else if (k === 'dataset') Object.assign(node.dataset, v);
      else if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
      else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else if (k === 'value' && 'value' in node) node.value = v;
      else if (v === true) node.setAttribute(k, '');
      else node.setAttribute(k, String(v));
    }
  }

  function h(tag, attrs, ...kids) {
    const node = document.createElement(tag);
    setAttrs(node, attrs);
    return append(node, kids);
  }

  function svg(tag, attrs, ...kids) {
    const node = document.createElementNS(SVGNS, tag);
    setAttrs(node, attrs);
    return append(node, kids);
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }

  const fmt = {
    int(n) { return (n === null || n === undefined || Number.isNaN(n)) ? '—' : Number(n).toLocaleString('en-US'); },
    num(n, d = 2) {
      if (n === null || n === undefined || Number.isNaN(Number(n))) return '—';
      const x = Number(n);
      return Number.isInteger(x) ? x.toLocaleString('en-US') : x.toFixed(d);
    },
    pct(r) {
      if (r === null || r === undefined || Number.isNaN(Number(r))) return '—';
      const p = Number(r) * 100;
      if (p === 100 || p === 0) return p.toFixed(0) + '%';
      // never round 99.6% up to 100% or 0.4% down to 0%
      return (p > 99 || p < 1 ? p.toFixed(2) : p.toFixed(1)) + '%';
    },
    time(iso) {
      if (!iso) return '—';
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return String(iso);
      const pad = (x) => String(x).padStart(2, '0');
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    },
    onoff(v) { return v ? 'ON' : 'OFF'; },
    short(s, n = 12) { return s ? String(s).slice(0, n) : '—'; },
  };

  function announce(msg) {
    const el = document.getElementById('sr-announcer');
    el.textContent = '';
    setTimeout(() => { el.textContent = msg; }, 30);
  }

  function shortTest(t) {
    if (!t) return '';
    const s = String(t);
    const i = s.lastIndexOf('::');
    return i >= 0 ? s.slice(i + 2) : s;
  }

  // ---------------------------------------------------------------- API
  async function apiGet(path) {
    const res = await fetch(path, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    let body = null;
    try { body = await res.json(); } catch (_) { body = null; }
    return { ok: res.ok, status: res.status, body };
  }

  async function apiPost(path, payload) {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(Object.assign({ csrf: state.session && state.session.csrf }, payload || {})),
      cache: 'no-store',
    });
    let body = null;
    try { body = await res.json(); } catch (_) { body = null; }
    if (!res.ok) {
      const detail = body && (body.detail || body.error || body.status);
      throw new Error(detail ? String(typeof detail === 'string' ? detail : JSON.stringify(detail))
        : res.status >= 500 ? `HTTP ${res.status}: server error (details are in the terminal running \`donotmix dashboard\`)` : `HTTP ${res.status}`);
    }
    return body;
  }

  // ---------------------------------------------------------------- state
  const state = {
    session: null,
    runs: [],
    runId: 'latest',
    report: null,
    reportStatus: 'loading', // loading | ok | none | error
    reportError: null,
    proposals: [],
    proposalsError: null,
    audit: null,
    bench: null,
    scan: { state: 'idle' },
    tab: 'overview',
    hazardId: null,
    matrixSel: null,
    sliceFlag: null,
    ui: { proposalMsgs: {} },
  };
  let derived = null;
  let scanTimer = null;

  function derive(r) {
    if (!r) return null;
    const flags = Array.isArray(r.flags) ? r.flags : [];
    const live = {};
    for (const f of flags) live[f.name] = !!f.live;
    const hazards = Array.isArray(r.hazards) ? r.hazards.slice() : [];
    const sevRank = { critical: 0, high: 1, medium: 2, low: 3 };
    hazards.sort((a, b) => (a.status === 'live' ? 0 : 1) - (b.status === 'live' ? 0 : 1)
      || (sevRank[a.severity] ?? 9) - (sevRank[b.severity] ?? 9)
      || String(a.id).localeCompare(String(b.id), 'en', { numeric: true }));
    const byId = {};
    for (const hz of hazards) byId[hz.id] = hz;
    const tests = (Array.isArray(r.tests) ? r.tests : []).map((t) => ({
      index: t.index,
      cfg: Object.assign({}, live, t.diff_from_live || {}),
      results: t.results || {},
      failed: t.failed || [],
      anyFail: Object.values(t.results || {}).some((x) => x === 'fail'),
    }));
    const matrix = r.matrix || {};
    const mflags = Array.isArray(matrix.flags) && matrix.flags.length ? matrix.flags : flags.map((f) => f.name);
    const pairMap = new Map();
    for (const p of matrix.pairs || []) {
      pairMap.set(p.a + '|' + p.b, p);
      pairMap.set(p.b + '|' + p.a, p);
    }
    const tripleMap = new Map();
    for (const t of matrix.triples || []) tripleMap.set(t.flags.slice().sort().join('|'), t);
    const soloHazards = {};
    for (const hz of hazards) {
      const core = Object.keys(hz.core || {});
      if (core.length === 1) (soloHazards[core[0]] = soloHazards[core[0]] || []).push(hz.id);
    }
    return { flags, live, hazards, byId, tests, mflags, pairMap, tripleMap, soloHazards };
  }

  function envName() {
    const r = state.report;
    return (r && r.environment && r.environment.environment && r.environment.environment.name)
      || (state.session && state.session.environment) || null;
  }

  // ---------------------------------------------------------------- loading
  async function loadReport() {
    state.reportStatus = 'loading';
    const q = state.runId && state.runId !== 'latest' ? encodeURIComponent(state.runId) : 'latest';
    try {
      const res = await apiGet('/api/report?run=' + q);
      if (res.ok && res.body && res.body.verdict) {
        state.report = res.body; state.reportStatus = 'ok'; state.reportError = null;
      } else if (res.status === 404 || (res.body && res.body.status === 'no-run')) {
        state.report = null; state.reportStatus = 'none'; state.reportError = (res.body && res.body.hint) || null;
      } else {
        state.report = null; state.reportStatus = 'error'; state.reportError = `report request failed (HTTP ${res.status})`;
      }
    } catch (e) {
      state.report = null; state.reportStatus = 'error'; state.reportError = String(e.message || e);
    }
    derived = derive(state.report);
    if (derived && (!state.hazardId || !derived.byId[state.hazardId])) state.hazardId = derived.hazards[0] ? derived.hazards[0].id : null;
  }

  async function loadRuns() {
    try { const r = await apiGet('/api/runs'); state.runs = r.ok && Array.isArray(r.body) ? r.body : []; } catch (_) { state.runs = []; }
  }
  async function loadProposals() {
    try {
      const r = await apiGet('/api/proposals');
      if (r.ok && Array.isArray(r.body)) { state.proposals = r.body; state.proposalsError = null; }
      else { state.proposals = []; state.proposalsError = `proposals request failed (HTTP ${r.status})`; }
    } catch (e) { state.proposals = []; state.proposalsError = String(e.message || e); }
  }
  async function loadAudit() {
    try { const r = await apiGet('/api/audit'); state.audit = r.ok ? r.body : { error: `HTTP ${r.status}` }; } catch (e) { state.audit = { error: String(e.message || e) }; }
  }
  async function loadBench() {
    try { const r = await apiGet('/api/benchmark'); state.bench = r.ok ? r.body : { status: 'error', hint: `HTTP ${r.status}` }; } catch (e) { state.bench = { status: 'error', hint: String(e.message || e) }; }
  }
  async function loadSession() {
    try { const r = await apiGet('/api/session'); state.session = r.ok ? r.body : null; } catch (_) { state.session = null; }
  }

  // ---------------------------------------------------------------- shared UI bits
  function tag(text, cls, title) { return h('span', { class: 'tag ' + (cls || ''), title }, text); }

  function statusTag(st) {
    return st === 'live'
      ? tag('✕ LIVE', 'tag-live', 'Fails in the current environment state')
      : tag('! LATENT', 'tag-latent', 'Not reachable in the current state, but reachable by a flag change');
  }

  function sevTag(sev) { return tag(sev || 'n/a', 'tag-sev-' + (sev || 'low'), 'Severity'); }

  function stateBadge(v) {
    return h('span', { class: 'state ' + (v ? 'state-on' : 'state-off') }, v ? '● ON' : '○ OFF');
  }

  function flagChip(flag, value, extra) {
    return h('span', { class: 'chip ' + (value ? 'on' : 'off') + (extra ? ' ' + extra : '') },
      h('span', { class: 'chip-flag' }, flag),
      h('span', { class: 'chip-val' }, value ? 'ON' : 'OFF'));
  }

  function panel(title, body, opts) {
    opts = opts || {};
    const id = opts.id;
    return h('section', { class: 'panel ' + (opts.cls || ''), 'aria-labelledby': id ? id + '-h' : null, id },
      h('div', { class: 'panel-head' }, h(opts.level || 'h3', { id: id ? id + '-h' : null }, title), opts.tag || null),
      h('div', { class: 'panel-body' }, body));
  }

  function kv(pairs) {
    const dl = h('dl', { class: 'kv' });
    for (const [k, v] of pairs) {
      if (v === undefined) continue;
      dl.appendChild(h('dt', null, k));
      dl.appendChild(h('dd', null, v === null || v === '' ? '—' : v));
    }
    return dl;
  }

  function sectionTitle(id, title, sub) {
    return h('div', { class: 'section-title' }, h('h2', { id }, title), sub ? h('p', null, sub) : null);
  }

  async function copyText(text, btn) {
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch (_) {
      const ta = h('textarea', { 'aria-hidden': 'true', style: { position: 'fixed', left: '-9999px', top: '0' } });
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { ok = document.execCommand('copy'); } catch (__) { ok = false; }
      ta.remove();
    }
    if (btn) {
      const old = btn.textContent;
      btn.textContent = ok ? '✓ Copied' : 'Copy failed';
      setTimeout(() => { btn.textContent = old; }, 1600);
    }
    announce(ok ? 'Copied to clipboard' : 'Copy failed; select the text manually');
  }

  function cmdBlock(label, text) {
    const pre = h('pre', null, h('code', null, text));
    const btn = h('button', { class: 'btn btn-sm', type: 'button', 'aria-label': 'Copy: ' + label });
    btn.textContent = 'Copy';
    btn.addEventListener('click', () => copyText(text, btn));
    return h('div', null, h('div', { class: 'label' }, label), h('div', { class: 'cmd' }, pre, btn));
  }

  function noReport(where) {
    const msg = state.reportStatus === 'error'
      ? ['Could not load the report.', state.reportError]
      : ['No scan has been run yet.', 'Run `donotmix scan`, or press Re-scan on the Overview tab.'];
    return h('div', { class: 'empty-state' },
      h('div', { class: 'sym', 'aria-hidden': 'true' }, h('span', null, '?')),
      h('h3', null, where ? `${where}: ${msg[0]}` : msg[0]),
      h('p', { class: 'muted' }, msg[1] || ''));
  }

  // ---------------------------------------------------------------- header
  function renderHeader() {
    const pill = document.getElementById('env-pill');
    const val = pill.querySelector('.env-pill-value');
    clear(val);
    const r = state.report;
    const env = envName();
    if (!env) { val.textContent = 'not connected'; }
    else {
      const proj = (r && r.environment && r.environment.project) || (state.session && state.session.project);
      const mode = r && r.mode ? r.mode.flagsmith : null;
      append(val, [h('b', null, env), proj ? ` · ${proj}` : '', mode ? ` · ${mode === 'live' ? 'live snapshot' : (/snapshot/.test(mode) ? mode : mode + ' snapshot')}` : '']);
      const prot = state.session && (state.session.protected_environments || []).some((p) => p.toLowerCase() === env.toLowerCase());
      if (prot) append(val, [' · ', h('b', null, 'PROTECTED')]);
    }
    const sel = document.getElementById('run-select');
    clear(sel);
    sel.appendChild(h('option', { value: 'latest' }, 'latest'));
    for (const run of state.runs) {
      const label = `${run.run_id} · ${run.status}${run.hazards ? ' · ' + run.hazards + ' hz' : ''}`;
      sel.appendChild(h('option', { value: run.run_id }, label));
    }
    sel.value = state.runId;
    const cnt = document.getElementById('hazard-count');
    const nLive = derived ? derived.hazards.filter((x) => x.status === 'live').length : 0;
    cnt.textContent = nLive ? `${nLive} live` : '';
    cnt.setAttribute('aria-label', nLive ? `${nLive} live hazards` : '');
  }

  // ---------------------------------------------------------------- overview
  const VERDICT_TEXT = {
    LIVE_HAZARD: { title: 'Live hazard', sym: '!', kicker: 'Do not mix · current state fails' },
    LATENT_HAZARDS: { title: 'Latent hazards', sym: '!', kicker: 'Caution · one flag change away from failure' },
    NO_HAZARDS_FOUND: { title: 'No hazards found', sym: '✓', kicker: 'No failure observed in executed configurations' },
    DRY_RUN: { title: 'Dry run', sym: '?', kicker: 'Plan only · nothing executed' },
  };

  function verdictPlacard(r) {
    const v = (r && r.verdict) || {};
    const st = v.status || 'NONE';
    const t = VERDICT_TEXT[st] || { title: st, sym: '?', kicker: 'Verdict' };
    return h('section', { class: 'placard v-' + st, 'aria-label': 'Verdict: ' + t.title },
      h('div', { class: 'placard-frame' },
        h('div', { class: 'placard-inner' },
          h('div', { class: 'placard-sym', 'aria-hidden': 'true' }, h('span', null, t.sym)),
          h('div', null,
            h('p', { class: 'placard-kicker' }, t.kicker),
            h('p', { class: 'placard-title', role: 'heading', 'aria-level': '2' }, t.title),
            h('p', { class: 'placard-summary' }, v.summary || ''),
            h('p', { class: 'placard-meta' }, `verdict ${st} · exit code ${v.exit_code ?? '—'} · run ${r.run_id || '—'} · ${fmt.time(r.created_at)}`)))));
  }

  function statTile(label, value, sub, opts) {
    opts = opts || {};
    return h('div', { class: 'stat' + (opts.alert ? ' stat-alert' : '') },
      h('div', { class: 'label' }, label),
      h('div', { class: 'stat-value' }, value),
      sub ? h('div', { class: 'stat-sub' }, sub) : null,
      opts.meter !== undefined ? h('div', { class: 'meter' + (opts.meter < 1 ? ' meter-partial' : ''), role: 'img', 'aria-label': `${label}: ${fmt.pct(opts.meter)}` },
        h('span', { style: { width: Math.max(0, Math.min(1, opts.meter)) * 100 + '%' } })) : null);
  }

  function renderOverview() {
    const root = clear(document.getElementById('tab-overview'));
    root.appendChild(h('h1', { id: 'h-overview', class: 'visually-hidden' }, 'Overview'));
    const r = state.report;
    if (!r) {
      root.appendChild(verdictPlacard({ verdict: { status: 'NONE', summary: state.reportStatus === 'error' ? state.reportError : 'No scan report available yet.' } }));
      root.appendChild(h('div', { class: 'grid grid-2' }, scanPanel(), sessionPanel()));
      return;
    }
    const d = derived;
    root.appendChild(verdictPlacard(r));
    const v = r.verdict || {};
    root.appendChild(h('div', { class: 'caveat', role: 'note' },
      h('span', { class: 'caveat-icon', 'aria-hidden': 'true' }, 'i'),
      caveatText(v.caveat)));

    const c = r.constraints || {};
    const ex = r.execution || {};
    const cov = r.coverage || {};
    const nLive = d.hazards.filter((x) => x.status === 'live').length;
    const nLatent = d.hazards.length - nLive;
    const flagsOn = d.flags.filter((f) => f.live).length;
    const tiles = [
      statTile('Flags', fmt.int(d.flags.length), `${flagsOn} on · ${d.flags.length - flagsOn} off in ${envName() || 'env'}`),
      statTile('Feasible combinations', h('span', null, fmt.int(c.feasible_configs), h('small', null, ' / ' + fmt.int(c.total_configs))),
        `${c.total_configs ? fmt.pct(c.feasible_configs / c.total_configs) : '—'} allowed by ${(c.declared || []).length} constraint(s)`,
        { meter: c.total_configs ? c.feasible_configs / c.total_configs : 0 }),
      statTile('Executed configs', fmt.int(ex.configs ?? (r.plan || {}).configs),
        `× ${fmt.int(ex.personas)} personas · ${fmt.int(ex.executions_total)} runs incl. minimization · ${fmt.int(ex.failed_executions)} failed`),
    ];
    for (const k of Object.keys(cov).sort((a, b) => Number(a) - Number(b))) {
      const cv = cov[k] || {};
      tiles.push(statTile(`${k}-way coverage`, fmt.pct(cv.ratio), `${fmt.int(cv.covered)} / ${fmt.int(cv.total)} feasible ${k}-tuples`, { meter: Number(cv.ratio) || 0 }));
    }
    tiles.push(statTile('Hazards', h('span', null, String(d.hazards.length)),
      `${nLive} live (✕) · ${nLatent} latent (!)`, { alert: nLive > 0 }));
    root.appendChild(h('div', { class: 'stats', role: 'list', 'aria-label': 'Key numbers' }, tiles.map((t) => { t.setAttribute('role', 'listitem'); return t; })));

    const envd = r.environment || {};
    const envObj = envd.environment || {};
    const mode = r.mode || {};
    const ai = r.ai || {};
    const envPanel = panel('Connected Flagsmith environment', kv([
      ['Environment', h('span', null, h('strong', null, envObj.name || '—'), envObj.key ? h('span', { class: 'muted mono small' }, `  key ${envObj.key}`) : null)],
      ['Project', envd.project || '—'],
      ['API URL', h('code', null, envd.api_url || '—')],
      ['Snapshot mode', h('span', null, mode.flagsmith === 'live' ? tag('● live', 'tag-ok') : tag('○ ' + (mode.flagsmith || 'unknown'), 'tag-ghost'),
        ' ', h('span', { class: 'muted small' }, `via ${envd.source || 'unknown'}`))],
      ['State hash', h('code', null, envd.state_hash || '—')],
      ['Fetched', h('time', { datetime: envd.fetched_at || '' }, fmt.time(envd.fetched_at))],
      ['Run', h('code', null, r.run_id || '—')],
      ['Strategy', `${(r.plan || {}).strategy || '—'} · strength ${(r.plan || {}).strength ?? '—'} · seed ${(r.plan || {}).seed ?? '—'}`],
    ]), { id: 'p-env' });

    const isLLM = (ai.mode || mode.ai || '').indexOf('rule') === -1 && (ai.provider && ai.provider !== 'rule');
    const aiPanel = panel('Analysis mode', h('div', { class: 'stack' },
      h('div', { class: 'row' },
        isLLM ? tag('LLM-assisted', 'tag-warn') : tag('Rule-based', 'tag-ok'),
        h('code', null, ai.mode || mode.ai || 'unknown')),
      h('p', null, ai.label || (isLLM ? 'LLM output is shown as unverified hypotheses.' : 'Deterministic rule-based analysis (no LLM used).')),
      kv([
        ['LLM calls', fmt.int(ai.llm_calls)],
        ['Tokens in / out', `${fmt.int(ai.input_tokens)} / ${fmt.int(ai.output_tokens)}`],
        ['Model', ai.model || undefined],
        ['Exec time', ex.total_seconds !== undefined ? `${fmt.num(ex.total_seconds, 1)} s total · ${fmt.num(ex.exec_seconds, 1)} s executing` : undefined],
      ]),
      (ai.notes || []).length ? h('ul', { class: 'small' }, ai.notes.map((n) => h('li', null, n))) : null), { id: 'p-ai' });

    root.appendChild(h('div', { class: 'grid grid-3' }, envPanel, aiPanel, scanPanel()));

    // flags table
    const coreCount = {};
    for (const hz of d.hazards) for (const f of Object.keys(hz.core || {})) coreCount[f] = (coreCount[f] || 0) + 1;
    const maxDeg = Math.max(1, ...d.flags.map((f) => f.degree || 0));
    const flagRows = d.flags.map((f) => h('tr', null,
      h('th', { scope: 'row' }, h('code', null, f.name)),
      h('td', null, stateBadge(f.live)),
      h('td', null, (f.segment_scoped || []).length ? (f.segment_scoped || []).join(', ') : h('span', { class: 'muted' }, 'no')),
      h('td', { class: 'num' }, h('span', { class: 'bar-inline', style: { width: Math.round(40 * (f.degree || 0) / maxDeg) + 'px' }, 'aria-hidden': 'true' }), fmt.int(f.degree)),
      h('td', { class: 'num' }, coreCount[f.name] ? h('span', null, '✕ ', String(coreCount[f.name])) : h('span', { class: 'muted' }, '0'))));
    root.appendChild(panel('Flags in this environment', h('div', { class: 'table-wrap' },
      h('table', { class: 'data' },
        h('caption', { class: 'visually-hidden' }, 'Flags, their live state and hazard involvement'),
        h('thead', null, h('tr', null, h('th', { scope: 'col' }, 'Flag'), h('th', { scope: 'col' }, 'Live state'), h('th', { scope: 'col' }, 'Segment-scoped'),
          h('th', { scope: 'col', class: 'num' }, 'Code coupling'), h('th', { scope: 'col', class: 'num' }, 'In hazard cores'))),
        h('tbody', null, flagRows))), { id: 'p-flags' }));

    // constraints + runs
    const cons = (c.declared || []).map((x) => h('li', null, h('code', null, x.rule || x.id), ' ', h('span', { class: 'muted small' }, `(${x.id}) ${x.reason || ''}`)));
    const consBody = h('div', { class: 'stack' },
      cons.length ? h('ul', null, cons) : h('p', { class: 'muted' }, 'No constraints declared. All 2^n combinations are treated as feasible.'),
      (c.baseline_violations || []).length ? h('div', { class: 'alert alert-error' }, 'The live state violates: ' + c.baseline_violations.join(', ')) : null,
      c.consistent === false ? h('div', { class: 'alert alert-error' }, 'Constraints are inconsistent: ' + JSON.stringify(c.conflict || [])) : null);
    const runRows = state.runs.slice(0, 10).map((run) => h('tr', null,
      h('td', null, h('button', { class: 'btn-link mono', type: 'button', onclick: () => selectRun(run.run_id) }, run.run_id)),
      h('td', null, fmt.time(run.created_at)),
      h('td', null, run.environment || '—'),
      h('td', null, verdictTag(run.status)),
      h('td', { class: 'num' }, fmt.int(run.hazards))));
    const runsBody = runRows.length ? h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
      h('thead', null, h('tr', null, ['Run', 'Created', 'Env', 'Verdict', 'Hazards'].map((x, i) => h('th', { scope: 'col', class: i === 4 ? 'num' : null }, x)))),
      h('tbody', null, runRows))) : h('p', { class: 'muted' }, 'No runs recorded.');
    root.appendChild(h('div', { class: 'grid grid-2' }, panel('Declared constraints', consBody, { id: 'p-cons' }), panel('Recent runs', runsBody, { id: 'p-runs' })));
  }

  function caveatText(c) {
    const text = c || 'Finite testing: a clean result means no failure was observed under the executed configurations, tests and personas. It is not a proof of safety.';
    const m = /^(Finite testing[.:])\s*(.*)$/s.exec(text);
    return m ? h('div', null, h('strong', null, m[1] + ' '), m[2]) : h('div', null, h('strong', null, 'Finite testing. '), text);
  }

  function verdictTag(st) {
    if (st === 'LIVE_HAZARD') return tag('✕ LIVE HAZARD', 'tag-live');
    if (st === 'LATENT_HAZARDS') return tag('! LATENT', 'tag-latent');
    if (st === 'NO_HAZARDS_FOUND') return tag('✓ NONE FOUND', 'tag-ok');
    return tag(st || '—', 'tag-ghost');
  }

  function sessionPanel() {
    const s = state.session || {};
    return panel('Session', kv([
      ['Project', s.project || '—'],
      ['Environment', s.environment || '—'],
      ['Protected', (s.protected_environments || []).join(', ') || '—'],
    ]), { id: 'p-session' });
  }

  // ---------------------------------------------------------------- scan
  function scanPanel() {
    const sc = state.scan || { state: 'idle' };
    const running = sc.state === 'running';
    const btn = h('button', { class: 'btn btn-primary', type: 'button', disabled: running || !state.session, 'aria-describedby': 'scan-help' },
      running ? h('span', { class: 'spinner', 'aria-hidden': 'true' }) : h('span', { 'aria-hidden': 'true' }, '⟳'), running ? 'Scanning…' : 'Re-scan');
    btn.addEventListener('click', () => startScan({ environment: envName() || undefined }));
    const stateLine = h('p', { class: 'scan-state', role: 'status' },
      sc.state === 'idle' ? '● idle — no scan started from this dashboard' :
        sc.state === 'running' ? '◐ running' + (sc.reason === 'reverify' ? ' (re-verification)' : '') :
          sc.state === 'done' ? `✓ done — run ${sc.run_id || '?'} · ${sc.status || ''}` :
            sc.state === 'error' ? '✕ error — ' + (sc.error || 'scan failed') : String(sc.state));
    const log = (sc.log || []).slice(-60);
    return panel('Re-scan', h('div', { class: 'stack' },
      h('p', { id: 'scan-help', class: 'small' }, 'Takes a fresh read-only snapshot of the Flagsmith environment and re-runs the combinatorial tests. It never changes flags.'),
      h('div', { class: 'row' }, btn),
      stateLine,
      log.length ? h('pre', { class: 'scan-log', tabindex: '0', 'aria-label': 'Scan log' }, log.join('\n')) : null), { id: 'p-scan' });
  }

  async function startScan(body) {
    try {
      const res = await apiPost('/api/scan', body);
      state.scan = res || { state: 'running' };
      announce('Scan started');
    } catch (e) {
      state.scan = { state: 'error', error: String(e.message || e), log: [] };
    }
    refreshScanViews();
    pollScan();
  }

  function pollScan() {
    clearTimeout(scanTimer);
    if (!state.scan || state.scan.state !== 'running') return;
    scanTimer = setTimeout(async () => {
      try {
        const r = await apiGet('/api/scan');
        if (r.ok && r.body) state.scan = r.body;
      } catch (_) { /* keep polling */ }
      if (state.scan.state === 'running') { refreshScanViews(); pollScan(); return; }
      announce(state.scan.state === 'done' ? `Scan finished: ${state.scan.status || ''}` : 'Scan failed');
      if (state.scan.state === 'done') {
        state.runId = 'latest';
        await Promise.all([loadRuns(), loadReport(), loadAudit(), loadProposals()]);
        renderAll();
      } else refreshScanViews();
    }, 1500);
  }

  function refreshScanViews() {
    const old = document.getElementById('p-scan');
    if (old) old.replaceWith(scanPanel());
    if (state.tab === 'hazards') renderProposalsInto();
  }

  // ---------------------------------------------------------------- reaction matrix
  const CELL = {
    hazard: { g: '!', name: 'hazard' },
    hazardLive: { g: '✕', name: 'live hazard' },
    covered: { g: '✓', name: 'covered' },
    partial: { g: '◐', name: 'partially covered' },
    untested: { g: '?', name: 'untested' },
    na: { g: '—', name: 'infeasible' },
  };

  function pairStatus(p) {
    if (!p) return 'untested';
    if (p.feasible === 0) return 'na';
    if (p.status === 'hazard' || (p.hazards || []).length) return 'hazard';
    if (p.status) return p.status;
    if (p.covered >= p.feasible) return 'covered';
    return p.covered > 0 ? 'partial' : 'untested';
  }

  function tripleStatus(t) {
    if (!t) return 'untested';
    if (t.feasible === 0) return 'na';
    if ((t.hazards || []).length) return 'hazard';
    if (t.covered >= t.feasible) return 'covered';
    return t.covered > 0 ? 'partial' : 'untested';
  }

  function anyLive(ids) { return (ids || []).some((id) => derived.byId[id] && derived.byId[id].status === 'live'); }

  function cellButton(st, ids, label, opts) {
    opts = opts || {};
    const live = st === 'hazard' && anyLive(ids);
    const key = st === 'hazard' ? (live ? 'hazardLive' : 'hazard') : st;
    const c = CELL[key] || CELL.untested;
    const b = h(opts.static ? 'span' : 'button', {
      class: `cell st-${st}${live ? ' live' : ''}`,
      type: opts.static ? null : 'button',
      'aria-label': opts.static ? null : label,
      'aria-pressed': opts.static ? null : 'false',
      tabindex: opts.static ? null : '-1',
      title: opts.static ? null : label,
      'aria-hidden': opts.static ? 'true' : null,
    }, h('span', { class: 'g', 'aria-hidden': 'true' }, c.g));
    return b;
  }

  function legend() {
    const items = [
      ['hazardLive', 'hazard', true, 'Hazard core, live (✕): fails now'],
      ['hazard', 'hazard', false, 'Hazard core, latent (!)'],
      ['covered', 'covered', false, 'All feasible combos executed, no failure (✓)'],
      ['partial', 'partial', false, 'Some feasible combos executed (◐)'],
      ['untested', 'untested', false, 'No combo executed (?)'],
      ['na', 'na', false, 'Infeasible under constraints (—)'],
    ];
    return h('ul', { class: 'legend', 'aria-label': 'Matrix legend' }, items.map(([k, st, live, text]) => {
      const cell = h('span', { class: `cell st-${st}${live ? ' live' : ''}`, 'aria-hidden': 'true' }, h('span', { class: 'g' }, CELL[k].g));
      return h('li', null, cell, text);
    }));
  }

  function combosFor(a, b) {
    const out = [];
    for (const va of [true, false]) for (const vb of [true, false]) {
      const ts = derived.tests.filter((t) => t.cfg[a] === va && t.cfg[b] === vb);
      out.push({ va, vb, n: ts.length, fails: ts.filter((t) => t.anyFail).length, idx: ts.map((t) => t.index) });
    }
    return out;
  }

  function hazardLinks(ids) {
    if (!(ids || []).length) return h('span', { class: 'muted' }, 'none');
    return h('span', { class: 'row inline' }, ids.map((id) => {
      const hz = derived.byId[id];
      return h('a', { href: '#hazards/' + encodeURIComponent(id), class: 'tag ' + (hz && hz.status === 'live' ? 'tag-live' : 'tag-latent') },
        (hz && hz.status === 'live' ? '✕ ' : '! ') + id);
    }));
  }

  function pairDetail(a, b) {
    if (!a) {
      return h('div', { class: 'stack' }, h('p', { class: 'muted' }, 'Hover, focus or click a cell to inspect a flag pair. Use the arrow keys to move between cells.'));
    }
    if (a === b) {
      const ids = derived.soloHazards[a] || [];
      return h('div', { class: 'stack' },
        h('h4', null, a),
        h('p', null, 'Diagonal: the flag on its own. ', stateBadge(derived.live[a]), ' in the live state.'),
        h('div', null, h('div', { class: 'label' }, 'Single-flag hazards'), hazardLinks(ids)));
    }
    const p = derived.pairMap.get(a + '|' + b);
    const st = pairStatus(p);
    const combos = combosFor(a, b);
    const allExecutedCovered = p && p.covered >= p.feasible;
    const rows = combos.map((c) => h('tr', null,
      h('td', null, stateBadge(c.va)),
      h('td', null, stateBadge(c.vb)),
      h('td', null, c.n ? h('span', null, c.fails ? h('strong', null, `✕ ${c.fails} failing`) : '✓ passing', ` · ${c.n} config${c.n > 1 ? 's' : ''}`)
        : h('span', { class: 'muted' }, allExecutedCovered ? '— infeasible (constraint)' : '? not executed'))));
    return h('div', { class: 'stack' },
      h('h4', null, h('code', null, a), ' ⊕ ', h('code', null, b)),
      h('p', null, 'Status: ', h('strong', null, (CELL[st === 'hazard' ? (anyLive(p && p.hazards) ? 'hazardLive' : 'hazard') : st] || {}).name || st)),
      kv([
        ['Feasible combos', p ? `${p.feasible} of 4` : '—'],
        ['Covered', p ? `${p.covered} / ${p.feasible}` : '—'],
        ['Infeasible', p ? String(p.infeasible ?? 4 - p.feasible) : '—'],
      ]),
      h('div', { class: 'table-wrap' }, h('table', { class: 'combo-table' },
        h('caption', { class: 'visually-hidden' }, 'The four on/off combinations and the executed plan configurations that contain them'),
        h('thead', null, h('tr', null, h('th', { scope: 'col' }, a), h('th', { scope: 'col' }, b), h('th', { scope: 'col' }, 'Executed (plan)'))),
        h('tbody', null, rows))),
      h('div', null, h('div', { class: 'label' }, 'Hazards whose core contains both flags'), hazardLinks(p && p.hazards)));
  }

  function renderMatrix() {
    const root = clear(document.getElementById('tab-matrix'));
    root.appendChild(sectionTitle('h-matrix', 'Reaction matrix', 'Which pairs of flags react, which were tested, and which were never mixed.'));
    if (!state.report) { root.appendChild(noReport('Reaction matrix')); return; }
    const d = derived;
    const flags = d.mflags;
    const n = flags.length;

    const detail = h('div', { class: 'panel-body', id: 'pair-detail', 'aria-live': 'polite' });
    const showDetail = (a, b) => { clear(detail); detail.appendChild(pairDetail(a, b)); };
    const table = h('table', { class: 'matrix', role: 'grid', 'aria-label': `Pairwise reaction matrix of ${n} flags` });
    const headRow = h('tr', null, h('td', null));
    flags.forEach((f, j) => headRow.appendChild(h('th', { scope: 'col', class: 'col', 'data-col': j }, h('span', null, f))));
    table.appendChild(h('thead', null, headRow));
    const tbody = h('tbody');
    const cells = [];
    flags.forEach((a, i) => {
      const tr = h('tr', null, h('th', { scope: 'row', class: 'rowh', 'data-row': i }, a));
      cells[i] = [];
      flags.forEach((b, j) => {
        let btn;
        if (i === j) {
          const solo = d.soloHazards[a] || [];
          const st = solo.length ? 'hazard' : 'diag';
          btn = cellButton(st, solo, `${a} alone: ${solo.length ? 'single-flag hazard ' + solo.join(', ') : 'no single-flag hazard'}`);
          if (!solo.length) btn.querySelector('.g').textContent = '■';
        } else {
          const p = d.pairMap.get(a + '|' + b);
          const st = pairStatus(p);
          const live = st === 'hazard' && anyLive(p && p.hazards);
          const nm = (CELL[st === 'hazard' ? (live ? 'hazardLive' : 'hazard') : st] || {}).name;
          const lab = `${a} with ${b}: ${nm}; ${p ? `${p.covered} of ${p.feasible} feasible combinations covered` : 'no data'}${p && (p.hazards || []).length ? '; hazards ' + p.hazards.join(', ') : ''}`;
          btn = cellButton(st, p && p.hazards, lab);
        }
        btn.dataset.r = i; btn.dataset.c = j;
        const sel = state.matrixSel;
        if (sel && sel[0] === a && sel[1] === b) btn.setAttribute('aria-pressed', 'true');
        cells[i][j] = btn;
        tr.appendChild(h('td', { role: 'gridcell' }, btn));
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);

    const highlight = (i, j) => {
      table.querySelectorAll('th.hl').forEach((x) => x.classList.remove('hl'));
      if (i === null) return;
      const rh = table.querySelector(`th[data-row="${i}"]`); if (rh) rh.classList.add('hl');
      const ch = table.querySelector(`th[data-col="${j}"]`); if (ch) ch.classList.add('hl');
    };
    const focusCell = (i, j) => {
      const b = cells[i] && cells[i][j]; if (!b) return;
      table.querySelectorAll('.cell[tabindex="0"]').forEach((x) => x.setAttribute('tabindex', '-1'));
      b.setAttribute('tabindex', '0'); b.focus();
    };
    let si = 0, sj = Math.min(1, n - 1);
    if (state.matrixSel) { si = Math.max(0, flags.indexOf(state.matrixSel[0])); sj = Math.max(0, flags.indexOf(state.matrixSel[1])); }
    if (cells[si] && cells[si][sj]) cells[si][sj].setAttribute('tabindex', '0');

    table.addEventListener('mouseover', (e) => {
      const b = e.target.closest('.cell'); if (!b || !table.contains(b)) return;
      const i = +b.dataset.r, j = +b.dataset.c; highlight(i, j); showDetail(flags[i], flags[j]);
    });
    table.addEventListener('mouseleave', () => {
      const s = state.matrixSel; highlight(s ? flags.indexOf(s[0]) : null, s ? flags.indexOf(s[1]) : null);
      showDetail(s ? s[0] : null, s ? s[1] : null);
    });
    table.addEventListener('focusin', (e) => {
      const b = e.target.closest('.cell'); if (!b) return;
      const i = +b.dataset.r, j = +b.dataset.c; highlight(i, j); showDetail(flags[i], flags[j]);
    });
    table.addEventListener('click', (e) => {
      const b = e.target.closest('button.cell'); if (!b) return;
      table.querySelectorAll('.cell[aria-pressed="true"]').forEach((x) => x.setAttribute('aria-pressed', 'false'));
      b.setAttribute('aria-pressed', 'true');
      const i = +b.dataset.r, j = +b.dataset.c;
      state.matrixSel = [flags[i], flags[j]];
      focusCell(i, j); showDetail(flags[i], flags[j]);
    });
    table.addEventListener('keydown', (e) => {
      const b = e.target.closest('.cell'); if (!b) return;
      let i = +b.dataset.r, j = +b.dataset.c;
      const k = e.key;
      if (k === 'ArrowRight') j = Math.min(n - 1, j + 1);
      else if (k === 'ArrowLeft') j = Math.max(0, j - 1);
      else if (k === 'ArrowDown') i = Math.min(n - 1, i + 1);
      else if (k === 'ArrowUp') i = Math.max(0, i - 1);
      else if (k === 'Home') j = 0;
      else if (k === 'End') j = n - 1;
      else return;
      e.preventDefault(); focusCell(i, j);
    });

    const nHaz = (state.report.matrix.pairs || []).filter((p) => pairStatus(p) === 'hazard').length;
    const nUnt = (state.report.matrix.pairs || []).filter((p) => ['untested', 'partial'].includes(pairStatus(p))).length;
    root.appendChild(h('div', { class: 'matrix-layout' },
      h('div', { class: 'stack' },
        legend(),
        h('p', { class: 'small muted' }, `${(state.report.matrix.pairs || []).length} flag pairs · ${nHaz} in a hazard core · ${nUnt} not fully covered. The diagonal shows single-flag hazards.`),
        h('div', { class: 'matrix-scroll' }, table)),
      h('section', { class: 'panel detail-card', 'aria-labelledby': 'pair-detail-h' },
        h('div', { class: 'panel-head' }, h('h3', { id: 'pair-detail-h' }, 'Pair inspector')), detail)));
    showDetail(state.matrixSel ? state.matrixSel[0] : null, state.matrixSel ? state.matrixSel[1] : null);

    root.appendChild(reactionChains());
    root.appendChild(sliceView());
  }

  function chain(hz) {
    const core = Object.entries(hz.core || {});
    const kids = [];
    core.forEach(([f, v], i) => {
      if (i) kids.push(h('span', { class: 'op', 'aria-label': 'combined with' }, '⊕'));
      kids.push(flagChip(f, v, 'hz'));
    });
    const tests = Array.from(new Set((hz.atoms || []).map((a) => shortTest(a.test))));
    kids.push(h('span', { class: 'chain-arrow', 'aria-label': 'causes' }, '→'));
    kids.push(h('span', { class: 'chip result' + (hz.status === 'live' ? '' : ' latent') },
      h('span', null, (hz.status === 'live' ? '✕ ' : '! ') + (tests.join(', ') || 'failure')),
      (hz.atoms || [])[0] ? h('span', { class: 'chip-val' }, hz.atoms[0].kind || '') : null));
    return h('div', { class: 'chain' }, kids);
  }

  function reactionChains() {
    const hi = derived.hazards.filter((x) => Object.keys(x.core || {}).length >= 3);
    const lo = derived.hazards.filter((x) => Object.keys(x.core || {}).length < 3);
    const list = (arr) => h('ul', { class: 'stack', style: { listStyle: 'none', padding: '0', margin: '0' } }, arr.map((hz) => h('li', null,
      h('div', { class: 'row small', style: { marginBottom: '6px' } },
        h('a', { href: '#hazards/' + encodeURIComponent(hz.id) }, h('strong', null, hz.id)), statusTag(hz.status), sevTag(hz.severity),
        tag(`${Object.keys(hz.core || {}).length}-way`, 'tag-ghost')),
      chain(hz))));
    return panel('Reaction chains (3-way and higher)', h('div', { class: 'stack' },
      h('p', { class: 'small' }, 'Higher-order hazards do not show up in any single pair: each pair below is safe on its own. Only the full chain reacts.'),
      hi.length ? list(hi) : h('p', { class: 'muted' }, 'No 3-way or higher hazards were found in the executed configurations.'),
      lo.length ? h('details', null, h('summary', null, `Lower-order reactions (${lo.length}), also visible in the grid`), h('div', { style: { marginTop: '10px' } }, list(lo))) : null),
    { id: 'p-chains' });
  }

  function sliceView() {
    const flags = derived.mflags;
    const triples = (state.report.matrix || {}).triples || [];
    if (!triples.length) return panel('3-way slice', h('p', { class: 'muted' }, 'No 3-way coverage data in this report.'), { id: 'p-slice' });
    if (!state.sliceFlag || !flags.includes(state.sliceFlag)) {
      const score = {};
      for (const t of triples) if ((t.hazards || []).length) for (const f of t.flags) score[f] = (score[f] || 0) + 1;
      state.sliceFlag = flags.slice().sort((a, b) => (score[b] || 0) - (score[a] || 0))[0];
    }
    const A = state.sliceFlag;
    const select = h('select', { class: 'input', id: 'slice-flag' }, flags.map((f) => h('option', { value: f }, f)));
    select.value = A;
    select.addEventListener('change', () => { state.sliceFlag = select.value; const p = document.getElementById('p-slice'); p.replaceWith(sliceView()); document.getElementById('slice-flag').focus(); });
    const others = flags.filter((f) => f !== A);
    const info = h('div', { class: 'small', 'aria-live': 'polite' }, h('span', { class: 'muted' }, 'Hover or focus a cell to see the triple.'));
    const table = h('table', { class: 'matrix', 'aria-label': `3-way coverage of ${A} with every other pair` });
    const hr = h('tr', null, h('td'));
    others.forEach((f) => hr.appendChild(h('th', { scope: 'col', class: 'col' }, h('span', null, f))));
    table.appendChild(h('thead', null, hr));
    const tb = h('tbody');
    others.forEach((b, i) => {
      const tr = h('tr', null, h('th', { scope: 'row', class: 'rowh' }, b));
      others.forEach((c, j) => {
        if (i === j) { tr.appendChild(h('td', null, h('span', { class: 'cell st-diag', 'aria-hidden': 'true' }, h('span', { class: 'g' }, '■')))); return; }
        const t = derived.tripleMap.get([A, b, c].sort().join('|'));
        const st = tripleStatus(t);
        const live = st === 'hazard' && anyLive(t && t.hazards);
        const nm = (CELL[st === 'hazard' ? (live ? 'hazardLive' : 'hazard') : st] || {}).name;
        const label = `${A} + ${b} + ${c}: ${nm}; ${t ? `${t.covered} of ${t.feasible} feasible combinations covered` : 'no data'}${t && (t.hazards || []).length ? '; hazards ' + t.hazards.join(', ') : ''}`;
        const btn = cellButton(st, t && t.hazards, label);
        btn.setAttribute('tabindex', '0');
        btn.removeAttribute('aria-pressed');
        const show = () => { clear(info); append(info, [h('strong', null, `${A} ⊕ ${b} ⊕ ${c}`), ` — ${nm}; ${t ? `${t.covered} of ${t.feasible} feasible combinations covered (8 total)` : 'no data'}. `, t && (t.hazards || []).length ? hazardLinks(t.hazards) : null]); };
        btn.addEventListener('mouseenter', show); btn.addEventListener('focus', show); btn.addEventListener('click', show);
        tr.appendChild(h('td', null, btn));
      });
      tb.appendChild(tr);
    });
    table.appendChild(tb);
    return panel('3-way slice', h('div', { class: 'stack' },
      h('div', { class: 'slice-controls' }, h('label', { for: 'slice-flag', class: 'label' }, 'Fix flag A'), select,
        h('span', { class: 'small muted' }, `Each cell is the triple A ⊕ row ⊕ column (8 on/off combinations).`)),
      h('div', { class: 'matrix-scroll' }, table), info), { id: 'p-slice' });
  }

  // ---------------------------------------------------------------- hazards
  function renderHazards() {
    const root = clear(document.getElementById('tab-hazards'));
    root.appendChild(sectionTitle('h-hazards', 'Hazard report', 'Minimal failing combinations, verified by re-execution. Live hazards first.'));
    if (!state.report) { root.appendChild(noReport('Hazard report')); root.appendChild(h('div', { id: 'proposals-wrap' })); renderProposalsInto(); return; }
    const d = derived;
    if (!d.hazards.length) {
      root.appendChild(h('div', { class: 'empty-state' }, h('div', { class: 'sym', 'aria-hidden': 'true' }, h('span', null, '✓')),
        h('h3', null, 'No hazards found in the executed configurations'),
        h('p', { class: 'muted' }, (state.report.verdict || {}).caveat || '')));
    } else {
      const list = h('ul', { class: 'hz-list', 'aria-label': 'Hazards' }, d.hazards.map((hz) => h('li', null,
        h('button', { class: 'hz-item', type: 'button', 'aria-current': hz.id === state.hazardId ? 'true' : 'false', onclick: () => { location.hash = '#hazards/' + encodeURIComponent(hz.id); } },
          h('span', { class: 'row' }, h('strong', { class: 'mono' }, hz.id), statusTag(hz.status), sevTag(hz.severity), tag(`${Object.keys(hz.core || {}).length}-way`, 'tag-ghost')),
          h('span', { class: 'hz-title' }, Object.entries(hz.core || {}).map(([f, v]) => `${f}=${v ? 'on' : 'off'}`).join(' + ') + ' → ' + Array.from(new Set((hz.atoms || []).map((a) => shortTest(a.test)))).join(', '))))));
      const hz = d.byId[state.hazardId] || d.hazards[0];
      root.appendChild(h('div', { class: 'hz-layout' }, h('nav', { 'aria-label': 'Hazard list' }, list), hazardDetail(hz)));
    }
    root.appendChild(h('div', { id: 'proposals-wrap' }));
    renderProposalsInto();
  }

  function hazardDetail(hz) {
    const coreEntries = Object.entries(hz.core || {});
    const gen = hz.generality || {};
    const ex = hz.explanation || {};
    const llm = ex.llm;
    const isLive = hz.status === 'live';

    const head = h('div', { class: 'hz-detail-head' + (isLive ? ' live' : '') },
      h('div', { class: 'stripe', 'aria-hidden': 'true' }),
      h('div', { class: 'inner' },
        h('div', { class: 'row', style: { marginBottom: '8px' } }, h('strong', { class: 'mono' }, hz.id), statusTag(hz.status), sevTag(hz.severity),
          tag(hz.failure_class || 'failure', 'tag-ghost'), tag(`${coreEntries.length}-way`, 'tag-ghost'),
          hz.fingerprint ? h('span', { class: 'hash' }, 'fp ' + hz.fingerprint) : null),
        h('h2', { id: 'hz-title', tabindex: '-1' }, hz.title || hz.id),
        h('p', { class: 'small' }, isLive
          ? `Live: the current ${envName() || ''} state already contains this combination and fails for ${(hz.personas || []).join(', ') || 'the tested personas'}.`
          : `Latent: the current state does not contain this combination, but it is reachable by changing flags. Affects ${(hz.personas || []).join(', ') || 'the tested personas'}.`)));

    // minimal failing condition
    const pinned = Object.entries(hz.pinned || {});
    const cond = panel('Minimal failing condition', h('div', { class: 'stack' },
      h('div', { class: 'chain' }, coreEntries.map(([f, v], i) => [i ? h('span', { class: 'op', 'aria-label': 'and' }, '⊕') : null, flagChip(f, v, 'hz')])),
      h('p', { class: 'small' }, `Each of these ${coreEntries.length} literal(s) is necessary: flipping any one makes the failure disappear. Minimized with ${fmt.int(hz.minimization_tests)} extra test executions. Trigger claim: `, h('code', null, hz.trigger_claim || '—'), '.'),
      pinned.length ? h('div', null, h('div', { class: 'label' }, 'Pinned by constraints (implied, not part of the cause)'),
        h('div', { class: 'chain' }, pinned.map(([f, p]) => h('span', { class: 'row' }, flagChip(f, p.value, 'pinned'), h('span', { class: 'small muted' }, 'by ' + (p.by || []).join(', ')))))) : null,
      (hz.masked || []).length ? h('p', { class: 'small' }, h('span', { class: 'label' }, 'Masked flags '), (hz.masked || []).join(', '), h('span', { class: 'muted' }, ' — their value hides the failure in some completions.')) : null,
      h('div', null, h('div', { class: 'label' }, 'Flagsmith overrides for the failing persona'),
        Object.keys(hz.overrides_applied || {}).length
          ? h('div', { class: 'chain' }, Object.entries(hz.overrides_applied).map(([f, v]) => flagChip(f, typeof v === 'object' ? !!(v && v.enabled) : !!v)))
          : h('p', { class: 'small muted' }, 'None: the failure reproduces with environment defaults (no identity or segment overrides).')),
      Object.keys(hz.repro_diff_from_live || {}).length ? h('div', null, h('div', { class: 'label' }, 'Reproduction config differs from live state in'),
        h('div', { class: 'chain' }, Object.entries(hz.repro_diff_from_live).map(([f, v]) => flagChip(f, v)))) : null), { id: 'p-cond' });

    // generality
    const checked = gen.checked || 0;
    const w = (x) => checked ? (100 * (x || 0) / checked) + '%' : '0%';
    const genPanel = panel('Generality', h('div', { class: 'stack' },
      h('p', null, h('strong', null, `Fails ${fmt.int(gen.failed)} / ${fmt.int(checked)} `), 'checked completions',
        gen.masked ? ` (${fmt.int(gen.masked)} masked)` : '', ` · ${gen.exhaustive ? 'exhaustive' : 'sampled'}`,
        gen.completion_space ? ` over a completion space of ${fmt.int(gen.completion_space)}` : '', '.'),
      h('div', { class: 'gen-bar', role: 'img', 'aria-label': `${gen.failed || 0} failed, ${gen.passed || 0} passed, ${gen.masked || 0} masked of ${checked}` },
        h('span', { class: 'f', style: { width: w(gen.failed) } }), h('span', { class: 'p', style: { width: w(gen.passed) } }), h('span', { class: 'm', style: { width: w(gen.masked) } })),
      h('div', { class: 'gen-legend', 'aria-hidden': 'true' },
        h('span', null, h('i', { style: { background: 'var(--red)' } }), `✕ failed ${gen.failed || 0}`),
        h('span', null, h('i', { style: { backgroundImage: 'repeating-linear-gradient(90deg,#111 0 1px,transparent 1px 5px)' } }), `✓ passed ${gen.passed || 0}`),
        h('span', null, h('i', { style: { backgroundImage: 'repeating-linear-gradient(-45deg,#c9c3b2 0 2px,#F5F1E6 2px 6px)' } }), `masked ${gen.masked || 0}`)),
      h('p', { class: 'small' }, 'Verdict: ', h('code', null, gen.verdict || '—'),
        gen.verdict === 'sufficient' ? ' — the core alone is enough to fail in every checked completion.' :
          gen.verdict === 'held-in-sample' ? ' — failed in every sampled completion; not proven for all.' : ''),
      (gen.passing_examples || []).length ? h('details', null, h('summary', null, `Passing completions (${gen.passing_examples.length})`), h('pre', null, JSON.stringify(gen.passing_examples, null, 1))) : null), { id: 'p-gen' });

    // failing tests
    const atoms = hz.atoms || [];
    const testsPanel = panel('Failing tests', h('div', { class: 'stack' },
      h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
        h('thead', null, h('tr', null, ['Persona', 'Test', 'Error'].map((x) => h('th', { scope: 'col' }, x)))),
        h('tbody', null, atoms.map((a) => h('tr', null, h('td', null, h('code', null, a.persona)), h('td', null, h('code', null, a.test)),
          h('td', null, h('strong', null, '✕ ' + (a.kind || '')), h('br'), h('span', { class: 'small' }, a.message || ''))))))),
      ex.observed_error ? h('div', null, h('div', { class: 'label' }, 'Error log'), h('pre', { tabindex: '0' }, ex.observed_error)) : null,
      (hz.signatures || []).length ? h('p', { class: 'small muted' }, 'Failure signature: ', h('code', null, hz.signatures.join(' | '))) : null), { id: 'p-tests' });

    // repro
    const rp = hz.repro || {};
    const reproPanel = panel('Reproduce', h('div', { class: 'stack' },
      rp.donotmix ? cmdBlock('DoNotMix', rp.donotmix) : null,
      rp.command ? cmdBlock(`Direct test command (persona ${rp.persona || '?'})`, rp.command) : null,
      rp.document ? cmdBlock('Environment document', rp.document) : null), { id: 'p-repro' });

    // explanation
    const ul = (items) => (items || []).length ? h('ul', null, items.map((x) => h('li', null, typeof x === 'string' ? x : JSON.stringify(x)))) : null;
    const llmBody = llm ? h('div', null,
      ul([llm.root_cause_hypothesis ? 'Hypothesis: ' + llm.root_cause_hypothesis : null, llm.explanation].filter(Boolean)),
      (llm.suspect_locations || []).length ? h('ul', null, llm.suspect_locations.map((l) => h('li', null, h('code', null, l.location), l.matches_static_evidence ? ' ✓ matches static evidence' : ' ? not in static evidence'))) : null,
      llm.confidence !== undefined && llm.confidence !== null ? h('p', { class: 'small', style: { padding: '0 10px' } }, 'Model confidence (self-reported): ', String(llm.confidence)) : null)
      : h('p', { class: 'empty' }, `Not used. Analysis mode: ${ex.mode || 'rule-based'}.`);
    const explainPanel = panel('Explanation', h('div', { class: 'stack' },
      h('div', { class: 'explain-cols' },
        h('div', { class: 'explain verified' }, h('h4', null, h('span', { 'aria-hidden': 'true' }, '✓'), 'Verified by tests'), ul(ex.verified) || h('p', { class: 'empty' }, 'Nothing recorded.')),
        h('div', { class: 'explain heuristic' }, h('h4', null, h('span', { 'aria-hidden': 'true' }, '≈'), 'Heuristic (static analysis)'), ul(ex.reasoning) || h('p', { class: 'empty' }, 'Nothing recorded.')),
        h('div', { class: 'explain llm' }, h('h4', null, h('span', { 'aria-hidden': 'true' }, '?'), 'LLM (unverified)'), llmBody)),
      ex.label ? h('p', { class: 'small muted' }, ex.label) : null), { id: 'p-explain' });

    const fixes = (ex.fix_suggestions || []).map((x) => h('li', null, x));
    const llmFixes = llm ? (llm.fix_suggestions || []).map((x) => h('li', null, h('span', { class: 'tag tag-ghost' }, 'LLM · unverified'), ' ', x)) : [];
    const related = hz.related || [];
    const props = state.proposals.filter((p) => ((p.evidence || {}).resolves || []).includes(hz.id));
    const fixPanel = panel('Fix suggestions', h('div', { class: 'stack' },
      fixes.length || llmFixes.length ? h('ol', null, fixes, llmFixes) : h('p', { class: 'muted' }, 'No suggestions recorded.'),
      props.length ? h('div', null, h('div', { class: 'label' }, 'Mitigation proposals that resolve this hazard'),
        h('div', { class: 'row' }, props.map((p) => h('a', { href: '#proposal-' + encodeURIComponent(p.id), class: 'tag' + (p.approval && p.approval.applied ? ' tag-ok' : '') }, p.id)))) : null,
      related.length ? h('div', null, h('div', { class: 'label' }, 'Related hazards'),
        h('ul', null, related.map((r) => h('li', null, h('a', { href: '#hazards/' + encodeURIComponent(r.id) }, r.id), ' — ', r.relation || 'related')))) : null), { id: 'p-fix' });

    // code refs
    const refs = hz.code_refs || [];
    const codePanel = panel(`Related code (${refs.length})`, refs.length ? h('div', { class: 'stack' }, refs.map((c) => h('div', null,
      h('div', { class: 'snippet-head' }, `${c.file}:${c.line}`, c.scope ? ` · ${c.scope}()` : '', c.flag ? ` · flag ${c.flag}` : ''),
      h('pre', { tabindex: '0', 'aria-label': `Code at ${c.file} line ${c.line}` }, h('code', null, c.snippet || ''))))) : h('p', { class: 'muted' }, 'No code references found by static analysis.'), { id: 'p-code' });

    const dep = hz.dependency_evidence || [];
    const depPanel = dep.length ? h('details', { class: 'panel' }, h('summary', { class: 'panel-head' }, h('h3', null, `Static dependency evidence (${dep.length})`)),
      h('div', { class: 'panel-body' }, h('ul', null, dep.map((e) => h('li', null, h('code', null, e.where), ` (${e.kind}) links `, (e.flags || []).join(', '), e.detail ? ' — ' + e.detail : ''))))) : null;

    return h('article', { class: 'hz-detail stack', 'aria-labelledby': 'hz-title' }, head,
      h('div', { class: 'grid grid-2' }, cond, genPanel), testsPanel, explainPanel, h('div', { class: 'grid grid-2' }, fixPanel, reproPanel), codePanel, depPanel);
  }

  // ---------------------------------------------------------------- approvals
  function auditFor(pid) { return ((state.audit && state.audit.entries) || []).filter((e) => e.proposal === pid); }

  function isProtected(name) {
    if (!name) return false;
    const low = name.toLowerCase();
    return low.startsWith('prod') || ((state.session && state.session.protected_environments) || []).some((p) => p.toLowerCase() === low);
  }

  function renderProposalsInto() {
    const wrap = document.getElementById('proposals-wrap');
    if (!wrap) return;
    const focusedId = document.activeElement && document.activeElement.id;
    clear(wrap);
    const env = envName();
    const all = state.proposals || [];
    const mine = all.filter((p) => !env || (p.environment && p.environment.name === env));
    const other = all.filter((p) => !mine.includes(p));
    const body = h('div', { class: 'stack' },
      h('p', { class: 'small' }, 'DoNotMix never changes a flag on its own. A human must type the environment name to approve a proposal; only then can it be applied, and the result re-verified with a fresh scan. Every step is written to the tamper-evident audit log.'),
      state.proposalsError ? h('div', { class: 'alert alert-error', role: 'alert' }, state.proposalsError) : null,
      mine.length ? mine.map(proposalCard) : h('div', { class: 'empty-state' },
        h('h3', null, 'No proposals for ' + (env || 'this environment')),
        h('p', { class: 'muted' }, 'Generate verified mitigations with ', h('code', null, 'donotmix propose'), '. Proposals appear here for approval.')),
      other.length ? h('details', null, h('summary', null, `Proposals for other environments (${other.length})`), h('div', { class: 'stack', style: { marginTop: '10px' } }, other.map(proposalCard))) : null);
    wrap.appendChild(panel('Change approvals', body, { id: 'p-approvals', level: 'h2' }));
    if (focusedId) { const f = document.getElementById(focusedId); if (f) f.focus(); }
  }

  function proposalCard(p) {
    const env = (p.environment || {}).name || '?';
    const appr = p.approval || {};
    const events = auditFor(p.id);
    const reverify = events.filter((e) => e.event === 'reverified').slice(-1)[0];
    const refused = events.filter((e) => /refused|declined/.test(e.event)).slice(-1)[0];
    const prot = isProtected(env);
    const pidSafe = String(p.id).replace(/[^A-Za-z0-9_-]/g, '_');
    const msg = state.ui.proposalMsgs[p.id];
    const scanning = state.scan && state.scan.state === 'running';

    const steps = h('ol', { class: 'steps', 'aria-label': 'Approval progress' },
      h('li', { class: 'done' }, h('span', { class: 'label' }, '1 · Proposed ✓'), fmt.time(p.created_at)),
      h('li', { class: appr.approved ? 'done' : 'pending' }, h('span', { class: 'label' }, appr.approved ? '2 · Approved ✓' : '2 · Approval ?'),
        appr.approved ? `${appr.by || '?'} · ${fmt.time(appr.at)}` : (prot ? 'refused: protected env' : 'waiting for a human')),
      h('li', { class: appr.applied ? 'done' : 'pending' }, h('span', { class: 'label' }, appr.applied ? '3 · Applied ✓' : '3 · Apply —'),
        appr.applied ? 'written & read back' : (appr.approved ? 'ready to apply' : 'locked until approved')),
      h('li', { class: reverify ? (reverify.live_hazards && reverify.live_hazards.length ? 'fail' : 'done') : 'pending' },
        h('span', { class: 'label' }, reverify ? (reverify.live_hazards && reverify.live_hazards.length ? '4 · Re-verify ✕' : '4 · Re-verified ✓') : '4 · Re-verify —'),
        reverify ? `${reverify.status || ''} · ${fmt.time(reverify.ts)}${reverify.live_hazards && reverify.live_hazards.length ? ' · still live: ' + reverify.live_hazards.join(', ') : ''}` : (appr.applied ? 'run a fresh scan' : 'after apply')));

    const ev = p.evidence || {};
    const info = kv([
      ['Target', h('span', null, h('strong', null, env), ` (${(p.environment || {}).key || '?'}) · project ${p.project || '?'} · `, h('code', null, p.api_url || '?'))],
      ['Changes', h('ul', { class: 'changes' }, (p.changes || []).map((c) => h('li', null, h('strong', null, c.flag), ': ', stateBadge(c.from), ' → ', stateBadge(c.to))))],
      ['Why', p.rationale || '—'],
      ['Evidence', h('span', null, 'resolves ', hazardLinksSafe(ev.resolves), ` · verified state passes ${fmt.int(ev.verified_passes)}/${fmt.int(ev.verified_runs)} test runs`,
        (ev.still_failing || []).length ? h('strong', null, ' · still failing: ' + ev.still_failing.join(', ')) : '')],
      ['Impact', p.impact || '—'],
      ['Content hash', h('code', { class: 'small' }, fmt.short(p.content_hash, 16))],
    ]);

    let actions;
    if (prot) {
      actions = h('div', { class: 'alert alert-error' }, `${env} is a protected environment. DoNotMix only reports there; it will not approve or apply changes.`);
    } else if (!appr.approved) {
      const typedId = 'typed-' + pidSafe, nameId = 'approver-' + pidSafe;
      const typed = h('input', { class: 'input', id: typedId, autocomplete: 'off', spellcheck: 'false', 'aria-describedby': 'approve-help-' + pidSafe });
      const approver = h('input', { class: 'input', id: nameId, autocomplete: 'name' });
      const btn = h('button', { class: 'btn btn-danger', type: 'submit', disabled: true }, `Approve change to ${env}`);
      const check = () => { btn.disabled = !(typed.value === env && approver.value.trim().length > 0); };
      typed.addEventListener('input', check); approver.addEventListener('input', check);
      const form = h('form', { class: 'approve-form', 'aria-label': 'Approve proposal ' + p.id },
        h('div', { class: 'label', style: { color: 'var(--black)' } }, '⚠ Human approval required'),
        h('p', { id: 'approve-help-' + pidSafe, class: 'small', style: { margin: '6px 0 0' } }, `Read the changes above. To approve, type the environment name exactly (`, h('code', null, env), ') and your name. Nothing is written to Flagsmith until you press Apply.'),
        h('div', { class: 'fields' },
          h('label', { for: typedId }, `Type “${env}” to confirm`, typed),
          h('label', { for: nameId }, 'Approver name', approver)),
        btn);
      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        if (btn.disabled) return;
        btn.disabled = true;
        try {
          const res = await apiPost(`/api/proposals/${encodeURIComponent(p.id)}/approve`, { typed: typed.value, approver: approver.value.trim() });
          state.ui.proposalMsgs[p.id] = { ok: true, text: `Approved. Audit entry ${res.audit || ''}.` };
          announce('Proposal approved');
        } catch (err) {
          state.ui.proposalMsgs[p.id] = { ok: false, text: 'Approval refused: ' + err.message };
        }
        await Promise.all([loadProposals(), loadAudit()]);
        renderProposalsInto();
      });
      actions = form;
    } else {
      const applyBtn = h('button', { class: 'btn btn-danger', type: 'button', disabled: !!appr.applied }, appr.applied ? '✓ Applied' : `Apply to ${env}`);
      const dryBtn = h('button', { class: 'btn', type: 'button', disabled: !!appr.applied }, 'Dry run');
      const reBtn = h('button', { class: 'btn btn-primary', type: 'button', disabled: !appr.applied || scanning }, scanning ? 'Scan running…' : [h('span', { 'aria-hidden': 'true' }, '⟳'), 'Re-verify (re-scan)']);
      const doApply = async (dry) => {
        if (!dry && !window.confirm(`Write ${(p.changes || []).length} approved flag change(s) to ${env} now?`)) return;
        applyBtn.disabled = true; dryBtn.disabled = true;
        try {
          const res = await apiPost(`/api/proposals/${encodeURIComponent(p.id)}/apply`, { dry_run: !!dry });
          state.ui.proposalMsgs[p.id] = { ok: true, text: dry ? 'Dry run — would run:\n' + (res.commands || []).join('\n') : 'Applied. ' + JSON.stringify(res.applied || res) };
          announce(dry ? 'Dry run complete' : 'Changes applied');
        } catch (err) {
          state.ui.proposalMsgs[p.id] = { ok: false, text: (dry ? 'Dry run failed: ' : 'Apply refused: ') + err.message };
        }
        await Promise.all([loadProposals(), loadAudit()]);
        renderProposalsInto();
      };
      applyBtn.addEventListener('click', () => doApply(false));
      dryBtn.addEventListener('click', () => doApply(true));
      reBtn.addEventListener('click', () => { state.ui.proposalMsgs[p.id] = { ok: true, text: 'Re-verification scan started. Progress is shown on the Overview tab.' }; startScan({ environment: env, proposal: p.id, reason: 'reverify' }); });
      actions = h('div', { class: 'stack' },
        h('div', { class: 'row' }, dryBtn, applyBtn, reBtn),
        h('p', { class: 'small muted' }, appr.applied ? 'Applied. Re-verify runs a fresh scan and records the outcome in the audit log.' : 'Apply re-checks the approval hash and the live flag states (drift) before writing, then reads each flag back.'));
    }

    return h('article', { class: 'panel', id: 'proposal-' + p.id, 'aria-labelledby': 'proposal-h-' + pidSafe },
      h('div', { class: 'panel-head' }, h('h3', { id: 'proposal-h-' + pidSafe, class: 'mono', style: { fontFamily: 'var(--font-mono)', fontSize: '1rem', textTransform: 'none', overflowWrap: 'anywhere' } }, p.id),
        tag(p.kind || 'change', 'tag-ghost'), prot ? tag('PROTECTED', 'tag-live') : null,
        appr.applied ? tag('✓ applied', 'tag-ok') : appr.approved ? tag('✓ approved', 'tag-ok') : tag('? awaiting approval', 'tag-latent')),
      h('div', { class: 'panel-body' }, steps, info, actions,
        msg ? h('div', { class: 'result-box', role: msg.ok ? 'status' : 'alert', style: { whiteSpace: 'pre-wrap', borderColor: msg.ok ? 'var(--black)' : 'var(--red)' } }, (msg.ok ? '✓ ' : '✕ ') + msg.text) : null,
        refused && !appr.applied ? h('p', { class: 'small' }, h('strong', null, '✕ Last refusal: '), `${refused.event} — ${refused.reason || ''} (${fmt.time(refused.ts)})`) : null,
        events.length ? h('details', null, h('summary', null, `Audit trail for this proposal (${events.length})`), auditTable(events, true)) : null));
  }

  function hazardLinksSafe(ids) {
    if (!derived) return (ids || []).join(', ') || 'none';
    return hazardLinks(ids);
  }

  // ---------------------------------------------------------------- benchmark
  function hbarChartCompact(rows, opts) {
    // narrow screens: label and value on one line, full-width bar underneath
    const W = 330, rowH = 42, pad = 4;
    const H = rows.length * rowH + pad * 2 + 16;
    const max = opts.max || Math.max(1e-9, ...rows.map((r) => (r.value || 0) + (r.err || 0)));
    const sx = (v) => (W * Math.max(0, Math.min(v, max))) / max;
    const g = svg('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, width: '100%', role: 'img', 'aria-label': opts.title || 'bar chart' });
    g.appendChild(svg('title', null, opts.title || 'bar chart'));
    for (let k = 0; k <= 4; k++) {
      const x = (W * k) / 4;
      g.appendChild(svg('line', { class: 'grid-line', x1: x, x2: x, y1: pad, y2: H - 16 }));
      g.appendChild(svg('text', { x: Math.min(Math.max(x, 12), W - 14), y: H - 3, 'text-anchor': 'middle', style: 'font-size:10px' }, opts.tick ? opts.tick((max * k) / 4) : fmt.num((max * k) / 4, 1)));
    }
    rows.forEach((r, i) => {
      const y = pad + i * rowH;
      g.appendChild(svg('text', { x: 0, y: y + 13, style: 'font-weight:700' }, r.label));
      g.appendChild(svg('text', { x: W, y: y + 13, 'text-anchor': 'end' }, r.text));
      g.appendChild(svg('rect', { class: 'bar' + (r.ours ? ' ours' : ''), x: 0, y: y + 19, width: sx(r.value || 0), height: 14 }));
      if (r.err) {
        const x1 = sx((r.value || 0) - r.err), x2 = sx((r.value || 0) + r.err), cy = y + 26;
        g.appendChild(svg('line', { class: 'err', x1, x2, y1: cy, y2: cy }));
        g.appendChild(svg('line', { class: 'err', x1, x2: x1, y1: cy - 5, y2: cy + 5 }));
        g.appendChild(svg('line', { class: 'err', x1: x2, x2, y1: cy - 5, y2: cy + 5 }));
      }
    });
    return g;
  }

  function hbarChart(rows, opts) {
    opts = opts || {};
    if (window.innerWidth < 680) return hbarChartCompact(rows, opts);
    const labelW = opts.labelW || 128, valueW = opts.valueW || 170, barW = opts.barW || 260, rowH = 26, pad = 6;
    const W = labelW + barW + valueW, H = rows.length * rowH + pad * 2 + 18;
    const max = opts.max || Math.max(1e-9, ...rows.map((r) => (r.value || 0) + (r.err || 0)));
    const sx = (v) => labelW + (barW * Math.max(0, v)) / max;
    const g = svg('svg', { class: 'chart', viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', 'aria-label': opts.title || 'bar chart', preserveAspectRatio: 'xMinYMin meet' });
    g.appendChild(svg('title', null, opts.title || 'bar chart'));
    for (let k = 0; k <= 4; k++) {
      const x = labelW + (barW * k) / 4;
      g.appendChild(svg('line', { class: 'grid-line', x1: x, x2: x, y1: pad, y2: H - 18 }));
      g.appendChild(svg('text', { x, y: H - 4, 'text-anchor': 'middle', style: 'font-size:10px' }, opts.tick ? opts.tick((max * k) / 4) : fmt.num((max * k) / 4, 1)));
    }
    rows.forEach((r, i) => {
      const y = pad + i * rowH;
      g.appendChild(svg('text', { x: labelW - 8, y: y + rowH / 2 + 4, 'text-anchor': 'end' }, r.label.length > 20 ? r.label.slice(0, 19) + '…' : r.label));
      g.appendChild(svg('rect', { class: 'bar' + (r.ours ? ' ours' : ''), x: labelW, y: y + 4, width: Math.max(0, sx(r.value || 0) - labelW), height: rowH - 8 }));
      if (r.err) {
        const x1 = Math.max(labelW, sx((r.value || 0) - r.err)), x2 = Math.min(labelW + barW, sx((r.value || 0) + r.err)), cy = y + rowH / 2;
        g.appendChild(svg('line', { class: 'err', x1, x2, y1: cy, y2: cy }));
        g.appendChild(svg('line', { class: 'err', x1, x2: x1, y1: cy - 5, y2: cy + 5 }));
        g.appendChild(svg('line', { class: 'err', x1: x2, x2, y1: cy - 5, y2: cy + 5 }));
      }
      g.appendChild(svg('text', { x: labelW + barW + 12, y: y + rowH / 2 + 4 }, r.text));
    });
    g.appendChild(svg('line', { class: 'axis', x1: labelW, x2: labelW, y1: pad, y2: H - 18 }));
    return g;
  }

  function chartWrap(el) { return h('div', { class: 'chart-scroll' }, el); }

  function isOurs(strategy) { return /risk|donotmix|dnm/i.test(String(strategy || '')); }

  function groupBy(arr, keyFn) {
    const m = new Map();
    for (const x of arr) { const k = keyFn(x); if (!m.has(k)) m.set(k, []); m.get(k).push(x); }
    return m;
  }

  function dataTable(cols, rows, caption) {
    return h('div', { class: 'table-wrap' }, h('table', { class: 'data' },
      caption ? h('caption', { class: 'visually-hidden' }, caption) : null,
      h('thead', null, h('tr', null, cols.map((c) => h('th', { scope: 'col', class: c.num ? 'num' : null }, c.label)))),
      h('tbody', null, rows.map((r) => h('tr', null, cols.map((c) => h('td', { class: c.num ? 'num' : null }, c.get(r))))))));
  }

  function renderBenchmark() {
    const root = clear(document.getElementById('tab-benchmark'));
    root.appendChild(sectionTitle('h-benchmark', 'Benchmark', 'How strategies compare on synthetic suites with hidden ground-truth faults.'));
    const b = state.bench;
    if (!b || b.status === 'not-run' || b.status === 'error' || !b.schema) {
      root.appendChild(h('div', { class: 'empty-state' },
        h('div', { class: 'sym', 'aria-hidden': 'true' }, h('span', null, '?')),
        h('h3', null, b && b.status === 'error' ? 'Benchmark summary could not be loaded' : 'Benchmark not run yet'),
        h('p', null, 'Run ', h('code', null, 'donotmix benchmark'), ' to produce results. No numbers are shown until a real run exists.'),
        b && b.hint ? h('p', { class: 'small muted' }, 'Hint from server: ', h('code', null, b.hint)) : null));
      return;
    }
    root.appendChild(panel('Run', kv([
      ['Schema', h('code', null, b.schema)], ['Created', fmt.time(b.created_at)], ['Git commit', h('code', null, b.git_commit || '—')],
      ['Suite', b.suite || undefined], ['Command', h('code', null, b.command || '—')],
      ['Machine', b.machine && typeof b.machine === 'object' ? Object.entries(b.machine).map(([k, v]) => `${k} ${v}`).join(' · ') : (b.machine || '—')],
      ['Config', b.config ? h('code', { class: 'small' }, JSON.stringify(b.config)) : undefined],
      ['Wall time', b.seconds !== undefined ? `${fmt.num(b.seconds, 1)} s` : undefined],
    ]), { id: 'p-bench-run' }));

    // hypotheses first: the conclusions
    const hyp = b.hypotheses || [];
    if (hyp.length) {
      const res = (x) => {
        const v = String(x || '').toLowerCase();
        if (v === 'supported') return h('span', { class: 'res res-supported' }, '✓ supported');
        if (v === 'not-supported' || v === 'not supported') return h('span', { class: 'res res-not-supported' }, '✕ not supported');
        if (v === 'mixed') return h('span', { class: 'res res-mixed' }, '◐ mixed');
        return h('span', { class: 'res' }, '– ' + (x || 'unknown'));
      };
      root.appendChild(panel('Hypotheses', h('ul', { class: 'hyp-list' }, hyp.map((r) => {
        const ev = typeof r.evidence === 'string' ? r.evidence.split(/;\s+/) : (Array.isArray(r.evidence) ? r.evidence.map(String) : [JSON.stringify(r.evidence)]);
        return h('li', { class: 'hyp' },
          h('div', { class: 'row' }, h('code', { class: 'nowrap' }, r.id), res(r.result)),
          h('p', { class: 'hyp-statement' }, r.statement || ''),
          ev.filter(Boolean).length ? h('ul', { class: 'hyp-ev small mono' }, ev.filter(Boolean).map((x) => h('li', null, x))) : null);
      })), { id: 'p-hyp' }));
    }

    const fb = b.fixed_budget || [];
    if (fb.length) {
      const asRate = fb.every((r) => r.detected_mean === null || r.detected_mean === undefined || Number(r.detected_mean) <= 1);
      const detTxt = (r) => asRate ? `${fmt.pct(r.detected_mean)} ± ${fmt.pct(r.detected_std)}` : `${fmt.num(r.detected_mean)} ± ${fmt.num(r.detected_std)} / ${r.faults_total ?? '?'}`;
      const budgets = Array.from(new Set(fb.map((r) => String(r.budget_label ?? r.budget)))).sort();
      if (!state.ui.benchBudget || !budgets.includes(state.ui.benchBudget)) state.ui.benchBudget = budgets[budgets.length - 1];
      const chartsBox = h('div', { class: 'grid chart-grid' });
      const drawCharts = () => {
        clear(chartsBox);
        const rowsB = fb.filter((r) => String(r.budget_label ?? r.budget) === state.ui.benchBudget);
        const groups = groupBy(rowsB, (r) => `${r.n}|${r.colocate ?? ''}`);
        for (const [k, rows] of groups) {
          const [n, co] = k.split('|');
          const first = rows[0];
          const cap = `n = ${n} flags${co !== '' ? ` · colocate ${co}` : ''} · budget ${state.ui.benchBudget} ≈ ${fmt.num(first.budget, 1)} tests · ${first.faults_total ?? '?'} hidden faults/case`;
          chartsBox.appendChild(h('figure', { style: { margin: 0 } },
            h('figcaption', { class: 'label' }, cap),
            chartWrap(hbarChart(rows.map((r) => ({ label: String(r.strategy), value: r.detected_mean, err: r.detected_std, ours: isOurs(r.strategy), text: `${detTxt(r)} · APFD ${fmt.num(r.apfd_mean, 2)}` })),
              { max: asRate ? 1 : (Math.max(...rows.map((r) => r.faults_total || 0)) || undefined), tick: asRate ? (v) => Math.round(v * 100) + '%' : null, valueW: 230,
                title: `Faults detected, n=${n}, budget ${state.ui.benchBudget}` }))));
        }
      };
      drawCharts();
      const bsel = h('select', { class: 'input', id: 'bench-budget' }, budgets.map((x) => h('option', { value: x }, x)));
      bsel.value = state.ui.benchBudget;
      bsel.addEventListener('change', () => { state.ui.benchBudget = bsel.value; drawCharts(); });
      const orders = Array.from(new Set(fb.flatMap((r) => Object.keys(r.detection_by_order || {})))).sort((a, b2) => Number(a) - Number(b2));
      root.appendChild(panel('Fixed budget: faults found', h('div', { class: 'stack' },
        h('p', { class: 'small' }, `Share of hidden interaction faults detected with the same test budget (mean ± sd over runs). Orange bars (◆) are DoNotMix risk strategies; black bars are baselines. Values are printed on every bar, so colour is not needed to read the chart.`),
        h('div', { class: 'slice-controls' }, h('label', { for: 'bench-budget', class: 'label' }, 'Budget'), bsel,
          h('span', { class: 'small muted' }, 'B2 = size of a pairwise suite, B3 = size of a 3-way suite.')),
        chartsBox,
        h('details', null, h('summary', null, `Full table (${fb.length} rows)`), dataTable([
          { label: 'n', num: true, get: (r) => String(r.n) },
          { label: 'Colocate', num: true, get: (r) => r.colocate !== undefined ? String(r.colocate) : '—' },
          { label: 'Budget', get: (r) => `${r.budget_label ?? ''}${r.budget !== undefined ? ' (' + fmt.num(r.budget, 1) + ')' : ''}` },
          { label: 'Strategy', get: (r) => h('span', null, isOurs(r.strategy) ? '◆ ' : '', r.strategy) },
          { label: 'Detected', num: true, get: (r) => detTxt(r) },
          ...orders.map((o) => ({ label: `${o}-way`, num: true, get: (r) => { const v = (r.detection_by_order || {})[o]; return v === undefined ? '—' : (asRate ? fmt.pct(v) : fmt.num(v)); } })),
          { label: '1st failure', num: true, get: (r) => fmt.num(r.first_failure_mean, 1) },
          { label: 'APFD', num: true, get: (r) => fmt.num(r.apfd_mean, 3) },
          { label: 'Tests', num: true, get: (r) => fmt.num(r.tests_used_mean, 1) },
          { label: 'Seconds', num: true, get: (r) => fmt.num(r.seconds_mean, 3) },
          { label: 'Runs', num: true, get: (r) => fmt.int(r.runs) },
        ], fb, 'Fixed budget results'))), { id: 'p-fb' }));
    }

    const cc = b.coverage_cost || [];
    if (cc.length) {
      const groups = groupBy(cc, (r) => String(r.n));
      const charts = [];
      for (const [n, rows] of groups) {
        const exh = rows.filter((r) => Number(r.t) === 0 || /exhaustive/i.test(r.strategy));
        const rest = rows.filter((r) => !exh.includes(r)).sort((a, c) => Number(a.t) - Number(c.t) || String(a.strategy).localeCompare(String(c.strategy)));
        charts.push(h('figure', { style: { margin: 0 } },
          h('figcaption', { class: 'label' }, `n = ${n} · tests to reach full t-way coverage (fewer is better)`),
          rest.length ? chartWrap(hbarChart(rest.map((r) => ({
            label: /\d/.test(String(r.strategy)) ? String(r.strategy) : `${r.strategy} (t=${r.t})`,
            value: r.tests_mean, err: r.tests_std, ours: !/random/i.test(r.strategy),
            text: `${fmt.num(r.tests_mean, 1)} ± ${fmt.num(r.tests_std, 1)}${r.all_reached === false ? ' (not all reached)' : ''}` })), { title: `Tests for t-way coverage at n=${n}`, valueW: 200 })) : null,
          exh.length ? h('p', { class: 'small mono' }, `Exhaustive: ${exh.map((r) => fmt.int(Math.round(r.tests_mean)) + ' feasible configs').join(', ')}`) : h('p', { class: 'small mono muted' }, 'Exhaustive: not run at this size.')));
      }
      root.appendChild(panel('Coverage cost', h('div', { class: 'stack' },
        h('p', { class: 'small' }, 'Orange bars: greedy covering arrays (what DoNotMix builds). Black bars: random sampling until the same coverage is reached. Every bar is labelled. Exhaustive testing is listed below each chart for scale.'),
        h('div', { class: 'grid chart-grid' }, charts)), { id: 'p-cc' }));
    }

    const mn = b.minimization || [];
    if (mn.length) {
      const pctCell = (v, bad) => h('span', null, h('span', { class: 'bar-inline', 'aria-hidden': 'true', style: { width: Math.round(40 * (v || 0)) + 'px', background: bad ? 'var(--red)' : 'var(--black)' } }), fmt.pct(v));
      root.appendChild(panel('Minimization accuracy', h('div', { class: 'stack' },
        h('p', { class: 'small' }, 'Exact = the reported core equals the hidden ground-truth core. Over/under-specified = extra or missing literals. False positive = a reported core that is not a real fault.'),
        dataTable([
          { label: 'n', num: true, get: (r) => String(r.n) },
          { label: 'Colocate', num: true, get: (r) => r.colocate !== undefined ? String(r.colocate) : '—' },
          { label: 'Exact', num: true, get: (r) => pctCell(r.exact_rate) },
          { label: 'Over-spec.', num: true, get: (r) => pctCell(r.overspecified_rate, true) },
          { label: 'Under-spec.', num: true, get: (r) => pctCell(r.underspecified_rate, true) },
          { label: 'False pos.', num: true, get: (r) => pctCell(r.false_positive_rate, true) },
          { label: 'Extra tests', num: true, get: (r) => fmt.num(r.extra_tests_mean, 1) },
          { label: 'Core / true size', num: true, get: (r) => `${fmt.num(r.core_size_mean, 2)} / ${fmt.num(r.true_size_mean, 2)}` },
          { label: 'Raw diff', num: true, get: (r) => fmt.num(r.raw_counterexample_diff_mean, 1) },
          { label: 'Matched', num: true, get: (r) => r.faults_matched !== undefined ? `${r.faults_matched}/${r.faults_total ?? '?'}` : '—' },
          { label: 'Count', num: true, get: (r) => fmt.int(r.count) },
        ], mn, 'Minimization accuracy')), { id: 'p-min' }));
    }

    if (b.mixmart && typeof b.mixmart === 'object' && Object.keys(b.mixmart).length) {
      root.appendChild(panel('MixMart demo', kv(Object.entries(b.mixmart).map(([k, v]) => [k, typeof v === 'object' ? h('code', null, JSON.stringify(v)) : String(v)])), { id: 'p-mixmart' }));
    }
  }

  // ---------------------------------------------------------------- audit
  const AUDIT_SKIP = new Set(['ts', 'event', 'actor', 'proposal', 'prev', 'hash']);

  function evTag(e) {
    const cls = /refused|declined|revoked/.test(e) ? 'ev-refused' : ('ev-' + e);
    const sym = /refused|declined|revoked/.test(e) ? '✕ ' : /approved|applied/.test(e) ? '✓ ' : '';
    return h('span', { class: 'ev ' + cls }, sym + e);
  }

  function auditTable(entries, compact) {
    const rows = entries.slice().reverse();
    return dataTable([
      { label: 'Time', get: (e) => h('time', { datetime: e.ts || '' }, fmt.time(e.ts)) },
      { label: 'Event', get: (e) => evTag(e.event) },
      { label: 'Actor', get: (e) => e.actor || '—' },
      ...(compact ? [] : [{ label: 'Proposal', get: (e) => e.proposal ? h('code', { class: 'small' }, e.proposal) : '—' }]),
      { label: 'Details', get: (e) => {
        const parts = Object.entries(e).filter(([k]) => !AUDIT_SKIP.has(k)).map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`);
        const s = parts.join('  ');
        return h('span', { class: 'small mono', style: { overflowWrap: 'anywhere' } }, s.length > 400 ? s.slice(0, 400) + '…' : s);
      } },
      { label: 'Hash ← prev', get: (e) => h('span', { class: 'hash nowrap', title: `hash ${e.hash}\nprev ${e.prev}` }, `${fmt.short(e.hash, 8)} ← ${fmt.short(e.prev, 8)}`) },
    ], rows, 'Audit log entries, newest first');
  }

  function renderAudit() {
    const root = clear(document.getElementById('tab-audit'));
    root.appendChild(sectionTitle('h-audit', 'Audit log', 'Every proposal, approval, refusal, apply and re-verification — hash-chained.'));
    const a = state.audit;
    if (!a || a.error) { root.appendChild(h('div', { class: 'alert alert-error', role: 'alert' }, 'Could not load the audit log: ' + ((a && a.error) || 'unknown error'))); return; }
    root.appendChild(h('div', { class: 'integrity ' + (a.intact ? 'ok' : 'bad'), role: 'status' },
      h('span', { class: 'sym', 'aria-hidden': 'true' }, a.intact ? '✓' : '✕'),
      h('div', null, h('div', { class: 'big' }, a.intact ? 'Chain intact' : 'Chain broken'), h('div', { class: 'mono small' }, a.detail || ''))));
    const entries = a.entries || [];
    if (!entries.length) {
      root.appendChild(h('div', { class: 'empty-state' }, h('h3', null, 'No audit entries yet'), h('p', { class: 'muted' }, 'Entries appear when proposals are created, approved, applied or re-verified.')));
      return;
    }
    const events = Array.from(new Set(entries.map((e) => e.event))).sort();
    const sel = h('select', { class: 'input', id: 'audit-filter' }, h('option', { value: '' }, `all events (${entries.length})`), events.map((e) => h('option', { value: e }, e)));
    const holder = h('div');
    const draw = () => { clear(holder); holder.appendChild(auditTable(sel.value ? entries.filter((e) => e.event === sel.value) : entries)); };
    sel.addEventListener('change', draw);
    draw();
    root.appendChild(h('div', { class: 'row' }, h('label', { for: 'audit-filter', class: 'label' }, 'Filter'), sel,
      h('span', { class: 'small muted' }, entries.length >= 200 ? 'Showing the latest 200 entries.' : `${entries.length} entries.`)));
    root.appendChild(holder);
  }

  // ---------------------------------------------------------------- routing
  const TABS = ['overview', 'matrix', 'hazards', 'benchmark', 'audit'];
  const RENDER = { overview: renderOverview, matrix: renderMatrix, hazards: renderHazards, benchmark: renderBenchmark, audit: renderAudit };

  function parseHash() {
    const raw = decodeURIComponent((location.hash || '').replace(/^#/, ''));
    if (raw.startsWith('proposal-')) return { tab: 'hazards', proposal: raw };
    const [tab, sub] = raw.split('/');
    return { tab: TABS.includes(tab) ? tab : 'overview', sub };
  }

  function route(focus) {
    const r = parseHash();
    const changed = state.tab !== r.tab;
    state.tab = r.tab;
    if (r.tab === 'hazards' && r.sub && derived && derived.byId[r.sub]) state.hazardId = r.sub;
    for (const t of TABS) {
      document.getElementById('tab-' + t).hidden = t !== r.tab;
      const a = document.querySelector(`.tabs a[data-tab="${t}"]`);
      if (t === r.tab) {
        a.setAttribute('aria-current', 'page');
        const ul = a.closest('ul');
        const li = a.parentElement;
        if (ul && li && (li.offsetLeft < ul.scrollLeft || li.offsetLeft + li.offsetWidth > ul.scrollLeft + ul.clientWidth)) ul.scrollLeft = Math.max(0, li.offsetLeft - 8);
      } else a.removeAttribute('aria-current');
    }
    RENDER[r.tab]();
    if (r.proposal) {
      const el = document.getElementById(r.proposal);
      if (el) { el.scrollIntoView({ block: 'start' }); el.setAttribute('tabindex', '-1'); el.focus({ preventScroll: true }); }
    } else if (r.tab === 'hazards' && r.sub && focus) {
      const t = document.getElementById('hz-title');
      if (t) t.focus({ preventScroll: false });
    } else if (changed && focus) {
      const hd = document.getElementById('h-' + r.tab);
      if (hd) { hd.setAttribute('tabindex', '-1'); hd.focus({ preventScroll: true }); }
      window.scrollTo(0, 0);
    }
  }

  function renderAll() { renderHeader(); route(false); }

  async function selectRun(id) {
    state.runId = id || 'latest';
    await loadReport();
    renderAll();
    announce('Loaded run ' + state.runId);
  }

  // ---------------------------------------------------------------- boot
  async function boot() {
    window.addEventListener('hashchange', () => route(true));
    let narrow = window.innerWidth < 680;
    window.addEventListener('resize', () => {
      const n = window.innerWidth < 680;
      if (n !== narrow) { narrow = n; if (state.tab === 'benchmark') renderBenchmark(); }
    });
    document.getElementById('run-select').addEventListener('change', (e) => selectRun(e.target.value));
    await Promise.all([loadSession(), loadRuns(), loadReport(), loadProposals(), loadAudit(), loadBench()]);
    const ge = document.getElementById('global-error');
    if (!state.session) { ge.hidden = false; ge.textContent = 'Could not reach the DoNotMix API. Is `donotmix dashboard` still running?'; }
    renderAll();
    try {
      const s = await apiGet('/api/scan');
      if (s.ok && s.body) { state.scan = s.body; if (state.scan.state === 'running') { refreshScanViews(); pollScan(); } else if (state.tab === 'overview') refreshScanViews(); }
    } catch (_) { /* ignore */ }
  }

  boot();
})();
