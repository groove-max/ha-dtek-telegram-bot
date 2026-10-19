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
const UI_STORAGE_PREFIX = `dtek-telegram-bot-ui:${new URL(".", window.location.href).pathname}`;
const ACTIVE_TAB_STORAGE_KEY = `${UI_STORAGE_PREFIX}:active-tab`;
const EDITOR_DRAFT_STORAGE_KEY = `${UI_STORAGE_PREFIX}:editor-draft`;
const TEMPLATE_DRAFT_STORAGE_KEY = `${UI_STORAGE_PREFIX}:template-draft`;
const TONE_LABELS = {
  good: "норма",
  warn: "увага",
  bad: "помилка",
  neutral: "інфо",
};
const HOUSE_STATE_META = {
  unknown: { label: "стан невідомий", tone: "neutral" },
  on: { label: "є світло", tone: "good" },
  partial: { label: "частково", tone: "warn" },
  off: { label: "нема світла", tone: "bad" },
};
const POWER_MODE_META = {
  sources: {
    label: "Датчики Home Assistant",
    copy: "Оберіть готовий статус живлення або вимірювання на вході міської мережі. Додаткове підтвердження — за бажанням. Напруга для повідомлень налаштовується окремо.",
  },
  dtek_only: {
    label: "Лише DTEK",
    copy: "Використовує лише binary_sensor.<prefix>_power. Найпростіше налаштування, але воно може відставати від зовнішніх сенсорів.",
  },
  voltage_only: {
    label: "Лише напруга",
    copy: "Достатньо одного датчика напруги на вході мережі. Для підтвердження відключення датчик і зв’язок мають працювати від резервного живлення та передавати 0 В. Зникнення або застарілі дані дають невідомий стан у статусі; окремі сповіщення про дані вмикаються за бажанням.",
  },
  loss_plus_voltage: {
    label: "Швидка втрата + напруга",
    copy: "Додатковий датчик на тій самій лінії має працювати при відключенні та явно повідомляти про втрату мережі. Після підтвердження він дозволяє відхилити стару напругу. Відновлення потребує свіжої напруги; недоступність датчика сама по собі не підтверджує відключення.",
  },
};

const state = {
  activeTab: "configure",
  activeProfile: -1,
  wizard: null,
  detailsOpen: {},
  profileSteps: {},
  showAll: false,
  botTarget: null,
  overview: null,
  discovery: null,
  diagnostics: null,
  configPayload: null,
  templatesPayload: null,
  draftConfig: null,
  editorDirty: false,
  editorRevision: 0,
  validatedPreview: null,
  baseRevision: null,
  configBusy: false,
  templateBusy: false,
  botBusy: false,
  botDirty: false,
  botEditingId: "",
  botRevision: null,
  dashboardLoading: false,
  writeEpoch: 0,
  previewRequest: 0,
  templateDrafts: {},
  templateBaseSource: "",
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
    addressPrefix: "",
    templateName: "",
  },
  templateSourceDraft: "",
  templateSourceDirty: false,
  templateSourceName: "",
  templatePreview: null,
  collapsedPanels: {},
  pendingAddressImportIndex: null,
};

function readSessionValue(key) {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSessionValue(key, value) {
  try {
    if (value === null || value === undefined || value === "") {
      window.sessionStorage.removeItem(key);
      return;
    }
    window.sessionStorage.setItem(key, String(value));
  } catch {
    // Ignore storage failures and keep the UI usable.
  }
}

function readSessionJson(key) {
  const raw = readSessionValue(key);
  if (!raw) {
    return null;
  }
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function writeSessionJson(key, value) {
  try {
    window.sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Ignore storage failures and keep the UI usable.
  }
}

function removeSessionValue(key) {
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    // Ignore storage failures and keep the UI usable.
  }
}

function persistEditorDraftSnapshot(config = null) {
  let draft = config;
  if (!draft) {
    try {
      draft = collectEditorConfig();
    } catch {
      draft = state.draftConfig;
    }
  }
  if (!draft) {
    return;
  }
  state.draftConfig = cloneJson(draft);
  writeSessionJson(EDITOR_DRAFT_STORAGE_KEY, state.draftConfig);
  writeSessionValue(`${EDITOR_DRAFT_STORAGE_KEY}:revision`, state.baseRevision);
}

function clearEditorDraftSnapshot() {
  removeSessionValue(EDITOR_DRAFT_STORAGE_KEY);
  removeSessionValue(`${EDITOR_DRAFT_STORAGE_KEY}:revision`);
}

function rememberTemplateDraft() {
  if (state.templateSourceName) {
    state.templateDrafts[state.templateSourceName] = {
      source: state.templateSourceDraft, dirty: state.templateSourceDirty,
      baseSource: state.templateBaseSource,
    };
  }
}

function persistTemplateDraftSnapshot() {
  rememberTemplateDraft();
  writeSessionJson(TEMPLATE_DRAFT_STORAGE_KEY, {
    drafts: state.templateDrafts,
    templateSelection: { ...state.templateSelection },
    templateSourceDraft: state.templateSourceDraft,
    templateSourceDirty: state.templateSourceDirty,
    templateSourceName: state.templateSourceName,
  });
}

function restorePersistedUiState() {
  state.profileSteps = readSessionJson(`${UI_STORAGE_PREFIX}:steps`) || {};
  state.showAll = readSessionValue(`${UI_STORAGE_PREFIX}:show-all`) === "true";
  const savedTab = readSessionValue(ACTIVE_TAB_STORAGE_KEY);
  if (savedTab) {
    state.activeTab = savedTab;
  }

  const savedDraft = readSessionJson(EDITOR_DRAFT_STORAGE_KEY);
  if (isPlainObject(savedDraft)) {
    state.draftConfig = savedDraft;
    state.baseRevision = readSessionValue(`${EDITOR_DRAFT_STORAGE_KEY}:revision`);
    state.editorDirty = true;
    state.editorStatus = {
      message: "Чернетку відновлено після перезавантаження сторінки.",
      tone: "status-warn",
    };
  }

  const savedTemplateDraft = readSessionJson(TEMPLATE_DRAFT_STORAGE_KEY);
  if (isPlainObject(savedTemplateDraft)) {
    state.templateDrafts = savedTemplateDraft.drafts || {};
    const savedSelection = isPlainObject(savedTemplateDraft.templateSelection)
      ? savedTemplateDraft.templateSelection
      : {};
    state.templateSelection = {
      addressIndex: Number.parseInt(savedSelection.addressIndex, 10) || 0,
      addressPrefix: String(savedSelection.addressPrefix || ""),
      templateName: String(savedSelection.templateName || ""),
    };
    state.templateSourceDraft = String(savedTemplateDraft.templateSourceDraft || "");
    state.templateSourceDirty = Boolean(savedTemplateDraft.templateSourceDirty);
    state.templateSourceName = String(savedTemplateDraft.templateSourceName || "");
    state.templateBaseSource = state.templateDrafts[state.templateSourceName]?.baseSource ?? "";
    if (state.templateSourceDirty) {
      state.templateStatus = {
        message: "Чернетку шаблону відновлено після перезавантаження сторінки.",
        tone: "status-warn",
      };
    }
  }
}

function updateBusyControls() {
  const unavailable = !state.configPayload;
  [validateButton, saveButton, saveRestartButton].forEach((button) => {
    button.disabled = unavailable || state.configBusy || state.botBusy;
  });
  [addAddressButton, importConfigButton, exportConfigButton, importAddressButton,
    document.getElementById("reload-config-button")].forEach((button) => {
    button.disabled = unavailable || state.configBusy || state.botBusy;
  });
  editorContainer.querySelectorAll('[data-action="wizard-apply"], [data-action="activate-bots"], [data-action="connect-bot"]').forEach(button => {
    button.disabled = unavailable || state.configBusy || state.botBusy;
  });
  document.getElementById("bot-fields").disabled = unavailable || state.configBusy || state.botBusy;
  document.querySelectorAll("[data-edit-bot], [data-delete-bot]").forEach((button) => { button.disabled = state.configBusy || state.botBusy; });
  templatesContainer.querySelectorAll("[data-action]").forEach((button) => {
    const needsAddress = ["template-preview", "template-send-test"].includes(button.dataset.action);
    button.disabled = state.templateBusy || !state.templatesPayload ||
      (needsAddress && !getDraftConfig().addresses?.length);
  });
}

function markEditorChanged() {
  state.editorDirty = true;
  state.editorRevision += 1;
  state.templatePreview = null;
  updateTemplatePreview();
  persistEditorDraftSnapshot();
  updateEditorLiveParts();
}

function updateEditorLiveParts() {
  updateRecipientPickers();
  const notices = document.getElementById("editor-notices");
  if (notices && state.configPayload) {
    const text = [];
    if (state.configPayload.test_chat_id) text.push(`Основний бот у тестовому режимі: повідомлення → ${state.configPayload.test_chat_id}.`);
    if (state.configPayload.bots?.restart_required) text.push("Боти збережені й очікують запуску. Натисніть «Застосувати» або запустіть збережені налаштування на кроці Telegram.");
    if (state.configPayload.restart_required) text.push("Збережені налаштування очікують застосування.");
    if (state.editorDirty && state.baseRevision && state.baseRevision !== state.configPayload.revision) {
      text.push("Збережені налаштування змінилися поза цією чернеткою. Ваш ввід збережено; перед збереженням узгодьте зміни.");
    }
    notices.textContent = text.join(" ");
  }
  const previewTime = document.getElementById("editor-preview-time");
  if (previewTime) previewTime.textContent = previewDescription();
  editorContainer.querySelectorAll(".address-editor-card").forEach((form, index) => {
    const address = state.draftConfig?.addresses?.[index];
    const preview = form.querySelector(".preview-card");
    if (!address) return;
    const title = form.querySelector(".profile-summary h3");
    if (title) title.textContent = address.display_name || "Новий профіль";
    updateProfileSummary(form, address);
    updateTelegramSetup(form, address);
    if (preview) preview.outerHTML = renderPowerPreview(address, index);
    const guidance = form.querySelector(".draft-guidance");
    if (guidance) guidance.outerHTML = renderDraftGuidance(address);
    const modeGuidance = form.querySelector(".power-mode-guidance");
    if (modeGuidance) modeGuidance.innerHTML = renderModeGuidance(address);
    form.querySelectorAll(".preset-button").forEach((button) => {
      button.classList.toggle("active", Boolean(address.power?.enabled && button.dataset.preset === address.power.mode));
    });
  });
  updateWizard();
  updateBusyControls();
}

function editorInteraction() {
  const active = document.activeElement;
  const fields = [...editorContainer.querySelectorAll("input, select, textarea")];
  const focused = fields.includes(active) ? fields.indexOf(active) : -1;
  return { focused, start: active?.selectionStart, end: active?.selectionEnd,
    transient: [...editorContainer.querySelectorAll(".address-editor-card")].map((form) => ({
      prefix: form.querySelector(".addr-entity-prefix").value,
      entity: form.querySelector(".voltage-entity-select")?.value,
      label: form.querySelector(".voltage-entity-label")?.value,
    })) };
}

function restoreEditorInteraction(saved) {
  editorContainer.querySelectorAll(".address-editor-card").forEach((form, index) => {
    const old = saved.transient[index];
    if (!old || old.prefix !== form.querySelector(".addr-entity-prefix").value) return;
    for (const [selector, value] of [[".voltage-entity-select", old.entity], [".voltage-entity-label", old.label]]) {
      const input = form.querySelector(selector);
      if (input && value !== undefined) input.value = value;
    }
  });
  const field = editorContainer.querySelectorAll("input, select, textarea")[saved.focused];
  if (field) {
    field.focus({ preventScroll: true });
    if (saved.start !== null) {
      try { field.setSelectionRange(saved.start, saved.end); } catch { /* Number/select controls. */ }
    }
  }
}

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
  // Keep incomplete/invalid input in the draft. Validation must explain it,
  // never silently replace it with a default or truncate a decimal integer.
  if (String(value).trim() === "") return "";
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || (parser === Number.parseInt && !Number.isInteger(parsed))) return value;
  return parsed;
}

