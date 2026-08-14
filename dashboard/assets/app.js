/* Hallmark · Cobalt application console
 *
 * Renders the five console views from the pipeline API. Every number displayed
 * is read from the API response; nothing is estimated or filled in client-side.
 * Missing values render as an em-dash, never as a plausible-looking zero.
 */

const SECTIONS = [
  { id: "overview", label: "Overview", icon: "i-overview" },
  { id: "leads", label: "Leads", icon: "i-leads" },
  { id: "market", label: "Market", icon: "i-market" },
  { id: "approvals", label: "Approvals", icon: "i-approvals" },
  { id: "runs", label: "Runs", icon: "i-runs" },
];

const state = {
  section: "overview",
  health: null,
  config: null,
  overview: null,
  leads: null,
  market: null,
  messages: null,
  runs: null,
  leadQuery: { search: "", region: "", stage: "", sort: "score", order: "desc" },
  selectedMessage: null,
  paletteIndex: 0,
  paletteRows: [],
};

/* ------------------------------------------------------------------ helpers */

const view = document.getElementById("view");
const railNav = document.getElementById("rail-nav");
const railStatus = document.getElementById("rail-status");
const statusline = document.getElementById("statusline");
const toasts = document.getElementById("toasts");
const palette = document.getElementById("palette");
const paletteInput = document.getElementById("palette-input");
const paletteList = document.getElementById("palette-list");
const drawer = document.getElementById("drawer");

