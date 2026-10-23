const TYPE = "integrationmanager/command";

export class IntegrationManagerPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.items = [];
    this.catalog = [];
    this.unsubscribers = [];
    this.busy = false;
    this.loaded = false;
  }

  set hass(value) {
    this._hass = value;
    if (this.isConnected && !this.loaded) this.initialize();
    else this.updateStates();
  }

  connectedCallback() {
    if (this._hass && !this.loaded) this.initialize();
  }

  disconnectedCallback() {
    this.loaded = false;
    clearTimeout(this.refreshTimer);
    this.unsubscribers.splice(0).forEach((unsubscribe) => unsubscribe());
  }

  async initialize() {
    this.loaded = true;
    this.render();
    await this.refresh();
    for (const type of ["entity_registry_updated", "device_registry_updated"]) {
      try {
        const unsubscribe = await this._hass.connection.subscribeEvents(() => {
          clearTimeout(this.refreshTimer);
          this.refreshTimer = setTimeout(() => this.refresh(), 250);
        }, type);
        if (this.isConnected) this.unsubscribers.push(unsubscribe);
        else unsubscribe();
      } catch (error) { this.showMessage(String(error.message || error)); }
    }
  }

  call(action, extra = {}) {
    return this._hass.callWS({ type: TYPE, action, ...extra });
  }

  async refresh() {
    try {
      const overview = await this.call("overview");
      this.items = overview.integrations;
      this.channel = overview.channel;
      this.managerMode = overview.manager_channel_mode || "managed";
      this.managerOverride = overview.manager_channel_override || "stable";
      this.restart = overview.restart_required;
      try {
        this.catalog = (await this.call("catalog")).integrations;
        this.catalogError = "";
      } catch (error) {
        this.catalog = [];
        this.catalogError = String(error.message || error);
      }
      this.render();
    } catch (error) { this.showMessage(String(error.message || error)); }
  }

  node(tag, text, parent) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (parent) parent.append(node);
    return node;
  }

  button(text, parent, callback) {
    const button = this.node("button", text, parent);
    button.disabled = this.busy;
    button.addEventListener("click", callback);
    return button;
  }

  showMessage(message) {
    this.message = message;
    const target = this.shadowRoot.querySelector("[role=status]");
    if (target) target.textContent = message;
  }

  async perform(action, extra) {
    if (this.busy) return;
    this.busy = true;
    if (action === "check_updates") this.message = "Checking for updates…";
    this.render();
    try {
      const result = await this.call(action, extra);
      this.message = result.message || (result.restart_required ? "Restart Home Assistant manually to apply file changes." : "Saved.");
    } catch (error) {
      this.message = String(error.message || error);
    } finally {
      this.busy = false;
      await this.refresh();
    }
  }

  updateStates() {
    for (const target of this.shadowRoot.querySelectorAll("[data-entity]")) {
      const state = this._hass.states[target.dataset.entity];
      const value = state?.state || "unavailable";
      const kind = target.dataset.kind;
      let label = value;
      if (value === "unknown") label = "Unknown";
      else if (value === "unavailable") label = "Unavailable";
      else if (kind === "health") label = { off: "OK", on: "Problem" }[value] || value;
      else if (kind === "update") label = { off: "Up-to-date", on: "Update available" }[value] || value;
      else if (kind === "release_channel") {
        const effective = state.attributes?.effective_channel || value;
        label = { stable: "Stable", release_candidate: "Release Candidate", Stable: "Stable", "Release Candidate": "Release Candidate" }[effective] || "Unknown";
      }
      target.textContent = label;
      if (target.tagName === "BUTTON") target.setAttribute("aria-label", `${target.dataset.name || "Entity"}: ${label}`);
    }
  }

  entityKind(entity) {
    return entity.kind || (entity.entity_id.startsWith("update.") ? "update" : "");
  }

  entityState(entity, parent) {
    const state = entity
      ? this.button("Unavailable", parent, () => this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: entity.entity_id }, bubbles: true, composed: true })))
      : this.node("span", "Not provided", parent);
    if (entity) {
      state.className = "entity-action";
      state.dataset.entity = entity.entity_id;
      state.dataset.kind = this.entityKind(entity);
      state.dataset.name = entity.name;
      if (entity.disabled || entity.hidden) state.title = "Disabled or hidden in Home Assistant; open native entity settings.";
    }
    return state;
  }

  entities(entities, parent) {
    const list = this.node("div", undefined, parent);
    list.className = "entities";
    if (!entities.length) this.node("small", "No matching native entities are registered.", list);
    for (const entity of entities) {
      const line = this.node("div", undefined, list);
      const action = this.button(entity.name, line, () => this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: entity.entity_id }, bubbles: true, composed: true })));
      action.className = "entity-action";
      this.entityState(entity, line);
      if (entity.disabled || entity.hidden) this.node("small", "This native entity is disabled or hidden in its registry settings.", line);
    }
  }

  channelSelect(label, value, parent, onChange, options = [["stable", "Stable"], ["release_candidate", "Release Candidate"]]) {
    const wrapper = this.node("label", label, parent);
    const select = this.node("select", undefined, wrapper);
    for (const [key, text] of options) {
      const option = this.node("option", text, select);
      option.value = key;
      option.selected = value === key;
    }
    select.disabled = this.busy || this.restart;
    select.addEventListener("change", () => onChange(select.value));
    return select;
  }

  render() {
    const root = this.shadowRoot;
    const expanded = new Set([...root.querySelectorAll("details[open]")].map((details) => details.dataset.domain));
    root.replaceChildren();
    const style = this.node("style", undefined, root);
    // Standard HTML controls work in custom panels without lazy-loading private HA elements.
    style.textContent = `
      :host{display:block;box-sizing:border-box;padding:16px;color:var(--primary-text-color,#212121);background:var(--primary-background-color,#fafafa);font-family:var(--ha-font-family-body,Roboto,sans-serif);font-size:var(--ha-font-size-m,14px);color-scheme:light dark}
      *{box-sizing:border-box}header,.heading,.actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}header img{width:36px;height:36px}h1{font-size:22px;margin:0}h2{font-size:17px;margin:0 0 8px}h3{font-size:15px;margin:0}p{margin:8px 0}
      section{background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#ddd);border-radius:var(--ha-card-border-radius,12px);padding:12px;margin:12px 0}.settings{display:grid;align-items:start;grid-template-columns:minmax(180px,1fr) minmax(280px,2fr);gap:12px}.settings section{margin:0}
      button,select,summary{font:inherit}button,select{min-height:36px;max-width:100%;border:1px solid var(--divider-color,#ccc);border-radius:6px;padding:6px 10px;background:var(--secondary-background-color,#f1f1f1);color:var(--primary-text-color,#212121)}button,summary,select{cursor:pointer}button:disabled,select:disabled{opacity:.6;cursor:default}button:hover:not(:disabled){background:var(--divider-color,#ddd)}:is(button,select,summary,a):focus-visible{outline:2px solid var(--primary-color,#03a9f4);outline-offset:2px}
      a{color:var(--primary-color,#03a9f4);text-decoration:none}a:hover{text-decoration:underline}
      label{display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;margin:6px 0}small{display:block;color:var(--secondary-text-color,#727272);margin-top:4px}.entities{display:grid;gap:4px;margin-top:8px}.entity-action{background:transparent;border-color:transparent;color:var(--primary-color,#03a9f4);padding:4px;min-height:32px}.entities>div{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.entities small{flex-basis:100%}
      .table-scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;text-align:left;min-width:760px}caption{text-align:left;color:var(--secondary-text-color,#727272);padding-bottom:8px}th,td{padding:8px;vertical-align:top;border-bottom:1px solid var(--divider-color,#ddd)}th{font-weight:500}td .entity-action{min-height:28px;padding:2px 4px}tbody tr:last-child td{border:0}td:first-child{min-width:150px}summary{padding:4px 0;white-space:nowrap}details .actions{margin-top:8px}details .entities{min-width:220px}summary::marker{color:var(--primary-color,#03a9f4)}.catalog-row{display:flex;align-items:center;justify-content:space-between;gap:8px;border-top:1px solid var(--divider-color,#ddd);padding:8px 0}.catalog-row:first-of-type{border:0}[role=status]{white-space:pre-wrap;min-height:1em}
      @media(max-width:650px){:host{padding:8px}.settings{grid-template-columns:minmax(0,1fr)}section{padding:10px}th,td{padding:6px}}`;
    const header = this.node("header", undefined, root);
    const image = this.node("img", undefined, header);
    image.src = "/integrationmanager_static/icon.png";
    image.alt = "";
    this.node("h1", "Integration Manager", header);
    this.button("Refresh", header, () => this.refresh());
    const feedback = this.node("p", this.message || "", root);
    feedback.setAttribute("role", "status");
    feedback.setAttribute("aria-live", "polite");
    if (this.restart) this.node("p", "Restart required. Restart Home Assistant manually to apply file changes.", root);
    const settings = this.node("div", undefined, root);
    settings.className = "settings";
    const global = this.node("section", undefined, settings);
    global.setAttribute("aria-labelledby", "global-heading");
    global.dataset.settings = "global";
    this.node("h2", "Global Settings", global).id = "global-heading";
    this.channelSelect("Global Release Channel", this.channel, global, (channel) => this.perform("channel", { channel }));
    const manager = this.node("section", undefined, settings);
    manager.setAttribute("aria-labelledby", "manager-heading");
    manager.dataset.settings = "manager";
    this.node("h2", "Integration Manager", manager).id = "manager-heading";
    this.channelSelect("Release Channel Mode", this.managerMode || "managed", manager, (mode) => this.perform("manager_channel", {
      mode, ...(mode === "override" ? { channel: this.managerOverride || "stable" } : {}),
    }), [["managed", "Managed"], ["default", "Default"], ["override", "Override"]]);
    if (this.managerMode === "override") {
      this.channelSelect("Override Channel", this.managerOverride, manager, (channel) => this.perform("manager_channel", { mode: "override", channel }));
    }
    this.button("Check for Updates", manager, () => this.perform("check_updates"));
    const installed = this.node("section", undefined, root);
    installed.setAttribute("aria-labelledby", "installed-heading");
    const heading = this.node("div", undefined, installed);
    heading.className = "heading";
    this.node("h2", "Installed custom integrations", heading).id = "installed-heading";
    const scroll = this.node("div", undefined, installed);
    scroll.className = "table-scroll";
    scroll.tabIndex = 0;
    scroll.setAttribute("role", "region");
    scroll.setAttribute("aria-label", "Installed integrations; scroll horizontally on narrow screens");
    const table = this.node("table", undefined, scroll);
    this.node("caption", "Live native entity states. Not provided means no matching entity is registered.", table);
    const headings = this.node("tr", undefined, this.node("thead", undefined, table));
    for (const name of ["Integration", "Installed version", "Release Channel", "Release Channel Mode", "Health", "Update", "Details / actions"]) this.node("th", name, headings).setAttribute("scope", "col");
    const body = this.node("tbody", undefined, table);
    for (const item of this.items) {
      const row = this.node("tr", undefined, body);
      row.dataset.integration = item.domain;
      const name = this.node("h3", undefined, this.node("td", undefined, row));
      if (item.device_id) {
        // Same native device/service URL used by HA's integration page. The
        // backend supplies a destination only for a unique live registry entry.
        const link = this.node("a", item.name, name);
        link.href = `/config/devices/device/${encodeURIComponent(item.device_id)}`;
      } else name.textContent = item.name;
      this.node("td", item.version || "Unknown", row);
      for (const kind of ["release_channel", "release_channel_mode", "health", "update"]) {
        const cell = this.node("td", undefined, row);
        const entity = item.entities.find((entity) => this.entityKind(entity) === kind);
        if (!entity && kind === "release_channel_mode") this.node("span", "—", cell);
        else this.entityState(entity, cell);
      }
      const details = this.node("details", undefined, this.node("td", undefined, row));
      details.dataset.domain = item.domain;
      if (expanded.has(item.domain)) details.setAttribute("open", "");
      const summary = this.node("summary", "Details", details);
      summary.setAttribute("aria-label", `Details and actions for ${item.name}`);
      this.node("small", `${item.domain}${item.files_present ? "" : " · Files removed"}`, details);
      if (item.domain === "integrationmanager") this.node("p", "Open the table states for native entity controls and updates.", details);
      else {
        this.entities(item.entities, details);
        const actions = this.node("div", undefined, details);
        actions.className = "actions";
        if (item.files_present) this.button("Remove files", actions, () => {
          if (window.confirm(`Remove ${item.name} files? Configuration and data are preserved. A manual HA restart is required.`)) this.perform("remove", { domain: item.domain });
        });
        this.button("Clean up HA configuration…", actions, () => {
          const confirmation = window.prompt(`Remove HA configuration entries owned by ${item.name}? Integration removal hooks may clean their own data. Shared files, YAML, backups and uncertain data remain. This cannot guarantee zero residue. Type REMOVE ${item.domain} CONFIGURATION to confirm.`);
          if (confirmation === `REMOVE ${item.domain} CONFIGURATION`) this.perform("cleanup", { domain: item.domain, confirmation });
        });
      }
    }
    if (!this.items.length) this.node("p", "No custom integrations found.", installed);
    const catalog = this.node("section", undefined, root);
    this.node("h2", "Available integrations", catalog);
    if (this.catalogError) this.node("p", `Catalog unavailable: ${this.catalogError}`, catalog);
    else if (!this.catalog.length) this.node("p", "The catalog is empty.", catalog);
    for (const item of this.catalog) {
      const row = this.node("div", undefined, catalog);
      row.className = "catalog-row";
      const description = this.node("div", undefined, row);
      this.node("h3", item.name, description);
      this.node("small", item.installed ? `Installed · ${item.version || "Version unknown"}` : "Not installed", description);
      if (!item.installed) this.button(`Install ${item.name}`, row, () => {
        if (window.confirm(`Install ${item.name} from the selected release channel? A manual HA restart is required.`)) this.perform("install", { domain: item.domain });
      });
    }
    this.updateStates();
  }
}

customElements.define("integrationmanager-panel", IntegrationManagerPanel);
