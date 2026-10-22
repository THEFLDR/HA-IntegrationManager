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
      target.textContent = state?.state || "unavailable";
    }
  }

  render() {
    const root = this.shadowRoot;
    root.replaceChildren();
    const style = this.node("style", undefined, root);
    style.textContent = `:host{display:block;padding:24px;color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family, sans-serif)}header{display:flex;align-items:center;gap:16px}img{width:64px;height:64px}section{background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:12px;padding:20px;margin:18px 0}button,select{font:inherit;padding:10px;margin:4px;border-radius:6px;cursor:pointer}button:disabled{cursor:wait}li{margin:12px 0}small{display:block;color:var(--secondary-text-color)}[role=status]{white-space:pre-wrap}h1{font-size:24px}ul{padding-left:20px}`;
    const header = this.node("header", undefined, root);
    const image = this.node("img", undefined, header);
    image.src = "/integrationmanager_static/icon.png";
    image.alt = "Integration Manager";
    this.node("h1", "Integration Manager", header);
    this.node("p", this.message || "", root).setAttribute("role", "status");
    if (this.restart) this.node("p", "Restart required. Restart Home Assistant manually to apply file changes.", root);
    const settings = this.node("section", undefined, root);
    const label = this.node("label", "Global release channel: ", settings);
    const select = this.node("select", undefined, label);
    for (const [value, text] of [["stable", "Stable"], ["release_candidate", "Release Candidate"]]) {
      const option = this.node("option", text, select);
      option.value = value;
      option.selected = this.channel === value;
    }
    select.disabled = this.busy || this.restart;
    select.addEventListener("change", () => this.perform("channel", { channel: select.value }));
    this.button("Refresh", settings, () => this.refresh());
    const installed = this.node("section", undefined, root);
    this.node("h2", "Installed custom integrations", installed);
    this.node("p", "Native Health, Update and Release Channel entities are shown when their integration provides them. Open an entity to use its native controls.", installed);
    for (const item of this.items) {
      const row = this.node("section", undefined, installed);
      this.node("h3", item.name, row);
      this.node("small", `${item.version || "Version unknown"} · ${item.domain}${item.files_present ? "" : " · Files removed"}`, row);
      if (!item.entities.length) this.node("p", "No matching native entities are registered.", row);
      for (const entity of item.entities) {
        const line = this.node("p", undefined, row);
        this.button(entity.name, line, () => this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: entity.entity_id }, bubbles: true, composed: true })));
        const state = this.node("span", entity.state, line);
        state.dataset.entity = entity.entity_id;
        if (entity.disabled || entity.hidden) this.node("small", "This native entity is disabled or hidden in its registry settings.", line);
      }
      if (item.domain !== "integrationmanager") {
        if (item.files_present) this.button("Remove files", row, () => {
          if (window.confirm(`Remove ${item.name} files? Configuration and data are preserved. A manual HA restart is required.`)) this.perform("remove", { domain: item.domain });
        });
        this.button("Clean up HA configuration…", row, () => {
          const confirmation = window.prompt(`Remove HA configuration entries owned by ${item.name}? Integration removal hooks may clean their own data. Shared files, YAML, backups and uncertain data remain. This cannot guarantee zero residue. Type REMOVE ${item.domain} CONFIGURATION to confirm.`);
          if (confirmation === `REMOVE ${item.domain} CONFIGURATION`) this.perform("cleanup", { domain: item.domain, confirmation });
        });
      }
    }
    const catalog = this.node("section", undefined, root);
    this.node("h2", "Available integrations", catalog);
    if (this.catalogError) this.node("p", `Catalog unavailable: ${this.catalogError}`, catalog);
    else if (!this.catalog.length) this.node("p", "The catalog is empty.", catalog);
    for (const item of this.catalog) {
      const row = this.node("section", undefined, catalog);
      this.node("h3", item.name, row);
      this.node("small", item.installed ? `Installed · ${item.version || "Version unknown"}` : "Not installed", row);
      if (!item.installed) this.button(`Install ${item.name}`, row, () => {
        if (window.confirm(`Install ${item.name} from the selected release channel? A manual HA restart is required.`)) this.perform("install", { domain: item.domain });
      });
    }
    this.updateStates();
  }
}

customElements.define("integrationmanager-panel", IntegrationManagerPanel);
