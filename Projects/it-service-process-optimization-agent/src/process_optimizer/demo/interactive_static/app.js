"use strict";
// Interactive LOCAL demo client. It renders server responses only: it computes no metric,
// and it shows success only after the backend has confirmed it. Tokens are synthetic,
// random per launch, kept in memory and sent only in the Authorization header.

const S = {
  session: null,
  role: "analyst",
  tab: "evidence",
  scenario: "group_returns",
  modelMode: "deterministic", // explicit opt-in to "ollama"; never switched automatically
  modelStatus: null, // last /demo/v2/model/status response
  investigation: null, // last /demo/v2/investigations response
  submitKey: null, // idempotency key of the current draft's submission
  submitCorrelation: null,
  selectedApproval: null,
  lastDecision: null, // {path, body, label}
  handoffFindings: [],
};

// ------------------------------------------------------------------ plain-language codes
const REASONS = {
  UNSUPPORTED_CAUSAL_STATEMENT: "States a cause-and-effect relationship. The evidence shows only an observed association.",
  UNSUPPORTED_CAUSAL_CONCLUSION: "A hypothesis is presented as a causal conclusion.",
  UNSUPPORTED_CAUSAL_ATTRIBUTION: "Attributes an outcome to a cause that no cited finding supports.",
  UNSUPPORTED_INTERVENTION_CLAIM: "Claims what a change will achieve, without a cited simulation to back it.",
  UNQUALIFIED_EXPECTED_OUTCOME: "States the expected outcome as certain instead of with uncertainty.",
  UNSUPPORTED_SAVINGS_CLAIM: "Claims savings that the evidence does not support.",
  UNSUPPORTED_INEFFICIENCY_CLAIM: "Labels an observed pattern as inefficiency without supporting evidence.",
  UNSUPPORTED_TIMING_EFFECT: "Claims a time effect that no cited finding supports.",
  UNSUPPORTED_QUANTIFIED_EFFECT: "Claims a quantified effect that no cited finding supports.",
  INVALID_EVIDENCE_ID: "Cites an evidence ID that does not exist in the verified evidence contract.",
  MISSING_EVIDENCE: "Cites no evidence.",
  INVALID_NUMERICAL_CITATION: "A cited number does not resolve to the cited evidence field.",
  FABRICATED_OR_MISMATCHED_NUMERICAL_CLAIM: "A number differs from the value in the cited evidence.",
  UNSUPPORTED_NUMERICAL_CLAIM_IN_TEXT: "The text contains a number that no cited evidence supports.",
  AMBIGUOUS_NUMERICAL_CLAIM_IN_TEXT: "The text contains a number in ambiguous notation.",
  NUMERICAL_FACT_STATEMENT_MISMATCH: "A fact's statement does not state that fact's own value.",
  SIMULATION_ASSUMPTION_MISMATCH: "The cited simulation assumptions do not match the simulation.",
  MISSING_SIMULATION_CITATION: "An effect claim needs a cited simulation.",
  INVALID_SIMULATION_CITATION: "The cited simulation is invalid.",
  SIMULATION_NOT_ESTIMABLE: "The cited simulation is not estimable.",
  COMPLETED_LIFECYCLE_SEMANTIC_TRAP: "Treats repeated 'Completed' events as rework, which the data does not establish.",
  MISSING_DATA_LIMITATIONS: "Data limitations are missing.",
  HUMAN_APPROVAL_NOT_REQUIRED: "The recommendation does not require human approval.",
  HUMAN_IDENTITY_REQUIRED: "Only a human identity may perform this action.",
  INPUT_INJECTION_PATTERN: "The goal contains an instruction-injection pattern.",
  INPUT_TOO_LONG: "The goal is too long.",
  INPUT_FORBIDDEN_CHARACTER: "The goal contains hidden or control characters.",
  INPUT_ENCODED_PAYLOAD: "The goal contains a long encoded payload.",
  UNKNOWN_VALIDATOR_ERROR_CODE: "The validator returned an unknown code, so the guardrail fails closed.",
  VALIDATION_FAILED: "Deterministic validation failed.",
};
const HTTP_CODES = {
  AUTHENTICATION_REQUIRED: "No valid identity was presented.",
  INSUFFICIENT_ROLE: "This role is not allowed to perform the action.",
  INSUFFICIENT_PERMISSION: "This role lacks the permission for the action.",
  SELF_APPROVAL_PROHIBITED: "Separation of duties: you cannot decide on a recommendation you submitted yourself, even if you hold the Process Owner role.",
  HUMAN_IDENTITY_REQUIRED: "Only a human identity may perform this action.",
  GUARDRAIL_BLOCKED: "The deterministic guardrails blocked the recommendation. Nothing was stored for approval.",
  RECOMMENDATION_NOT_VALIDATED: "The deterministic validator rejected the recommendation. Nothing was stored for approval.",
  REVISION_REQUIRED: "The current policy no longer accepts this recommendation. Nothing was executed and no ticket was created.",
  WORKFLOW_CONFLICT: "Conflict: the request repeats or contradicts an earlier decision.",
  INVALID_STATE_TRANSITION: "This decision is not possible in the request's current state.",
  RESOURCE_ACCESS_DENIED: "This request is outside your scope.",
  NOT_FOUND: "Not found.",
  INPUT_GUARDRAIL_BLOCKED: "The input guardrail blocked the investigation goal before any processing.",
  APPROVAL_NOT_PENDING: "Only a pending request can be used for this demonstration.",
  SCHEMA_VALIDATION_ERROR: "The server rejected the input as invalid (schema validation).",
  LOCAL_MODEL_UNAVAILABLE: "The local Ollama model is not available. Nothing was generated, and no mock output was substituted.",
  LOCAL_MODEL_BUSY: "A local-model investigation is already running. Wait for it to finish.",
  MODEL_PROVENANCE_MISMATCH: "A reply came from a different model than the one selected, so the result was discarded.",
  INVESTIGATION_UNAVAILABLE: "The investigation could not run.",
};
const GRAPH_ERRORS = {
  GUARDRAIL_BLOCKED: "A deterministic guardrail blocked a step (for example a tool not allowed in that stage).",
  TOOL_BUDGET_EXHAUSTED: "The model requested more tool calls than the run budget allows.",
  ITERATION_LIMIT_REACHED: "The investigation iteration budget was exhausted.",
  MODEL_CALL_LIMIT_REACHED: "The per-run model-call limit was reached.",
  MODEL_TOKEN_BUDGET_EXHAUSTED: "The per-run token budget was exhausted.",
  RUN_DEADLINE_EXCEEDED: "The run deadline was exceeded.",
  PROVIDER_TIMEOUT: "A model call exceeded the per-call timeout.",
  MODEL_OUTPUT_INVALID: "The model returned malformed or schema-invalid output; it was discarded.",
  EVIDENCE_MISSING: "The model selected no trusted evidence finding.",
  INCOMPLETE_INVESTIGATION: "The model ended without a usable plan or recommendation.",
  TOOL_ARGUMENT_INVALID: "A tool request failed argument validation.",
};

// ---------------------------------------------------------------------------- DOM utils
const $ = (id) => document.getElementById(id);
function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}
function put(target, ...nodes) { (typeof target === "string" ? $(target) : target).replaceChildren(...nodes.flat(Infinity).filter((n) => n !== null && n !== undefined && n !== false)); }
const badge = (text, kind = "") => el("span", { class: `badge ${kind}`, text });
const code = (text) => el("code", { text });
function kv(pairs) { return el("dl", { class: "kv" }, pairs.filter(Boolean).map(([k, v]) => [el("dt", { text: k }), el("dd", {}, v)])); }
function source(...parts) { return el("div", { class: "source" }, "Source: ", parts); }
function outcome(kind, title, ...body) { return el("div", { class: `outcome ${kind}`, role: kind === "bad" ? "alert" : "status" }, el("div", { class: "title", text: title }), body); }
function reasonList(codes, dict = REASONS) {
  if (!codes || !codes.length) return null;
  return el("ul", {}, codes.map((c) => el("li", {}, code(c), " ", dict[c] || HTTP_CODES[c] || "")));
}

