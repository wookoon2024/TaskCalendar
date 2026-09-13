document.addEventListener('DOMContentLoaded', () => {
  const today = new Date();
  const dateStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;

  const els = {
    btnSchedule: document.getElementById('btn-schedule'),
    btnTask: document.getElementById('btn-task'),
    btnMemo: document.getElementById('btn-memo'),
    typeRadios: document.querySelectorAll('input[name="type"]'),
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
    btnSubmit: document.getElementById('btn-submit-action')
  };

  els.valDate.value = dateStr;
  let originTabId = null;

  let currentType = 'schedule';

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
      els.valDesc.style.height = '199px';
    } else if (val === 'task') {
      els.itemDate.style.display = 'flex';
      els.itemCategory.style.display = 'flex';
      els.itemAuthor.style.display = 'flex';
      els.itemStatus.style.display = 'flex';
      els.valDesc.style.height = '100px';
    } else {
      els.itemDate.style.display = 'none';
      els.itemCategory.style.display = 'none';
      els.itemAuthor.style.display = 'none';
      els.itemStatus.style.display = 'none';
      els.valDesc.style.height = '234px';
    }

    if (save) {
      chrome.storage.local.set({ lastSelectedType: val });
    }
  }

  // 탭 클릭 이벤트 바인딩
  [els.btnSchedule, els.btnTask, els.btnMemo].forEach(btn => {
    if (btn) {
      btn.addEventListener('click', () => {
        const type = btn.getAttribute('data-type');
        switchType(type, true);
      });
    }
  });

  function loadFormData() {
    chrome.storage.local.get(['taskCalendarData', 'lastSelectedType'], (result) => {
      // 이전에 선택했던 유형(일정/업무/메모) 기억하여 복원
      const savedType = result.lastSelectedType || 'schedule';
      switchType(savedType, false);

      if (!result.taskCalendarData) {
        return;
      }
      const data = result.taskCalendarData;
      originTabId = data.originTabId || null;

      // 체크박스 기본 상태 복원
      els.chkTitle.checked = true;
      els.chkCategory.checked = true;
      els.chkAuthor.checked = true;
      els.chkStatus.checked = true;
      els.chkDate.checked = true;
      els.chkUrl.checked = true;

      // 제목: 클릭한 링크 텍스트 > 선택한 텍스트(단문) > 페이지 제목
      if (data.linkText) {
        els.valTitle.value = data.linkText;
      } else if (data.selectedText && data.selectedText.length <= 150 && !data.selectedText.includes('\n')) {
        els.valTitle.value = data.selectedText.trim();
      } else {
        els.valTitle.value = data.pageTitle || '';
      }

      // 작성자 / 기안자
      if (data.author) {
        els.valAuthor.value = data.author;
      } else {
        els.valAuthor.value = '';
      }

      // 기본 업무분류 및 상태
      if (data.category) {
        els.valCategory.value = data.category;
      } else {
        els.valCategory.value = '일반';
      }
      els.valStatus.value = '등록';

      // 내용: 선택한 텍스트(제목과 다른 경우) > 행 메타 정보 (작성자, 날짜, 조회 등)
      if (data.selectedText && data.selectedText.trim() !== els.valTitle.value.trim()) {
        els.valDesc.value = data.selectedText;
        els.chkDesc.checked = true;
      } else if (data.metaText) {
        els.valDesc.value = data.metaText;
        els.chkDesc.checked = true;
      } else {
        els.valDesc.value = '';
        els.chkDesc.checked = false;
      }

      // 감지된 날짜가 있으면 날짜 필드 갱신
      if (data.detectedDate) {
        els.valDate.value = data.detectedDate;
      } else {
        els.valDate.value = dateStr;
      }

      // URL: 게시글 링크 URL 우선, 없으면 페이지 URL
      els.valUrl.value = data.linkUrl || data.pageUrl || '';

      syncCheckboxState();
    });
  }

  loadFormData();

  // 이미 팝업 창이 열려 있는 상태에서 다른 항목을 우클릭했을 때 실시간 폼 갱신
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

  // 체크박스 ↔ 입력 연동
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

    // 프로토콜 URL 트리거 (팝업 자체 앵커 클릭 + 원본 탭 주입 병행)
    try {
      var a = document.createElement('a');
      a.href = url;
      a.style.display = 'none';
      document.body.appendChild(a);
      a.click();
    } catch(e) {}

    if (originTabId) {
      chrome.scripting.executeScript({
        target: { tabId: originTabId },
        func: (protocolUrl) => {
          try {
            var el = document.createElement('a');
            el.href = protocolUrl;
            el.style.display = 'none';
            document.body.appendChild(el);
            el.click();
            setTimeout(function() { el.remove(); }, 1000);
          } catch(err) {}
        },
        args: [url]
      }, () => {
        setTimeout(() => window.close(), 300);
      });
    } else {
      setTimeout(() => window.close(), 300);
    }
  });
});
