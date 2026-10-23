import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { parseHTML } from "linkedom";

const { window } = parseHTML("<html><body></body></html>");
Object.assign(globalThis, { window, document: window.document, HTMLElement: window.HTMLElement, customElements: window.customElements, CustomEvent: window.CustomEvent });
await import("../custom_components/integrationmanager/frontend/panel.js");

let panel, calls;
beforeEach(() => {
  calls = [];
  panel = document.createElement("integrationmanager-panel");
  panel._hass = {
    states: { "update.native": { state: "on" } },
    callWS: async (message) => {
      calls.push(message);
      if (message.action === "overview") return { integrations: panel.items, channel: "stable", restart_required: false };
      if (message.action === "catalog") return { integrations: panel.catalog };
      return { restart_required: true };
    },
  };
});

test("friendly names, safe text and native entity controls", () => {
  panel.items = [{ name: "Friendly <script>alert(1)</script>", domain: "sample", version: "0.1.0", files_present: true, entities: [{ entity_id: "update.native", name: "Native update", state: "off" }] }];
  panel.render();
  assert.equal(panel.shadowRoot.querySelector("h3").textContent, panel.items[0].name);
  assert.equal(panel.shadowRoot.querySelector("script"), null);
  assert.equal(panel.shadowRoot.querySelector("[data-entity]").textContent, "Update available");
  let event;
  panel.addEventListener("hass-more-info", (value) => { event = value; });
  [...panel.shadowRoot.querySelectorAll("button")].find((b) => b.textContent === "Native update").click();
  assert.equal(event.detail.entityId, "update.native");
});

test("manager cannot uninstall itself; catalog uses integration name", () => {
  panel.items = [{ name: "Integration Manager", domain: "integrationmanager", version: "0.1.5", files_present: true, entities: [] }];
  panel.catalog = [{ name: "Friendly Integration", repository: "THEFLDR/HA-Slug", domain: "sample", installed: false }];
  panel.render();
  const text = [...panel.shadowRoot.childNodes].map((node) => node.textContent).join("");
  assert(!text.includes("Remove files"));
  assert(!text.includes("HA-Slug"));
  assert(text.includes("Install Friendly Integration"));
});

test("failed catalog leaves installed overview usable", async () => {
  panel.items = [{ name: "Unlisted Integration", domain: "unlisted", files_present: true, entities: [] }];
  panel._hass.callWS = async ({ action }) => {
    if (action === "catalog") throw new Error("offline");
    return { integrations: panel.items, channel: "stable" };
  };
  await panel.refresh();
  assert([...panel.shadowRoot.childNodes].map((node) => node.textContent).join("").includes("Unlisted Integration"));
  assert([...panel.shadowRoot.childNodes].map((node) => node.textContent).join("").includes("Catalog unavailable: offline"));
});

test("file operation is single-flight and displays restart requirement", async () => {
  await panel.perform("install", { domain: "sample" });
  assert(calls.some((c) => c.action === "install" && c.domain === "sample"));
  assert([...panel.shadowRoot.childNodes].map((node) => node.textContent).join("").includes("Restart Home Assistant manually"));
  panel.busy = true;
  const count = calls.length;
  await panel.perform("remove", { domain: "sample" });
  assert.equal(calls.length, count);
});

test("uninstall and cleanup require distinct confirmations", () => {
  panel.items = [{ name: "Friendly Integration", domain: "sample", files_present: true, entities: [] }];
  panel.render();
  let request;
  panel.perform = (action, extra) => { request = { action, extra }; };
  window.confirm = () => false;
  const buttons = [...panel.shadowRoot.querySelectorAll("button")];
  buttons.find((b) => b.textContent === "Remove files").click();
  assert.equal(request, undefined);
  window.confirm = () => true;
  buttons.find((b) => b.textContent === "Remove files").click();
  assert.equal(request.action, "remove");
  window.prompt = () => "yes";
  buttons.find((b) => b.textContent.startsWith("Clean up")).click();
  assert.equal(request.action, "remove");
  window.prompt = () => "REMOVE sample CONFIGURATION";
  buttons.find((b) => b.textContent.startsWith("Clean up")).click();
  assert.equal(request.action, "cleanup");
  assert.equal(request.extra.confirmation, "REMOVE sample CONFIGURATION");
});

