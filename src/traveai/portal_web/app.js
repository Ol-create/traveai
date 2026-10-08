/* TraveAI merchant portal. Plain JS, no build step. Talks to /portal/api with the session
 * cookie; state-changing calls send the CSRF token. Never uses innerHTML for data. */
"use strict";

const VIEWS = { overview: "Overview", deliveries: "Deliveries", keys: "API keys", webhooks: "Webhooks", account: "Account" };
const STATUS_TONE = {
  delivered: "ok", scheduled: "muted", assigned: "live", picking_up: "live", airborne: "live", arriving: "live",
  aborted: "warn", returned_to_base: "warn", canceled: "bad", failed: "bad",
};
const CANCELABLE = new Set(["scheduled", "assigned", "picking_up"]);

let me = null;
let showNewSecret = null; // a secret to show once at the top of the next view

/* ---------- helpers -------------------------------------------------------------------- */

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) node.append(c instanceof Node ? c : String(c));
  return node;
}

const money = (cents) => `$${(cents / 100).toFixed(2)}`;
const label = (s) => String(s).replaceAll("_", " ");
const when = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "–");
const chip = (status) => el("span", { class: `chip ${STATUS_TONE[status] || "muted"}` }, label(status));

let toastTimer;
function toast(message, isError = false) {
  const t = $("toast");
  t.textContent = message;
  t.className = "toast" + (isError ? " error" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), isError ? 6000 : 3000);
}

class ApiFailure extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function api(path, { method = "GET", body } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && me) headers["X-CSRF-Token"] = me.csrf_token;
  const res = await fetch(`/portal/api${path}`, {
    method, headers, credentials: "same-origin", body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const message = typeof d === "object" && !Array.isArray(d) ? d.message
      : Array.isArray(d) ? d.map((e) => `${e.loc?.at(-1)}: ${e.msg}`).join("; ") : `Request failed (${res.status})`;
    if (res.status === 401 && path !== "/login") showAuth("login");
    throw new ApiFailure(res.status, d?.code, message);
  }
  return data;
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied");
  } catch {
    toast("Copy failed; select the text instead", true);
  }
}

/** A destructive button that needs a second click within 4 s. */
function confirmButton(text, onConfirm) {
  const b = el("button", { class: "danger small", type: "button" }, text);
  let timer;
  b.addEventListener("click", async () => {
    if (!b.classList.contains("armed")) {
      b.classList.add("armed");
      b.textContent = "Click again to confirm";
      timer = setTimeout(() => { b.classList.remove("armed"); b.textContent = text; }, 4000);
      return;
    }
    clearTimeout(timer);
    b.disabled = true;
    try { await onConfirm(); } catch (err) { toast(err.message, true); b.disabled = false; }
  });
  return b;
}

function secretBox(title, secret, note) {
  return el("div", { class: "secret-box", role: "alert" },
    el("strong", {}, title),
    el("code", {}, secret),
    el("div", { class: "row" },
      el("button", { class: "small", type: "button", onclick: () => copy(secret) }, "Copy"),
      el("span", { class: "muted small" }, note)));
}

/* ---------- auth ----------------------------------------------------------------------- */

function showAuth(mode) {
  me = null;
  $("app").hidden = true;
  $("auth").hidden = false;
  const signup = mode === "signup";
  $("auth-title").textContent = signup ? "Create your account" : "Log in";
  $("login-form").hidden = signup;
  $("signup-form").hidden = !signup;
  $("auth-error").textContent = "";
}

async function submitAuth(e, path) {
  e.preventDefault();
  const form = e.target;
  const body = Object.fromEntries(new FormData(form));
  const button = form.querySelector("button");
  button.disabled = true;
  $("auth-error").textContent = "";
  try {
    me = await api(path, { method: "POST", body });
    form.reset();
    if (me.first_test_key) {
      showNewSecret = secretBox("Your first test key", me.first_test_key, "Shown once. Send it as: Authorization: Bearer <key>");
    }
    history.replaceState(null, "", "#/overview"); // no hashchange: render exactly once
    enterApp();
  } catch (err) {
    $("auth-error").textContent = err.message;
  } finally {
    button.disabled = false;
  }
}

