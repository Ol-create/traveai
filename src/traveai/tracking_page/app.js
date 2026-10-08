/* Recipient tracking page. The token in the URL (/t/<token>) is the only credential. */
"use strict";

const POLL_MS = 3000;
const FINAL = new Set(["delivered", "canceled", "failed"]);
const STEP = { scheduled: 0, assigned: 0, picking_up: 1, airborne: 2, arriving: 3, delivered: 4 };

const token = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
const api = `/v1/public/tracking/${encodeURIComponent(token)}`;
const $ = (id) => document.getElementById(id);

let data = null;
let fetchedAt = 0;
let pollTimer = null;
let fitted = false;

/* ---------- map ------------------------------------------------------------------------ */

const map = L.map("map", { zoomControl: false, attributionControl: true });
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  attribution: "&copy; OpenStreetMap",
}).addTo(map);
const icon = (cls, text) =>
  L.divIcon({ className: `mk ${cls}`, html: `<div>${text}</div>`, iconSize: [30, 30], iconAnchor: [15, 15] });
const home = L.marker([0, 0], { icon: icon("mk-home", "⌂"), keyboard: false });
const store = L.marker([0, 0], { icon: icon("mk-store", "◼"), keyboard: false });
const drone = L.marker([0, 0], { icon: icon("mk-drone", "✈"), zIndexOffset: 500, keyboard: false });
const path = L.polyline([], { weight: 4, dashArray: "8 8", color: "#2563eb" });

function drawMap(d) {
  home.setLatLng([d.dropoff.lat, d.dropoff.lng]).addTo(map).bindTooltip("You");
  store.setLatLng([d.pickup.lat, d.pickup.lng]).addTo(map).bindTooltip(d.merchant_name);
  path.setLatLngs(FINAL.has(d.status) ? [] : d.route).addTo(map);
  if (d.drone) drone.setLatLng([d.drone.lat, d.drone.lng]).addTo(map);
  else drone.remove();

  const pts = [[d.dropoff.lat, d.dropoff.lng], [d.pickup.lat, d.pickup.lng]];
  if (!fitted) {
    map.fitBounds(pts, { padding: [40, 40], maxZoom: 15 });
    fitted = true;
  } else if (d.drone && !map.getBounds().pad(-0.1).contains(drone.getLatLng())) {
    map.panTo(drone.getLatLng()); // keep the drone in view without fighting the user's zoom
  }
}

/* ---------- text ----------------------------------------------------------------------- */

function fmtEta(seconds) {
  if (seconds == null) return "–";
  if (seconds < 60) return "under 1 min";
  const m = Math.round(seconds / 60);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
}

const time = (iso) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

function headline(d) {
  const shop = d.merchant_name;
  switch (d.status) {
    case "scheduled":
    case "assigned":
      return ["Order confirmed", `A drone will pick up your order from ${shop} shortly.`];
    case "picking_up":
      return ["Picking up your order", `The drone is collecting it from ${shop}.`];
    case "airborne":
      return ["On its way", "Your order is in the air."];
    case "arriving":
      return d.awaiting_pin
        ? ["Your drone is here", "It's hovering at the drop-off spot."]
        : ["Almost there", "Head to the drop-off spot: the drone is about a minute away."];
    case "delivered":
      return ["Delivered", `Dropped off at ${time(d.delivered_at)}. Enjoy!`];
    case "aborted":
    case "returned_to_base":
      return ["Delivery delayed", ""];
    case "canceled":
      return ["Delivery canceled", ""];
    case "failed":
      return ["Delivery not completed", ""];
    default:
      return ["Tracking your delivery", ""];
  }
}

/* ---------- render --------------------------------------------------------------------- */

