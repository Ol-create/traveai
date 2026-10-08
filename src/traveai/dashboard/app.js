/* TraveAI ops dashboard: live map + test-delivery form. Plain JS, no build step.
 * Talks to the same API merchants use, with the API key pasted in the top bar. */
"use strict";

const LIVE_EVERY_MS = 1000;
const STATIC_EVERY_MS = 60000;
const KEY_STORAGE = "traveai.apiKey";
const DALLAS = [32.795, -96.8];

const ROUTES = {
  "de-lw": { pickup: [32.7843, -96.7837], dropoff: [32.812, -96.752] },
  "md-up": { pickup: [32.8125, -96.84], dropoff: [32.801, -96.801] },
  "ba-dh": { pickup: [32.7486, -96.827], dropoff: [32.79, -96.781] },
  "w-e": { pickup: [32.76, -96.93], dropoff: [32.76, -96.86] },
};
const PAYLOADS = {
  burrito: { category: "food", weight_kg: 1.2, length_cm: 30, width_cm: 25, height_cm: 15, description: "Burrito bowl" },
  pizza: { category: "food", weight_kg: 2.0, length_cm: 40, width_cm: 40, height_cm: 8, temperature_controlled: true, description: "Hot pizza" },
  insulin: { category: "medical", weight_kg: 0.4, length_cm: 15, width_cm: 10, height_cm: 8, temperature_controlled: true, prescription: true, description: "Insulin pens" },
  lab: { category: "medical", weight_kg: 0.3, length_cm: 20, width_cm: 10, height_cm: 10, temperature_controlled: true, description: "Blood samples" },
  heavy: { category: "food", weight_kg: 3.0, length_cm: 40, width_cm: 30, height_cm: 30, description: "Catering box" },
};
const CANCELABLE = new Set(["scheduled", "assigned", "picking_up"]);
const FINAL = new Set(["delivered", "canceled", "failed"]);

const state = {
  key: null,
  me: null,
  pickMode: null,
  points: { ...ROUTES["de-lw"] },
  quote: null,
  selected: null,
  pins: {}, // delivery id -> PIN (kept in memory only)
  cards: new Map(), // delivery id -> card elements
  drones: new Map(), // vehicle id -> marker
  tracks: new Map(), // delivery id -> map layers
  liveBusy: false,
};

/* ---------- small helpers ---------------------------------------------------------------- */

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) node.append(c instanceof Node ? c : String(c));
  return node;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function fmtEta(seconds) {
  if (seconds == null) return "–";
  if (seconds <= 0) return "now";
  const m = Math.floor(seconds / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return m ? `${m} min ${s} s` : `${s} s`;
}

const fmtMoney = (cents) => `$${(cents / 100).toFixed(2)}`;
const fmtTime = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const label = (s) => s.replaceAll("_", " ");

let toastTimer;
function toast(message, isError = false) {
  const t = $("#toast");
  t.textContent = message;
  t.className = "toast" + (isError ? " error" : "");
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), isError ? 6000 : 3500);
}

function storage(action, value) {
  try {
    if (action === "get") return localStorage.getItem(KEY_STORAGE);
    if (action === "set") localStorage.setItem(KEY_STORAGE, value);
  } catch {
    /* private mode or blocked storage: just don't remember the key */
  }
  return null;
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      Authorization: `Bearer ${state.key}`,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
    },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const msg = typeof d === "string" ? d : d?.message || (Array.isArray(d) ? d.map((e) => e.msg).join("; ") : `HTTP ${res.status}`);
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return data;
}

/* ---------- map -------------------------------------------------------------------------- */

const map = L.map("map", { zoomControl: true }).setView(DALLAS, 12);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
}).addTo(map);

const layers = {
  airspace: L.layerGroup().addTo(map),
  places: L.layerGroup().addTo(map),
  tracks: L.layerGroup().addTo(map),
  form: L.layerGroup().addTo(map),
  drones: L.layerGroup().addTo(map),
};

