/* ResQ AI — control room.
 *
 * The operator half of the backend: the severity queue and the right to
 * override it, dispatch, human review of flagged reports, the responder
 * roster, the process log, and the governance record.
 *
 * Every action is attributed to a named operator, because the backend records
 * who did it and a blank attribution would make that record useless.
 *
 * ROLE SEPARATION IS ADVISORY HERE. The backend has no authentication and no
 * per-incident authorisation, so the scoping below is a UI affordance, not a
 * security boundary — the sign-in screen says so plainly rather than implying
 * a protection that does not exist. What *is* real is the attribution: every
 * override and review lands in the append-only process log under this name,
 * which is what makes "my incidents" derivable at all.
 */

import { api } from './api.js';
import {
  $, $$, esc, toast, severityBand, severityLabel, titleCase,
  ago, minutesLabel, parseUtc, debounce, reasonLabel,
} from './ui.js';

const state = {
  tab: 'overview',
  counts: {},
  eventFilter: { activity: '', case_id: '' },
};

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'mine', label: 'My incidents', count: 'mine' },
  { key: 'queue', label: 'Queue', count: 'queue' },
  { key: 'review', label: 'Review', count: 'flagged' },
  { key: 'dispatch', label: 'Dispatch' },
  { key: 'responders', label: 'Responders' },
  { key: 'bottlenecks', label: 'Bottlenecks', count: 'bottlenecks' },
  { key: 'log', label: 'Process log' },
  { key: 'governance', label: 'Governance' },
];

const ROLES = {
  officer: { label: 'Control Officer', scope: 'Incidents you have handled' },
  supervisor: { label: 'Supervisor', scope: 'All incidents in the region' },
  admin: { label: 'Administrator', scope: 'Everything, including the audit log' },
};

const operator = {
  get name() { return localStorage.getItem('resq.operator') || ''; },
  set name(v) { localStorage.setItem('resq.operator', v); },
  get role() { return localStorage.getItem('resq.role') || 'officer'; },
  set role(v) { localStorage.setItem('resq.role', v); },
  get signedIn() { return Boolean(this.name); },
  signOut() {
    localStorage.removeItem('resq.operator');
    localStorage.removeItem('resq.role');
  },
  /** A Control Officer works their own incidents; seniors see everything. */
  get seesEverything() { return this.role !== 'officer'; },
};

/* Cases this operator has touched, read back out of the event log. The backend
 * does not assign incidents to operators, so "mine" is derived from what this
 * name has actually done — which is the only honest definition available. */
async function myCaseIds() {
  const { items } = await api.events({ resource: `operator:${operator.name}`, limit: 500 });
  return new Set(items.map((e) => e.case_id));
}

// --- Shell ---------------------------------------------------------------

function renderTabs() {
  $('#ctlTabs').innerHTML = TABS.map((tab) => {
    const count = tab.count ? state.counts[tab.count] : null;
    return `<button data-tab="${tab.key}" class="${state.tab === tab.key ? 'is-active' : ''}">
      ${tab.label}${count ? `<span class="count">${count}</span>` : ''}
    </button>`;
  }).join('');
  $$('#ctlTabs button').forEach((btn) => {
    btn.addEventListener('click', () => { state.tab = btn.dataset.tab; render(); });
  });
}

function loading() {
  $('#ctlMain').innerHTML = '<div class="ctl-grid"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>';
}

function failure(err) {
  $('#ctlMain').innerHTML = `<div class="empty">
    <b>${esc(err.message)}</b><br><br>
    The control room needs a live connection to the dispatch backend.<br>
    <span class="tiny dim">${esc(api.base)}</span>
  </div>`;
}

async function render() {
  renderTabs();
  loading();
  try {
    await VIEWS[state.tab]();
  } catch (err) {
    failure(err);
  }
}

// --- Overview -------------------------------------------------------------

