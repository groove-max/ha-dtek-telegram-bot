const refreshButton = document.getElementById("refresh-button");
const lastUpdated = document.getElementById("last-updated");
const statsGrid = document.getElementById("stats-grid");
const addressesList = document.getElementById("addresses-list");
const discoveryList = document.getElementById("discovery-list");
const diagnosticsOutput = document.getElementById("diagnostics-output");
const editorContainer = document.getElementById("editor-container");
const editorStatus = document.getElementById("editor-status");
const templatesContainer = document.getElementById("templates-container");
const templateStatus = document.getElementById("template-status");
const addAddressButton = document.getElementById("add-address-button");
const importConfigButton = document.getElementById("import-config-button");
const exportConfigButton = document.getElementById("export-config-button");
const importAddressButton = document.getElementById("import-address-button");
const validateButton = document.getElementById("validate-button");
const saveButton = document.getElementById("save-button");
const saveRestartButton = document.getElementById("save-restart-button");
const configImportInput = document.getElementById("config-import-input");
const addressImportInput = document.getElementById("address-import-input");
const tabButtons = Array.from(document.querySelectorAll(".tab-button"));
const tabPanels = Array.from(document.querySelectorAll(".tab-panel"));

const UI_LOCALE = "uk-UA";
const REFRESH_INTERVAL_MS = 15000;
const MAX_QUICK_PICKS = 8;
const CONFIG_EXPORT_SCHEMA = "dtek-telegram-bot/config-export-v1";
const ADDRESS_EXPORT_SCHEMA = "dtek-telegram-bot/address-export-v1";
const TONE_LABELS = {
  good: "норма",
  warn: "увага",
  bad: "помилка",
  neutral: "інфо",
};
const HOUSE_STATE_META = {
  on: { label: "є світло", tone: "good" },
  partial: { label: "частково", tone: "warn" },
  off: { label: "нема світла", tone: "bad" },
};
const POWER_MODE_META = {
  dtek_only: {
    label: "Лише DTEK",
    copy: "Використовує лише binary_sensor.<prefix>_power. Найпростіше налаштування, але воно може відставати від зовнішніх сенсорів.",
  },
  voltage_only: {
    label: "Лише напруга",
    copy: "Будинок визначається тільки за топологією секції напруги. Те, чи рахуються unavailable/unknown як відсутність фази, задається окремо в секції «Напруга та фази».",
  },
  loss_plus_voltage: {
    label: "Швидка втрата + напруга",
    copy: "Використовує швидкий сенсор втрати як тригер, але підтверджує повний outage і відновлення тільки через загальну топологію секції напруги та її політику unavailable.",
  },
};

const state = {
  activeTab: "configure",
  overview: null,
  discovery: null,
  diagnostics: null,
  configPayload: null,
  templatesPayload: null,
  draftConfig: null,
  editorDirty: false,
  editorStatus: {
    message: "Редактор завантажується…",
    tone: "muted",
  },
  templateStatus: {
    message: "Шаблони завантажуються…",
    tone: "muted",
  },
  templateSelection: {
    addressIndex: 0,
    templateName: "",
  },
  templateSourceDraft: "",
  templateSourceDirty: false,
  templateSourceName: "",
  templatePreview: null,
  collapsedPanels: {},
  pendingAddressImportIndex: null,
};

function getAddressUiKey(address, index) {
  const prefix = address?.entity_prefix?.trim();
  return prefix || `draft-${index}`;
}

function getPanelStateKey(address, index, panelName) {
  return `${getAddressUiKey(address, index)}::${panelName}`;
}

function isPanelCollapsed(address, index, panelName, fallback = false) {
  const key = getPanelStateKey(address, index, panelName);
  if (Object.hasOwn(state.collapsedPanels, key)) {
    return Boolean(state.collapsedPanels[key]);
  }
  return fallback;
}

function rememberPanelState(key, collapsed) {
  state.collapsedPanels[key] = Boolean(collapsed);
}

function isInteractiveRefreshBlocked() {
  const active = document.activeElement;
  if (!(active instanceof HTMLElement)) {
    return false;
  }
  if (state.activeTab === "configure" && editorContainer.contains(active)) {
    return true;
  }
  if (state.activeTab === "templates" && templatesContainer.contains(active)) {
    return true;
  }
  return false;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function deepMerge(target, source) {
  const result = cloneJson(target);
  Object.entries(source || {}).forEach(([key, value]) => {
    if (Array.isArray(value)) {
      result[key] = cloneJson(value);
      return;
    }
    if (isPlainObject(value) && isPlainObject(result[key])) {
      result[key] = deepMerge(result[key], value);
      return;
    }
    result[key] = value;
  });
  return result;
}

function setEditorStatus(message, tone = "muted") {
  state.editorStatus = { message, tone };
  editorStatus.className = `status-message ${tone}`;
  editorStatus.textContent = message;
}

function setTemplateStatus(message, tone = "muted") {
  state.templateStatus = { message, tone };
  templateStatus.className = `status-message ${tone}`;
  templateStatus.textContent = message;
}

function toNumber(value, fallback, parser = Number.parseFloat) {
  const parsed = parser(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const details = data.errors
      ? data.errors.map((error) => `${error.loc.join(".")}: ${error.msg}`).join("; ")
      : data.message || response.statusText;
    throw new Error(details);
  }
  return data;
}

function createBlankAddress(prefill = {}) {
  return {
    entity_prefix: prefill.entity_prefix ?? "",
    display_name: prefill.display_name ?? prefill.friendly_name ?? "",
    telegram_chat_id: "",
    schedule_change: { enabled: true, silent: true },
    emergency: { enabled: true, silent: false },
    group_change: { enabled: true, silent: false },
    voltage: {
      enabled: false,
      entities: [],
      present_above: 50,
      unavailable_as_missing: true,
      low: 195,
      high: 245,
      delay: 15,
      silent: false,
    },
    power: {
      enabled: false,
      mode: "dtek_only",
      loss_entity: "",
      loss_state: "0",
      loss_delay: 30,
      confirm_timeout: 15,
      restore_delay: 10,
      silent: false,
      phase_notifications: true,
    },
    upcoming_outage: {
      enabled: true,
      minutes: 10,
      silent: false,
      power_filter: "only_when_available",
    },
    status_message: {
      enabled: true,
      update_interval: 300,
      min_update_interval: 10,
      delivery_mode: "pinned_edit",
      pin: true,
      silent: true,
    },
  };
}

function buildAddressExportPayload(address) {
  const normalizedAddress = deepMerge(createBlankAddress(), address || {});
  return {
    schema: ADDRESS_EXPORT_SCHEMA,
    version: 1,
    exported_at: new Date().toISOString(),
    address: normalizedAddress,
    source: {
      entity_prefix: normalizedAddress.entity_prefix || "",
      display_name: normalizedAddress.display_name || "",
    },
  };
}

function buildConfigExportPayload(config) {
  const normalizedConfig = {
    export_default_templates: Boolean(config?.export_default_templates),
    addresses: (config?.addresses || []).map((address) =>
      deepMerge(createBlankAddress(), address || {})
    ),
  };

  return {
    schema: CONFIG_EXPORT_SCHEMA,
    version: 1,
    exported_at: new Date().toISOString(),
    config: normalizedConfig,
    source: {
      address_count: normalizedConfig.addresses.length,
    },
  };
}

function normalizeImportedAddressPayload(payload) {
  const rawAddress =
    isPlainObject(payload?.address) ? payload.address : isPlainObject(payload) ? payload : null;

  if (!rawAddress) {
    throw new Error("Файл не містить коректного об'єкта адреси.");
  }

  const normalized = deepMerge(createBlankAddress(), rawAddress);
  normalized.entity_prefix = String(normalized.entity_prefix || "").trim();
  normalized.display_name = String(normalized.display_name || "").trim();
  normalized.telegram_chat_id = String(normalized.telegram_chat_id || "").trim();
  normalized.power.loss_entity = String(normalized.power?.loss_entity || "").trim();
  normalized.power.loss_state = String(normalized.power?.loss_state || "0").trim() || "0";

  normalized.voltage.entities = (normalized.voltage?.entities || [])
    .filter((item) => item && String(item.entity || "").trim())
    .map((item) => ({
      entity: String(item.entity || "").trim(),
      label: String(item.label || "").trim(),
    }));

  if (!normalized.entity_prefix) {
    throw new Error("У файлі адреси відсутній entity_prefix.");
  }

  return normalized;
}

function normalizeImportedConfigPayload(payload) {
  if (payload?.schema === ADDRESS_EXPORT_SCHEMA) {
    throw new Error("Це файл однієї адреси. Використайте кнопку «Імпортувати адресу».");
  }

  const rawConfig =
    isPlainObject(payload?.config) ? payload.config : isPlainObject(payload) ? payload : null;

  if (!rawConfig) {
    throw new Error("Файл не містить коректного об'єкта конфігурації.");
  }

  if (!Array.isArray(rawConfig.addresses)) {
    throw new Error("У файлі конфігурації відсутній список адрес.");
  }

  const currentConfig = getDraftConfig();
  return {
    ha_token: String(rawConfig.ha_token || currentConfig.ha_token || "").trim(),
    export_default_templates: Boolean(
      rawConfig.export_default_templates ?? currentConfig.export_default_templates ?? false,
    ),
    addresses: rawConfig.addresses.map((address) =>
      normalizeImportedAddressPayload({ address }),
    ),
  };
}

function downloadTextFile(filename, text, mimeType = "application/json") {
  const blob = new Blob([text], { type: `${mimeType};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function exportAddress(index) {
  const config = collectEditorConfig();
  const address = config.addresses[index];
  if (!address) {
    setEditorStatus("Не вдалося знайти адресу для експорту.", "status-bad");
    return;
  }

  const payload = buildAddressExportPayload(address);
  const slug = (address.entity_prefix || `address-${index + 1}`)
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-");
  downloadTextFile(`${slug}.dtek-address.json`, `${JSON.stringify(payload, null, 2)}\n`);
  setEditorStatus(
    `Налаштування адреси ${address.display_name || address.entity_prefix} експортовано у файл.`,
    "status-good",
  );
}

function exportConfig() {
  const config = collectEditorConfig();
  const payload = buildConfigExportPayload(config);
  downloadTextFile("dtek-telegram-bot-config.json", `${JSON.stringify(payload, null, 2)}\n`);
  setEditorStatus(
    "Поточний конфіг експортовано у файл. Він не записувався на пристрій add-on.",
    "status-good",
  );
}

function triggerAddressImport(targetIndex = null) {
  state.pendingAddressImportIndex =
    Number.isInteger(targetIndex) && targetIndex >= 0 ? targetIndex : null;
  addressImportInput.value = "";
  addressImportInput.click();
}

async function handleConfigImportFile(file) {
  if (!file) {
    return;
  }

  let parsed;
  try {
    parsed = JSON.parse(await file.text());
  } catch (error) {
    setEditorStatus("Не вдалося прочитати JSON-файл конфігурації.", "status-bad");
    return;
  }

  let importedConfig;
  try {
    importedConfig = normalizeImportedConfigPayload(parsed);
  } catch (error) {
    setEditorStatus(`Помилка імпорту конфіга: ${error.message}`, "status-bad");
    return;
  }

  setDraftConfig(
    importedConfig,
    "Конфіг імпортовано у чернетку. Запустіть перевірку перед збереженням на пристрій.",
    "status-good",
  );
}

async function handleAddressImportFile(file) {
  if (!file) {
    return;
  }

  let parsed;
  try {
    parsed = JSON.parse(await file.text());
  } catch (error) {
    setEditorStatus("Не вдалося прочитати JSON-файл адреси.", "status-bad");
    return;
  }

  let importedAddress;
  try {
    importedAddress = normalizeImportedAddressPayload(parsed);
  } catch (error) {
    setEditorStatus(`Помилка імпорту: ${error.message}`, "status-bad");
    return;
  }

  const config = getDraftConfig();
  const targetIndex = state.pendingAddressImportIndex;
  const duplicateIndex = config.addresses.findIndex(
    (address) => address.entity_prefix === importedAddress.entity_prefix,
  );

  if (targetIndex === null) {
    if (duplicateIndex >= 0) {
      config.addresses[duplicateIndex] = importedAddress;
      setDraftConfig(
        config,
        `Адресу ${importedAddress.entity_prefix} оновлено з файлу. Запустіть перевірку, щоб оновити прев'ю.`,
        "status-good",
      );
      return;
    }
    config.addresses.push(importedAddress);
    setDraftConfig(
      config,
      `Адресу ${importedAddress.entity_prefix} імпортовано в чернетку. Запустіть перевірку, щоб оновити прев'ю.`,
      "status-good",
    );
    return;
  }

  if (!config.addresses[targetIndex]) {
    setEditorStatus("Не вдалося знайти адресу для заміни під час імпорту.", "status-bad");
    return;
  }

  if (duplicateIndex >= 0 && duplicateIndex !== targetIndex) {
    setEditorStatus(
      `Адреса ${importedAddress.entity_prefix} вже є в чернетці. Імпортуйте її в наявну картку або видаліть дублікат.`,
      "status-warn",
    );
    return;
  }

  config.addresses[targetIndex] = importedAddress;
  setDraftConfig(
    config,
    `Адресу ${importedAddress.entity_prefix} імпортовано з файлу із заміною поточної картки. Запустіть перевірку, щоб оновити прев'ю.`,
    "status-good",
  );
}