function pinIcon(letter, color) {
  return L.divIcon({
    className: "pin-icon",
    html: `<div style="background:${color}"><span>${esc(letter)}</span></div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 22],
  });
}

function droneIcon(d) {
  return L.divIcon({
    className: "drone-icon" + (d.delivery_id ? " mine" : ""),
    html: `<div class="body st-${esc(d.status)}"></div><div class="tag">${esc(d.call_sign)}</div>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  });
}

function drawStatic(data) {
  layers.airspace.clearLayers();
  layers.places.clearLayers();
  const css = getComputedStyle(document.documentElement);
  const red = css.getPropertyValue("--nofly").trim();
  const blue = css.getPropertyValue("--airport").trim();
  const gray = css.getPropertyValue("--st-idle").trim();

  L.geoJSON(data.airspace, {
    style: (f) => {
      const p = f.properties;
      if (p.no_fly && p.active) return { color: red, weight: 1.5, fillColor: red, fillOpacity: 0.16 };
      if (p.no_fly) return { color: gray, weight: 1.2, dashArray: "5 5", fillOpacity: 0 };
      return { color: blue, weight: 1, fillColor: blue, fillOpacity: 0.04 };
    },
    onEachFeature: (f, layer) => {
      const p = f.properties;
      const note = p.no_fly ? (p.active ? "no-fly now" : "not active now") : "LAANC authorization needed";
      layer.bindTooltip(`<strong>${esc(p.name)}</strong><br>${esc(note)}`, { sticky: true });
    },
  }).addTo(layers.airspace);

  for (const f of data.airspace.features) {
    const p = f.properties;
    if (!p.laanc_ceilings) continue;
    for (const ring of p.laanc_ceilings) {
      L.circle(p.center, { radius: ring.within_m, color: blue, weight: 0.8, dashArray: "2 4", fill: false, interactive: false })
        .addTo(layers.airspace);
    }
    const ceilings = p.laanc_ceilings.map((r) => `≤ ${(r.within_m / 1000).toFixed(1)} km: ${r.ceiling_ft} ft`).join("<br>");
    L.circleMarker(p.center, { radius: 3, color: blue, fillOpacity: 1 })
      .bindTooltip(`<strong>${esc(p.name)}</strong><br>Max altitude<br>${ceilings}`)
      .addTo(layers.airspace);
  }

  for (const hub of data.hubs) {
    L.marker([hub.lat, hub.lng], {
      icon: L.divIcon({ className: "hub-icon", html: "<div>H</div>", iconSize: [20, 20], iconAnchor: [10, 10] }),
      zIndexOffset: -100,
    })
      .bindTooltip(`<strong>Hub</strong><br>${hub.drones.map(esc).join(", ")}`)
      .addTo(layers.places);
  }
  for (const z of data.drop_zones) {
    L.circleMarker([z.lat, z.lng], { radius: 5, color: "#15803d", weight: 2, fillOpacity: 0.5 })
      .bindTooltip(`<strong>${esc(z.label)}</strong><br>Drop zone · ${esc(label(z.kind))}`)
      .addTo(layers.places);
  }
}

function drawDrones(drones) {
  const seen = new Set();
  for (const d of drones) {
    seen.add(d.id);
    let marker = state.drones.get(d.id);
    if (!marker) {
      marker = L.marker([d.lat, d.lng], { icon: droneIcon(d), zIndexOffset: 500 }).addTo(layers.drones);
      state.drones.set(d.id, marker);
    }
    marker.setLatLng([d.lat, d.lng]);
    const key = `${d.status}|${d.delivery_id}`;
    if (marker._key !== key) {
      marker.setIcon(droneIcon(d));
      marker._key = key;
    }
    marker.bindTooltip(
      `<strong>${esc(d.call_sign)}</strong> · ${esc(d.model)}<br>${esc(label(d.status))} · ` +
        `${d.battery_pct}% battery · ${Math.round(d.altitude_ft)} ft` +
        (d.delivery_id ? "<br>Flying your delivery" : ""),
    );
  }
  for (const [id, marker] of state.drones) {
    if (!seen.has(id)) {
      marker.remove();
      state.drones.delete(id);
    }
  }
}