// ---------------------------------------------------------------------- number formats
// Unit conversions of server values only; the raw value is kept in the tooltip.
const fmtInt = (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString("en-US"));
const fmtPct = (r) => (r === null || r === undefined ? "—" : `${(r * 100).toFixed(1)} %`);
function fmtDur(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const s = Number(seconds);
  if (s < 3600) return `${(s / 60).toFixed(1)} min`;
  if (s < 86400) return `${(s / 3600).toFixed(1)} h`;
  return `${(s / 86400).toFixed(1)} days`;
}
const dur = (seconds) => el("span", { title: `${seconds} seconds (server value)`, text: fmtDur(seconds) });
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString("en-GB") : "—");
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)).replace(/[^A-Za-z0-9-]/g, "");

// ------------------------------------------------------------------------------- API
function identity() { return S.session.identities.find((i) => i.key === S.role); }
function codeOf(body) {
  const d = body && body.detail;
  if (d && typeof d === "object" && !Array.isArray(d)) return d.code;
  if (Array.isArray(d)) return "SCHEMA_VALIDATION_ERROR";
  return null;
}
async function api(method, path, body, { auth = true } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const who = auth && S.session ? identity() : null;
  if (who) headers.Authorization = `Bearer ${who.token}`;
  let status = 0, data = null;
  try {
    const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), cache: "no-store" });
    status = res.status;
    const text = await res.text();
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  } catch (err) {
    status = 0; data = { detail: { code: "NETWORK_ERROR" } };
  }
  logCall(who ? who.label : "anonymous", method, path, status, codeOf(data));
  return { status, ok: status >= 200 && status < 300, body: data, code: codeOf(data) };
}
function logCall(who, method, path, status, c) {
  const cls = status >= 500 || status === 0 ? "s5" : status >= 400 ? "s4" : "s2";
  const li = el("li", {}, el("span", { class: "muted", text: new Date().toLocaleTimeString("en-GB") + "  " }),
    el("span", { class: "who", text: `[${who}] ` }), `${method} ${path} → `, el("span", { class: cls, text: String(status || "ERR") }), c ? ` ${c}` : "");
  $("apilog").prepend(li);
}
function httpProblem(res, what) {
  const c = res.code;
  const detail = res.body && res.body.detail;
  const extra = detail && detail.reason_codes ? reasonList(detail.reason_codes) : null;
  const kind = res.status === 403 || res.status === 401 ? "warn" : "bad";
  return outcome(kind, `${what}: server answered ${res.status}${c ? " " + c : ""}`, el("p", { text: HTTP_CODES[c] || "The request was refused." }), extra);
}

// ------------------------------------------------------------------------ session/roles
async function loadSession() {
  const res = await api("GET", "/demo/v2/session", undefined, { auth: false });
  if (!res.ok) {
    document.body.prepend(el("div", { class: "banner warn", text: `The demo session is unavailable (${res.status} ${res.code || ""}). The role switcher exists only in APP_ENV=local.` }));
    return false;
  }
  S.session = res.body;
  const ai = (S.session.model_options || []).find((m) => m.real_ai);
  if (ai) $("legend-model").textContent = ai.model;
  // Provenance comes from the served evidence contract, never from this page.
  const ds = S.session.dataset || {};
  const dsLabel = ds.synthetic ? `${ds.name} (synthetic public demo data)` : (ds.name || "unknown dataset");
  $("legend-dataset").textContent = dsLabel;
  $("evidence-dataset").textContent = dsLabel;
  renderHeader();
  renderRoles();
  renderSafety();
  return true;
}
function renderHeader() {
  const i = S.session.integrity;
  put("mode-badges",
    badge("LOCAL DEMO", ""),
    S.session.dataset && S.session.dataset.synthetic ? badge("SYNTHETIC DATA · NOT BPI 2013", "warn") : null,
    modelBadge(),
    badge(i.evidence_pin_matches ? "EVIDENCE PIN ✓" : "EVIDENCE PIN ✗", i.evidence_pin_matches ? "ok" : "bad"),
    badge(i.policy_mode === "PINNED" ? `POLICY ${i.policy_schema_version} PINNED ✓` : "POLICY: SIMULATED STRICTER", i.policy_mode === "PINNED" ? "ok" : "bad"),
    badge("NO AZURE · NO PAID MODEL", ""));
  const banner = $("policy-banner");
  if (i.policy_mode !== "PINNED") {
    banner.textContent = `SIMULATED POLICY UPGRADE ACTIVE (demo only, not packaged): policy ${i.policy_version.slice(0, 12)}… differs from the pinned 5a886139…. Submissions and approvals are checked against it until an Admin restores the pinned policy.`;
    banner.classList.remove("hidden");
  } else banner.classList.add("hidden");
}
function modelOption(key) { return (S.session.model_options || []).find((m) => m.key === key); }
function modelBadge() {
  const m = modelOption(S.modelMode);
  if (!m || !m.real_ai) return badge("MODEL: DETERMINISTIC MOCK · NOT REAL AI", "warn");
  return badge(`MODEL: REAL LOCAL AI · ${m.model} · REAL_MODEL_OUTPUT`, "info");
}
function renderRoles() {
  put("roles", S.session.identities.map((i) => el("button", {
    class: "role", role: "radio", "aria-checked": String(i.key === S.role), title: i.description,
    onclick: () => { S.role = i.key; renderRoles(); refreshTab(); },
  }, el("span", { class: "name", text: i.label }), el("span", { class: "actor", text: `${i.actor_id} · ${i.roles.join(" + ")}` }))));
}

// -------------------------------------------------------------------------------- tabs
function showTab(tab) {
  S.tab = tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll("main > .panel").forEach((p) => p.classList.toggle("hidden", p.id !== `tab-${tab}`));
  refreshTab();
}
function refreshTab() {
  ({ evidence: loadEvidence, whatif: loadWhatIfFindings, approvals: loadApprovals, tickets: loadTickets, audit: loadAudit, safety: refreshSession })[S.tab]?.();
}
async function refreshSession() {
  const res = await api("GET", "/demo/v2/session", undefined, { auth: false });
  if (res.ok) { S.session = res.body; renderHeader(); renderSafety(); }
}

