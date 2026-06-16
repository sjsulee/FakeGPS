// FakeGPS Studio frontend: Leaflet map + control-plane calls.
"use strict";

const map = L.map("map").setView([37.3349, -122.009], 13);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 19,
  attribution: "© OpenStreetMap",
}).addTo(map);

// Center the map on the browser's real location on load (localhost is a secure
// context, so geolocation is allowed). Just moves the view — does not push any
// fake location to the device.
function locateMe() {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      map.setView([pos.coords.latitude, pos.coords.longitude], 16);
    },
    (err) => setStatus(`無法取得目前位置：${err.message}`, true),
    { enableHighAccuracy: true, timeout: 8000 }
  );
}

let mode = "teleport";
let pin = null;             // current/teleport marker
let pendingTeleport = null; // {lat, lon} chosen but not yet sent
let routePts = [];          // [{lat, lon}]
let routeMarkers = [];
let routeLine = null;

const $ = (id) => document.getElementById(id);
const api = async (path, body, method) => {
  const m = method || (body ? "POST" : "GET");
  const res = await fetch(path, {
    method: m,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
};
const setStatus = (msg, isErr) => {
  const el = $("status");
  el.textContent = msg || "";
  el.style.color = isErr ? "#ff9aa6" : "#8a909a";
};

// ---- device marker ----
function setMarker(lat, lon, label) {
  if (!pin) {
    pin = L.marker([lat, lon], { draggable: false }).addTo(map);
  } else {
    pin.setLatLng([lat, lon]);
  }
  if (label) pin.bindPopup(label);
}

// ---- live updates over WS ----
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "position") {
      setMarker(m.lat, m.lon);
    } else if (m.type === "cleared" || m.type === "route_stopped") {
      setStatus("Stopped.");
    } else if (m.type === "route_finished") {
      setStatus("Route finished.");
      $("route-stop").classList.add("hidden");
      $("do-route").classList.remove("hidden");
    } else if (m.type === "error") {
      setStatus(m.detail, true);
    }
  };
  ws.onclose = () => setTimeout(connectWS, 1500);
}
connectWS();

// ---- devices ----
async function loadDevices() {
  const sel = $("device");
  sel.innerHTML = "";
  try {
    const { demo, devices } = await api("/api/devices");
    if (!devices.length) {
      sel.innerHTML = "<option value=''>No devices found</option>";
      setStatus(demo ? "Demo mode, no devices?" : "Connect a device and rescan.");
      return;
    }
    for (const d of devices) {
      const o = document.createElement("option");
      o.value = d.udid;
      o.textContent = `${d.name} · ${d.platform} ${d.os_version}`;
      sel.appendChild(o);
    }
    setStatus(`${devices.length} device(s) found${demo ? " (demo)" : ""}.`);
    // Auto-connect when there's exactly one device, so the user can act
    // immediately without hunting for the Connect button.
    if (devices.length === 1) connectDevice();
  } catch (e) {
    setStatus(e.message, true);
  }
}

async function connectDevice() {
  const udid = $("device").value;
  if (!udid) return;
  try {
    setStatus("連線中… (iOS 首次需掛載開發者映像，約數十秒)");
    const { selected } = await api("/api/select", { udid });
    $("devinfo").textContent = `已連線：${selected.name} (${selected.platform} ${selected.os_version})`;
    setStatus("已連線。點地圖選位置後按 Teleport。");
  } catch (e) {
    setStatus(e.message, true);
  }
}

$("refresh").onclick = loadDevices;
$("connect").onclick = connectDevice;

// ---- mode tabs ----
document.querySelectorAll(".tab").forEach((btn) => {
  btn.onclick = () => {
    mode = btn.dataset.mode;
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    for (const m of ["teleport", "joystick", "route"]) {
      $(`panel-${m}`).classList.toggle("hidden", m !== mode);
    }
  };
});