function formatEntityLines(entities = []) {
  return entities
    .map((entity) => {
      const label = entity.label?.trim() ?? "";
      return label ? `${entity.entity} | ${label}` : entity.entity;
    })
    .join("\n");
}

function parseEntityLines(text) {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [entity, label = ""] = line.split("|");
      return {
        entity: entity.trim(),
        label: label.trim(),
      };
    })
    .filter((item) => item.entity);
}

function appendEntityLine(text, entity, label = "") {
  const line = label ? `${entity} | ${label}` : entity;
  const lines = text
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
  const exists = lines.some((item) => item.split("|")[0].trim() === entity);
  if (exists) {
    return text;
  }
  return [...lines, line].join("\n");
}

function getDraftConfig() {
  if (editorContainer.querySelector(".address-editor-card")) {
    return collectEditorConfig();
  }
  return state.draftConfig || state.configPayload?.config || { ha_token: "", export_default_templates: false, addresses: [] };
}

function getCatalogItems(group) {
  return state.discovery?.catalog?.[group] || [];
}

function getCandidateForPrefix(prefix) {
  if (!prefix) {
    return null;
  }
  return state.discovery?.candidates?.find((item) => item.entity_prefix === prefix) || null;
}

function getAddressCandidateOptions(currentPrefix = "", currentName = "") {
  const candidates = [...(state.discovery?.candidates || [])];
  const exists = candidates.some((item) => item.entity_prefix === currentPrefix);
  if (currentPrefix && !exists) {
    candidates.unshift({
      entity_prefix: currentPrefix,
      friendly_name: currentName || currentPrefix,
      configured: true,
    });
  }
  return candidates;
}

function getCatalogEntity(entityId) {
  if (!entityId) {
    return null;
  }

  const catalog = state.discovery?.catalog || {};
  for (const items of Object.values(catalog)) {
    const match = (items || []).find((item) => item.entity_id === entityId);
    if (match) {
      return match;
    }
  }
  return null;
}

function mergeSelectableItems(items = [], currentSelections = []) {
  const merged = [...items];
  const seen = new Set(items.map((item) => item.entity_id));

  currentSelections.forEach((item) => {
    const entityId = item?.entity || item?.entity_id || "";
    if (!entityId || seen.has(entityId)) {
      return;
    }
    seen.add(entityId);
    merged.push({
      entity_id: entityId,
      friendly_name: item?.label || entityId,
      state: "обрано",
      unit: "",
    });
  });

  return merged;
}

function chooseSuggestedEntity(group, address, currentValue = "") {
  if (currentValue) {
    return currentValue;
  }
  const items = getCatalogItems(group);
  if (!items.length) {
    return "";
  }
  const prefix = address?.entity_prefix || "";
  const matching = prefix
    ? items.find((item) => item.entity_id.includes(prefix) || item.friendly_name?.includes(prefix))
    : null;
  return (matching || items[0]).entity_id;
}

function formatEntityHint(item) {
  const stateValue = item?.state ?? "n/a";
  const unit = item?.unit ? ` ${item.unit}` : "";
  return `${stateValue}${unit}`;
}

function formatQuickPickLabel(item) {
  const name = item?.friendly_name || item?.entity_id || "entity";
  return `${name} (${formatEntityHint(item)})`;
}

function getHouseStateMeta(value) {
  return HOUSE_STATE_META[value] || { label: value || "n/a", tone: "neutral" };
}

function getPowerModeMeta(mode) {
  return POWER_MODE_META[mode] || POWER_MODE_META.dtek_only;
}

function getSuggestedVoltageEntities(address, currentSelections = []) {
  if ((currentSelections || []).length) {
    return currentSelections;
  }

  const prefix = address?.entity_prefix || "";
  const items = getCatalogItems("voltage_sensors");
  const matching = prefix
    ? items.filter(
        (item) =>
          item.entity_id.includes(prefix) ||
          item.friendly_name?.toLowerCase().includes(prefix.toLowerCase()),
      )
    : items;

  return matching.slice(0, 3).map((item) => ({
    entity: item.entity_id,
    label: "",
  }));
}

function renderEntityOptions(items, selectedValue = "", placeholder = "Оберіть entity") {
  return `
    <option value="">${escapeHtml(placeholder)}</option>
    ${items
      .map(
        (item) => `
          <option value="${escapeHtml(item.entity_id)}" ${item.entity_id === selectedValue ? "selected" : ""}>
            ${escapeHtml(formatQuickPickLabel(item))}
          </option>
        `,
      )
      .join("")}
  `;
}

function setActiveTab(tab) {
  state.activeTab = tab;
  tabButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tab);
  });
  tabPanels.forEach((panel) => {
    panel.classList.toggle("hidden", panel.id !== `panel-${tab}`);
  });
}

function renderStats() {
  const runtime = state.overview?.runtime || {};
  const global = state.overview?.global || {};
  const stats = [
    {
      label: "Процес",
      value: runtime.running ? "Працює" : "Зупинено",
      tone: runtime.running ? "good" : "bad",
    },
    {
      label: "HA Socket",
      value: runtime.ha_connected ? "Підключено" : "Відключено",
      tone: runtime.ha_connected ? "good" : "warn",
    },
    {
      label: "Адреси",
      value: String(global.address_count ?? 0),
      tone: "neutral",
    },
    {
      label: "Події в черзі",
      value: String(runtime.queued_events ?? 0),
      tone: Number(runtime.queued_events ?? 0) === 0 ? "good" : "warn",
    },
    {
      label: "Відстежувані entity",
      value: String(runtime.watched_entities ?? 0),
      tone: "neutral",
    },
    {
      label: "Telegram",
      value: global.has_telegram_token ? "Налаштовано" : "Немає токена",
      tone: global.has_telegram_token ? "good" : "bad",
    },
  ];

  statsGrid.innerHTML = stats
    .map(
      (stat) => `
        <article class="stat-card">
          <p class="stat-label">${escapeHtml(stat.label)}</p>
          <p class="stat-value">${escapeHtml(stat.value)}</p>
          <span class="badge ${escapeHtml(stat.tone)}">${escapeHtml(TONE_LABELS[stat.tone] || stat.tone)}</span>
        </article>
      `,
    )
    .join("");
}