// ----------------------------------------------------------------------------- evidence
const EVIDENCE_CARDS = ["kpis", "card-cohort", "card-activities", "card-bottlenecks", "card-handoffs", "card-transitions", "card-variants", "card-provenance"];
async function loadEvidence() {
  put("evidence-error");
  const [overview, cohort, bottlenecks, handoffs, transitions, provenance, association, handoffFinding] = await Promise.all([
    api("GET", "/aggregate/v1/process-overview"),
    api("POST", "/aggregate/v1/cohort-comparison", { metric: $("cohort-metric")?.value || "observed_case_span_seconds" }),
    api("POST", "/aggregate/v1/bottleneck-findings", { metric_name: "high_duration_directly_follows_transition", limit: 20 }),
    api("POST", "/aggregate/v1/group-handoffs", { limit: 10 }),
    api("POST", "/aggregate/v1/lifecycle-patterns", { limit: 10 }),
    api("GET", "/demo/v2/evidence/provenance"),
    api("POST", "/aggregate/v1/bottleneck-findings", { metric_name: "observed_span_association_with_group_returns", limit: 5 }),
    api("POST", "/aggregate/v1/bottleneck-findings", { metric_name: "high_frequency_or_repeated_group_handoff", limit: 1 }),
  ]);
  if (!overview.ok) {
    EVIDENCE_CARDS.forEach((id) => put(id));
    put("evidence-error", httpProblem(overview, `${identity().label} reading process evidence`));
    return;
  }
  renderKpis({ association, bottlenecks, handoffFinding, cohort });
  renderCohort(cohort, association);
  renderActivities(overview.body);
  renderBottlenecks(bottlenecks);
  renderHandoffs(handoffs);
  renderTransitions(transitions);
  renderVariants();
  renderProvenance(provenance);
}
function kpi(label, value, hint) { return el("div", { class: "kpi" }, el("div", { class: "label", text: label }), el("div", { class: "value" }, value), el("div", { class: "hint", text: hint })); }
// Headline figures come from validated evidence findings that pass suppression (the
// process-overview record itself is withheld by the server; see renderActivities).
function renderKpis({ association, bottlenecks, handoffFinding, cohort }) {
  const first = (res) => (res && res.ok ? res.body.result.items[0] : null);
  const a = first(association), b = first(bottlenecks), h = first(handoffFinding);
  void cohort;
  put("kpis",
    a ? kpi("Incidents (cases) analysed", fmtInt(a.counts.denominator), `finding ${a.finding_id.slice(8, 20)}… · counts.denominator`) : null,
    a ? kpi("Incidents with a return to an earlier group", fmtInt(a.counts.numerator), "same finding · counts.numerator") : null,
    a ? kpi("Median observed span, with return", dur(a.measured_statistics.with_return_span_median_seconds), "first to last event; not resolution time") : null,
    a ? kpi("Median observed span, without return", dur(a.measured_statistics.without_return_span_median_seconds), `ratio ${Number(a.measured_statistics.span_median_ratio).toFixed(2)} · association, not cause`) : null,
    h ? kpi("Group handoff events", fmtInt(h.counts.denominator), "consecutive org:group changes · handoff findings' denominator") : null,
    b ? kpi("Consecutive-event transitions", fmtInt(b.counts.denominator), "bottleneck findings' denominator") : null);
}
const COHORT_METRICS = [
  ["observed_case_span_seconds", "Observed case span", true],
  ["event_count", "Events per incident", false],
  ["group_change_event_count", "Group handoffs per incident", false],
  ["repeated_activity_event_count", "Repeated activity names (not confirmed rework)", false],
  ["immediate_group_reversal_event_count", "Immediate group reversals (A → B → A)", false],
];
function renderCohort(res, association) {
  const card = $("card-cohort");
  const current = $("cohort-metric")?.value || "observed_case_span_seconds";
  const select = el("select", { id: "cohort-metric", onchange: loadEvidence }, COHORT_METRICS.map(([v, l]) => el("option", { value: v, selected: v === current, text: l })));
  if (!res.ok) { put(card, el("h3", { text: "Group returns: cohort comparison" }), httpProblem(res, "cohort-comparison")); return; }
  const item = res.body.result.items[0];
  const isTime = COHORT_METRICS.find((m) => m[0] === item.metric)?.[2];
  const fmt = (v) => (isTime ? dur(v) : fmtInt(v));
  const label = { with_group_return: "With a group return", without_group_return: "Without a group return" };
  const diff = item.distributional_difference;
  const finding = association.ok ? association.body.result.items[0] : null;
  put(card,
    el("h3", { text: "Group returns: cohort comparison" }),
    el("p", { class: "muted small", text: item.cohort_definition }),
    el("label", { class: "field" }, "Metric", select),
    el("div", { class: "cohorts" }, item.cohorts.map((c) => el("div", { class: "cohort" },
      el("div", { class: "eyebrow", text: label[c.cohort] || c.cohort }),
      el("div", { class: "big" }, fmt(c.distribution.median)), el("div", { class: "small muted", text: "median" }),
      el("div", { class: "small" }, "p75 ", fmt(c.distribution.p75), " · p90 ", fmt(c.distribution.p90)),
      el("div", { class: "small muted", text: `${fmtInt(c.incident_count)} incidents` })))),
    el("p", { class: "small" }, "Median difference ", fmt(diff.median_difference), diff.median_ratio ? ` · median ratio ${Number(diff.median_ratio).toFixed(2)}` : " · median ratio not defined"),
    outcome("info", `Classification: ${item.classification}`, el("p", { class: "small", text: "An observed association between cohorts. It does not show that group returns cause longer spans." })),
    source(code("POST /aggregate/v1/cohort-comparison"), finding ? [" · evidence ", code(finding.finding_id)] : ""));
}
function bars(rows, valueKey, labelKey, alt) {
  const max = Math.max(...rows.map((r) => r[valueKey]));
  return el("div", { class: "bars" }, rows.map((r) => {
    const fill = el("div", { class: `bar-fill ${alt ? "alt" : ""}` });
    fill.style.width = `${(100 * r[valueKey]) / max}%`;
    return el("div", { class: "bar-row" }, el("span", { text: r[labelKey] }), el("div", { class: "bar-track" }, fill), el("span", { class: "v", text: fmtInt(r[valueKey]) }));
  }));
}
function renderActivities(ov) {
  const o = ov.result.items[0];
  if (!o) {
    // The server returned zero items: item-level small-group suppression withheld the
    // whole overview record. The demo never bypasses that.
    put("card-activities",
      el("h3", { text: "Dataset overview: total events, activity and lifecycle mix" }),
      outcome("warn", "Withheld by the server's privacy suppression",
        el("p", { class: "small", text: `The process-overview tool returned ${ov.result.returned_count} records. The overview contains at least one count below the small-group threshold, and suppression works per record, so the whole record is withheld. Total event count and activity frequencies are therefore not shown, and the page does not reconstruct them.` })),
      el("p", { class: "small muted", text: "Headline figures above come from validated evidence findings, which pass the same suppression." }),
      source(code("GET /aggregate/v1/process-overview"), " → returned_count 0"));
    return;
  }
  put("card-activities",
    el("h3", { text: "Activities and lifecycle states (event counts)" }),
    bars(o.activity_frequencies, "event_count", "activity"),
    el("h3", { class: "small", text: " " }),
    bars(o.lifecycle_frequencies.slice(0, 8), "event_count", "lifecycle", true),
    el("p", { class: "small muted", text: lifecycleCaption(o.lifecycle_frequencies.length, 8) }),
    source(code("GET /aggregate/v1/process-overview")));
}
function lifecycleCaption(total, limit) {
  if (total <= limit) return `Showing all ${total} lifecycle ${total === 1 ? "state" : "states"}, in server order.`;
  return `Showing the first ${limit} of ${total} lifecycle states, in server order.`;
}
function renderBottlenecks(res) {
  if (!res.ok) { put("card-bottlenecks", el("h3", { text: "Bottleneck candidates" }), httpProblem(res, "bottleneck-findings")); return; }
  const rows = res.body.result.items;
  if (!rows.length) {
    // An empty result is an analysis outcome, not an error: no transition met the rule.
    put("card-bottlenecks",
      el("h3", { text: "Bottleneck candidates: slow directly-follows transitions" }),
      outcome("info", "No slow transition met the bottleneck rule in this package",
        el("p", { class: "small", text: "The process-mining pipeline flags a directly-follows transition only if it has enough observations and its median or p90 wait is clearly above the median or p90 of all transitions (defaults: at least 30 observations, 2× the median or 1.5× the p90). Any candidate must also pass small-group suppression. In this package no transition qualified, so there is nothing to list. The page does not lower the rule to show something." })),
      source(code("POST /aggregate/v1/bottleneck-findings"), " · metric ", code("high_duration_directly_follows_transition"), ` → ${rows.length} findings`));
    return;
  }
  put("card-bottlenecks",
    el("h3", { text: "Bottleneck candidates: slow directly-follows transitions" }),
    el("p", { class: "muted small", text: rows[0]?.metric.definition || "" }),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Process step", "Transitions", "Median wait", "p90 wait", "Median vs. all-transition baseline", "Evidence ID"].map((h, i) => el("th", { class: i && i < 5 ? "num" : "", text: h })))),
      el("tbody", {}, rows.map((f) => el("tr", {},
        el("td", { text: f.aggregate_population.filters.process_step }),
        el("td", { class: "num", text: fmtInt(f.counts.observation_count) }),
        el("td", { class: "num" }, dur(f.measured_statistics.elapsed_seconds_median)),
        el("td", { class: "num" }, dur(f.measured_statistics.elapsed_seconds_p90)),
        el("td", { class: "num", text: `× ${Number(f.measured_statistics.median_to_baseline_ratio).toFixed(1)}` }),
        el("td", {}, code(f.finding_id))))))),
    el("p", { class: "small muted", text: "Candidates under a transparent rule, not confirmed bottlenecks. The 'Completed → Completed' step is a lifecycle pattern (Resolved → Closed), not rework." }),
    source(code("POST /aggregate/v1/bottleneck-findings"), " · metric ", code("high_duration_directly_follows_transition")));
}
function renderHandoffs(res) {
  if (!res.ok) { put("card-handoffs", el("h3", { text: "Group handoffs" }), httpProblem(res, "group-handoffs")); return; }
  const rows = res.body.result.items;
  put("card-handoffs",
    el("h3", { text: "Group handoffs and returns" }),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["From → to group", "Handoffs", "Cases", "Returns to earlier group", "Median wait"].map((h, i) => el("th", { class: i ? "num" : "", text: h })))),
      el("tbody", {}, rows.map((r) => el("tr", {},
        el("td", { text: `${r.source_group} → ${r.target_group}` }),
        el("td", { class: "num", text: fmtInt(r.observation_count) }),
        el("td", { class: "num", text: fmtInt(r.case_count) }),
        el("td", { class: "num", text: `${fmtInt(r.repeated_visit_count)} (${fmtPct(r.repeated_visit_rate)})` }),
        el("td", { class: "num" }, dur(r.elapsed_seconds_median))))))),
    el("p", { class: "small muted", text: `The first ${rows.length} rows in server order; truncated: ${res.body.result.truncated}. Groups below the small-group threshold are suppressed by the server.` }),
    source(code("POST /aggregate/v1/group-handoffs")));
}
function renderTransitions(res) {
  if (!res.ok) { put("card-transitions", el("h3", { text: "Directly-follows transitions" }), httpProblem(res, "lifecycle-patterns")); return; }
  const rows = res.body.result.items;
  put("card-transitions",
    el("h3", { text: "Directly-follows transitions (DFG edges with lifecycle and group)" }),
    el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["From", "To", "Count", "Incidents", "Median wait"].map((h, i) => el("th", { class: i > 1 ? "num" : "", text: h })))),
      el("tbody", {}, rows.map((r) => el("tr", {},
        el("td", { text: `${r.source_activity} (${r.source_lifecycle}) @${r.source_group}` }),
        el("td", { text: `${r.target_activity} (${r.target_lifecycle}) @${r.target_group}` }),
        el("td", { class: "num", text: fmtInt(r.event_transition_count) }),
        el("td", { class: "num", text: fmtInt(r.incident_count) }),
        el("td", { class: "num" }, dur(r.elapsed_seconds_median))))))),
    el("p", { class: "small muted", text: `The first ${rows.length} rows in server order; truncated: ${res.body.result.truncated}.` }),
    source(code("POST /aggregate/v1/lifecycle-patterns")));
}
function renderVariants() {
  put("card-variants",
    el("h3", { text: "Process variants" }),
    outcome("warn", "Not available in the verified evidence package",
      el("p", { class: "small", text: "The verified aggregate package (six artifacts, see provenance) contains no process-variant artifact, and no aggregate tool returns variants. To avoid fabricating them, the demo shows none." })));
}
function renderProvenance(res) {
  if (!res.ok) { put("card-provenance", el("h3", { text: "Provenance and suppression" }), httpProblem(res, "provenance")); return; }
  const p = res.body;
  put("card-provenance",
    el("h3", { text: "Provenance and suppression" }),
    kv([
      ["Analysis run", code(p.analysis_run_id)],
      ["Dataset", `${p.source_dataset.name}`],
      ["Manifest", badge(p.manifest_status, "ok")],
      ["Interpretation", code(p.contract_interpretation)],
      ["Findings in contract", fmtInt(p.suppression.findings_in_contract)],
      ["Visible findings", fmtInt(p.suppression.findings_visible)],
      ["Withheld (small groups)", fmtInt(p.suppression.findings_withheld)],
    ]),
    el("p", { class: "small muted", text: p.suppression.rule }),
    el("details", {}, el("summary", { class: "small", text: "Artifact hashes" }),
      el("ul", { class: "small" }, p.artifacts.map((a) => el("li", {}, `${a.filename} `, code(a.sha256.slice(0, 16) + "…"))))),
    source(code("GET /demo/v2/evidence/provenance")));
}