function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/** Em-dash for absent data. A missing number is never rendered as 0. */
function num(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function score(value) {
  if (value === null || value === undefined) return "—";
  return Number(value).toFixed(3);
}

function percent(value) {
  if (value === null || value === undefined) return "—";
  return `${Math.round(Number(value) * 100)}%`;
}

function when(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function icon(id, size = 16) {
  return `<svg width="${size}" height="${size}" aria-hidden="true"><use href="#${id}" /></svg>`;
}

async function api(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload && payload.detail ? payload.detail : `Request failed (${response.status})`;
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return payload;
}

/* Silent success is the default; toasts carry failures and undoable actions. */
function toast(message, { kind = "positive", undo = null, timeout = 8000 } = {}) {
  const node = document.createElement("div");
  node.className = `toast toast--${kind}`;
  node.innerHTML = `
    ${icon(kind === "critical" ? "i-alert" : "i-check", 16)}
    <span>${esc(message)}</span>
    ${undo ? '<button class="toast__undo" type="button">Undo</button>' : ""}
  `;
  if (undo) {
    node.querySelector(".toast__undo").addEventListener("click", () => {
      node.remove();
      undo();
    });
  }
  toasts.append(node);
  window.setTimeout(() => node.remove(), timeout);
}

/* --------------------------------------------------------------- components */

function emptyState(title, body, hint) {
  return `
    <div class="empty">
      ${icon("i-empty", 28)}
      <h3>${esc(title)}</h3>
      <p>${esc(body)}</p>
      ${hint ? `<p><code>${esc(hint)}</code></p>` : ""}
    </div>
  `;
}

function mockBanner(integrations) {
  const missing = (integrations || []).filter((item) => !item.configured);
  if (!missing.length) return "";
  const names = missing.map((item) => item.label).join(", ");
  return `
    <div class="banner">
      ${icon("i-alert", 18)}
      <div class="banner__body">
        <strong>Mock data.</strong> ${esc(names)} ${missing.length === 1 ? "has" : "have"}
        no credentials configured, so the pipeline generated placeholder records for
        ${missing.length === 1 ? "that stage" : "those stages"}. Add the keys to
        <code>.env</code> and re-run to replace them.
      </div>
    </div>
  `;
}

function statusChip(status) {
  const map = {
    approved: ["positive", "i-check", "Approved"],
    sent: ["positive", "i-check", "Sent"],
    rejected: ["critical", "i-close", "Rejected"],
    pending: ["caution", "i-alert", "Awaiting"],
    complete: ["positive", "i-check", "Complete"],
    running: ["accent", "i-play", "Running"],
    failed: ["critical", "i-alert", "Failed"],
    cancelled: ["critical", "i-close", "Cancelled"],
    succeeded: ["positive", "i-check", "Succeeded"],
  };
  const [kind, glyph, label] = map[status] || ["", "i-alert", status || "unknown"];
  return `<span class="chip${kind ? ` chip--${kind}` : ""}">${icon(glyph, 12)}${esc(label)}</span>`;
}

function meter(label, value, muted = false) {
  const width = value === null || value === undefined ? 0 : Math.max(0, Math.min(1, value)) * 100;
  return `
    <div class="meter">
      <span class="meter__label">${esc(label)}</span>
      <span class="meter__track">
        <span class="meter__fill${muted ? " meter__fill--muted" : ""}" style="width: ${width}%"></span>
      </span>
      <span class="meter__value">${score(value)}</span>
    </div>
  `;
}

function factRow(key, value) {
  return `
    <div class="factlist__row">
      <span class="factlist__key">${esc(key)}</span>
      <span class="factlist__value">${value}</span>
    </div>
  `;
}

/* -------------------------------------------------------------------- views */

function renderOverview() {
  const data = state.overview;
  if (!data) return emptyState("No run yet", "The console reads what the pipeline writes to data/. Run it once to populate this view.", 'python run_pipeline.py "sustainable fashion"');

  const t = data.totals;
  const funnel = data.funnel
    .map((stage, index) => {
      const ratio =
        stage.total && stage.count !== null && stage.count !== undefined
          ? stage.count / stage.total
          : stage.count
            ? 1
            : 0;
      const denominator = stage.total ? ` <span>/ ${num(stage.total)}</span>` : "";
      return `
        <li class="funnel__stage" data-status="${esc(stage.status)}">
          <span class="funnel__index">${String(index + 1).padStart(2, "0")}</span>
          <div class="funnel__label">
            <span class="funnel__name">${esc(stage.label)}</span>
            <span class="funnel__count">${num(stage.count)}${denominator} ${statusChip(stage.status)}</span>
          </div>
          <p class="funnel__summary">${esc(stage.summary)}</p>
          <span class="funnel__bar"><span class="funnel__fill" style="width: ${ratio * 100}%"></span></span>
        </li>
      `;
    })
    .join("");

  const integrations = (state.health?.integrations || [])
    .map(
      (item) => `
        <div class="factlist__row">
          <span class="factlist__key">${esc(item.label)}<br /><span class="table__sub">${esc(item.purpose)}</span></span>
          <span class="factlist__value">${
            item.configured
              ? statusChip("complete")
              : `<span class="chip chip--caution">${icon("i-alert", 12)}mock</span>`
          }</span>
        </div>
      `,
    )
    .join("");

  const waiting =
    t.awaiting_decision > 0
      ? `
        <div class="banner">
          ${icon("i-alert", 18)}
          <div class="banner__body">
            <strong>${num(t.awaiting_decision)} draft${t.awaiting_decision === 1 ? "" : "s"} awaiting your decision.</strong>
            Nothing sends until you approve it.
            <a href="#approvals" style="color: var(--color-accent); text-decoration: underline; text-underline-offset: 2px">Open the approval queue</a>.
          </div>
        </div>
      `
      : "";

  return `
    <div class="page-head enter">
      <span class="tag">Run ${esc(data.run_id)} · ${esc(data.status || "unknown")}</span>
      <h1>${esc(data.niche || "Untitled niche")}</h1>
      <p class="lede">
        ${esc((data.regions || []).join(" · ") || "No regions recorded")} —
        started ${esc(when(data.started_at))}${data.completed_at ? `, finished ${esc(when(data.completed_at))}` : ""}.
      </p>
    </div>

    ${data.error ? `<div class="banner banner--critical">${icon("i-alert", 18)}<div class="banner__body"><strong>Run failed.</strong> ${esc(data.error)}</div></div>` : ""}
    ${waiting}
    ${mockBanner(state.health?.integrations)}

    <div class="split">
      <section class="panel--flush panel">
        <div class="panel__head">
          <h2 style="font-size: var(--text-md)">Pipeline funnel</h2>
          <span class="tag">stage · reached / eligible</span>
        </div>
        <div class="panel__body">
          <ol class="funnel">${funnel}</ol>
        </div>
      </section>

      <div class="stack">
        <section class="panel">
          <div class="readout">
            <span class="tag">Qualified leads</span>
            <span class="readout__value">${num(t.leads)}</span>
            <span class="readout__note">
              ${num(t.pitch_ready)} pitch-ready · ${num(t.contactable)} with a named contact
            </span>
          </div>
        </section>

        <section class="panel">
          <div class="panel-title tag" style="display: block; margin-block-end: var(--space-sm)">Lead scores</div>
          <div class="factlist">
            ${factRow("Average", score(data.scores.average))}
            ${factRow("Highest", score(data.scores.top))}
            ${factRow("Lowest", score(data.scores.low))}
            ${factRow("Brands analysed", num(t.brands_analyzed))}
            ${factRow("Competitor ads found", num(t.active_ads))}
          </div>
        </section>

        <section class="panel">
          <div class="tag" style="display: block; margin-block-end: var(--space-sm)">Outreach</div>
          <div class="factlist">
            ${factRow("Drafted", num(t.drafted))}
            ${factRow("Awaiting decision", num(t.awaiting_decision))}
            ${factRow("Approved", num(t.approved))}
            ${factRow("Rejected", num(t.rejected))}
            ${factRow("Sent", num(t.sent))}
          </div>
        </section>

        <section class="panel">
          <div class="tag" style="display: block; margin-block-end: var(--space-sm)">Data sources</div>
          <div class="factlist">${integrations}</div>
        </section>
      </div>
    </div>
  `;
}

function renderLeads() {
  const data = state.leads;
  const q = state.leadQuery;

  const regionOptions = ["", ...((data && data.regions) || [])]
    .map(
      (region) =>
        `<option value="${esc(region)}"${region === q.region ? " selected" : ""}>${
          region ? esc(region) : "All regions"
        }</option>`,
    )
    .join("");

  const stageOptions = [
    ["", "All stages"],
    ["pitch-ready", "Pitch-ready"],
    ["contacted", "Contact only"],
    ["scored", "Scored only"],
  ]
    .map(
      ([value, label]) =>
        `<option value="${esc(value)}"${value === q.stage ? " selected" : ""}>${esc(label)}</option>`,
    )
    .join("");

  const sortOptions = [
    ["score", "Score"],
    ["name", "Brand"],
    ["size", "Company size"],
    ["ads", "Active ads"],
    ["confidence", "Match confidence"],
  ]
    .map(
      ([value, label]) =>
        `<option value="${esc(value)}"${value === q.sort ? " selected" : ""}>${esc(label)}</option>`,
    )
    .join("");

  const rows = (data?.leads || [])
    .map((lead) => {
      const ads = lead.ad_presence_summary || {};
      const contact = lead.contact;
      return `
        <tr class="table__row" tabindex="0" data-lead="${esc(lead.id)}">
          <td data-label="Brand">
            <span>
              <span class="table__brand">${esc(lead.brand_name)}</span><br />
              <span class="table__sub">${esc(contact ? `${contact.name} · ${contact.title}` : "No contact yet")}</span>
            </span>
          </td>
          <td data-label="Region"><span class="mono">${esc(lead.region || "—")}</span></td>
          <td data-label="Size" class="num">${num(lead.company_size)}</td>
          <td data-label="Active ads" class="num">${num(ads.active_ads)}</td>
          <td data-label="Score" class="num">${score(lead.qualification_score)}</td>
          <td data-label="Stage">${
            lead.stage === "pitch-ready"
              ? `<span class="chip chip--positive">${icon("i-check", 12)}pitch-ready</span>`
              : lead.stage === "contacted"
                ? `<span class="chip">contact only</span>`
                : `<span class="chip">scored</span>`
          }</td>
        </tr>
      `;
    })
    .join("");

  const weights = data?.weights || {};
  const weightLine = Object.entries(weights)
    .map(([key, value]) => `${key.replaceAll("_", " ")} ${value}`)
    .join(" · ");

  return `
    <div class="page-head enter">
      <span class="tag">Prospect stage output</span>
      <h1>Qualified leads</h1>
      <p class="lede">
        Brands with little or no ad footprint in a market where competitors are spending.
        Ranked by the weighted score the prospect agent wrote${weightLine ? `: ${esc(weightLine)}` : ""}.
      </p>
    </div>

    <div class="toolbar">
      <div class="field">
        <label class="field__label" for="lead-search">Search</label>
        <input
          class="input"
          id="lead-search"
          type="search"
          placeholder="Brand, domain, or contact"
          value="${esc(q.search)}"
          data-filter="search"
        />
      </div>
      <div class="field">
        <label class="field__label" for="lead-region">Region</label>
        <select class="select" id="lead-region" data-filter="region">${regionOptions}</select>
      </div>
      <div class="field">
        <label class="field__label" for="lead-stage">Stage</label>
        <select class="select" id="lead-stage" data-filter="stage">${stageOptions}</select>
      </div>
      <div class="field">
        <label class="field__label" for="lead-sort">Sort by</label>
        <select class="select" id="lead-sort" data-filter="sort">${sortOptions}</select>
      </div>
    </div>

    <section class="panel panel--flush">
      <div class="panel__head">
        <h2 style="font-size: var(--text-md)">${num(data?.count)} lead${data?.count === 1 ? "" : "s"}</h2>
        <span class="tag">select a row for the score breakdown</span>
      </div>
      ${
        rows
          ? `<div class="table-scroll">
              <table class="table">
                <thead>
                  <tr>
                    <th scope="col">Brand</th>
                    <th scope="col">Region</th>
                    <th scope="col" class="num">Size</th>
                    <th scope="col" class="num">Active ads</th>
                    <th scope="col" class="num">Score</th>
                    <th scope="col">Stage</th>
                  </tr>
                </thead>
                <tbody>${rows}</tbody>
              </table>
            </div>`
          : `<div class="panel__body">${emptyState(
              "No leads match",
              "Every qualified lead was filtered out. Clear the filters, or widen the company-size band in config.yaml and run the pipeline again.",
              "prospect.min_company_size / prospect.max_company_size",
            )}</div>`
      }
    </section>
  `;
}

function renderMarket() {
  const data = state.market;
  if (!data) return emptyState("No market data", "Run the pipeline to populate the ad-discovery stage.", 'python run_pipeline.py "sustainable fashion"');

  const advertisers = (data.advertisers || [])
    .map(
      (item) => `
        <tr>
          <td data-label="Advertiser"><span class="table__brand">${esc(item.name)}</span></td>
          <td data-label="Ads" class="num">${num(item.ad_count)}</td>
          <td data-label="Impressions" class="num">${num(item.total_impressions)}</td>
          <td data-label="Spend" class="num">${
            item.estimated_spend ? num(item.estimated_spend) : "—"
          }</td>
        </tr>
      `,
    )
    .join("");

  const maxRegion = Math.max(1, ...Object.values(data.ads_per_region || {}));
  const regions = Object.entries(data.ads_per_region || {})
    .sort((a, b) => b[1] - a[1])
    .map(
      ([region, count]) => `
        <div class="meter">
          <span class="meter__label mono">${esc(region)}</span>
          <span class="meter__track">
            <span class="meter__fill" style="width: ${(count / maxRegion) * 100}%"></span>
          </span>
          <span class="meter__value">${num(count)}</span>
        </div>
      `,
    )
    .join("");

  const hooks = (data.patterns?.common_hooks || [])
    .map((hook) => `<span class="chip chip--accent">${esc(hook)}</span>`)
    .join(" ");

  const copy = (data.patterns?.sample_copy || []).map((line) => esc(line)).join("\n");

  return `
    <div class="page-head enter">
      <span class="tag">Ad discovery stage output</span>
      <h1>Market activity</h1>
      <p class="lede">
        Who is already advertising in ${esc(data.niche || "this niche")}. This is the intel the pitch
        leans on — a lead is worth contacting because these brands are spending and they are not.
      </p>
    </div>

    ${mockBanner(state.health?.integrations)}

    <div class="split">
      <section class="panel panel--flush">
        <div class="panel__head">
          <h2 style="font-size: var(--text-md)">Dominant advertisers</h2>
          <span class="tag">${num((data.advertisers || []).length)} tracked</span>
        </div>
        ${
          advertisers
            ? `<div class="table-scroll">
                <table class="table">
                  <thead>
                    <tr>
                      <th scope="col">Advertiser</th>
                      <th scope="col" class="num">Ads</th>
                      <th scope="col" class="num">Impressions</th>
                      <th scope="col" class="num">Spend</th>
                    </tr>
                  </thead>
                  <tbody>${advertisers}</tbody>
                </table>
              </div>`
            : `<div class="panel__body">${emptyState(
                "No advertisers recorded",
                "The ad-discovery stage returned no active ads for this niche and region set.",
              )}</div>`
        }
      </section>

      <div class="stack">
        <section class="panel">
          <div class="tag" style="display: block; margin-block-end: var(--space-sm)">Active ads by region</div>
          ${regions || `<p class="lede" style="font-size: var(--text-sm)">No per-region ads recorded.</p>`}
        </section>

        <section class="panel">
          <div class="tag" style="display: block; margin-block-end: var(--space-sm)">Recurring hooks</div>
          ${
            hooks ||
            `<p class="lede" style="font-size: var(--text-sm)">No hook patterns extracted. Pattern detection is keyword-based; see TODO.md.</p>`
          }
        </section>

        ${
          copy
            ? `<section class="graphite">
                <div class="graphite__head">
                  <span class="graphite__label">Sample competitor copy</span>
                  <span class="graphite__label">${num((data.patterns?.sample_copy || []).length)} lines</span>
                </div>
                <pre class="graphite__body">${copy}</pre>
              </section>`
            : ""
        }
      </div>
    </div>
  `;
}

function renderApprovals() {
  const data = state.messages;
  if (!data) return emptyState("No drafts", "Run the pipeline through the outreach stage to draft pitches.", 'python run_pipeline.py "sustainable fashion"');

  const messages = data.messages || [];
  if (!messages.length) {
    return `
      <div class="page-head enter">
        <span class="tag">Outreach stage output</span>
        <h1>Approval queue</h1>
      </div>
      <section class="panel">
        ${emptyState(
          "Nothing drafted yet",
          "The outreach agent drafts one pitch per lead that has both a contact and a sample video. No lead reached that state in this run.",
          'python run_pipeline.py "sustainable fashion"',
        )}
      </section>
    `;
  }

  const selected =
    messages.find((message) => message.id === state.selectedMessage) || messages[0];
  state.selectedMessage = selected.id;

  const items = messages
    .map(
      (message) => `
        <button
          class="queue__item"
          type="button"
          role="option"
          aria-selected="${message.id === selected.id}"
          data-message="${esc(message.id)}"
        >
          <span class="queue__item-top">
            <span class="queue__brand">${esc(message.lead_id)}</span>
            ${statusChip(message.status)}
          </span>
          <span class="queue__meta">${esc(message.to_name)} · ${esc(message.to_email)}</span>
          <span class="queue__meta mono">score ${score(message.qualification_score)} · ${esc(message.region || "—")}</span>
        </button>
      `,
    )
    .join("");

  const gateNotice = data.approval.auto_approve
    ? `<div class="banner banner--critical">${icon("i-alert", 18)}<div class="banner__body"><strong>Auto-approve is on.</strong> Set <code>approval.auto_approve: false</code> in <code>config.yaml</code> before running against real contacts.</div></div>`
    : !data.approval.enabled
      ? `<div class="banner banner--critical">${icon("i-alert", 18)}<div class="banner__body"><strong>The approval gate is disabled.</strong> Set <code>approval.enabled: true</code> in <code>config.yaml</code>.</div></div>`
      : "";

  const pending = messages.filter((message) => message.status === "pending").length;

  const decided = selected.status !== "pending";

  return `
    <div class="page-head enter">
      <span class="tag">Outreach stage output</span>
      <h1>Approval queue</h1>
      <p class="lede">
        Every pitch needs an explicit decision before the pipeline will send it.
        ${num(pending)} of ${num(messages.length)} still awaiting yours.
      </p>
    </div>

    ${gateNotice}

    <div class="queue">
      <section class="panel panel--flush">
        <div class="panel__head">
          <h2 style="font-size: var(--text-md)">Drafts</h2>
          <span class="tag">${num(messages.length)}</span>
        </div>
        <div class="queue__list" role="listbox" aria-label="Drafted messages">${items}</div>
      </section>

      <div class="stack">
        <section class="panel">
          <div class="head-row">
            <div class="page-head">
              <span class="tag">To ${esc(selected.to_name)} · ${esc(selected.contact_title || "role unknown")}</span>
              <h2 style="font-size: var(--text-md)">${esc(selected.subject)}</h2>
            </div>
            ${statusChip(selected.status)}
          </div>
          <div class="factlist" style="margin-block-start: var(--space-md)">
            ${factRow("Recipient", esc(selected.to_email))}
            ${factRow("Region", esc(selected.region || "—"))}
            ${factRow("Lead score", score(selected.qualification_score))}
            ${factRow("Contact confidence", percent(selected.contact_confidence))}
            ${factRow("Decided", esc(when(selected.decided_at)))}
          </div>
        </section>

        <section class="graphite">
          <div class="graphite__head">
            <span class="graphite__label">Draft body</span>
            <span class="graphite__label">${esc(selected.id)}</span>
          </div>
          <pre class="graphite__body">${esc(selected.body)}</pre>
          ${
            selected.video_url
              ? `<div class="graphite__foot">Sample video: ${esc(selected.video_url)}</div>`
              : ""
          }
        </section>

        <div class="actions">
          <button class="btn btn--primary" type="button" data-decide="approved" data-message="${esc(selected.id)}"
            ${selected.status === "approved" ? "disabled" : ""}>
            ${icon("i-check", 15)} Approve
          </button>
          <button class="btn btn--critical" type="button" data-decide="rejected" data-message="${esc(selected.id)}"
            ${selected.status === "rejected" ? "disabled" : ""}>
            ${icon("i-close", 15)} Reject
          </button>
          ${
            decided && selected.status !== "sent"
              ? `<button class="btn btn--quiet" type="button" data-decide="pending" data-message="${esc(selected.id)}">Clear decision</button>`
              : ""
          }
          ${
            selected.video_url
              ? `<a class="btn" href="${esc(selected.video_url)}" target="_blank" rel="noreferrer noopener">${icon("i-external", 15)} Open video</a>`
              : ""
          }
        </div>

        <p class="lede" style="font-size: var(--text-sm)">
          Decisions are written to <code>data/approvals.json</code> and mirrored into
          <code>data/pipeline_state.json</code>, so a resumed run sees the same approved set.
          Approving does not send: SMTP delivery is still disabled in the outreach agent.
        </p>
      </div>
    </div>
  `;
}

function renderRuns() {
  const data = state.runs;
  const active = data?.active?.run;
  const isActive = Boolean(data?.active?.active);

  const rows = (data?.runs || [])
    .map(
      (run) => `
        <tr>
          <td data-label="Run">
            <span>
              <span class="table__brand">${esc(run.id)}</span>${
                run.is_live_state ? ` <span class="chip chip--accent">live state</span>` : ""
              }<br />
              <span class="table__sub">${esc(run.niche || "—")} · ${esc((run.regions || []).join(", ") || "—")}</span>
            </span>
          </td>
          <td data-label="Finished"><span class="mono">${esc(when(run.completed_at || run.modified_at))}</span></td>
          <td data-label="Leads" class="num">${num(run.leads)}</td>
          <td data-label="Avg score" class="num">${score(run.average_score)}</td>
          <td data-label="Drafted" class="num">${num(run.drafted)}</td>
          <td data-label="Approved" class="num">${num(run.approved)}</td>
          <td data-label="Status">${statusChip(run.status === "completed" ? "complete" : run.status)}</td>
        </tr>
      `,
    )
    .join("");

  const config = state.config;

  return `
    <div class="page-head enter">
      <span class="tag">Orchestrator</span>
      <h1>Runs</h1>
      <p class="lede">
        Each run writes a report to <code>data/</code>. Starting a run here shells out to the same
        CLI entry point, with stdin closed so the terminal approval prompt is skipped — this console
        is the gate instead.
      </p>
    </div>

    ${
      isActive
        ? `<div class="banner">${icon("i-play", 18)}<div class="banner__body"><strong>Run in progress</strong> (pid ${esc(active.pid)}) since ${esc(when(active.started_at))}. ${esc((active.command || []).join(" "))}</div></div>`
        : active
          ? `<div class="banner${active.state === "failed" ? " banner--critical" : ""}">${icon(active.state === "failed" ? "i-alert" : "i-check", 18)}<div class="banner__body"><strong>Last launched run ${esc(active.state)}</strong> — exit code ${esc(active.exit_code ?? "—")}, finished ${esc(when(active.finished_at))}.</div></div>`
          : ""
    }

    <section class="panel">
      <div class="tag" style="display: block; margin-block-end: var(--space-md)">Start a run</div>
      <form class="toolbar" id="run-form">
        <div class="field">
          <label class="field__label" for="run-niche">Niche</label>
          <input class="input" id="run-niche" name="niche" type="text"
            placeholder="${esc(config?.market?.niche || "sustainable fashion")}" />
          <span class="field__help">Blank uses <code>market.niche</code> from config.yaml.</span>
        </div>
        <div class="field">
          <label class="field__label" for="run-regions">Regions</label>
          <input class="input" id="run-regions" name="regions" type="text"
            placeholder="${esc((config?.market?.regions || []).join(",") || "US,GB,CA")}" />
          <span class="field__help">Comma-separated ISO codes.</span>
        </div>
        <div class="field">
          <label class="field__label" for="run-submit">&nbsp;</label>
          <button class="btn btn--primary" id="run-submit" type="submit" ${isActive ? "disabled" : ""}>
            ${icon("i-play", 15)} ${isActive ? "Run in progress" : "Start run"}
          </button>
          <span class="field__help">${isActive ? "One run at a time." : "Runs in the background."}</span>
        </div>
        <div class="field">
          <label class="field__label" for="run-resume">&nbsp;</label>
          <button class="btn" id="run-resume" type="button" data-action="resume-run" ${isActive ? "disabled" : ""}>
            Resume last
          </button>
          <span class="field__help">Continues from <code>pipeline_state.json</code>.</span>
        </div>
      </form>
    </section>

    <section class="panel panel--flush">
      <div class="panel__head">
        <h2 style="font-size: var(--text-md)">History</h2>
        <span class="tag">${num((data?.runs || []).length)} recorded</span>
      </div>
      ${
        rows
          ? `<div class="table-scroll">
              <table class="table">
                <thead>
                  <tr>
                    <th scope="col">Run</th>
                    <th scope="col">Finished</th>
                    <th scope="col" class="num">Leads</th>
                    <th scope="col" class="num">Avg score</th>
                    <th scope="col" class="num">Drafted</th>
                    <th scope="col" class="num">Approved</th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>${rows}</tbody>
              </table>
            </div>`
          : `<div class="panel__body">${emptyState(
              "No runs recorded",
              "Start one above, or run the CLI directly.",
              'python run_pipeline.py "sustainable fashion" --regions US,GB,CA',
            )}</div>`
      }
    </section>
  `;
}

const RENDERERS = {
  overview: renderOverview,
  leads: renderLeads,
  market: renderMarket,
  approvals: renderApprovals,
  runs: renderRuns,
};

/* ----------------------------------------------------------------- chrome */

function renderRail() {
  const counts = {
    leads: state.overview?.totals?.leads,
    approvals: state.overview?.totals?.awaiting_decision,
    market: state.overview?.totals?.advertisers,
    runs: (state.runs?.runs || []).length || undefined,
  };

  railNav.innerHTML = SECTIONS.map(
    (section) => `
      <a
        class="rail__link"
        href="#${section.id}"
        ${state.section === section.id ? 'aria-current="page"' : ""}
      >
        ${icon(section.icon, 17)}
        <span>${esc(section.label)}</span>
        ${
          counts[section.id] !== undefined && counts[section.id] !== null
            ? `<span class="rail__count">${num(counts[section.id])}</span>`
            : ""
        }
      </a>
    `,
  ).join("");

  const health = state.health;
  railStatus.innerHTML = `
    <span class="tag">Environment</span>
    <span class="table__sub">
      ${
        health
          ? health.mock_mode
            ? "All sources mocked"
            : `${health.integrations.filter((item) => item.configured).length}/${health.integrations.length} sources live`
          : "checking…"
      }
    </span>
    <span class="table__sub mono">${esc(state.config?.market?.niche || "")}</span>
  `;

  const send = state.overview?.summaries?.send;
  statusline.innerHTML = [
    `run ${esc(state.overview?.run_id || "—")}`,
    `<span class="statusline__dot">·</span>`,
    `${num(state.overview?.totals?.leads)} leads`,
    `<span class="statusline__dot">·</span>`,
    `${num(send?.total_drafted)} drafted`,
    `<span class="statusline__dot">·</span>`,
    `${num(state.overview?.totals?.approved)} approved`,
    `<span class="statusline__dot">·</span>`,
    `sending disabled in outreach agent`,
  ].join(" ");
}

function renderView() {
  view.setAttribute("aria-busy", "false");
  view.innerHTML = RENDERERS[state.section]();
  renderRail();
  bindViewEvents();
}

/* ------------------------------------------------------------------ events */

function bindViewEvents() {
  view.querySelectorAll("[data-filter]").forEach((control) => {
    const key = control.dataset.filter;
    const eventName = control.tagName === "SELECT" ? "change" : "input";
    let timer = null;
    control.addEventListener(eventName, (event) => {
      const value = event.target.value;
      window.clearTimeout(timer);
      timer = window.setTimeout(
        async () => {
          state.leadQuery[key] = value;
          await loadLeads();
          renderView();
          const restored = view.querySelector(`[data-filter="${key}"]`);
          if (restored) {
            restored.focus({ preventScroll: true });
            if (restored.setSelectionRange && restored.type === "search") {
              const end = restored.value.length;
              restored.setSelectionRange(end, end);
            }
          }
        },
        eventName === "input" ? 220 : 0,
      );
    });
  });

  view.querySelectorAll("[data-lead]").forEach((row) => {
    const open = () => openLeadDrawer(row.dataset.lead);
    row.addEventListener("click", open);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
  });

  view.querySelectorAll("[data-message]").forEach((node) => {
    if (node.dataset.decide) return;
    node.addEventListener("click", () => {
      state.selectedMessage = node.dataset.message;
      renderView();
    });
  });

  view.querySelectorAll("[data-decide]").forEach((button) => {
    button.addEventListener("click", () => decide(button, button.dataset.message, button.dataset.decide));
  });

  const runForm = view.querySelector("#run-form");
  if (runForm) {
    runForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const niche = runForm.querySelector("#run-niche").value.trim();
      const regions = runForm
        .querySelector("#run-regions")
        .value.split(",")
        .map((value) => value.trim())
        .filter(Boolean);
      startRun(runForm.querySelector("#run-submit"), { niche: niche || null, regions, resume: false });
    });
  }

  const resumeButton = view.querySelector('[data-action="resume-run"]');
  if (resumeButton) {
    resumeButton.addEventListener("click", () => startRun(resumeButton, { resume: true }));
  }
}

