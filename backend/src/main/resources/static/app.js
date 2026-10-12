/* Enterprise Analytics Platform: dashboard client (vanilla JS + Chart.js) */
(() => {
  'use strict';

  // ------------------------------------------------------------------ state
  const state = {
    token: null,
    user: null,          // { username, displayName, role }
    region: '',          // '' = all
    tab: 'overview',
    charts: {},
    cache: new Map(),    // api path -> response JSON
  };
  const REGION_AWARE = new Set(['overview', 'sales', 'customers']);
  // Which dashboard tabs each role may open (the server enforces the same rules on every API call)
  const ROLE_TABS = {
    ADMIN: ['overview', 'sales', 'customers', 'support', 'ai', 'insights', 'data', 'admin'],
    ANALYST: ['overview', 'sales', 'customers', 'support', 'ai', 'insights', 'data'],
    MANAGER: ['overview', 'sales', 'customers', 'support', 'ai', 'insights'],
    EMPLOYEE: ['support'],
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  // ------------------------------------------------------------------ formatting
  const inr = (v) => {
    if (v == null) return '–';
    const n = Number(v);
    if (Math.abs(n) >= 1e7) return '₹' + (n / 1e7).toFixed(2) + ' Cr';
    if (Math.abs(n) >= 1e5) return '₹' + (n / 1e5).toFixed(1) + ' L';
    return '₹' + Math.round(n).toLocaleString('en-IN');
  };
  // compact axis labels: ₹20 Cr, ₹2.5 Cr, ₹50 L
  const inrAxis = (v) => {
    const n = Number(v);
    if (n === 0) return '₹0';
    if (Math.abs(n) >= 1e7) return '₹' + +(n / 1e7).toFixed(1) + ' Cr';
    if (Math.abs(n) >= 1e5) return '₹' + +(n / 1e5).toFixed(1) + ' L';
    return '₹' + n.toLocaleString('en-IN');
  };
  const num = (v) => (v == null ? '–' : Number(v).toLocaleString('en-IN'));
  const pct = (v, digits = 1) => (v == null ? '–' : Number(v).toFixed(digits) + '%');
  const monthLabel = (iso) => {
    const d = new Date(iso + 'T00:00:00');
    return d.toLocaleDateString('en-IN', { month: 'short', year: '2-digit' });
  };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  function delta(v, label = 'vs prior 12 months') {
    if (v == null) return `<span>${label}</span>`;
    const n = Number(v);
    const cls = n > 0 ? 'up' : n < 0 ? 'down' : '';
    const arrow = n > 0 ? '▲' : n < 0 ? '▼' : '•';
    return `<span class="${cls}">${arrow} ${Math.abs(n).toFixed(1)}%</span> ${label}`;
  }

  function toast(msg, ms = 4000) {
    const t = $('#toast');
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => (t.hidden = true), ms);
  }

  // ------------------------------------------------------------------ API
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function api(path, { method = 'GET', body, useCache = true, retries = 3, form, raw = false } = {}) {
    if (method === 'GET' && useCache && state.cache.has(path)) return state.cache.get(path);
    for (let attempt = 0; attempt <= retries; attempt++) {
      const headers = state.token ? { Authorization: 'Bearer ' + state.token } : {};
      if (!form) headers['Content-Type'] = 'application/json';
      const res = await fetch('api/' + path, {
        method,
        headers,
        body: form || (body ? JSON.stringify(body) : undefined),
      });
      if (res.status === 401 && state.token) { logout('Your session expired. Please sign in again.'); throw new Error('unauthorized'); }
      if (res.status === 503 && attempt < retries) { await sleep(2500 * (attempt + 1)); continue; } // DB waking up
      if (!res.ok) {
        let detail = res.statusText;
        try { const j = await res.json(); detail = j.detail || j.message || detail; } catch { /* not JSON */ }
        const err = new Error(detail); err.status = res.status; throw err;
      }
      if (raw) return res;
      const data = await res.json();
      if (method === 'GET' && useCache) state.cache.set(path, data);
      return data;
    }
    throw new Error('Service unavailable');
  }
  const withRegion = (path) => (state.region ? `${path}${path.includes('?') ? '&' : '?'}region=${state.region}` : path);

  // ------------------------------------------------------------------ server wake-up
  async function checkServer() {
    const box = $('#server-status');
    const text = $('.text', box);
    const started = Date.now();
    const slowTimer = setTimeout(() => {
      box.className = 'server-status waking';
      text.textContent = 'Waking up the free-tier server. This can take up to a minute…';
    }, 2000);
    for (;;) {
      try {
        const res = await fetch('api/health', { cache: 'no-store' });
        if (res.ok) break;
      } catch { /* still booting */ }
      if (Date.now() - started > 120000) {
        clearTimeout(slowTimer);
        box.className = 'server-status down';
        text.textContent = 'Server is not responding. Please refresh in a minute.';
        return;
      }
      await sleep(3000);
    }
    clearTimeout(slowTimer);
    box.className = 'server-status ok';
    text.textContent = 'Server is online';
  }

  // ------------------------------------------------------------------ auth
  function saveSession(data) {
    state.token = data.token;
    state.user = { username: data.username, displayName: data.displayName, role: data.role, demo: data.demo !== false };
    try { sessionStorage.setItem('eap.session', JSON.stringify({ token: data.token, user: state.user, exp: Date.now() + data.expiresInSeconds * 1000 })); } catch { /* storage blocked */ }
  }

  function restoreSession() {
    try {
      const s = JSON.parse(sessionStorage.getItem('eap.session') || 'null');
      if (s && s.exp > Date.now()) { state.token = s.token; state.user = s.user; return true; }
    } catch { /* ignore */ }
    return false;
  }

  async function login(username, password) {
    const err = $('#login-error');
    err.hidden = true;
    $$('.demo-btn, #login-form button').forEach((b) => (b.disabled = true));
    try {
      const data = await api('auth/login', { method: 'POST', body: { username, password } });
      saveSession(data);
      showApp();
    } catch (e) {
      err.textContent = e.status === 401 ? 'Invalid username or password.' : 'Could not sign in: ' + e.message;
      err.hidden = false;
    } finally {
      $$('.demo-btn, #login-form button').forEach((b) => (b.disabled = false));
    }
  }

  function logout(message) {
    state.token = null; state.user = null; state.cache.clear();
    try { sessionStorage.removeItem('eap.session'); } catch { /* ignore */ }
    Object.values(state.charts).forEach((c) => c.destroy());
    state.charts = {};
    $('#app-view').hidden = true;
    $('#login-view').hidden = false;
    if (message) { const e = $('#login-error'); e.textContent = message; e.hidden = false; }
    checkServer();
  }

  // ------------------------------------------------------------------ shell
  function showApp() {
    $('#login-view').hidden = true;
    $('#app-view').hidden = false;
    $('#user-name').textContent = state.user.displayName;
    $('#user-role').textContent = state.user.role;
    $('#user-owner').hidden = state.user.demo !== false;
    const allowed = ROLE_TABS[state.user.role] || ['support'];
    $$('.tabs [role=tab]').forEach((b) => (b.hidden = !allowed.includes(b.dataset.tab)));
    decorateSqlButtons();
    selectTab(allowed[0]);
    // Wake the free-tier data & AI service in the background so it is ready when needed
    if (allowed.includes('ai')) api('ai/status', { useCache: false, retries: 0 }).catch(() => {});
  }

  function selectTab(tab) {
    state.tab = tab;
    $$('.tabs [role=tab]').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
    $$('[data-panel]').forEach((p) => (p.hidden = p.dataset.panel !== tab));
    $('#region-filter').style.visibility = REGION_AWARE.has(tab) ? 'visible' : 'hidden';
    loadTab(tab);
  }

  async function loadTab(tab) {
    try {
      if (tab === 'overview') await loadOverview();
      else if (tab === 'sales') await loadSales();
      else if (tab === 'customers') await loadCustomers();
      else if (tab === 'support') await loadSupport();
      else if (tab === 'ai') await loadAi();
      else if (tab === 'insights') await loadInsights();
      else if (tab === 'data') await loadData();
      else if (tab === 'admin') await loadAdmin();
    } catch (e) {
      if (e.message !== 'unauthorized') toast('Could not load data: ' + e.message);
    }
  }

  function decorateSqlButtons() {
    const canSeeSql = ['ANALYST', 'ADMIN'].includes(state.user.role);
    $$('.card[data-sql]').forEach((card) => {
      $('.sql-btn', card)?.remove();
      const btn = document.createElement('button');
      btn.className = 'sql-btn' + (canSeeSql ? '' : ' locked');
      btn.textContent = canSeeSql ? 'SQL' : '🔒 SQL';
      btn.title = canSeeSql ? 'View the SQL behind this card' : 'Sign in as Analyst or Admin to view the SQL';
      btn.addEventListener('click', () => (canSeeSql ? showSql(card.dataset.sql) : toast('The SQL view needs the Analyst or Admin role (role-based access control).')));
      card.appendChild(btn);
    });
  }

  async function showSql(name) {
    try {
      const data = await api('analytics/sql/' + name);
      $('#sql-title').textContent = name + '.sql';
      $('#sql-code').textContent = data.sql;
      $('#sql-dialog').showModal();
    } catch (e) { toast('Could not load SQL: ' + e.message); }
  }

  // ------------------------------------------------------------------ chart helpers
  function baseOptions(extra = {}) {
    const grid = cssVar('--grid'), ink = cssVar('--text-2');
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 250 },
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false, labels: { color: ink, usePointStyle: true, pointStyle: 'rectRounded', boxWidth: 10, boxHeight: 10 } },
        tooltip: {
          backgroundColor: cssVar('--surface'), titleColor: cssVar('--text'), bodyColor: ink,
          borderColor: cssVar('--border'), borderWidth: 1, padding: 10, boxPadding: 4, usePointStyle: true,
        },
      },
      scales: {
        x: { grid: { display: false }, border: { color: grid }, ticks: { color: ink, maxRotation: 0, autoSkipPadding: 12 } },
        y: { grid: { color: grid }, border: { display: false }, ticks: { color: ink }, beginAtZero: true },
      },
      ...extra,
    };
  }

  function drawChart(id, config) {
    state.charts[id]?.destroy();
    state.charts[id] = new Chart(document.getElementById(id), config);
  }

  // ------------------------------------------------------------------ OVERVIEW
  async function loadOverview() {
    const [summary, trend, regions] = await Promise.all([
      api(withRegion('dashboard/summary')),
      api(withRegion('analytics/revenue-trend')),
      api('analytics/regions'),
    ]);
    const s = summary.sales;
    $('#as-of').textContent = s.as_of ? 'Data through ' + new Date(s.as_of + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) + ' · trailing 12 months' : '';
    $('#kpis').innerHTML = [
      kpi('Revenue', inr(s.revenue), delta(s.revenue_growth_pct)),
      kpi('Orders', num(s.orders), delta(s.orders_growth_pct)),
      kpi('Active customers', num(s.active_customers), delta(s.customers_growth_pct)),
      kpi('Avg order value', inr(s.avg_order_value), delta(s.aov_growth_pct)),
      kpi('Return rate', pct(s.return_rate_pct, 2), '<span>of all orders</span>'),
    ].join('');

    const labels = trend.map((r) => monthLabel(r.month));
    drawChart('chart-revenue', {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: 'Revenue', data: trend.map((r) => r.revenue), borderColor: cssVar('--series-1'), backgroundColor: cssVar('--series-1'), borderWidth: 2, pointRadius: 0, pointHoverRadius: 5, tension: 0.25 },
          { label: '3-month average', data: trend.map((r) => r.moving_avg_3m), borderColor: cssVar('--series-2'), backgroundColor: cssVar('--series-2'), borderWidth: 2, borderDash: [5, 4], pointRadius: 0, pointHoverRadius: 5, tension: 0.25 },
        ],
      },
      options: baseOptions({
        plugins: {
          ...baseOptions().plugins,
          legend: { ...baseOptions().plugins.legend, display: true, position: 'top', align: 'end' },
          tooltip: {
            ...baseOptions().plugins.tooltip,
            callbacks: {
              label: (c) => ` ${c.dataset.label}: ${inr(c.parsed.y)}`,
              afterBody: (items) => {
                const r = trend[items[0].dataIndex];
                const lines = [`Orders: ${num(r.orders)}`];
                if (r.mom_growth_pct != null) lines.push(`MoM: ${r.mom_growth_pct > 0 ? '+' : ''}${r.mom_growth_pct}%`);
                if (r.yoy_growth_pct != null) lines.push(`YoY: ${r.yoy_growth_pct > 0 ? '+' : ''}${r.yoy_growth_pct}%`);
                return lines;
              },
            },
          },
        },
        scales: { ...baseOptions().scales, y: { ...baseOptions().scales.y, ticks: { color: cssVar('--text-2'), callback: (v) => inrAxis(v) } } },
      }),
    });

    const max = Math.max(...regions.map((r) => Number(r.revenue)));
    $('#region-list').innerHTML = regions.map((r) => `
      <div class="region-row ${state.region && state.region !== r.region ? 'dim' : ''}">
        <div class="top"><span class="name">${esc(r.region)}</span><span>${inr(r.revenue)} · ${pct(r.share_pct)}</span></div>
        <div class="bar-track"><div class="bar-fill" style="width:${(100 * r.revenue / max).toFixed(1)}%"></div></div>
        <div class="sub">Top city ${esc(r.top_city)} · YoY ${r.yoy_growth_pct > 0 ? '+' : ''}${pct(r.yoy_growth_pct)}</div>
      </div>`).join('');

    const t = summary.support;
    $('#support-mini').innerHTML = `
      <div><div class="v">${num(t.open_tickets)}</div><div class="l">Open tickets</div></div>
      <div><div class="v">${num(t.open_overdue)}</div><div class="l">Open &amp; past SLA</div></div>
      <div><div class="v">${t.avg_resolution_hours_30d ?? '–'} h</div><div class="l">Avg resolution (30d)</div></div>
      <div><div class="v">${pct(t.sla_breach_pct_30d)}</div><div class="l">SLA breached (30d)</div></div>`;
  }

  const kpi = (label, value, deltaHtml) =>
    `<div class="kpi"><div class="label">${label}</div><div class="value">${value}</div><div class="delta">${deltaHtml}</div></div>`;

  // ------------------------------------------------------------------ SALES
  async function loadSales() {
    const [cats, products, reps] = await Promise.all([
      api(withRegion('analytics/categories')),
      api(withRegion('analytics/top-products')),
      api('analytics/sales-reps'),
    ]);

    drawChart('chart-categories', {
      type: 'bar',
      data: {
        labels: cats.map((c) => c.category),
        datasets: [{ label: 'Revenue', data: cats.map((c) => c.revenue), backgroundColor: cssVar('--series-1'), borderRadius: 4, borderSkipped: 'start', barPercentage: 0.7, categoryPercentage: 0.9 }],
      },
      options: baseOptions({
        indexAxis: 'y',
        interaction: { mode: 'nearest', axis: 'y', intersect: false },
        plugins: { ...baseOptions().plugins, tooltip: { ...baseOptions().plugins.tooltip, callbacks: { label: (c) => ` ${inr(c.parsed.x)} · ${pct(cats[c.dataIndex].share_pct)} of revenue` } } },
        scales: {
          x: { grid: { color: cssVar('--grid') }, border: { display: false }, ticks: { color: cssVar('--text-2'), maxRotation: 0, autoSkipPadding: 10, callback: (v) => inrAxis(v) }, beginAtZero: true },
          y: { grid: { display: false }, border: { color: cssVar('--grid') }, ticks: { color: cssVar('--text-2') } },
        },
      }),
    });

    $('#table-categories').innerHTML = table(
      ['Category', ['Revenue', 'num'], ['Share', 'num'], ['Margin', 'num'], ['Units', 'num']],
      cats.map((c) => [esc(c.category), [inr(c.revenue), 'num'], [pct(c.share_pct), 'num'], [pct(c.margin_pct), 'num'], [num(c.units), 'num']]));

    $('#table-products').innerHTML = table(
      [['#', 'rank'], 'Product', 'SKU', 'Category', ['Cat. rank', 'num'], ['Units', 'num'], ['Revenue', 'num'], ['Margin', 'num']],
      products.map((p) => [[p.overall_rank, 'rank'], esc(p.name), `<code>${esc(p.sku)}</code>`, `<span class="tag">${esc(p.category)}</span>`,
        ['#' + p.category_rank, 'num'], [num(p.units), 'num'], [inr(p.revenue), 'num'], [pct(p.margin_pct), 'num']]));

    $('#table-reps').innerHTML = table(
      ['Region', ['#', 'rank'], 'Sales rep', ['Orders', 'num'], ['Revenue', 'num'], ['Share of region', 'num']],
      reps.map((r) => [r.region_rank === 1 ? `<strong>${esc(r.region)}</strong>` : '', [r.region_rank, 'rank'], esc(r.full_name),
        [num(r.orders), 'num'], [inr(r.revenue), 'num'], [pct(r.region_share_pct), 'num']]));
  }

  // ------------------------------------------------------------------ CUSTOMERS
  async function loadCustomers() {
    const [growth, cohorts, segments, customers, rfm] = await Promise.all([
      api(withRegion('analytics/customer-growth')),
      api(withRegion('analytics/cohorts')),
      api(withRegion('analytics/segments')),
      api(withRegion('analytics/top-customers')),
      api(withRegion('analytics/rfm-segments')),
    ]);
    renderRfm(rfm);

    const surface = cssVar('--surface');
    drawChart('chart-growth', {
      type: 'bar',
      data: {
        labels: growth.map((g) => monthLabel(g.month)),
        datasets: [
          { label: 'New', data: growth.map((g) => g.new_customers), backgroundColor: cssVar('--series-1'), borderColor: surface, borderWidth: { top: 2 }, borderSkipped: false, borderRadius: 0, stack: 's' },
          { label: 'Returning', data: growth.map((g) => g.returning_customers), backgroundColor: cssVar('--series-2'), borderColor: surface, borderWidth: { bottom: 0 }, borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: false, stack: 's' },
        ],
      },
      options: baseOptions({
        plugins: {
          ...baseOptions().plugins,
          legend: { ...baseOptions().plugins.legend, display: true, position: 'top', align: 'end' },
          tooltip: { ...baseOptions().plugins.tooltip, callbacks: { afterBody: (items) => `Returning share: ${pct(growth[items[0].dataIndex].returning_pct)}` } },
        },
        scales: { x: { ...baseOptions().scales.x, stacked: true }, y: { ...baseOptions().scales.y, stacked: true } },
      }),
    });

    renderCohorts(cohorts);

    $('#table-segments').innerHTML = table(
      ['Segment', ['Customers', 'num'], ['Revenue', 'num'], ['Share', 'num'], ['Avg LTV', 'num']],
      segments.map((s) => [esc(s.segment), [num(s.customers), 'num'], [inr(s.revenue), 'num'], [pct(s.revenue_share_pct), 'num'], [inr(s.avg_lifetime_value), 'num']]));

    $('#table-customers').innerHTML = table(
      [['#', 'rank'], 'Customer', 'Segment', ['Orders', 'num'], ['LTV', 'num'], ['Last order', 'num']],
      customers.map((c) => [[c.ltv_rank, 'rank'], `${esc(c.full_name)}<div class="muted small">${esc(c.city)}</div>`, `<span class="tag">${esc(c.segment)}</span>`,
        [num(c.orders), 'num'], [inr(c.lifetime_value), 'num'], [c.days_since_last_order === 0 ? 'today' : c.days_since_last_order + 'd ago', 'num']]));
  }

  function renderCohorts(rows) {
    const byCohort = new Map();
    rows.forEach((r) => {
      if (!byCohort.has(r.cohort_month)) byCohort.set(r.cohort_month, { size: r.cohort_size, cells: {} });
      byCohort.get(r.cohort_month).cells[r.month_offset] = r.retention_pct;
    });
    const values = rows.filter((r) => r.month_offset > 0).map((r) => Number(r.retention_pct));
    const lo = Math.min(...values), hi = Math.max(...values);
    const step = (v) => Math.min(5, Math.floor(((v - lo) / Math.max(hi - lo, 1)) * 6));
    let html = '<thead><tr><th>Cohort</th><th class="num">Customers</th>';
    for (let m = 1; m <= 11; m++) html += `<th>M${m}</th>`;
    html += '</tr></thead><tbody>';
    for (const [month, c] of byCohort) {
      html += `<tr><td>${monthLabel(month)}</td><td class="num">${num(c.size)}</td>`;
      for (let m = 1; m <= 11; m++) {
        const v = c.cells[m];
        if (v == null) { html += '<td></td>'; continue; }
        const k = step(Number(v));
        const ink = k >= 3 ? '#fff' : cssVar('--text');
        html += `<td class="cell" style="background:var(--heat-${k});color:${ink}" title="${monthLabel(month)} cohort, month ${m}: ${v}% active">${Math.round(v)}%</td>`;
      }
      html += '</tr>';
    }
    $('#table-cohorts').innerHTML = html + '</tbody>';
  }

  // ------------------------------------------------------------------ SUPPORT
  async function loadSupport() {
    const [summary, sla, trend, agents] = await Promise.all([
      api('analytics/support/summary'),
      api('analytics/support/sla'),
      api('analytics/support/trend'),
      api('analytics/support/agents'),
    ]);
    const t = summary;
    $('#support-kpis').innerHTML = [
      kpi('Open tickets', num(t.open_tickets), `<span>${num(t.open_high_priority)} high / critical</span>`),
      kpi('Open & past SLA', num(t.open_overdue), '<span>need escalation</span>'),
      kpi('Opened (30d)', num(t.opened_30d), '<span>last 30 days</span>'),
      kpi('Avg resolution (30d)', (t.avg_resolution_hours_30d ?? '–') + ' h', '<span>opened in last 30 days</span>'),
      kpi('SLA breached (30d)', pct(t.sla_breach_pct_30d), '<span>of resolved tickets</span>'),
    ].join('');

    drawChart('chart-sla', {
      type: 'bar',
      data: { labels: sla.map((s) => s.category), datasets: [{ label: 'SLA breach %', data: sla.map((s) => s.sla_breach_pct), backgroundColor: cssVar('--series-1'), borderRadius: 4, borderSkipped: 'start', barPercentage: 0.7 }] },
      options: baseOptions({
        indexAxis: 'y',
        interaction: { mode: 'nearest', axis: 'y', intersect: false },
        plugins: { ...baseOptions().plugins, tooltip: { ...baseOptions().plugins.tooltip, callbacks: { label: (c) => ` ${pct(c.parsed.x)} breached · ${num(sla[c.dataIndex].tickets)} tickets` } } },
        scales: {
          x: { grid: { color: cssVar('--grid') }, border: { display: false }, ticks: { color: cssVar('--text-2'), callback: (v) => v + '%' }, beginAtZero: true },
          y: { grid: { display: false }, border: { color: cssVar('--grid') }, ticks: { color: cssVar('--text-2') } },
        },
      }),
    });

    $('#table-sla').innerHTML = table(
      ['Category', ['Tickets', 'num'], ['Avg (h)', 'num'], ['P90 (h)', 'num'], ['Breached', 'num']],
      sla.map((s) => [esc(s.category), [num(s.tickets), 'num'], [s.avg_resolution_hours, 'num'], [s.p90_resolution_hours, 'num'], [pct(s.sla_breach_pct), 'num']]));

    drawChart('chart-tickets', {
      type: 'line',
      data: { labels: trend.map((r) => monthLabel(r.month)), datasets: [{ label: 'Tickets opened', data: trend.map((r) => r.opened), borderColor: cssVar('--series-1'), backgroundColor: cssVar('--series-1'), borderWidth: 2, pointRadius: 0, pointHoverRadius: 5, tension: 0.25 }] },
      options: baseOptions({
        plugins: { ...baseOptions().plugins, tooltip: { ...baseOptions().plugins.tooltip, callbacks: { afterBody: (items) => { const r = trend[items[0].dataIndex]; return r.sla_breach_pct != null ? `SLA breached: ${pct(r.sla_breach_pct)}` : ''; } } } },
      }),
    });

    $('#table-agents').innerHTML = table(
      [['#', 'rank'], 'Agent', ['Handled', 'num'], ['Open now', 'num'], ['Avg resolution (h)', 'num'], ['SLA met', 'num']],
      agents.map((a) => [[a.rank, 'rank'], esc(a.full_name), [a.handled, 'num'], [a.open_now, 'num'], [a.avg_resolution_hours, 'num'], [pct(a.sla_met_pct), 'num']]));
  }

  // ------------------------------------------------------------------ ADMIN
  async function loadAdmin() {
    const users = await api('admin/users', { useCache: false });
    $('#table-users').innerHTML = table(
      [['ID', 'rank'], 'Username', 'Name', 'Role', 'Account'],
      users.map((u) => [[u.id, 'rank'], `<code>${esc(u.username)}</code>`, esc(u.displayName), `<span class="tag">${esc(u.role)}</span>`,
        u.demo ? 'Public demo' : '<strong>Owner</strong>']));
    renderServiceStatus($('#service-status'));
  }

  async function renderServiceStatus(box) {
    box.innerHTML = '<div class="muted small">Checking the data & AI service… (it may need a minute to wake up)</div>';
    try {
      const s = await api('ai/status', { useCache: false, retries: 0 });
      if (s.configured === false) { box.innerHTML = '<div class="muted small">Not configured on this deployment.</div>'; return; }
      const yes = (v) => (v ? 'Yes' : 'No');
      box.innerHTML = `
        <div><div class="v">${yes(s.database)}</div><div class="l">Database reachable</div></div>
        <div><div class="v">${s.role_isolation === true ? 'On' : s.role_isolation === false ? 'Off' : '–'}</div><div class="l">ai_reader role isolation</div></div>
        <div><div class="v">${s.llm_configured ? esc(s.model) : 'None'}</div><div class="l">Language model</div></div>
        <div><div class="v">${s.documents ?? '–'} / ${s.documents_embedded ?? '–'}</div><div class="l">Docs indexed / embedded</div></div>
        <div><div class="v">${s.questions_today ?? '–'} / ${s.daily_limit ?? '–'}</div><div class="l">AI questions today / limit</div></div>`;
    } catch (e) {
      box.innerHTML = `<div class="muted small">${esc(e.message)}</div>`;
    }
  }

  async function adminAction(path, label) {
    const status = $('#cache-status');
    status.textContent = label + '…';
    try {
      const r = await api(path, { method: 'POST', retries: 0 });
      state.cache.clear();
      status.textContent = label + ' done at ' + new Date().toLocaleTimeString('en-IN') +
        (r && r.k ? ` (k = ${r.k}, silhouette ${r.silhouette}, ${num(r.customers)} customers)` : '') +
        (Array.isArray(r) ? ` (${r.length} documents)` : '');
    } catch (e) { status.textContent = label + ' failed: ' + e.message; }
  }

  // ------------------------------------------------------------------ ML SEGMENTS (Customers tab)
  function renderRfm(data) {
    const segs = data.segments || [];
    if (!segs.length) {
      $('#rfm-segments').innerHTML = '<p class="muted">Segments have not been computed yet. An admin can run the segmentation job from the Admin tab.</p>';
      $('#table-churn').innerHTML = '';
      return;
    }
    const computed = segs[0].computed_at ? new Date(segs[0].computed_at).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' }) : '';
    $('#rfm-segments').innerHTML = `
      <div class="seg-grid">${segs.map((s) => `
        <div class="seg">
          <div class="seg-top"><strong>${esc(s.segment_label)}</strong><span class="muted small">${num(s.customers)} customers</span></div>
          <div class="seg-bars">
            <div class="seg-bar"><span class="muted small">Customers</span><div class="bar-track"><div class="bar-fill" style="width:${s.customer_share_pct}%"></div></div><span class="small">${pct(s.customer_share_pct)}</span></div>
            <div class="seg-bar"><span class="muted small">Revenue</span><div class="bar-track"><div class="bar-fill alt" style="width:${s.value_share_pct}%"></div></div><span class="small">${pct(s.value_share_pct)}</span></div>
          </div>
          <div class="muted small">Median ${Math.round(s.median_recency_days)} days since last order · ${Math.round(s.median_orders)} orders · <span class="risk-high">${num(s.high_churn_risk)} high churn risk</span></div>
        </div>`).join('')}
      </div>
      <p class="muted small">Model refreshed ${esc(computed)}</p>`;
    $('#table-churn').innerHTML = table(
      ['Customer', 'Segment', 'RFM segment', ['Days since order', 'num'], ['Usual gap', 'num'], ['LTV', 'num']],
      (data.watchlist || []).map((c) => [`${esc(c.full_name)}<div class="muted small">${esc(c.city)}</div>`, `<span class="tag">${esc(c.segment)}</span>`,
        esc(c.segment_label), [c.recency_days, 'num'], [c.avg_gap_days ? Math.round(c.avg_gap_days) + 'd' : '–', 'num'], [inr(c.lifetime_value), 'num']]));
  }

  // ------------------------------------------------------------------ AI ANALYST
  const AI_EXAMPLES = [
    'Top 5 products by revenue in Delhi during the last 6 months',
    'Monthly revenue trend for the West region this year',
    'Which ticket category breaches its SLA most often?',
    'How many high churn risk customers are there in each region?',
    'What is the refund policy for orders above ₹50,000?',
    'What discount can a Regional Sales Manager approve?',
  ];
  let aiCounter = 0;

  async function loadAi() {
    const chips = $('#ai-examples');
    if (!chips.childElementCount) {
      chips.innerHTML = AI_EXAMPLES.map((q) => `<button type="button" class="chip">${esc(q)}</button>`).join('');
      $$('.chip', chips).forEach((c) => c.addEventListener('click', () => { $('#ai-input').value = c.textContent; askAi(c.textContent); }));
    }
    $('#doc-form').hidden = state.user.demo !== false || state.user.role !== 'ADMIN';
    const status = $('#ai-status');
    status.textContent = 'Connecting to the AI service… (on the free tier it can take up to a minute to wake up)';
    try {
      const [s, docs] = await Promise.all([
        api('ai/status', { useCache: false, retries: 0 }),
        api('ai/documents', { useCache: false, retries: 0 }),
      ]);
      if (s.configured === false) { status.textContent = 'The AI service is not configured on this deployment.'; return; }
      status.innerHTML = s.llm_configured
        ? `● Ready · model <code>${esc(s.model)}</code> · role isolation ${s.role_isolation ? 'on' : 'off'} · ${s.questions_today}/${s.daily_limit} questions used today`
        : '● Ready, but no language model is configured. Policy questions still work with keyword search.';
      $('#ai-docs').innerHTML = docs.map((d) => `<li><strong>${esc(d.title)}</strong><span class="muted small">${d.chunk_count} chunks · ${d.embedded ? 'semantic + keyword' : 'keyword only'}</span></li>`).join('') || '<li class="muted">No documents indexed yet.</li>';
    } catch (e) {
      status.textContent = e.message;
    }
  }

  function bubble(html, cls) {
    const thread = $('#ai-thread');
    $('.ai-empty', thread)?.remove();
    const div = document.createElement('div');
    div.className = 'msg ' + cls;
    div.innerHTML = html;
    thread.appendChild(div);
    div.scrollIntoView({ behavior: 'smooth', block: 'end' });
    return div;
  }

  const mdLite = (text) => esc(text).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/\[(\d+)\]/g, '<sup class="cite">[$1]</sup>').replace(/\n/g, '<br>');

  const isMoneyCol = (c) => /(revenue|amount|value|sales|price|cost|spend|monetary|aov|margin_inr|ltv)/i.test(c) && !/(pct|percent|rate|share|count|units|orders)/i.test(c);
  const isPctCol = (c) => /(pct|percent|rate|share)/i.test(c);
  function fmtCell(col, v) {
    if (v == null) return '–';
    if (typeof v === 'number') return isMoneyCol(col) ? inr(v) : isPctCol(col) ? pct(v) : Number.isInteger(v) ? num(v) : v.toLocaleString('en-IN', { maximumFractionDigits: 2 });
    return esc(v);
  }

  async function askAi(question) {
    question = (question || '').trim();
    if (!question) return;
    $('#ai-input').value = '';
    bubble(esc(question), 'user');
    const pending = bubble('<span class="typing">Thinking<span>.</span><span>.</span><span>.</span></span>', 'bot');
    try {
      const r = await api('ai/ask', { method: 'POST', body: { question }, retries: 0 });
      pending.innerHTML = renderAnswer(r);
      const chartBox = $('canvas', pending);
      if (chartBox && r.chart) drawAiChart(chartBox.id, r);
    } catch (e) {
      pending.innerHTML = `<p class="error">${esc(e.message)}</p>`;
    }
  }

  function renderAnswer(r) {
    let html = `<div class="answer">${mdLite(r.answer || '')}</div>`;
    if (r.route === 'sql' && r.status === 'OK') {
      const chart = r.chart || {};
      if (chart.type === 'number' && r.rows.length) {
        const v = r.rows[0][chart.value_index ?? 0];
        html += `<div class="big-number">${fmtCell(r.columns[chart.value_index ?? 0], v)}<span class="muted small">${esc(r.columns[chart.value_index ?? 0])}</span></div>`;
      } else if (chart.type === 'line' || chart.type === 'bar') {
        html += `<div class="chart-box ai-chart"><canvas id="ai-chart-${++aiCounter}" role="img" aria-label="${esc(chart.title || 'Result chart')}"></canvas></div>`;
      }
      if (r.rows.length) {
        html += `<details class="result-table" ${chart.type === 'table' ? 'open' : ''}><summary>Result table · ${num(r.row_count)} row(s)${r.truncated ? ' (truncated)' : ''}</summary>
          <div class="table-wrap"><table>${table(r.columns.map((c) => [esc(c), 'num-auto']), r.rows.slice(0, 50).map((row) => row.map((v, i) => [fmtCell(r.columns[i], v), typeof v === 'number' ? 'num' : ''])))}</table></div></details>`;
      }
    }
    if (r.sql) {
      html += `<details class="sql-details"><summary>SQL${r.attempts > 1 ? ` (self-corrected after ${r.attempts - 1} failed attempt)` : ''} · validated and run read-only</summary><pre><code>${esc(r.sql)}</code></pre></details>`;
    }
    if (r.status === 'REJECTED' || r.status === 'ERROR') {
      html += `<p class="muted small">Reason: ${esc(r.error || 'unknown')}</p>`;
    }
    if (r.route === 'docs' && r.sources && r.sources.length) {
      html += `<div class="sources"><div class="muted small">Sources${r.mode === 'extractive' ? ' (no language model: showing the best-matching passage)' : ''}</div>` +
        r.sources.slice(0, 4).map((s) => `<div class="source ${s.cited ? 'cited' : ''}"><span class="n">[${s.n}]</span><div><strong>${esc(s.document)}</strong> › ${esc(s.section || '')}
          <div class="muted small">${esc(s.snippet)}</div><div class="muted tiny">matched by ${esc((s.matched_by || []).join(' + '))}</div></div></div>`).join('') + '</div>';
    }
    if (r.latency_ms) html += `<div class="muted tiny">${(r.latency_ms / 1000).toFixed(1)} s · route: ${esc(r.route || '–')}</div>`;
    return html;
  }

  function drawAiChart(id, r) {
    const c = r.chart;
    const labels = r.rows.map((row) => {
      const v = row[c.label_index];
      return typeof v === 'string' && /^\d{4}-\d{2}-\d{2}/.test(v) ? monthLabel(v.slice(0, 10)) : String(v);
    });
    const colors = [cssVar('--series-1'), cssVar('--series-2'), cssVar('--series-3')];
    const datasets = (c.value_indexes || []).map((idx, k) => ({
      label: r.columns[idx], data: r.rows.map((row) => row[idx]),
      borderColor: colors[k], backgroundColor: colors[k], borderWidth: 2, pointRadius: 0, pointHoverRadius: 5,
      tension: 0.25, borderRadius: 4, barPercentage: 0.7,
    }));
    const firstCol = r.columns[(c.value_indexes || [])[0]] || '';
    const horizontal = c.type === 'bar' && labels.length > 6;
    const valueAxis = { grid: { color: cssVar('--grid') }, border: { display: false }, beginAtZero: true,
      ticks: { color: cssVar('--text-2'), callback: (v) => (isMoneyCol(firstCol) ? inrAxis(v) : isPctCol(firstCol) ? v + '%' : num(v)) } };
    const labelAxis = { grid: { display: false }, border: { color: cssVar('--grid') }, ticks: { color: cssVar('--text-2'), autoSkipPadding: 10 } };
    drawChart(id, {
      type: c.type,
      data: { labels, datasets },
      options: baseOptions({
        indexAxis: horizontal ? 'y' : 'x',
        plugins: {
          ...baseOptions().plugins,
          legend: { ...baseOptions().plugins.legend, display: datasets.length > 1, position: 'top', align: 'end' },
          tooltip: { ...baseOptions().plugins.tooltip, callbacks: { label: (ctx) => ` ${ctx.dataset.label}: ${fmtCell(ctx.dataset.label, horizontal ? ctx.parsed.x : ctx.parsed.y)}` } },
        },
        scales: horizontal ? { x: valueAxis, y: labelAxis } : { x: labelAxis, y: valueAxis },
      }),
    });
  }

  async function uploadDocument(form) {
    const fd = new FormData(form);
    try {
      const r = await api('ai/documents', { method: 'POST', form: fd, retries: 0 });
      toast(`Indexed "${r.document}" (${r.chunks ?? 0} chunks, ${r.status})`);
      form.reset();
      loadAi();
    } catch (e) { toast('Upload failed: ' + e.message); }
  }

  // ------------------------------------------------------------------ INSIGHTS
  async function loadInsights() {
    const [latest, history] = await Promise.all([
      api('insights/latest', { useCache: false }),
      api('insights', { useCache: false }),
    ]);
    $('#insight-run').hidden = state.user.role !== 'ADMIN';
    if (!latest || !latest.id) {
      $('#insight-headline').textContent = 'No report has been generated yet';
      $('#insight-meta').textContent = 'The scheduled job will create one, or an admin can generate one now.';
      ['#insight-kpis', '#insight-bullets', '#table-anomalies', '#insight-sla', '#table-reports'].forEach((s) => ($(s).innerHTML = ''));
      return;
    }
    renderInsight(latest);
    $('#table-reports').innerHTML = table(['Generated', 'Data through', 'Trigger', 'E-mail'],
      history.map((h) => [`<a href="#" data-report="${h.id}">${new Date(h.generated_at).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })}</a>`,
        esc(h.as_of_date), esc(h.trigger), esc(h.email_status || '–')]));
    $$('#table-reports [data-report]').forEach((a) => a.addEventListener('click', async (e) => {
      e.preventDefault();
      renderInsight(await api('insights/' + a.dataset.report, { useCache: false }));
    }));
  }

  function renderInsight(r) {
    const p = r.payload || {};
    $('#insight-headline').textContent = r.headline;
    $('#insight-meta').textContent = `Data through ${r.as_of_date} · generated ${new Date(r.generated_at).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })} (${r.trigger})` +
      (r.email_status ? ` · e-mail: ${r.email_status}` : '');
    $('#insight-summary').hidden = !r.summary;
    $('#insight-summary').textContent = r.summary || '';
    $('#insight-kpis').innerHTML = (p.kpis || []).map((k) => {
      const value = k.value == null ? '–' : k.money ? inr(k.value) : num(k.value);
      const cls = k.good == null ? '' : k.good ? 'up' : 'down';
      const arrow = k.change_pct == null ? '' : k.change_pct >= 0 ? '▲' : '▼';
      return kpi(esc(k.label), value, k.change_pct == null ? '<span>no prior week</span>' : `<span class="${cls}">${arrow} ${Math.abs(k.change_pct).toFixed(1)}%</span> vs previous 7 days`);
    }).join('');
    $('#insight-bullets').innerHTML = (p.bullets || []).map((b) => `<li>${esc(b)}</li>`).join('');
    $('#table-anomalies').innerHTML = (p.anomalies || []).length
      ? table(['Segment', 'Type', ['This week', 'num'], ['8-week avg', 'num'], ['Change', 'num'], ['z-score', 'num']],
        p.anomalies.map((a) => [esc(a.member), `<span class="tag">${esc(a.dimension)}</span>`, [inr(a.current), 'num'], [inr(a.baseline_mean), 'num'],
          [`<span class="${a.change_pct > 0 ? 'up' : 'down'}">${a.change_pct > 0 ? '+' : ''}${a.change_pct.toFixed(0)}%</span>`, 'num'], [a.z.toFixed(1), 'num']]))
      : '<tbody><tr><td class="muted">No anomalies this week.</td></tr></tbody>';
    const sla = p.sla || {};
    $('#insight-sla').innerHTML = `
      <div><div class="v">${num(sla.breached_7d)}</div><div class="l">Breached (7d)</div></div>
      <div><div class="v">${pct(sla.breach_rate_pct)}</div><div class="l">Breach rate (prev ${pct(sla.breach_rate_prev_pct)})</div></div>
      <div><div class="v">${num(sla.open_overdue)}</div><div class="l">Open &amp; past SLA</div></div>
      <div><div class="v">${num(sla.open_high_priority)}</div><div class="l">Open high / critical</div></div>`;
    const link = $('#insight-email');
    link.hidden = false;
    link.onclick = async (e) => {
      e.preventDefault();
      const res = await api(`insights/${r.id}/email`, { raw: true, useCache: false });
      const url = URL.createObjectURL(new Blob([await res.text()], { type: 'text/html' }));
      window.open(url, '_blank', 'noopener');
    };
  }

  async function runInsightsNow() {
    const btn = $('#insight-run');
    btn.disabled = true; btn.textContent = 'Generating…';
    try { await api('insights/run', { method: 'POST', retries: 0 }); await loadInsights(); toast('New report generated'); }
    catch (e) { toast('Failed: ' + e.message); }
    finally { btn.disabled = false; btn.textContent = 'Generate a report now'; }
  }

  // ------------------------------------------------------------------ DATA (ETL)
  async function loadData() {
    const isAdmin = state.user.role === 'ADMIN';
    const isOwner = isAdmin && state.user.demo === false;
    $('#upload-form').hidden = !isAdmin;
    $('#sample-run').hidden = !isAdmin;
    const commit = $('#upload-form [name=commit]');
    commit.disabled = !isOwner;
    if (!isOwner) commit.checked = false;
    $('#commit-note').textContent = !isAdmin
      ? 'Analysts can read every data-quality report. Loading data needs the Admin role.'
      : isOwner ? 'Unchecked = dry run: the file is fully validated and reported, then the transaction is rolled back.'
        : 'Demo accounts always run as a dry run: full validation and report, then the transaction is rolled back, so the shared demo database stays unchanged. Only the owner account can commit.';
    loadRuns();
    loadSamples(false);
  }

  async function loadSamples(refresh) {
    const box = $('#sample-files');
    box.innerHTML = '<div class="muted small">Preparing a sample batch from the live database… (the data service may need a minute to wake up)</div>';
    try {
      const s = await api('data/samples' + (refresh ? '?refresh=true' : ''), { useCache: false, retries: 0 });
      box.innerHTML = `<div class="muted small">Covers ${esc(s.period_start)} to ${esc(s.period_end)}</div>` +
        s.files.map((f) => `<div class="sample-file"><code>${esc(f.name)}</code><span class="muted small">${num(f.rows)} rows</span>
          <a href="#" data-file="${esc(f.name)}" class="small">Download</a></div>`).join('');
      $$('[data-file]', box).forEach((a) => a.addEventListener('click', async (e) => {
        e.preventDefault();
        const res = await api('data/samples/' + a.dataset.file, { raw: true, useCache: false, retries: 0 });
        const url = URL.createObjectURL(await res.blob());
        const link = Object.assign(document.createElement('a'), { href: url, download: a.dataset.file });
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
      }));
    } catch (e) {
      box.innerHTML = `<div class="muted small">${esc(e.message)}</div>`;
    }
  }

  async function loadRuns() {
    const runs = await api('data/runs', { useCache: false });
    $('#table-runs').innerHTML = runs.length ? table(
      [['#', 'rank'], 'When', 'File', 'Entity', 'Mode', 'Status', ['Rows', 'num'], ['Loaded', 'num'], ['Rejected', 'num'], ['Flagged', 'num'], ['Quality', 'num']],
      runs.map((r) => [[`<a href="#" data-run="${r.id}">${r.id}</a>`, 'rank'], new Date(r.started_at).toLocaleString('en-IN', { dateStyle: 'short', timeStyle: 'short' }),
        `<code>${esc(r.file_name)}</code>`, esc(r.entity), `<span class="tag">${esc(r.mode)}</span>`, statusPill(r.status),
        [num(r.rows_in), 'num'], [num(r.rows_loaded), 'num'], [num(r.rows_rejected), 'num'], [num(r.rows_flagged), 'num'], [r.quality_score == null ? '–' : pct(r.quality_score), 'num']]))
      : '<tbody><tr><td class="muted">No pipeline runs yet. Try the sample batch.</td></tr></tbody>';
    $$('#table-runs [data-run]').forEach((a) => a.addEventListener('click', async (e) => {
      e.preventDefault();
      const run = await api('data/runs/' + a.dataset.run, { useCache: false });
      showRunReports([run], `Run #${run.id}: ${run.file_name}`, `${run.mode} · ${run.status} · ${run.source} · ${run.triggered_by || ''}`);
    }));
  }

  const statusPill = (s) => `<span class="pill pill-${String(s).toLowerCase()}">${esc(s)}</span>`;

  function showRunReports(results, title, meta) {
    $('#run-result-card').hidden = false;
    $('#run-result-title').textContent = title;
    $('#run-result-meta').textContent = meta || '';
    $('#run-result').innerHTML = results.map((r) => {
      const summary = r.issue_summary || [];
      const issues = r.issues || [];
      return `<div class="run-report">
        <div class="run-head"><code>${esc(r.file_name)}</code> <span class="tag">${esc(r.entity)}</span> ${statusPill(r.status)} <span class="tag">${esc(r.mode)}</span>
          ${r.error || r.error_message ? `<span class="error small">${esc(r.error || r.error_message)}</span>` : ''}</div>
        <div class="kpis compact">
          ${kpi('Rows in', num(r.rows_in), '<span>after removing blank rows</span>')}
          ${kpi('Loaded', num(r.rows_loaded), `<span>${num(r.rows_inserted)} new · ${num(r.rows_updated)} updated</span>`)}
          ${kpi('Rejected', num(r.rows_rejected), '<span>errors</span>')}
          ${kpi('Duplicates', num(r.rows_duplicate), '<span>skipped</span>')}
          ${kpi('Flagged', num(r.rows_flagged), '<span>loaded with a warning</span>')}
          ${kpi('Quality score', r.quality_score == null ? '–' : pct(r.quality_score), '<span>clean rows / rows in</span>')}
        </div>
        ${summary.length ? `<div class="table-wrap"><table>${table(['Severity', 'Rule', 'Column', ['Rows', 'num']],
          summary.map((s) => [statusPill(s.severity), `<code>${esc(s.rule)}</code>`, esc(s.column ?? s.column_name ?? '–'), [num(s.count), 'num']]))}</table></div>` : ''}
        ${issues.length ? `<details><summary>Row-level issues (${num(issues.length)} shown)</summary><div class="table-wrap"><table>${table(
          [['Line', 'num'], 'Severity', 'Rule', 'Column', 'Value', 'Message'],
          issues.slice(0, 80).map((i) => [[i.line ?? i.row_number ?? '–', 'num'], statusPill(i.severity), `<code>${esc(i.rule)}</code>`, esc(i.column ?? i.column_name ?? '–'),
            `<code>${esc(i.raw_value ?? '')}</code>`, esc(i.message)]))}</table></div></details>` : ''}
      </div>`;
    }).join('');
    $('#run-result-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  async function runSampleBatch() {
    const btn = $('#sample-run');
    const commit = $('#upload-form [name=commit]').checked && state.user.demo === false;
    btn.disabled = true; btn.textContent = 'Running…';
    try {
      const r = await api('data/samples/run?commit=' + commit, { method: 'POST', retries: 0 });
      showRunReports(r.results, `Sample batch ${r.period_start} to ${r.period_end}`,
        `${r.mode}${r.forced_dry_run ? ' (demo accounts always run as a dry run)' : ''} · ${r.results.length} files processed atomically in dependency order`);
      if (commit) { state.cache.clear(); loadSamples(false); }
      loadRuns();
    } catch (e) { toast('Pipeline failed: ' + e.message); }
    finally { btn.disabled = false; btn.textContent = 'Run the whole batch'; }
  }

  async function uploadCsv(form) {
    const fd = new FormData(form);
    const commit = form.commit.checked && state.user.demo === false;
    fd.set('commit', String(commit));
    if (!fd.get('entity')) fd.delete('entity');
    const btn = $('button[type=submit]', form);
    btn.disabled = true; btn.textContent = 'Running…';
    try {
      const r = await api('data/upload', { method: 'POST', form: fd, retries: 0 });
      showRunReports([r], `Upload: ${r.file_name}`, `${r.mode}${r.forced_dry_run ? ' (demo accounts always run as a dry run)' : ''}`);
      if (commit) state.cache.clear();
      loadRuns();
    } catch (e) { toast('Upload failed: ' + e.message); }
    finally { btn.disabled = false; btn.textContent = 'Run pipeline'; }
  }

  // ------------------------------------------------------------------ table helper
  // header: 'Label' | ['Label', cls]   cell: html | [html, cls]
  function table(headers, rows) {
    const cell = (c, tag) => (Array.isArray(c) ? `<${tag} class="${c[1]}">${c[0]}</${tag}>` : `<${tag}>${c}</${tag}>`);
    return `<thead><tr>${headers.map((h) => cell(h, 'th')).join('')}</tr></thead>` +
      `<tbody>${rows.map((r) => `<tr>${r.map((c) => cell(c, 'td')).join('')}</tr>`).join('')}</tbody>`;
  }

  // ------------------------------------------------------------------ wiring
  function init() {
    $$('.demo-btn').forEach((b) => b.addEventListener('click', () => login(b.dataset.user, 'demo123')));
    $('#login-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const f = new FormData(e.target);
      login(f.get('username'), f.get('password'));
    });
    $('#logout').addEventListener('click', () => logout());
    $$('.tabs [role=tab]').forEach((b) => b.addEventListener('click', () => selectTab(b.dataset.tab)));
    $$('#region-filter [role=radio]').forEach((b) => b.addEventListener('click', () => {
      state.region = b.dataset.region;
      $$('#region-filter [role=radio]').forEach((x) => x.setAttribute('aria-checked', String(x === b)));
      loadTab(state.tab);
    }));
    $('#sql-close').addEventListener('click', () => $('#sql-dialog').close());
    $('#clear-cache').addEventListener('click', () => adminAction('admin/cache/clear', 'Cache clear'));
    $('#run-segments').addEventListener('click', () => adminAction('admin/segments/run', 'Segmentation'));
    $('#reindex-docs').addEventListener('click', () => adminAction('admin/documents/reindex', 'Re-indexing'));
    $('#ai-form').addEventListener('submit', (e) => { e.preventDefault(); askAi($('#ai-input').value); });
    $('#doc-form').addEventListener('submit', (e) => { e.preventDefault(); uploadDocument(e.target); });
    $('#insight-run').addEventListener('click', runInsightsNow);
    $('#upload-form').addEventListener('submit', (e) => { e.preventDefault(); uploadCsv(e.target); });
    $('#sample-run').addEventListener('click', runSampleBatch);
    $('#sample-refresh').addEventListener('click', () => loadSamples(true));
    // Re-draw charts with the new palette when the OS theme changes
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (state.token) loadTab(state.tab); });

    if (restoreSession()) showApp();
    else { $('#login-view').hidden = false; checkServer(); }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
