const defaultBrokers = [
  { id: "broker-a", sensorId: "sensor-a-1", name: "Area 1", label: "Broker A", url: "http://localhost:8001", x: 18, y: 24 },
  { id: "broker-b", sensorId: "sensor-b-1", name: "Area 2", label: "Broker B", url: "http://localhost:8002", x: 48, y: 18 },
  { id: "broker-c", sensorId: "sensor-c-1", name: "Area 3", label: "Broker C", url: "http://localhost:8003", x: 76, y: 32 },
  { id: "broker-d", sensorId: "sensor-d-1", name: "Area 4", label: "Broker D", url: "http://localhost:8004", x: 30, y: 72 },
  { id: "broker-e", sensorId: "sensor-e-1", name: "Area 5", label: "Broker E", url: "http://localhost:8005", x: 68, y: 76 },
];

const defaultDrones = [
  { id: "drone-base-1", name: "Base 1", droneName: "Drone 1", url: "http://localhost:9001", x: 24, y: 48 },
  { id: "drone-base-2", name: "Base 2", droneName: "Drone 2", url: "http://localhost:9002", x: 52, y: 48 },
  { id: "drone-base-3", name: "Base 3", droneName: "Drone 3", url: "http://localhost:9003", x: 80, y: 58 },
];

const runtimeConfig = window.DISTRIBUTED_CONFIG || {};
const brokers = Array.isArray(runtimeConfig.brokers) ? runtimeConfig.brokers : defaultBrokers;
const drones = Array.isArray(runtimeConfig.drones) ? runtimeConfig.drones : defaultDrones;
const AREA_SIZE = Number(runtimeConfig.areaSize || 20);
const MAP_LAT_MIN = 24;
const MAP_LAT_RANGE = 4.8;
const MAP_LON_MIN = 52;
const MAP_LON_RANGE = 6.8;

const $ = (selector) => document.querySelector(selector);

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

function statusBadge(status) {
  const value = status || "offline";
  return `<span class="badge ${value}">${value}</span>`;
}

function setDefinitionList(dl, items) {
  dl.innerHTML = items
    .map(([label, value]) => `<dt>${label}</dt><dd>${value}</dd>`)
    .join("");
}

function populateBrokerSelect() {
  $("#brokerSelect").innerHTML = brokers
    .map((broker) => `<option value="${broker.url}">${broker.name} - ${broker.label}</option>`)
    .join("");
}

function updateBrokerSelect(brokerResults) {
  const selected = $("#brokerSelect").value;
  $("#brokerSelect").innerHTML = brokers
    .map((broker, index) => {
      const result = brokerResults[index];
      const active = result?.ok && result.data.broker_alive && result.data.sensor_alive;
      return `<option value="${broker.url}" ${active ? "" : "disabled"}>${broker.name} - ${broker.label}${active ? "" : " (inativa)"}</option>`;
    })
    .join("");
  if (brokers.some((broker) => broker.url === selected)) {
    $("#brokerSelect").value = selected;
  }
}