test("native state changes render without polling", () => {
  panel.items = [{ name: "Sample", domain: "sample", entities: [{ entity_id: "update.native", name: "Update", state: "off" }] }];
  panel.render();
  panel.hass = { ...panel._hass, states: { "update.native": { state: "off" } } };
  assert.equal(panel.shadowRoot.querySelector("[data-entity]").textContent, "Up-to-date");
  assert.equal(calls.length, 0);
});

test("health, update, channel and mode display live human-readable states", () => {
  const entities = [
    { entity_id: "binary_sensor.owner_health", name: "Health", kind: "health" },
    { entity_id: "update.owner_update", name: "Update", kind: "update" },
    { entity_id: "sensor.owner_channel", name: "Release Channel", kind: "release_channel" },
    { entity_id: "sensor.owner_mode", name: "Release Channel Mode", kind: "release_channel_mode" },
  ];
  panel.items = [{ name: "Integration Manager", domain: "integrationmanager", entities }];
  panel._hass.states = {
    "binary_sensor.owner_health": { state: "off" },
    "update.owner_update": { state: "off" },
    "sensor.owner_channel": { state: "Managed", attributes: { effective_channel: "release_candidate" } },
    "sensor.owner_mode": { state: "Managed" },
  };
  panel.render();
  const states = () => [...panel.shadowRoot.querySelectorAll("tbody tr [data-entity]")].map((element) => element.textContent);
  assert.deepEqual(states(), ["Release Candidate", "Managed", "OK", "Up-to-date"]);
  panel.hass = { ...panel._hass, states: {
    "binary_sensor.owner_health": { state: "on" },
    "update.owner_update": { state: "on" },
    "sensor.owner_channel": { state: "Stable", attributes: { effective_channel: "stable" } },
    "sensor.owner_mode": { state: "Override" },
  } };
  assert.deepEqual(states(), ["Stable", "Override", "Problem", "Update available"]);
  panel.hass = { ...panel._hass, states: {
    "binary_sensor.owner_health": { state: "unavailable" },
    "update.owner_update": { state: "unknown" },
  } };
  assert.deepEqual(states(), ["Unavailable", "Unavailable", "Unavailable", "Unknown"]);
  assert.equal(calls.length, 0);
});

test("dashboard update check is single-flight and reports success and failure", async () => {
  panel.items = [{ name: "Other Integration", domain: "other", entities: [] }];
  panel.render();
  assert.equal([...panel.shadowRoot.querySelectorAll("button")].filter((b) => b.textContent === "Check for Updates").length, 1);
  const original = panel._hass.callWS;
  let finish;
  panel._hass.callWS = (request) => request.action === "check_updates" ? new Promise((resolve) => { finish = resolve; }) : original(request);
  const checking = panel.perform("check_updates");
  assert.equal(panel.message, "Checking for updates…");
  assert(panel.busy);
  assert([...panel.shadowRoot.querySelectorAll("button")].every((button) => button.disabled));
  await panel.perform("check_updates");
  finish({ message: "Update available: 0.2.0." });
  await checking;
  assert.equal(panel.message, "Update available: 0.2.0.");
  panel._hass.callWS = async (request) => {
    if (request.action === "check_updates") throw new Error("Update check failed: offline");
    return original(request);
  };
  await panel.perform("check_updates");
  assert.equal(panel.message, "Update check failed: offline");
  assert(!panel.busy);
});

test("global and manager channels use separate controls and commands", async () => {
  panel._hass.callWS = async (request) => {
    calls.push(request);
    if (request.action === "overview") return { integrations: [], channel: "release_candidate", manager_channel: "stable", manager_channel_mode: "override", manager_channel_override: "stable" };
    return { integrations: [] };
  };
  await panel.refresh();
  const selects = [...panel.shadowRoot.querySelectorAll("select")];
  assert.equal(selects.length, 3);
  const requests = [];
  panel.perform = (action, data) => requests.push({ action, ...data });
  Object.defineProperty(selects[0], "value", { value: "stable" });
  selects[0].dispatchEvent(new window.Event("change"));
  Object.defineProperty(selects[1], "value", { value: "default" });
  selects[1].dispatchEvent(new window.Event("change"));
  Object.defineProperty(selects[2], "value", { value: "release_candidate" });
  selects[2].dispatchEvent(new window.Event("change"));
  assert.deepEqual(requests, [
    { action: "channel", channel: "stable" },
    { action: "manager_channel", mode: "default" },
    { action: "manager_channel", mode: "override", channel: "release_candidate" },
  ]);
});