function drawTracks(deliveries) {
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
  const seen = new Set();
  for (const d of deliveries) {
    if (FINAL.has(d.status) && d.id !== state.selected) continue;
    seen.add(d.id);
    let t = state.tracks.get(d.id);
    if (!t) {
      t = {
        flown: L.polyline([], { color: "#98a2b3", weight: 3, opacity: 0.7 }),
        ahead: L.polyline([], { color: accent, weight: 3, dashArray: "6 6" }),
        pickup: L.marker(d.pickup, { icon: pinIcon("P", "#0891b2") }),
        dropoff: L.marker(d.dropoff, { icon: pinIcon("D", "#15803d") }),
      };
      Object.values(t).forEach((layer) => layer.addTo(layers.tracks));
      t.pickup.bindTooltip("Pickup");
      t.dropoff.bindTooltip("Drop-off");
      state.tracks.set(d.id, t);
    }
    const i = d.next_waypoint_index ?? 0;
    const route = d.route || [];
    t.flown.setLatLngs(route.slice(0, i));
    t.ahead.setLatLngs(route.length ? route.slice(Math.max(0, i - 1)) : [d.pickup, d.dropoff]);
    const weight = d.id === state.selected ? 5 : 3;
    t.ahead.setStyle({ weight });
    t.flown.setStyle({ weight });
  }
  for (const [id, t] of state.tracks) {
    if (!seen.has(id)) {
      Object.values(t).forEach((layer) => layer.remove());
      state.tracks.delete(id);
    }
  }
}

/* ---------- new delivery form ------------------------------------------------------------ */

function drawFormPoints() {
  layers.form.clearLayers();
  const { pickup, dropoff } = state.points;
  if (pickup) L.marker(pickup, { icon: pinIcon("P", "#64748b"), opacity: 0.85 }).bindTooltip("New pickup").addTo(layers.form);
  if (dropoff) L.marker(dropoff, { icon: pinIcon("D", "#64748b"), opacity: 0.85 }).bindTooltip("New drop-off").addTo(layers.form);
  if (pickup && dropoff) L.polyline([pickup, dropoff], { color: "#64748b", weight: 2, dashArray: "2 6" }).addTo(layers.form);
  const f = (p) => (p ? `${p[0].toFixed(4)}, ${p[1].toFixed(4)}` : "not set");
  $("#points").textContent = `Pickup ${f(pickup)} → drop-off ${f(dropoff)}`;
}

function setPickMode(mode) {
  state.pickMode = state.pickMode === mode ? null : mode;
  document.querySelectorAll("[data-pick]").forEach((b) => b.classList.toggle("active", b.dataset.pick === state.pickMode));
  const hint = $("#pick-hint");
  hint.hidden = !state.pickMode;
  hint.textContent = state.pickMode ? `Click the map to set the ${state.pickMode === "pickup" ? "pickup" : "drop-off"}` : "";
  map.getContainer().style.cursor = state.pickMode ? "crosshair" : "";
}

map.on("click", (e) => {
  if (!state.pickMode) return;
  state.points[state.pickMode] = [e.latlng.lat, e.latlng.lng];
  $("#route-preset").value = "custom";
  const next = state.pickMode === "pickup" && !state.points.dropoff ? "dropoff" : null;
  setPickMode(state.pickMode); // turn off
  if (next) setPickMode(next);
  clearQuote();
  drawFormPoints();
});

document.querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => setPickMode(b.dataset.pick)));

$("#route-preset").addEventListener("change", (e) => {
  clearQuote();
  if (e.target.value === "custom") {
    state.points = { pickup: null, dropoff: null };
    setPickMode("pickup");
  } else {
    state.points = { ...ROUTES[e.target.value] };
    map.fitBounds([state.points.pickup, state.points.dropoff], { padding: [80, 80], maxZoom: 14 });
  }
  drawFormPoints();
});
["#payload-preset", "#priority"].forEach((s) => $(s).addEventListener("change", clearQuote));

function clearQuote() {
  state.quote = null;
  $("#book-btn").disabled = true;
  $("#quote-result").hidden = true;
}