function renderAddresses() {
  const addresses = state.overview?.addresses || [];
  if (!addresses.length) {
    addressesList.innerHTML = '<p class="empty-state">Ще немає налаштованих адрес.</p>';
    return;
  }

  addressesList.innerHTML = addresses
    .map((address) => {
      const powerModeMeta = getPowerModeMeta(address.power_mode);
      const livePowerMeta = getHouseStateMeta(address.live_power);
      const featureBadges = address.enabled_features
        .map((feature) => `<span class="badge neutral">${escapeHtml(feature)}</span>`)
        .join("");

      const liveEntities = Object.entries(address.live_entities)
        .map(
          ([entityId, value]) => `
            <div class="entity-row">
              <div>
                <p class="entity-name">${escapeHtml(value.friendly_name || entityId)}</p>
                <p class="entity-id">${escapeHtml(entityId)}</p>
              </div>
              <span class="entity-state">${escapeHtml(value.state ?? "—")}</span>
            </div>
          `,
        )
        .join("");

      const storedState = JSON.stringify(address.stored_state, null, 2);

      return `
        <article class="address-card">
          <div class="address-head">
            <div>
              <h3>${escapeHtml(address.display_name)}</h3>
              <p class="entity-id">${escapeHtml(address.entity_prefix)}</p>
            </div>
            <div class="address-meta">
              <span class="badge neutral">${escapeHtml(powerModeMeta.label)}</span>
              <span class="badge ${livePowerMeta.tone}">
                світло: ${escapeHtml(livePowerMeta.label)}
              </span>
            </div>
          </div>
          <div class="chip-row">${featureBadges || '<span class="badge neutral">без функцій</span>'}</div>
          <div class="meta-grid">
            <div>
              <p class="meta-label">Чат призначення</p>
              <p class="meta-value">${escapeHtml(address.chat_id || "не вказано")}</p>
            </div>
            <div>
              <p class="meta-label">Відстежувані entity</p>
              <p class="meta-value">${escapeHtml(address.watched_entities.length)}</p>
            </div>
          </div>
          <div class="subpanel">
            <h4>Живі entity</h4>
            ${liveEntities || '<p class="empty-state">Поки що немає живих даних entity.</p>'}
          </div>
          <div class="subpanel">
            <h4>Збережений state</h4>
            <pre class="mini-json">${escapeHtml(storedState)}</pre>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderCatalogSection(title, hint, items) {
  if (!items.length) {
    return "";
  }

  return `
    <article class="catalog-card">
      <h4>${escapeHtml(title)}</h4>
      <p class="catalog-copy">${escapeHtml(hint)}</p>
      <div class="stack compact">
        ${items
          .slice(0, MAX_QUICK_PICKS)
          .map(
            (item) => `
              <div class="catalog-row">
                <div>
                  <p class="entity-name">${escapeHtml(item.friendly_name || item.entity_id)}</p>
                  <p class="entity-id">${escapeHtml(item.entity_id)}</p>
                </div>
                <span class="entity-state">${escapeHtml(formatEntityHint(item))}</span>
              </div>
            `,
          )
          .join("")}
      </div>
    </article>
  `;
}

function renderDiscovery() {
  const discovery = state.discovery;
  if (!discovery?.ha_connected) {
    discoveryList.innerHTML =
      '<p class="empty-state">Home Assistant ще не підключений.</p>';
    return;
  }

  const candidatesMarkup = discovery.candidates.length
    ? discovery.candidates
        .map(
          (candidate) => `
            <article class="discovery-card">
              <div class="address-head">
                <div>
                  <h3>${escapeHtml(candidate.friendly_name)}</h3>
                  <p class="entity-id">${escapeHtml(candidate.entity_prefix)}</p>
                </div>
                <span class="badge ${candidate.configured ? "good" : "neutral"}">
                  ${candidate.configured ? "налаштовано" : "доступно"}
                </span>
              </div>
              <div class="discovery-entities">
                ${Object.entries(candidate.entities)
                  .filter(([, entityId]) => entityId)
                  .map(
                    ([label, entityId]) => `
                      <p><span class="meta-label">${escapeHtml(label)}</span> ${escapeHtml(entityId)}</p>
                    `,
                  )
                  .join("")}
              </div>
              <div class="discovery-actions">
                <button
                  class="secondary-button"
                  type="button"
                  data-action="use-discovery"
                  data-prefix="${escapeHtml(candidate.entity_prefix)}"
                  data-name="${escapeHtml(candidate.friendly_name)}"
                >
                  Додати в редактор
                </button>
              </div>
            </article>
          `,
        )
        .join("")
    : '<p class="empty-state">DTEK-подібних кандидатів не знайдено.</p>';

  const catalog = discovery.catalog || {};
  const catalogMarkup = [
    renderCatalogSection(
      "Сенсори частоти",
      "Найкращі кандидати для loss_entity у режимі «Швидка втрата + напруга».",
      catalog.frequency_sensors || [],
    ),
    renderCatalogSection(
      "Сенсори напруги",
      "Базові сенсори для загальної топології напруги, статусу фаз і power-логіки.",
      catalog.voltage_sensors || [],
    ),
    renderCatalogSection(
      "DTEK binary сенсори",
      "Опорні power entity, знайдені на DTEK-пристроях.",
      catalog.binary_power || [],
    ),
    renderCatalogSection(
      "Статуси відключень",
      "Сенсори outage_status, які використовує feature emergency.",
      catalog.status_sensors || [],
    ),
    renderCatalogSection(
      "Групи графіка",
      "Основні schedule_group сенсори для підказок і перевірки прив'язки адрес.",
      catalog.schedule_group_sensors || [],
    ),
    renderCatalogSection(
      "Календарі відключень",
      "Підтверджені outage_schedule календарі, знайдені в Home Assistant.",
      catalog.outage_calendars || [],
    ),
  ]
    .filter(Boolean)
    .join("");

  discoveryList.innerHTML = `
    ${candidatesMarkup}
    ${catalogMarkup ? `<section class="catalog-grid">${catalogMarkup}</section>` : ""}
  `;
}

function renderDiagnostics() {
  diagnosticsOutput.textContent = JSON.stringify(state.diagnostics || {}, null, 2);
}

function getDraftGuidance(address) {
  const errors = [];
  const hints = [];
  const hasFrequencySensors = getCatalogItems("frequency_sensors").length > 0;
  const hasVoltageSensors = getCatalogItems("voltage_sensors").length > 0;
  const voltageCount = (address.voltage?.entities || []).length;
  const usesVoltageTopology =
    address.power?.enabled &&
    ["voltage_only", "loss_plus_voltage"].includes(address.power.mode);
  const treatsUnavailableAsMissing = address.voltage?.unavailable_as_missing !== false;

  if (!address.entity_prefix) {
    errors.push("Для роботи профілю потрібно вказати entity_prefix.");
  }
  if (!address.display_name && address.entity_prefix) {
    hints.push("display_name порожній, тому runtime спробує взяти назву з Home Assistant.");
  }
  if ((address.voltage?.enabled || usesVoltageTopology) && !voltageCount) {
    errors.push("Моніторинг напруги увімкнено, але voltage entities не вибрані.");
  }
  if (address.power?.enabled) {
    if (address.power.mode === "dtek_only") {
      hints.push("Режим лише DTEK простий, але зовнішні сенсори зазвичай реагують швидше.");
      if (hasFrequencySensors && hasVoltageSensors) {
        hints.push("У каталозі є і частота, і напруга, тому тут зазвичай найкраще підійде режим «Швидка втрата + напруга».");
      }
    }
    if (address.power.mode === "voltage_only" && !voltageCount) {
      errors.push("Режим «Лише напруга» потребує хоча б один сенсор у секції «Напруга та фази».");
    }
    if (address.power.mode === "loss_plus_voltage") {
      if (!address.power.loss_entity) {
        errors.push("Режим «Швидка втрата + напруга» потребує loss_entity.");
      }
      if (!hasFrequencySensors) {
        hints.push("У каталозі ще немає сенсорів частоти, тому список для loss_entity може бути порожнім.");
      }
      if (!hasVoltageSensors) {
        hints.push("У каталозі ще немає сенсорів напруги, тому секцію «Напруга та фази» доведеться заповнювати вручну.");
      }
    }
    if (usesVoltageTopology && voltageCount > 1) {
      hints.push("Кілька сенсорів у секції напруги активують пофазову логіку: partial для будинку і окремі phase_lost/phase_restored повідомлення.");
    }
    if (usesVoltageTopology && voltageCount === 1) {
      hints.push("Один сенсор у секції напруги означає однофазну схему без стану partial.");
    }
    if (usesVoltageTopology && !address.voltage?.enabled) {
      hints.push("Окремі сповіщення про якість напруги вимкнені, але вибрані сенсори все одно використовуються для визначення наявності живлення.");
    }
    if (usesVoltageTopology && treatsUnavailableAsMissing) {
      hints.push("Unavailable/unknown для сенсорів напруги зараз рахуються як відсутня фаза. Якщо всі фази стануть unavailable, будинок може перейти в off.");
    }
    if (usesVoltageTopology && !treatsUnavailableAsMissing) {
      hints.push("Unavailable/unknown для сенсорів напруги зараз ігноруються в power-логіці. Якщо вся топологія стане невизначеною, runtime fallback'иться на DTEK.");
    }
  } else {
    hints.push("Сповіщення про наявність світла для цієї адреси вимкнені.");
  }
  if (address.voltage?.enabled && !voltageCount) {
    errors.push("Окремі сповіщення про напругу увімкнені, але сенсори в секції «Напруга та фази» не вибрані.");
  }
  if (address.power?.enabled && voltageCount > 1 && address.power?.phase_notifications === false) {
    hints.push("Пофазова topology буде враховуватися для стану будинку, але окремі phase_lost/phase_restored повідомлення вимкнені.");
  }
  if (address.upcoming_outage?.enabled && address.upcoming_outage?.power_filter === "only_when_missing") {
    hints.push("Попередження про найближче відключення підуть лише коли будинок уже в стані off.");
  }
  if (address.status_message?.enabled && address.status_message?.delivery_mode === "send_new") {
    hints.push("Статус у режимі «нове повідомлення щоразу» може створювати багато повідомлень при частих оновленнях.");
  }
  if (address.status_message?.enabled && address.status_message?.delivery_mode === "pinned_edit" && address.status_message?.pin === false) {
    hints.push("Статус редагуватиме одне повідомлення, але не закріплюватиме його.");
  }
  if (
    address.status_message?.enabled &&
    Number(address.status_message.min_update_interval) >
      Number(address.status_message.update_interval)
  ) {
    hints.push("min_update_interval для status_message більший за update_interval, що виглядає нетипово.");
  }
  return { errors, hints };
}

function renderQuickPickButtons(index, items, options) {
  if (!items.length) {
    return '<p class="empty-state">Поки що не знайдено релевантних сенсорів.</p>';
  }

  return `
    <div class="quick-picks">
      ${items
        .slice(0, MAX_QUICK_PICKS)
        .map(
          (item) => `
            <button
              class="picker-button"
              type="button"
              data-action="${escapeHtml(options.action)}"
              data-index="${index}"
              data-entity="${escapeHtml(item.entity_id)}"
              data-label="${escapeHtml(item.friendly_name || "")}"
              data-target="${escapeHtml(options.target)}"
            >
              ${escapeHtml(formatQuickPickLabel(item))}
            </button>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderQuickPickBlock(title, copy, index, items, options) {
  return `
    <div class="helper-block">
      <p class="meta-label">${escapeHtml(title)}</p>
      <p class="helper-copy">${escapeHtml(copy)}</p>
      ${renderQuickPickButtons(index, items, options)}
    </div>
  `;
}

function renderModePresetBar(address, index) {
  const currentMode = address.power?.mode || "dtek_only";
  const isEnabled = address.power?.enabled;

  return `
    <div class="preset-bar">
      ${Object.entries(POWER_MODE_META)
        .map(
          ([mode, meta]) => `
            <button
              class="preset-button ${isEnabled && currentMode === mode ? "active" : ""}"
              type="button"
              data-action="apply-power-preset"
              data-index="${index}"
              data-preset="${escapeHtml(mode)}"
            >
              <span>${escapeHtml(meta.label)}</span>
            </button>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderModeGuidance(address) {
  const mode = address.power?.enabled ? address.power?.mode || "dtek_only" : "disabled";
  if (mode === "disabled") {
    return `
      <div class="helper-block subtle">
        <p class="meta-label">Підказка щодо режиму</p>
        <p class="helper-copy">
          Сповіщення про наявність світла зараз вимкнені. Увімкніть функцію або
          застосуйте готовий пресет нижче.
        </p>
      </div>
    `;
  }

  const meta = POWER_MODE_META[mode] || POWER_MODE_META.dtek_only;
  return `
    <div class="helper-block subtle">
      <p class="meta-label">Підказка щодо режиму</p>
      <p class="helper-copy">${escapeHtml(meta.copy)}</p>
    </div>
  `;
}

function renderEntityCollectionEditor(index, scope, title, helpText, items, selectedEntities) {
  const mergedItems = mergeSelectableItems(items, selectedEntities);

  return `
    <div class="helper-block entity-picker-block">
      <div class="entity-picker-grid">
        <label class="field">
          <span>${escapeHtml(title)}</span>
          <select class="${escapeHtml(scope)}-entity-select">
            ${renderEntityOptions(mergedItems, "", "Оберіть сенсор")}
          </select>
        </label>
        <label class="field">
          <span>Мітка</span>
          <input
            class="${escapeHtml(scope)}-entity-label"
            type="text"
            value=""
            placeholder="Необов'язково"
          />
        </label>
        <div class="entity-picker-actions">
          <button
            class="secondary-button"
            type="button"
            data-action="add-entity-item"
            data-index="${index}"
            data-scope="${escapeHtml(scope)}"
          >
            Додати
          </button>
        </div>
      </div>
      <p class="helper-copy">${escapeHtml(helpText)}</p>
      ${
        selectedEntities.length
          ? `
            <div class="${escapeHtml(scope)}-selected-entities selected-entity-list">
              ${selectedEntities
                .map((entry) => {
                  const catalogItem = getCatalogEntity(entry.entity);
                  const name = entry.label || catalogItem?.friendly_name || entry.entity;
                  const meta = [entry.entity];
                  if (entry.label && catalogItem?.friendly_name && entry.label !== catalogItem.friendly_name) {
                    meta.push(`мітка: ${entry.label}`);
                  } else if (entry.label && !catalogItem?.friendly_name) {
                    meta.push(`мітка: ${entry.label}`);
                  }

                  return `
                    <div
                      class="selected-entity-item"
                      data-entity="${escapeHtml(entry.entity)}"
                      data-label="${escapeHtml(entry.label || "")}"
                    >
                      <div>
                        <p class="entity-name">${escapeHtml(name)}</p>
                        <p class="entity-id">${escapeHtml(meta.join(" · "))}</p>
                      </div>
                      <button
                        class="chip-remove-button"
                        type="button"
                        data-action="remove-entity-item"
                        data-index="${index}"
                        data-scope="${escapeHtml(scope)}"
                        data-entity="${escapeHtml(entry.entity)}"
                      >
                        Видалити
                      </button>
                    </div>
                  `;
                })
                .join("")}
            </div>
          `
          : '<p class="empty-state">Поки що жодного сенсора не додано.</p>'
      }
    </div>
  `;
}

function renderDraftGuidance(address) {
  const guidance = getDraftGuidance(address);
  const blockers = guidance.errors.length;
  const statusClass = blockers ? "bad" : guidance.hints.length ? "warn" : "good";
  const statusLabel = blockers
    ? `${blockers} блокер${blockers === 1 ? "" : "и"}`
    : guidance.hints.length
      ? "є підказки"
      : "готово до перевірки";

  return `
    <div class="draft-guidance ${statusClass}">
      <div class="preview-head">
        <h4>Підказки для draft</h4>
        <span class="badge ${statusClass}">${escapeHtml(statusLabel)}</span>
      </div>
      ${
        guidance.errors.length
          ? `
            <div class="guidance-list">
              ${guidance.errors
                .map(
                  (item) => `
                    <div class="guidance-item error">
                      <span class="guidance-dot"></span>
                      <p>${escapeHtml(item)}</p>
                    </div>
                  `,
                )
                .join("")}
            </div>
          `
          : ""
      }
      ${
        guidance.hints.length
          ? `
            <div class="guidance-list">
              ${guidance.hints
                .map(
                  (item) => `
                    <div class="guidance-item hint">
                      <span class="guidance-dot"></span>
                      <p>${escapeHtml(item)}</p>
                    </div>
                  `,
                )
                .join("")}
            </div>
          `
          : !guidance.errors.length
            ? '<p class="helper-copy">Цей draft виглядає структурно повним. Запустіть перевірку, щоб порівняти його з живим станом HA.</p>'
            : ""
      }
    </div>
  `;
}

function resolvePreview(address, index) {
  const previews = state.configPayload?.preview?.addresses || [];
  if (previews[index]?.entity_prefix === address.entity_prefix) {
    return previews[index];
  }
  if (address.entity_prefix) {
    return previews.find((item) => item.entity_prefix === address.entity_prefix) || null;
  }
  return previews[index] || null;
}

function renderSignalCard(title, snapshot, meta = "") {
  if (!snapshot) {
    return `
      <div class="signal-card">
        <p class="meta-label">${escapeHtml(title)}</p>
        <p class="empty-state">Не налаштовано.</p>
      </div>
    `;
  }

  const extra = meta ? `<p class="signal-meta">${escapeHtml(meta)}</p>` : "";
  return `
    <div class="signal-card">
      <p class="meta-label">${escapeHtml(title)}</p>
      <p class="entity-name">${escapeHtml(snapshot.friendly_name || snapshot.entity_id)}</p>
      <p class="entity-id">${escapeHtml(snapshot.entity_id)}</p>
      <p class="signal-value">${escapeHtml(formatEntityHint(snapshot))}</p>
      ${extra}
    </div>
  `;
}

function renderSnapshotList(title, snapshots, extraBuilder) {
  if (!snapshots.length) {
    return "";
  }

  return `
    <div class="preview-list">
      <p class="meta-label">${escapeHtml(title)}</p>
      ${snapshots
        .map(
          (snapshot) => `
            <div class="preview-list-row">
              <div>
                <p class="entity-name">${escapeHtml(snapshot.friendly_name || snapshot.entity_id)}</p>
                <p class="entity-id">${escapeHtml(snapshot.entity_id)}</p>
              </div>
              <div class="preview-list-state">
                <span class="entity-state">${escapeHtml(formatEntityHint(snapshot))}</span>
                ${
                  extraBuilder
                    ? `<p class="signal-meta">${escapeHtml(extraBuilder(snapshot))}</p>`
                    : ""
                }
              </div>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderPowerPreview(address, index) {
  const preview = resolvePreview(address, index);
  if (!preview) {
    return `
      <div class="preview-card ${state.editorDirty ? "stale" : ""}">
        <div class="preview-head">
          <h4>Живе прев'ю</h4>
          <span class="badge neutral">потрібна перевірка</span>
        </div>
        <p class="preview-copy">
          Запустіть перевірку або оновлення, щоб порівняти draft з поточними станами Home Assistant.
        </p>
      </div>
    `;
  }

  const lossSnapshot = preview.signals?.loss_entity || null;
  const voltageSnapshots = preview.signals?.voltage_entities || [];
  const voltageTopology = preview.signals?.voltage_topology || {};
  const modeMeta = getPowerModeMeta(preview.mode);
  const houseStateMeta = getHouseStateMeta(preview.detected_power);
  const topologyStateMeta = getHouseStateMeta(voltageTopology.house_state);
  const topologyStateLabel = voltageTopology.house_state
    ? topologyStateMeta.label
    : "невизначено";

  const lossMeta = lossSnapshot
    ? `loss_state ${lossSnapshot.loss_state}; збіг: ${
        lossSnapshot.matches_loss_state === null
          ? "n/a"
          : lossSnapshot.matches_loss_state
            ? "так"
            : "ні"
      }`
    : "";
  const topologyMeta = [
    `фаз: ${voltageTopology.phase_count ?? 0}`,
    `активно: ${voltageTopology.present_phase_count ?? 0}`,
    `відомо: ${voltageTopology.known_phase_count ?? 0}`,
    `поріг наявності: ${voltageTopology.present_above ?? "n/a"} В`,
    `unavailable: ${voltageTopology.unavailable_as_missing === false ? "ігноруються" : "втрата фази"}`,
  ].join(" · ");

  return `
    <div class="preview-card ${state.editorDirty ? "stale" : ""}">
      <div class="preview-head">
        <h4>Живе прев'ю</h4>
        <div class="address-meta">
          <span class="badge neutral">${escapeHtml(modeMeta.label)}</span>
          <span class="badge ${houseStateMeta.tone}">
            зараз: ${escapeHtml(houseStateMeta.label)}
          </span>
        </div>
      </div>
      <p class="preview-copy">${escapeHtml(preview.reason)}</p>
      <div class="signal-grid">
        ${renderSignalCard("DTEK binary сенсор", preview.signals?.dtek_power)}
        ${renderSignalCard("Сенсор втрати", lossSnapshot, lossMeta)}
        ${
          voltageSnapshots.length
            ? `
              <div class="signal-card">
                <p class="meta-label">Топологія напруги</p>
                <p class="entity-name">${escapeHtml(topologyStateLabel)}</p>
                <p class="signal-value">${escapeHtml(topologyMeta)}</p>
                ${
                  (voltageTopology.missing_phases || []).length
                    ? `<p class="signal-meta">відсутні: ${escapeHtml(voltageTopology.missing_phases.join(", "))}</p>`
                    : ""
                }
                ${
                  (voltageTopology.unknown_phases || []).length
                    ? `<p class="signal-meta">невизначені: ${escapeHtml(voltageTopology.unknown_phases.join(", "))}</p>`
                    : ""
                }
              </div>
            `
            : renderSignalCard("Топологія напруги", null)
        }
      </div>
      ${renderSnapshotList(
        "Сенсори напруги",
        voltageSnapshots,
        (snapshot) =>
          `поріг ${snapshot.present_above} В; наявність: ${
            snapshot.available_for_power === null
              ? "ігнорується"
              : snapshot.available_for_power
                ? "так"
                : "ні"
          }${snapshot.phase_label ? ` · ${snapshot.phase_label}` : ""}`,
      )}
      <p class="preview-stale-note">Прев'ю оновлюється після перевірки, збереження або ручного оновлення.</p>
    </div>
  `;
}

function renderAddressEditor(address, index) {
  const candidate = getCandidateForPrefix(address.entity_prefix);
  const addressCandidates = getAddressCandidateOptions(address.entity_prefix, address.display_name);
  const voltageEntities = address.voltage?.entities || [];
  const frequencySensors = mergeSelectableItems(getCatalogItems("frequency_sensors"), [
    { entity: address.power?.loss_entity || "", label: "Поточне значення" },
  ]);
  const voltageSensors = mergeSelectableItems(getCatalogItems("voltage_sensors"), voltageEntities);
  const powerHints = candidate?.entities?.power
    ? `
      <div class="helper-inline">
        <span class="badge neutral">dtek</span>
        <span class="entity-id">${escapeHtml(candidate.entities.power)}</span>
      </div>
    `
    : "";
  const voltagePanelKey = getPanelStateKey(address, index, "voltage");
  const powerPanelKey = getPanelStateKey(address, index, "power");
  const advancedPanelKey = getPanelStateKey(address, index, "advanced");
  return `
    <article class="editor-card address-editor-card" data-index="${index}">
      <div class="addr-header">
        <div class="addr-title-row">
          <h3>Адреса ${index + 1}</h3>
          <div class="addr-actions">
            <button class="icon-button" type="button" data-action="export-address" data-index="${index}" title="Експортувати адресу">⇩</button>
            <button class="icon-button" type="button" data-action="import-address" data-index="${index}" title="Імпортувати з файлу">⇧</button>
            <button class="icon-button" type="button" data-action="clone-address" data-index="${index}" title="Клонувати">⧉</button>
            <button class="icon-button danger" type="button" data-action="remove-address" data-index="${index}" title="Видалити">✕</button>
          </div>
        </div>
        <div class="addr-identity-grid">
          <label class="field">
            <span>Entity prefix</span>
            <select class="addr-entity-prefix">
              <option value="">Оберіть адресу з dtek_monitor</option>
              ${addressCandidates
                .map(
                  (item) => `
                    <option
                      value="${escapeHtml(item.entity_prefix)}"
                      data-name="${escapeHtml(item.friendly_name || item.entity_prefix)}"
                      ${item.entity_prefix === (address.entity_prefix || "") ? "selected" : ""}
                    >
                      ${escapeHtml(item.friendly_name || item.entity_prefix)} · ${escapeHtml(item.entity_prefix)}
                    </option>
                  `,
                )
                .join("")}
            </select>
          </label>
          <label class="field">
            <span>Назва</span>
            <input class="addr-display-name" type="text" value="${escapeHtml(address.display_name || "")}" placeholder="Назва для Telegram" />
          </label>
          <label class="field">
            <span>Чат ID</span>
            <input class="addr-chat-id" type="text" value="${escapeHtml(address.telegram_chat_id || "")}" placeholder="Окремий чат (необов'язково)" />
          </label>
        </div>
      </div>

      <div class="addr-section">
        <p class="section-label">Сповіщення</p>
        <div class="feature-grid">
          <label class="toggle"><input class="feature-schedule-change" type="checkbox" ${address.schedule_change?.enabled ? "checked" : ""} /><span>Зміна графіка</span></label>
          <label class="toggle"><input class="feature-emergency" type="checkbox" ${address.emergency?.enabled ? "checked" : ""} /><span>Екстрені</span></label>
          <label class="toggle"><input class="feature-group-change" type="checkbox" ${address.group_change?.enabled ? "checked" : ""} /><span>Зміна групи</span></label>
          <label class="toggle"><input class="feature-upcoming-enabled" type="checkbox" ${address.upcoming_outage?.enabled ? "checked" : ""} /><span>Наближення відключення</span></label>
          <label class="toggle"><input class="feature-status-enabled" type="checkbox" ${address.status_message?.enabled ? "checked" : ""} /><span>Закріплений статус</span></label>
        </div>
      </div>

      <div
        class="subpanel voltage-panel ${isPanelCollapsed(
          address,
          index,
          "voltage",
          !(
            address.voltage?.enabled ||
            voltageEntities.length ||
            (address.power?.enabled && address.power?.mode !== "dtek_only")
          ),
        ) ? "collapsed" : ""}"
        data-panel-key="${escapeHtml(voltagePanelKey)}"
      >
        <div class="subpanel-head" data-action="toggle-section">
          <h4>Напруга та фази</h4>
        </div>
        <div class="subpanel-body">
          <div class="helper-block subtle">
            <p class="meta-label">Напруга та фази</p>
            <p class="helper-copy">
              Вибрані тут сенсори є базою для всієї внутрішньої логіки add-on. Один сенсор означає однофазну схему, два або три сенсори вмикають пофазовий стан будинку та повідомлення про partial/phase changes.
            </p>
          </div>
          <div class="voltage-topology-fields">
            ${renderEntityCollectionEditor(
              index,
              "voltage",
              "Сенсори напруги",
              "Оберіть один, два або три сенсори із каталогу. Цей набір використовується і для status/power logic, і для окремих voltage alerts.",
              voltageSensors,
              voltageEntities,
            )}
          </div>
          <div class="inline-fields voltage-topology-fields">
            <label class="field compact-field">
              <span>Є живлення вище (В)</span>
              <input class="voltage-present-above" type="number" step="0.1" value="${escapeHtml(address.voltage?.present_above ?? 50)}" />
            </label>
            <label class="toggle compact-field">
              <input class="voltage-unavailable-as-missing" type="checkbox" ${address.voltage?.unavailable_as_missing !== false ? "checked" : ""} />
              <span>Unavailable = відсутня фаза</span>
            </label>
          </div>
          <p class="helper-copy">
            Якщо опцію ввімкнено, unavailable/unknown значення можуть рахуватися як пропала фаза і впливати на power/off. Якщо вимкнено, такі значення ігноруються в power-логіці, а при повністю невизначеній топології add-on fallback'иться на DTEK.
          </p>

          <div class="helper-block">
            <label class="toggle">
              <input class="voltage-enabled" type="checkbox" ${address.voltage?.enabled ? "checked" : ""} />
              <span>Надсилати окремі сповіщення про якість напруги</span>
            </label>
            <p class="helper-copy">
              Цей чекбокс вмикає лише окремі повідомлення про низьку/високу/нормальну напругу. Самі сенсори вище все одно використовуються для power, status і пофазової логіки.
            </p>
          </div>
          <div class="inline-fields voltage-alert-only">
            <label class="field compact-field">
              <span>Мін</span>
              <input class="voltage-low" type="number" step="0.1" value="${escapeHtml(address.voltage?.low ?? 195)}" />
            </label>
            <label class="field compact-field">
              <span>Макс</span>
              <input class="voltage-high" type="number" step="0.1" value="${escapeHtml(address.voltage?.high ?? 245)}" />
            </label>
            <label class="field compact-field">
              <span>Затримка (с)</span>
              <input class="voltage-delay" type="number" step="1" value="${escapeHtml(address.voltage?.delay ?? 15)}" />
            </label>
          </div>
        </div>
      </div>

      <div
        class="subpanel ${isPanelCollapsed(address, index, "power", !address.power?.enabled) ? "collapsed" : ""}"
        data-panel-key="${escapeHtml(powerPanelKey)}"
      >
        <div class="subpanel-head" data-action="toggle-section">
          <label class="toggle"><input class="power-enabled" type="checkbox" ${address.power?.enabled ? "checked" : ""} /><span>Визначення живлення</span></label>
        </div>
        <div class="subpanel-body">
          ${renderModePresetBar(address, index)}
          ${renderModeGuidance(address)}
          ${powerHints}

          <div class="power-fields">
            <div class="inline-fields">
              <label class="field compact-field">
                <span>Режим</span>
                <select class="power-mode">
                  <option value="dtek_only" ${address.power?.mode === "dtek_only" ? "selected" : ""}>Лише DTEK</option>
                  <option value="voltage_only" ${address.power?.mode === "voltage_only" ? "selected" : ""}>Лише напруга</option>
                  <option value="loss_plus_voltage" ${address.power?.mode === "loss_plus_voltage" ? "selected" : ""}>Швидка втрата + напруга</option>
                </select>
              </label>
            </div>

            <div class="helper-block subtle voltage-mode-only power-voltage-policy">
              <label class="toggle">
                <input class="power-unavailable-as-missing" type="checkbox" ${address.voltage?.unavailable_as_missing !== false ? "checked" : ""} />
                <span>Unavailable сенсор = відсутня фаза</span>
              </label>
              <p class="helper-copy">
                Це той самий параметр, що і в секції «Напруга та фази», але тут він показаний у контексті power-логіки. Якщо ввімкнено, unavailable/unknown можуть брати участь у переході будинку в off. Якщо вимкнено, вони ігноруються, а при повністю невизначеній topology add-on fallback'иться на DTEK.
              </p>
            </div>

            <div class="power-sensor-group loss-plus-voltage-only">
              <p class="section-label">Сенсор втрати</p>
              <div class="inline-fields">
                <label class="field" style="flex:2">
                  <span>Entity</span>
                  <select class="power-loss-entity">
                    ${renderEntityOptions(
                      frequencySensors,
                      address.power?.loss_entity || "",
                      "Оберіть сенсор частоти",
                    )}
                  </select>
                </label>
                <label class="field compact-field">
                  <span>Стан втрати</span>
                  <input class="power-loss-state" type="text" value="${escapeHtml(address.power?.loss_state || "0")}" />
                </label>
                <label class="field compact-field">
                  <span>Затримка (с)</span>
                  <input class="power-loss-delay" type="number" step="1" value="${escapeHtml(address.power?.loss_delay ?? 30)}" />
                </label>
              </div>
            </div>

            <div class="power-sensor-group loss-plus-voltage-only">
              <p class="section-label">Підтвердження повного outage</p>
              <div class="inline-fields">
                <label class="field" style="flex:2">
                  <span>Логіка</span>
                  <input type="text" value="Підтвердження береться із секції «Напруга та фази»" readonly />
                </label>
                <label class="field compact-field">
                  <span>Очікування свіжих даних (с)</span>
                  <input class="power-confirm-timeout" type="number" step="1" value="${escapeHtml(address.power?.confirm_timeout ?? 15)}" />
                </label>
              </div>
              <p class="helper-copy">
                Після спрацювання loss trigger add-on чекає новий update від сенсора(ів) напруги. Якщо за цей час приходить свіже значення вище порога, це вважається хибним спрацюванням. Якщо update не приходить або напруга явно зникає, старе значення вважається stale і будинок переходить в outage.
              </p>
            </div>

            <div class="power-sensor-group voltage-mode-only">
              <p class="section-label">Повернення</p>
              <div class="inline-fields">
                <label class="field compact-field">
                  <span>Логіка</span>
                  <input type="text" value="Відновлення також визначається за секцією «Напруга та фази»" readonly />
                </label>
                <label class="field compact-field">
                  <span>Затримка (с)</span>
                  <input class="power-restore-delay" type="number" step="1" value="${escapeHtml(address.power?.restore_delay ?? 10)}" />
                </label>
              </div>
            </div>
          </div>
          ${renderDraftGuidance(address)}
          ${renderPowerPreview(address, index)}
        </div>
      </div>

      <div
        class="subpanel ${isPanelCollapsed(address, index, "advanced", true) ? "collapsed" : ""}"
        data-panel-key="${escapeHtml(advancedPanelKey)}"
      >
        <div class="subpanel-head" data-action="toggle-section">
          <h4>Додаткові параметри</h4>
        </div>
        <div class="subpanel-body">
          <div class="helper-block">
            <p class="meta-label">Тихий режим повідомлень</p>
            <p class="helper-copy">
              Керує тільки звуком/сповіщенням у Telegram. Логіка самих feature при цьому не змінюється.
            </p>
            <div class="feature-grid">
              <label class="toggle"><input class="schedule-silent" type="checkbox" ${address.schedule_change?.silent !== false ? "checked" : ""} /><span>Зміна графіка</span></label>
              <label class="toggle"><input class="emergency-silent" type="checkbox" ${address.emergency?.silent ? "checked" : ""} /><span>Екстрені</span></label>
              <label class="toggle"><input class="group-silent" type="checkbox" ${address.group_change?.silent ? "checked" : ""} /><span>Зміна групи</span></label>
              <label class="toggle"><input class="voltage-silent" type="checkbox" ${address.voltage?.silent ? "checked" : ""} /><span>Напруга</span></label>
              <label class="toggle"><input class="power-silent" type="checkbox" ${address.power?.silent ? "checked" : ""} /><span>Світло / фази</span></label>
              <label class="toggle"><input class="upcoming-silent" type="checkbox" ${address.upcoming_outage?.silent ? "checked" : ""} /><span>Найближче відключення</span></label>
              <label class="toggle"><input class="status-silent" type="checkbox" ${address.status_message?.silent !== false ? "checked" : ""} /><span>Статус</span></label>
            </div>
          </div>

          <div class="helper-block">
            <p class="meta-label">Світло та фази</p>
            <div class="feature-grid">
              <label class="toggle">
                <input class="power-phase-notifications" type="checkbox" ${address.power?.phase_notifications !== false ? "checked" : ""} />
                <span>Окремі повідомлення про фази</span>
              </label>
            </div>
          </div>

          <div class="helper-block">
            <p class="meta-label">Найближче відключення</p>
            <div class="inline-fields">
              <label class="field compact-field">
                <span>Коли надсилати</span>
                <select class="feature-upcoming-power-filter">
                  <option value="only_when_available" ${(address.upcoming_outage?.power_filter ?? "only_when_available") === "only_when_available" ? "selected" : ""}>Лише коли світло є</option>
                  <option value="only_when_missing" ${address.upcoming_outage?.power_filter === "only_when_missing" ? "selected" : ""}>Лише коли світла нема</option>
                  <option value="always" ${address.upcoming_outage?.power_filter === "always" ? "selected" : ""}>Завжди</option>
                </select>
              </label>
              <label class="field compact-field">
                <span>Хвилин до відключення</span>
                <input class="feature-upcoming-minutes" type="number" step="1" value="${escapeHtml(address.upcoming_outage?.minutes ?? 10)}" />
              </label>
            </div>
          </div>

          <div class="helper-block">
            <p class="meta-label">Статусне повідомлення</p>
            <div class="inline-fields">
              <label class="field compact-field">
                <span>Режим</span>
                <select class="feature-status-delivery-mode">
                  <option value="pinned_edit" ${(address.status_message?.delivery_mode ?? "pinned_edit") === "pinned_edit" ? "selected" : ""}>Редагувати одне повідомлення</option>
                  <option value="send_new" ${address.status_message?.delivery_mode === "send_new" ? "selected" : ""}>Нове повідомлення щоразу</option>
                </select>
              </label>
              <label class="toggle compact-field status-pinned-only">
                <input class="feature-status-pin" type="checkbox" ${address.status_message?.pin !== false ? "checked" : ""} />
                <span>Закріплювати повідомлення</span>
              </label>
              <label class="field compact-field">
                <span>Інтервал статусу (с)</span>
                <input class="feature-status-update-interval" type="number" step="1" value="${escapeHtml(address.status_message?.update_interval ?? 300)}" />
              </label>
              <label class="field compact-field">
                <span>Мін. інтервал (с)</span>
                <input class="feature-status-min-update-interval" type="number" step="1" value="${escapeHtml(address.status_message?.min_update_interval ?? 10)}" />
              </label>
            </div>
          </div>

          <div class="inline-fields">
            <p class="helper-copy">
              Тут зібрані саме параметри доставки й поведінки повідомлень. Основні сенсори та логіка залишаються в секціях вище.
            </p>
          </div>
        </div>
      </div>
    </article>
  `;
}

function renderEditor(force = false) {
  if (!state.configPayload) {
    editorContainer.innerHTML = '<p class="empty-state">Редактор ще не готовий.</p>';
    return;
  }
  if (state.editorDirty && !force && editorContainer.children.length) {
    editorStatus.className = `status-message ${state.editorStatus.tone}`;
    editorStatus.textContent = state.editorStatus.message;
    return;
  }

  const config = state.draftConfig || cloneJson(state.configPayload.config);
  const options = state.configPayload.options;
  const previewTime = state.configPayload.preview?.generated_at
    ? new Date(state.configPayload.preview.generated_at).toLocaleTimeString(UI_LOCALE)
    : null;
  const addresses = config.addresses.length ? config.addresses : [createBlankAddress()];

  editorContainer.innerHTML = `
    <section class="editor-card">
      <div class="field-grid">
        <label class="field">
          <span>Чат за замовчуванням</span>
          <input type="text" value="${escapeHtml(options.default_chat_id || "не вказано")}" readonly />
        </label>
        <label class="field">
          <span>Конфіг add-on</span>
          <input type="text" value="${escapeHtml(state.configPayload.config_path)}" readonly />
        </label>
      </div>
      <div class="field-grid">
        <label class="field field-wide">
          <span>Резервний HA token</span>
          <input id="cfg-ha-token" type="text" value="${escapeHtml(config.ha_token || "")}" />
        </label>
      </div>
      <p class="helper-copy">
        Живе прев'ю ${previewTime ? `востаннє оновлено о ${escapeHtml(previewTime)}.` : "ще недоступне."}
      </p>
    </section>
    ${addresses.map((address, index) => renderAddressEditor(address, index)).join("")}
  `;

  syncAllEditorVisibility();
  editorStatus.className = `status-message ${state.editorStatus.tone}`;
  editorStatus.textContent = state.editorStatus.message;
}

function collectSelectedEntityItems(form, selector) {
  return Array.from(form.querySelectorAll(selector))
    .map((node) => ({
      entity: node.dataset.entity?.trim() || "",
      label: node.dataset.label?.trim() || "",
    }))
    .filter((item) => item.entity);
}

function collectEditorConfig() {
  const currentConfig = state.draftConfig || state.configPayload?.config || {};
  const config = {
    ha_token: document.getElementById("cfg-ha-token")?.value.trim() ?? "",
    export_default_templates: currentConfig.export_default_templates ?? false,
    addresses: [],
  };

  const addressForms = editorContainer.querySelectorAll(".address-editor-card");
  for (const form of addressForms) {
    config.addresses.push({
      entity_prefix: form.querySelector(".addr-entity-prefix").value.trim(),
      display_name: form.querySelector(".addr-display-name").value.trim(),
      telegram_chat_id: form.querySelector(".addr-chat-id").value.trim(),
      schedule_change: {
        enabled: form.querySelector(".feature-schedule-change").checked,
        silent: form.querySelector(".schedule-silent").checked,
      },
      emergency: {
        enabled: form.querySelector(".feature-emergency").checked,
        silent: form.querySelector(".emergency-silent").checked,
      },
      group_change: {
        enabled: form.querySelector(".feature-group-change").checked,
        silent: form.querySelector(".group-silent").checked,
      },
      voltage: {
        enabled: form.querySelector(".voltage-enabled").checked,
        entities: collectSelectedEntityItems(form, ".voltage-selected-entities .selected-entity-item"),
        present_above: toNumber(form.querySelector(".voltage-present-above").value, 50),
        unavailable_as_missing: (
          form.querySelector(".power-unavailable-as-missing")
          || form.querySelector(".voltage-unavailable-as-missing")
        ).checked,
        low: toNumber(form.querySelector(".voltage-low").value, 195),
        high: toNumber(form.querySelector(".voltage-high").value, 245),
        delay: toNumber(form.querySelector(".voltage-delay").value, 15, Number.parseInt),
        silent: form.querySelector(".voltage-silent").checked,
      },
      power: {
        enabled: form.querySelector(".power-enabled").checked,
        mode: form.querySelector(".power-mode").value,
        loss_entity: form.querySelector(".power-loss-entity").value.trim(),
        loss_state: form.querySelector(".power-loss-state").value.trim(),
        loss_delay: toNumber(form.querySelector(".power-loss-delay").value, 30, Number.parseInt),
        confirm_timeout: toNumber(form.querySelector(".power-confirm-timeout").value, 15, Number.parseInt),
        restore_delay: toNumber(form.querySelector(".power-restore-delay").value, 10, Number.parseInt),
        silent: form.querySelector(".power-silent").checked,
        phase_notifications: form.querySelector(".power-phase-notifications").checked,
      },
      upcoming_outage: {
        enabled: form.querySelector(".feature-upcoming-enabled").checked,
        minutes: toNumber(form.querySelector(".feature-upcoming-minutes").value, 10, Number.parseInt),
        silent: form.querySelector(".upcoming-silent").checked,
        power_filter: form.querySelector(".feature-upcoming-power-filter").value,
      },
      status_message: {
        enabled: form.querySelector(".feature-status-enabled").checked,
        update_interval: toNumber(
          form.querySelector(".feature-status-update-interval").value,
          300,
          Number.parseInt,
        ),
        min_update_interval: toNumber(
          form.querySelector(".feature-status-min-update-interval").value,
          10,
          Number.parseInt,
        ),
        delivery_mode: form.querySelector(".feature-status-delivery-mode").value,
        pin: form.querySelector(".feature-status-pin").checked,
        silent: form.querySelector(".status-silent").checked,
      },
    });
  }

  return config;
}

function syncAddressFormVisibility(form) {
  const powerEnabled = form.querySelector(".power-enabled").checked;
  const mode = form.querySelector(".power-mode").value;
  const voltageEnabled = form.querySelector(".voltage-enabled").checked;
  const statusEnabled = form.querySelector(".feature-status-enabled").checked;
  const statusDeliveryMode = form.querySelector(".feature-status-delivery-mode").value;
  const usesVoltageTopology = powerEnabled && mode !== "dtek_only";
  const lossPlusVoltageMode = powerEnabled && mode === "loss_plus_voltage";
  const hasVoltageEntities =
    form.querySelectorAll(".voltage-selected-entities .selected-entity-item").length > 0;

  form.querySelectorAll(".power-fields").forEach((node) => {
    node.classList.toggle("hidden", !powerEnabled);
  });
  form.querySelectorAll(".voltage-mode-only").forEach((node) => {
    node.classList.toggle("hidden", !usesVoltageTopology);
  });
  form.querySelectorAll(".loss-plus-voltage-only").forEach((node) => {
    node.classList.toggle("hidden", !lossPlusVoltageMode);
  });
  form.querySelectorAll(".voltage-alert-only").forEach((node) => {
    node.classList.toggle("hidden", !voltageEnabled);
  });
  form.querySelectorAll(".status-pinned-only").forEach((node) => {
    node.classList.toggle(
      "hidden",
      !statusEnabled || statusDeliveryMode !== "pinned_edit",
    );
  });

  const voltagePanel = form.querySelector(".voltage-panel");
  if (voltagePanel && (voltageEnabled || usesVoltageTopology || hasVoltageEntities)) {
    voltagePanel.classList.remove("collapsed");
    if (voltagePanel.dataset.panelKey) {
      rememberPanelState(voltagePanel.dataset.panelKey, false);
    }
  }
  const powerPanel = form.querySelector(".power-enabled")?.closest(".subpanel");
  if (powerPanel && powerEnabled) {
    powerPanel.classList.remove("collapsed");
    if (powerPanel.dataset.panelKey) {
      rememberPanelState(powerPanel.dataset.panelKey, false);
    }
  }
}

function syncAllEditorVisibility() {
  editorContainer
    .querySelectorAll(".address-editor-card")
    .forEach((form) => syncAddressFormVisibility(form));
}

function setDraftConfig(config, message, tone = "muted") {
  state.draftConfig = config;
  state.editorDirty = true;
  state.templatePreview = null;
  renderEditor(true);
  renderTemplates(true);
  setEditorStatus(message, tone);
}

function updateEntityCollection(target, operation) {
  const config = collectEditorConfig();
  const index = Number.parseInt(target.dataset.index ?? "-1", 10);
  const scope = target.dataset.scope || "";
  const address = config.addresses[index];
  if (!address) {
    return;
  }

  const collection = scope === "voltage" ? address.voltage.entities : null;
  if (!collection) {
    return;
  }

  if (operation === "add") {
    const form = target.closest(".address-editor-card");
    const entitySelect = form?.querySelector(`.${scope}-entity-select`);
    const labelInput = form?.querySelector(`.${scope}-entity-label`);
    const entityId = entitySelect?.value.trim() || "";
    const label = labelInput?.value.trim() || "";

    if (!entityId) {
      setEditorStatus("Спочатку оберіть сенсор зі списку.", "status-warn");
      return;
    }

    if (!collection.some((item) => item.entity === entityId)) {
      collection.push({ entity: entityId, label });
    }
    setDraftConfig(
      config,
      "Список сенсорів оновлено. Запустіть перевірку, щоб оновити прев'ю.",
      "muted",
    );
    return;
  }

  if (operation === "remove") {
    const entityId = target.dataset.entity || "";
    const nextItems = collection.filter((item) => item.entity !== entityId);

    if (scope === "voltage") {
      address.voltage.entities = nextItems;
    }

    setDraftConfig(
      config,
      "Сенсор видалено з чернетки. Запустіть перевірку, щоб оновити прев'ю.",
      "muted",
    );
  }
}

function applyQuickFill(target) {
  const config = collectEditorConfig();
  const index = Number.parseInt(target.dataset.index ?? "-1", 10);
  const address = config.addresses[index];
  if (!address) {
    return;
  }
  const entity = target.dataset.entity || "";
  const label = target.dataset.label || "";
  const selector = target.dataset.target || "";

  if (selector === ".voltage-entities") {
    if (!(address.voltage.entities || []).some((item) => item.entity === entity)) {
      address.voltage.entities = [...(address.voltage.entities || []), { entity, label }];
    }
  } else if (selector === ".power-loss-entity") {
    address.power.loss_entity = entity;
    address.power.enabled = true;
    if (address.power.mode === "dtek_only") {
      address.power.mode = "loss_plus_voltage";
    }
  }

  setDraftConfig(
    config,
    "Чернетку оновлено з каталогу сенсорів. Запустіть перевірку, щоб оновити прев'ю.",
    "muted",
  );
}

function applyPowerPreset(target) {
  const config = collectEditorConfig();
  const index = Number.parseInt(target.dataset.index ?? "-1", 10);
  const preset = target.dataset.preset || "dtek_only";
  const address = config.addresses[index];
  if (!address) {
    return;
  }

  address.power.enabled = true;
  address.power.mode = preset;
  address.power.loss_state = address.power.loss_state || "0";
  address.power.loss_delay = Number.isFinite(Number(address.power.loss_delay))
    ? address.power.loss_delay
    : 30;
  address.power.confirm_timeout = Number.isFinite(Number(address.power.confirm_timeout))
    ? address.power.confirm_timeout
    : 15;
  address.power.restore_delay = Number.isFinite(Number(address.power.restore_delay))
    ? address.power.restore_delay
    : 10;

  if (preset === "loss_plus_voltage") {
    address.power.loss_entity = chooseSuggestedEntity(
      "frequency_sensors",
      address,
      address.power.loss_entity,
    );
  }
  if (preset === "voltage_only" || preset === "loss_plus_voltage") {
    const suggested = getSuggestedVoltageEntities(address, address.voltage.entities || []);
    if (suggested.length) {
      address.voltage.entities = suggested;
    }
  }

  setDraftConfig(
    config,
    `Застосовано пресет «${POWER_MODE_META[preset]?.label || preset}». Запустіть перевірку, щоб оновити прев'ю.`,
    "status-good",
  );
}

async function validateConfig() {
  const config = collectEditorConfig();
  const result = await fetchJson("./api/config/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config }),
  });
  state.configPayload = { ...state.configPayload, config: result.config, preview: result.preview };
  state.draftConfig = cloneJson(result.config);
  state.editorDirty = false;
  renderEditor(true);
  renderTemplates(true);
  setEditorStatus("Конфіг валідний. Живе прев'ю оновлено з Home Assistant.", "status-good");
}

async function saveConfig({ restart }) {
  const config = collectEditorConfig();
  const result = await fetchJson("./api/config/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ config, restart }),
  });
  state.configPayload = {
    ...state.configPayload,
    config: result.config,
    preview: result.preview,
  };
  state.draftConfig = cloneJson(result.config);
  state.editorDirty = false;
  renderEditor(true);
  renderTemplates(true);
  if (restart) {
    setEditorStatus("Конфіг збережено. Перезапуск add-on уже запланований.", "status-warn");
  } else {
    setEditorStatus(
      `Конфіг збережено в ${result.path}. Щоб застосувати зміни, add-on все ще треба перезапустити.`,
      "status-good",
    );
  }
}

function getTemplateList() {
  return state.templatesPayload?.templates || [];
}

function ensureTemplateSelection() {
  const templates = getTemplateList();
  const config = getDraftConfig();
  if (!state.templateSelection.templateName && templates.length) {
    state.templateSelection.templateName = templates[0].name;
  }
  if (!templates.some((item) => item.name === state.templateSelection.templateName) && templates.length) {
    state.templateSelection.templateName = templates[0].name;
  }
  if (state.templateSelection.addressIndex >= (config.addresses?.length || 0)) {
    state.templateSelection.addressIndex = 0;
  }
}

function getSelectedTemplateMeta() {
  return getTemplateList().find((item) => item.name === state.templateSelection.templateName) || null;
}

function syncTemplateSource(force = false) {
  const meta = getSelectedTemplateMeta();
  if (!meta) {
    state.templateSourceDraft = "";
    state.templateSourceDirty = false;
    state.templateSourceName = "";
    return;
  }
  if (force || !state.templateSourceDirty || state.templateSourceName !== meta.name) {
    state.templateSourceDraft = meta.source || "";
    state.templateSourceDirty = false;
    state.templateSourceName = meta.name;
  }
}

function renderTemplatePreviewPanel() {
  const preview = state.templatePreview;
  if (!preview) {
    return `
      <section class="editor-card">
        <div class="panel-head">
          <h3>Згенероване прев'ю</h3>
          <p>Оберіть адресу та шаблон, а потім побудуйте прев'ю або надішліть тест.</p>
        </div>
        <pre class="template-preview">Прев'ю ще не побудовано.</pre>
      </section>
      <section class="editor-card">
        <div class="panel-head">
          <h3>Контекст</h3>
          <p>Тут з'являться змінні, які передаються в Jinja-шаблон.</p>
        </div>
        <pre class="template-preview">{}</pre>
      </section>
    `;
  }

  return `
    <section class="editor-card">
      <div class="panel-head">
        <h3>Згенероване прев'ю</h3>
        <p>${escapeHtml(preview.address.display_name || preview.address.entity_prefix || "Адреса")}</p>
      </div>
      <pre class="template-preview">${escapeHtml(preview.rendered)}</pre>
    </section>
    <section class="editor-card">
      <div class="panel-head">
        <h3>Контекст</h3>
        <p>Runtime-подібні дані, використані для поточного прев'ю.</p>
      </div>
      <pre class="template-preview">${escapeHtml(JSON.stringify(preview.context, null, 2))}</pre>
    </section>
  `;
}

function renderTemplates(force = false) {
  if (!state.templatesPayload) {
    templatesContainer.innerHTML = '<p class="empty-state">Шаблони ще не готові.</p>';
    return;
  }
  if (state.templateSourceDirty && !force && templatesContainer.children.length) {
    templateStatus.className = `status-message ${state.templateStatus.tone}`;
    templateStatus.textContent = state.templateStatus.message;
    return;
  }

  const config = getDraftConfig();
  ensureTemplateSelection();
  syncTemplateSource(force);

  const addresses = config.addresses || [];
  const selectedMeta = getSelectedTemplateMeta();
  const previewLabel = selectedMeta
    ? `${selectedMeta.group} · ${selectedMeta.origin === "override" ? "змінений" : "стандартний"}`
    : "Шаблон не вибрано";

  templatesContainer.innerHTML = `
    <div class="template-shell">
      <section class="editor-card">
        <div class="field-grid template-toolbar-grid">
          <label class="field">
            <span>Адреса</span>
            <select id="template-address-select">
              ${addresses
                .map(
                  (address, index) => `
                    <option value="${index}" ${index === state.templateSelection.addressIndex ? "selected" : ""}>
                      ${escapeHtml(address.display_name || address.entity_prefix || `Адреса ${index + 1}`)}
                    </option>
                  `,
                )
                .join("")}
            </select>
          </label>
          <label class="field">
            <span>Шаблон</span>
            <select id="template-select">
              ${getTemplateList()
                .map(
                  (item) => `
                    <option value="${escapeHtml(item.name)}" ${item.name === state.templateSelection.templateName ? "selected" : ""}>
                      ${escapeHtml(item.group)} · ${escapeHtml(item.name)}
                    </option>
                  `,
                )
                .join("")}
            </select>
          </label>
        </div>
        <div class="editor-toolbar">
          <button class="secondary-button" type="button" data-action="template-preview">Побудувати прев'ю</button>
          <button class="secondary-button" type="button" data-action="template-send-test">Надіслати тест</button>
          <button class="secondary-button" type="button" data-action="template-save">Зберегти шаблон</button>
          <button class="secondary-button" type="button" data-action="template-reset">Повернути стандартний</button>
        </div>
        <div class="template-meta-bar">
          <span class="badge neutral">${escapeHtml(previewLabel)}</span>
          <span class="template-meta-copy">Зміни зберігаються всередині add-on. Окремий експорт у файли не потрібен.</span>
        </div>
      </section>

      <div class="template-layout">
        <section class="editor-card">
          <div class="panel-head">
            <h3>Текст шаблону</h3>
            <p>
              Редагуйте Jinja-шаблон тут. Прев'ю і тестове повідомлення
              використовують поточний текст ще до збереження.
            </p>
          </div>
          <textarea id="template-source" class="template-source">${escapeHtml(state.templateSourceDraft)}</textarea>
        </section>

        <div class="side-stack">
          ${renderTemplatePreviewPanel()}
        </div>
      </div>
    </div>
  `;

  templateStatus.className = `status-message ${state.templateStatus.tone}`;
  templateStatus.textContent = state.templateStatus.message;
}

async function previewSelectedTemplate({ sendTest = false } = {}) {
  const config = getDraftConfig();
  ensureTemplateSelection();
  const body = {
    config,
    template_name: state.templateSelection.templateName,
    address_index: state.templateSelection.addressIndex,
    source_override: state.templateSourceDraft,
  };
  const url = sendTest ? "./api/templates/send-test" : "./api/templates/preview";
  const result = await fetchJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  state.templatePreview = result.preview;
  renderTemplates(true);
  if (sendTest) {
    const sent = result.preview.sent;
    if (sent?.ok) {
      setTemplateStatus(`Тестове повідомлення надіслано в ${sent.chat_id}.`, "status-good");
    } else {
      setTemplateStatus("Прев'ю побудовано, але надсилання в Telegram завершилося помилкою.", "status-bad");
    }
  } else {
    setTemplateStatus("Прев'ю шаблону оновлено з поточного draft-джерела.", "status-good");
  }
}

async function saveTemplateOverride() {
  ensureTemplateSelection();
  const result = await fetchJson("./api/templates/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      template_name: state.templateSelection.templateName,
      source: state.templateSourceDraft,
    }),
  });
  await loadTemplatesSnapshot();
  state.templateSourceDraft = result.template.source;
  state.templateSourceDirty = false;
  state.templateSourceName = result.template.name;
  renderTemplates(true);
  setTemplateStatus(`Override для ${result.template.name} збережено.`, "status-good");
}