// ---- map clicks ----
map.on("click", async (e) => {
  const { lat, lng } = e.latlng;
  if (mode === "teleport") {
    pendingTeleport = { lat, lon: lng };
    $("t-lat").value = lat.toFixed(6);
    $("t-lon").value = lng.toFixed(6);
    setMarker(lat, lng, "Pending teleport");
  } else if (mode === "joystick") {
    // Clicking in joystick mode sets the start point immediately, so the
    // direction keys/buttons have somewhere to walk from.
    try {
      await api("/api/teleport", { lat, lon: lng });
      setMarker(lat, lng, "Joystick start");
      setStatus("起點已設定，可用方向鍵 / WASD 或方向鈕走動。");
    } catch (err) {
      setStatus(err.message, true);
    }
  } else if (mode === "route") {
    routePts.push({ lat, lon: lng });
    const m = L.circleMarker([lat, lng], { radius: 5, color: "#2f6df6" }).addTo(map);
    routeMarkers.push(m);
    redrawRoute();
  }
});

function redrawRoute() {
  if (routeLine) map.removeLayer(routeLine);
  if (routePts.length >= 2) {
    routeLine = L.polyline(routePts.map((p) => [p.lat, p.lon]), { color: "#2f6df6" }).addTo(map);
  }
}

// ---- speed ----
const speed = () => parseFloat($("speed").value);
$("speed-preset").onchange = () => {
  $("speed").value = $("speed-preset").value;
  $("speed-val").textContent = $("speed").value;
};
$("speed").oninput = () => ($("speed-val").textContent = $("speed").value);

// ---- teleport ----
$("do-teleport").onclick = async () => {
  const lat = parseFloat($("t-lat").value);
  const lon = parseFloat($("t-lon").value);
  if (Number.isNaN(lat) || Number.isNaN(lon)) return setStatus("Pick a point first.", true);
  try {
    await api("/api/teleport", { lat, lon });
    setStatus(`Teleported to ${lat.toFixed(5)}, ${lon.toFixed(5)}.`);
  } catch (e) {
    setStatus(e.message, true);
  }
};

// ---- joystick ----
let joyBusy = false; // drop overlapping requests so held keys don't pile up

async function step(heading) {
  if (joyBusy) return;
  joyBusy = true;
  try {
    const r = await api("/api/joystick", { heading, speed: speed() });
    if (r.current) setMarker(r.current[0], r.current[1]);
  } catch (e) {
    // Most common cause: no start point yet. Give an actionable hint.
    if (/start location/i.test(e.message) || /no device/i.test(e.message)) {
      setStatus("請先連線裝置並在地圖上點一下設定起點。", true);
    } else {
      setStatus(e.message, true);
    }
  } finally {
    joyBusy = false;
  }
}

document.querySelectorAll(".dpad button[data-h]").forEach((b) => {
  b.onclick = () => step(parseFloat(b.dataset.h));
});
$("stopwalk").onclick = () => api("/api/stop", null, "POST").catch(() => {});

// Keyboard joystick: arrow keys + WASD. Holding a key keeps walking.
const KEY_HEADING = {
  ArrowUp: 0, KeyW: 0,
  ArrowRight: 90, KeyD: 90,
  ArrowDown: 180, KeyS: 180,
  ArrowLeft: 270, KeyA: 270,
};
const heldKeys = new Set();
let joyTimer = null;

function joyTick() {
  if (heldKeys.size === 0) return;
  // Average the held directions so diagonals (e.g. W+D) work.
  let x = 0, y = 0;
  for (const code of heldKeys) {
    const h = (KEY_HEADING[code] * Math.PI) / 180;
    x += Math.sin(h);
    y += Math.cos(h);
  }
  if (x === 0 && y === 0) return;
  const heading = ((Math.atan2(x, y) * 180) / Math.PI + 360) % 360;
  step(heading);
}

function typingInField(e) {
  const t = e.target;
  return t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA");
}