const VIEWS = {
  async overview() {
    const [queue, responders, bottlenecks, governance, flagged] = await Promise.all([
      api.queue({ limit: 200 }),
      api.responders({ limit: 100 }),
      api.bottlenecks(),
      api.governance(),
      api.listReports({ status: 'flagged', limit: 100 }),
    ]);

    state.counts = {
      queue: queue.total,
      flagged: flagged.total,
      bottlenecks: bottlenecks.bottlenecks.length,
      mine: (await myCaseIds()).size,
    };
    renderTabs();

    const free = responders.items.filter((r) => r.dispatchable).length;
    const slots = responders.items
      .filter((r) => r.dispatchable)
      .reduce((sum, r) => sum + r.spare_capacity, 0);
    const worst = bottlenecks.bottlenecks[0];
    const longest = queue.items.reduce((max, item) =>
      Math.max(max, item.priority.minutes_waiting), 0);

    $('#ctlMain').innerHTML = `
      <div class="ctl-grid">
        <div class="metric"><b>${queue.total}</b><span>WAITING IN QUEUE</span>
          <small>Longest wait ${minutesLabel(longest)}</small></div>
        <div class="metric"><b>${free}</b><span>CREWS AVAILABLE</span>
          <small>${slots} free assignment slot${slots === 1 ? '' : 's'} across the fleet</small></div>
        <div class="metric"><b>${flagged.total}</b><span>AWAITING REVIEW</span>
          <small>${flagged.total ? 'A person must decide — nothing is auto-rejected' : 'Review queue is clear'}</small></div>
        <div class="metric ${worst ? 'bottleneck flagged' : ''}">
          <b class="ratio">${worst ? `${worst.deviation_ratio}×` : '—'}</b>
          <span>WORST BOTTLENECK</span>
          <small>${worst ? `${esc(worst.transition)}: ${worst.current_mean_minutes} min now vs ${worst.median_minutes} min median` : 'No stage is running above its historical median'}</small></div>
      </div>

      <div class="section-title">Top of the queue</div>
      <div class="stack" id="overviewQueue">
        ${queue.items.slice(0, 5).map(queueRow).join('') || '<div class="empty">The queue is clear.</div>'}
      </div>

      <div class="section-title">Scoring provenance</div>
      <div class="metric"><small style="font-size:13.5px;color:var(--text-primary)">${esc(governance.scoring.honest_summary)}</small></div>`;

    bindQueueRows();
  },

  // --- My incidents ------------------------------------------------------

  async mine() {
    const ids = await myCaseIds();
    state.counts.mine = ids.size;
    renderTabs();

    if (!ids.size) {
      $('#ctlMain').innerHTML = `<div class="restricted">
        <div class="lock-line">🔒 ${esc(ROLES[operator.role].label)} · ${esc(ROLES[operator.role].scope)}</div>
        You have not acted on any incident yet.<br><br>
        Pin, dispatch or review something from the Queue and it appears here, attributed to
        <b>${esc(operator.name)}</b> in the process log.
      </div>`;
      return;
    }

    const reports = await Promise.all([...ids].slice(0, 40).map((id) => api.getReport(id).catch(() => null)));
    const rows = reports.filter(Boolean);

    $('#ctlMain').innerHTML = `
      <div class="lock-line">🔒 Authorised access · ${esc(ROLES[operator.role].label)} · ${esc(operator.name)}</div>
      <p class="tiny dim" style="margin-bottom:14px">
        Derived from the process log: these are the cases you have personally acted on.
      </p>
      <div class="stack">${rows.map((r) => {
        const band = severityBand(r.severity_score);
        return `<div class="ctl-row sev-${band}">
          <div class="body">
            <div class="headline" data-open="${r.id}">${esc(r.text)}</div>
            <div class="metaline">
              <span class="pill sev-chip sev-${band}">Severity ${r.severity_score}</span>
              <span class="pill status-chip status-${esc(r.status)}">${esc(titleCase(r.status))}</span>
              <span class="tiny dim">${ago(r.client_created_at)}</span>
            </div>
          </div>
        </div>`;
      }).join('')}</div>`;
    bindOpen();
  },

  // --- Queue -------------------------------------------------------------

  async queue() {
    const queue = await api.queue({ limit: 200 });
    state.counts.queue = queue.total;
    renderTabs();

    $('#ctlMain').innerHTML = `
      <p class="tiny dim" style="margin-bottom:12px">
        Ranked by 0.70 × severity + 0.15 × trust + 0.15 × ageing. Pin sorts above every
        computed score, demote below every one — each override is written to the process log
        with your name against it.
      </p>
      <div class="stack">${queue.items.map(queueRow).join('') || '<div class="empty">Nothing waiting.</div>'}</div>`;
    bindQueueRows();
  },

  // --- Review ------------------------------------------------------------

  async review() {
    const flagged = await api.listReports({ status: 'flagged', limit: 100 });
    state.counts.flagged = flagged.total;
    renderTabs();

    $('#ctlMain').innerHTML = `
      <p class="tiny dim" style="margin-bottom:12px">
        Automated scoring routed these here; it cannot reject them. Rejection is only ever a
        human decision, and it is recorded as one.
      </p>
      <div class="stack">${flagged.items.map((report) => {
        const band = severityBand(report.severity_score);
        return `<div class="ctl-row sev-${band}">
          <div class="body">
            <div class="headline" data-open="${report.id}">${esc(report.text)}</div>
            <div class="metaline">
              <span class="pill sev-chip sev-${band}">Severity ${report.severity_score}</span>
              <span class="pill status-flagged">Trust ${report.authenticity_score}</span>
              <span class="tiny dim">${esc(titleCase(report.incident_type || 'unclassified'))} · ${ago(report.client_created_at)}</span>
            </div>
          </div>
          <div class="actions">
            <button class="btn btn-sm btn-good" data-verify="${report.id}">Verify</button>
            <button class="btn btn-sm btn-danger" data-reject="${report.id}">Reject</button>
          </div>
        </div>`;
      }).join('') || '<div class="empty">Nothing is waiting for review.</div>'}</div>`;

    bindOpen();
    $$('[data-verify]').forEach((btn) => btn.addEventListener('click', () => review(btn.dataset.verify, 'verify')));
    $$('[data-reject]').forEach((btn) => btn.addEventListener('click', () => review(btn.dataset.reject, 'reject')));
  },

  // --- Dispatch ----------------------------------------------------------

  async dispatch() {
    const [queue, responders] = await Promise.all([
      api.queue({ limit: 10 }),
      api.responders({ limit: 100 }),
    ]);

    const free = responders.items.filter((r) => r.dispatchable);
    const slots = free.reduce((sum, r) => sum + r.spare_capacity, 0);

    $('#ctlMain').innerHTML = `
      <div class="ctl-grid">
        <div class="metric"><b>${queue.total}</b><span>WAITING</span></div>
        <div class="metric"><b>${slots}</b><span>FREE SLOTS</span>
          <small>${free.length} crew${free.length === 1 ? '' : 's'} available within range</small></div>
      </div>

      <div class="row" style="margin:16px 0;gap:10px;flex-wrap:wrap">
        <button class="btn btn-sm btn-gold" id="assignNext" ${slots ? '' : 'disabled'}>Assign next in queue</button>
        <button class="btn btn-sm btn-ghost" id="assignFive" ${slots ? '' : 'disabled'}>Assign up to 5</button>
        ${slots ? '' : '<span class="tiny dim">Every crew is at capacity or offline — assigning would defer.</span>'}
      </div>

      <div class="section-title">Next up</div>
      <div class="stack">${queue.items.slice(0, 8).map((item) => {
        const band = severityBand(item.priority.severity);
        return `<div class="ctl-row sev-${band}">
          <span class="rank-badge">${item.position}</span>
          <div class="body">
            <div class="headline" data-open="${item.id}">${esc(item.text)}</div>
            <div class="metaline">
              <span class="pill sev-chip sev-${band}">Severity ${item.priority.severity}</span>
              <span class="tiny dim">priority ${item.priority_score} · waiting ${minutesLabel(item.priority.minutes_waiting)}</span>
            </div>
          </div>
          <div class="actions"><button class="btn btn-sm btn-ghost" data-assign="${item.id}">Assign</button></div>
        </div>`;
      }).join('') || '<div class="empty">Nothing waiting.</div>'}</div>`;

    bindOpen();
    $('#assignNext').addEventListener('click', () => assign({}));
    $('#assignFive').addEventListener('click', () => assignMany(5));
    $$('[data-assign]').forEach((btn) => btn.addEventListener('click', () => assign({ reportId: btn.dataset.assign })));
  },

  // --- Responders --------------------------------------------------------

  async responders() {
    const { items } = await api.responders({ limit: 100 });
    $('#ctlMain').innerHTML = `
      <div class="table-wrap"><table class="table">
        <thead><tr><th>Crew</th><th>Skill</th><th>Status</th><th class="num">Load</th><th class="num">Tracked</th></tr></thead>
        <tbody>${items.map((r) => `<tr>
          <td><b>${esc(r.name)}</b></td>
          <td>${esc(titleCase(r.skill))}</td>
          <td><span class="pill ${r.dispatchable ? 'status-resolved' : 'status-flagged'}">${esc(titleCase(r.status))}</span></td>
          <td class="num">${r.active_count}/${r.capacity}</td>
          <td class="num">${r.open_assignments}</td>
        </tr>`).join('')}</tbody>
      </table></div>
      <p class="tiny dim" style="margin-top:10px">
        <b>Load</b> is what dispatch enforces capacity against. <b>Tracked</b> counts assignments
        this system created — the seeded fleet starts with prior workload, so the two differ legitimately.
      </p>`;
  },

  // --- Bottlenecks -------------------------------------------------------

  async bottlenecks() {
    const report = await api.bottlenecks();
    state.counts.bottlenecks = report.bottlenecks.length;
    renderTabs();

    $('#ctlMain').innerHTML = `
      <div class="ctl-grid">
        <div class="metric"><b>${report.closed_cases}</b><span>COMPLETED CASES</span><small>The baseline the medians are learned from</small></div>
        <div class="metric"><b>${report.open_cases}</b><span>CASES IN FLIGHT</span></div>
        <div class="metric"><b>${report.deviation_threshold}×</b><span>FLAG THRESHOLD</span></div>
      </div>
      ${report.note ? `<div class="metric" style="margin-top:14px"><small style="font-size:13.5px">${esc(report.note)}</small></div>` : ''}

      ${report.bottlenecks.length ? `<div class="section-title">Findings</div>
      <div class="stack">${report.bottlenecks.map((b) => `
        <div class="ctl-row bottleneck flagged">
          <div class="body">
            <div class="spread"><b style="font-size:15px">${esc(b.transition)}</b><span class="ratio" style="color:var(--danger)">${b.deviation_ratio}×</span></div>
            <p class="tiny muted" style="margin-top:6px;line-height:1.5">
              ${b.closed_cases} completed cases took a median of <b>${b.median_minutes} min</b>.
              ${b.open_cases} open cases are averaging <b>${b.current_mean_minutes} min</b>.
            </p>
            <p class="tiny" style="margin-top:8px;color:#ffd0a8;line-height:1.5"><b>Action:</b> ${esc(b.suggested_action)}</p>
          </div>
        </div>`).join('')}</div>` : ''}

      <div class="section-title">Every stage</div>
      <div class="table-wrap"><table class="table">
        <thead><tr><th>Transition</th><th class="num">Cases</th><th class="num">Median</th><th class="num">Now</th><th class="num">Ratio</th></tr></thead>
        <tbody>${report.transitions.map((t) => `<tr>
          <td>${esc(t.transition)}${t.is_bottleneck ? ' <span class="pill status-flagged">slow</span>' : ''}</td>
          <td class="num">${t.closed_cases}</td>
          <td class="num">${t.median_minutes} min</td>
          <td class="num">${t.open_cases ? `${t.current_mean_minutes} min` : '—'}</td>
          <td class="num">${t.deviation_ratio ? `${t.deviation_ratio}×` : '—'}</td>
        </tr>`).join('')}</tbody>
      </table></div>
      <p class="tiny dim" style="margin-top:10px">
        A stage with no completed cases has no baseline, so it is shown but never flagged —
        a long wait with nothing to compare it against is a fact, not a finding.
      </p>`;
  },

  // --- Process log --------------------------------------------------------

  async log() {
    const { activity, case_id } = state.eventFilter;
    const page = await api.events({ limit: 200, activity, case_id });

    $('#ctlMain').innerHTML = `
      <div class="filters">
        <select id="activityFilter">
          <option value="">All activities</option>
          ${['REPORT_RECEIVED', 'TRIAGE_COMPLETED', 'AUTHENTICITY_SCORED', 'REPORT_FLAGGED', 'REPORT_VERIFIED', 'REPORT_REJECTED', 'QUEUED', 'PRIORITY_OVERRIDDEN', 'ASSIGNED', 'DISPATCH_DEFERRED', 'ASSIGNMENT_REJECTED', 'ACKNOWLEDGED', 'EN_ROUTE', 'ON_SCENE', 'RESOLVED', 'CLOSED']
            .map((a) => `<option value="${a}" ${activity === a ? 'selected' : ''}>${a}</option>`).join('')}
        </select>
        <input id="caseFilter" placeholder="Filter by case id" value="${esc(case_id)}">
        <a class="btn btn-sm btn-ghost" href="${api.eventsCsvUrl({ activity, case_id })}" download>Download CSV</a>
      </div>
      <p class="tiny dim" style="margin-bottom:12px">
        Append-only. The CSV exports as <code>case_id, activity, timestamp, resource</code> —
        the shape Disco, ProM and pm4py read with no column mapping.
      </p>
      <div class="table-wrap" style="padding:12px 16px">
        ${page.items.slice().reverse().map((event) => `<div class="log-line">
          <time>${parseUtc(event.timestamp)?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time>
          <div>
            <b>${esc(event.activity)}</b>
            <span class="tiny dim"> · ${esc(event.resource)}</span>
            <div class="tiny dim"><a href="#" data-open="${event.case_id}">case ${esc(event.case_id.slice(0, 8))}…</a></div>
          </div>
        </div>`).join('') || '<div class="empty">No events match.</div>'}
      </div>
      <p class="tiny dim" style="margin-top:10px">Showing ${page.items.length} of ${page.total} events, newest first.</p>`;

    $('#activityFilter').addEventListener('change', (e) => {
      state.eventFilter.activity = e.target.value;
      render();
    });
    $('#caseFilter').addEventListener('input', debounce((e) => {
      const value = e.target.value.trim();
      // Only a complete uuid is a valid filter; anything else 422s.
      if (value === '' || /^[0-9a-f-]{36}$/i.test(value)) {
        state.eventFilter.case_id = value;
        render();
      }
    }, 500));
    bindOpen();
  },

  // --- Governance ---------------------------------------------------------

  async governance() {
    const g = await api.governance();
    const h = g.human_in_the_loop;

    $('#ctlMain').innerHTML = `
      <div class="metric" style="border-color:var(--accent-blue)">
        <span style="color:var(--accent-blue)">WHAT IS ACTUALLY RUNNING</span>
        <p style="font-size:14.5px;line-height:1.55;margin-top:9px">${esc(g.scoring.honest_summary)}</p>
      </div>

      <div class="section-title">Providers</div>
      <div class="table-wrap"><table class="table">
        <thead><tr><th>Provider</th><th>Kind</th><th>Credentials</th><th>Available</th><th class="num">Reports scored</th></tr></thead>
        <tbody>${g.scoring.providers.map((p) => `<tr>
          <td><b>${esc(p.name)}</b>${p.model ? `<div class="tiny dim">${esc(p.model)}</div>` : ''}</td>
          <td>${esc(p.kind)}<div class="tiny dim">${p.capabilities.join(', ')}</div></td>
          <td>${p.credentials_configured ? 'yes' : '—'}</td>
          <td>${p.available ? 'yes' : '—'}</td>
          <td class="num"><b>${p.reports_scored}</b></td>
        </tr>`).join('')}</tbody>
      </table></div>
      <p class="tiny dim" style="margin-top:8px">Active: <b>${esc(g.scoring.active_provider)}</b> · ${esc(g.scoring.fallback_state)}</p>

      <div class="section-title">Human decisions on record</div>
      <div class="ctl-grid">
        <div class="metric"><b>${h.priority_overrides}</b><span>QUEUE OVERRIDES</span></div>
        <div class="metric"><b>${h.reports_verified_by_human}</b><span>VERIFIED BY A PERSON</span></div>
        <div class="metric"><b>${h.reports_rejected_by_human}</b><span>REJECTED BY A PERSON</span></div>
        <div class="metric"><b>${g.data.auto_rejected_reports}</b><span>AUTO-REJECTED</span>
          <small>Must always be zero — no automated path may reject a report</small></div>
      </div>
      <p class="tiny dim" style="margin-top:8px">Operators seen: ${h.operators_seen.length ? h.operators_seen.map(esc).join(', ') : 'none yet'}</p>

      <div class="section-title">Thresholds in use</div>
      <div class="table-wrap"><table class="table"><tbody>
        ${Object.entries(g.thresholds).map(([key, value]) =>
          `<tr><td>${esc(titleCase(key))}</td><td class="num"><b>${esc(value)}</b></td></tr>`).join('')}
      </tbody></table></div>

      <div class="section-title">Deployment</div>
      <div class="table-wrap"><table class="table"><tbody>
        ${Object.entries(g.service).map(([key, value]) =>
          `<tr><td>${esc(titleCase(key))}</td><td>${esc(value)}</td></tr>`).join('')}
        <tr><td>Reports</td><td>${g.data.reports}</td></tr>
        <tr><td>Process events</td><td>${g.data.process_events}</td></tr>
      </tbody></table></div>`;
  },
};

// --- Shared row rendering --------------------------------------------------

function queueRow(item) {
  const band = severityBand(item.priority.severity);
  const badge = item.pinned ? 'pinned' : item.demoted ? 'demoted' : '';
  return `<div class="ctl-row sev-${band}">
    <span class="rank-badge ${badge}">${item.position}</span>
    <div class="body">
      <div class="headline" data-open="${item.id}">${esc(item.text)}</div>
      <div class="metaline">
        <span class="pill sev-chip sev-${band}">${severityLabel(item.priority.severity)} ${item.priority.severity}</span>
        <span class="pill status-chip status-${esc(item.status)}">${esc(titleCase(item.status))}</span>
        <span class="tiny dim">trust ${item.priority.authenticity} · ageing +${item.priority.ageing_bonus} · priority <b>${item.priority_score}</b></span>
        <span class="tiny dim">waiting ${minutesLabel(item.priority.minutes_waiting)}</span>
        ${item.pinned ? '<span class="pill status-queued">pinned</span>' : ''}
        ${item.demoted ? '<span class="pill status-rejected">demoted</span>' : ''}
      </div>
    </div>
    <div class="actions">
      ${item.pinned || item.demoted
        ? `<button class="btn btn-sm btn-ghost" data-override="clear" data-id="${item.id}">Clear</button>`
        : `<button class="btn btn-sm btn-gold" data-override="pin" data-id="${item.id}">Pin</button>
           <button class="btn btn-sm btn-ghost" data-override="demote" data-id="${item.id}">Demote</button>`}
      <button class="btn btn-sm btn-ghost" data-assign="${item.id}">Assign</button>
    </div>
  </div>`;
}

function bindQueueRows() {
  bindOpen();
  $$('[data-override]').forEach((btn) => {
    btn.addEventListener('click', () => override(btn.dataset.id, btn.dataset.override));
  });
  $$('[data-assign]').forEach((btn) => {
    btn.addEventListener('click', () => assign({ reportId: btn.dataset.assign }));
  });
}

function bindOpen() {
  $$('[data-open]').forEach((node) => {
    node.addEventListener('click', (e) => { e.preventDefault(); openDrawer(node.dataset.open); });
  });
}

// --- Actions ---------------------------------------------------------------

async function override(reportId, action) {
  let reason = null;
  if (action !== 'clear') {
    reason = prompt(`Why are you ${action === 'pin' ? 'pinning' : 'demoting'} this report?`, '');
    if (reason === null) return;
  }
  try {
    const result = await api.override(reportId, { action, operator: operator.name, reason });
    toast(`Moved from position ${result.previous_position ?? '—'} to ${result.position ?? '—'}`, 'ok');
    render();
  } catch (err) {
    toast(err.message, 'err');
  }
}

async function assign({ reportId } = {}) {
  try {
    const result = await api.assign({ reportId, operator: operator.name });
    if (result.outcome === 'assigned') {
      toast(`Assigned to ${result.responder.name} (${result.match.distance_km} km)`, 'ok');
    } else if (result.outcome === 'deferred') {
      toast(result.reason || 'Deferred — no crew available', 'err');
    } else {
      toast('The queue is empty');
    }
    render();
  } catch (err) {
    toast(err.message, 'err');
  }
}

async function assignMany(limit) {
  let assigned = 0;
  for (let i = 0; i < limit; i += 1) {
    try {
      const result = await api.assign({ operator: operator.name });
      if (result.outcome !== 'assigned') break;
      assigned += 1;
    } catch {
      break;
    }
  }
  toast(assigned ? `${assigned} report${assigned > 1 ? 's' : ''} assigned` : 'Nothing could be assigned', assigned ? 'ok' : 'err');
  render();
}

async function review(reportId, decision) {
  const note = prompt(`Note for this ${decision} decision (optional)`, '');
  if (note === null) return;
  try {
    await api.reviewReport(reportId, { decision, reviewer: operator.name, note: note || undefined });
    toast(decision === 'verify' ? 'Verified and queued' : 'Rejected — the report is kept on record', 'ok');
    render();
  } catch (err) {
    toast(err.message, 'err');
  }
}

// --- Case drawer -------------------------------------------------------------

async function openDrawer(reportId) {
  const drawer = $('#drawer');
  drawer.hidden = false;
  $('#drawerBody').innerHTML = '<div class="stack"><div class="skeleton"></div><div class="skeleton"></div></div>';

  try {
    const [report, events] = await Promise.all([
      api.getReport(reportId),
      api.events({ case_id: reportId, limit: 200 }),
    ]);
    const band = severityBand(report.severity_score);

    const restricted = !operator.seesEverything && !(await myCaseIds()).has(reportId);

    $('#drawerTitle').textContent = titleCase(report.incident_type || 'Report');
    $('#drawerBody').innerHTML = `
      ${restricted ? `<div class="restricted" style="margin-bottom:14px">
        <div class="lock-line">🔒 Not assigned to you</div>
        You are signed in as <b>${esc(operator.name)}</b> (${esc(ROLES[operator.role].label)}).
        A Control Officer normally works only their own incidents. Details are still shown
        because the backend has no authorisation layer to enforce this — that is a gap, not a
        feature.
      </div>` : `<div class="lock-line">🔒 Authorised access · assigned to ${esc(operator.name)}</div>`}

      ${aiPanel(report)}

      <div class="ctl-row sev-${band}" style="margin-bottom:14px">
        <div class="body">
          <p style="font-size:15px;line-height:1.5">${esc(report.text)}</p>
          <div class="metaline">
            <span class="pill sev-chip sev-${band}">Severity ${report.severity_score}</span>
            <span class="pill status-chip status-${esc(report.status)}">${esc(titleCase(report.status))}</span>
            <span class="pill status-chip">Trust ${report.authenticity_score ?? '—'}</span>
          </div>
          <p class="tiny dim" style="margin-top:8px">${esc(report.reporter_pseudonym)} · filed ${ago(report.client_created_at)} · scored by ${esc(report.scoring_provider || '—')}</p>
        </div>
      </div>

      ${reasonTable('Severity', report.severity_reasons)}
      ${reasonTable('Trust', report.authenticity_reasons)}

      <div class="section-title">Process trail (${events.total})</div>
      <div>${events.items.map((event) => `<div class="log-line">
        <time>${parseUtc(event.timestamp)?.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time>
        <div><b>${esc(event.activity)}</b><div class="tiny dim">${esc(event.resource)}</div></div>
      </div>`).join('')}</div>`;
  } catch (err) {
    $('#drawerBody').innerHTML = `<div class="empty">${esc(err.message)}</div>`;
  }
}

/* AI insights, control-room only.
 *
 * The citizen app deliberately shows none of this: reason codes and confidence
 * are internal reasoning. An operator needs them to decide, and needs to see
 * clearly which part is the model's opinion and which part is theirs. */
function aiPanel(report) {
  const risk = (report.severity_reasons || [])
    .filter((r) => r.code.startsWith('LIFE_RISK_') || r.code.startsWith('VULNERABILITY_') || r.code.startsWith('PEOPLE_'))
    .map((r) => reasonLabel(r.code));

  const humanReview = (report.authenticity_reasons || []).find((r) => r.code.startsWith('HUMAN_REVIEW'));
  const aiVerdict = severityLabel(report.severity_score).toUpperCase();
  const humanVerdict = humanReview
    ? (humanReview.code.endsWith('VERIFIED') ? 'CONFIRMED' : 'REJECTED')
    : report.status === 'flagged' ? 'PENDING REVIEW' : 'NOT REQUIRED';

  return `<div class="ai-panel">
    <div class="ai-head">AI assessment</div>
    <div class="ai-scores">
      <div class="ai-score"><b style="color:var(--sev-${severityBand(report.severity_score)})">${report.severity_score ?? '—'}</b><span>SEVERITY</span></div>
      <div class="ai-score"><b>${report.authenticity_score ?? '—'}</b><span>AUTHENTICITY</span></div>
      <div class="ai-score"><b>${esc(report.scoring_provider || '—')}</b><span>SCORED BY</span></div>
    </div>
    ${risk.length ? `<div style="margin-top:12px">${risk.map((r) => `<div class="ai-factor">${esc(r)}</div>`).join('')}</div>` : ''}
    <div class="decision-split">
      <div class="decision ai"><span>AI recommends</span><b>${esc(aiVerdict)}</b></div>
      <div class="decision human"><span>Human decision</span><b>${esc(humanVerdict)}</b></div>
    </div>
  </div>`;
}

function reasonTable(title, reasons) {
  if (!reasons || !reasons.length) return '';
  const total = reasons.reduce((sum, r) => sum + r.weight, 0);
  return `<div class="section-title">${title} — why</div>
    <div class="table-wrap"><table class="table"><tbody>
      ${reasons.map((r) => `<tr>
        <td><b>${esc(r.code)}</b><div class="tiny dim">${esc(reasonLabel(r.code))} · ${esc(r.source)}</div></td>
        <td class="num" style="color:${r.weight >= 0 ? 'var(--accent-green)' : 'var(--danger)'}"><b>${r.weight >= 0 ? '+' : ''}${r.weight}</b></td>
      </tr>`).join('')}
      <tr><td><b>Total</b></td><td class="num"><b>${total}</b></td></tr>
    </tbody></table></div>`;
}

// --- Boot ---------------------------------------------------------------------

async function checkHealth() {
  try {
    const health = await api.health();
    $('#ctlStatus').textContent = `${health.environment} · database ${health.database.status} · v${health.version}`;
  } catch {
    $('#ctlStatus').textContent = `Cannot reach ${api.base}`;
  }
}

function paintOperator() {
  $('#operatorName').textContent = operator.name;
  const badge = $('#roleBadge');
  badge.textContent = ROLES[operator.role].label;
  badge.className = `role-badge ${operator.role}`;
}

function showSignIn() {
  const gate = $('#signin');
  gate.hidden = false;
  $('#signinName').value = operator.name || '';
  $('#signinRole').value = operator.role;

  const enter = () => {
    const name = $('#signinName').value.trim();
    if (!name) { $('#signinName').focus(); return; }
    operator.name = name;
    operator.role = $('#signinRole').value;
    gate.hidden = true;
    paintOperator();
    checkHealth();
    render();
  };
  $('#signinGo').addEventListener('click', enter);
  $('#signinName').addEventListener('keydown', (e) => { if (e.key === 'Enter') enter(); });
}

function boot() {
  // Bind everything that outlives the sign-in gate *before* the gate, or these
  // handlers never attach on a first visit and the drawer becomes impossible
  // to close.
  $('#signOut').addEventListener('click', () => {
    operator.signOut();
    window.location.reload();
  });
  $('#refreshAll').addEventListener('click', () => { checkHealth(); render(); });
  $('#drawerClose').addEventListener('click', () => { $('#drawer').hidden = true; });
  $('#drawer').addEventListener('click', (e) => {
    if (e.target.id === 'drawer') $('#drawer').hidden = true;
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') $('#drawer').hidden = true;
  });

  if (!operator.signedIn) {
    showSignIn();
    return;
  }
  paintOperator();

  checkHealth();
  render();
  setInterval(() => { if (state.tab === 'overview') render(); }, 30000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}