async function decide(button, messageId, status) {
  const previous = (state.messages?.messages || []).find((message) => message.id === messageId);
  button.setAttribute("aria-busy", "true");
  try {
    await api(`/messages/${encodeURIComponent(messageId)}/decision`, {
      method: "POST",
      body: JSON.stringify({ status, run_id: state.messages.run_id }),
    });
    await Promise.all([loadMessages(), loadOverview()]);
    renderView();

    if (status !== "pending" && previous && previous.status !== status) {
      toast(`${messageId} marked ${status}.`, {
        kind: status === "rejected" ? "critical" : "positive",
        undo: async () => {
          await api(`/messages/${encodeURIComponent(messageId)}/decision`, {
            method: "POST",
            body: JSON.stringify({ status: previous.status, run_id: state.messages.run_id }),
          });
          await Promise.all([loadMessages(), loadOverview()]);
          renderView();
        },
      });
    }
  } catch (error) {
    button.removeAttribute("aria-busy");
    toast(error.message, { kind: "critical" });
  }
}

async function startRun(button, payload) {
  button.setAttribute("aria-busy", "true");
  try {
    await api("/runs", { method: "POST", body: JSON.stringify(payload) });
    await loadRuns();
    renderView();
    toast("Pipeline run started. This view refreshes as it progresses.");
    pollRun();
  } catch (error) {
    button.removeAttribute("aria-busy");
    toast(error.message, { kind: "critical" });
  }
}