document.addEventListener("keydown", (e) => {
  // Only drive the joystick while its panel is the active mode.
  if (mode !== "joystick" || typingInField(e)) return;
  if (!(e.code in KEY_HEADING)) return;
  e.preventDefault();
  if (!heldKeys.has(e.code)) {
    heldKeys.add(e.code);
    joyTick(); // immediate response on first press
    document.querySelector(`.dpad button[data-h="${KEY_HEADING[e.code]}"]`)?.classList.add("pressed");
  }
  if (joyTimer === null) {
    joyTimer = setInterval(joyTick, 500); // matches backend tick interval
  }
});

function releaseKey(code) {
  heldKeys.delete(code);
  document.querySelector(`.dpad button[data-h="${KEY_HEADING[code]}"]`)?.classList.remove("pressed");
  if (heldKeys.size === 0 && joyTimer !== null) {
    clearInterval(joyTimer);
    joyTimer = null;
  }
}

document.addEventListener("keyup", (e) => {
  if (e.code in KEY_HEADING) releaseKey(e.code);
});
// Releasing focus (e.g. alt-tab) shouldn't leave a key "stuck".
window.addEventListener("blur", () => {
  for (const code of [...heldKeys]) releaseKey(code);
});

// ---- route ----
$("route-clear").onclick = () => {
  routePts = [];
  routeMarkers.forEach((m) => map.removeLayer(m));
  routeMarkers = [];
  if (routeLine) { map.removeLayer(routeLine); routeLine = null; }
};
// Show the jitter field only when realistic movement is on.
$("route-realistic").onchange = () => {
  $("jitter-row").classList.toggle("hidden", !$("route-realistic").checked);
};
$("do-route").onclick = async () => {
  if (routePts.length < 2) return setStatus("Add at least 2 waypoints.", true);
  const realistic = $("route-realistic").checked;
  try {
    await api("/api/route", {
      points: routePts,
      speed: speed(),
      loop: $("route-loop").checked,
      realistic,
      jitter_m: realistic ? parseFloat($("route-jitter").value) || 0 : 0,
    });
    setStatus(realistic ? "Route playing… (擬真移動)" : "Route playing…");
    $("do-route").classList.add("hidden");
    $("route-stop").classList.remove("hidden");
  } catch (e) {
    setStatus(e.message, true);
  }
};
$("route-stop").onclick = async () => {
  await api("/api/stop", null, "POST").catch(() => {});
  $("route-stop").classList.add("hidden");
  $("do-route").classList.remove("hidden");
};
$("gpx").onchange = async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const gpx = await file.text();
    const { points } = await api("/api/gpx", { gpx });
    $("route-clear").click();
    routePts = points.map(([lat, lon]) => ({ lat, lon }));
    routePts.forEach((p) => {
      routeMarkers.push(L.circleMarker([p.lat, p.lon], { radius: 4, color: "#2f6df6" }).addTo(map));
    });
    redrawRoute();
    if (routePts.length) map.fitBounds(routePts.map((p) => [p.lat, p.lon]));
    setStatus(`Loaded ${routePts.length} GPX points.`);
  } catch (err) {
    setStatus(err.message, true);
  }
};

// ---- clear ----
$("clear").onclick = async () => {
  try {
    await api("/api/clear", null, "POST");
    setStatus("Cleared. Real GPS restored.");
  } catch (e) {
    setStatus(e.message, true);
  }
};

// Add a "locate me" control to the map so the user can recenter any time.
const LocateControl = L.Control.extend({
  options: { position: "topleft" },
  onAdd() {
    const btn = L.DomUtil.create("a", "leaflet-bar leaflet-control");
    btn.href = "#";
    btn.title = "定位到我目前位置";
    btn.textContent = "◎";
    btn.style.cssText = "font-size:18px;text-align:center;line-height:30px;width:30px;height:30px;";
    L.DomEvent.on(btn, "click", (e) => {
      L.DomEvent.stop(e);
      locateMe();
    });
    return btn;
  },
});
map.addControl(new LocateControl());

locateMe();
loadDevices();
