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
  assert.equal(panel.shadowRoot.querySelector("[data-entity]").textContent, "on");
  let event;
  panel.addEventListener("hass-more-info", (value) => { event = value; });
  [...panel.shadowRoot.querySelectorAll("button")].find((b) => b.textContent === "Native update").click();
  assert.equal(event.detail.entityId, "update.native");
});

test("manager cannot uninstall itself; catalog uses integration name", () => {
  panel.items = [{ name: "Integration Manager", domain: "integrationmanager", version: "0.1.1", files_present: true, entities: [] }];
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
  assert.equal(panel.shadowRoot.querySelector("[data-entity]").textContent, "off");
  assert.equal(calls.length, 0);
});