$("login-form").addEventListener("submit", (e) => submitAuth(e, "/login"));
$("signup-form").addEventListener("submit", (e) => submitAuth(e, "/signup"));


/* ---------- shell and routing ---------------------------------------------------------- */

function enterApp() {
  $("auth").hidden = true;
  $("app").hidden = false;
  $("merchant-name").textContent = `${me.merchant.name} · ${me.merchant.live_enabled ? "live enabled" : "test mode"}`;
  route();
}

function route() {
  const name = location.hash.replace(/^#\//, "") || "overview";
  if (!me) return showAuth(name === "signup" ? "signup" : "login");
  const view = VIEWS[name] ? name : "overview";
  document.querySelectorAll(".nav a[data-view]").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  $("view-title").textContent = VIEWS[view];
  document.title = `${VIEWS[view]} · TraveAI Portal`;
  const root = $("view");
  root.replaceChildren();
  if (showNewSecret) { root.append(showNewSecret); showNewSecret = null; }
  ({ overview, deliveries, keys, webhooks, account })[view](root).catch((err) => toast(err.message, true));
}

window.addEventListener("hashchange", () => {
  const name = location.hash.replace(/^#\//, "");
  if (!me && (name === "signup" || name === "login")) return showAuth(name);
  route();
});

/* ---------- overview ------------------------------------------------------------------- */

async function overview(root) {
  const u = await api("/usage?days=30");
  const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
  root.append(
    el("div", { class: "kpis" },
      kpi("Deliveries (30 days)", u.total),
      kpi("Delivered", u.delivered),
      kpi("Spend", money(u.spend_cents)),
      kpi("On time", pct(u.on_time_rate)),
      kpi("Avg. time to door", u.avg_delivery_minutes == null ? "–" : `${u.avg_delivery_minutes} min`)),
  );
  const max = Math.max(1, ...u.daily.map((d) => d.deliveries));
  const bars = el("div", { class: "bars", role: "img", "aria-label": "Deliveries per day, last 30 days" });
  for (const d of u.daily) {
    const bar = el("div", { class: `bar${d.deliveries ? "" : " zero"}`, title: `${d.date}: ${d.deliveries}` });
    bar.style.height = `${(d.deliveries / max) * 100}%`; // CSSOM, allowed by the CSP
    bars.append(bar);
  }
  root.append(el("section", { class: "card" },
    el("h2", {}, "Deliveries per day"), bars,
    el("div", { class: "bars-axis" }, el("span", {}, u.daily[0].date), el("span", {}, "today"))));

  if (u.total === 0) {
    root.append(el("section", { class: "card" },
      el("h2", {}, "Get started"),
      el("ol", { class: "checklist" },
        el("li", {}, "Copy your test key from ", el("a", { href: "#/keys" }, "API keys"), "."),
        el("li", {}, "Follow the quickstart in the ", el("a", { href: "/docs", target: "_blank", rel: "noopener" }, "API docs"), ": quote, then book."),
        el("li", {}, "Add a ", el("a", { href: "#/webhooks" }, "webhook"), " to hear about every status change."),
        el("li", {}, "Watch test drones fly on the ", el("a", { href: "/dashboard/", target: "_blank", rel: "noopener" }, "live map"), "."))));
  } else {
    root.append(el("section", { class: "card" }, el("h2", {}, "By status"),
      el("div", { class: "row" }, Object.entries(u.by_status).map(([s, n]) => el("span", {}, chip(s), ` ${n}`)))));
  }
}

const kpi = (labelText, value) => el("div", { class: "kpi" }, el("div", { class: "label" }, labelText), el("div", { class: "value" }, value));

/* ---------- deliveries ----------------------------------------------------------------- */

async function deliveries(root) {
  const filters = el("form", { class: "row card" },
    el("label", {}, "Status", el("select", { name: "status" },
      el("option", { value: "" }, "All"),
      ...Object.keys(STATUS_TONE).map((s) => el("option", { value: s }, label(s))))),
    el("label", {}, "Order # or delivery id", el("input", { name: "q", maxlength: "64", placeholder: "e.g. RX-20931" })),
    el("button", { type: "submit" }, "Search"));
  const tbody = el("tbody");
  const more = el("button", { class: "ghost", type: "button", hidden: true }, "Load more");
  const detail = el("section", { class: "card detail", hidden: true });
  root.append(filters,
    el("section", { class: "card" }, el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Created", "Status", "Order #", "Type", "Price", "Delivered"].map((h) => el("th", {}, h)))),
      tbody)), more),
    detail);

  let cursor = null;
  async function load(reset) {
    const f = new FormData(filters);
    const params = new URLSearchParams({ limit: "25" });
    if (f.get("status")) params.set("status", f.get("status"));
    if (f.get("q")) params.set("q", f.get("q").trim());
    if (!reset && cursor) params.set("starting_after", cursor);
    const page = await api(`/deliveries?${params}`);
    if (reset) tbody.replaceChildren();
    for (const d of page.data) {
      const tr = el("tr", { class: "clickable", tabindex: "0" },
        el("td", {}, when(d.created_at)), el("td", {}, chip(d.status)),
        el("td", { class: "mono" }, d.external_reference || "–"), el("td", {}, label(d.payload.category)),
        el("td", { class: "num" }, money(d.price.amount_cents)),
        el("td", {}, d.proof ? when(d.proof.delivered_at) : "–"));
      const open = () => { tbody.querySelectorAll("tr").forEach((r) => r.classList.remove("selected")); tr.classList.add("selected"); showDelivery(detail, d.id); };
      tr.addEventListener("click", open);
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      tbody.append(tr);
    }
    if (reset && !page.data.length) tbody.append(el("tr", {}, el("td", { colspan: "6", class: "empty" }, "No deliveries match.")));
    cursor = page.data.at(-1)?.id || cursor;
    more.hidden = !page.has_more;
  }
  filters.addEventListener("submit", (e) => { e.preventDefault(); cursor = null; load(true).catch((err) => toast(err.message, true)); });
  more.addEventListener("click", () => load(false).catch((err) => toast(err.message, true)));
  await load(true);
}