async function resetTemplateOverride() {
  ensureTemplateSelection();
  const result = await fetchJson("./api/templates/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      template_name: state.templateSelection.templateName,
    }),
  });
  await loadTemplatesSnapshot();
  state.templateSourceDraft = result.template.source;
  state.templateSourceDirty = false;
  state.templateSourceName = result.template.name;
  state.templatePreview = null;
  renderTemplates(true);
  setTemplateStatus(`Шаблон ${result.template.name} скинуто до вбудованого джерела.`, "status-good");
}

async function loadTemplatesSnapshot() {
  state.templatesPayload = await fetchJson("./api/templates");
}

async function loadDashboard({ background = false } = {}) {
  if (!background) {
    refreshButton.disabled = true;
    refreshButton.textContent = "Оновлення…";
  }

  try {
    const [overview, discovery, diagnostics, config, templates] = await Promise.all([
      fetchJson("./api/overview"),
      fetchJson("./api/discovery"),
      fetchJson("./api/diagnostics"),
      fetchJson("./api/config"),
      fetchJson("./api/templates"),
    ]);

    state.overview = overview;
    state.discovery = discovery;
    state.diagnostics = diagnostics;
    state.configPayload = config;
    state.templatesPayload = templates;

    if (!state.editorDirty || !state.draftConfig) {
      state.draftConfig = cloneJson(config.config);
    }

    const blockInteractiveRefresh = background && isInteractiveRefreshBlocked();

    renderStats();
    renderAddresses();
    renderDiscovery();
    renderDiagnostics();
    if (!blockInteractiveRefresh || state.activeTab !== "configure") {
      renderEditor(false);
    }
    if (!blockInteractiveRefresh || state.activeTab !== "templates") {
      renderTemplates(false);
    }
    setActiveTab(state.activeTab);

    lastUpdated.textContent = `Оновлено о ${new Date().toLocaleTimeString(UI_LOCALE)}`;
    if (!state.editorDirty) {
      setEditorStatus("Редактор готовий.", "muted");
    }
    if (!state.templateSourceDirty) {
      setTemplateStatus("Шаблони готові.", "muted");
    }
  } catch (error) {
    diagnosticsOutput.textContent = `Не вдалося завантажити dashboard:\n${error}`;
    lastUpdated.textContent = "Помилка оновлення";
    setEditorStatus(String(error), "status-bad");
    setTemplateStatus(String(error), "status-bad");
  } finally {
    if (!background) {
      refreshButton.disabled = false;
      refreshButton.textContent = "Оновити";
    }
  }
}