async function fetchJson(url, options = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error("Сервер повернув неочікувану відповідь. Чернетку збережено; перевірте з’єднання з HA.");
    }
    if (!response.ok || data?.ok === false) {
      const details = Array.isArray(data?.errors)
        ? data.errors.map((error) => `${error.loc.join(".")}: ${error.msg}`).join("; ")
        : data?.message || response.statusText;
      throw new Error(details);
    }
    if (!isPlainObject(data)) throw new Error("Некоректна відповідь сервера. Чернетку не змінено.");
    return data;
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error("Час очікування відповіді вичерпано. Чернетку збережено. Якщо ви зберігали налаштування, перевірте збережену версію перед повтором.");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

function createBlankAddress(prefill = {}) {
  return {
    entity_prefix: prefill.entity_prefix ?? "",
    profile_id: "", mode: "building", account_entity: "", telegram_user_id: 0, allow_readings: false, reading_reminders: false,
    display_name: prefill.display_name ?? prefill.friendly_name ?? "",
    telegram_chat_id: "",
    telegram_bot_id: "default",
    selected_group: "",
    schedule_change: { enabled: true, silent: true },
    emergency: { enabled: true, silent: false },
    group_change: { enabled: true, silent: false },
    voltage: {
      enabled: false,
      entities: [],
      present_above: 50,
      max_age_seconds: 600,
      unavailable_as_missing: false,
      low: 195,
      high: 245,
      hysteresis: 5,
      delay: 15,
      silent: false,
    },
    power: {
      enabled: false,
      mode: "sources",
      sources: [],
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
    receive_commands: config?.receive_commands !== false,
    welcome_enabled: config?.welcome_enabled !== false,
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
  normalized.telegram_bot_id = String(normalized.telegram_bot_id || "default").trim();
  normalized.power.loss_entity = String(normalized.power?.loss_entity || "").trim();
  normalized.power.loss_state = String(normalized.power?.loss_state || "0").trim() || "0";

  normalized.voltage.entities = (normalized.voltage?.entities || [])
    .filter((item) => item && String(item.entity || "").trim())
    .map((item) => ({
      entity: String(item.entity || "").trim(),
      label: String(item.label || "").trim(),
    }));

  if (!normalized.entity_prefix && normalized.mode !== "personal") {
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
    receive_commands: rawConfig.receive_commands ?? currentConfig.receive_commands ?? true,
    welcome_enabled: rawConfig.welcome_enabled ?? currentConfig.welcome_enabled ?? true,
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

  const revision = state.editorRevision;
  let parsed;
  try {
    parsed = JSON.parse(await file.text());
  } catch (error) {
    setEditorStatus("Не вдалося прочитати JSON-файл конфігурації.", "status-bad");
    return;
  }

  if (revision !== state.editorRevision || state.configBusy) {
    setEditorStatus("Під час читання файлу чернетка змінилася. Повторіть імпорт; ваш ввід збережено.", "status-warn");
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

  const revision = state.editorRevision;
  const targetIndex = state.pendingAddressImportIndex;
  let parsed;
  try {
    parsed = JSON.parse(await file.text());
  } catch (error) {
    setEditorStatus("Не вдалося прочитати JSON-файл адреси.", "status-bad");
    return;
  }

  if (revision !== state.editorRevision || state.configBusy) {
    setEditorStatus("Під час читання файлу чернетка змінилася. Повторіть імпорт; ваш ввід збережено.", "status-warn");
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
  const duplicateIndex = config.addresses.findIndex(
    (address) => profileIdentity(address) === profileIdentity(importedAddress),
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
  if (document.getElementById("cfg-ha-token")) {
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
  const changed = state.activeTab !== tab;
  state.activeTab = tab;
  if (changed && tab === "templates" && state.templatesPayload) renderTemplates(true);
  writeSessionValue(ACTIVE_TAB_STORAGE_KEY, tab);
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
              <p class="meta-label">Telegram-бот</p>
              <p class="meta-value">${escapeHtml(address.bot_name || "Основний бот")}</p>
            </div>
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
      "Бінарні статуси HA",
      "Для живлення обирайте лише статус міської мережі. Інші бінарні датчики тут також видимі.",
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

  if (!address.entity_prefix && address.mode !== "personal") {
    errors.push("Для роботи профілю потрібно вказати entity_prefix.");
  }
  if (!address.display_name && address.entity_prefix) {
    hints.push("display_name порожній, тому runtime спробує взяти назву з Home Assistant.");
  }
  if ((address.voltage?.enabled || usesVoltageTopology) && !voltageCount) {
    errors.push("Моніторинг напруги увімкнено, але voltage entities не вибрані.");
  }
  if (address.power?.enabled) {
    if (address.power.mode === "sources" && !address.power.sources?.[0]?.entity) errors.push("Оберіть основний датчик живлення.");
    if (address.power.mode === "sources" && address.power.sources?.length === 2 && address.power.sources[0].entity === address.power.sources[1].entity) errors.push("Для підтвердження потрібен інший незалежний датчик.");
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
    if (usesVoltageTopology) {
      hints.push("Втрата зв’язку або застарілі вимірювання не підтверджують відключення. Про них надходить окреме повідомлення після затримки.");
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
            ? '<p class="helper-copy">Налаштування заповнені. Запустіть перевірку, щоб порівняти його з живим станом HA.</p>'
            : ""
      }
    </div>
  `;
}

function currentEditorPreview() {
  return state.editorDirty && state.validatedPreview?.revision === state.editorRevision
    ? state.validatedPreview.preview : state.configPayload?.preview;
}

function previewDescription() {
  const checked = state.editorDirty && state.validatedPreview?.revision === state.editorRevision;
  const snapshot = currentEditorPreview();
  const time = snapshot?.generated_at ? new Date(snapshot.generated_at).toLocaleTimeString(UI_LOCALE) : null;
  if (checked) return `Знімок перевіреної чернетки${time ? ` о ${time}` : ""}. Для нових показань натисніть «Перевірити».`;
  if (state.editorDirty) return "Показано стан збережених налаштувань. Перевірте чернетку, щоб побачити її результат.";
  return time ? `Живий стан збережених налаштувань оновлено о ${time}.` : "Живий стан ще недоступний.";
}

function profileIdentity(address) {
  return address?.profile_id || address?.entity_prefix || "";
}

function resolvePreview(address, index) {
  const snapshot = currentEditorPreview();
  const previews = snapshot?.addresses || [];
  if (profileIdentity(previews[index]) === profileIdentity(address)) {
    return previews[index];
  }
  if (address.entity_prefix) {
    return previews.find((item) => profileIdentity(item) === profileIdentity(address)) || null;
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
      <div class="preview-card ${state.editorDirty && state.validatedPreview?.revision !== state.editorRevision ? "stale" : ""}">
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

  if (preview.mode === "sources") {
    const evidence = preview.signals?.power_sources;
    const labels = {reported: "дані датчика", unavailable: "немає зв’язку", stale: "застарілий звіт", awaiting_report: "очікування нового звіту після підключення", invalid_value: "некоректне значення", invalid_timestamp: "некоректний час звіту", unit_mismatch: "невідповідні одиниці", silent_after_confirmed_loss: "датчик вимкнувся після підтвердження"};
    return `<div class="preview-card"><h4>Перевірка датчиків</h4><p>${escapeHtml(preview.reason || "Очікування даних")}</p><div class="signal-grid">${(evidence?.sources || []).map(item => `<div class="signal-card"><p class="entity-id">${escapeHtml(item.entity_id)}</p><strong>${escapeHtml(getHouseStateMeta(item.state).label)}</strong><p>${escapeHtml(labels[item.reason] || item.reason)}</p></div>`).join("")}</div><p class="helper-copy">Це перевірка поточних значень. Робочий бот додатково витримує налаштовані затримки; очікування після зникнення датчика не відновлюється зі збереженого стану.</p></div>`;
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
    "недоступний сенсор: стан невідомий",
  ].join(" · ");

  return `
    <div class="preview-card ${state.editorDirty && state.validatedPreview?.revision !== state.editorRevision ? "stale" : ""}">
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
      <p class="preview-stale-note">${escapeHtml(previewDescription())}</p>
    </div>
  `;
}

function powerSourceCatalog() {
  return ["binary_power", "voltage_sensors", "frequency_sensors"].flatMap(getCatalogItems);
}

function renderPowerSource(source = {}, index = 0) {
  const kind = source.kind || "binary";
  const items = mergeSelectableItems(powerSourceCatalog(), [{entity: source.entity || "", label: "Поточний датчик"}]);
  return `<fieldset class="power-source" data-source-index="${index}">
    <legend>${index ? "Додаткове підтвердження (необов’язково)" : "Звідки визначати наявність світла?"}</legend>
    <label class="field"><span>${index ? "Другий датчик тієї самої лінії" : "Датчик у Home Assistant"}</span>
      <select class="source-entity">${renderEntityOptions(items, source.entity || "", index ? "Без другого датчика" : "Оберіть статус, напругу або частоту")}</select>
    </label>
    <div class="source-settings">
      <label class="field source-binary"><span>Світло є, коли датчик має стан</span>
        <select class="source-present-state"><option value="on" ${source.present_state !== "off" ? "selected" : ""}>Увімкнено (on)</option><option value="off" ${source.present_state === "off" ? "selected" : ""}>Вимкнено (off)</option></select>
      </label>
      ${index ? `<label class="field"><span>Під час відключення цей датчик…</span><select class="source-availability"><option value="always_on" ${source.availability !== "mains_powered" ? "selected" : ""}>Працює та повідомляє про відсутність мережі</option><option value="mains_powered" ${source.availability === "mains_powered" ? "selected" : ""}>Вимикається разом із мережею</option></select></label>` : `<p class="helper-copy">Цей датчик і його зв’язок мають працювати під час відключення. Наприклад, статус із вашої автоматизації або вхід мережі інвертора. Вихід інвертора не показує наявність міської мережі.</p>`}
      <details class="source-advanced"><summary>Тип датчика та свіжість даних</summary>
        <label class="field"><span>Тип значення</span><select class="source-kind"><option value="binary" ${kind === "binary" ? "selected" : ""}>Статус (on/off)</option><option value="voltage" ${kind === "voltage" ? "selected" : ""}>Напруга (В)</option><option value="frequency" ${kind === "frequency" ? "selected" : ""}>Частота (Гц)</option></select></label>
        <label class="field source-numeric"><span>Поріг наявності мережі (В або Гц)</span><input class="source-threshold" type="number" min="0.01" step="any" value="${escapeHtml(source.present_above ?? (kind === "voltage" ? 50 : 1))}" /></label>
        <label class="field source-binary"><span>Як оновлюється статус?</span><select class="source-freshness"><option value="state" ${(source.freshness || "state") === "state" ? "selected" : ""}>При зміні; HA повідомляє про втрату зв’язку</option><option value="heartbeat" ${source.freshness === "heartbeat" ? "selected" : ""}>Регулярні звіти з перевіркою давності</option></select></label>
        <label class="field source-heartbeat"><span>Максимальний вік звіту (с)</span><input class="source-max-age" type="number" min="1" value="${escapeHtml(source.max_age_seconds ?? 180)}" /></label>
        <label class="field"><span>MQTT-топік живих даних (необов’язково)</span><input class="source-mqtt" type="text" value="${escapeHtml(source.mqtt_topic || "")}" /></label>
        <p class="helper-copy">Незмінний бінарний статус не застаріває сам по собі: його інтеграція має повідомляти unavailable при втраті зв’язку. Для вимірювань потрібні регулярні звіти. MQTT-топік, якщо вказаний, має містити саме це значення; retained-повідомлення не підтверджують стан.</p>
      </details>
    </div>
  </fieldset>`;
}

function collectPowerSources(form) {
  const rows = [...form.querySelectorAll(".power-source")];
  return rows.filter((row, index) => row.querySelector(".source-entity").value || (index === 0 && rows[1]?.querySelector(".source-entity").value)).map(row => {
    const kind = row.querySelector(".source-kind").value;
    return {
      entity: row.querySelector(".source-entity").value, kind,
      present_state: row.querySelector(".source-present-state").value,
      present_above: toNumber(row.querySelector(".source-threshold").value, kind === "voltage" ? 50 : 1),
      availability: row.querySelector(".source-availability")?.value || "always_on",
      freshness: kind === "binary" ? row.querySelector(".source-freshness").value : "heartbeat",
      max_age_seconds: toNumber(row.querySelector(".source-max-age").value, 180, Number.parseInt),
      mqtt_topic: row.querySelector(".source-mqtt").value.trim(),
    };
  });
}

function syncPowerSources(form) {
  form.querySelectorAll(".power-source").forEach(row => {
    const kind = row.querySelector(".source-kind").value;
    row.querySelector(".source-settings").classList.toggle("hidden", !row.querySelector(".source-entity").value);
    row.querySelectorAll(".source-binary").forEach(n => n.classList.toggle("hidden", kind !== "binary"));
    row.querySelectorAll(".source-numeric").forEach(n => n.classList.toggle("hidden", kind === "binary"));
    row.querySelectorAll(".source-heartbeat").forEach(n => n.classList.toggle("hidden", kind === "binary" && row.querySelector(".source-freshness").value === "state" && !row.querySelector(".source-mqtt").value.trim()));
  });
}

function renderAddressEditor(address, index) {
  const candidate = getCandidateForPrefix(address.entity_prefix);
  const addressCandidates = getAddressCandidateOptions(address.entity_prefix, address.display_name);
  const voltageEntities = address.voltage?.entities || [];
  const frequencySensors = mergeSelectableItems(powerSourceCatalog(), [
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
    <article class="editor-card address-editor-card" data-index="${index}" data-profile-id="${escapeHtml(address.profile_id || "")}">
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
          <label class="field"><span>Режим профілю</span><select class="addr-mode"><option value="building" ${address.mode !== "personal" ? "selected" : ""}>Графіки за адресою</option><option value="personal" ${address.mode === "personal" ? "selected" : ""}>Особистий кабінет</option></select></label>
          <label class="field personal-only"><span>Особовий рахунок у HA</span><select class="addr-account"><option value="">Оберіть рахунок</option>${(state.discovery?.accounts || []).map(a => `<option value="${escapeHtml(a.entity_id)}" ${a.entity_id === address.account_entity ? "selected" : ""}>${escapeHtml(a.masked + " · " + a.address)}</option>`).join("")}${address.account_entity && !(state.discovery?.accounts || []).some(a => a.entity_id === address.account_entity) ? `<option selected value="${escapeHtml(address.account_entity)}">${escapeHtml(address.account_entity)} · недоступний</option>` : ""}</select><small>Підключіть кабінет через DTEK Monitor. Група береться тільки з API кабінету.</small></label>
          <label class="field personal-only"><span>Telegram ID власника</span><input class="addr-user-id" type="text" inputmode="numeric" value="${address.telegram_user_id || ""}" />${recipientPicker(".addr-user-id", true)}<small>Напишіть /start обраному боту, натисніть «Оновити» й оберіть себе зі списку або введіть ID вручну. Перевірте ім’я, @username та ID: збереження надасть цій людині доступ до рахунку.</small></label>
          <label class="toggle personal-only"><input class="addr-allow-readings" type="checkbox" ${address.allow_readings ? "checked" : ""}/><span>Дозволити передачу показів із підтвердженням</span></label>
          <label class="toggle personal-only"><input class="addr-reminders" type="checkbox" ${address.reading_reminders ? "checked" : ""}/><span>Нагадувати про покази</span></label>
          <label class="field building-only">
            <span>Адреса з DTEK Monitor</span>
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
            <span>Telegram-бот</span>
            <select class="addr-bot-id">${botOptions(address.telegram_bot_id || "default")}</select>
            <small>Оберіть наявного бота або натисніть «Додати іншого бота» вище.</small>
          </label>
          <label class="field building-only">
            <span>Чат ID</span>
            <input class="addr-chat-id" type="text" value="${escapeHtml(address.telegram_chat_id || "")}" placeholder="Окремий чат (необов'язково)" />
            ${recipientPicker(".addr-chat-id")}
            <small>Особистий чат, група або канал. Для особистого чату спочатку натисніть «Розпочати» у бота. Порожнє поле використовує чат обраного бота.</small>
          </label>
          <label class="field building-only">
            <span>Обрана група (порожньо — всі окремо)</span>
            <input class="addr-selected-group" type="text" value="${escapeHtml(address.selected_group || "")}" placeholder="GPV5.1" />
            <small>Якщо оператор не показує номери груп, залиште поле порожнім — бот покаже всі варіанти окремо.</small>
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
        <div class="subpanel-head" data-action="toggle-section" role="button" tabindex="0">
          <h4>Напруга: показувати та попереджати</h4>
        </div>
        <div class="subpanel-body">
          <div class="helper-block subtle">
            <p class="meta-label">Напруга та фази</p>
            <p class="helper-copy">
              Ці датчики показують напругу та її якість. У режимі «Датчики Home Assistant» вони не змінюють рішення про наявність світла. Для кількох фаз використовуйте окрему збережену схему в розділі живлення.
            </p>
          </div>
          <div class="voltage-topology-fields">
            ${renderEntityCollectionEditor(
              index,
              "voltage",
              "Сенсори напруги",
              "Оберіть датчик напруги для статусу. Для різних фаз можна додати кілька та вказати їх назви.",
              voltageSensors,
              voltageEntities,
            )}
          </div>
          <div class="inline-fields voltage-topology-fields">
            <label class="field compact-field">
              <span>Є живлення вище (В)</span>
              <input class="voltage-present-above" type="number" step="0.1" value="${escapeHtml(address.voltage?.present_above ?? 50)}" />
            </label>
            <label class="field">
              <span>Максимальний вік вимірювання, с</span>
              <input class="voltage-max-age" type="number" min="1" value="${escapeHtml(address.voltage?.max_age_seconds ?? 600)}" />
            </label>

          </div>
          <p class="helper-copy">
            Недоступні, відновлені після перезапуску або застарілі показання означають невідомий стан. Виберіть максимальний вік з урахуванням реального інтервалу оновлення датчика.
          </p>

          <div class="helper-block">
            <label class="toggle">
              <input class="voltage-enabled" type="checkbox" ${address.voltage?.enabled ? "checked" : ""} />
              <span>Надсилати окремі сповіщення про якість напруги</span>
            </label>
            <p class="helper-copy">
              Цей чекбокс вмикає лише окремі повідомлення про низьку/високу/нормальну напругу. Показання залишаються у статусі навіть без окремих сповіщень. Наявність світла визначається в секції живлення.
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
              <span>Гістерезис (В)</span>
              <input class="voltage-hysteresis" type="number" step="0.1" min="0" value="${escapeHtml(address.voltage?.hysteresis ?? 5)}" />
            </label>
            <label class="field compact-field">
              <span>Затримка (с)</span>
              <input class="voltage-delay" type="number" step="1" value="${escapeHtml(address.voltage?.delay ?? 15)}" />
            </label>
          </div>
          <p class="helper-copy voltage-alert-only">
            Після low/high alert повідомлення про нормалізацію надсилається тільки тоді, коли напруга повернулась у безпечний діапазон із цим запасом, щоб не спамити біля порога.
          </p>
        </div>
      </div>

      <div
        class="subpanel ${isPanelCollapsed(address, index, "power", !address.power?.enabled) ? "collapsed" : ""}"
        data-panel-key="${escapeHtml(powerPanelKey)}"
      >
        <div class="subpanel-head" data-action="toggle-section" role="button" tabindex="0">
          <label class="toggle"><input class="power-enabled" type="checkbox" ${address.power?.enabled ? "checked" : ""} /><span>Визначення живлення</span></label>
        </div>
        <div class="subpanel-body">

          <div class="power-mode-guidance">${renderModeGuidance(address)}</div>
          ${powerHints}

          <div class="power-fields">
            <div class="inline-fields">
              <label class="field compact-field">
                <span>Режим</span>
                <select class="power-mode">
                  <option value="sources" ${address.power?.mode === "sources" ? "selected" : ""}>Датчики Home Assistant</option>
                  <option value="dtek_only" ${address.power?.mode === "dtek_only" ? "selected" : ""}>Лише DTEK</option>
                  <option value="voltage_only" ${address.power?.mode === "voltage_only" ? "selected" : ""}>Напруга та фази (сумісна схема)</option>
                  <option value="loss_plus_voltage" ${address.power?.mode === "loss_plus_voltage" ? "selected" : ""}>Сигнал втрати + напруга (сумісна схема)</option>
                </select>
              </label>
            </div>

            <div class="sources-only">
              ${renderPowerSource(address.power?.sources?.[0], 0)}
              ${renderPowerSource(address.power?.sources?.[1], 1)}
              <p class="helper-copy">Два датчики мають вимірювати ту саму лінію незалежно один від одного. Вони не є різними фазами. Суперечливі дані означають невідомий стан. Зникнення другого датчика підтверджує відключення тільки після явного сигналу першого та очікування.</p>
            </div>
            <div class="helper-block subtle sensor-mode-only">
              <label class="toggle">
                <input class="power-telemetry-notifications" type="checkbox" ${address.power?.telemetry_notifications === true ? "checked" : ""} />
                <span>Повідомляти про втрату та повернення даних</span>
              </label>
              <label class="field compact-field">
                <span>Затримка попередження про втрату даних (с)</span>
                <input class="power-telemetry-loss-delay" type="number" min="0" value="${escapeHtml(address.power?.telemetry_loss_delay ?? 20)}" />
              </label>
              <p class="helper-copy">Недоступність датчика не підтверджує відключення світла. Для швидкого підтвердження потрібен незалежний сенсор тієї самої лінії, доступний від резервного живлення.</p>
            </div>

            <div class="power-sensor-group sensor-mode-only">
              <label class="field compact-field">
                <span>Підтвердження відсутності напруги (с)</span>
                <input class="power-loss-delay" type="number" min="0" step="1" value="${escapeHtml(address.power?.loss_delay ?? 30)}" />
              </label>
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
                      "Оберіть датчик мережі",
                    )}
                  </select>
                </label>
                <label class="field">
                  <span>MQTT-топік живих даних (необов’язково)</span>
                  <input class="power-loss-mqtt-topic" type="text" value="${escapeHtml(address.power?.loss_mqtt_topic || "")}" />
                  <span class="helper-copy">Для MQTT-датчика: приймати підтвердження тільки з нових повідомлень, ігнорувати збережені retained значення.</span>
                </label>
                <label class="field compact-field">
                  <span>Стан втрати</span>
                  <input class="power-loss-state" type="text" value="${escapeHtml(address.power?.loss_state || "0")}" />
                </label>

              </div>
            </div>

            <div class="power-sensor-group sensor-mode-only">
              <p class="section-label">Очікування другого датчика</p>
              <div class="inline-fields">
                <label class="field loss-plus-voltage-only" style="flex:2">
                  <span>Логіка</span>
                  <input type="text" value="Підтвердження береться із секції «Напруга та фази»" readonly />
                </label>
                <label class="field compact-field">
                  <span>Очікування свіжих даних (с)</span>
                  <input class="power-confirm-timeout" type="number" step="1" value="${escapeHtml(address.power?.confirm_timeout ?? 15)}" />
                </label>
              </div>
              <p class="helper-copy loss-plus-voltage-only">
                Після спрацювання loss trigger add-on чекає новий update від сенсора(ів) напруги. Якщо за цей час приходить свіже значення вище порога, це вважається хибним спрацюванням. Якщо update не приходить або напруга явно зникає, старе значення вважається stale і будинок переходить в outage.
              </p>
            </div>

            <div class="power-sensor-group sensor-mode-only">
              <p class="section-label">Повернення</p>
              <div class="inline-fields">
                <label class="field voltage-mode-only">
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
        <div class="subpanel-head" data-action="toggle-section" role="button" tabindex="0">
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
  if (!state.wizard) {
    const saved = readSessionJson(`${UI_STORAGE_PREFIX}:wizard`);
    const i = state.draftConfig?.addresses?.findIndex(a => a.profile_id === saved?.id) ?? -1;
    if (i >= 0 && Number.isInteger(saved?.step) && saved.step >= 0 && saved.step <= 3) { state.wizard = saved; state.activeProfile = i; }
  }
  if (!state.configPayload) {
    editorContainer.innerHTML = '<p class="empty-state">Редактор ще не готовий.</p>';
    return;
  }
  if (!force && editorContainer.children.length &&
      (state.editorDirty || editorContainer.contains(document.activeElement))) {
    updateEditorLiveParts();
    editorStatus.className = `status-message ${state.editorStatus.tone}`;
    editorStatus.textContent = state.editorStatus.message;
    return;
  }

  const config = state.draftConfig || cloneJson(state.configPayload.config);
  const options = state.configPayload.options;
  const addresses = config.addresses;
  state.activeProfile = Math.min(state.activeProfile, Math.max(0, addresses.length - 1));
  const interaction = editorInteraction();

  editorContainer.innerHTML = `
    <details class="editor-card global-settings"><summary>Загальні та службові налаштування</summary>
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
          <input id="cfg-ha-token" type="password" autocomplete="off" value="${escapeHtml(config.ha_token || "")}" />
        </label>
      </div>
      <label class="toggle"><input id="cfg-receive-commands" type="checkbox" ${config.receive_commands !== false ? "checked" : ""}/><span>Приймати команди та особисті повідомлення</span></label>
      <p class="helper-copy">Якщо цей токен працює на кількох HA, увімкніть приймання лише на сервері з особистим кабінетом. Решта серверів продовжать надсилати сповіщення.</p>
      <label class="toggle"><input id="cfg-welcome" type="checkbox" ${config.welcome_enabled !== false ? "checked" : ""}/><span>Привітання для нових користувачів</span></label>
      <p class="helper-copy">Лише у приватній переписці у відповідь на звернення, не частіше разу на добу. Текст і посилання можна змінити: «Шаблони» → «Привітання · welcome». Типово — опис бота, посилання на GitHub і Telegram ID співрозмовника. Шаблон спільний для всіх ботів цього add-on; дані рахунків у нього не передаються.</p>
      <p id="editor-preview-time" class="helper-copy">${escapeHtml(previewDescription())}</p>
    </details>
    ${addresses.map((address, index) => renderAddressEditor(address, index)).join("") || '<p class="empty-state">Профілів ще немає. Натисніть «Додати профіль», щоб обрати адресу або особистий кабінет.</p>'}
  `;

  decorateProfiles();
  syncAllEditorVisibility();
  restoreEditorInteraction(interaction);
  updateEditorLiveParts();
  editorStatus.className = `status-message ${state.editorStatus.tone}`;
  editorStatus.textContent = state.editorStatus.message;
}

function decorateProfiles() {
  // Reparent existing controls, preserving the collector and all draft protections.
  const global = editorContainer.querySelector(".global-settings");
  if (global) editorContainer.append(global);
  editorContainer.querySelectorAll(".address-editor-card").forEach((form, index) => {
    const address = state.draftConfig?.addresses?.[index] || {};
    const summary = document.createElement("div");
    summary.className = "profile-summary";
    const bot = state.configPayload?.bots?.profiles?.find(b => b.id === (address.telegram_bot_id || "default"));
    const recipient = address.mode === "personal" ? address.telegram_user_id : address.telegram_chat_id || bot?.chat_id || state.configPayload?.options?.default_chat_id;
    const runtime = state.overview?.addresses?.find(a => a.profile_id === profileIdentity(address) || a.entity_prefix === address.entity_prefix);
    summary.innerHTML = `<div><h3>${escapeHtml(address.display_name || "Новий профіль")}</h3><p class="helper-copy">${address.mode === "personal" ? "Особистий кабінет" : "Графіки за адресою"} · ${escapeHtml(bot?.name || "Основний бот")} · ${escapeHtml(recipient || "Одержувача не обрано")}</p><small>${runtime ? (state.overview?.runtime?.ha_connected ? "Працює · " + escapeHtml(HOUSE_STATE_META[runtime.live_power]?.label || "дані оновлюються") : "Немає зв’язку з HA") : "Налаштування профілю"}</small></div><button type="button" class="secondary-button" data-action="edit-profile" data-index="${index}" aria-expanded="${index === state.activeProfile}">Налаштувати</button>`;
    const body = document.createElement("div");
    body.className = "profile-editor";
    while (form.firstChild) body.append(form.firstChild);
    form.append(summary, body);
    const source = document.createElement("p"); source.className = "profile-source helper-copy"; summary.querySelector("div").append(source);
    const actions = document.createElement("details");
    actions.className = "profile-actions";
    actions.innerHTML = '<summary>Дії з профілем</summary>';
    actions.append(body.querySelector(".addr-actions"));
    body.append(actions);
    body.classList.toggle("hidden", index !== state.activeProfile);
    const timings = document.createElement("details");
    timings.className = "power-timing";
    timings.innerHTML = "<summary>Затримки та повідомлення про втрату даних</summary>";
    body.querySelectorAll(".power-fields > .sensor-mode-only").forEach(node => timings.append(node));
    body.querySelector(".power-fields").append(timings);
    const isNew = state.wizard?.id === address.profile_id && Boolean(address.profile_id);
    if (isNew) form.classList.add("wizard-profile");
    form.classList.add("stepped-profile");
    const modes = body.querySelector(".power-mode").closest(".inline-fields");
    const compatibility = document.createElement("details");
    compatibility.className = "power-compatibility";
    compatibility.innerHTML = '<summary>Додатково: сумісність зі старими схемами</summary><p class="helper-copy">Нові профілі використовують датчики Home Assistant. Тут можна залишити попередню схему для наявної конфігурації.</p>';
    modes.before(compatibility); compatibility.append(modes);
    const legacy = document.createElement("p"); legacy.className = "legacy-power-note helper-copy";
    compatibility.before(legacy);
    const stages = [0,1,2,3].map(step => {
      const node = document.createElement("section");
      node.className = "profile-stage";
      node.dataset.stage = step;
      return node;
    });
    const grid = body.querySelector(".addr-identity-grid");
    for (const field of [...grid.children]) {
      const delivery = field.querySelector(".addr-bot-id, .addr-chat-id, .addr-user-id");
      const notify = field.querySelector(".addr-allow-readings, .addr-reminders");
      stages[delivery ? 1 : notify ? 2 : 0].append(field);
    }
    body.querySelector(".addr-header").remove();
    while (body.firstChild) stages[2].append(body.firstChild);
    const nav = document.createElement("nav");
    nav.className = "wizard-steps";
    nav.setAttribute("aria-label", "Кроки налаштування");
    nav.innerHTML = ["1. Джерело", "2. Telegram", "3. Сповіщення", "4. Перевірка"].map((label, i) => `<button type="button" class="secondary-button" data-action="wizard-step" data-step="${i}">${label}</button>`).join("");
    body.append(nav, ...stages);
    const controls = document.createElement("div");
    controls.className = "wizard-controls";
    controls.innerHTML = `<button type="button" class="secondary-button" data-action="wizard-back">Назад</button><button type="button" class="secondary-button" data-action="wizard-next">Далі</button>`;
    body.append(controls);
    stages[1].insertAdjacentHTML("afterbegin", `<div class="telegram-setup"><div class="telegram-progress" role="status"></div><div class="tool-actions"><button type="button" class="secondary-button" data-action="connect-bot">Додати іншого бота</button><button type="button" class="secondary-button" data-action="activate-bots">Запустити збережені налаштування</button><button type="button" class="secondary-button" data-action="refresh-recipients">Оновити одержувачів</button></div><p class="helper-copy">Після запуску напишіть боту /start. Для групи додайте бота й надішліть /start@ім’я_бота; для каналу призначте його адміністратором. Оберіть одержувача нижче або введіть ID. Виявлення користувача саме по собі не надає доступу до рахунку.</p></div>`);
    if (!isNew) controls.insertAdjacentHTML("beforeend", '<button type="button" class="secondary-button" data-action="show-all">Усі розділи</button>');
  });
  editorContainer.querySelectorAll("details").forEach(node => {
    node.open = Boolean(state.detailsOpen[detailKey(node)]);
  });
  updateWizard();
}

function detailKey(node) {
  const form = node.closest(".address-editor-card");
  return `${form?.dataset.profileId || form?.dataset.index || "global"}:${node.className}:${node.closest(".power-source")?.dataset.sourceIndex || ""}`;
}
editorContainer.addEventListener("toggle", event => {
  if (event.target instanceof HTMLDetailsElement && event.target.isConnected) {
    state.detailsOpen[detailKey(event.target)] = event.target.open;
  }
}, true);

function selectedBot(address) {
  return state.configPayload?.bots?.profiles?.find(b => b.id === (address.telegram_bot_id || "default"));
}
function destination(address) {
  const bot = selectedBot(address);
  const id = address.mode === "personal" ? address.telegram_user_id : address.telegram_chat_id || bot?.chat_id || (bot?.id === "default" ? state.configPayload?.options?.default_chat_id : "");
  const peer = state.configPayload?.bots?.recipients?.[bot?.id]?.find(p => String(p.id) === String(id));
  return {id, peer, label: id ? `${peer?.name || (address.mode === "personal" ? "Власник" : "Чат")}${peer?.username ? " · @" + peer.username : ""} · ${id}` : "Одержувача не обрано"};
}
function updateProfileSummary(form, address) {
  const bot = selectedBot(address), dest = destination(address);
  const summary = form.querySelector(".profile-summary");
  summary.querySelector("p").textContent = `${address.mode === "personal" ? "Особистий кабінет" : "Графіки за адресою"} · ${bot?.name || "Бота не знайдено"} · ${dest.label}`;
  // A cabinet and a building can share a prefix. Never match a different profile's health.
  const source = summary.querySelector(".profile-source");
  const account = state.discovery?.accounts?.find(a => a.entity_id === address.account_entity);
  if (source) source.textContent = address.mode === "personal" ? account ? `${account.masked} · ${account.address}` : "Рахунок недоступний" : getCandidateForPrefix(address.entity_prefix)?.friendly_name || address.entity_prefix || "Адресу не обрано";
  const runtime = state.overview?.addresses?.find(a => address.profile_id ? a.profile_id === address.profile_id : a.entity_prefix === address.entity_prefix && a.mode !== "personal");
  summary.querySelector("small").textContent = !state.overview?.runtime?.ha_connected ? "Немає зв’язку з Home Assistant" : runtime ? `Працює · ${HOUSE_STATE_META[runtime.live_power]?.label || "дані оновлюються"}` : "Ще не запущено";
  const note = form.querySelector(".legacy-power-note");
  if (note) { note.textContent = `Поточна схема: ${POWER_MODE_META[address.power?.mode]?.label || "Датчики Home Assistant"}. Змінити можна в додаткових налаштуваннях нижче.`; note.classList.toggle("hidden", !address.power?.enabled || address.power.mode === "sources"); }
}
function updateTelegramSetup(form, address) {
  const bot = selectedBot(address), snapshot = state.configPayload?.bots;
  const runtime = snapshot?.runtime?.find(b => b.id === bot?.id);
  const dest = destination(address);
  const receiving = snapshot?.receiving !== false;
  const active = Boolean(runtime?.ready && !runtime.error && !runtime.delivery_error);
  const message = !bot ? "1. Оберіть або додайте бота." : snapshot?.restart_required ? "1. Бот збережений. Запустіть збережені налаштування, щоб він почав бачити одержувачів. Чернетка цього профілю залишиться незастосованою." : !receiving ? "Приймання команд вимкнено на цьому HA. Виявлення одержувачів і особисте меню не працюватимуть. Увімкніть приймання в загальних налаштуваннях лише на одному сервері для цього токена." : (runtime?.error || runtime?.delivery_error) ? `Бот потребує уваги: ${runtime.error || runtime.delivery_error}` : !active ? "Підключення бота ще не підтверджене. Оновіть стан після запуску." : dest.id ? `Бот підключений. Одержувач: ${dest.label}.` : "2. Напишіть боту /start, потім оновіть список і оберіть одержувача.";
  const node = form.querySelector(".telegram-progress");
  if (node) {
    node.textContent = message;
    if (bot?.username && /^[a-zA-Z0-9_]+$/.test(bot.username)) {
      const link = document.createElement("a"); link.href = `https://t.me/${bot.username}`; link.target = "_blank"; link.rel = "noopener"; link.textContent = ` Відкрити @${bot.username}`; node.append(link);
    }
  }
  const activate = form.querySelector('[data-action="activate-bots"]');
  if (activate) { activate.classList.toggle("hidden", !snapshot?.restart_required && !state.configPayload?.restart_required); activate.disabled = state.configBusy || state.botBusy; }
}
function sensorLabel(entity) {
  const item = powerSourceCatalog().find(s => s.entity_id === entity);
  return item ? `${item.friendly_name || entity} (${entity})` : entity || "не обрано";
}
function reviewProfile(address) {
  const bot = selectedBot(address), dest = destination(address);
  const account = state.discovery?.accounts?.find(a => a.entity_id === address.account_entity);
  const house = getCandidateForPrefix(address.entity_prefix);
  const source = address.mode === "personal" ? account ? `${account.masked} · ${account.address}` : "Рахунок недоступний у Home Assistant" : house?.friendly_name || address.entity_prefix || "Адресу не обрано";
  const names = [address.schedule_change.enabled && "зміни графіка", address.group_change.enabled && "зміни групи", address.emergency.enabled && "екстрені відключення", address.upcoming_outage.enabled && `нагадування за ${address.upcoming_outage.minutes} хв`, address.power.enabled && "наявність світла", address.voltage.enabled && "якість напруги", address.status_message.enabled && "статус", address.mode === "personal" && address.reading_reminders && "нагадування про покази"].filter(Boolean);
  const sources = address.power?.sources || [];
  let power = "Без локального визначення живлення";
  if (address.power.enabled) {
    power = address.power.mode === "sources" ? sources.map((s,i) => `${i ? "Підтвердження" : "Основний"}: ${sensorLabel(s.entity)}; ${s.kind === "binary" ? `статус «${s.present_state}» означає наявність мережі` : s.kind === "frequency" ? "частота" : "напруга"}; ${s.availability === "mains_powered" ? "вимикається разом із мережею" : "працює при відключенні"}`).join("\n") || "Датчик не обрано" : `${POWER_MODE_META[address.power.mode]?.label}: ${address.power.loss_entity ? sensorLabel(address.power.loss_entity) + "; " : ""}${(address.voltage.entities || []).map(e => sensorLabel(e.entity)).join(", ")}`;
    power += `\nЗатримка відключення: ${address.power.loss_delay} с; відновлення: ${address.power.restore_delay} с.`;
  }
  const warnings = [];
  if (!state.overview?.runtime?.ha_connected) warnings.push("Немає зв’язку з Home Assistant. Перевірте його перед запуском.");
  if (address.mode === "personal" && !account) warnings.push("Обраний рахунок зараз недоступний.");
  if (!bot || !dest.id) warnings.push("Оберіть бота та одержувача на кроці Telegram.");
  if (dest.peer?.available === false || dest.peer?.available === 0) warnings.push("Бот більше не має доступу до обраного чату.");
  if (bot?.test_mode) warnings.push(address.mode === "personal" && String(bot.test_chat_id) !== String(dest.id) ? "Тестовий чат не збігається з власником. Особисті повідомлення заблоковані." : `Увімкнений тестовий режим: повідомлення → ${bot.test_chat_id}.`);
  if (address.mode === "personal" && getDraftConfig().receive_commands === false) warnings.push("Приймання команд вимкнено. Особисте меню недоступне.");
  const rows = [["Джерело", source], ["Бот", bot ? `${bot.name}${bot.username ? " · @" + bot.username : ""}` : "не обрано"], ["Одержувач", dest.label], ["Сповіщення", names.join(", ") || "вимкнені"], ["Живлення", power], ["Якість напруги", address.voltage.enabled ? (address.voltage.entities || []).map(e => sensorLabel(e.entity)).join(", ") || "датчик не обрано" : "сповіщення вимкнені"]];
  if (address.mode === "personal") rows.push(["Передача показів", address.allow_readings ? "Дозволена лише власнику, після окремого підтвердження в Telegram" : "Вимкнена"]);
  return `<h3>Перевірте перед запуском</h3><dl class="profile-review">${rows.map(([k,v])=>`<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(v)}</dd>`).join("")}</dl>${warnings.map(w=>`<p class="status-warn">${escapeHtml(w)}</p>`).join("")}<p class="helper-copy">«Застосувати» перевірить і збереже всі профілі та перезапустить застосунок. Увімкнені повідомлення надходитимуть указаним одержувачам. Незбережені поля токена потрібно спершу зберегти у формі бота.</p><button type="button" class="primary-button" data-action="wizard-apply">Застосувати</button>`;
}
function updateWizard() {
  editorContainer.querySelectorAll(".stepped-profile").forEach(form => {
    const index = Number(form.dataset.index), address = state.draftConfig?.addresses?.[index];
    if (!address) return;
    const isNew = form.classList.contains("wizard-profile");
    const step = isNew ? state.wizard?.step || 0 : state.profileSteps[profileIdentity(address)] || 0;
    const all = state.showAll && !isNew;
    form.querySelectorAll(".profile-stage").forEach(node => node.classList.toggle("hidden", !all && Number(node.dataset.stage) !== step));
    form.querySelectorAll('[data-action="wizard-step"]').forEach(node => node.setAttribute("aria-current", !all && Number(node.dataset.step) === step ? "step" : "false"));
    form.querySelector('[data-action="wizard-back"]').disabled = step === 0 || all;
    form.querySelector('[data-action="wizard-next"]').classList.toggle("hidden", step === 3 || all);
    const toggle = form.querySelector('[data-action="show-all"]');
    if (toggle) toggle.textContent = all ? "Повернутися до кроків" : "Усі розділи";
    if (step === 3 || all) {
      const html = reviewProfile(address), node = form.querySelector('[data-stage="3"]');
      if (node._reviewHtml !== html) { node.innerHTML = html; node._reviewHtml = html; }
    }
  });
  if (state.wizard) writeSessionJson(`${UI_STORAGE_PREFIX}:wizard`, state.wizard);
}
function wizardStep(next, form) {
  if (!form) return;
  const index = Number(form.dataset.index), address = collectEditorConfig().addresses[index];
  const isNew = form.classList.contains("wizard-profile");
  const step = isNew ? state.wizard.step : state.profileSteps[profileIdentity(address)] || 0;
  if (isNew && next > step) {
    if (next > 0 && !(address.mode === "personal" ? address.account_entity : address.entity_prefix)) {
      setEditorStatus("Оберіть адресу або рахунок, щоб продовжити.", "status-warn"); return;
    }
    if (next > 1 && !destination(address).id) {
      setEditorStatus("Оберіть одержувача у Telegram.", "status-warn"); return;
    }
    if (next > 1 && address.mode === "personal" && !/^[1-9][0-9]*$/.test(String(address.telegram_user_id || ""))) {
      setEditorStatus("Оберіть власника особистого кабінету в Telegram.", "status-warn"); return;
    }
  }
  if (state.editorDirty) persistEditorDraftSnapshot();
  const value = Math.max(0, Math.min(3, next));
  if (isNew) state.wizard.step = value;
  else { state.profileSteps[profileIdentity(address)] = value; writeSessionJson(`${UI_STORAGE_PREFIX}:steps`, state.profileSteps); }
  updateWizard();
  form.querySelector(`[data-action="wizard-step"][data-step="${value}"]`)?.focus();
}

function collectSelectedEntityItems(form, selector) {
  return Array.from(form.querySelectorAll(selector))
    .map(node => ({entity: node.dataset.entity?.trim() || "", label: node.dataset.label?.trim() || ""}))
    .filter(item => item.entity);
}

function collectEditorConfig() {
  const currentConfig = state.draftConfig || state.configPayload?.config || {};
  const config = {
    ha_token: document.getElementById("cfg-ha-token")?.value.trim() ?? "",
    export_default_templates: currentConfig.export_default_templates ?? false,
    receive_commands: document.getElementById("cfg-receive-commands")?.checked ?? true,
    welcome_enabled: document.getElementById("cfg-welcome")?.checked ?? true,
    addresses: [],
  };

  const addressForms = editorContainer.querySelectorAll(".address-editor-card");
  for (const form of addressForms) {
    const personal = form.querySelector(".addr-mode").value === "personal";
    config.addresses.push({
      profile_id: form.dataset.profileId || "",
      mode: personal ? "personal" : "building",
      account_entity: form.querySelector(".addr-account").value,
      telegram_user_id: Number(form.querySelector(".addr-user-id").value) || 0,
      allow_readings: form.querySelector(".addr-allow-readings").checked,
      reading_reminders: form.querySelector(".addr-reminders").checked,
      entity_prefix: form.querySelector(".addr-entity-prefix").value.trim(),
      display_name: form.querySelector(".addr-display-name").value.trim(),
      telegram_chat_id: personal ? String(Number(form.querySelector(".addr-user-id").value) || "") : form.querySelector(".addr-chat-id").value.trim(),
      telegram_bot_id: form.querySelector(".addr-bot-id").value,
      selected_group: personal ? "" : form.querySelector(".addr-selected-group").value.trim(),
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
        max_age_seconds: toNumber(form.querySelector(".voltage-max-age").value, 600),
        present_above: toNumber(form.querySelector(".voltage-present-above").value, 50),
        unavailable_as_missing: false,
        low: toNumber(form.querySelector(".voltage-low").value, 195),
        high: toNumber(form.querySelector(".voltage-high").value, 245),
        hysteresis: toNumber(form.querySelector(".voltage-hysteresis").value, 5),
        delay: toNumber(form.querySelector(".voltage-delay").value, 15, Number.parseInt),
        silent: form.querySelector(".voltage-silent").checked,
      },
      power: {
        enabled: form.querySelector(".power-enabled").checked,
        mode: form.querySelector(".power-mode").value,
        sources: collectPowerSources(form),
        loss_entity: form.querySelector(".power-loss-entity").value.trim(),
        loss_mqtt_topic: form.querySelector(".power-loss-mqtt-topic").value.trim(),
        loss_state: form.querySelector(".power-loss-state").value.trim(),
        loss_delay: toNumber(form.querySelector(".power-loss-delay").value, 30, Number.parseInt),
        confirm_timeout: toNumber(form.querySelector(".power-confirm-timeout").value, 15, Number.parseInt),
        restore_delay: toNumber(form.querySelector(".power-restore-delay").value, 10, Number.parseInt),
        silent: form.querySelector(".power-silent").checked,
        phase_notifications: form.querySelector(".power-phase-notifications").checked,
        telemetry_notifications: form.querySelector(".power-telemetry-notifications").checked,
        telemetry_loss_delay: toNumber(form.querySelector(".power-telemetry-loss-delay").value, 20, Number.parseInt),
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
  updateRecipientPickers(form);
  syncPowerSources(form);
  const personal = form.querySelector(".addr-mode")?.value === "personal";
  form.querySelectorAll(".personal-only").forEach(n => n.classList.toggle("hidden", !personal));
  form.querySelectorAll(".building-only").forEach(n => n.classList.toggle("hidden", personal));
  const powerEnabled = form.querySelector(".power-enabled").checked;
  const mode = form.querySelector(".power-mode").value;
  const voltageEnabled = form.querySelector(".voltage-enabled").checked;
  const statusEnabled = form.querySelector(".feature-status-enabled").checked;
  const statusDeliveryMode = form.querySelector(".feature-status-delivery-mode").value;
  const usesVoltageTopology = powerEnabled && ["voltage_only", "loss_plus_voltage"].includes(mode);
  form.querySelectorAll(".sources-only").forEach(n => n.classList.toggle("hidden", !powerEnabled || mode !== "sources"));
  form.querySelectorAll(".sensor-mode-only").forEach(n => n.classList.toggle("hidden", !powerEnabled || mode === "dtek_only"));
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
  if (voltagePanel && !Object.hasOwn(state.collapsedPanels, voltagePanel.dataset.panelKey) && (voltageEnabled || usesVoltageTopology || hasVoltageEntities)) {
    voltagePanel.classList.remove("collapsed");
    if (voltagePanel.dataset.panelKey) {
      rememberPanelState(voltagePanel.dataset.panelKey, false);
    }
  }
  const powerPanel = form.querySelector(".power-enabled")?.closest(".subpanel");
  if (powerPanel && powerEnabled && !Object.hasOwn(state.collapsedPanels, powerPanel.dataset.panelKey)) {
    powerPanel.classList.remove("collapsed");
    if (powerPanel.dataset.panelKey) {
      rememberPanelState(powerPanel.dataset.panelKey, false);
    }
  }
  form.querySelectorAll(".subpanel").forEach((panel) => {
    panel.querySelector(".subpanel-head")?.setAttribute("aria-expanded", String(!panel.classList.contains("collapsed")));
  });
}

function syncAllEditorVisibility() {
  editorContainer
    .querySelectorAll(".address-editor-card")
    .forEach((form) => syncAddressFormVisibility(form));
}

function setDraftConfig(config, message, tone = "muted") {
  state.draftConfig = cloneJson(config);
  state.editorDirty = true;
  state.editorRevision += 1;
  state.templatePreview = null;
  persistEditorDraftSnapshot(state.draftConfig);
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

    if (entitySelect) entitySelect.value = "";
    if (labelInput) labelInput.value = "";
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
  if (state.configBusy || state.botBusy || !state.configPayload) return;
  const config = collectEditorConfig();
  const revision = state.editorRevision;
  state.configBusy = true;
  updateBusyControls();
  try {
    const result = await fetchJson("./api/config/validate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config }),
    });
    if (revision !== state.editorRevision) {
      setEditorStatus("Перевірено попередню версію. Нові зміни залишилися в чернетці; перевірте її ще раз.", "status-warn");
      return;
    }
    // Validation is not a save. Keep the draft and its persisted revision.
    state.validatedPreview = { revision, preview: result.preview };
    updateEditorLiveParts();
    setEditorStatus("Налаштування перевірено. Попередній перегляд оновлено. Незбережені зміни залишаються у чернетці.", "status-good");
  } finally {
    state.configBusy = false;
    updateBusyControls();
  }
}

async function saveConfig({ restart }) {
  if (state.configBusy || state.botBusy || !state.configPayload) return;
  if (state.botDirty) { setEditorStatus("Спочатку збережіть або скасуйте зміни у формі бота. Токен ще не збережений.", "status-warn"); return; }
  const config = collectEditorConfig();
  const revision = state.editorRevision;
  state.configBusy = true;
  state.writeEpoch += 1;
  updateBusyControls();
  try {
    const result = await fetchJson("./api/config/save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config, restart, expected_revision: state.baseRevision }),
    });
    if (!isPlainObject(result.config) || !Array.isArray(result.config.addresses) || !result.revision) {
      throw new Error("Сервер не підтвердив збережену версію. Чернетку не змінено.");
    }
    state.configPayload = { ...state.configPayload, config: result.config,
      preview: result.preview, revision: result.revision, restart_required: true };
    state.baseRevision = result.revision;
    if (revision === state.editorRevision) {
      state.draftConfig = cloneJson(result.config);
      state.editorDirty = false;
      state.wizard = null;
      removeSessionValue(`${UI_STORAGE_PREFIX}:wizard`);
      clearEditorDraftSnapshot();
    } else {
      persistEditorDraftSnapshot();
    }
    updateEditorLiveParts();
    setEditorStatus(state.editorDirty
      ? "Попередню версію збережено. Нові зміни залишаються незбереженими у чернетці."
      : restart ? "Налаштування збережено. Перезапуск запланований."
        : "Налаштування збережено. Для застосування натисніть «Застосувати».",
      state.editorDirty || restart ? "status-warn" : "status-good");
  } finally {
    state.configBusy = false;
    state.writeEpoch += 1;
    updateBusyControls();
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
  const addressIndex = config.addresses?.findIndex(
    (address) => profileIdentity(address) === state.templateSelection.addressPrefix,
  ) ?? -1;
  if (addressIndex >= 0) state.templateSelection.addressIndex = addressIndex;
  if (state.templateSelection.addressIndex >= (config.addresses?.length || 0)) {
    state.templateSelection.addressIndex = 0;
  }
  state.templateSelection.addressPrefix = profileIdentity(config.addresses?.[state.templateSelection.addressIndex]);
}

function getSelectedTemplateMeta() {
  return getTemplateList().find((item) => item.name === state.templateSelection.templateName) || null;
}

function syncTemplateSource() {
  rememberTemplateDraft();
  const meta = getSelectedTemplateMeta();
  if (!meta) return;
  const draft = state.templateDrafts[meta.name];
  state.templateSourceDraft = draft?.dirty ? draft.source : meta.source || "";
  state.templateSourceDirty = Boolean(draft?.dirty);
  state.templateBaseSource = draft?.dirty ? draft.baseSource : meta.source || "";
  state.templateSourceName = meta.name;
  persistTemplateDraftSnapshot();
}

function updateTemplatePreview() {
  const panel = templatesContainer.querySelector(".side-stack");
  if (panel) panel.innerHTML = renderTemplatePreviewPanel();
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
  if (!force && templatesContainer.children.length &&
      (state.templateSourceDirty || templatesContainer.contains(document.activeElement))) {
    templateStatus.className = `status-message ${state.templateStatus.tone}`;
    templateStatus.textContent = state.templateStatus.message;
    return;
  }

  const config = getDraftConfig();
  ensureTemplateSelection();
  syncTemplateSource();

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
          <p class="helper-copy">Тест надсилається лише в тестовий чат бота, обраного для адреси. Робочий чат не використовується.</p>
          <button class="secondary-button" type="button" data-action="template-save">Зберегти шаблон</button>
          <button class="secondary-button" type="button" data-action="template-reload">Завантажити збережений текст</button>
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
  updateBusyControls();
}

async function previewSelectedTemplate({ sendTest = false } = {}) {
  if (state.templateBusy) return;
  const config = getDraftConfig();
  ensureTemplateSelection();
  const requestId = ++state.previewRequest;
  const revision = state.editorRevision;
  const body = { config, template_name: state.templateSelection.templateName,
    address_index: state.templateSelection.addressIndex, source_override: state.templateSourceDraft };
  state.templateBusy = true;
  updateBusyControls();
  try {
    const result = await fetchJson(sendTest ? "./api/templates/send-test" : "./api/templates/preview", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (requestId !== state.previewRequest || revision !== state.editorRevision ||
        body.template_name !== state.templateSelection.templateName ||
        body.address_index !== state.templateSelection.addressIndex ||
        body.source_override !== state.templateSourceDraft) {
      setTemplateStatus(sendTest ? "Тест оброблено для попередньої версії. Поточна чернетка не змінилася."
        : "Чернетка змінилася під час перевірки. Побудуйте актуальне прев'ю ще раз.", "status-warn");
      return;
    }
    state.templatePreview = result.preview;
    updateTemplatePreview();
    if (sendTest) {
      const sent = result.preview.sent;
      setTemplateStatus(sent?.ok ? `Тестове повідомлення надіслано в ${sent.chat_id}.`
        : "Прев'ю побудовано, але надсилання завершилося помилкою.", sent?.ok ? "status-good" : "status-bad");
    } else setTemplateStatus("Прев'ю оновлено. Текст чернетки не змінено.", "status-good");
  } finally { state.templateBusy = false; updateBusyControls(); }
}

async function writeTemplate(reset = false) {
  if (state.templateBusy) return;
  ensureTemplateSelection();
  const name = state.templateSelection.templateName;
  const source = state.templateSourceDraft;
  if (reset && !window.confirm("Повернути стандартний шаблон? Незбережений текст цього шаблону буде замінено.")) return;
  state.templateBusy = true;
  state.writeEpoch += 1;
  persistTemplateDraftSnapshot();
  updateBusyControls();
  try {
    const result = await fetchJson(reset ? "./api/templates/reset" : "./api/templates/save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template_name: name, source, expected_source: state.templateBaseSource }),
    });
    if (result.template?.name !== name || typeof result.template?.source !== "string") {
      throw new Error("Сервер не підтвердив збережений текст. Чернетку не змінено.");
    }
    rememberTemplateDraft();
    const draft = state.templateDrafts[name];
    const changed = draft && draft.source !== source;
    state.templateDrafts[name] = { source: changed ? draft.source : result.template.source,
      dirty: Boolean(changed), baseSource: result.template.source };
    const templates = getTemplateList();
    const index = templates.findIndex((item) => item.name === name);
    if (index >= 0) templates[index] = { ...templates[index], ...result.template };
    // Forget the previous active snapshot before reading the acknowledged draft.
    state.templateSourceName = "";
    syncTemplateSource();
    const textarea = document.getElementById("template-source");
    if (textarea && textarea.value !== state.templateSourceDraft) textarea.value = state.templateSourceDraft;
    state.templatePreview = null;
    updateTemplatePreview();
    setTemplateStatus(changed ? "Попередній текст збережено. Нові зміни залишилися у чернетці."
      : `${name}: ${reset ? "стандартний шаблон відновлено" : "збережено"}.`, changed ? "status-warn" : "status-good");
  } finally { state.templateBusy = false; state.writeEpoch += 1; updateBusyControls(); }
}

async function reloadTemplate() {
  if (state.templateBusy) return;
  const name = state.templateSelection.templateName;
  const requestId = state.previewRequest;
  if (state.templateSourceDirty && !window.confirm("Відкинути чернетку цього шаблону й завантажити збережений текст?")) return;
  state.templateBusy = true;
  updateBusyControls();
  try {
    const payload = await fetchJson("./api/templates");
    if (name !== state.templateSelection.templateName || requestId !== state.previewRequest) {
      setTemplateStatus("Під час завантаження чернетка змінилася. Ваш ввід збережено.", "status-warn");
      return;
    }
    state.templatesPayload = payload;
    delete state.templateDrafts[name];
    state.templateSourceName = "";
    state.templatePreview = null;
    renderTemplates(true);
    setTemplateStatus("Збережений текст завантажено.", "muted");
  } finally { state.templateBusy = false; updateBusyControls(); }
}

async function reloadConfig() {
  if (state.configBusy) return;
  if (state.editorDirty && !window.confirm("Відкинути незбережені зміни конфігурації та завантажити збережені?")) return;
  const revision = state.editorRevision;
  state.configBusy = true;
  state.writeEpoch += 1;
  updateBusyControls();
  try {
    const payload = await fetchJson("./api/config");
    if (revision !== state.editorRevision) {
      setEditorStatus("Під час завантаження чернетка змінилася. Ваш ввід збережено.", "status-warn");
      return;
    }
    state.configPayload = payload;
    state.draftConfig = cloneJson(payload.config);
    state.baseRevision = payload.revision;
    state.editorDirty = false;
    state.editorRevision += 1;
    state.templatePreview = null;
    clearEditorDraftSnapshot();
    renderEditor(true);
    renderTemplates(true);
    setEditorStatus("Збережені налаштування завантажено.", "muted");
  } finally { state.configBusy = false; state.writeEpoch += 1; updateBusyControls(); }
}

async function loadDashboard({ background = false } = {}) {
  if (state.dashboardLoading) return;
  state.dashboardLoading = true;
  const epoch = state.writeEpoch;

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
    renderStats();
    renderAddresses();
    renderDiscovery();
    renderDiagnostics();
    if (epoch !== state.writeEpoch || state.configBusy || state.templateBusy || state.botBusy) return;
    state.configPayload = config;
    renderBots();
    state.templatesPayload = templates;

    if ((!state.editorDirty && !editorContainer.contains(document.activeElement)) || !state.draftConfig) {
      state.draftConfig = cloneJson(config.config);
      state.baseRevision = config.revision;
    }

    const blockInteractiveRefresh = isInteractiveRefreshBlocked();

    if (!blockInteractiveRefresh || state.activeTab !== "configure") {
      renderEditor(false);
    }
    if (!blockInteractiveRefresh || state.activeTab !== "templates") {
      renderTemplates(false);
    }
    updateEditorLiveParts();
    setActiveTab(state.activeTab);

    lastUpdated.textContent = `Оновлено о ${new Date().toLocaleTimeString(UI_LOCALE)}`;
    if (!state.editorDirty) {
      setEditorStatus("Редактор готовий.", "muted");
    }
    if (!state.templateSourceDirty) {
      setTemplateStatus("Шаблони готові.", "muted");
    }
    if (!state.editorDirty) {
      clearEditorDraftSnapshot();
    }
    if (!state.templateSourceDirty) {
      persistTemplateDraftSnapshot();
    }
  } catch (error) {
    diagnosticsOutput.textContent = `Не вдалося завантажити dashboard:\n${error}`;
    lastUpdated.textContent = "Помилка оновлення";
    setEditorStatus(String(error), "status-bad");
    setTemplateStatus(String(error), "status-bad");
  } finally {
    state.dashboardLoading = false;
    updateBusyControls();
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
  const candidates = state.discovery?.candidates || [];
  const address = createBlankAddress(candidates.length === 1 ? candidates[0] : {});
  address.profile_id = Array.from(crypto.getRandomValues(new Uint8Array(16)), v => v.toString(16).padStart(2, "0")).join("");
  state.activeProfile = config.addresses.length;
  state.wizard = {id: address.profile_id, step: 0};
  config.addresses.push(address);
  setDraftConfig(
    config,
    "Новий профіль: оберіть джерело даних, одержувача та сповіщення.",
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
  if (target.classList.contains("recipient-picker")) return;
  const form = target.closest(".address-editor-card");
  if (["cfg-welcome", "cfg-receive-commands"].includes(target.id)) { markEditorChanged(); return; }
  if (form) {
    if (target.classList.contains("source-entity")) {
      const row = target.closest(".power-source");
      const entity = target.value;
      const kind = entity.startsWith("binary_sensor.") ? "binary" : getCatalogItems("frequency_sensors").some(s => s.entity_id === entity) ? "frequency" : "voltage";
      row.querySelector(".source-kind").value = kind;
      row.querySelector(".source-threshold").value = kind === "voltage" ? 50 : 1;
      row.querySelector(".source-freshness").value = kind === "binary" ? "state" : "heartbeat";
    }
    if (target.classList.contains("addr-account") && !form.querySelector(".addr-display-name").value.trim()) {
      const account = state.discovery?.accounts?.find(a => a.entity_id === target.value);
      if (account) form.querySelector(".addr-display-name").value = account.address;
    }
    if (target.classList.contains("addr-entity-prefix") && target instanceof HTMLSelectElement) {
      const displayNameInput = form.querySelector(".addr-display-name");
      const selectedOption = target.selectedOptions?.[0] || null;
      const suggestedName = selectedOption?.dataset.name || "";
      if (displayNameInput instanceof HTMLInputElement && !displayNameInput.value.trim() && suggestedName) {
        displayNameInput.value = suggestedName;
      }
    }
    syncAddressFormVisibility(form);
    markEditorChanged();
    setEditorStatus("Чернетку змінено. Запустіть перевірку, щоб оновити живе прев'ю.", "muted");
  }
});

editorContainer.addEventListener("input", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }
  if (target.classList.contains("recipient-picker")) return;
  if (target.closest(".address-editor-card") || target.id === "cfg-ha-token") {
    markEditorChanged();
  }
});

templatesContainer.addEventListener("change", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return;
  }
  if (target.id === "template-address-select") {
    state.templateSelection.addressIndex = Number.parseInt(target.value, 10) || 0;
    state.templateSelection.addressPrefix = profileIdentity(getDraftConfig().addresses?.[state.templateSelection.addressIndex]);
    state.templatePreview = null;
    persistTemplateDraftSnapshot();
    renderTemplates(true);
    setTemplateStatus("Адресу шаблону змінено. Побудуйте прев'ю знову, щоб використати поточну чернетку.", "muted");
  }
  if (target.id === "template-select") {
    persistTemplateDraftSnapshot();
    state.templateSelection.templateName = target.value;
    state.templatePreview = null;
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
    state.previewRequest += 1;
    state.templatePreview = null;
    updateTemplatePreview();
    persistTemplateDraftSnapshot();
    setTemplateStatus("Джерело шаблону змінено. Побудуйте прев'ю, щоб рендерити поточну чернетку.", "muted");
  }
});

editorContainer.addEventListener("keydown", (event) => {
  if (["Enter", " "].includes(event.key) && event.target.matches('.subpanel-head[data-action="toggle-section"]')) {
    event.preventDefault();
    event.target.click();
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

  if (action === "edit-profile") {
    if (state.editorDirty) persistEditorDraftSnapshot();
    state.activeProfile = Number(actionEl.dataset.index);
    editorContainer.querySelectorAll(".address-editor-card").forEach((form, index) => {
      form.querySelector(".profile-editor").classList.toggle("hidden", index !== state.activeProfile);
      form.querySelector('[data-action="edit-profile"]').setAttribute("aria-expanded", String(index === state.activeProfile));
    });
    return;
  }
  if (action === "wizard-next" || action === "wizard-back" || action === "wizard-step") {
    const form = actionEl.closest(".address-editor-card");
    const address = state.draftConfig.addresses[Number(form.dataset.index)];
    const step = form.classList.contains("wizard-profile") ? state.wizard.step : state.profileSteps[profileIdentity(address)] || 0;
    wizardStep(action === "wizard-step" ? Number(actionEl.dataset.step) : step + (action === "wizard-next" ? 1 : -1), form);
    return;
  }
  if (action === "show-all") {
    state.showAll = !state.showAll;
    writeSessionValue(`${UI_STORAGE_PREFIX}:show-all`, String(state.showAll));
    updateWizard(); return;
  }
  if (action === "connect-bot") {
    if (!mayReplaceBotForm()) return;
    state.botTarget = profileIdentity(state.draftConfig.addresses[Number(actionEl.closest(".address-editor-card").dataset.index)]);
    editBot();
    const dialog = document.getElementById("bot-dialog");
    dialog.querySelector(".bot-dialog-body").append(document.getElementById("bot-form"), document.getElementById("bot-status"));
    document.getElementById("bot-extra").open = false;
    dialog.showModal(); document.getElementById("bot-name").focus(); return;
  }
  if (action === "refresh-recipients") { void loadDashboard(); return; }
  if (action === "activate-bots") { void activateBots(); return; }
  if (action === "wizard-apply") {
    void saveConfig({restart: true}).catch(error => setEditorStatus(`Помилка застосування: ${error.message}`, "status-bad"));
    return;
  }
  if (action === "toggle-section") {
    if (target.closest(".toggle")) {
      return;
    }
    const subpanel = actionEl.closest(".subpanel");
    if (subpanel) {
      subpanel.classList.toggle("collapsed");
      actionEl.setAttribute("aria-expanded", String(!subpanel.classList.contains("collapsed")));
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
  if (action === "template-reload") {
    void reloadTemplate().catch((error) => setTemplateStatus(`Помилка завантаження: ${error.message}`, "status-bad"));
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
    void writeTemplate().catch((error) => {
      setTemplateStatus(`Помилка збереження: ${error.message}`, "status-bad");
    });
    return;
  }
  if (action === "template-reset") {
    void writeTemplate(true).catch((error) => {
      setTemplateStatus(`Помилка скидання: ${error.message}`, "status-bad");
    });
    return;
  }

  const config = getDraftConfig();
  const index = Number.parseInt(actionEl.dataset.index ?? "-1", 10);

  if (action === "remove-address" && index >= 0) {
    if (!window.confirm("Видалити адресу з чернетки? Для застосування потрібно зберегти налаштування.")) return;
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
    const copy = cloneJson(config.addresses[index]);
    copy.profile_id = Array.from(crypto.getRandomValues(new Uint8Array(16)), (v) => v.toString(16).padStart(2, "0")).join("");
    config.addresses.splice(index + 1, 0, copy);
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

document.getElementById("reload-config-button").addEventListener("click", () => {
  void reloadConfig().catch((error) => setEditorStatus(`Помилка завантаження: ${error.message}`, "status-bad"));
});
window.addEventListener("beforeunload", (event) => {
  if (state.editorDirty || state.botDirty || Object.values(state.templateDrafts).some((draft) => draft.dirty)) {
    event.preventDefault();
    event.returnValue = "";
  }
});
function botOptions(selected) {
  const profiles = state.configPayload?.bots?.profiles || [{id: "default", name: "Основний бот"}];
  const options = profiles.map((p) => `<option value="${escapeHtml(p.id)}" ${p.id === selected ? "selected" : ""}>${escapeHtml(p.name)}${p.username ? ` · @${escapeHtml(p.username)}` : ""}</option>`);
  if (!profiles.some((p) => p.id === selected)) options.push(`<option value="${escapeHtml(selected)}" selected>Недоступний профіль: ${escapeHtml(selected)}</option>`);
  return options.join("");
}

function recipientPicker(target, privateOnly = false) {
  return `<select class="recipient-picker" data-target="${target}" data-private-only="${privateOnly}" aria-label="Виявлені одержувачі"><option value="">Оберіть зі списку або введіть ID вище</option></select>`;
}

function updateRecipientPickers(root = document) {
  root.querySelectorAll(".recipient-picker").forEach((select) => {
    if (select === document.activeElement) return;
    const address = select.closest(".address-editor-card");
    const botId = address ? address.querySelector(".addr-bot-id")?.value : state.botEditingId;
    const tokenChanged = !address && Boolean(document.getElementById("bot-token")?.value);
    const peers = tokenChanged ? [] : (state.configPayload?.bots?.recipients?.[botId] || []);
    const visible = peers.filter(p => select.dataset.privateOnly !== "true" || p.type === "private");
    const types = {private: "👤", group: "👥", supergroup: "👥", channel: "📣"};
    const hint = visible.length ? "Оберіть зі списку або введіть ID вище" : "Ще немає одержувачів — введіть ID або напишіть боту";
    const html = `<option value="">${hint}</option>` + visible.map(p =>
      `<option value="${escapeHtml(p.id)}" ${p.available === 0 || p.available === false ? "disabled" : ""}>${escapeHtml(`${types[p.type] || ""} ${p.name || "Без назви"}${p.username ? ` · @${p.username}` : ""} · ${p.id}${!p.available ? " · недоступний" : ""}`)}</option>`
    ).join("");
    // Browsers normalize boolean attributes in innerHTML. Compare our source,
    // otherwise a refresh could clear a selection between input and change.
    if (select._recipientOptionsHtml !== html) {
      select.innerHTML = html;
      select._recipientOptionsHtml = html;
    }
    // Only the option list changes. Typed IDs and draft configuration stay intact.
  });
}

document.addEventListener("change", (event) => {
  const select = event.target;
  if (!(select instanceof HTMLSelectElement) || !select.classList.contains("recipient-picker") || !select.value) return;
  const root = select.closest(".address-editor-card") || document.getElementById("bot-form");
  const target = root.querySelector(select.dataset.target);
  if (!target) return;
  target.value = select.value;
  select.value = "";
  target.dispatchEvent(new Event("input", {bubbles: true}));
  target.dispatchEvent(new Event("change", {bubbles: true}));
});

function botStatus(message, bad = false) {
  const node = document.getElementById("bot-status");
  node.textContent = message;
  node.className = `status-message ${bad ? "status-bad" : "muted"}`;
}

function renderBots() {
  const snapshot = state.configPayload?.bots;
  if (!snapshot) return;
  if (state.botRevision === null) state.botRevision = snapshot.revision;
  const runtime = snapshot.runtime || [];
  document.getElementById("bot-list").innerHTML = snapshot.profiles.map((p) => {
    const active = runtime.find((item) => item.id === p.id);
    const error = active?.error || active?.delivery_error || "";
    return `<article class="editor-card"><h3>${escapeHtml(p.name)} ${p.username ? `· @${escapeHtml(p.username)}` : ""}</h3>
      <p>Чат: ${escapeHtml(p.chat_id || "вказується в адресі")}${p.test_mode ? ` · Тестовий режим → ${escapeHtml(p.test_chat_id)}` : ""}</p>
      ${error ? `<p class="status-bad">${escapeHtml(error)}</p>` : ""}
      ${p.id === "default" ? '<p class="helper-copy">Налаштовується у Home Assistant → Застосунки → DTEK Telegram Bot → Конфігурація.</p>' :
        `<div class="editor-toolbar"><button type="button" class="secondary-button" data-edit-bot="${escapeHtml(p.id)}">Редагувати</button><button type="button" class="secondary-button" data-delete-bot="${escapeHtml(p.id)}">Видалити</button></div>`}
      </article>`;
  }).join("");
  document.querySelectorAll(".addr-bot-id").forEach((select) => {
    if (select !== document.activeElement) select.innerHTML = botOptions(select.value);
  });
  document.querySelectorAll("[data-edit-bot], [data-delete-bot]").forEach((button) => { button.disabled = state.botBusy; });
  updateRecipientPickers();
}

function editBot(id = "") {
  state.botEditingId = id;
  state.botDirty = false;
  state.botRevision = state.configPayload?.bots?.revision || "";
  const profile = state.configPayload?.bots?.profiles.find((p) => p.id === id) || {};
  document.getElementById("bot-form").reset();
  document.getElementById("bot-name").value = profile.name || "";
  document.getElementById("bot-chat").value = profile.chat_id || "";
  document.getElementById("bot-test-chat").value = profile.test_chat_id || "";
  document.getElementById("bot-test-mode").checked = Boolean(profile.test_mode);
  document.getElementById("bot-feedback").checked = Boolean(profile.feedback_enabled);
  document.getElementById("bot-form-title").textContent = id ? `Редагувати ${profile.name || id}` : "Додати бота";
  document.getElementById("bot-name").focus();
  updateRecipientPickers(document.getElementById("bot-form"));
}

document.getElementById("bot-token").addEventListener("input", () => updateRecipientPickers(document.getElementById("bot-form")));

function mayReplaceBotForm() {
  return !state.botDirty ||
    window.confirm("Замінити поточні поля профілю? Незбережені зміни буде втрачено.");
}

document.getElementById("bot-new").addEventListener("click", () => { if (mayReplaceBotForm()) editBot(); });
document.getElementById("bot-reload").addEventListener("click", () => { if (mayReplaceBotForm()) editBot(state.botEditingId); });
document.getElementById("bot-list").addEventListener("click", (event) => {
  const edit = event.target.closest("[data-edit-bot]");
  if (edit && mayReplaceBotForm()) editBot(edit.dataset.editBot);
  const remove = event.target.closest("[data-delete-bot]");
  if (remove && window.confirm("Видалити профіль бота? Адреси не повинні його використовувати.")) {
    void saveBot(true, remove.dataset.deleteBot);
  }
});

document.getElementById("bot-form").addEventListener("input", () => { state.botDirty = true; });
document.getElementById("bot-form").addEventListener("submit", (event) => {
  event.preventDefault();
  void saveBot(false);
});

async function saveBot(remove = false, id = state.botEditingId) {
  if (state.botBusy || state.configBusy) return;
  const payload = remove ? {id, expected_revision: state.configPayload?.bots?.revision} : {
    id, expected_revision: state.botRevision,
    name: document.getElementById("bot-name").value.trim(),
    token: document.getElementById("bot-token").value.trim(),
    chat_id: document.getElementById("bot-chat").value.trim(),
    test_chat_id: document.getElementById("bot-test-chat").value.trim(),
    test_mode: document.getElementById("bot-test-mode").checked,
    feedback_enabled: document.getElementById("bot-feedback").checked,
  };
  state.botBusy = true;
  state.writeEpoch += 1;
  document.getElementById("bot-fields").disabled = true;
  renderBots();
  updateBusyControls();
  botStatus(remove ? "Видалення…" : "Перевіряємо токен у Telegram…");
  try {
    const result = await fetchJson(remove ? "./api/bots/delete" : "./api/bots/save", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    if (!Array.isArray(result.profiles) || !result.revision || (!remove && !result.profile?.id)) {
      throw new Error("Неочікувана відповідь сервера. Поля профілю збережено у формі; оновіть список перед повтором.");
    }
    state.configPayload.bots = result;
    if (!remove) {
      editBot(result.profile.id);
      if (state.botTarget) {
        const form = [...editorContainer.querySelectorAll(".address-editor-card")].find(f => profileIdentity(state.draftConfig.addresses[Number(f.dataset.index)]) === state.botTarget);
        if (form) { form.querySelector(".addr-bot-id").innerHTML = botOptions(result.profile.id); markEditorChanged(); }
        document.getElementById("bot-dialog").close();
      }
    }
    else if (state.botEditingId === id) editBot();
    botStatus("Збережено. Оберіть бота для адреси та натисніть «Застосувати».");
  } catch (error) {
    botStatus(error.message, true);
  } finally {
    // Credentials stay only in this form until a successful save or explicit reset.
    payload.token = "";
    state.botBusy = false;
    state.writeEpoch += 1;
    document.getElementById("bot-fields").disabled = false;
    renderBots();
    updateEditorLiveParts();
  }
}

document.getElementById("bot-dialog").addEventListener("close", () => {
  document.getElementById("bot-form-home").append(document.getElementById("bot-status"), document.getElementById("bot-form"));
  state.botTarget = null;
});
document.getElementById("bot-dialog-close").addEventListener("click", () => document.getElementById("bot-dialog").close());
async function activateBots() {
  if (state.botBusy || state.configBusy) return;
  if (state.botDirty) { setEditorStatus("Спочатку збережіть токен у формі бота.", "status-warn"); return; }
  if (state.editorDirty) persistEditorDraftSnapshot();
  state.configBusy = true; state.writeEpoch++; updateBusyControls();
  try {
    await fetchJson("./api/bots/activate", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({expected_revision:state.configPayload.bots.revision, config_revision:state.configPayload.revision})});
    setEditorStatus("Перезапуск заплановано. Збережені профілі відновлять роботу; чернетку не застосовано. Зачекайте й оновіть одержувачів.", "status-warn");
  } catch (error) { setEditorStatus(error.message, "status-bad"); }
  finally { state.configBusy=false; state.writeEpoch++; updateBusyControls(); }
}
updateBusyControls();
restorePersistedUiState();
setActiveTab(state.activeTab);
void loadDashboard();
window.setInterval(() => {
  void loadDashboard({ background: true });
}, REFRESH_INTERVAL_MS);