async function showDelivery(box, id) {
  const [d, events] = await Promise.all([api(`/deliveries/${id}`), api(`/deliveries/${id}/events`)]);
  const rows = [
    ["Delivery id", el("span", { class: "mono" }, d.id)],
    ["Status", chip(d.status)],
    ["Order #", d.external_reference || "–"],
    ["Recipient", [d.recipient.name, d.recipient.phone].filter(Boolean).join(" · ") || "–"],
    ["Payload", `${label(d.payload.category)}${d.payload.description ? ` · ${d.payload.description}` : ""} · ${d.payload.weight_kg} kg`],
    ["Price", money(d.price.amount_cents)],
    ["Estimated drop-off", when(d.estimated_dropoff_at)],
    ["Delivered", d.proof ? when(d.proof.delivered_at) : "–"],
    ["Problem", d.failure_reason ? label(d.failure_reason) : "–"],
    ["Customer link", el("span", {}, el("a", { href: d.tracking_url, target: "_blank", rel: "noopener noreferrer" }, "Open"), " · ",
      el("button", { class: "ghost small", type: "button", onclick: () => copy(d.tracking_url) }, "Copy"))],
  ];
  box.replaceChildren(
    el("div", { class: "row" }, el("h2", {}, "Delivery details"),
      CANCELABLE.has(d.status) ? confirmButton("Cancel delivery", async () => {
        await api(`/deliveries/${id}/cancel`, { method: "POST" });
        toast("Delivery canceled");
        showDelivery(box, id);
      }) : null),
    el("dl", {}, rows.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)])),
    el("h2", {}, "Timeline"),
    el("ul", { class: "timeline" }, events.data.map((e) => el("li", {},
      el("time", {}, when(e.created_at)),
      el("span", {}, e.type === "delivery.custody" ? `custody: ${label(e.data.action)}` : label(e.type.replace("delivery.", "")),
        e.data.reason ? ` (${label(e.data.reason)})` : "")))));
  box.hidden = false;
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ---------- API keys ------------------------------------------------------------------- */