refreshButton.addEventListener("click", () => {
  void loadDashboard();
});

addAddressButton.addEventListener("click", () => {
  const config = getDraftConfig();
  config.addresses.push(createBlankAddress());
  setDraftConfig(
    config,
    "Додано порожню адресу в чернетку. Після редагування запустіть перевірку, щоб оновити прев'ю.",
    "muted",
  );
});

importConfigButton.addEventListener("click", () => {
  configImportInput.value = "";
  configImportInput.click();
});

exportConfigButton.addEventListener("click", () => {
  exportConfig();
});

importAddressButton.addEventListener("click", () => {
  triggerAddressImport(null);
});

configImportInput.addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLInputElement)) {
    return;
  }
  const [file] = target.files || [];
  void handleConfigImportFile(file).finally(() => {
    target.value = "";
  });
});

addressImportInput.addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLInputElement)) {
    return;
  }
  const [file] = target.files || [];
  void handleAddressImportFile(file).finally(() => {
    state.pendingAddressImportIndex = null;
    target.value = "";
  });
});

validateButton.addEventListener("click", () => {
  void validateConfig().catch((error) => {
    setEditorStatus(`Помилка перевірки: ${error.message}`, "status-bad");
  });
});

saveButton.addEventListener("click", () => {
  void saveConfig({ restart: false }).catch((error) => {
    setEditorStatus(`Помилка збереження: ${error.message}`, "status-bad");
  });
});