// -------------------------------------------------------------------------- investigate
const SCENARIO_TEXT = {
  group_returns: ["Repeated group returns", "Are incidents that bounce back to an earlier support group associated with longer observed case spans? Includes a what-if citation."],
  completed_lifecycle: ["Long 'Completed → Completed' step", "Investigates the long Completed-to-Completed transition without assuming it is rework."],
};
async function loadModelStatus() {
  const res = await api("GET", "/demo/v2/model/status", undefined, { auth: false });
  S.modelStatus = res.ok ? res.body : { available: false, reason: res.code || "STATUS_UNAVAILABLE" };
  renderModelChoices();
}
function renderModelChoices() {
  put("model-choices", (S.session.model_options || []).map((m) => el("button", {
    class: "choice", role: "radio", "aria-checked": String(m.key === S.modelMode),
    onclick: () => { S.modelMode = m.key; renderModelChoices(); renderHeader(); if (m.real_ai) loadModelStatus(); },
  }, el("b", { text: m.label }), el("span", { class: "small muted", text: m.real_ai ? `${m.model} on ${m.endpoint} · ${m.execution_label}` : `${m.provider} · ${m.execution_label}` }))));
  const st = S.modelStatus;
  if (S.modelMode !== "ollama") { put("model-status", "Deterministic, scripted reasoning steps. Not AI output."); return; }
  if (!st) { put("model-status", "Checking the local Ollama server…"); return; }
  put("model-status", st.available
    ? el("span", {}, badge("LOCAL MODEL READY", "ok"), ` Ollama ${st.ollama_version} · ${st.model} · ${st.parameter_size || "?"} · ${st.quantization_level || "?"} · digest ${String(st.digest || "").slice(0, 12)}… · loopback only, no download. A run takes about 1 minute.`)
    : el("span", {}, badge("LOCAL MODEL UNAVAILABLE", "bad"), ` ${st.reason}. Running it will fail visibly; the mock is never substituted.`));
}
function renderInvestigateForm() {
  renderModelChoices();
  put("scenario-choices", S.session.scenarios.map((s) => el("button", {
    class: "choice", role: "radio", "aria-checked": String(s.key === S.scenario),
    onclick: () => { S.scenario = s.key; $("goal").value = s.default_goal; renderInvestigateForm(); },
  }, el("b", { text: SCENARIO_TEXT[s.key]?.[0] || s.key }), el("span", { class: "small muted", text: SCENARIO_TEXT[s.key]?.[1] || "" }))));
  if (!$("goal").value) $("goal").value = S.session.scenarios.find((s) => s.key === S.scenario).default_goal;
  if (!$("variant").options.length) {
    put("variant", S.session.variants.map((v) => el("option", { value: v.key, text: `${v.label}  —  expected: ${v.expectation}` })));
  }
}
async function runInvestigation(button) {
  const variant = $("variant").value;
  const body = { scenario: S.scenario, goal: $("goal").value.trim() || null, variant, model_mode: S.modelMode };
  if (variant === "custom") body.custom_problem_statement = $("custom-text").value.trim() || null;
  button.disabled = true;
  const m = modelOption(S.modelMode);
  put("investigation-result", el("div", { class: "card muted", text: m && m.real_ai ? `Running the real local model ${m.model} through the LangGraph workflow on the server. This usually takes 20–90 seconds…` : "Running on the server…" }));
  const res = await api("POST", "/demo/v2/investigations", body);
  button.disabled = false;
  if (!res.ok) { S.investigation = null; put("investigation-result", httpProblem(res, "Investigation")); return; }
  S.investigation = res.body;
  S.submitKey = `submit-${uuid()}`;
  S.submitCorrelation = res.body.model && res.body.model.real_ai ? `corr-ollama-${uuid()}` : `corr-${uuid()}`;
  renderInvestigation();
}
function renderInvestigation() {
  const r = S.investigation, inv = r.investigation;
  const blocks = [];
  const mi = r.model || { real_ai: false };
  if (mi.real_ai) {
    const inv0 = mi.invocations;
    blocks.push(outcome("ok", `Execution label: ${r.execution_label} · real local AI: ${mi.model}`,
      el("p", { class: "small", text: `The analyst, investigator and optimizer outputs were generated by ${mi.model}, running locally in Ollama ${mi.ollama_version || ""} on ${mi.endpoint}. Every reply was reported by the server as coming from this model.` }),
      kv([["Provider", mi.provider], ["Model digest", code(String(mi.model_digest || "").slice(0, 16) + "…")], ["Size / quantization", `${mi.parameter_size || "?"} · ${mi.quantization_level || "?"}`], ["Genuine model response", badge(String(mi.genuine_model_response), mi.genuine_model_response ? "ok" : "bad")], ["Model calls", `${inv0.model_invocation_attempts} (${inv0.failed_attempts} failed)`], ["Tokens (in / out)", `${fmtInt(inv0.input_tokens)} / ${fmtInt(inv0.output_tokens)}`], ["Local protocol", code(mi.local_protocol_version)]]),
      el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["Agent role", "Reported model", "Latency", "Tokens in / out", "Error"].map((h) => el("th", { text: h })))),
        el("tbody", {}, inv0.calls.map((c) => el("tr", {}, el("td", { text: c.role }), el("td", {}, c.response_model ? code(c.response_model) : "—"), el("td", { text: `${(c.latency_ms / 1000).toFixed(1)} s` }), el("td", { text: `${fmtInt((c.token_usage || {}).input_tokens)} / ${fmtInt((c.token_usage || {}).output_tokens)}` }), el("td", {}, c.error_category ? badge(c.error_category, "bad") : "—"))))))));
  } else {
    blocks.push(outcome("warn", `Execution label: ${r.execution_label}`, el("p", { class: "small", text: "The analyst, investigator and optimizer outputs come from a deterministic mock provider, not a real AI model. The tools, evidence, validator and guardrails are real." })));
  }
  // Step states come from the backend (graph audit events and errors); nothing is assumed.
  const STEP_LOOK = { COMPLETED: ["done", "✓", "completed"], BLOCKED: ["blocked", "⛔", "blocked"], FAILED: ["fail", "✗", "failed"], NOT_EXECUTED: ["skipped", "–", "not executed"] };
  const steps = el("div", {},
    el("div", { class: "flow", role: "list" }, (r.steps || []).map((st) => {
      const [cls, icon, word] = STEP_LOOK[st.state] || STEP_LOOK.NOT_EXECUTED;
      return el("span", { class: `step ${cls}`, role: "listitem", title: st.detail || word, "aria-label": `${st.label}: ${word}${st.detail ? " — " + st.detail : ""}` }, `${icon} ${st.label}`);
    })),
    el("ul", { class: "small step-details" }, (r.steps || []).filter((st) => st.state !== "COMPLETED").map((st) => el("li", {}, el("b", { text: st.label }), `: ${STEP_LOOK[st.state][2]}${st.detail ? " — " + st.detail : ""}`))),
    el("div", { class: "small muted" }, "Legend: ✓ completed · ⛔ blocked by a guardrail or safety limit · ✗ failed · – not executed (the graph stopped earlier)."));
  blocks.push(el("div", { class: "card" }, el("h3", { text: "Run summary" }), steps,
    kv([["Final decision", badge(inv.final_decision, inv.final_decision === "VALIDATED_RECOMMENDATIONS" ? "ok" : "warn")], ["Iterations", String(inv.iterations_used)], ["Tool calls", String(inv.tool_calls_used)], ["Analysis run", code(inv.analysis_run_id)],
      inv.errors.length ? ["Graph stopped because", el("ul", {}, inv.errors.map((e) => el("li", {}, code(e.code), ` (${e.stage}) `, GRAPH_ERRORS[e.code] || e.safe_message)))] : null]),
    el("h3", { text: "Retrieved evidence" }),
    el("ul", {}, inv.selected_findings.map((f) => el("li", {}, code(f.finding_id), " ", el("span", { class: "small", text: f.rationale })))),
    el("h3", { text: "Tool calls (real aggregate tools)" }),
    el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["#", "Tool", "Status", "Rows returned", "Evidence refs"].map((h) => el("th", { text: h })))),
      el("tbody", {}, inv.tool_call_records.map((t) => el("tr", {}, el("td", { text: String(t.sequence) }), el("td", {}, code(t.tool_name)), el("td", {}, badge(t.status, t.status === "success" ? "ok" : "bad")), el("td", { text: String(t.returned_count) }), el("td", { text: String(t.evidence_reference_ids.length) })))))),
    el("h3", { text: "Hypotheses" }),
    el("ul", {}, inv.hypotheses.map((h) => el("li", {}, el("span", { text: h.statement }), " ", badge(h.status), " ", h.explicitly_non_causal ? badge("NON-CAUSAL", "info") : badge("CAUSAL?", "bad")))),
    source(code("POST /demo/v2/investigations"), ` → InvestigationRunner + ${mi.provider}`)));
  if (!r.draft) {
    blocks.push(outcome("warn", "No recommendation to submit", el("p", { text: mi.real_ai ? "The real model's run ended without a recommendation candidate (see why above). Nothing was submitted, and no mock output was substituted." : "The investigation ended without a recommendation that passed validation." })));
    put("investigation-result", blocks);
    return;
  }
  const rec = r.draft.submission.recommendation;
  blocks.push(el("div", { class: "card" },
    el("h3", {}, "Recommendation ", badge(r.draft.variant_label, r.draft.variant === "as_generated" ? "info" : "warn"), " ",
      mi.real_ai ? (r.draft.modified_after_generation ? el("span", { class: "kind kind-mock", text: `NEGATIVE TEST · WORDING REPLACED · not ${mi.model} output` }) : el("span", { class: "kind kind-ai", text: `REAL LOCAL AI · ${mi.model}` })) : el("span", { class: "kind kind-mock", text: "DETERMINISTIC MOCK" })),
    mi.real_ai && r.draft.graph_final_decision !== "VALIDATED_RECOMMENDATIONS" ? outcome("bad", `The graph's own validator did not accept this candidate (${r.draft.graph_final_decision}).`, el("p", { class: "small", text: "It is shown for transparency only and cannot be submitted." })) : null,
    kv([
      ["Problem statement", rec.problem_statement],
      ["Suggested improvement", rec.suggested_process_improvement],
      ["Expected outcome (with uncertainty)", rec.expected_outcome_with_uncertainty],
      ["Follow-up KPI", rec.proposed_follow_up_kpi],
      ["Implementation risk", rec.implementation_risk],
      ["Evidence IDs", el("span", {}, rec.evidence_ids.map((e) => [code(e), " "]))],
      ["Observed facts", el("div", {}, el("span", { class: "kind kind-evidence", text: "MEASURED EVIDENCE · each number checked against the cited field" }), el("ul", {}, rec.observed_numerical_facts.map((f) => el("li", { class: "small" }, f.statement, " ", code(`${f.value} ${f.unit}`), " ← ", code(f.evidence_id)))))],
      ["Data limitations", el("ul", {}, rec.data_limitations.map((t) => el("li", { class: "small", text: t })))],
      rec.simulation ? ["Cited simulation", el("span", {}, el("span", { class: "kind kind-sim", text: "HYPOTHETICAL WHAT-IF" }), " ", code(rec.simulation.simulation_id), " ", badge(rec.simulation.classification, "warn"))] : null,
      ["Requires human approval", badge(String(rec.requires_human_approval), "ok")],
      ["Recommendation ID", code(rec.recommendation_id)],
    ])));
  blocks.push(renderPreflight(r));
  const blockedAi = mi.real_ai && (r.preflight.decision === "BLOCK" || !r.preflight.validator_valid || r.draft.graph_final_decision !== "VALIDATED_RECOMMENDATIONS");
  blocks.push(blockedAi
    ? outcome("bad", "Not submitted", el("p", { class: "small", text: "This model-generated recommendation failed deterministic validation or guardrails, so the page offers no submission. The server would reject it in any case." }))
    : renderSubmitCard());
  put("investigation-result", blocks);
}
function renderPreflight(r) {
  const p = r.preflight;
  let box;
  if (p.decision === "BLOCK") {
    box = outcome("bad", "Guardrail decision: BLOCK. This recommendation cannot be submitted.", el("p", { class: "small", text: "Reasons (fixed codes from the deterministic engine; no recommendation text is logged):" }), reasonList(p.reason_codes));
  } else if (r.draft.variant === "known_gap_cause") {
    box = outcome("warn", "KNOWN GAP (OPEN_NOT_SAFE): guardrails did NOT block this causal wording",
      el("p", { class: "small", text: "Policy 2.0.0 does not recognise the plain verb 'cause'. This unsupported causal claim would reach human review, and only the Process Owner's rejection stops it. This is a documented, open limitation; it is not counted as a pass." }));
  } else {
    box = outcome("ok", "Guardrail decision: REQUIRE_APPROVAL. Validated; a human must decide.", el("p", { class: "small", text: "The validator accepted every evidence ID and number. Validated recommendations are never auto-executed." }));
  }
  return el("div", { class: "card" }, el("h3", { text: "Validation and guardrails (preflight, read-only)" }),
    kv([["Validator", badge(p.validator_valid ? "VALID" : "INVALID", p.validator_valid ? "ok" : "bad")], ["Checkpoint", code(p.checkpoint)], ["Policy version", code(p.policy_version.slice(0, 16) + "…")]]),
    box, el("p", { class: "small muted", text: p.note }));
}
function renderSubmitCard() {
  return el("div", { class: "card", id: "submit-card" },
    el("h3", { text: "Submit for human approval" }),
    el("p", { class: "small muted" }, "Sends the draft to the real ", code("POST /workflow/recommendations/submit"), ". The server re-runs validation and guardrails and stores the request only if both pass. Idempotency key: ", code(S.submitKey)),
    el("label", { class: "field" }, "Priority", el("select", { id: "priority" }, ["MEDIUM", "LOW", "HIGH"].map((p) => el("option", { value: p, text: p })))),
    el("div", { class: "actions" },
      el("button", { class: "btn primary", onclick: (e) => submitDraft(e.target, false) }, "Submit as ", identity().label),
      el("button", { class: "btn", onclick: (e) => submitDraft(e.target, true) }, "Resubmit identical request (same idempotency key)")),
    el("div", { id: "submit-result" }));
}
async function submitDraft(button, replay) {
  const sub = { ...S.investigation.draft.submission, priority: $("priority").value, idempotency_key: S.submitKey, correlation_id: S.submitCorrelation };
  button.disabled = true;
  const res = await api("POST", "/workflow/recommendations/submit", sub);
  button.disabled = false;
  if (res.ok) {
    const a = res.body;
    put("submit-result", outcome("ok", `${replay ? "Replay answered by server" : "Stored by the server"}: ${a.state}`,
      kv([["Approval request", code(a.approval_request_id)], ["Submitted by", a.submitted_by], ["Version hash", code(a.recommendation_version_hash.slice(0, 16) + "…")], ["HTTP", String(res.status)]]),
      el("p", { class: "small", text: replay ? "The same idempotency key returned the same approval request; no duplicate was created." : "Next: switch to the Process Owner role and open 4 · Approvals." })));
  } else {
    const pre = S.investigation.preflight;
    put("submit-result", httpProblem(res, "Submission"),
      res.code === "GUARDRAIL_BLOCKED" || res.code === "RECOMMENDATION_NOT_VALIDATED"
        ? el("div", { class: "small muted" }, "Why (from the read-only preflight of the same checks):", reasonList(pre.reason_codes)) : null);
  }
}

