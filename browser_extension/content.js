const STORAGE_KEY = "taskcalendarBridgeConfig";
const DEFAULT_BRIDGE_URL = "http://127.0.0.1:18452";
const BUTTON_CLASS = "taskcalendar-import-button";
const observer = new MutationObserver(() => scheduleScan());

let cachedConfig = null;
let scanTimer = null;

bootstrap();

function bootstrap() {
  if (document.documentElement) {
    observer.observe(document.documentElement, { childList: true, subtree: true });
  }
  window.addEventListener("load", scheduleScan, { once: true });
  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName === "local" && changes[STORAGE_KEY]) {
      cachedConfig = null;
      scheduleScan();
    }
  });
  scheduleScan();
}

function scheduleScan() {
  if (scanTimer !== null) {
    clearTimeout(scanTimer);
  }
  scanTimer = window.setTimeout(() => {
    scanTimer = null;
    void runScan();
  }, 120);
}

async function runScan() {
  const config = await loadConfig();
  const activeRuleIds = new Set();

  for (const rule of config.rules) {
    if (!rule || !rule.id) {
      continue;
    }
    if (!rule.enabled || !matchesRule(rule)) {
      removeButton(rule.id);
      continue;
    }
    activeRuleIds.add(rule.id);
    ensureButton(rule);
  }

  for (const button of document.querySelectorAll(`.${BUTTON_CLASS}`)) {
    if (!activeRuleIds.has(button.dataset.ruleId || "")) {
      button.remove();
    }
  }
}

async function loadConfig() {
  if (cachedConfig) {
    return cachedConfig;
  }
  const stored = await chrome.storage.local.get(STORAGE_KEY);
  cachedConfig = normalizeConfig(stored[STORAGE_KEY]);
  return cachedConfig;
}

function normalizeConfig(rawConfig) {
  const config = rawConfig && typeof rawConfig === "object" ? rawConfig : {};
  return {
    bridgeBaseUrl: String(config.bridgeBaseUrl || DEFAULT_BRIDGE_URL).trim() || DEFAULT_BRIDGE_URL,
    rules: Array.isArray(config.rules) ? config.rules : []
  };
}

function matchesRule(rule) {
  const urlPatterns = toStringArray(rule.urlPatterns);
  if (urlPatterns.length > 0 && !urlPatterns.some((pattern) => safeRegexTest(pattern, location.href))) {
    return false;
  }

  const requiredSelectors = toStringArray(rule.requiredSelectors);
  if (requiredSelectors.some((selector) => !safeQuery(selector))) {
    return false;
  }

  const forbiddenSelectors = toStringArray(rule.forbiddenSelectors);
  if (forbiddenSelectors.some((selector) => safeQuery(selector))) {
    return false;
  }

  const bodyText = normalizeText(document.body?.innerText || "");
  const requiredTexts = toStringArray(rule.requiredTexts);
  if (requiredTexts.some((text) => !bodyText.includes(normalizeText(text)))) {
    return false;
  }

  return Boolean(safeQuery(rule.titleSelector));
}

function ensureButton(rule) {
  const existing = document.querySelector(`.${BUTTON_CLASS}[data-rule-id="${rule.id}"]`);
  if (existing && existing.isConnected) {
    return;
  }

  const container = safeQuery(rule.buttonContainerSelector || rule.titleSelector);
  if (!container) {
    return;
  }

  const button = document.createElement("button");
  button.type = "button";
  button.className = BUTTON_CLASS;
  button.dataset.ruleId = rule.id;
  button.dataset.defaultLabel = rule.buttonText || "일정 추가하기";
  button.textContent = button.dataset.defaultLabel;
  button.addEventListener("click", () => {
    void handleImportClick(rule, button);
  });

  if (isVoidElement(container)) {
    container.insertAdjacentElement("afterend", button);
  } else {
    container.appendChild(button);
  }
}

function removeButton(ruleId) {
  const button = document.querySelector(`.${BUTTON_CLASS}[data-rule-id="${ruleId}"]`);
  if (button) {
    button.remove();
  }
}

async function handleImportClick(rule, button) {
  const payload = buildPayload(rule);
  if (!payload.title) {
    setButtonState(button, "error", "제목 없음");
    restoreButton(button, 2000);
    return;
  }

  button.disabled = true;
  setButtonState(button, "", "추가 중...");

  const response = await chrome.runtime.sendMessage({
    type: "import-schedule",
    payload
  }).catch((error) => ({
    ok: false,
    error: error instanceof Error ? error.message : String(error)
  }));

  if (!response?.ok) {
    setButtonState(button, "error", "실패");
    button.title = response?.error || "TaskCalendar bridge error";
    restoreButton(button, 2200);
    return;
  }

  if (response.data?.duplicated) {
    setButtonState(button, "duplicate", "이미 추가됨");
  } else {
    setButtonState(button, "success", "추가됨");
  }
  button.title = response.data?.day ? `등록 날짜: ${response.data.day}` : "";
  restoreButton(button, 1800);
}