function renderQuote(q) {
  const box = $("#quote-result");
  box.replaceChildren();
  if (q.feasible) {
    box.append(
      el("div", { class: "headline" },
        el("span", { class: "price" }, fmtMoney(q.price.amount_cents)),
        el("span", { class: "ok" }, `Arrives in ~${Math.round(q.eta_seconds / 60)} min`)),
      el("div", { class: "muted small" },
        `${(q.distance_m / 1000).toFixed(1)} km flown at ${q.cruise_altitude_ft} ft · ` +
        `wind gusts ${q.weather.wind_gust_mps} m/s · expires ${fmtTime(q.expires_at)}`),
      el("ul", { class: "small" }, q.price.breakdown.map((li) => el("li", {}, `${label(li.item)}: ${fmtMoney(li.amount_cents)}`))),
    );
    if (q.requirements.length) {
      box.append(el("div", { class: "small warn" }, "Requires: " + q.requirements.map(label).join(", ")));
    }
  } else {
    box.append(
      el("div", { class: "bad" }, el("strong", {}, "Can't deliver this right now")),
      el("ul", { class: "small" }, q.reasons.map((r) => el("li", {}, el("strong", {}, label(r.code)), ` – ${r.message}`))),
    );
  }
  box.hidden = false;
}

$("#quote-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!state.key) return toast("Connect with an API key first", true);
  const { pickup, dropoff } = state.points;
  if (!pickup || !dropoff) return toast("Set a pickup and a drop-off", true);
  try {
    const q = await api("/v1/quotes", {
      method: "POST",
      body: {
        pickup: { lat: pickup[0], lng: pickup[1] },
        dropoff: { lat: dropoff[0], lng: dropoff[1] },
        payload: PAYLOADS[$("#payload-preset").value],
        priority: $("#priority").value,
      },
    });
    state.quote = q;
    renderQuote(q);
    $("#book-btn").disabled = !q.feasible;
  } catch (err) {
    toast(err.message, true);
  }
});

$("#book-btn").addEventListener("click", async () => {
  if (!state.quote) return;
  const ref = $("#external-ref").value.trim();
  try {
    const d = await api("/v1/deliveries", {
      method: "POST",
      body: { quote_id: state.quote.id, ...(ref ? { external_reference: ref } : {}) },
    });
    if (d.recipient_pin) state.pins[d.id] = d.recipient_pin;
    state.selected = d.id;
    clearQuote();
    $("#external-ref").value = "";
    toast(d.recipient_pin ? `Booked. Recipient PIN: ${d.recipient_pin}` : "Booked. Watch it fly!");
    refreshLive();
  } catch (err) {
    toast(err.message, true);
  }
});

/* ---------- deliveries list -------------------------------------------------------------- */

function createCard(d) {
  const refs = {};
  refs.root = el("li", { class: "delivery", onclick: () => select(d.id) });
  refs.chip = el("span", { class: "chip" });
  refs.title = el("strong");
  refs.eta = el("span", { class: "small" });
  refs.meta = el("div", { class: "meta" });
  refs.pin = el("div", { class: "pin", hidden: true });
  refs.btns = el("div", { class: "btns", onclick: (e) => e.stopPropagation() });
  refs.timeline = el("ul", { class: "timeline list", hidden: true });

  refs.cancel = el("button", { class: "danger small", type: "button", onclick: () => act(d.id, "cancel") }, "Cancel");
  refs.pinInput = el("input", {
    class: "pin-input", inputmode: "numeric", maxlength: "8", placeholder: "PIN",
    "aria-label": "Recipient PIN",
  });
  refs.handoff = el("button", { class: "small", type: "button", onclick: () => handoff(d.id, refs.pinInput.value) }, "Release package");
  refs.failKind = el("select", { "aria-label": "Failure to inject" },
    el("option", { value: "high_wind" }, "High wind"),
    el("option", { value: "low_battery" }, "Low battery"),
    el("option", { value: "drop_zone_blocked" }, "Drop zone blocked"));
  refs.inject = el("button", { class: "ghost small", type: "button", onclick: () => act(d.id, "inject", refs.failKind.value) }, "Inject failure");
  refs.history = el("button", { class: "ghost small", type: "button", onclick: () => toggleTimeline(d.id) }, "Timeline");
  // The recipient's public page (what the merchant texts to their customer).
  refs.customer = el("a", { class: "ghost small btn-link", target: "_blank", rel: "noopener noreferrer" }, "Customer page");
  refs.btns.append(refs.cancel, refs.pinInput, refs.handoff, refs.failKind, refs.inject, refs.history, refs.customer);

  refs.root.append(el("div", { class: "top" }, refs.title, refs.chip), refs.meta, refs.pin, refs.btns, refs.timeline);
  return refs;
}