let pollTimer = null;
function pollRun() {
  window.clearInterval(pollTimer);
  pollTimer = window.setInterval(async () => {
    await loadRuns();
    if (!state.runs?.active?.active) {
      window.clearInterval(pollTimer);
      await Promise.all([loadHealth(), loadOverview(), loadLeads(), loadMarket(), loadMessages()]);
      toast("Run finished. Data reloaded.");
    }
    renderView();
  }, 4000);
}

/* ------------------------------------------------------------------ drawer */

async function openLeadDrawer(leadId) {
  const lead = (state.leads?.leads || []).find((item) => item.id === leadId);
  if (!lead) return;

  const breakdown = lead.score_breakdown || {};
  const ads = lead.ad_presence_summary || {};
  const contact = lead.contact;
  const video = lead.video;

  document.getElementById("drawer-tag").textContent = `${lead.region || "—"} · ${lead.industry || "—"}`;
  document.getElementById("drawer-title").textContent = lead.brand_name;
  document.getElementById("drawer-body").innerHTML = `
    <section>
      <div class="tag" style="display: block; margin-block-end: var(--space-sm)">Why it qualified</div>
      <p>${esc(lead.qualification_reason || "No reason recorded.")}</p>
    </section>

    <section>
      <div class="tag" style="display: block; margin-block-end: var(--space-sm)">
        Score ${score(lead.qualification_score)} · rank ${score(lead.relative_rank_score)} in batch
      </div>
      <div class="stack--tight" style="display: flex; flex-direction: column">
        ${meter("Competitor presence", breakdown.competitor_presence)}
        ${meter("Brand maturity", breakdown.brand_maturity)}
        ${meter("Low ad presence", breakdown.low_ad_presence)}
        ${meter("Match confidence", breakdown.match_confidence, true)}
      </div>
    </section>

    <section>
      <div class="tag" style="display: block; margin-block-end: var(--space-sm)">Company</div>
      <div class="factlist">
        ${factRow("Domain", lead.domain ? `<a href="${esc(lead.domain)}" target="_blank" rel="noreferrer noopener" style="color: var(--color-accent)">${esc(lead.domain)}</a>` : "—")}
        ${factRow("Employees", num(lead.company_size))}
        ${factRow("Active ads", num(ads.active_ads))}
        ${factRow("Estimated spend", ads.spend_reliable ? num(ads.estimated_spend) : "not reported by Meta")}
      </div>
    </section>

    <section>
      <div class="tag" style="display: block; margin-block-end: var(--space-sm)">Contact</div>
      ${
        contact
          ? `<div class="factlist">
              ${factRow("Name", esc(contact.name))}
              ${factRow("Title", esc(contact.title))}
              ${factRow("Email", esc(contact.email))}
              ${factRow("LinkedIn", contact.linkedin ? `<a href="${esc(contact.linkedin)}" target="_blank" rel="noreferrer noopener" style="color: var(--color-accent)">profile</a>` : "—")}
              ${factRow("Confidence", percent(contact.confidence))}
              ${factRow("Source", esc(contact.source))}
            </div>`
          : `<p class="lede" style="font-size: var(--text-sm)">Contact enrichment found nobody for this brand. It stays in the lead list but cannot be pitched.</p>`
      }
    </section>

    ${
      video
        ? `<section class="graphite">
            <div class="graphite__head">
              <span class="graphite__label">Sample video prompt</span>
              <span class="graphite__label">${esc(when(video.generated_at))}</span>
            </div>
            <pre class="graphite__body">${esc(video.prompt)}</pre>
            <div class="graphite__foot">${esc(video.url)}</div>
          </section>`
        : `<section>
            <div class="tag" style="display: block; margin-block-end: var(--space-sm)">Sample video</div>
            <p class="lede" style="font-size: var(--text-sm)">Not generated for this lead.</p>
          </section>`
    }
  `;
  drawer.showModal();
}