function render(d) {
  $("from").textContent = `${d.merchant_name} · ${d.category === "medical" ? "Pharmacy order" : "Order"}`;
  const [h, sub] = headline(d);
  $("headline").textContent = h;
  $("subline").textContent = sub;
  document.title = `${h} · ${d.merchant_name}`;

  // Progress steps
  const steps = [...$("steps").children];
  const at = STEP[d.status];
  steps.forEach((li, i) => {
    li.classList.toggle("done", at != null && i < at);
    li.classList.toggle("current", at != null && i === at);
    li.removeAttribute("aria-current");
    if (i === at) li.setAttribute("aria-current", "step");
  });
  $("steps").classList.toggle("all-done", d.status === "delivered");
  $("steps").hidden = at == null;

  // ETA
  const showEta = d.eta_seconds != null && !FINAL.has(d.status) && !d.awaiting_pin && at != null;
  $("eta-box").hidden = !showEta;

  // PIN
  const pinCard = $("pin-card");
  const wasHidden = pinCard.hidden;
  pinCard.hidden = !d.awaiting_pin;
  if (d.awaiting_pin && wasHidden) {
    $("pin-msg").textContent = "";
    $("pin").focus({ preventScroll: true });
    pinCard.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  // Problems
  const problem = $("problem");
  problem.hidden = !d.problem;
  problem.textContent = d.problem || "";
  problem.classList.toggle("final", FINAL.has(d.status));

  // Proof
  const proof = $("proof");
  proof.hidden = !d.proof_photo_url;
  if (d.proof_photo_url) {
    $("proof-time").textContent = `Dropped off at ${time(d.delivered_at)}`;
    const img = $("proof-img");
    if (img.getAttribute("src") !== d.proof_photo_url) img.src = d.proof_photo_url;
  }

  drawMap(d);
  tick();
}

function tick() {
  if (!data) return;
  const elapsed = (Date.now() - fetchedAt) / 1000;
  if (data.eta_seconds != null) $("eta").textContent = fmtEta(Math.max(0, data.eta_seconds - elapsed));
  $("updated").textContent = FINAL.has(data.status) ? "Final status" : `Live · updated ${Math.round(elapsed)} s ago`;
}

function showError(title, text) {
  $("headline").textContent = title;
  $("subline").textContent = text;
  ["eta-box", "pin-card", "proof", "steps"].forEach((id) => ($(id).hidden = true));
  $("map").hidden = true;
  $("updated").textContent = "";
}

/* ---------- data ----------------------------------------------------------------------- */

async function load() {
  clearTimeout(pollTimer);
  try {
    const res = await fetch(api, { cache: "no-store" });
    if (res.status === 404) return showError("Link not found", "Check that you opened the full link from your message.");
    if (res.status === 410) return showError("This link has expired", "Tracking links stop working a day after delivery.");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
    fetchedAt = Date.now();
    render(data);
    if (FINAL.has(data.status)) return; // nothing more will change
  } catch {
    $("updated").textContent = "Connection lost, retrying…";
  }
  pollTimer = setTimeout(load, POLL_MS);
}

$("pin-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("pin");
  const msg = $("pin-msg");
  const button = e.submitter || e.target.querySelector("button");
  button.disabled = true;
  msg.className = "pin-msg";
  msg.textContent = "Checking…";
  try {
    const res = await fetch(`${api}/handoff`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pin: input.value.trim() }),
    });
    const body = await res.json().catch(() => ({}));
    if (res.ok) {
      msg.textContent = "PIN accepted. Your package is being released.";
      data = body;
      fetchedAt = Date.now();
      render(data);
      return;
    }
    msg.className = "pin-msg error";
    if (res.status === 422) msg.textContent = "Enter the digits of your PIN.";
    else msg.textContent = body.detail?.message || "That didn't work. Try again.";
    input.select();
  } catch {
    msg.className = "pin-msg error";
    msg.textContent = "No connection. Try again.";
  } finally {
    button.disabled = false;
  }
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && data && !FINAL.has(data.status)) load(); // refresh on return
});

setInterval(tick, 1000);
load();
