document.addEventListener('DOMContentLoaded', () => {
  const today = new Date();
  const dateStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;

  const FIELDS = ['title', 'department', 'author', 'date', 'category', 'status', 'desc', 'url'];

  // DOM 요소 참조
  const els = {
    headerRow: document.getElementById('header-row'),
    viewRegister: document.getElementById('view-register'),
    viewConfig: document.getElementById('view-config'),
    btnToggleConfig: document.getElementById('btn-toggle-config'),
    btnPickElement: document.getElementById('btn-pick-element'),
    siteRuleBadge: document.getElementById('site-rule-status'),
    siteDomainLabel: document.getElementById('site-domain-label'),
    cfgDomainBadge: document.getElementById('cfg-domain-badge'),

    // 탭 및 등록 폼
    btnSchedule: document.getElementById('btn-schedule'),
    btnTask: document.getElementById('btn-task'),
    btnMemo: document.getElementById('btn-memo'),
    itemDepartment: document.getElementById('item-department'),
    itemAuthor: document.getElementById('item-author'),
    itemDate: document.getElementById('item-date'),
    itemCategoryStatus: document.getElementById('item-category-status'),
    itemDesc: document.getElementById('item-desc'),
    chkTitle: document.getElementById('chk-title'),
    chkCategory: document.getElementById('chk-category'),
    chkDepartment: document.getElementById('chk-department'),
    chkAuthor: document.getElementById('chk-author'),
    chkStatus: document.getElementById('chk-status'),
    chkDesc: document.getElementById('chk-desc'),
    chkDate: document.getElementById('chk-date'),
    chkUrl: document.getElementById('chk-url'),
    valTitle: document.getElementById('val-title'),
    valCategory: document.getElementById('val-category'),
    valDepartment: document.getElementById('val-department'),
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
  let activePatterns = {};
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
      if (els.itemDepartment) els.itemDepartment.style.display = 'none';
      if (els.itemAuthor) els.itemAuthor.style.display = 'none';
      els.itemDate.style.display = 'flex';
      if (els.itemCategoryStatus) els.itemCategoryStatus.style.display = 'none';
      if (els.itemDesc) els.itemDesc.style.display = 'none';
    } else if (val === 'task') {
      if (els.itemDepartment) els.itemDepartment.style.display = 'flex';
      if (els.itemAuthor) els.itemAuthor.style.display = 'flex';
      els.itemDate.style.display = 'flex';
      if (els.itemCategoryStatus) els.itemCategoryStatus.style.display = 'flex';
      if (els.itemDesc) els.itemDesc.style.display = 'flex';
    } else {
      // 메모 탭: 날짜 표시
      if (els.itemDepartment) els.itemDepartment.style.display = 'none';
      if (els.itemAuthor) els.itemAuthor.style.display = 'none';
      els.itemDate.style.display = 'flex';
      if (els.itemCategoryStatus) els.itemCategoryStatus.style.display = 'none';
      if (els.itemDesc) els.itemDesc.style.display = 'none';
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
    const actionsEl = document.querySelector('.actions');
    if (target === 'config') {
      els.headerRow.style.display = 'none';
      if (els.siteRuleBadge) els.siteRuleBadge.style.display = 'none';
      els.viewRegister.style.display = 'none';
      if (actionsEl) actionsEl.style.display = 'none';
      els.viewConfig.style.display = 'flex';
      populateConfigView();

      // 사이트 맞춤 설정 시 세로 높이를 500으로 확장하여 스크롤 없이 시원하게 표시
      try {
        chrome.windows.getCurrent((win) => {
          if (win && win.id) {
            chrome.windows.update(win.id, { height: 500 });
          }
        });
      } catch (e) {}
    } else {
      els.headerRow.style.display = 'flex';
      els.viewRegister.style.display = 'flex';
      if (actionsEl) actionsEl.style.display = 'flex';
      els.viewConfig.style.display = 'none';
      updateRuleBadgeVisibility(!!currentData.hasCustomRule);

      // 등록 화면으로 복귀 시 420으로 복원
      try {
        chrome.windows.getCurrent((win) => {
          if (win && win.id) {
            chrome.windows.update(win.id, { height: 420 });
          }
        });
      } catch (e) {}
    }
  }

  els.btnToggleConfig.addEventListener('click', () => switchView('config'));
  els.btnCfgCancel.addEventListener('click', () => switchView('register'));

  // 🎯 타겟 필드 선택 및 헤더 버튼 상태 연동
  let targetPickField = 'all';

  function selectPickField(field) {
    targetPickField = field || 'all';
    document.querySelectorAll('.field-row').forEach(r => r.classList.remove('field-pick-selected'));
    document.querySelectorAll('.btn-field-pick').forEach(b => b.classList.remove('active'));

    const labels = {
      'department': '부서',
      'author': '기안자',
      'title': '제목',
      'date': '날짜',
      'category': '분류',
      'status': '상태',
      'desc': '비고'
    };

    if (field && labels[field]) {
      els.btnPickElement.textContent = `🎯 ${labels[field]} 직접 찍기`;
      els.btnPickElement.title = `웹페이지에서 ${labels[field]} 항목을 마우스로 직접 클릭하여 선택합니다`;
      els.btnPickElement.classList.add('field-targeted');

      const rowMap = {
        'department': els.itemDepartment,
        'author': els.itemAuthor,
        'title': document.getElementById('item-title'),
        'date': els.itemDate,
        'category': els.itemCategoryStatus,
        'status': els.itemCategoryStatus,
        'desc': els.itemDesc
      };
      if (rowMap[field]) rowMap[field].classList.add('field-pick-selected');

      const btnPick = document.querySelector(`.btn-field-pick[data-pick="${field}"]`);
      if (btnPick) btnPick.classList.add('active');
    } else {
      targetPickField = 'all';
      els.btnPickElement.textContent = '🎯 항목 직접 찍기';
      els.btnPickElement.title = '웹페이지에서 원하는 메일/게시글을 마우스로 직접 클릭하여 선택합니다';
      els.btnPickElement.classList.remove('field-targeted');
    }
  }

  // 필드 포커스 및 행 클릭 시 찍기 대상 자동 설정
  if (els.valDepartment) {
    els.valDepartment.addEventListener('focus', () => selectPickField('department'));
    els.valDepartment.addEventListener('click', () => selectPickField('department'));
  }
  if (els.itemDepartment) {
    els.itemDepartment.addEventListener('click', (e) => {
      if (!e.target.classList.contains('btn-field-pick')) selectPickField('department');
    });
  }
  if (els.valAuthor) {
    els.valAuthor.addEventListener('focus', () => selectPickField('author'));
    els.valAuthor.addEventListener('click', () => selectPickField('author'));
  }
  if (els.itemAuthor) {
    els.itemAuthor.addEventListener('click', (e) => {
      if (!e.target.classList.contains('btn-field-pick')) selectPickField('author');
    });
  }
  if (els.valTitle) {
    els.valTitle.addEventListener('focus', () => selectPickField('title'));
    els.valTitle.addEventListener('click', () => selectPickField('title'));
  }
  const itemTitleEl = document.getElementById('item-title');
  if (itemTitleEl) {
    itemTitleEl.addEventListener('click', (e) => {
      if (!e.target.classList.contains('btn-field-pick')) selectPickField('title');
    });
  }
  if (els.valDate) {
    els.valDate.addEventListener('focus', () => selectPickField('date'));
    els.valDate.addEventListener('click', () => selectPickField('date'));
  }
  if (els.itemDate) {
    els.itemDate.addEventListener('click', (e) => {
      if (!e.target.classList.contains('btn-field-pick')) selectPickField('date');
    });
  }
  if (els.valCategory) {
    els.valCategory.addEventListener('focus', () => selectPickField('category'));
    els.valCategory.addEventListener('click', () => selectPickField('category'));
  }
  if (els.valStatus) {
    els.valStatus.addEventListener('focus', () => selectPickField('status'));
    els.valStatus.addEventListener('click', () => selectPickField('status'));
  }
  if (els.valDesc) {
    els.valDesc.addEventListener('focus', () => selectPickField('desc'));
    els.valDesc.addEventListener('click', () => selectPickField('desc'));
  }
  if (els.itemDesc) {
    els.itemDesc.addEventListener('click', (e) => {
      if (!e.target.classList.contains('btn-field-pick')) selectPickField('desc');
    });
  }

  // 각 필드별 🎯 직접 찍기 버튼 클릭 리스너
  document.querySelectorAll('.btn-field-pick').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const field = btn.getAttribute('data-pick');
      selectPickField(field);
      triggerFieldPicker(field);
    });
  });

  function triggerFieldPicker(field) {
    const pickTarget = field || targetPickField || 'all';

    // 현재 폼에 입력된 값들 및 체크 상태를 온전히 보존
    const preservedData = {
      selectedType: currentType,
      title: els.valTitle.value,
      linkText: els.valTitle.value,
      titleChecked: els.chkTitle.checked,
      department: els.valDepartment ? els.valDepartment.value : '',
      departmentChecked: els.chkDepartment ? els.chkDepartment.checked : true,
      author: els.valAuthor.value,
      authorChecked: els.chkAuthor.checked,
      date: els.valDate.value,
      detectedDate: els.valDate.value,
      dateChecked: els.chkDate.checked,
      category: els.valCategory.value,
      categoryChecked: els.chkCategory.checked,
      status: els.valStatus.value,
      statusChecked: els.chkStatus.checked,
      desc: els.valDesc.value,
      descChecked: els.chkDesc.checked,
      url: els.valUrl.value,
      linkUrl: els.valUrl.value,
      pageUrl: (currentData && currentData.pageUrl) || els.valUrl.value,
      pageTitle: (currentData && currentData.pageTitle) || els.valTitle.value,
      urlChecked: els.chkUrl.checked,
      siteDomain: currentDomain,
      hasCustomRule: !!(currentData && currentData.hasCustomRule),
      originTabId: originTabId,
      originFrameId: (currentData && currentData.originFrameId) || 0,
      targetPickField: pickTarget
    };

    chrome.storage.local.set({
      tcPreservedFormData: preservedData,
      tcPickTargetField: pickTarget
    }, () => {
      function startPickerOnTab(tabId) {
        chrome.tabs.sendMessage(tabId, { action: "startElementPicker", targetField: pickTarget }, (res) => {
          if (chrome.runtime.lastError) {
            chrome.scripting.executeScript({
              target: { tabId: tabId, allFrames: true },
              files: ["content.js"]
            }, () => {
              setTimeout(() => {
                chrome.tabs.sendMessage(tabId, { action: "startElementPicker", targetField: pickTarget });
              }, 100);
            });
          }
        });
        window.close();
      }

      if (originTabId) {
        startPickerOnTab(originTabId);
      } else {
        chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
          if (tabs && tabs[0]) {
            startPickerOnTab(tabs[0].id);
          }
        });
      }
    });
  }

  // 상단 헤더의 🎯 항목 직접 찍기 버튼 클릭 시
  if (els.btnPickElement) {
    els.btnPickElement.addEventListener('click', () => {
      let pickTarget = targetPickField;
      if (pickTarget === 'all' && currentType === 'task' && els.valTitle.value.trim() && !els.valDesc.value.trim()) {
        pickTarget = 'desc';
      }
      triggerFieldPicker(pickTarget);
    });
  }

  // 백그라운드로부터 새 데이터 전달 시 자동 갱신
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.action === "reloadTaskCalendarData") {
      loadFormData();
    }
  });

  // ========== 서식 치환 & 정규식 패턴 추출 헬퍼 (미리보기용) ==========
  function applyExtractPattern(text, pattern) {
    if (!text || !pattern || typeof pattern !== 'string') return text || '';
    let p = pattern.trim();
    if (!p) return text;
    try {
      if (p === '대괄호' || p === 'bracket') p = '\\[(.*?)\\]';
      else if (p === '소괄호' || p === 'paren') p = '\\((.*?)\\)';
      else if (p === '첫단어' || p === 'first_word') p = '^([^\\s]+)';
      const reg = new RegExp(p);
      const match = text.match(reg);
      if (match) {
        return (match[1] !== undefined ? match[1] : match[0]).trim();
      }
    } catch(e) {}
    return text;
  }

  function renderTemplatePreview(templateStr, targetField = '') {
    if (!templateStr) return '';
    const base = {
      '제목': currentData.linkText || (activeRules.title && activeRules.title !== '__none__' ? '예시 제목' : ''),
      'title': currentData.linkText || '',
      '분류': currentData.category || (activeRules.category && activeRules.category !== '__none__' ? '예시 분류' : ''),
      'category': currentData.category || '',
      '기안자': currentData.author || '',
      '작성자': currentData.author || '',
      'author': currentData.author || '',
      '부서': currentData.department || '',
      'department': currentData.department || '',
      'dept': currentData.department || '',
      '상태': currentData.status || '등록',
      'status': currentData.status || '등록',
      '날짜': currentData.detectedDate || els.valDate.value || dateStr,
      'date': currentData.detectedDate || els.valDate.value || dateStr,
      '내용': currentData.selectedText || currentData.descOverride || currentData.desc || '',
      '비고': currentData.desc || currentData.descOverride || currentData.selectedText || '',
      'desc': currentData.desc || currentData.descOverride || currentData.selectedText || '',
      '출처': currentData.linkUrl || currentData.pageUrl || '',
      'url': currentData.linkUrl || currentData.pageUrl || ''
    };

    // 설정된 필드별 추출 패턴(정규식)을 기본값에 먼저 반영
    if (activePatterns.title) base.title = base['제목'] = applyExtractPattern(base.title, activePatterns.title);
    if (activePatterns.category) base.category = base['분류'] = applyExtractPattern(base.category, activePatterns.category);
    if (activePatterns.department) base.department = base['부서'] = base.dept = applyExtractPattern(base.department, activePatterns.department);
    if (activePatterns.author) base.author = base['기안자'] = base['작성자'] = applyExtractPattern(base.author, activePatterns.author);
    if (activePatterns.status) base.status = base['상태'] = applyExtractPattern(base.status, activePatterns.status);
    if (activePatterns.desc) base.desc = base['비고'] = base['내용'] = applyExtractPattern(base.desc, activePatterns.desc);

    return templateStr.replace(/\{([^{}]+)\}/g, (match, key) => {
      let filter = '';
      let k = key.trim();
      const colonIdx = k.search(/[:|]/);
      if (colonIdx !== -1) {
        filter = k.substring(colonIdx + 1).trim();
        k = k.substring(0, colonIdx).trim().toLowerCase();
      } else {
        k = k.toLowerCase();
      }

      let val = match;
      if (k === '제목' || k === 'title') val = base.title;
      else if (k === '분류' || k === 'category') val = base.category;
      else if (k === '기안자' || k === '작성자' || k === 'author') val = base.author;
      else if (k === '부서' || k === 'department' || k === 'dept') val = base.department;
      else if (k === '상태' || k === 'status') val = base.status;
      else if (k === '날짜' || k === 'date') val = base.date;
      else if (k === '내용' || k === '본문' || k === '비고' || k === 'desc') val = base.desc;
      else if (k === '출처' || k === '링크' || k === 'url') val = base.url;

      if (filter && val && val !== match) {
        val = applyExtractPattern(val, filter);
      }
      return val;
    });
  }

  // ========== 2차 추출(스마트 단어 후보 분석 & 패턴 자동 역추론) ==========
  function extractCandidateTokens(str, field = '') {
    if (!str || typeof str !== 'string') return [];
    str = str.replace(/[\s\n\r\t]+/g, ' ').trim();
    if (str.length < 2) return [];

    const candidates = [];
    const seen = new Set();

    function add(item) {
      if (!item) return;
      item = item.trim();
      item = item.replace(/^[:\s\-_.,/]+|[:\s\-_.,/]+$/g, '').trim();
      if (item && item.length >= 2 && item.length <= 30 && !seen.has(item) && item !== str) {
        seen.add(item);
        candidates.push(item);
      }
    }

    // 1. 대괄호 [ ... ] 및 소괄호 ( ... ) 안 내용 우선 추출
    const bMatches = str.matchAll(/\[([^\]]+)\]/g);
    for (const m of bMatches) add(m[1]);

    const pMatches = str.matchAll(/\(([^)]+)\)/g);
    for (const m of pMatches) add(m[1]);

    // 2. 직급/라벨 뒤 이름 (예: 주무관 홍길동, 기안: 홍길동 등)
    const titleMatches = str.matchAll(/(?:기안자?|작성자?|담당자?|주무관|사무관|서기관|주사|사원|대리|과장|차장|부장|팀장|계장|연구원|선임|책임)\s*[:\s\[]\s*([가-힣a-zA-Z]{2,10})/g);
    for (const m of titleMatches) add(m[1]);

    // 3. 구분자(/, |, ,)가 있는 경우에만 분리 (공백만으로 일반 문장을 쪼개지 않음)
    if (str.includes('/') || str.includes('|') || str.includes(',')) {
      const parts = str.split(/[/|,]+/);
      for (const p of parts) {
        if (!/^\d+$/.test(p) && !/^(조회|조회수|추천|추천수|공지|알림|전체|등록|수정)$/.test(p)) {
          add(p);
        }
      }
    }

    return candidates.slice(0, 5);
  }

  function deducePatternForToken(fullStr, token) {
    if (!fullStr || !token || fullStr === token) return '';
    token = token.trim();
    const idx = fullStr.indexOf(token);
    if (idx === -1) return '';

    const esc = token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const before = fullStr.substring(0, idx);
    const after = fullStr.substring(idx + token.length);

    // 1. 대괄호 안에 들어있는 경우
    if (before.includes('[') && after.includes(']')) {
      const bBracket = before.lastIndexOf('[');
      const prefix = before.substring(0, bBracket).trim();
      const bracketCount = (fullStr.match(/\[.*?\]/g) || []).length;
      if (bracketCount > 1 && prefix) {
        const escPre = prefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        return `${escPre}\\s*\\[(.*?)\\]`;
      }
      return '\\[(.*?)\\]';
    }

    // 2. 소괄호 안에 들어있는 경우
    if (before.includes('(') && after.includes(')')) {
      const bParen = before.lastIndexOf('(');
      const parenPrefix = before.substring(0, bParen).trim();
      const parenCount = (fullStr.match(/\(.*?\)/g) || []).length;
      if (parenCount > 1 && parenPrefix) {
        const escPre = parenPrefix.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        return `${escPre}\\s*\\((.*?)\\)`;
      }
      return '\\((.*?)\\)';
    }

    // 3. 앞쪽에 명시적 라벨/키워드가 있는 경우 (예: "조회수 203" -> 조회수\s*[:]?\s*(.*))
    const labelMatch = before.match(/([가-힣a-zA-Z0-9_-]+)\s*[:]?\s*$/);
    if (labelMatch && after.trim().length === 0) {
      const label = labelMatch[1].replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      return `${label}\\s*[:]?\\s*(.*)`;
    }

    // 4. 앞 단어 + 공백 뒤에 있는 경우 (예: 주무관 홍길동)
    const wordPreMatch = fullStr.match(new RegExp(`([가-힣a-zA-Z0-9]+)\\s+${esc}`));
    if (wordPreMatch && wordPreMatch[1]) {
      const preWord = wordPreMatch[1].replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      return `${preWord}\\s+([^\\s,/:|]+)`;
    }

    // 5. 일반 fallback: 단어 자체 매칭
    return `(${esc})`;
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

    function limit50(str) {
      if (!str || typeof str !== 'string') return '';
      let s = str.replace(/[\s\n\r\t]+/g, ' ').trim();
      // 끝부분의 공백+따옴표 (예: '안녕하세요 "') 및 HTML 엔티티(&quot;) 제거
      s = s.replace(/\s+["'”’`]+$/g, '').trim();
      s = s.replace(/\s*(&(quot|#34|#39|apos);)+\s*$/gi, '').trim();
      const dQuotes = (s.match(/"/g) || []).length;
      if (dQuotes % 2 !== 0 && s.endsWith('"')) s = s.slice(0, -1).trim();
      const sQuotes = (s.match(/'/g) || []).length;
      if (sQuotes % 2 !== 0 && s.endsWith("'")) s = s.slice(0, -1).trim();
      return s.substring(0, 50).trim();
    }

    if (data.checkedStates) {
      // 복원/피커 선택된 데이터: 보존된 이전 입력값 및 체크박스 상태 복원
      if (data.linkText !== undefined) {
        els.valTitle.value = limit50(data.linkText);
        els.chkTitle.checked = data.checkedStates.title !== false;
      }
      if (data.department !== undefined && els.valDepartment) {
        els.valDepartment.value = limit50(data.department);
        if (els.chkDepartment) els.chkDepartment.checked = data.checkedStates.department !== false;
      }
      if (data.author !== undefined) {
        els.valAuthor.value = limit50(data.author);
        els.chkAuthor.checked = data.checkedStates.author !== false;
      }
      if (data.category !== undefined) {
        els.valCategory.value = limit50(data.category);
        els.chkCategory.checked = data.checkedStates.category !== false;
      }
      if (data.status !== undefined) {
        els.valStatus.value = limit50(data.status);
        els.chkStatus.checked = data.checkedStates.status !== false;
      }
      if (data.detectedDate !== undefined) {
        els.valDate.value = data.detectedDate;
        els.chkDate.checked = data.checkedStates.date !== false;
      }
      if (data.desc !== undefined) {
        els.valDesc.value = data.desc;
        els.chkDesc.checked = data.checkedStates.desc !== false;
      }
      if (data.linkUrl !== undefined) {
        els.valUrl.value = data.linkUrl;
        els.chkUrl.checked = data.checkedStates.url === true;
      }
    } else {
      // 제목 (최대 50자)
      if (activeRules.title === '__none__') {
        els.valTitle.value = '';
        els.chkTitle.checked = false;
      } else if (data.linkText) {
        els.valTitle.value = limit50(data.linkText);
        els.chkTitle.checked = (userSavedCheckedStates && userSavedCheckedStates.title !== undefined) ? userSavedCheckedStates.title : true;
      } else {
        els.valTitle.value = limit50(data.pageTitle || '');
        els.chkTitle.checked = (userSavedCheckedStates && userSavedCheckedStates.title !== undefined) ? userSavedCheckedStates.title : true;
      }

      // 부서 (최대 50자)
      if (els.valDepartment) {
        if (activeRules.department === '__none__') {
          els.valDepartment.value = '';
          if (els.chkDepartment) els.chkDepartment.checked = false;
        } else {
          els.valDepartment.value = limit50(data.department || '');
          if (els.chkDepartment) {
            els.chkDepartment.checked = (userSavedCheckedStates && userSavedCheckedStates.department !== undefined)
              ? userSavedCheckedStates.department
              : !!els.valDepartment.value;
          }
        }
      }

      // 작성자 / 기안자 (최대 50자)
      if (activeRules.author === '__none__') {
        els.valAuthor.value = '';
        els.chkAuthor.checked = false;
      } else {
        els.valAuthor.value = limit50(data.author || '');
        els.chkAuthor.checked = (userSavedCheckedStates && userSavedCheckedStates.author !== undefined)
          ? userSavedCheckedStates.author
          : !!els.valAuthor.value;
      }

      // 분류 (최대 50자)
      if (activeRules.category === '__none__') {
        els.valCategory.value = '';
        els.chkCategory.checked = false;
      } else {
        els.valCategory.value = limit50(data.category || '일반');
        els.chkCategory.checked = (userSavedCheckedStates && userSavedCheckedStates.category !== undefined)
          ? userSavedCheckedStates.category
          : true;
      }

      // 상태 (텍스트박스 기본값 "등록", 최대 50자)
      if (activeRules.status === '__none__') {
        els.valStatus.value = '';
        els.chkStatus.checked = false;
      } else {
        els.valStatus.value = limit50(data.status || '등록');
        els.chkStatus.checked = (userSavedCheckedStates && userSavedCheckedStates.status !== undefined)
          ? userSavedCheckedStates.status
          : true;
      }

      // 비고 (최대 1000자)
      if (activeRules.desc === '__none__') {
        els.valDesc.value = '';
        els.chkDesc.checked = false;
      } else if (data.desc || data.descOverride || data.selectedText) {
        els.valDesc.value = (data.desc || data.descOverride || data.selectedText || '').substring(0, 1000);
        els.chkDesc.checked = (userSavedCheckedStates && userSavedCheckedStates.desc !== undefined)
          ? userSavedCheckedStates.desc
          : true;
      } else {
        els.valDesc.value = '';
        els.chkDesc.checked = (userSavedCheckedStates && userSavedCheckedStates.desc !== undefined)
          ? userSavedCheckedStates.desc
          : true;
      }

      // 날짜
      if (activeRules.date === '__none__') {
        els.valDate.value = '';
        els.chkDate.checked = false;
      } else if (data.detectedDate) {
        els.valDate.value = data.detectedDate;
        els.chkDate.checked = (userSavedCheckedStates && userSavedCheckedStates.date !== undefined)
          ? userSavedCheckedStates.date
          : true;
      } else {
        els.valDate.value = dateStr;
        els.chkDate.checked = (userSavedCheckedStates && userSavedCheckedStates.date !== undefined)
          ? userSavedCheckedStates.date
          : true;
      }

      // 출처 URL: 보안 및 전자결재 URL 보호를 위해 기본 숨김 및 미입력 처리
      els.valUrl.value = '';
      els.chkUrl.checked = (userSavedCheckedStates && userSavedCheckedStates.url !== undefined)
        ? userSavedCheckedStates.url
        : false;
    }

    // 맞춤 규칙 적용 상태 표시 여부
    updateRuleBadgeVisibility(!!data.hasCustomRule);

    syncCheckboxState();

    if (data.targetPickField) {
      selectPickField(data.targetPickField);
    }

    // 🎯 K캘린더 팝업 내부 모달: 방금 직접 찍기로 들어온 경우 팝업 안에서 확인 대화창 표시
    if (data.justPicked && data.justPicked.field && data.justPicked.rawText) {
      const pField = data.justPicked.field;
      const pRaw = data.justPicked.rawText;
      delete data.justPicked;
      chrome.storage.local.get(['taskCalendarData'], (res) => {
        if (res && res.taskCalendarData && res.taskCalendarData.justPicked) {
          delete res.taskCalendarData.justPicked;
          chrome.storage.local.set({ taskCalendarData: res.taskCalendarData });
        }
      });
      showInPopupConfirmModal(pField, pRaw);
    }
  }

  function updateRuleBadgeVisibility(hasRule) {
    const statusEl = document.getElementById('site-rule-status');
    const domainEl = document.getElementById('site-domain-label');
    if (hasRule && currentDomain) {
      if (statusEl) statusEl.style.display = 'block';
      if (domainEl) domainEl.textContent = `(${currentDomain})`;
      els.btnToggleConfig.textContent = '⚙️ 맞춤 규칙 수정';
    } else {
      if (statusEl) statusEl.style.display = 'none';
      els.btnToggleConfig.textContent = '⚙️ 사이트 맞춤';
    }
  }

  const FIELD_NAMES_KO = {
    department: '부서',
    author: '기안자',
    title: '제목',
    date: '날짜',
    category: '분류',
    status: '상태',
    desc: '비고 / 내용',
    all: '항목'
  };

  function showInPopupConfirmModal(field, rawText) {
    const overlay = document.getElementById('modal-confirm-overlay');
    const titleEl = document.getElementById('modal-confirm-title');
    const rawEl = document.getElementById('modal-confirm-raw');
    const inputEl = document.getElementById('modal-confirm-input');
    const closeBtn = document.getElementById('modal-confirm-close');
    const cancelBtn = document.getElementById('btn-modal-cancel');
    const okBtn = document.getElementById('btn-modal-ok');
    const chipsRow = document.getElementById('modal-confirm-chips-row');
    const chipsContainer = document.getElementById('modal-confirm-chips');

    if (!overlay || !titleEl || !rawEl || !inputEl) return;

    const fieldKo = FIELD_NAMES_KO[field] || '항목';
    titleEl.textContent = `🎯 [${fieldKo}] 항목 추출 확인`;
    rawEl.textContent = rawText;
    inputEl.value = rawText;

    // 추천 칩 분석 (대괄호 안 단어, 기안자/주무관 뒤 단어 등)
    const tokens = extractCandidateTokens(rawText, field);
    if (tokens.length > 0 && chipsContainer && chipsRow) {
      chipsContainer.innerHTML = '';
      chipsRow.style.display = 'flex';
      tokens.forEach(tok => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'modal-confirm-chip-btn';
        btn.textContent = `🎯 ${tok}`;
        btn.addEventListener('click', () => {
          inputEl.value = tok;
          inputEl.focus();
          inputEl.select();
        });
        chipsContainer.appendChild(btn);
      });
    } else if (chipsRow) {
      chipsRow.style.display = 'none';
    }

    overlay.style.display = 'flex';
    setTimeout(() => {
      inputEl.focus();
      inputEl.select();
    }, 50);

    function closeModal() {
      overlay.style.display = 'none';
    }

    function useAsIs() {
      const fieldInputMap = {
        department: els.valDepartment,
        author: els.valAuthor,
        title: els.valTitle,
        category: els.valCategory,
        status: els.valStatus,
        date: els.valDate,
        desc: els.valDesc
      };
      const targetInput = fieldInputMap[field];
      if (targetInput) {
        if (field === 'date') {
          const m = rawText.match(/\b(20\d{2}|19\d{2})[-./](\d{1,2})[-./](\d{1,2})\b/);
          targetInput.value = m ? `${m[1]}-${String(m[2]).padStart(2, '0')}-${String(m[3]).padStart(2, '0')}` : rawText;
        } else {
          targetInput.value = rawText;
        }
        targetInput.dispatchEvent(new Event('input', { bubbles: true }));
      }

      // 그대로 사용 시: 기존에 등록되어 있던 2차 정규식 추출 규칙 완전 제거
      delete activePatterns[field];

      if (currentDomain) {
        chrome.storage.local.get(['tc_site_rules'], (storageRes) => {
          const siteRules = storageRes.tc_site_rules || {};
          if (siteRules[currentDomain] && siteRules[currentDomain].patterns) {
            delete siteRules[currentDomain].patterns[field];
            siteRules[currentDomain].updatedAt = Date.now();
            chrome.storage.local.set({ tc_site_rules: siteRules });
          }
          const hasRemainingRule = Object.keys(activeRules).some(k => activeRules[k]) ||
                                   Object.keys(activeTemplates).some(k => activeTemplates[k]) ||
                                   Object.keys(activePatterns).some(k => activePatterns[k]);
          updateRuleBadgeVisibility(hasRemainingRule);
        });
      }

      closeModal();
    }

    function applyModalValue() {
      const finalVal = inputEl.value.trim() || rawText;

      // 대상 인풋창에 값 주입
      const fieldInputMap = {
        department: els.valDepartment,
        author: els.valAuthor,
        title: els.valTitle,
        category: els.valCategory,
        status: els.valStatus,
        date: els.valDate,
        desc: els.valDesc
      };
      const targetInput = fieldInputMap[field];
      if (targetInput) {
        if (field === 'date') {
          const m = finalVal.match(/\b(20\d{2}|19\d{2})[-./](\d{1,2})[-./](\d{1,2})\b/);
          targetInput.value = m ? `${m[1]}-${String(m[2]).padStart(2, '0')}-${String(m[3]).padStart(2, '0')}` : finalVal;
        } else {
          targetInput.value = finalVal;
        }
        targetInput.dispatchEvent(new Event('input', { bubbles: true }));
      }

      // 원문과 다르고 원문에 포함되어 있다면 정규식 패턴 자동 역추론 및 영구 저장
      if (finalVal !== rawText && rawText.includes(finalVal) && currentDomain) {
        const autoPat = deducePatternForToken(rawText, finalVal);
        if (autoPat) {
          activePatterns[field] = autoPat;
          chrome.storage.local.get(['tc_site_rules'], (storageRes) => {
            const siteRules = storageRes.tc_site_rules || {};
            if (!siteRules[currentDomain]) {
              siteRules[currentDomain] = {
                selectors: Object.assign({}, activeRules),
                templates: Object.assign({}, activeTemplates),
                patterns: {},
                samples: {},
                domain: currentDomain,
                updatedAt: Date.now()
              };
            }
            if (!siteRules[currentDomain].patterns) siteRules[currentDomain].patterns = {};
            siteRules[currentDomain].patterns[field] = autoPat;
            siteRules[currentDomain].updatedAt = Date.now();
            chrome.storage.local.set({ tc_site_rules: siteRules });
            updateRuleBadgeVisibility(true);
          });
        }
      } else {
        // 원문과 같거나 원문 그대로 사용하는 경우: 기존에 등록되어 있던 2차 추출 패턴 규칙 완전 해제
        delete activePatterns[field];
        if (currentDomain) {
          chrome.storage.local.get(['tc_site_rules'], (storageRes) => {
            const siteRules = storageRes.tc_site_rules || {};
            if (siteRules[currentDomain] && siteRules[currentDomain].patterns) {
              delete siteRules[currentDomain].patterns[field];
              siteRules[currentDomain].updatedAt = Date.now();
              chrome.storage.local.set({ tc_site_rules: siteRules });
            }
            const hasRemainingRule = Object.keys(activeRules).some(k => activeRules[k]) ||
                                     Object.keys(activeTemplates).some(k => activeTemplates[k]) ||
                                     Object.keys(activePatterns).some(k => activePatterns[k]);
            updateRuleBadgeVisibility(hasRemainingRule);
          });
        }
      }

      closeModal();
    }

    okBtn.onclick = applyModalValue;
    cancelBtn.onclick = useAsIs;
    closeBtn.onclick = useAsIs;

    inputEl.onkeydown = (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        applyModalValue();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        useAsIs();
      }
    };
  }

  let userSavedCheckedStates = null;

  function saveCurrentCheckboxStates() {
    const states = Object.assign({}, userSavedCheckedStates || {});
    if (els.chkTitle) states.title = els.chkTitle.checked;
    if (els.chkDate) states.date = els.chkDate.checked;
    if (els.chkUrl) states.url = els.chkUrl.checked;

    if (currentType === 'task') {
      if (els.chkDepartment) states.department = els.chkDepartment.checked;
      if (els.chkAuthor) states.author = els.chkAuthor.checked;
      if (els.chkCategory) states.category = els.chkCategory.checked;
      if (els.chkStatus) states.status = els.chkStatus.checked;
      if (els.chkDesc) states.desc = els.chkDesc.checked;
    }
    userSavedCheckedStates = states;
    chrome.storage.local.set({ userCheckedStates: states });
  }

  function loadFormData() {
    chrome.storage.local.get(['taskCalendarData', 'lastSelectedType', 'tc_site_rules', 'userCheckedStates'], (result) => {
      userSavedCheckedStates = result.userCheckedStates || null;
      const data = result.taskCalendarData;
      const savedType = (data && data.selectedType) || result.lastSelectedType || 'schedule';
      switchType(savedType, false);

      const allSiteRules = result.tc_site_rules || {};

      if (!data) return;

      currentDomain = data.siteDomain || '';
      if (currentDomain && allSiteRules[currentDomain]) {
        const saved = allSiteRules[currentDomain];
        activeRules = Object.assign({}, saved.selectors || saved);
        activeTemplates = Object.assign({}, saved.templates || {});
        activePatterns = Object.assign({}, saved.patterns || {});
        ruleSamples = Object.assign({}, saved.samples || {});
        data.hasCustomRule = Object.keys(activeRules).some(k => activeRules[k]) ||
                             Object.keys(activeTemplates).some(k => activeTemplates[k]) ||
                             Object.keys(activePatterns).some(k => activePatterns[k]);
      } else {
        activeRules = {};
        activeTemplates = {};
        activePatterns = {};
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
    if (els.valDepartment && els.chkDepartment) els.valDepartment.disabled = !els.chkDepartment.checked;
    els.valAuthor.disabled = !els.chkAuthor.checked;
    els.valStatus.disabled = !els.chkStatus.checked;
    els.valDesc.disabled = !els.chkDesc.checked;
    els.valDate.disabled = !els.chkDate.checked;
    els.valUrl.disabled = !els.chkUrl.checked;
  }
  [els.chkTitle, els.chkCategory, els.chkDepartment, els.chkAuthor, els.chkStatus, els.chkDesc, els.chkDate, els.chkUrl].filter(Boolean).forEach(chk => {
    chk.addEventListener('change', () => {
      syncCheckboxState();
      saveCurrentCheckboxStates();
    });
  });

  // ========== 사이트 맞춤 설정 뷰 렌더링 & 서식 상태 갱신 ==========
  function updateCardPreview(field) {
    const statusPill = document.getElementById(`cfg-status-${field}`);
    const prevBox = document.getElementById(`cfg-prev-${field}`);
    const prevText = document.getElementById(`cfg-prev-text-${field}`);
    const prevSel = document.getElementById(`cfg-prev-sel-${field}`);
    const tplInput = document.getElementById(`cfg-tpl-${field}`);
    const patInput = document.getElementById(`cfg-pattern-${field}`);

    const tpl = (activeTemplates[field] || '').trim();
    const pat = (activePatterns[field] || '').trim();
    const sel = activeRules[field] || '';

    // 1) 서식(사용자정의) 또는 추출 패턴이 설정되어 있는 경우
    if (tpl || pat) {
      statusPill.className = 'cfg-status-pill pill-custom';
      statusPill.textContent = '✏️ 사용자정의';
      prevBox.style.display = 'flex';
      const effectiveTpl = tpl || `{${field === 'category' ? '분류' : field === 'department' ? '부서' : field === 'author' ? '기안자' : field === 'status' ? '상태' : field === 'date' ? '날짜' : field === 'desc' ? '비고' : '제목'}}`;
      const previewVal = renderTemplatePreview(effectiveTpl, field);
      prevText.textContent = `치환 결과: "${previewVal}"`;
      let descParts = [];
      if (tpl) descParts.push(`서식: ${tpl}`);
      if (pat) descParts.push(`패턴: /${pat}/`);
      if (sel && sel !== '__none__') descParts.push(`기준 선택자: ${sel}`);
      prevSel.textContent = descParts.join(' | ');
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
      const patInput = document.getElementById(`cfg-pattern-${field}`);
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
        else if (field === 'department') sampleInput.value = els.valDepartment ? els.valDepartment.value.trim() : '';
        else if (field === 'author') sampleInput.value = els.valAuthor.value.trim();
        else if (field === 'status' && els.valStatus.value !== '등록') sampleInput.value = els.valStatus.value;
        else if (field === 'date') sampleInput.value = els.valDate.value;
        else if (field === 'desc') sampleInput.value = els.valDesc.value.trim();
        else if (field === 'url') sampleInput.value = els.valUrl.value;
      }

      // 서식 및 패턴 텍스트 복원
      if (patInput) patInput.value = activePatterns[field] || '';

      if (activeTemplates[field] || activePatterns[field]) {
        if (tplInput) tplInput.value = activeTemplates[field] || '';
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

      // 2차 추출 추천 단어 칩 렌더링
      const subPickContainer = document.getElementById(`cfg-subpick-chips-${field}`);
      if (subPickContainer) {
        subPickContainer.innerHTML = '';
        const sampleText = ruleSamples[field] || sampleInput.value || '';
        const cfgTokens = extractCandidateTokens(sampleText, field);
        const parentRow = subPickContainer.closest('.cfg-pattern-row');
        const chipLabel = parentRow ? parentRow.querySelector('.cfg-chip-label') : null;
        if (cfgTokens.length > 0) {
          if (chipLabel) chipLabel.style.display = 'block';
          subPickContainer.style.display = 'flex';
          cfgTokens.forEach(tok => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'sub-pick-btn';
            btn.textContent = `🎯 ${tok}`;
            btn.title = `클릭하면 '${tok}' 추출 패턴으로 자동 설정됩니다`;
            btn.addEventListener('click', () => {
              const deducedPat = deducePatternForToken(sampleText, tok);
              if (deducedPat) {
                if (patInput) patInput.value = deducedPat;
                activePatterns[field] = deducedPat;
                patInput?.dispatchEvent(new Event('input', { bubbles: true }));
              }
            });
            subPickContainer.appendChild(btn);
          });
        } else {
          if (chipLabel) chipLabel.style.display = 'none';
          subPickContainer.style.display = 'none';
        }
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

      // 50자 초과 검색 차단
      if (sampleText.length > 50) {
        statusPill.className = 'cfg-status-pill pill-fail';
        statusPill.textContent = '❌ 50자 이하만 검색 가능';
        sampleInput.focus();
        return;
      }

      btn.disabled = true;
      statusPill.className = 'cfg-status-pill pill-empty';
      statusPill.textContent = '🔍 검색 중...';

      chrome.runtime.sendMessage({
        action: "matchElementInTab",
        tabId: originTabId,
        frameId: currentData.originFrameId,
        sampleText: sampleText,
        isUrl: (field === 'url')
      }, (res) => {
        btn.disabled = false;
        if (!res || !res.success) {
          statusPill.className = 'cfg-status-pill pill-fail';
          statusPill.textContent = (res && res.error) ? res.error : '❌ 일치 항목 없음';
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
      const patInput = document.getElementById(`cfg-pattern-${field}`);
      const tplPanel = document.getElementById(`cfg-tpl-panel-${field}`);
      const btnCustom = document.querySelector(`.btn-cfg-custom[data-field="${field}"]`);

      activeRules[field] = '__none__';
      ruleSamples[field] = '';
      delete activeTemplates[field];
      delete activePatterns[field];

      btn.classList.add('active');
      if (btnCustom) btnCustom.classList.remove('active');
      if (tplPanel) tplPanel.style.display = 'none';
      if (tplInput) tplInput.value = '';
      if (patInput) patInput.value = '';

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
      const patInput = document.getElementById(`cfg-pattern-${field}`);
      const btnNone = document.querySelector(`.btn-cfg-none[data-field="${field}"]`);

      if (!tplPanel) return;

      const isHidden = tplPanel.style.display === 'none';

      if (isHidden) {
        // 패널 열기
        tplPanel.style.display = 'flex';
        btn.classList.add('active');
        if (btnNone) btnNone.classList.remove('active');

        // 기본 추천 서식 제안
        if (!tplInput.value.trim() && !patInput?.value.trim()) {
          if (field === 'title') {
            tplInput.value = '[{분류}] {제목}';
          } else if (field === 'desc') {
            tplInput.value = '{부서}';
          } else {
            tplInput.value = `{${field === 'category' ? '분류' : field === 'department' ? '부서' : field === 'author' ? '기안자' : field === 'status' ? '상태' : field === 'date' ? '날짜' : '출처'}}`;
          }
        }
        if (tplInput.value.trim()) {
          activeTemplates[field] = tplInput.value.trim();
        }
        tplInput.focus();
      } else {
        // 패널 닫기
        tplPanel.style.display = 'none';
        btn.classList.remove('active');
        delete activeTemplates[field];
        delete activePatterns[field];
        if (patInput) patInput.value = '';
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

  // 패턴(정규식) 입력 시 실시간 미리보기 갱신
  document.querySelectorAll('.cfg-pattern-input').forEach(input => {
    const id = input.id;
    const field = id.replace('cfg-pattern-', '');

    input.addEventListener('input', () => {
      const val = input.value;
      if (val.trim()) {
        activePatterns[field] = val.trim();
      } else {
        delete activePatterns[field];
      }
      updateCardPreview(field);
    });
  });

  // 빠른 패턴 칩 클릭 시 패턴 입력란에 자동 설정 & 실시간 미리보기
  document.querySelectorAll('.cfg-quick-pattern').forEach(btn => {
    btn.addEventListener('click', () => {
      const field = btn.getAttribute('data-field');
      const pattern = btn.getAttribute('data-pattern');
      const patInput = document.getElementById(`cfg-pattern-${field}`);
      if (!patInput || !pattern) return;

      patInput.value = pattern;
      activePatterns[field] = pattern;
      patInput.dispatchEvent(new Event('input', { bubbles: true }));
      patInput.focus();
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
    const hasAnyPattern = Object.keys(activePatterns).some(k => activePatterns[k]);

    if (!hasAnySelector && !hasAnyTemplate && !hasAnyPattern) {
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
        patterns: activePatterns,
        samples: ruleSamples,
        domain: currentDomain,
        updatedAt: Date.now()
      };

      chrome.storage.local.set({ tc_site_rules: siteRules }, () => {
        // 새 규칙으로 바로 재추출 실행
        chrome.runtime.sendMessage({
          action: "reExtractData",
          tabId: originTabId,
          frameId: currentData.originFrameId,
          linkUrl: currentData.linkUrl || "",
          selection: currentData.selectedText || "",
          customRule: {
            selectors: activeRules,
            templates: activeTemplates,
            patterns: activePatterns
          },
          templates: activeTemplates,
          patterns: activePatterns
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
        activePatterns = {};
        ruleSamples = {};
        populateConfigView();

        // 기본 엔진으로 재추출
        chrome.runtime.sendMessage({
          action: "reExtractData",
          tabId: originTabId,
          frameId: currentData.originFrameId,
          linkUrl: currentData.linkUrl || "",
          selection: currentData.selectedText || "",
          customRule: null,
          templates: null,
          patterns: null
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
    saveCurrentCheckboxStates();
    const type = currentType || 'schedule';
    chrome.storage.local.set({ lastSelectedType: type });

    // 🎯 맞춤 규칙 자동 갱신 저장:
    // 사용자가 항목 직접 찍기(비고 등)를 수행한 경우, 찍은 곳의 선택자(selector)를 사이트 맞춤 규칙(tc_site_rules)에 영구 저장!
    const ruleUpdates = (currentData && currentData.pendingRuleUpdates) || {};
    if (currentDomain && Object.keys(ruleUpdates).length > 0) {
      chrome.storage.local.get(['tc_site_rules'], (storageRes) => {
        const siteRules = storageRes.tc_site_rules || {};
        if (!siteRules[currentDomain]) {
          siteRules[currentDomain] = {
            selectors: Object.assign({}, activeRules),
            templates: Object.assign({}, activeTemplates),
            patterns: Object.assign({}, activePatterns),
            samples: Object.assign({}, ruleSamples),
            domain: currentDomain,
            updatedAt: Date.now()
          };
        }
        if (!siteRules[currentDomain].selectors) siteRules[currentDomain].selectors = {};
        if (!siteRules[currentDomain].patterns) siteRules[currentDomain].patterns = {};
        if (!siteRules[currentDomain].samples) siteRules[currentDomain].samples = {};

        for (const [fld, info] of Object.entries(ruleUpdates)) {
          if (info && info.selector) {
            // 비고 필드인 경우 체크가 해제되었거나 입력값이 없으면 규칙 저장 제외
            if (fld === 'desc' && (!els.chkDesc.checked || !els.valDesc.value.trim())) {
              continue;
            }
            siteRules[currentDomain].selectors[fld] = info.selector;
            if (activeRules) activeRules[fld] = info.selector;
            if (info.sample) {
              siteRules[currentDomain].samples[fld] = info.sample;
              if (ruleSamples) ruleSamples[fld] = info.sample;
            }
          }
        }
        siteRules[currentDomain].updatedAt = Date.now();
        chrome.storage.local.set({ tc_site_rules: siteRules });
      });
    }

    function clean50(val) {
      if (!val || typeof val !== 'string') return '';
      let s = val.replace(/[\s\n\r\t]+/g, ' ').trim();
      s = s.replace(/\s+["'”’`]+$/g, '').trim();
      s = s.replace(/\s*(&(quot|#34|#39|apos);)+\s*$/gi, '').trim();
      const dQuotes = (s.match(/"/g) || []).length;
      if (dQuotes % 2 !== 0 && s.endsWith('"')) s = s.slice(0, -1).trim();
      const sQuotes = (s.match(/'/g) || []).length;
      if (sQuotes % 2 !== 0 && s.endsWith("'")) s = s.slice(0, -1).trim();
      return s.substring(0, 50).trim();
    }

    let url = `taskcalendar://add?type=${type}`;

    if (type === 'memo') {
      // 3. 메모에 날짜 추가 -> 메모앱으로 가져왔을 때 메모제목을 날짜로 하고 제목을 내용을 넣어줌
      const memoDate = (els.chkDate && els.chkDate.checked && els.valDate.value) ? els.valDate.value : dateStr;
      const memoTitleAsContent = els.chkTitle.checked ? clean50(els.valTitle.value) : '';

      url += `&title=${encodeURIComponent(memoDate)}`;
      if (memoTitleAsContent) {
        url += `&desc=${encodeURIComponent(memoTitleAsContent)}`;
      }
      url += `&date=${encodeURIComponent(memoDate)}`;
    } else {
      if (els.chkTitle.checked && clean50(els.valTitle.value)) {
        url += `&title=${encodeURIComponent(clean50(els.valTitle.value))}`;
      }

      if (type === 'task') {
        if (els.chkDepartment && els.chkDepartment.checked && clean50(els.valDepartment.value)) {
          url += `&department=${encodeURIComponent(clean50(els.valDepartment.value))}`;
        }
        if (els.chkAuthor.checked && clean50(els.valAuthor.value)) {
          url += `&author=${encodeURIComponent(clean50(els.valAuthor.value))}`;
        }
        if (els.chkCategory.checked && clean50(els.valCategory.value)) {
          url += `&category=${encodeURIComponent(clean50(els.valCategory.value))}`;
        }
        if (els.chkStatus.checked && clean50(els.valStatus.value)) {
          url += `&status=${encodeURIComponent(clean50(els.valStatus.value))}`;
        }
      }

      if (els.chkDate.checked && els.valDate.value) {
        url += `&date=${els.valDate.value}`;
      }

      // 비고: 체크된 경우 전송 (메모 및 비고 용도로 최대 1000자까지 허용)
      if (els.chkDesc && els.chkDesc.checked && els.valDesc.value.trim()) {
        const descText = els.valDesc.value.replace(/[\r\t]+/g, ' ').trim().substring(0, 1000);
        if (descText) {
          url += `&desc=${encodeURIComponent(descText)}`;
        }
      }
    }

    // 출처는 보안상 기본 숨김이며, 체크된 경우에만 전송
    if (els.chkUrl && els.chkUrl.checked && els.valUrl.value.trim()) {
      url += `&url=${encodeURIComponent(els.valUrl.value.trim())}`;
    }

    els.btnSubmit.disabled = true;
    els.btnSubmit.textContent = '전송 중...';

    // 보던 원래 웹페이지(originTabId)에서 직접 프로토콜 호출 (새 탭 생성 없이 보던 창에서 'Calendar.exe 열기' 안내 표시)
    if (originTabId) {
      chrome.scripting.executeScript({
        target: { tabId: originTabId },
        func: (protocolUrl) => {
          try {
            const a = document.createElement('a');
            a.href = protocolUrl;
            a.style.display = 'none';
            document.body.appendChild(a);
            a.click();
            setTimeout(() => { a.remove(); }, 2000);
          } catch (e) {
            try {
              const ifr = document.createElement('iframe');
              ifr.style.display = 'none';
              ifr.src = protocolUrl;
              document.body.appendChild(ifr);
              setTimeout(() => { ifr.remove(); }, 2000);
            } catch (err) {}
          }
        },
        args: [url]
      }).catch(() => {
        // 특수 페이지(chrome:// 등)로 인해 스크립트 주입 불가 시에만 백그라운드로 전송
        chrome.runtime.sendMessage({ action: "openProtocolUrl", url: url }).catch(() => {});
      });
    } else {
      chrome.runtime.sendMessage({ action: "openProtocolUrl", url: url }).catch(() => {});
    }

    els.btnSubmit.textContent = '✅ 전송 완료!';
    els.btnSubmit.style.background = '#10B981';
    setTimeout(() => window.close(), 500);
  });
});
