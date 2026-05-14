const brokers = [
  { name: "Broker A", url: "http://localhost:8001" },
  { name: "Broker B", url: "http://localhost:8002" },
  { name: "Broker C", url: "http://localhost:8003" },
];

const drones = [
  { name: "Drone 1", url: "http://localhost:9001" },
  { name: "Drone 2", url: "http://localhost:9002" },
  { name: "Drone 3", url: "http://localhost:9003" },
];

const $ = (selector) => document.querySelector(selector);

async function getJson(url) {
  const response = await fetch(url);
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

    title.textContent = brokers[index].name;
    if (result.ok) {
      const requests = Object.values(result.data.requests || {});
      badge.textContent = "online";
      badge.className = "badge online";
      setDefinitionList(dl, [
        ["Setor", result.data.sector_id],
        ["Sensores", Object.keys(result.data.sensors || {}).length],
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

    title.textContent = drones[index].name;
    if (result.ok) {
      const status = result.data.status;
      badge.textContent = status;
      badge.className = `badge ${status}`;
      setDefinitionList(dl, [
        ["Atual", result.data.current_request || "-"],
        ["Concluidas", result.data.completed],
        ["Falhas", result.data.failed],
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
    table.innerHTML = `<tr><td colspan="6">Nenhuma ocorrencia ainda.</td></tr>`;
    return;
  }
  table.innerHTML = requests
    .slice(0, 18)
    .map(
      (request) => `
        <tr>
          <td><code>${request.request_id.slice(0, 8)}</code></td>
          <td>${request.origin_broker}</td>
          <td>${request.event_type}</td>
          <td>${request.criticality}</td>
          <td>${statusBadge(request.status)}</td>
          <td>${request.assigned_drone || "-"}</td>
        </tr>
      `,
    )
    .join("");
}

function renderMetrics(brokerResults, droneResults, requests) {
  const online = brokerResults.filter((result) => result.ok).length;
  const idle = droneResults.filter((result) => result.ok && result.data.status === "idle").length;
  const queued = requests.filter((request) => request.status === "queued").length;
  const completed = droneResults
    .filter((result) => result.ok)
    .reduce((sum, result) => sum + Number(result.data.completed || 0), 0);

  $("#onlineBrokers").textContent = `${online}/3`;
  $("#idleDrones").textContent = `${idle}/3`;
  $("#queuedRequests").textContent = queued;
  $("#completedMissions").textContent = completed;
}

async function refresh() {
  $("#refreshStatus").textContent = "Atualizando...";

  const brokerResults = await Promise.all(
    brokers.map((broker) =>
      getJson(`${broker.url}/state`)
        .then((data) => ({ ok: true, data }))
        .catch((error) => ({ ok: false, error })),
    ),
  );

  const droneResults = await Promise.all(
    drones.map((drone) =>
      getJson(`${drone.url}/status`)
        .then((data) => ({ ok: true, data }))
        .catch((error) => ({ ok: false, error })),
    ),
  );

  const requests = collectRequests(brokerResults);
  renderMetrics(brokerResults, droneResults, requests);
  renderBrokerCards(brokerResults);
  renderDroneCards(droneResults);
  renderRequests(requests);
  $("#refreshStatus").textContent = `Atualizado ${new Date().toLocaleTimeString()}`;
}

async function createRequest(event) {
  event.preventDefault();
  const brokerUrl = $("#brokerSelect").value;
  const payload = {
    event_type: $("#eventType").value,
    criticality: Number($("#criticality").value),
    lat: Number((24 + Math.random() * 4.8).toFixed(5)),
    lon: Number((52 + Math.random() * 6.8).toFixed(5)),
  };

  const response = await fetch(`${brokerUrl}/request-drone`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    alert("Nao foi possivel criar a ocorrencia.");
    return;
  }
  await refresh();
}

$("#requestForm").addEventListener("submit", createRequest);
$("#refreshButton").addEventListener("click", refresh);
refresh();
setInterval(refresh, 3000);