test("compact list scales with closed accessible details and no invented statuses", () => {
  panel.items = Array.from({ length: 80 }, (_, index) => ({
    domain: `sample_${index}`, name: `Sample ${index}`, version: "1.2.3", files_present: true, entities: [],
  }));
  panel.render();
  const table = panel.shadowRoot.querySelector("table");
  assert.deepEqual([...table.querySelectorAll("th")].map((cell) => cell.textContent),
    ["Integration", "Installed version", "Release Channel", "Release Channel Mode", "Health", "Update", "Details / actions"]);
  assert.equal(table.querySelectorAll("tbody tr").length, 80);
  assert.equal(table.querySelectorAll("details[open]").length, 0);
  assert(table.querySelector("summary").getAttribute("aria-label").includes("Sample 0"));
  assert.equal(table.querySelectorAll("td")[2].textContent, "Not provided");
  assert.equal(table.querySelectorAll("td")[3].textContent, "—");
  assert.equal(table.querySelectorAll("td")[4].textContent, "Not provided");
  assert.equal(table.querySelectorAll("td")[5].textContent, "Not provided");
  assert.equal(table.querySelectorAll("td")[3].querySelector("button"), null);
  assert.equal(panel.shadowRoot.querySelector(".table-scroll").getAttribute("tabindex"), "0");
  assert.equal(panel.shadowRoot.querySelector(".table-scroll").getAttribute("role"), "region");
});

test("expanded details survive refresh and keep native entity and removal actions", async () => {
  panel.items = [{ domain: "sample", name: "Sample", version: "1.2.3", files_present: true,
    entities: [{ entity_id: "update.native", name: "Native update", kind: "update" }] }];
  panel.render();
  panel.shadowRoot.querySelector("details").setAttribute("open", "");
  await panel.refresh();
  const details = panel.shadowRoot.querySelector("details");
  assert(details.hasAttribute("open"));
  assert.deepEqual([...details.querySelectorAll("button")].map((button) => button.textContent),
    ["Native update", "Update available", "Remove files", "Clean up HA configuration…"]);
  let entityId;
  panel.addEventListener("hass-more-info", (event) => { entityId = event.detail.entityId; });
  details.querySelector("button").click();
  assert.equal(entityId, "update.native");
  details.removeAttribute("open");
  panel.render();
  assert(!panel.shadowRoot.querySelector("details").hasAttribute("open"));
});

test("manager settings own its controls while list retains a compact summary", async () => {
  const entity = { entity_id: "update.native", name: "Manager update", kind: "update" };
  panel.items = [{ domain: "integrationmanager", name: "Integration Manager", version: "0.1.5", files_present: true, entities: [entity] }];
  panel.managerMode = "managed";
  panel.render();
  const manager = panel.shadowRoot.querySelector("[data-settings=manager]");
  const global = panel.shadowRoot.querySelector("[data-settings=global]");
  assert.equal(global.querySelector("h2").textContent, "Global Settings");
  assert.equal(manager.querySelector("h2").textContent, "Integration Manager");
  assert.equal(global.querySelectorAll("select").length, 1);
  assert.equal(manager.querySelectorAll("select").length, 1);
  assert.equal(panel.shadowRoot.querySelector("tbody details").querySelectorAll("button").length, 0);
  assert.equal(manager.querySelector("button").textContent, "Check for Updates");
  assert.equal(manager.querySelectorAll("[data-entity]").length, 0);
  panel.managerMode = "override";
  panel.managerOverride = "release_candidate";
  panel.render();
  assert.equal(panel.shadowRoot.querySelectorAll("[data-settings=manager] select").length, 2);
  panel.restart = true;
  panel.render();
  assert([...panel.shadowRoot.querySelectorAll("select")].every((select) => select.disabled));
});