// ------------------------------------------------------------------------------ what-if
async function loadWhatIfFindings() {
  renderAssumptions();
  if (S.handoffFindings.length && S.handoffRole === S.role) return;
  const res = await api("POST", "/aggregate/v1/bottleneck-findings", { metric_name: "high_frequency_or_repeated_group_handoff", limit: 100 });
  S.handoffRole = S.role;
  if (!res.ok) { S.handoffFindings = []; put("whatif-findings", httpProblem(res, "Loading handoff findings")); return; }
  // Ordering only (by the server's repeated_visit_count); no value is computed.
  S.handoffFindings = [...res.body.result.items].sort((a, b) => (b.measured_statistics.repeated_visit_count || 0) - (a.measured_statistics.repeated_visit_count || 0));
  put("whatif-findings", el("table", {}, el("thead", {}, el("tr", {}, ["", "Group transition", "Returns", "Handoffs", "Evidence"].map((h) => el("th", { text: h })))),
    el("tbody", {}, S.handoffFindings.map((f, i) => el("tr", {},
      el("td", {}, el("input", { type: "checkbox", value: f.finding_id, checked: i === 0, "aria-label": f.aggregate_population.filters.group_transition })),
      el("td", { text: f.aggregate_population.filters.group_transition }),
      el("td", { class: "num", text: fmtInt(f.measured_statistics.repeated_visit_count) }),
      el("td", { class: "num", text: fmtInt(f.counts.observation_count) }),
      el("td", {}, code(f.finding_id.slice(0, 16) + "…")))))));
}
const DEFAULT_ASSUMPTIONS = [["conservative", 0.1, 0.1], ["moderate", 0.25, 0.3], ["optimistic", 0.5, 0.5]];
function renderAssumptions() {
  if ($("assumptions").children.length) return;
  put("assumptions", DEFAULT_ASSUMPTIONS.map(([n, e, r]) => el("tr", {},
    el("td", { text: n }),
    el("td", {}, el("input", { type: "number", min: "0", max: "1", step: "0.05", value: String(e), id: `as-${n}-e`, "aria-label": `${n} eligible share` })),
    el("td", {}, el("input", { type: "number", min: "0", max: "1", step: "0.05", value: String(r), id: `as-${n}-r`, "aria-label": `${n} reduction share` })))));
}
async function runWhatIf(button) {
  const ids = [...document.querySelectorAll("#whatif-findings input:checked")].map((i) => i.value);
  const body = {
    analysis_run_id: S.session.integrity.analysis_run_id,
    selected_finding_ids: ids,
    assumptions: DEFAULT_ASSUMPTIONS.map(([n]) => ({ name: n, eligible_event_share: Number($(`as-${n}-e`).value), assumed_reduction_share: Number($(`as-${n}-r`).value) })),
  };
  button.disabled = true;
  const res = await api("POST", "/aggregate/v1/simulate-group-transition-reduction", body);
  button.disabled = false;
  if (!res.ok) {
    const detail = res.body?.detail;
    const msgs = Array.isArray(detail) ? el("ul", {}, detail.map((d) => el("li", { class: "small", text: d.msg }))) : null;
    put("whatif-result", httpProblem(res, "Simulation"), msgs);
    return;
  }
  renderWhatIf(res.body.result.items[0]);
}
function est(e) {
  if (e.status === "NOT_ESTIMABLE") return el("span", { title: e.explanation }, badge("NOT ESTIMABLE", "warn"));
  return el("span", { title: e.explanation, text: `${Number(e.value).toLocaleString("en-US", { maximumFractionDigits: 1 })} ${e.unit.replace(/_/g, " ")}` });
}
function renderWhatIf(sim) {
  const b = sim.baseline;
  const blocks = [el("div", { class: "card" },
    el("h3", {}, "Simulation result ", badge(sim.status, sim.status === "ESTIMATED" ? "info" : "warn"), " ", badge(sim.classification, "warn")),
    el("p", { text: sim.intervention_hypothesis }),
    kv([["Simulation ID", code(sim.simulation_id)], ["Engine", code(sim.engine_version)]]),
    sim.not_estimable_reasons.length ? outcome("warn", "Not estimable", el("ul", {}, sim.not_estimable_reasons.map((t) => el("li", { text: t })))) : null)];
  if (b) {
    blocks.push(el("div", { class: "card" }, el("h3", {}, "Measured baseline ", badge("FROM EVIDENCE", "ok")),
      kv([["Selected handoff events", fmtInt(b.selected_group_transition_events)], ["Selected repeated-visit events", fmtInt(b.selected_repeated_group_visit_events)], ["All group-change events", fmtInt(b.all_group_change_events)], ["All repeated-visit events", fmtInt(b.all_repeated_group_visit_events)], ["Incidents with repeated visits", fmtInt(b.incidents_with_repeated_group_visits)], ["Total incidents", fmtInt(b.total_incidents)]])));
  }
  blocks.push(el("div", { class: "card" }, el("h3", {}, "Hypothetical scenarios ", badge("SIMULATED · NOT MEASURED", "warn")),
    el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["Scenario", "Eligible", "Reduction", "Combined", "Repeated visits avoided", "Remaining", "Affected incidents", "Elapsed-time impact", "SLA impact"].map((h) => el("th", { text: h })))),
      el("tbody", {}, sim.scenarios.map((s) => el("tr", {},
        el("td", { text: s.name }), el("td", { text: fmtPct(s.eligible_event_share_assumption) }), el("td", { text: fmtPct(s.reduction_share_assumption) }), el("td", { text: fmtPct(s.combined_hypothetical_reduction_share) }),
        el("td", {}, est(s.hypothetical_repeated_visit_events_avoided)), el("td", {}, est(s.hypothetical_repeated_visit_events_remaining)), el("td", {}, est(s.affected_incident_count)),
        el("td", {}, est(s.elapsed_time_impact)), el("td", {}, est(s.sla_impact))))))),
    el("p", { class: "small", text: sim.sensitivity_analysis.interpretation }),
    el("h3", { text: "Uncertainty" }), el("ul", {}, sim.uncertainty_statements.map((t) => el("li", { class: "small", text: t }))),
    source(code("POST /aggregate/v1/simulate-group-transition-reduction"), " → WhatIfSimulationEngine")));
  put("whatif-result", blocks);
}

