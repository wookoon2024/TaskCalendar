// memo_manager.js - 페이지 메모 목록 / 내보내기 / 가져오기
(function () {
  'use strict';

  var STORE_KEY = 'tc_page_memos';
  var EXPORT_APP = 'taskcalendar-page-memo';
  var EXPORT_VERSION = 1;

  var listEl = document.getElementById('list');
  var statsEl = document.getElementById('stats');
  var toastEl = document.getElementById('toast');
  var fileInput = document.getElementById('file-input');

  var store = {};

  function uid() {
    return 'm' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  }

  function fmtDate(ts) {
    if (!ts) return '';
    try {
      var d = new Date(ts);
      return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0') +
        ' ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
    } catch (e) {
      return '';
    }
  }

  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add('show');
    window.clearTimeout(toast._timer);
    toast._timer = window.setTimeout(function () { toastEl.classList.remove('show'); }, 2200);
  }

  function h(tag, className, text) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (text != null) el.textContent = text;
    return el;
  }

  function save() {
    return new Promise(function (resolve) {
      chrome.storage.local.set({ [STORE_KEY]: store }, function () { resolve(); });
    });
  }

  // ---------------------------------------------------------------- 렌더
  function pageLastUpdated(page) {
    var max = page.createdAt || 0;
    (page.memos || []).forEach(function (m) {
      if ((m.updatedAt || 0) > max) max = m.updatedAt || 0;
    });
    return max;
  }

  function render() {
    listEl.textContent = '';

    var keys = Object.keys(store);
    var memoTotal = 0;
    keys.forEach(function (k) {
      memoTotal += Array.isArray(store[k].memos) ? store[k].memos.length : 0;
    });

    statsEl.innerHTML = '';
    if (keys.length === 0) {
      listEl.appendChild(h('div', 'empty', '등록된 페이지 메모가 없습니다.\n웹 페이지에서 우클릭 → "해당 페이지 메모 등록" 으로 메모를 추가하세요.'));
      statsEl.textContent = '';
      return;
    }
    statsEl.appendChild(document.createTextNode('저장된 페이지 '));
    statsEl.appendChild(h('b', null, String(keys.length)));
    statsEl.appendChild(document.createTextNode('개 · 메모 '));
    statsEl.appendChild(h('b', null, String(memoTotal)));
    statsEl.appendChild(document.createTextNode('개'));

    keys.sort(function (a, b) { return pageLastUpdated(store[b]) - pageLastUpdated(store[a]); });

    keys.forEach(function (key) {
      var page = store[key] || {};
      var memos = Array.isArray(page.memos) ? page.memos : [];

      var card = h('div', 'pm-page');

      var head = h('div', 'pm-page-head');
      var info = h('div');
      info.style.flex = '1';
      info.style.minWidth = '0';
      var urlEl = h('div', 'pm-url', page.url || key);
      urlEl.title = page.url || key;
      info.appendChild(urlEl);

      var badges = h('div', 'pm-badges');
      badges.appendChild(h('span', 'pill pill-count', '메모 ' + memos.length + '개'));
      var ignored = (page.ignoreParams || []).slice();
      if (page.ignoreHash) ignored.push('#hash');
      if (ignored.length > 0) {
        badges.appendChild(h('span', 'pill pill-ignore', '무시: ' + ignored.join(', ')));
      }
      info.appendChild(badges);

      var pageDel = h('button', 'btn btn-danger btn-mini', '페이지 삭제');
      pageDel.type = 'button';
      pageDel.addEventListener('click', function () {
        if (window.confirm('이 페이지의 메모를 모두 삭제할까요?')) {
          delete store[key];
          save().then(render);
        }
      });

      head.appendChild(info);
      head.appendChild(pageDel);
      card.appendChild(head);

      memos.forEach(function (memo) {
        card.appendChild(buildMemoRow(key, memo));
      });

      listEl.appendChild(card);
    });
  }

  function buildMemoRow(key, memo) {
    var row = h('div', 'pm-memo');
    var left = h('div');
    left.style.flex = '1';
    left.style.minWidth = '0';

    var text = h('div', 'pm-memo-text');
    if (memo.text) {
      text.textContent = memo.text;
    } else {
      text.classList.add('empty');
      text.textContent = '(내용 없음)';
    }
    left.appendChild(text);
    left.appendChild(h('div', 'pm-memo-meta', '수정 ' + fmtDate(memo.updatedAt || memo.createdAt)));

    var actions = h('div', 'pm-memo-actions');

    var copyBtn = h('button', 'btn btn-mini', '복사');
    copyBtn.type = 'button';
    copyBtn.addEventListener('click', function () {
      var payload = (memo.text || '') + '\n\n(' + (store[key].url || key) + ')';
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(payload).then(function () { toast('메모를 복사했습니다.'); });
      }
    });

    var delBtn = h('button', 'btn btn-danger btn-mini', '삭제');
    delBtn.type = 'button';
    delBtn.addEventListener('click', function () {
      store[key].memos = (store[key].memos || []).filter(function (m) { return m.id !== memo.id; });
      if (store[key].memos.length === 0) delete store[key];
      save().then(render);
    });

    actions.appendChild(copyBtn);
    actions.appendChild(delBtn);

    row.appendChild(left);
    row.appendChild(actions);
    return row;
  }

  // ---------------------------------------------------------------- 내보내기
  function exportData() {
    var payload = {
      app: EXPORT_APP,
      version: EXPORT_VERSION,
      exportedAt: new Date().toISOString(),
      pages: store
    };
    var text = JSON.stringify(payload, null, 2);
    var now = new Date();
    var name = 'taskcalendar_page_memos_' + now.getFullYear() +
      String(now.getMonth() + 1).padStart(2, '0') + String(now.getDate()).padStart(2, '0') + '.json';

    var blob = new Blob([text], { type: 'application/json' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    window.setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    toast('내보내기 완료: ' + name);
  }

  // ---------------------------------------------------------------- 가져오기
  function currentImportMode() {
    var el = document.querySelector('input[name="importMode"]:checked');
    return el ? el.value : 'merge';
  }

  function normalizeImportedPage(page, key) {
    var p = page && typeof page === 'object' ? page : {};
    return {
      url: p.url || key,
      ignoreParams: Array.isArray(p.ignoreParams) ? p.ignoreParams : [],
      ignoreHash: !!p.ignoreHash,
      schema: p.schema || EXPORT_VERSION,
      createdAt: p.createdAt || Date.now(),
      memos: Array.isArray(p.memos) ? p.memos.filter(function (m) { return m && m.id; }) : []
    };
  }

  function mergeStore(incoming) {
    Object.keys(incoming).forEach(function (key) {
      var page = normalizeImportedPage(incoming[key], key);
      if (!store[key]) {
        store[key] = page;
        return;
      }
      var cur = store[key];
      var byId = {};
      (cur.memos || []).forEach(function (m) { byId[m.id] = m; });
      (page.memos || []).forEach(function (m) {
        var existing = byId[m.id];
        if (!existing || (m.updatedAt || 0) > (existing.updatedAt || 0)) byId[m.id] = m;
      });
      cur.memos = Object.keys(byId).map(function (id) { return byId[id]; })
        .sort(function (a, b) { return (a.createdAt || 0) - (b.createdAt || 0); });

      var merged = {};
      (cur.ignoreParams || []).concat(page.ignoreParams || []).forEach(function (p) { merged[p] = true; });
      cur.ignoreParams = Object.keys(merged);
      cur.ignoreHash = !!(cur.ignoreHash || page.ignoreHash);
      if (!cur.url) cur.url = page.url;
    });
    return store;
  }

  function overwriteStore(incoming) {
    var next = {};
    Object.keys(incoming).forEach(function (key) {
      next[key] = normalizeImportedPage(incoming[key], key);
    });
    return next;
  }

  function handleFile(file) {
    var reader = new FileReader();
    reader.onload = function () {
      var parsed;
      try {
        parsed = JSON.parse(String(reader.result || ''));
      } catch (e) {
        toast('JSON 파일을 읽을 수 없습니다.');
        return;
      }
      var pages = parsed && parsed.pages && typeof parsed.pages === 'object' ? parsed.pages : null;
      if (!pages) {
        toast('올바른 페이지 메모 내보내기 파일이 아닙니다.');
        return;
      }

      var mode = currentImportMode();
      var incomingCount = Object.keys(pages).length;
      var message = mode === 'overwrite'
        ? '현재 ' + Object.keys(store).length + '개 페이지를 모두 지우고 ' + incomingCount + '개 페이지로 덮어씁니다. 계속할까요?'
        : incomingCount + '개 페이지를 현재 데이터에 병합합니다. 계속할까요?';
      if (!window.confirm(message)) return;

      store = mode === 'overwrite' ? overwriteStore(pages) : mergeStore(pages);
      save().then(function () {
        render();
        toast('가져오기 완료 (' + (mode === 'overwrite' ? '덮어쓰기' : '병합') + ')');
      });
    };
    reader.readAsText(file, 'utf-8');
  }

  // ---------------------------------------------------------------- 초기화
  document.getElementById('btn-export').addEventListener('click', exportData);
  document.getElementById('btn-import').addEventListener('click', function () { fileInput.click(); });
  fileInput.addEventListener('change', function () {
    if (fileInput.files && fileInput.files[0]) handleFile(fileInput.files[0]);
    fileInput.value = '';
  });

  chrome.storage.local.get(STORE_KEY, function (res) {
    var value = res && res[STORE_KEY];
    store = value && typeof value === 'object' ? value : {};
    render();
  });

  chrome.storage.onChanged.addListener(function (changes, area) {
    if (area !== 'local' || !changes[STORE_KEY]) return;
    var value = changes[STORE_KEY].newValue;
    store = value && typeof value === 'object' ? value : {};
    render();
  });
})();
