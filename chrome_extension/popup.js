document.addEventListener('DOMContentLoaded', () => {
  const today = new Date();
  const dateStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;

  const FIELDS = ['title', 'category', 'author', 'status', 'date', 'desc', 'url'];

  // DOM 요소 참조
  const els = {
    headerRow: document.getElementById('header-row'),
    viewRegister: document.getElementById('view-register'),
    viewConfig: document.getElementById('view-config'),
    btnToggleConfig: document.getElementById('btn-toggle-config'),
    siteRuleBadge: document.getElementById('site-rule-badge'),
    siteDomainLabel: document.getElementById('site-domain-label'),
    cfgDomainBadge: document.getElementById('cfg-domain-badge'),

    // 탭 및 등록 폼
    btnSchedule: document.getElementById('btn-schedule'),
    btnTask: document.getElementById('btn-task'),
    btnMemo: document.getElementById('btn-memo'),
    itemDate: document.getElementById('item-date'),
    itemCategory: document.getElementById('item-category'),
    itemAuthor: document.getElementById('item-author'),
    itemStatus: document.getElementById('item-status'),
    chkTitle: document.getElementById('chk-title'),
    chkCategory: document.getElementById('chk-category'),
    chkAuthor: document.getElementById('chk-author'),
    chkStatus: document.getElementById('chk-status'),
    chkDesc: document.getElementById('chk-desc'),
    chkDate: document.getElementById('chk-date'),
    chkUrl: document.getElementById('chk-url'),
    valTitle: document.getElementById('val-title'),
    valCategory: document.getElementById('val-category'),
    valAuthor: document.getElementById('val-author'),
    valStatus: document.getElementById('val-status'),
    valDesc: document.getElementById('val-desc'),
    valDate: document.getElementById('val-date'),
    valUrl: document.getElementById('val-url'),
    btnCancel: document.getElementById('btn-cancel-action'),
    btnSubmit: document.getElementById('btn-submit-action'),

    // 설정 뷰 액션 버튼
    btnCfgCancel: document.getElementById('btn-cfg-cancel'),
    btnCfgReset: document.getElementById('btn-cfg-reset'),
    btnCfgSave: document.getElementById('btn-cfg-save')
  };

  els.valDate.value = dateStr;

  let currentDomain = '';
  let originTabId = null;
  let currentData = {};
  let currentType = 'schedule';

  // 현재 도메인의 맞춤 규칙 및 사용자정의 서식 상태
  let activeRules = {};
  let activeTemplates = {};
  let ruleSamples = {};

  // ========== 등록 뷰: 탭 전환 ==========
  function switchType(val, save = true) {
    if (!val || (val !== 'schedule' && val !== 'task' && val !== 'memo')) {
      val = 'schedule';
    }
    currentType = val;

    els.btnSchedule.classList.toggle('active', val === 'schedule');
    els.btnTask.classList.toggle('active', val === 'task');
    els.btnMemo.classList.toggle('active', val === 'memo');

    if (val === 'schedule') {
      els.itemDate.style.display = 'flex';
      els.itemCategory.style.display = 'none';
      els.itemAuthor.style.display = 'none';
      els.itemStatus.style.display = 'none';
      els.valDesc.style.height = '180px';
    } else if (val === 'task') {
      els.itemDate.style.display = 'flex';
      els.itemCategory.style.display = 'flex';
      els.itemAuthor.style.display = 'flex';
      els.itemStatus.style.display = 'flex';
      els.valDesc.style.height = '85px';
    } else {
      els.itemDate.style.display = 'none';
      els.itemCategory.style.display = 'none';
      els.itemAuthor.style.display = 'none';
      els.itemStatus.style.display = 'none';
      els.valDesc.style.height = '215px';
    }

    if (save) {
      chrome.storage.local.set({ lastSelectedType: val });
    }
  }

  [els.btnSchedule, els.btnTask, els.btnMemo].forEach(btn => {
    if (btn) {
      btn.addEventListener('click', () => {
        const type = btn.getAttribute('data-type');
        switchType(type, true);
      });
    }
  });

  // ========== 뷰 전환 (등록 ↔ 사이트 맞춤 설정) ==========
  function switchView(target) {
    if (target === 'config') {
      els.headerRow.style.display = 'none';
      els.siteRuleBadge.style.display = 'none';
      els.viewRegister.style.display = 'none';
      els.viewConfig.style.display = 'flex';
      populateConfigView();
    } else {
      els.headerRow.style.display = 'flex';
      els.viewRegister.style.display = 'flex';
      els.viewConfig.style.display = 'none';
      updateRuleBadgeVisibility(!!currentData.hasCustomRule);
    }
  }

  els.btnToggleConfig.addEventListener('click', () => switchView('config'));
  els.btnCfgCancel.addEventListener('click', () => switchView('register'));

  // ========== 서식 치환 헬퍼 (미리보기용) ==========
  function renderTemplatePreview(templateStr) {
    if (!templateStr) return '';
    const base = {
      '제목': currentData.linkText || (activeRules.title && activeRules.title !== '__none__' ? '예시 제목' : ''),
      'title': currentData.linkText || '',
      '분류': currentData.category || (activeRules.category && activeRules.category !== '__none__' ? '예시 분류' : ''),
      'category': currentData.category || '',
      '기안자': currentData.author || '',
      '작성자': currentData.author || '',
      'author': currentData.author || '',
      '상태': currentData.status || '등록',
      'status': currentData.status || '등록',
      '날짜': currentData.detectedDate || els.valDate.value || dateStr,
      'date': currentData.detectedDate || els.valDate.value || dateStr,
      '내용': currentData.selectedText || currentData.descOverride || '',
      'desc': currentData.selectedText || currentData.descOverride || '',
      '출처': currentData.linkUrl || currentData.pageUrl || '',
      'url': currentData.linkUrl || currentData.pageUrl || ''
    };

    return templateStr.replace(/\{([^{}]+)\}/g, (match, key) => {
      const k = key.trim().toLowerCase();
      if (k === '제목' || k === 'title') return base.title;
      if (k === '분류' || k === 'category') return base.category;
      if (k === '기안자' || k === '작성자' || k === 'author') return base.author;
      if (k === '상태' || k === 'status') return base.status;
      if (k === '날짜' || k === 'date') return base.date;
      if (k === '내용' || k === '본문' || k === 'desc') return base.desc;
      if (k === '출처' || k === '링크' || k === 'url') return base.url;
      return match;
    });
  }

  // ========== 데이터 폼 채우기 ==========
  function applyExtractedDataToForm(data) {
    if (!data) return;
    currentData = data;
    originTabId = data.originTabId || originTabId;
    currentDomain = data.siteDomain || currentDomain || '';

    // 도메인 라벨 업데이트
    if (currentDomain) {
      els.cfgDomainBadge.textContent = currentDomain;
      els.siteDomainLabel.textContent = `(${currentDomain})`;
    }

    // 제목
    if (activeRules.title === '__none__') {
      els.valTitle.value = '';
      els.chkTitle.checked = false;
    } else if (data.linkText) {
      els.valTitle.value = data.linkText;
      els.chkTitle.checked = true;
    } else if (data.selectedText && data.selectedText.length <= 150 && !data.selectedText.includes('\n')) {
      els.valTitle.value = data.selectedText.trim();
      els.chkTitle.checked = true;
    } else {
      els.valTitle.value = data.pageTitle || '';
      els.chkTitle.checked = true;
    }

    // 작성자 / 기안자
    if (activeRules.author === '__none__') {
      els.valAuthor.value = '';
      els.chkAuthor.checked = false;
    } else {
      els.valAuthor.value = data.author || '';
      els.chkAuthor.checked = !!data.author;
    }

    // 분류
    if (activeRules.category === '__none__') {
      els.valCategory.value = '';
      els.chkCategory.checked = false;
    } else {
      els.valCategory.value = data.category || '일반';
      els.chkCategory.checked = true;
    }

    // 상태
    if (activeRules.status === '__none__') {
      els.chkStatus.checked = false;
    } else if (data.status) {
      let found = false;
      for (let i = 0; i < els.valStatus.options.length; i++) {
        if (els.valStatus.options[i].value === data.status) {
          els.valStatus.selectedIndex = i;
          found = true;
          break;
        }
      }
      if (!found && data.status.trim()) {
        const opt = document.createElement('option');
        opt.value = data.status.trim();
        opt.textContent = data.status.trim();
        opt.selected = true;
        els.valStatus.appendChild(opt);
      }
      els.chkStatus.checked = true;
    } else {
      els.valStatus.value = '등록';
      els.chkStatus.checked = true;
    }

    // 내용
    if (activeRules.desc === '__none__') {
      els.valDesc.value = '';
      els.chkDesc.checked = false;
    } else if (data.descOverride) {
      els.valDesc.value = data.descOverride;
      els.chkDesc.checked = true;
    } else if (data.selectedText && data.selectedText.trim() !== els.valTitle.value.trim()) {
      els.valDesc.value = data.selectedText;
      els.chkDesc.checked = true;
    } else if (data.metaText) {
      els.valDesc.value = data.metaText;
      els.chkDesc.checked = true;
    } else {
      els.valDesc.value = '';
      els.chkDesc.checked = false;
    }

    // 날짜
    if (activeRules.date === '__none__') {
      els.valDate.value = '';
      els.chkDate.checked = false;
    } else if (data.detectedDate) {
      els.valDate.value = data.detectedDate;
      els.chkDate.checked = true;
    } else {
      els.valDate.value = dateStr;
      els.chkDate.checked = true;
    }

    // 출처 URL
    if (activeRules.url === '__none__') {
      els.valUrl.value = '';
      els.chkUrl.checked = false;
    } else {
      els.valUrl.value = data.linkUrl || data.pageUrl || '';
      els.chkUrl.checked = !!els.valUrl.value;
    }

    // 맞춤 규칙 적용 배지 표시 여부
    updateRuleBadgeVisibility(!!data.hasCustomRule);

    syncCheckboxState();
  }

  function updateRuleBadgeVisibility(hasRule) {
    if (hasRule && currentDomain) {
      els.siteRuleBadge.style.display = 'flex';
      els.btnToggleConfig.style.background = '#EDE9FE';
      els.btnToggleConfig.style.borderColor = '#C4B5FD';
      els.btnToggleConfig.textContent = '⚙️ 맞춤 규칙 수정';
    } else {
      els.siteRuleBadge.style.display = 'none';
      els.btnToggleConfig.style.background = '#EDE9FE';
      els.btnToggleConfig.style.borderColor = '#DDD6FE';
      els.btnToggleConfig.textContent = '⚙️ 사이트 맞춤';
    }
  }

  function loadFormData() {
    chrome.storage.local.get(['taskCalendarData', 'lastSelectedType', 'tc_site_rules'], (result) => {
      const savedType = result.lastSelectedType || 'schedule';
      switchType(savedType, false);

      const allSiteRules = result.tc_site_rules || {};

      if (!result.taskCalendarData) return;
      const data = result.taskCalendarData;

      currentDomain = data.siteDomain || '';
      if (currentDomain && allSiteRules[currentDomain]) {
        const saved = allSiteRules[currentDomain];
        activeRules = Object.assign({}, saved.selectors || saved);
        activeTemplates = Object.assign({}, saved.templates || {});
        ruleSamples = Object.assign({}, saved.samples || {});
        data.hasCustomRule = Object.keys(activeRules).some(k => activeRules[k]) || Object.keys(activeTemplates).some(k => activeTemplates[k]);
      } else {
        activeRules = {};
        activeTemplates = {};
        ruleSamples = {};
      }

      applyExtractedDataToForm(data);
    });
  }

  loadFormData();

  // 다른 항목 우클릭 시 실시간 갱신
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.action === "reloadTaskCalendarData") {
      loadFormData();
    }
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.taskCalendarData) {
      loadFormData();
    }
  });

  // 체크박스 ↔ 비활성화 연동
  function syncCheckboxState() {
    els.valTitle.disabled = !els.chkTitle.checked;
    els.valCategory.disabled = !els.chkCategory.checked;
    els.valAuthor.disabled = !els.chkAuthor.checked;
    els.valStatus.disabled = !els.chkStatus.checked;
    els.valDesc.disabled = !els.chkDesc.checked;
    els.valDate.disabled = !els.chkDate.checked;
    els.valUrl.disabled = !els.chkUrl.checked;
  }
  [els.chkTitle, els.chkCategory, els.chkAuthor, els.chkStatus, els.chkDesc, els.chkDate, els.chkUrl].forEach(chk => {
    chk.addEventListener('change', syncCheckboxState);
  });

  // ========== 사이트 맞춤 설정 뷰 렌더링 & 서식 상태 갱신 ==========
  function updateCardPreview(field) {
    const statusPill = document.getElementById(`cfg-status-${field}`);
    const prevBox = document.getElementById(`cfg-prev-${field}`);
    const prevText = document.getElementById(`cfg-prev-text-${field}`);
    const prevSel = document.getElementById(`cfg-prev-sel-${field}`);
    const tplInput = document.getElementById(`cfg-tpl-${field}`);

    const tpl = (activeTemplates[field] || '').trim();
    const sel = activeRules[field] || '';

    // 1) 서식(사용자정의)이 설정되어 있는 경우
    if (tpl) {
      statusPill.className = 'cfg-status-pill pill-custom';
      statusPill.textContent = '✏️ 사용자정의';
      prevBox.style.display = 'flex';
      const previewVal = renderTemplatePreview(tpl);
      prevText.textContent = `치환 결과: "${previewVal}"`;
      prevSel.textContent = `서식: ${tpl}${sel && sel !== '__none__' ? ` (기준 선택자: ${sel})` : ''}`;
      return;
    }

    // 2) 없음(__none__)인 경우
    if (sel === '__none__') {
      statusPill.className = 'cfg-status-pill pill-none';
      statusPill.textContent = '⚪ 해당없음';
      prevBox.style.display = 'flex';
      prevText.textContent = '해당 항목은 추출하지 않고 건너뜁니다.';
      prevSel.textContent = '(추출 제외 / 공백 처리)';
      return;
    }

    // 3) 선택자가 매칭된 경우
    if (sel) {
      statusPill.className = 'cfg-status-pill pill-matched';
      statusPill.textContent = '✅ 매칭 완료';
      prevBox.style.display = 'flex';
      prevText.textContent = `매칭 내용: "${ruleSamples[field] || ''}"`;
      prevSel.textContent = `선택자: ${sel}`;
      return;
    }

    // 4) 미설정
    statusPill.className = 'cfg-status-pill pill-empty';
    statusPill.textContent = '미설정';
    prevBox.style.display = 'none';
  }

  function populateConfigView() {
    els.cfgDomainBadge.textContent = currentDomain || '현재 사이트';

    FIELDS.forEach(field => {
      const sampleInput = document.getElementById(`cfg-sample-${field}`);
      const tplInput = document.getElementById(`cfg-tpl-${field}`);
      const tplPanel = document.getElementById(`cfg-tpl-panel-${field}`);
      const btnNone = document.querySelector(`.btn-cfg-none[data-field="${field}"]`);
      const btnCustom = document.querySelector(`.btn-cfg-custom[data-field="${field}"]`);

      if (btnNone) btnNone.classList.remove('active');
      if (btnCustom) btnCustom.classList.remove('active');

      // 예시 텍스트 복원
      if (ruleSamples[field]) {
        sampleInput.value = ruleSamples[field];
      } else if (!sampleInput.value.trim()) {
        if (field === 'title') sampleInput.value = els.valTitle.value.trim();
        else if (field === 'category' && els.valCategory.value !== '일반') sampleInput.value = els.valCategory.value.trim();
        else if (field === 'author') sampleInput.value = els.valAuthor.value.trim();
        else if (field === 'status' && els.valStatus.value !== '등록') sampleInput.value = els.valStatus.value;
        else if (field === 'date') sampleInput.value = els.valDate.value;
        else if (field === 'url') sampleInput.value = els.valUrl.value;
      }

      // 서식 텍스트 복원
      if (activeTemplates[field]) {
        if (tplInput) tplInput.value = activeTemplates[field];
        if (tplPanel) tplPanel.style.display = 'flex';
        if (btnCustom) btnCustom.classList.add('active');
      } else {
        if (tplInput) tplInput.value = '';
        if (tplPanel) tplPanel.style.display = 'none';
      }

      if (activeRules[field] === '__none__' && btnNone) {
        btnNone.classList.add('active');
        sampleInput.placeholder = '(이 사이트에는 해당 항목 없음 - 건너뜀)';
      }

      updateCardPreview(field);
    });
  }

  // 각 필드의 [검색] 버튼 클릭 이벤트 바인딩
  document.querySelectorAll('.btn-cfg-search').forEach(btn => {
    btn.addEventListener('click', () => {
      const field = btn.getAttribute('data-field');
      const sampleInput = document.getElementById(`cfg-sample-${field}`);
      const statusPill = document.getElementById(`cfg-status-${field}`);
      const btnNone = document.querySelector(`.btn-cfg-none[data-field="${field}"]`);

      if (btnNone) btnNone.classList.remove('active');

      const sampleText = sampleInput.value.trim();
      if (!sampleText) {
        statusPill.className = 'cfg-status-pill pill-fail';
        statusPill.textContent = '❌ 검색어 입력 필요';
        sampleInput.focus();
        return;
      }

      btn.disabled = true;
      statusPill.className = 'cfg-status-pill pill-empty';
      statusPill.textContent = '🔍 검색 중...';

      chrome.runtime.sendMessage({
        action: "matchElementInTab",
        tabId: originTabId,
        sampleText: sampleText,
        isUrl: (field === 'url')
      }, (res) => {
        btn.disabled = false;
        if (!res || !res.success) {
          statusPill.className = 'cfg-status-pill pill-fail';
          statusPill.textContent = '❌ 일치 항목 없음';
          return;
        }

        // 매칭 성공
        activeRules[field] = res.selector;
        ruleSamples[field] = sampleText;

        updateCardPreview(field);
      });
    });
  });

  // 각 필드의 [없음] 버튼 클릭 이벤트 바인딩 (해당 항목 건너뛰기)
  document.querySelectorAll('.btn-cfg-none').forEach(btn => {
    btn.addEventListener('click', () => {
      const field = btn.getAttribute('data-field');
      const sampleInput = document.getElementById(`cfg-sample-${field}`);
      const tplInput = document.getElementById(`cfg-tpl-${field}`);
      const tplPanel = document.getElementById(`cfg-tpl-panel-${field}`);
      const btnCustom = document.querySelector(`.btn-cfg-custom[data-field="${field}"]`);

      activeRules[field] = '__none__';
      ruleSamples[field] = '';
      delete activeTemplates[field];

      btn.classList.add('active');
      if (btnCustom) btnCustom.classList.remove('active');
      if (tplPanel) tplPanel.style.display = 'none';
      if (tplInput) tplInput.value = '';

      sampleInput.value = '';
      sampleInput.placeholder = '(이 사이트에는 해당 항목 없음 - 건너뜀)';

      updateCardPreview(field);
    });
  });

  // 각 필드의 [사용자정의] 버튼 클릭 이벤트 바인딩 (서식 패널 토글)
  document.querySelectorAll('.btn-cfg-custom').forEach(btn => {
    btn.addEventListener('click', () => {
      const field = btn.getAttribute('data-field');
      const tplPanel = document.getElementById(`cfg-tpl-panel-${field}`);
      const tplInput = document.getElementById(`cfg-tpl-${field}`);
      const btnNone = document.querySelector(`.btn-cfg-none[data-field="${field}"]`);

      if (!tplPanel) return;

      const isHidden = tplPanel.style.display === 'none';

      if (isHidden) {
        // 패널 열기
        tplPanel.style.display = 'flex';
        btn.classList.add('active');
        if (btnNone) btnNone.classList.remove('active');

        // 기본 추천 서식 제안
        if (!tplInput.value.trim()) {
          if (field === 'title') {
            tplInput.value = '[{분류}] {제목}';
          } else if (field === 'desc') {
            tplInput.value = '제목: {제목}\n분류: {분류}\n출처: {출처}';
          } else {
            tplInput.value = `{${field === 'category' ? '분류' : field === 'author' ? '기안자' : field === 'status' ? '상태' : field === 'date' ? '날짜' : '출처'}}`;
          }
        }
        activeTemplates[field] = tplInput.value.trim();
        tplInput.focus();
      } else {
        // 패널 닫기
        tplPanel.style.display = 'none';
        btn.classList.remove('active');
        delete activeTemplates[field];
      }

      updateCardPreview(field);
    });
  });

  // 서식 입력 시 실시간 미리보기 갱신
  document.querySelectorAll('.cfg-tpl-input').forEach(input => {
    const id = input.id;
    const field = id.replace('cfg-tpl-', '');

    input.addEventListener('input', () => {
      const val = input.value;
      if (val.trim()) {
        activeTemplates[field] = val;
      } else {
        delete activeTemplates[field];
      }
      updateCardPreview(field);
    });
  });

  // 치환 태그 칩 클릭 시 커서 위치에 태그 자동 삽입
  document.querySelectorAll('.cfg-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const field = chip.getAttribute('data-field');
      const tag = chip.getAttribute('data-tag');
      const tplInput = document.getElementById(`cfg-tpl-${field}`);
      if (!tplInput || !tag) return;

      const start = tplInput.selectionStart || tplInput.value.length;
      const end = tplInput.selectionEnd || tplInput.value.length;
      const text = tplInput.value;

      tplInput.value = text.substring(0, start) + tag + text.substring(end);
      tplInput.focus();
      tplInput.selectionStart = tplInput.selectionEnd = start + tag.length;

      // input 이벤트 트리거로 미리보기 갱신
      tplInput.dispatchEvent(new Event('input', { bubbles: true }));
    });
  });

  // [규칙 저장 및 적용]
  els.btnCfgSave.addEventListener('click', () => {
    if (!currentDomain) {
      alert('도메인 정보를 확인할 수 없습니다.');
      return;
    }

    const hasAnySelector = Object.keys(activeRules).some(k => activeRules[k]);
    const hasAnyTemplate = Object.keys(activeTemplates).some(k => activeTemplates[k]);

    if (!hasAnySelector && !hasAnyTemplate) {
      alert('최소 1개 이상의 항목을 검색하거나, [없음] 또는 [사용자정의]를 지정해주세요.');
      return;
    }

    els.btnCfgSave.disabled = true;
    els.btnCfgSave.textContent = '저장 및 추출 중...';

    chrome.storage.local.get(['tc_site_rules'], (storageRes) => {
      const siteRules = storageRes.tc_site_rules || {};
      siteRules[currentDomain] = {
        selectors: activeRules,
        templates: activeTemplates,
        samples: ruleSamples,
        domain: currentDomain,
        updatedAt: Date.now()
      };

      chrome.storage.local.set({ tc_site_rules: siteRules }, () => {
        // 새 규칙으로 바로 재추출 실행
        chrome.runtime.sendMessage({
          action: "reExtractData",
          tabId: originTabId,
          linkUrl: currentData.linkUrl || "",
          selection: currentData.selectedText || "",
          customRule: activeRules,
          templates: activeTemplates
        }, (extractRes) => {
          els.btnCfgSave.disabled = false;
          els.btnCfgSave.textContent = '규칙 저장 및 적용';

          if (extractRes && extractRes.success && extractRes.data) {
            const freshData = Object.assign({}, currentData, extractRes.data, {
              hasCustomRule: true,
              siteDomain: currentDomain
            });
            // TaskCalendarData 업데이트
            chrome.storage.local.set({ taskCalendarData: freshData });
            applyExtractedDataToForm(freshData);
          } else {
            updateRuleBadgeVisibility(true);
          }

          switchView('register');
        });
      });
    });
  });

  // [규칙 초기화]
  els.btnCfgReset.addEventListener('click', () => {
    if (!confirm(`[${currentDomain}] 사이트의 맞춤 추출 규칙을 초기화하시겠습니까?`)) {
      return;
    }

    chrome.storage.local.get(['tc_site_rules'], (storageRes) => {
      const siteRules = storageRes.tc_site_rules || {};
      delete siteRules[currentDomain];

      chrome.storage.local.set({ tc_site_rules: siteRules }, () => {
        activeRules = {};
        activeTemplates = {};
        ruleSamples = {};
        populateConfigView();

        // 기본 엔진으로 재추출
        chrome.runtime.sendMessage({
          action: "reExtractData",
          tabId: originTabId,
          linkUrl: currentData.linkUrl || "",
          selection: currentData.selectedText || "",
          customRule: null,
          templates: null
        }, (extractRes) => {
          if (extractRes && extractRes.success && extractRes.data) {
            const resetData = Object.assign({}, currentData, extractRes.data, {
              hasCustomRule: false,
              siteDomain: currentDomain
            });
            chrome.storage.local.set({ taskCalendarData: resetData });
            applyExtractedDataToForm(resetData);
          } else {
            updateRuleBadgeVisibility(false);
          }

          switchView('register');
        });
      });
    });
  });

  // ========== 등록 뷰: 취소 및 등록 버튼 ==========
  els.btnCancel.addEventListener('click', () => window.close());

  els.btnSubmit.addEventListener('click', () => {
    const type = currentType || 'schedule';
    chrome.storage.local.set({ lastSelectedType: type });

    let url = `taskcalendar://add?type=${type}`;

    if (els.chkTitle.checked && els.valTitle.value.trim()) {
      url += `&title=${encodeURIComponent(els.valTitle.value.trim())}`;
    }

    if (type === 'task') {
      if (els.chkCategory.checked && els.valCategory.value.trim()) {
        url += `&category=${encodeURIComponent(els.valCategory.value.trim())}`;
      }
      if (els.chkAuthor.checked && els.valAuthor.value.trim()) {
        url += `&author=${encodeURIComponent(els.valAuthor.value.trim())}`;
      }
      if (els.chkStatus.checked && els.valStatus.value) {
        url += `&status=${encodeURIComponent(els.valStatus.value)}`;
      }
    }

    if (els.chkDesc.checked && els.valDesc.value.trim()) {
      url += `&desc=${encodeURIComponent(els.valDesc.value.trim())}`;
    }

    if (type !== 'memo' && els.chkDate.checked && els.valDate.value) {
      url += `&date=${els.valDate.value}`;
    }

    if (els.chkUrl.checked && els.valUrl.value.trim()) {
      url += `&url=${encodeURIComponent(els.valUrl.value.trim())}`;
    }

    // 1. JSON 페이로드 및 프로토콜 URL 동시 조립
    const payload = {
      action: 'add',
      type: type,
      title: (els.chkTitle.checked && els.valTitle.value.trim()) || '',
      category: (type === 'task' && els.chkCategory.checked && els.valCategory.value.trim()) || '',
      author: (type === 'task' && els.chkAuthor.checked && els.valAuthor.value.trim()) || '',
      status: (type === 'task' && els.chkStatus.checked && els.valStatus.value) || '',
      desc: (els.chkDesc.checked && els.valDesc.value.trim()) || '',
      date: (type !== 'memo' && els.chkDate.checked && els.valDate.value) || '',
      url: (els.chkUrl.checked && els.valUrl.value.trim()) || ''
    };

    els.btnSubmit.disabled = true;
    els.btnSubmit.textContent = '등록 중...';

    async function dispatchRegistration() {
      let sentViaHttp = false;

      // 1) 고속 로컬 HTTP API (127.0.0.1:23119) 우선 시도 - 브라우저 경고/팝업 차단 없이 즉각 0초 전송
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 600);
        const res = await fetch('http://127.0.0.1:23119/add', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
          signal: controller.signal
        });
        clearTimeout(timeoutId);
        if (res.ok) {
          sentViaHttp = true;
        }
      } catch (e) {
        sentViaHttp = false;
      }

      if (sentViaHttp) {
        els.btnSubmit.textContent = '✅ 등록 완료!';
        els.btnSubmit.style.background = '#10B981';
        setTimeout(() => window.close(), 350);
        return;
      }

      // 2) 프로토콜 URL 폴백 (앱이 꺼져있을 때 앱 기동 포함)
      // 백그라운드 서비스 워커를 통한 탭 업데이트 및 탭 생성 호출
      chrome.runtime.sendMessage({
        action: "openProtocolUrl",
        url: url,
        tabId: originTabId
      }).catch(() => {});

      // 원본 탭 내 히든 아이프레임 주입 호출
      if (originTabId) {
        chrome.scripting.executeScript({
          target: { tabId: originTabId },
          func: (protocolUrl) => {
            try {
              const ifr = document.createElement('iframe');
              ifr.style.display = 'none';
              ifr.src = protocolUrl;
              document.body.appendChild(ifr);
              setTimeout(() => { ifr.remove(); }, 4000);
            } catch (err) {}
          },
          args: [url]
        }).catch(() => {});
      }

      els.btnSubmit.textContent = '✅ 등록 완료!';
      els.btnSubmit.style.background = '#10B981';
      setTimeout(() => window.close(), 600);
    }

    dispatchRegistration();
  });
});