test("list summaries reflect live unknown, unavailable, channel and mode states", () => {
  panel.items = [{ domain: "sample", name: "Sample", files_present: true, entities: [
    { entity_id: "binary_sensor.health", name: "Health", kind: "health" },
    { entity_id: "update.native", name: "Update", kind: "update" },
    { entity_id: "sensor.channel", name: "Channel", kind: "release_channel" },
    { entity_id: "sensor.mode", name: "Mode", kind: "release_channel_mode" },
  ] }];
  panel.render();
  const values = () => [...panel.shadowRoot.querySelectorAll("tbody [data-entity]")].slice(0, 4).map((node) => node.textContent);
  panel.hass = { ...panel._hass, states: {
    "binary_sensor.health": { state: "unknown" }, "update.native": { state: "unavailable" },
    "sensor.channel": { state: "Release Candidate" }, "sensor.mode": { state: "Default" },
  } };
  assert.deepEqual(values(), ["Release Candidate", "Default", "Unknown", "Unavailable"]);
  panel.hass = { ...panel._hass, states: {
    "binary_sensor.health": { state: "off" }, "update.native": { state: "on" },
    "sensor.channel": { state: "Stable" }, "sensor.mode": { state: "Managed" },
  } };
  assert.deepEqual(values(), ["Stable", "Managed", "OK", "Update available"]);
  assert.equal(calls.length, 0);
});

test("each table state opens its native entity dialog with an accessible live label", () => {
  const entities = [
    { entity_id: "sensor.channel", name: "Release Channel", kind: "release_channel" },
    { entity_id: "sensor.mode", name: "Release Channel Mode", kind: "release_channel_mode" },
    { entity_id: "binary_sensor.health", name: "Health", kind: "health", disabled: true },
    { entity_id: "update.native", name: "Update", kind: "update", hidden: true },
  ];
  panel.items = [{ domain: "integrationmanager", name: "Integration Manager", entities, device_id: "actual-service-id", version: "0.1.5", files_present: true }];
  panel.render();
  const opened = [];
  panel.addEventListener("hass-more-info", (event) => {
    assert(event.bubbles);
    opened.push(event.detail.entityId);
  });
  const buttons = [...panel.shadowRoot.querySelectorAll("tbody [data-entity]")];
  assert.equal(buttons.length, 4);
  const cells = [...panel.shadowRoot.querySelectorAll("tbody tr > td")];
  assert.equal(cells.length, 7);
  assert.equal(cells[2].querySelectorAll("button").length, 1);
  assert.equal(cells[3].querySelectorAll("button").length, 1);
  assert.equal(cells[3].querySelector("button").dataset.entity, "sensor.mode");
  for (const button of buttons) {
    assert.equal(button.tagName, "BUTTON");
    assert(button.getAttribute("aria-label").includes(button.textContent));
    assert(!button.disabled);
    button.click();
  }
  assert.deepEqual(opened, entities.map((entity) => entity.entity_id));
  assert.equal(panel.shadowRoot.querySelector("tbody details").querySelectorAll("button").length, 0);
  panel.hass = { ...panel._hass, states: { "update.native": { state: "off" } } };
  assert.equal(buttons[3].getAttribute("aria-label"), "Update: Up-to-date");
});

test("integration names link only to registry-supplied destinations and settings stay concise", () => {
  panel.items = [
    { domain: "integrationmanager", name: "Integration Manager", entities: [], device_id: "native-service" },
    { domain: "one", name: "Unique device", entities: [], device_id: "native-device" },
    { domain: "many", name: "Ambiguous devices", entities: [], device_id: null },
    { domain: "none", name: "No registry destination", entities: [] },
  ];
  panel.render();
  const links = [...panel.shadowRoot.querySelectorAll("tbody h3 a")];
  assert.deepEqual(links.map((link) => link.getAttribute("href")),
    ["/config/devices/device/native-service", "/config/devices/device/native-device"]);
  assert.equal(panel.shadowRoot.querySelectorAll("tbody h3").length, 4);
  const settings = panel.shadowRoot.querySelector("[data-settings=manager]");
  assert.equal(settings.querySelectorAll("select").length, 1);
  assert.equal(settings.querySelectorAll("button").length, 1);
  assert.equal(settings.querySelectorAll("[data-entity]").length, 0);
  assert(!settings.textContent.includes("Installed version"));
  assert(!settings.textContent.includes("Effective Release Channel"));
});

// HA sidebar icons accept path providers, not the raster image used here.
test("dashboard uses the approved PNG through the existing static route", () => {
  panel.render();
  const image = panel.shadowRoot.querySelector("header img");
  assert.equal(image.getAttribute("src"), "/integrationmanager_static/icon.png");
  assert.equal(panel.shadowRoot.querySelectorAll("header img").length, 1);
});