// ---------------------------------------------------------------------------- approvals
const STATE_KIND = { PENDING_APPROVAL: "info", EXECUTED: "ok", REJECTED: "bad", REVISION_REQUESTED: "warn", APPROVED: "ok", EXECUTING: "info", EXECUTION_FAILED: "bad" };
async function loadApprovals() {
  const res = await api("GET", "/demo/v2/approvals");
  if (!res.ok) { put("approval-list", httpProblem(res, `${identity().label} listing approval requests`)); put("approval-detail", el("p", { class: "muted", text: "No access." })); return; }
  const items = [...res.body].reverse();
  if (!items.length) { put("approval-list", el("p", { class: "muted", text: "No requests yet. Submit one as the Analyst (2 · Investigate)." })); return; }
  put("approval-list", items.map((a) => el("button", { class: `list-item ${S.selectedApproval === a.approval_request_id ? "selected" : ""}`, onclick: () => { S.selectedApproval = a.approval_request_id; S.lastDecision = null; loadApprovals(); } },
    el("div", { class: "row" }, code(a.approval_request_id.slice(0, 20) + "…"), badge(a.state, STATE_KIND[a.state] || "")),
    el("div", { class: "small muted", text: `by ${a.submitted_by} · ${a.priority} · ${fmtTime(a.created_at)}` }))));
  if (S.selectedApproval) loadApprovalDetail(S.selectedApproval);
}
async function loadApprovalDetail(id) {
  const res = await api("GET", `/demo/v2/approvals/${encodeURIComponent(id)}/recommendation`);
  if (!res.ok) { put("approval-detail", httpProblem(res, "Loading the request")); return; }
  const { approval: a, recommendation: rec, provenance: p, decisions } = res.body;
  const pending = a.state === "PENDING_APPROVAL";
  put("approval-detail",
    el("h3", {}, "Request ", badge(a.state, STATE_KIND[a.state] || "")),
    kv([["Approval request", code(a.approval_request_id)], ["Submitted by", a.submitted_by], ["Version", `${a.recommendation_version} · hash `, code(a.recommendation_version_hash.slice(0, 16) + "…")], ["Accepted under policy", p ? code(p.policy_version.slice(0, 16) + "…") : "—"]]),
    el("h3", { text: "Stored recommendation (immutable version)" }),
    kv([["Problem statement", rec.problem_statement], ["Suggested improvement", rec.suggested_process_improvement], ["Expected outcome", rec.expected_outcome_with_uncertainty], ["Evidence", el("span", {}, rec.evidence_ids.map((e) => [code(e), " "]))], ["Data limitations", el("ul", {}, rec.data_limitations.map((t) => el("li", { class: "small", text: t })))]]),
    outcome("info", "Reviewer check", el("p", { class: "small", text: "Guardrails catch many unsupported claims, but not all (see Safety & limits: OPEN_NOT_SAFE). Reject wording that states a cause the evidence does not establish." })),
    decisions.length ? [el("h3", { text: "Decisions so far" }), el("ul", {}, decisions.map((d) => el("li", { class: "small" }, badge(d.decision, STATE_KIND[d.decision] || ""), ` by ${d.actor_id} · ${fmtTime(d.created_at)}${d.reason ? " · " + d.reason : ""}`)))] : null,
    el("h3", { text: "Decide" }),
    el("p", { class: "small muted" }, "Acting as ", el("b", { text: identity().label }), ". The buttons stay enabled for every role so you can watch the server enforce the rules."),
    el("label", { class: "field" }, "Reason (stored with the decision)", el("textarea", { id: "decision-reason", rows: "2", maxlength: "2000" })),
    el("div", { class: "actions" },
      el("button", { class: "btn good", onclick: (e) => decide(e.target, a, "approve") }, "Approve"),
      el("button", { class: "btn danger", onclick: (e) => decide(e.target, a, "reject") }, "Reject"),
      el("button", { class: "btn", onclick: (e) => decide(e.target, a, "request-revision") }, "Request revision")),
    !pending ? el("p", { class: "small muted", text: `This request is ${a.state}; the server will refuse a new decision.` }) : null,
    el("div", { id: "decision-result" }));
}
async function decide(button, a, action, replay = false) {
  const body = replay ? S.lastDecision.body : {
    recommendation_version_hash: a.recommendation_version_hash,
    idempotency_key: `decide-${uuid()}`,
    correlation_id: a.correlation_id,
    reason: ($("decision-reason")?.value || "").trim() || null,
  };
  const path = replay ? S.lastDecision.path : `/workflow/approvals/${encodeURIComponent(a.approval_request_id)}/${action}`;
  if (button) button.disabled = true;
  const res = await api("POST", path, body);
  if (button) button.disabled = false;
  const nodes = [];
  if (res.ok) {
    S.lastDecision = { path, body, action, approval: a };
    const d = res.body;
    const tickets = await api("GET", "/workflow/tickets");
    // Tickets carry the approval's correlation ID; approval_reference is the decision ID.
    const mine = tickets.ok ? tickets.body.filter((t) => t.correlation_id === d.correlation_id && t.recommendation_version_hash === d.recommendation_version_hash) : [];
    nodes.push(outcome(d.state === "EXECUTED" ? "ok" : d.state === "REJECTED" ? "bad" : "info",
      `${replay ? "Replay with the same idempotency key: " : ""}server state ${d.state}`,
      kv([["HTTP", String(res.status)], ["Idempotency key", code(body.idempotency_key)],
        ["Tickets for this request", tickets.ok ? `${mine.length}` : `not visible to this role (${tickets.status})`],
        mine[0] ? ["Ticket", el("span", {}, code(mine[0].ticket_id), " ", badge(mine[0].status, "ok"), " ", code(mine[0].execution_result?.adapter || ""))] : null,
        tickets.ok ? ["All tickets in database (visible)", String(tickets.body.length)] : null])));
    if (d.state === "EXECUTED" || d.state === "REJECTED" || d.state === "REVISION_REQUESTED") {
      nodes.push(el("div", { class: "actions" },
        el("button", { class: "btn", onclick: (e) => decide(e.target, a, action, true) }, "Replay the same decision (same key)"),
        el("button", { class: "btn", onclick: (e) => decide(e.target, a, action) }, "Send the decision again with a NEW key")));
    }
  } else {
    nodes.push(httpProblem(res, `${action} as ${identity().label}`));
  }
  put("decision-result", nodes);
  // Refresh the list (states) without losing the result box.
  const list = await api("GET", "/demo/v2/approvals");
  if (list.ok) {
    const items = [...list.body].reverse();
    put("approval-list", items.map((x) => el("button", { class: `list-item ${S.selectedApproval === x.approval_request_id ? "selected" : ""}`, onclick: () => { S.selectedApproval = x.approval_request_id; loadApprovals(); } },
      el("div", { class: "row" }, code(x.approval_request_id.slice(0, 20) + "…"), badge(x.state, STATE_KIND[x.state] || "")),
      el("div", { class: "small muted", text: `by ${x.submitted_by} · ${x.priority} · ${fmtTime(x.created_at)}` }))));
  }
}