function updateCard(refs, d) {
  refs.root.classList.toggle("selected", d.id === state.selected);
  refs.chip.className = `chip ${d.status}`;
  refs.chip.textContent = label(d.status);
  refs.title.textContent = `${d.description || d.category}${d.external_reference ? ` · ${d.external_reference}` : ""}`;

  const parts = [];
  const heading = !FINAL.has(d.status) && !["aborted", "returned_to_base"].includes(d.status);
  if (heading && d.eta_seconds != null && d.phase !== "returning") parts.push(`ETA ${fmtEta(d.eta_seconds)}`);
  if (d.drone) parts.push(`${d.drone}${d.phase ? ` · ${label(d.phase)}` : ""}`);
  if (d.remaining_m) parts.push(`${(d.remaining_m / 1000).toFixed(1)} km to go`);
  if (d.priority !== "standard") parts.push(d.priority);
  // Only for problem statuses: a retried delivery keeps the old reason on record.
  const troubled = ["aborted", "returned_to_base", "failed", "canceled"].includes(d.status);
  if (troubled && d.failure_reason) parts.push(`reason: ${label(d.failure_reason)}`);
  refs.meta.textContent = parts.join(" · ") || fmtTime(d.created_at);

  const pin = state.pins[d.id];
  refs.pin.hidden = !(pin && !FINAL.has(d.status));
  if (pin) refs.pin.replaceChildren("Recipient PIN ", el("code", {}, pin));

  const live = !FINAL.has(d.status);
  refs.cancel.hidden = !CANCELABLE.has(d.status);
  refs.handoff.hidden = refs.pinInput.hidden = d.phase !== "awaiting_handoff";
  if (pin && !refs.pinInput.value) refs.pinInput.value = pin;
  refs.failKind.hidden = refs.inject.hidden = !(live && state.me?.test_mode);
  if (d.tracking_url && refs.customer.getAttribute("href") !== d.tracking_url) refs.customer.href = d.tracking_url;

  if (d.phase === "awaiting_handoff" && refs.lastPhase !== d.phase) {
    // The drone only hovers for a couple of minutes (much less in a sped-up simulation).
    toast(`${d.drone} is hovering at the drop-off. Enter the recipient PIN now.`);
    refs.root.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  refs.lastPhase = d.phase;

  if (refs.lastStatus !== d.status) {
    refs.lastStatus = d.status;
    if (!refs.timeline.hidden) loadTimeline(d.id);
  }
}

function renderDeliveries(deliveries) {
  const list = $("#deliveries");
  const seen = new Set();
  deliveries.forEach((d, i) => {
    seen.add(d.id);
    let refs = state.cards.get(d.id);
    if (!refs) {
      refs = createCard(d);
      state.cards.set(d.id, refs);
    }
    if (list.children[i] !== refs.root) list.insertBefore(refs.root, list.children[i] || null);
    updateCard(refs, d);
  });
  for (const [id, refs] of state.cards) {
    if (!seen.has(id)) {
      refs.root.remove();
      state.cards.delete(id);
    }
  }
  $("#no-deliveries").hidden = deliveries.length > 0;
  $("#delivery-count").textContent = deliveries.length ? `(${deliveries.length})` : "";
}

function select(id) {
  state.selected = state.selected === id ? null : id;
  const t = state.tracks.get(id);
  if (state.selected && t) {
    const pts = [...t.flown.getLatLngs(), ...t.ahead.getLatLngs(), t.pickup.getLatLng(), t.dropoff.getLatLng()];
    map.fitBounds(L.latLngBounds(pts), { padding: [60, 60], maxZoom: 15 });
  }
  refreshLive();
}

async function act(id, action, kind) {
  try {
    if (action === "cancel") await api(`/v1/deliveries/${id}/cancel`, { method: "POST", body: {} });
    if (action === "inject") {
      await api(`/v1/test/deliveries/${id}/failures`, { method: "POST", body: { kind } });
      toast(`${label(kind)} will strike this flight`);
    }
    refreshLive();
  } catch (err) {
    toast(err.message, true);
  }
}

async function handoff(id, pin) {
  pin = (pin || "").trim();
  if (!pin) return toast("Type the recipient's PIN first", true);
  try {
    await api(`/v1/deliveries/${id}/handoff`, { method: "POST", body: { pin } });
    toast("PIN accepted. Package released.");
    refreshLive();
  } catch (err) {
    toast(err.message, true);
  }
}

async function loadTimeline(id) {
  const refs = state.cards.get(id);
  if (!refs) return;
  try {
    const { data } = await api(`/v1/deliveries/${id}/events`);
    refs.timeline.replaceChildren(
      ...data.map((e) => {
        const what = e.type === "delivery.custody" ? `custody: ${label(e.data.action)}` : label(e.type.replace("delivery.", ""));
        const why = e.data.reason ? ` (${label(e.data.reason)})` : e.data.vehicle ? ` (${e.data.vehicle})` : "";
        return el("li", {}, el("time", {}, fmtTime(e.created_at)), el("span", {}, what + why));
      }),
    );
  } catch (err) {
    toast(err.message, true);
  }
}

function toggleTimeline(id) {
  const refs = state.cards.get(id);
  refs.timeline.hidden = !refs.timeline.hidden;
  if (!refs.timeline.hidden) loadTimeline(id);
}

/* ---------- fleet list ------------------------------------------------------------------- */

function renderFleet(drones) {
  $("#fleet").replaceChildren(
    ...drones.map((d) => {
      const level = d.battery_pct < 25 ? "low" : d.battery_pct < 50 ? "mid" : "";
      return el("li", {},
        el("span", { class: "name" }, el("i", { class: `dot st-${d.status}` }), d.call_sign),
        el("div", { class: `battery ${level}`, title: `${d.battery_pct}% battery` },
          el("div", { style: `width:${Math.max(0, Math.min(100, d.battery_pct))}%` })),
        el("span", { class: "state" }, `${label(d.status)} · ${Math.round(d.battery_pct)}%`));
    }),
  );
}

/* ---------- polling and connection ------------------------------------------------------- */

async function refreshLive() {
  if (!state.key || state.liveBusy) return;
  state.liveBusy = true;
  try {
    const live = await api("/v1/map/live");
    drawDrones(live.drones);
    drawTracks(live.deliveries);
    renderDeliveries(live.deliveries);
    renderFleet(live.drones);
  } catch (err) {
    if (err.status === 401) disconnect("API key rejected");
  } finally {
    state.liveBusy = false;
  }
}

async function refreshStatic() {
  if (!state.key) return;
  try {
    drawStatic(await api("/v1/map/static"));
  } catch {
    /* next tick will retry */
  }
}

function setConn(text, cls) {
  const c = $("#conn");
  c.textContent = text;
  c.className = `pill ${cls}`;
}

function disconnect(reason) {
  state.key = null;
  state.me = null;
  setConn(reason || "Not connected", reason ? "pill-err" : "pill-off");
}

async function connect(key) {
  state.key = key;
  try {
    state.me = await api("/v1/me");
  } catch (err) {
    disconnect(err.status === 401 ? "Key rejected" : "API unreachable");
    return;
  }
  storage("set", key);
  setConn(`${state.me.name}${state.me.test_mode ? " · test mode" : " · LIVE"}`, "pill-on");
  await refreshStatic();
  refreshLive();
}

$("#key-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const key = $("#api-key").value.trim();
  if (key) connect(key);
});

setInterval(refreshLive, LIVE_EVERY_MS);
setInterval(refreshStatic, STATIC_EVERY_MS);
drawFormPoints();

const saved = storage("get");
if (saved) {
  $("#api-key").value = saved;
  connect(saved);
}