/* ---------------------------------------------------------------- palette */

function paletteRows(query) {
  const needle = query.trim().toLowerCase();
  const rows = [];

  SECTIONS.forEach((section) =>
    rows.push({ kind: "section", label: section.label, action: () => navigate(section.id) }),
  );

  (state.leads?.leads || []).forEach((lead) =>
    rows.push({
      kind: "lead",
      label: `${lead.brand_name} — score ${score(lead.qualification_score)}`,
      action: () => {
        navigate("leads");
        window.setTimeout(() => openLeadDrawer(lead.id), 60);
      },
    }),
  );

  (state.messages?.messages || []).forEach((message) =>
    rows.push({
      kind: `draft · ${message.status}`,
      label: `${message.lead_id} — ${message.to_email}`,
      action: () => {
        state.selectedMessage = message.id;
        navigate("approvals");
      },
    }),
  );

  return needle ? rows.filter((row) => row.label.toLowerCase().includes(needle)) : rows;
}

function renderPalette() {
  const rows = state.paletteRows;
  if (!rows.length) {
    paletteList.innerHTML = `<div class="palette__row"><span class="palette__row-label">No matches</span></div>`;
    return;
  }
  paletteList.innerHTML = rows
    .map(
      (row, index) => `
        <div class="palette__row" role="option" aria-selected="${index === state.paletteIndex}" data-index="${index}">
          <span class="palette__row-label">${esc(row.label)}</span>
          <span class="palette__row-kind">${esc(row.kind)}</span>
        </div>
      `,
    )
    .join("");

  paletteList.querySelectorAll("[data-index]").forEach((node) => {
    node.addEventListener("click", () => {
      state.paletteIndex = Number(node.dataset.index);
      runPaletteSelection();
    });
  });

  const active = paletteList.querySelector('[aria-selected="true"]');
  if (active) active.scrollIntoView({ block: "nearest" });
}