saveRestartButton.addEventListener("click", () => {
  void saveConfig({ restart: true }).catch((error) => {
    setEditorStatus(`Помилка збереження: ${error.message}`, "status-bad");
  });
});

tabButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setActiveTab(button.dataset.tab || "configure");
  });
});

editorContainer.addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }
  const form = target.closest(".address-editor-card");
  if (form) {
    if (target.classList.contains("addr-entity-prefix") && target instanceof HTMLSelectElement) {
      const displayNameInput = form.querySelector(".addr-display-name");
      const selectedOption = target.selectedOptions?.[0] || null;
      const suggestedName = selectedOption?.dataset.name || "";
      if (displayNameInput instanceof HTMLInputElement && !displayNameInput.value.trim() && suggestedName) {
        displayNameInput.value = suggestedName;
      }
    }
    if (target.classList.contains("voltage-unavailable-as-missing") || target.classList.contains("power-unavailable-as-missing")) {
      const checked = target instanceof HTMLInputElement ? target.checked : false;
      const voltageCheckbox = form.querySelector(".voltage-unavailable-as-missing");
      const powerCheckbox = form.querySelector(".power-unavailable-as-missing");
      if (voltageCheckbox instanceof HTMLInputElement) {
        voltageCheckbox.checked = checked;
      }
      if (powerCheckbox instanceof HTMLInputElement) {
        powerCheckbox.checked = checked;
      }
    }
    state.editorDirty = true;
    syncAddressFormVisibility(form);
    setEditorStatus("Чернетку змінено. Запустіть перевірку, щоб оновити живе прев'ю.", "muted");
  }
});