// ------------------------------------------------------------------------------ tickets
async function loadTickets() {
  const res = await api("GET", "/workflow/tickets");
  if (!res.ok) { put("tickets", httpProblem(res, `${identity().label} listing tickets`)); return; }
  const t = res.body;
  put("tickets",
    el("div", { class: "ticket-count" }, "Tickets visible to ", el("span", { text: identity().label }), ": ", el("b", { text: String(t.length) }),
      S.role === "analyst" ? el("span", { class: "small muted", text: " (an Analyst sees only tickets from their own submissions)" }) : null),
    t.length ? t.slice().reverse().map((x) => el("div", { class: "card" },
      el("h3", {}, code(x.ticket_id), " ", badge(x.status, "ok")),
      kv([["Problem", x.process_problem_statement], ["Action", x.proposed_improvement_action], ["Owner · priority", `${x.proposed_owner} · ${x.priority}`], ["Approval decision", code(x.approval_reference)], ["Correlation", code(x.correlation_id)],
        ["Execution", el("span", {}, code(x.execution_result?.adapter || "—"), " ", code(x.execution_result?.outcome || ""))], ["Evidence", el("span", {}, x.supporting_evidence_references.map((e) => [code(e), " "]))], ["Created", fmtTime(x.created_at)]]))) : el("p", { class: "muted", text: "No tickets yet. A ticket exists only after a Process Owner approval passes revalidation." }));
}