function refreshPalette() {
  state.paletteRows = paletteRows(paletteInput.value);
  state.paletteIndex = 0;
  renderPalette();
}

function runPaletteSelection() {
  const row = state.paletteRows[state.paletteIndex];
  palette.close();
  if (row) row.action();
}

function openPalette() {
  paletteInput.value = "";
  refreshPalette();
  palette.showModal();
  paletteInput.focus({ preventScroll: true });
}

paletteInput.addEventListener("input", refreshPalette);

palette.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    state.paletteIndex = Math.min(state.paletteIndex + 1, state.paletteRows.length - 1);
    renderPalette();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    state.paletteIndex = Math.max(state.paletteIndex - 1, 0);
    renderPalette();
  } else if (event.key === "Enter") {
    event.preventDefault();
    runPaletteSelection();
  }
});

palette.addEventListener("click", (event) => {
  if (event.target === palette) palette.close();
});

drawer.addEventListener("click", (event) => {
  if (event.target === drawer) drawer.close();
});

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    if (palette.open) palette.close();
    else openPalette();
  }
});

document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-action]");
  if (!trigger) return;
  const action = trigger.dataset.action;

  if (action === "open-palette") openPalette();

  if (action === "toggle-rail") {
    const rail = document.getElementById("rail");
    const open = rail.dataset.open !== "true";
    rail.dataset.open = String(open);
    trigger.setAttribute("aria-expanded", String(open));
    trigger.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
  }

  if (action === "close-drawer") drawer.close();
});