function buildPayload(rule) {
  const title = readText(rule.titleSelector);
  const description = collectDescription(rule.descriptionSelectors);
  const dateText = [
    readText(rule.startDateSelector),
    readText(rule.endDateSelector),
    readText(rule.dateSelector),
    readText(rule.timeSelector)
  ].filter(Boolean).join(" ");
  const dateCandidates = extractIsoDates(dateText);
  const startDateText = readText(rule.startDateSelector);
  const endDateText = readText(rule.endDateSelector);
  const explicitStartDates = extractIsoDates(startDateText);
  const explicitEndDates = extractIsoDates(endDateText);
  const startDate = explicitStartDates[0] || dateCandidates[0] || todayIso();
  const endDate = explicitEndDates[0] || dateCandidates[1] || explicitStartDates[0] || startDate;

  const timeTexts = {
    start: readText(rule.startTimeSelector),
    end: readText(rule.endTimeSelector),
    shared: readText(rule.timeSelector) || dateText
  };
  const sharedTimes = extractTimes(timeTexts.shared);
  const startTimes = extractTimes(timeTexts.start);
  const endTimes = extractTimes(timeTexts.end);
  const startTime = startTimes[0] || sharedTimes[0] || "";
  const endTime = endTimes[0] || sharedTimes[1] || "";
  const allDay = Boolean(rule.allDay) || !startTime;
  const fingerprintSeed = readText(rule.fingerprintSelector) || `${title}|${startDate}|${startTime}|${location.href}`;

  return {
    title,
    description,
    start_date: startDate,
    end_date: endDate,
    start_time: startTime,
    end_time: endTime,
    all_day: allDay,
    icon_type: String(rule.iconType || ""),
    alert_type: String(rule.alertType || "none"),
    alert_offset: String(rule.alertOffset || "at_start"),
    source_url: location.href,
    source_origin: String(rule.id || ""),
    source_fingerprint: `${String(rule.fingerprintPrefix || rule.id || "")}|${fingerprintSeed}`.trim()
  };
}

function setButtonState(button, state, label) {
  if (state) {
    button.dataset.state = state;
  } else {
    delete button.dataset.state;
  }
  button.textContent = label;
}

function restoreButton(button, delayMs) {
  window.setTimeout(() => {
    if (!button.isConnected) {
      return;
    }
    button.disabled = false;
    delete button.dataset.state;
    button.textContent = button.dataset.defaultLabel || "일정 추가하기";
  }, delayMs);
}

function collectDescription(selectors) {
  const values = [];
  for (const selector of toStringArray(selectors)) {
    const value = readText(selector);
    if (value && !values.includes(value)) {
      values.push(value);
    }
  }
  return values.join("\n");
}

function readText(selector) {
  const node = safeQuery(selector);
  if (!node) {
    return "";
  }
  if ("value" in node && typeof node.value === "string") {
    return normalizeText(node.value);
  }
  return normalizeText(node.textContent || "");
}

function safeQuery(selector) {
  if (!selector || typeof selector !== "string") {
    return null;
  }
  try {
    return document.querySelector(selector);
  } catch (_error) {
    return null;
  }
}

function safeRegexTest(pattern, value) {
  try {
    return new RegExp(pattern).test(value);
  } catch (_error) {
    return false;
  }
}

function toStringArray(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item || "").trim()).filter(Boolean);
}

function normalizeText(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function todayIso() {
  const now = new Date();
  return `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`;
}

function extractIsoDates(text) {
  const normalized = normalizeText(text);
  if (!normalized) {
    return [];
  }

  const values = [];
  const yearMatches = normalized.matchAll(/(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*(?:일)?/g);
  for (const match of yearMatches) {
    values.push(toIsoDate(Number(match[1]), Number(match[2]), Number(match[3])));
  }

  if (values.length > 0) {
    return unique(values);
  }

  const now = new Date();
  const monthDayMatches = normalized.matchAll(/(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*(?:일)?/g);
  for (const match of monthDayMatches) {
    values.push(toIsoDate(now.getFullYear(), Number(match[1]), Number(match[2])));
  }
  return unique(values);
}

function extractTimes(text) {
  const normalized = normalizeText(text);
  if (!normalized) {
    return [];
  }
  const values = [];
  const matches = normalized.matchAll(/(오전|오후)?\s*(\d{1,2})(?::|시)\s*(\d{1,2})?\s*(?:분)?/g);
  for (const match of matches) {
    let hour = Number(match[2]);
    const minute = Number(match[3] || 0);
    const meridiem = match[1] || "";
    if (Number.isNaN(hour) || Number.isNaN(minute) || minute < 0 || minute > 59) {
      continue;
    }
    if (meridiem === "오전" && hour === 12) {
      hour = 0;
    } else if (meridiem === "오후" && hour < 12) {
      hour += 12;
    }
    if (hour < 0 || hour > 23) {
      continue;
    }
    values.push(`${pad2(hour)}:${pad2(minute)}`);
  }
  return unique(values);
}

function unique(values) {
  return [...new Set(values.filter(Boolean))];
}

function toIsoDate(year, month, day) {
  if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) {
    return "";
  }
  if (month < 1 || month > 12 || day < 1 || day > 31) {
    return "";
  }
  return `${year}-${pad2(month)}-${pad2(day)}`;
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

function isVoidElement(node) {
  return /^(AREA|BASE|BR|COL|EMBED|HR|IMG|INPUT|LINK|META|PARAM|SOURCE|TRACK|WBR)$/.test(node.tagName);
}
