const STORAGE_KEY = "taskcalendarBridgeConfig";
const editor = document.getElementById("config-editor");
const saveButton = document.getElementById("save-button");
const resetButton = document.getElementById("reset-button");
const statusNode = document.getElementById("status");

const DEFAULT_CONFIG = {
  bridgeBaseUrl: "http://127.0.0.1:18452",
  rules: [
    {
      id: "sample-page",
      enabled: false,
      urlPatterns: ["https://example.com/.*"],
      requiredSelectors: ["h1.page-title", ".schedule-date"],
      requiredTexts: [],
      forbiddenSelectors: [],
      titleSelector: "h1.page-title",
      buttonContainerSelector: "h1.page-title",
      descriptionSelectors: [".schedule-summary", ".teacher-name"],
      dateSelector: ".schedule-date",
      timeSelector: ".schedule-time",
      allDay: false,
      iconType: "important",
      alertType: "popup",
      alertOffset: "30m",
      fingerprintSelector: "[data-item-id]",
      fingerprintPrefix: "sample-page",
      buttonText: "일정 추가하기"
    }
  ]
};

void initialize();

async function initialize() {
  const stored = await chrome.storage.local.get(STORAGE_KEY);
  const config = stored[STORAGE_KEY] || cloneDefaultConfig();
  editor.value = JSON.stringify(config, null, 2);
}

saveButton.addEventListener("click", async () => {
  try {
    const parsed = JSON.parse(editor.value);
    validateConfig(parsed);
    await chrome.storage.local.set({ [STORAGE_KEY]: parsed });
    setStatus("저장되었습니다.", false);
  } catch (error) {
    setStatus(error instanceof Error ? error.message : String(error), true);
  }
});

resetButton.addEventListener("click", async () => {
  const sample = cloneDefaultConfig();
  editor.value = JSON.stringify(sample, null, 2);
  await chrome.storage.local.set({ [STORAGE_KEY]: sample });
  setStatus("샘플 설정으로 되돌렸습니다.", false);
});

function validateConfig(config) {
  if (!config || typeof config !== "object") {
    throw new Error("최상위 JSON 객체가 필요합니다.");
  }
  if (!("bridgeBaseUrl" in config) || typeof config.bridgeBaseUrl !== "string") {
    throw new Error("bridgeBaseUrl 문자열이 필요합니다.");
  }
  if (!Array.isArray(config.rules)) {
    throw new Error("rules 배열이 필요합니다.");
  }
  for (const [index, rule] of config.rules.entries()) {
    if (!rule || typeof rule !== "object") {
      throw new Error(`rules[${index}] 는 객체여야 합니다.`);
    }
    if (!rule.id || typeof rule.id !== "string") {
      throw new Error(`rules[${index}].id 가 필요합니다.`);
    }
    if (!rule.titleSelector || typeof rule.titleSelector !== "string") {
      throw new Error(`rules[${index}].titleSelector 가 필요합니다.`);
    }
  }
}

function setStatus(message, isError) {
  statusNode.textContent = message;
  statusNode.style.color = isError ? "#c0392b" : "#1f7a67";
}

function cloneDefaultConfig() {
  return JSON.parse(JSON.stringify(DEFAULT_CONFIG));
}