async function keys(root) {
  const { data } = await api("/keys");
  const live = me.merchant.live_enabled;
  const form = el("form", { class: "row" },
    el("label", {}, "Label", el("input", { name: "label", maxlength: "60", placeholder: "e.g. Checkout server" })),
    el("label", {}, "Mode", el("select", { name: "mode" },
      el("option", { value: "test" }, "Test (simulated deliveries)"),
      el("option", { value: "live", disabled: !live }, live ? "Live" : "Live (after verification)"))),
    el("button", { type: "submit" }, "Create key"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      const k = await api("/keys", { method: "POST", body: Object.fromEntries(new FormData(form)) });
      showNewSecret = secretBox(`New ${k.mode} key${k.label ? `: ${k.label}` : ""}`, k.secret, "Shown once; we only keep a hash.");
      route();
    } catch (err) { toast(err.message, true); }
  });
  root.append(
    el("section", { class: "card" }, el("h2", {}, "Create a key"), form,
      live ? null : el("p", { class: "muted small" }, "Live keys unlock once we've verified your business. Test keys work with the simulator.")),
    el("section", { class: "card" }, el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Label", "Key", "Mode", "Created", "Status", ""].map((h) => el("th", {}, h)))),
      el("tbody", {}, data.length ? data.map((k) => el("tr", {},
        el("td", {}, k.label || "–"),
        el("td", { class: "mono" }, `${k.prefix}…`),
        el("td", {}, el("span", { class: `chip ${k.mode === "live" ? "live" : "muted"}` }, k.mode)),
        el("td", {}, when(k.created_at)),
        el("td", {}, k.revoked_at ? el("span", { class: "chip bad" }, "revoked") : el("span", { class: "chip ok" }, "active")),
        el("td", {}, k.revoked_at ? null : confirmButton("Revoke", async () => {
          await api(`/keys/${k.id}/revoke`, { method: "POST" });
          toast("Key revoked: requests with it now fail");
          route();
        }))))
        : el("tr", {}, el("td", { colspan: "6", class: "empty" }, "No keys yet.")))))));
}

/* ---------- webhooks ------------------------------------------------------------------- */