/* -------------------------------------------------------------------- data */

async function loadHealth() {
  try {
    state.health = await api("/health");
  } catch {
    state.health = null;
  }
}

async function loadConfig() {
  try {
    state.config = await api("/config");
  } catch {
    state.config = null;
  }
}

async function loadOverview() {
  try {
    state.overview = await api("/overview");
  } catch {
    state.overview = null;
  }
}

async function loadLeads() {
  const q = state.leadQuery;
  const params = new URLSearchParams({ sort: q.sort, order: q.order });
  if (q.search) params.set("search", q.search);
  if (q.region) params.set("region", q.region);
  if (q.stage) params.set("stage", q.stage);
  try {
    state.leads = await api(`/leads?${params}`);
  } catch {
    state.leads = null;
  }
}

async function loadMarket() {
  try {
    state.market = await api("/market");
  } catch {
    state.market = null;
  }
}

async function loadMessages() {
  try {
    state.messages = await api("/messages");
  } catch {
    state.messages = null;
  }
}

async function loadRuns() {
  try {
    state.runs = await api("/runs");
  } catch {
    state.runs = null;
  }
}

/* ------------------------------------------------------------------ router */

function currentSection() {
  const hash = window.location.hash.replace("#", "");
  return SECTIONS.some((section) => section.id === hash) ? hash : "overview";
}

function navigate(sectionId) {
  if (window.location.hash === `#${sectionId}`) {
    state.section = sectionId;
    renderView();
    return;
  }
  window.location.hash = `#${sectionId}`;
}

window.addEventListener("hashchange", () => {
  state.section = currentSection();
  document.getElementById("rail").dataset.open = "false";
  const toggle = document.querySelector('[data-action="toggle-rail"]');
  if (toggle) toggle.setAttribute("aria-expanded", "false");
  renderView();
  window.scrollTo({ top: 0 });
});

async function boot() {
  state.section = currentSection();
  await Promise.all([loadHealth(), loadConfig()]);
  await Promise.all([loadOverview(), loadLeads(), loadMarket(), loadMessages(), loadRuns()]);
  renderView();
  if (state.runs?.active?.active) pollRun();
}

boot();