editorContainer.addEventListener("input", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }
  if (target.closest(".address-editor-card") || target.id === "cfg-ha-token") {
    state.editorDirty = true;
  }
});

templatesContainer.addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }
  if (target.id === "template-address-select") {
    state.templateSelection.addressIndex = Number.parseInt(target.value, 10) || 0;
    state.templatePreview = null;
    renderTemplates(true);
    setTemplateStatus("Адресу шаблону змінено. Побудуйте прев'ю знову, щоб використати поточний draft-конфіг.", "muted");
  }
  if (target.id === "template-select") {
    state.templateSelection.templateName = target.value;
    state.templatePreview = null;
    state.templateSourceDirty = false;
    renderTemplates(true);
    setTemplateStatus("Шаблон змінено. Відредагуйте джерело або побудуйте прев'ю.", "muted");
  }
});

templatesContainer.addEventListener("input", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }
  if (target.id === "template-source") {
    state.templateSourceDraft = target.value;
    state.templateSourceDirty = true;
    state.templatePreview = null;
    setTemplateStatus("Джерело шаблону змінено. Побудуйте прев'ю, щоб рендерити поточний draft.", "muted");
  }
});

document.addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }

  const actionEl = target.closest("[data-action]");
  const action = actionEl?.dataset.action;
  if (!action) {
    return;
  }

  if (action === "toggle-section") {
    if (target.closest(".toggle")) {
      return;
    }
    const subpanel = actionEl.closest(".subpanel");
    if (subpanel) {
      subpanel.classList.toggle("collapsed");
      if (subpanel.dataset.panelKey) {
        rememberPanelState(
          subpanel.dataset.panelKey,
          subpanel.classList.contains("collapsed"),
        );
      }
    }
    return;
  }

  if (action === "fill-field" || action === "append-entity-line") {
    applyQuickFill(actionEl);
    return;
  }
  if (action === "add-entity-item") {
    updateEntityCollection(actionEl, "add");
    return;
  }
  if (action === "remove-entity-item") {
    updateEntityCollection(actionEl, "remove");
    return;
  }
  if (action === "apply-power-preset") {
    applyPowerPreset(actionEl);
    return;
  }
  if (action === "template-preview") {
    void previewSelectedTemplate().catch((error) => {
      setTemplateStatus(`Помилка прев'ю: ${error.message}`, "status-bad");
    });
    return;
  }
  if (action === "template-send-test") {
    void previewSelectedTemplate({ sendTest: true }).catch((error) => {
      setTemplateStatus(`Помилка надсилання: ${error.message}`, "status-bad");
    });
    return;
  }
  if (action === "template-save") {
    void saveTemplateOverride().catch((error) => {
      setTemplateStatus(`Помилка збереження: ${error.message}`, "status-bad");
    });
    return;
  }
  if (action === "template-reset") {
    void resetTemplateOverride().catch((error) => {
      setTemplateStatus(`Помилка скидання: ${error.message}`, "status-bad");
    });
    return;
  }

  const config = getDraftConfig();
  const index = Number.parseInt(actionEl.dataset.index ?? "-1", 10);

  if (action === "remove-address" && index >= 0) {
    config.addresses.splice(index, 1);
    setDraftConfig(config, "Адресу видалено з чернетки. Запустіть перевірку, щоб перебудувати прев'ю.", "muted");
    return;
  }
  if (action === "export-address" && index >= 0) {
    exportAddress(index);
    return;
  }
  if (action === "import-address" && index >= 0) {
    triggerAddressImport(index);
    return;
  }
  if (action === "clone-address" && index >= 0) {
    config.addresses.splice(index + 1, 0, cloneJson(config.addresses[index]));
    setDraftConfig(config, "Адресу клоновано в чернетці. Запустіть перевірку, щоб перебудувати прев'ю.", "muted");
    return;
  }
  if (action === "use-discovery") {
    const prefix = actionEl.dataset.prefix ?? "";
    const name = actionEl.dataset.name ?? "";
    const exists = config.addresses.some((address) => address.entity_prefix === prefix);
    if (exists) {
      setEditorStatus(`${prefix} уже є в чернетці.`, "status-warn");
      setActiveTab("configure");
      return;
    }
    config.addresses.push(
      createBlankAddress({
        entity_prefix: prefix,
        display_name: name,
        friendly_name: name,
      }),
    );
    setActiveTab("configure");
    setDraftConfig(
      config,
      `Префікс ${prefix} додано в чернетку з пошуку. Далі оберіть сенсори зі списків і запустіть перевірку.`,
      "status-good",
    );
  }
});

setActiveTab(state.activeTab);
void loadDashboard();
window.setInterval(() => {
  void loadDashboard({ background: true });
}, REFRESH_INTERVAL_MS);
