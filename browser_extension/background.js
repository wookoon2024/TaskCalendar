const STORAGE_KEY = "taskcalendarBridgeConfig";

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

chrome.runtime.onInstalled.addListener(async () => {
  const stored = await chrome.storage.local.get(STORAGE_KEY);
  if (!stored[STORAGE_KEY]) {
    await chrome.storage.local.set({ [STORAGE_KEY]: cloneDefaultConfig() });
  }
});

chrome.action.onClicked.addListener(() => {
  chrome.runtime.openOptionsPage();
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "import-schedule") {
    return false;
  }

  (async () => {
    try {
      const stored = await chrome.storage.local.get(STORAGE_KEY);
      const config = stored[STORAGE_KEY] || cloneDefaultConfig();
      const bridgeBaseUrl = normalizeBridgeBaseUrl(config.bridgeBaseUrl || DEFAULT_CONFIG.bridgeBaseUrl);
      const response = await fetch(`${bridgeBaseUrl}/api/import-schedule`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify(message.payload || {})
      });
      const data = await response.json().catch(() => ({ ok: false, error: "invalid_bridge_response" }));
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || `bridge_error_${response.status}`);
      }
      sendResponse({ ok: true, data });
    } catch (error) {
      sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) });
    }
  })();

  return true;
});

function cloneDefaultConfig() {
  return JSON.parse(JSON.stringify(DEFAULT_CONFIG));
}

function normalizeBridgeBaseUrl(rawValue) {
  const text = String(rawValue || "").trim();
  const baseUrl = text || DEFAULT_CONFIG.bridgeBaseUrl;
  return baseUrl.replace(/\/+$/, "");
}