function distance(a, b) {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function brokerPoint(broker, result = null) {
  const position = result?.ok ? result.data.position : null;
  return {
    x: Number(position?.x ?? broker.x),
    y: Number(position?.y ?? broker.y),
  };
}

function brokerArea(broker, result = null) {
  const center = brokerPoint(broker, result);
  const size = Number(result?.ok ? result.data.area_size || AREA_SIZE : broker.areaSize || AREA_SIZE);
  const half = size / 2;
  const left = clamp(center.x - half, 0, 100);
  const top = clamp(center.y - half, 0, 100);
  return {
    ...center,
    left,
    top,
    right: clamp(center.x + half, 0, 100),
    bottom: clamp(center.y + half, 0, 100),
    width: clamp(center.x + half, 0, 100) - left,
    height: clamp(center.y + half, 0, 100) - top,
  };
}

function mapPointToGeo(point) {
  return {
    lat: Number((MAP_LAT_MIN + ((100 - point.y) / 100) * MAP_LAT_RANGE).toFixed(5)),
    lon: Number((MAP_LON_MIN + (point.x / 100) * MAP_LON_RANGE).toFixed(5)),
  };
}

function geoLabel(lat, lon) {
  return `${Number(lat).toFixed(3)}, ${Number(lon).toFixed(3)}`;
}

function randomPointInBrokerArea(broker, result = null) {
  const area = brokerArea(broker, result);
  return {
    x: area.left + Math.random() * area.width,
    y: area.top + Math.random() * area.height,
  };
}

function missionToMapPoint(mission) {
  const area = mission?.area || {};
  const lat = Number(area.lat);
  const lon = Number(area.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
    return null;
  }

  return {
    x: clamp(((lon - MAP_LON_MIN) / MAP_LON_RANGE) * 100, 5, 95),
    y: clamp(100 - ((lat - MAP_LAT_MIN) / MAP_LAT_RANGE) * 100, 5, 95),
  };
}

function lerpPoint(from, to, progress) {
  return {
    x: from.x + (to.x - from.x) * progress,
    y: from.y + (to.y - from.y) * progress,
  };
}

function nearestLiveBase(base, droneResults) {
  return drones
    .filter((candidate) => candidate.id !== base.id)
    .filter((candidate) => {
      const index = drones.findIndex((drone) => drone.id === candidate.id);
      const result = droneResults[index];
      return result?.ok && result.data.base_alive;
    })
    .sort((left, right) => distance(base, left) - distance(base, right))[0];
}

async function setBrokerPart(broker, target, alive) {
  await postJson(`${broker.url}/control`, { target, action: alive ? "restore" : "destroy" });
}

async function setDronePart(drone, target, alive, droneResults = []) {
  const payload = { target, action: alive ? "restore" : "destroy" };
  if (target === "base" && !alive) {
    const fallback = nearestLiveBase(drone, droneResults);
    payload.command_base = fallback ? fallback.id : "";
  }
  await postJson(`${drone.url}/control`, payload);
}

async function syncDroneCommandBases() {
  const droneResults = await fetchDroneResults();
  const liveBaseIds = new Set(
    droneResults
      .filter((result) => result.ok && result.data.base_alive)
      .map((result) => result.data.base_id),
  );

  await Promise.all(
    droneResults.map((result, index) => {
      if (!result.ok || !result.data.drone_alive || result.data.base_alive) {
        return Promise.resolve();
      }
      const currentCommandBase = result.data.command_base;
      if (liveBaseIds.has(currentCommandBase)) {
        return Promise.resolve();
      }
      const fallback = nearestLiveBase(drones[index], droneResults);
      return postJson(`${drones[index].url}/control`, {
        target: "base",
        action: "destroy",
        command_base: fallback ? fallback.id : "",
      }).catch(() => undefined);
    }),
  );
}

function renderBrokerCards(results) {
  const container = $("#brokers");
  const template = $("#brokerTemplate");
  container.innerHTML = "";

  results.forEach((result, index) => {
    const node = template.content.cloneNode(true);
    const panel = node.querySelector(".panel");
    const title = node.querySelector("h3");
    const badge = node.querySelector(".badge");
    const dl = node.querySelector("dl");
    const broker = brokers[index];

    title.textContent = `${broker.name} - ${broker.label}`;
    if (result.ok) {
      const requests = Object.values(result.data.requests || {});
      const status = result.data.broker_alive ? "online" : "destroyed";
      badge.textContent = status;
      badge.className = `badge ${status}`;
      if (!result.data.broker_alive) {
        panel.classList.add("offline");
      }
      setDefinitionList(dl, [
        ["Area", result.data.sector_id],
        ["Sensor", result.data.sensor_alive ? "online" : "destruido"],
        ["Fila", result.data.queue_size],
        ["Requisicoes", requests.length],
      ]);
    } else {
      panel.classList.add("offline");
      badge.textContent = "offline";
      badge.className = "badge offline";
      setDefinitionList(dl, [["Erro", "sem resposta"]]);
    }
    container.appendChild(node);
  });
}

function renderDroneCards(results) {
  const container = $("#drones");
  const template = $("#droneTemplate");
  container.innerHTML = "";

  results.forEach((result, index) => {
    const node = template.content.cloneNode(true);
    const title = node.querySelector("h3");
    const badge = node.querySelector(".badge");
    const dl = node.querySelector("dl");
    const drone = drones[index];

    title.textContent = `${drone.name} - ${drone.droneName}`;
    if (result.ok) {
      const status = !result.data.drone_alive ? "destroyed" : result.data.status;
      badge.textContent = status;
      badge.className = `badge ${status}`;
      setDefinitionList(dl, [
        ["Base", result.data.base_alive ? "online" : "destruida"],
        ["Comando", result.data.command_base || "-"],
        ["Atual", result.data.current_request || "-"],
        ["Concluidas", result.data.completed],
      ]);
    } else {
      badge.textContent = "offline";
      badge.className = "badge offline";
      setDefinitionList(dl, [["Erro", "sem resposta"]]);
    }
    container.appendChild(node);
  });
}

function collectRequests(brokerResults) {
  const byId = new Map();
  brokerResults
    .filter((result) => result.ok)
    .forEach((result) => {
      Object.values(result.data.requests || {}).forEach((request) => {
        const existing = byId.get(request.request_id);
        if (!existing || request.origin_broker === result.data.broker_id) {
          byId.set(request.request_id, request);
        }
      });
    });
  return Array.from(byId.values()).sort((a, b) => (b.created_at_ms || 0) - (a.created_at_ms || 0));
}

function renderRequests(requests) {
  const table = $("#requestsTable");
  if (requests.length === 0) {
    table.innerHTML = `<tr><td colspan="7">Nenhuma ocorrencia ainda.</td></tr>`;
    return;
  }
  table.innerHTML = requests
    .slice(0, 18)
    .map((request) => {
      const lat = request.area?.lat;
      const lon = request.area?.lon;
      const coords = Number.isFinite(Number(lat)) && Number.isFinite(Number(lon)) ? geoLabel(lat, lon) : "-";
      const distance = Number.isFinite(Number(request.assigned_distance)) ? Number(request.assigned_distance).toFixed(1) : "-";
      return `
        <tr>
          <td><code>${request.request_id.slice(0, 8)}</code></td>
          <td>${request.origin_broker}</td>
          <td>${request.event_type}</td>
          <td>${request.criticality}</td>
          <td>${coords}</td>
          <td>${statusBadge(request.status)}</td>
          <td>${request.assigned_drone ? `${request.assigned_drone} (${distance})` : "-"}</td>
        </tr>
      `;
    })
    .join("");
}

function renderMetrics(brokerResults, droneResults, requests) {
  const online = brokerResults.filter((result) => result.ok && result.data.broker_alive).length;
  const liveDrones = droneResults.filter((result) => result.ok && result.data.drone_alive).length;
  const idle = droneResults.filter((result) => result.ok && result.data.drone_alive && result.data.status === "idle").length;
  const queued = requests.filter((request) => request.status === "queued").length;
  const completed = droneResults
    .filter((result) => result.ok)
    .reduce((sum, result) => sum + Number(result.data.completed || 0), 0);

  $("#onlineBrokers").textContent = `${online}/${brokers.length}`;
  $("#idleDrones").textContent = `${idle}/${liveDrones || drones.length}`;
  $("#queuedRequests").textContent = queued;
  $("#completedMissions").textContent = completed;
}

function pointClass(...parts) {
  return parts.filter(Boolean).join(" ");
}

function renderMap(brokerResults, droneResults) {
  const overlay = $("#mapLinks");
  const nodes = $("#mapNodes");
  nodes.innerHTML = "";
  overlay.innerHTML = "";
  renderGeoGrid(nodes);

  brokers.forEach((broker, index) => {
    const result = brokerResults[index];
    const alive = result?.ok && result.data.broker_alive;
    const sensorAlive = result?.ok && result.data.sensor_alive;
    const active = alive && sensorAlive;
    const area = brokerArea(broker, result);
    const point = brokerPoint(broker, result);
    nodes.insertAdjacentHTML(
      "beforeend",
      `<div class="${pointClass("map-area", active ? "active" : "inactive")}" style="left:${area.left}%; top:${area.top}%; width:${area.width}%; height:${area.height}%;">
        <span>${broker.name}</span>
      </div>`,
    );
    nodes.insertAdjacentHTML(
      "beforeend",
      `<button class="${pointClass("map-node", "broker-node", active ? "online" : "destroyed")}" style="left:${point.x}%; top:${point.y}%;" data-node="${broker.id}">
        <span>${broker.name}</span><strong>${broker.label}</strong><em>${sensorAlive ? "sensor online" : "sensor off"}</em>
      </button>`,
    );
  });

  drones.forEach((drone, index) => {
    const result = droneResults[index];
    const status = result?.ok ? result.data : null;
    const baseAlive = Boolean(status?.base_alive);
    const droneAlive = Boolean(status?.drone_alive);
    const commandBase = status?.command_base || drone.id;
    const commandTarget = drones.find((item) => item.id === commandBase) || drone;
    const destination = status?.status === "busy" ? missionToMapPoint(status.current_mission) : null;
    const progress = Number(status?.mission_progress || 0);
    const dronePoint = destination ? lerpPoint(commandTarget, destination, progress) : { x: drone.x, y: drone.y };

    if (droneAlive && commandTarget.id !== drone.id) {
      overlay.insertAdjacentHTML("beforeend", mapLine(drone, commandTarget));
    }
    if (droneAlive && destination) {
      overlay.insertAdjacentHTML("beforeend", mapLine(commandTarget, destination, "mission-line"));
      const geo = mapPointToGeo(destination);
      nodes.insertAdjacentHTML(
        "beforeend",
        `<div class="map-target" style="left:${destination.x}%; top:${destination.y}%;">
          <span>Chamado ${geoLabel(geo.lat, geo.lon)}</span>
        </div>`,
      );
    }

    nodes.insertAdjacentHTML(
      "beforeend",
      `<button class="${pointClass("map-node", "base-node", baseAlive ? "online" : "destroyed", droneAlive ? "" : "drone-down")}" style="left:${drone.x}%; top:${drone.y}%;" data-node="${drone.id}">
        <span>${drone.name}</span><strong>${drone.droneName}</strong><em>cmd: ${commandBase}</em>
      </button>`,
    );

    nodes.insertAdjacentHTML(
      "beforeend",
      `<div class="${pointClass("drone-marker", droneAlive ? status?.status || "idle" : "destroyed")}" style="left:${dronePoint.x}%; top:${dronePoint.y}%;">
        <span></span>
      </div>`,
    );
  });
}

function renderGeoGrid(container) {
  const steps = 4;
  for (let index = 0; index <= steps; index += 1) {
    const pct = (index / steps) * 100;
    const lon = MAP_LON_MIN + (pct / 100) * MAP_LON_RANGE;
    const lat = MAP_LAT_MIN + ((100 - pct) / 100) * MAP_LAT_RANGE;
    container.insertAdjacentHTML(
      "beforeend",
      `<span class="geo-label lon-label" style="left:${pct}%;">${lon.toFixed(2)} lon</span>
       <span class="geo-label lat-label" style="top:${pct}%;">${lat.toFixed(2)} lat</span>`,
    );
  }
}

function mapLine(from, to, className = "command-line") {
  const x1 = from.x;
  const y1 = from.y;
  const x2 = to.x;
  const y2 = to.y;
  return `<line class="${className}" x1="${x1}%" y1="${y1}%" x2="${x2}%" y2="${y2}%" />`;
}

function controlButton(label, action, disabled = false) {
  return `<button type="button" data-action="${action}" ${disabled ? "disabled" : ""}>${label}</button>`;
}

function renderControls(brokerResults, droneResults) {
  const areaControls = $("#areaControls");
  const baseControls = $("#baseControls");

  areaControls.innerHTML = brokers
    .map((broker, index) => {
      const result = brokerResults[index];
      const brokerAlive = result?.ok && result.data.broker_alive;
      const sensorAlive = result?.ok && result.data.sensor_alive;
      return `
        <article class="control-row" data-kind="broker" data-index="${index}">
          <div><strong>${broker.name}</strong><span>${broker.label} / ${broker.sensorId}</span></div>
          <div class="button-pair">
            ${controlButton(brokerAlive ? "Destruir broker" : "Criar broker", brokerAlive ? "destroy-broker" : "restore-broker")}
            ${controlButton(sensorAlive ? "Destruir sensor" : "Criar sensor", sensorAlive ? "destroy-sensor" : "restore-sensor", !brokerAlive)}
          </div>
        </article>
      `;
    })
    .join("");

  baseControls.innerHTML = drones
    .map((drone, index) => {
      const result = droneResults[index];
      const baseAlive = result?.ok && result.data.base_alive;
      const droneAlive = result?.ok && result.data.drone_alive;
      const commandBase = result?.ok ? result.data.command_base : "-";
      return `
        <article class="control-row" data-kind="drone" data-index="${index}">
          <div><strong>${drone.name}</strong><span>${drone.droneName} comando: ${commandBase}</span></div>
          <div class="button-pair">
            ${controlButton(baseAlive ? "Destruir base" : "Criar base", baseAlive ? "destroy-base" : "restore-base")}
            ${controlButton(droneAlive ? "Destruir drone" : "Criar drone", droneAlive ? "destroy-drone" : "restore-drone")}
          </div>
        </article>
      `;
    })
    .join("");
}

async function fetchBrokerResults() {
  return Promise.all(
    brokers.map((broker) =>
      getJson(`${broker.url}/state`)
        .then((data) => ({ ok: true, data }))
        .catch((error) => ({ ok: false, error })),
    ),
  );
}

async function fetchDroneResults() {
  return Promise.all(
    drones.map((drone) =>
      getJson(`${drone.url}/status`)
        .then((data) => ({ ok: true, data }))
        .catch((error) => ({ ok: false, error })),
    ),
  );
}

async function refresh() {
  $("#refreshStatus").textContent = "Atualizando...";

  const brokerResults = await fetchBrokerResults();
  const droneResults = await fetchDroneResults();
  const requests = collectRequests(brokerResults);

  updateBrokerSelect(brokerResults);
  renderMetrics(brokerResults, droneResults, requests);
  renderMap(brokerResults, droneResults);
  renderControls(brokerResults, droneResults);
  renderBrokerCards(brokerResults);
  renderDroneCards(droneResults);
  renderRequests(requests);
  $("#refreshStatus").textContent = `Atualizado ${new Date().toLocaleTimeString()}`;
}

async function createRequest(event) {
  event.preventDefault();
  const brokerUrl = $("#brokerSelect").value;
  const brokerIndex = brokers.findIndex((broker) => broker.url === brokerUrl);
  const broker = brokers[brokerIndex];

  if (!broker) {
    alert("Selecione uma area ativa.");
    return;
  }

  let brokerResult;
  try {
    const data = await getJson(`${broker.url}/state`);
    brokerResult = { ok: true, data };
  } catch {
    alert("Nao foi possivel criar a ocorrencia: broker sem resposta.");
    return;
  }

  if (!brokerResult.data.broker_alive || !brokerResult.data.sensor_alive) {
    alert("Ocorrencias so podem surgir em areas com broker e sensor ativos.");
    await refresh();
    return;
  }

  const point = randomPointInBrokerArea(broker, brokerResult);
  const geo = mapPointToGeo(point);
  const payload = {
    event_type: $("#eventType").value,
    criticality: Number($("#criticality").value),
    lat: geo.lat,
    lon: geo.lon,
  };

  try {
    await postJson(`${brokerUrl}/request-drone`, payload);
    await refresh();
  } catch {
    alert("Nao foi possivel criar a ocorrencia.");
  }
}

async function handleControlClick(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) {
    return;
  }
  const row = button.closest(".control-row");
  const action = button.dataset.action;
  const index = Number(row.dataset.index);

  button.disabled = true;
  try {
    if (row.dataset.kind === "broker") {
      const broker = brokers[index];
      if (action === "destroy-broker") await setBrokerPart(broker, "broker", false);
      if (action === "restore-broker") await setBrokerPart(broker, "broker", true);
      if (action === "destroy-sensor") await setBrokerPart(broker, "sensor", false);
      if (action === "restore-sensor") await setBrokerPart(broker, "sensor", true);
    } else {
      const drone = drones[index];
      const droneResults = await fetchDroneResults();
      if (action === "destroy-base") await setDronePart(drone, "base", false, droneResults);
      if (action === "restore-base") await setDronePart(drone, "base", true, droneResults);
      if (action === "destroy-drone") await setDronePart(drone, "drone", false, droneResults);
      if (action === "restore-drone") await setDronePart(drone, "drone", true, droneResults);
      await syncDroneCommandBases();
    }
  } finally {
    await refresh();
  }
}

$("#requestForm").addEventListener("submit", createRequest);
$("#refreshButton").addEventListener("click", refresh);
$("#areaControls").addEventListener("click", handleControlClick);
$("#baseControls").addEventListener("click", handleControlClick);
populateBrokerSelect();
refresh();
setInterval(refresh, 3000);
