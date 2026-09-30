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

  async function api(path, { method = 'GET', body, useCache = true } = {}) {
    if (method === 'GET' && useCache && state.cache.has(path)) return state.cache.get(path);
    for (let attempt = 0; attempt < 4; attempt++) {
      const res = await fetch('api/' + path, {
        method,
        headers: {
          'Content-Type': 'application/json',
          ...(state.token ? { Authorization: 'Bearer ' + state.token } : {}),
        },
        body: body ? JSON.stringify(body) : undefined,
      });
      if (res.status === 401 && state.token) { logout('Your session expired. Please sign in again.'); throw new Error('unauthorized'); }
      if (res.status === 503 && attempt < 3) { await sleep(2500 * (attempt + 1)); continue; } // DB waking up
      if (!res.ok) {
        let detail = res.statusText;
        try { const j = await res.json(); detail = j.detail || j.message || detail; } catch { /* not JSON */ }
        const err = new Error(detail); err.status = res.status; throw err;
      }
      const data = await res.json();
      if (method === 'GET') state.cache.set(path, data);
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
    state.user = { username: data.username, displayName: data.displayName, role: data.role };
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
    $('#admin-tab').hidden = state.user.role !== 'ADMIN';
    decorateSqlButtons();
    selectTab('overview');
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
    const [growth, cohorts, segments, customers] = await Promise.all([
      api(withRegion('analytics/customer-growth')),
      api(withRegion('analytics/cohorts')),
      api(withRegion('analytics/segments')),
      api(withRegion('analytics/top-customers')),
    ]);

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
      api('dashboard/summary'),
      api('analytics/support/sla'),
      api('analytics/support/trend'),
      api('analytics/support/agents'),
    ]);
    const t = summary.support;
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
      [['ID', 'rank'], 'Username', 'Name', 'Role', 'Enabled'],
      users.map((u) => [[u.id, 'rank'], `<code>${esc(u.username)}</code>`, esc(u.displayName), `<span class="tag">${esc(u.role)}</span>`, u.enabled ? 'Yes' : 'No']));
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
    $('#clear-cache').addEventListener('click', async () => {
      try {
        await api('admin/cache/clear', { method: 'POST' });
        state.cache.clear();
        $('#cache-status').textContent = 'Server cache cleared at ' + new Date().toLocaleTimeString('en-IN');
      } catch (e) { toast('Failed: ' + e.message); }
    });
    // Re-draw charts with the new palette when the OS theme changes
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (state.token) loadTab(state.tab); });

    if (restoreSession()) showApp();
    else { $('#login-view').hidden = false; checkServer(); }
  }

  document.addEventListener('DOMContentLoaded', init);
})();