// -------------------------------------------------------------------------------- audit
async function loadAudit() {
  const res = await api("GET", "/demo/v2/audit/latest?limit=100");
  if (!res.ok) { put("audit", httpProblem(res, `${identity().label} reading the audit trail`)); return; }
  const events = res.body;
  const requested = [...new Set(events.filter((e) => e.action_type === "APPROVAL_REQUESTED").map((e) => e.artifact_id))];
  put("audit",
    el("div", { class: "card" },
      el("h3", { text: "Admin demo control: simulated policy upgrade (for REVISION_REQUIRED)" }),
      el("p", { class: "small muted", text: "Adds one extra pattern, derived from the chosen pending recommendation, to a copy of the packaged policy in the temporary directory. A later approval of that request fails revalidation with REVISION_REQUIRED. The pinned policy is never modified; restore it afterwards." }),
      requested.length ? el("label", { class: "field" }, "Pending approval request (IDs from the audit trail)", el("select", { id: "policy-target" }, requested.map((id) => el("option", { value: id, text: id })))) : el("p", { class: "small muted", text: "No approval requests yet." }),
      el("div", { class: "actions" },
        el("button", { class: "btn", disabled: !requested.length, onclick: simulateStricter }, "Activate simulated stricter policy"),
        el("button", { class: "btn", onclick: restorePolicy }, "Restore pinned policy")),
      el("div", { id: "policy-result" })),
    el("div", { class: "card" }, el("h3", { text: `Latest ${events.length} audit events (newest first)` }),
      el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["Time", "Actor", "Action", "Result", "Artifact", "Details (codes only)"].map((h) => el("th", { text: h })))),
        el("tbody", {}, events.map((e) => el("tr", {},
          el("td", { class: "small", text: fmtTime(e.timestamp) }), el("td", { class: "small", text: e.actor_id }),
          el("td", {}, badge(e.action_type, /BLOCK/.test(e.action_type) ? "bad" : e.action_type.includes("GRANTED") ? "ok" : "")),
          el("td", { text: e.result }), el("td", { class: "small" }, e.artifact_type, " ", code(e.artifact_id.slice(0, 18))),
          el("td", { class: "small" }, Object.entries(e.details).filter(([, v]) => v !== null && v !== "").map(([k, v]) => el("div", {}, el("span", { class: "muted", text: `${k}: ` }), String(v).length > 40 ? String(v).slice(0, 40) + "…" : String(v)))))))))));
}
async function simulateStricter() {
  const res = await api("POST", "/demo/v2/policy/simulate-stricter", { approval_request_id: $("policy-target").value });
  put("policy-result", res.ok ? outcome("warn", "Simulated stricter policy active", el("p", { class: "small" }, "Policy ", code(res.body.policy.policy_version.slice(0, 16) + "…"), " (pinned: ", code(res.body.pinned_policy_version.slice(0, 16) + "…"), "). Now switch to Process Owner and try to approve that request.")) : httpProblem(res, "Policy simulation"));
  await refreshSession();
}
async function restorePolicy() {
  const res = await api("POST", "/demo/v2/policy/restore");
  put("policy-result", res.ok ? outcome("ok", "Pinned policy restored", el("p", { class: "small" }, code(res.body.policy.policy_version))) : httpProblem(res, "Restore policy"));
  await refreshSession();
}
async function deliverNotifications(button) {
  button.disabled = true;
  const res = await api("POST", "/demo/v2/notifications/deliver");
  button.disabled = false;
  if (!res.ok) { put("audit", httpProblem(res, "Delivering notifications")); return; }
  await loadAudit();
  $("audit").prepend(outcome("ok", `Local adapter: ${res.body.delivered} delivered, ${res.body.failed} failed`, el("p", { class: "small", text: "Nothing left this machine (LOCAL_DEMO_ONLY_NO_EXTERNAL_SEND)." })));
}

// ------------------------------------------------------------------------------- safety
function renderSafety() {
  const s = S.session, i = s.integrity, iso = s.isolation;
  put("safety-integrity", el("h3", { text: "Integrity pins (fail closed at startup)" }), kv([
    ["Analysis run", code(i.analysis_run_id)],
    ["Evidence contract SHA-256", el("span", {}, code(i.evidence_sha256), " ", badge(i.evidence_pin_matches ? "matches pin" : "MISMATCH", i.evidence_pin_matches ? "ok" : "bad"))],
    ["Guardrail policy", `${i.policy_id} · schema ${i.policy_schema_version}`],
    ["Policy version", el("span", {}, code(i.policy_version), " ", badge(i.policy_pin_matches ? "matches pin" : "SIMULATED / DIFFERENT", i.policy_pin_matches ? "ok" : "bad"))],
    ["Policy mode", badge(i.policy_mode, i.policy_mode === "PINNED" ? "ok" : "bad")],
  ]));
  put("safety-isolation", el("h3", { text: "Isolation" }), kv(Object.entries(iso).map(([k, v]) => [k.replace(/_/g, " "), String(v)])));
  put("safety-limits", el("h3", { text: "Known limitations (shown honestly)" }), el("ul", {}, s.known_limitations.map((l) => el("li", {}, code(l.id), " ", l.summary))));
  put("safety-policy", el("h3", { text: "Role permissions (enforced by the server)" }), el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ["Role", "Actor", "Roles", "Permissions", "Purpose"].map((h) => el("th", { text: h })))),
    el("tbody", {}, s.identities.map((x) => el("tr", {}, el("td", { text: x.label }), el("td", {}, code(x.actor_id)), el("td", { text: x.roles.join(", ") }), el("td", { class: "small", text: x.permissions.join(", ") }), el("td", { class: "small", text: x.description })))))));
}

// --------------------------------------------------------------------------------- boot
document.addEventListener("DOMContentLoaded", async () => {
  if (!(await loadSession())) return;
  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $("reload-evidence").addEventListener("click", loadEvidence);
  $("reload-approvals").addEventListener("click", loadApprovals);
  $("reload-tickets").addEventListener("click", loadTickets);
  $("reload-audit").addEventListener("click", loadAudit);
  $("deliver-notifications").addEventListener("click", (e) => deliverNotifications(e.target));
  $("run-investigation").addEventListener("click", (e) => runInvestigation(e.target));
  $("run-whatif").addEventListener("click", (e) => runWhatIf(e.target));
  $("variant").addEventListener("change", () => $("custom-wrap").classList.toggle("hidden", $("variant").value !== "custom"));
  renderInvestigateForm();
  loadModelStatus();
  renderAssumptions();
  loadEvidence();
});