async function webhooks(root) {
  const { data } = await api("/webhooks");
  const form = el("form", { class: "row" },
    el("label", {}, "URL", el("input", { name: "url", type: "url", required: true, placeholder: "https://example.com/traveai/webhooks" })),
    el("label", {}, "Events", el("select", { name: "events" },
      el("option", { value: "delivery.*" }, "All delivery events"),
      el("option", { value: "delivery.delivered,delivery.failed,delivery.canceled" }, "Final outcomes only"))),
    el("label", {}, "Mode", el("select", { name: "mode" },
      el("option", { value: "test" }, "Test"),
      el("option", { value: "live", disabled: !me.merchant.live_enabled }, "Live"))),
    el("button", { type: "submit" }, "Add endpoint"));
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    try {
      const ep = await api("/webhooks", { method: "POST", body: { url: f.get("url"), enabled_events: f.get("events").split(","), mode: f.get("mode") } });
      showNewSecret = secretBox("Signing secret for " + ep.url, ep.secret, "Shown once. Verify the TraveAI-Signature header with it.");
      route();
    } catch (err) { toast(err.message, true); }
  });
  root.append(el("section", { class: "card" }, el("h2", {}, "Add an endpoint"), form,
    el("p", { class: "muted small" }, "Live endpoints must be public HTTPS. Test endpoints may use http://localhost.")));

  if (!data.length) root.append(el("section", { class: "card empty" }, "No webhook endpoints yet."));
  for (const ep of data) {
    const log = el("div", { hidden: true });
    root.append(el("section", { class: "card" },
      el("div", { class: "row" },
        el("strong", { class: "mono" }, ep.url),
        el("span", { class: `chip ${ep.livemode ? "live" : "muted"}` }, ep.livemode ? "live" : "test"),
        el("span", { class: "muted small" }, ep.enabled_events.join(", "))),
      el("div", { class: "row" },
        el("button", { class: "ghost small", type: "button", onclick: () => toggleLog(ep.id, log) }, "Recent deliveries"),
        el("button", { class: "ghost small", type: "button", onclick: async () => {
          try {
            const r = await api(`/webhooks/${ep.id}/rotate_secret`, { method: "POST" });
            showNewSecret = secretBox("New signing secret for " + r.url, r.secret, "The old secret stops working now.");
            route();
          } catch (err) { toast(err.message, true); }
        } }, "Rotate secret"),
        confirmButton("Delete", async () => { await api(`/webhooks/${ep.id}`, { method: "DELETE" }); toast("Endpoint deleted"); route(); })),
      log));
  }
}

async function toggleLog(id, box) {
  box.hidden = !box.hidden;
  if (box.hidden) return;
  const { data } = await api(`/webhooks/${id}/messages`);
  box.replaceChildren(data.length ? el("div", { class: "table-wrap" }, el("table", {},
    el("thead", {}, el("tr", {}, ["Event", "Status", "Attempts", "Last result", "Created"].map((h) => el("th", {}, h)))),
    el("tbody", {}, data.map((m) => el("tr", {},
      el("td", { class: "mono" }, m.event_type),
      el("td", {}, el("span", { class: `chip ${m.status === "succeeded" ? "ok" : m.status === "failed" ? "bad" : "warn"}` }, m.status)),
      el("td", { class: "num" }, m.attempts),
      el("td", {}, m.last_error || (m.last_status_code ? `HTTP ${m.last_status_code}` : "–")),
      el("td", {}, when(m.created_at)))))))
    : el("p", { class: "empty" }, "Nothing sent yet."));
}

/* ---------- account -------------------------------------------------------------------- */

async function account(root) {
  const m = me.merchant;
  root.append(el("section", { class: "card detail" },
    el("h2", {}, "Business"),
    el("dl", {},
      el("dt", {}, "Name"), el("dd", {}, m.name),
      el("dt", {}, "Type"), el("dd", {}, label(m.category)),
      el("dt", {}, "Merchant id"), el("dd", { class: "mono" }, m.id),
      el("dt", {}, "Live mode"), el("dd", {}, m.live_enabled ? el("span", { class: "chip ok" }, "enabled")
        : el("span", {}, el("span", { class: "chip warn" }, "pending verification"), " Contact us to verify your business.")))),
  el("section", { class: "card detail" },
    el("h2", {}, "You"),
    el("dl", {},
      el("dt", {}, "Name"), el("dd", {}, me.user.name),
      el("dt", {}, "Email"), el("dd", {}, me.user.email),
      el("dt", {}, "Role"), el("dd", {}, me.user.role)),
    el("button", { class: "ghost", type: "button", onclick: async () => {
      await api("/logout", { method: "POST" });
      showAuth("login");
      location.hash = "#/login";
    } }, "Log out")));
}

/* ---------- start ---------------------------------------------------------------------- */

(async () => {
  try {
    me = await api("/me");
    if (["#/login", "#/signup", ""].includes(location.hash)) history.replaceState(null, "", "#/overview");
    enterApp();
  } catch {
    showAuth(location.hash === "#/signup" ? "signup" : "login");
  }
})();
