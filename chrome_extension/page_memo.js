// page_memo.js - 브라우저 페이지 위에 뜨는 플로팅 페이지 메모 (상단 프레임 전용)
(function () {
  'use strict';

  // 메모는 상단 프레임에만 표시한다. (iframe 중복 주입 방지)
  if (window.top !== window) return;

  var STORE_KEY = 'tc_page_memos';
  var HOST_ID = '__tc_page_memo_host';
  var CARD_WIDTH = 290;
  var CARD_HEIGHT = 270;
  var SCHEMA_VERSION = 1;

  // 메모 안에서 발생한 이벤트는 페이지(사이트 단축키/스크롤/뒤로가기 등)로 절대 새어나가지 않도록 격리한다.
  var ISOLATED_EVENTS = [
    'keydown', 'keyup', 'keypress',
    'mousedown', 'mouseup', 'click', 'dblclick', 'contextmenu',
    'input', 'change', 'paste', 'cut', 'copy', 'dragstart'
  ];

  var shadowRoot = null;
  var cardLayer = null;
  var store = {};
  var lastHref = location.href;

  // ---------------------------------------------------------------- 유틸
  function uid() {
    return 'm' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  }

  // URL 매칭용 정규화: ignoreParams 제거 + 파라미터 순서 정렬 + (옵션) 해시 제거
  function normalizeUrl(rawUrl, ignoreParams, ignoreHash) {
    try {
      var u = new URL(String(rawUrl || ''), location.href);
      var ignore = {};
      (ignoreParams || []).forEach(function (p) { ignore[String(p)] = true; });
      var entries = [];
      u.searchParams.forEach(function (value, key) {
        if (!ignore[key]) entries.push([key, value]);
      });
      entries.sort(function (a, b) {
        if (a[0] !== b[0]) return a[0] < b[0] ? -1 : 1;
        if (a[1] !== b[1]) return a[1] < b[1] ? -1 : 1;
        return 0;
      });
      var qs = entries.map(function (pair) {
        return encodeURIComponent(pair[0]) + '=' + encodeURIComponent(pair[1]);
      }).join('&');
      var hash = ignoreHash ? '' : u.hash;
      return u.origin + u.pathname + (qs ? '?' + qs : '') + hash;
    } catch (e) {
      return String(rawUrl || '');
    }
  }

  function pageUrl(pageKey) {
    var page = store[pageKey] || {};
    return page.url || pageKey;
  }

  function findMatchingPageKey(currentUrl) {
    if (store[currentUrl]) return currentUrl;
    var keys = Object.keys(store);
    for (var i = 0; i < keys.length; i++) {
      var key = keys[i];
      var page = store[key] || {};
      var target = normalizeUrl(pageUrl(key), page.ignoreParams, page.ignoreHash);
      var current = normalizeUrl(currentUrl, page.ignoreParams, page.ignoreHash);
      if (target === current) return key;
    }
    return null;
  }

  function loadStore() {
    return new Promise(function (resolve) {
      chrome.storage.local.get(STORE_KEY, function (res) {
        var value = res && res[STORE_KEY];
        resolve(value && typeof value === 'object' ? value : {});
      });
    });
  }

  function saveStore() {
    return new Promise(function (resolve) {
      chrome.storage.local.set({ [STORE_KEY]: store }, function () { resolve(); });
    });
  }

  function clampPosition(x, y) {
    var cx = Number.isFinite(x) ? x : window.innerWidth - CARD_WIDTH - 24;
    var cy = Number.isFinite(y) ? y : 90;
    var maxX = Math.max(8, window.innerWidth - CARD_WIDTH - 8);
    var maxY = Math.max(8, window.innerHeight - 48);
    return { x: Math.min(Math.max(8, cx), maxX), y: Math.min(Math.max(8, cy), maxY) };
  }

  function h(tag, className, text) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (text != null) el.textContent = text;
    return el;
  }

  function pageLabel(pageKey) {
    try {
      var u = new URL(pageUrl(pageKey));
      var label = u.hostname + u.pathname + (u.search || '');
      return label.length > 60 ? label.slice(0, 57) + '…' : label;
    } catch (e) {
      return String(pageKey || '').slice(0, 60);
    }
  }

  // ---------------------------------------------------------------- Shadow DOM
  var STYLE = [
    '.tc-pm-card{position:fixed;width:' + CARD_WIDTH + 'px;height:' + CARD_HEIGHT + 'px;background:#fff;border:1px solid #CBD5E1;border-radius:8px;',
    'box-shadow:0 8px 24px rgba(15,23,42,.18);font-family:-apple-system,BlinkMacSystemFont,"Malgun Gothic","Segoe UI",Roboto,sans-serif;',
    'font-size:12px;color:#1E293B;z-index:2147483647;pointer-events:auto;overflow:hidden;line-height:1.4;box-sizing:border-box;',
    'display:flex;flex-direction:column;}',
    '.tc-pm-card:not(.collapsed){resize:both;min-width:220px;min-height:180px;max-width:90vw;max-height:85vh;}',
    '.tc-pm-card.collapsed{width:44px !important;height:44px !important;min-width:44px !important;min-height:44px !important;',
    'background:transparent !important;border:none !important;box-shadow:none !important;resize:none !important;',
    'border-radius:50% !important;overflow:visible !important;}',
    '.tc-pm-card.collapsed .tc-pm-head,.tc-pm-card.collapsed .tc-pm-body{display:none !important;}',
    '.tc-pm-bubble{display:none;width:44px;height:44px;border-radius:50%;background:linear-gradient(135deg, #2563EB, #1D4ED8);',
    'box-shadow:0 4px 14px rgba(37,99,235,.45), 0 2px 6px rgba(0,0,0,.2);align-items:center;justify-content:center;color:#fff;',
    'font-size:20px;cursor:pointer;user-select:none;position:relative;transition:transform .15s ease, box-shadow .15s ease;}',
    '.tc-pm-card.collapsed .tc-pm-bubble{display:flex !important;}',
    '.tc-pm-bubble:hover{transform:scale(1.08);box-shadow:0 6px 18px rgba(37,99,235,.55), 0 3px 8px rgba(0,0,0,.25);}',
    '.tc-pm-head{display:flex;align-items:center;gap:4px;background:#2563EB;color:#fff;padding:6px 8px;cursor:move;user-select:none;flex-shrink:0;}',
    '.tc-pm-grip{cursor:move;opacity:.85;font-size:13px;}',
    '.tc-pm-title{flex:1;font-size:11px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}',
    '.tc-pm-icon{border:none;background:rgba(255,255,255,.16);color:#fff;width:20px;height:20px;border-radius:4px;cursor:pointer;',
    'font-size:12px;line-height:1;display:inline-flex;align-items:center;justify-content:center;padding:0;transition:background .12s;}',
    '.tc-pm-icon:hover{background:rgba(255,255,255,.36);}',
    '.tc-pm-del:hover{background:#DC2626;}',
    '.tc-pm-body{padding:8px;flex:1;min-height:0;display:flex;flex-direction:column;overflow:hidden;}',
    '.tc-pm-text{white-space:pre-wrap;word-break:break-word;line-height:1.5;overflow-y:auto;flex:1;min-height:0;}',
    '.tc-pm-text.empty{color:#94A3B8;font-style:italic;}',
    '.tc-pm-link{color:#2563EB;text-decoration:underline;word-break:break-all;cursor:pointer;}',
    '.tc-pm-link:hover{color:#1D4ED8;}',
    '.tc-pm-editor{display:none;flex-direction:column;gap:6px;flex:1;min-height:0;overflow:hidden;}',
    '.tc-pm-card.editing .tc-pm-editor{display:flex;}',
    '.tc-pm-card.editing .tc-pm-text{display:none;}',
    '.tc-pm-title-input{width:100%;box-sizing:border-box;border:1px solid #CBD5E1;border-radius:4px;padding:5px 7px;',
    'font-family:inherit;font-size:12px;font-weight:600;color:#1E293B;background:#FAFAFA;flex-shrink:0;}',
    '.tc-pm-title-input:focus{outline:none;border-color:#2563EB;background:#fff;}',
    '.tc-pm-input{width:100%;min-height:60px;flex:1;resize:none;box-sizing:border-box;border:1px solid #CBD5E1;border-radius:4px;padding:6px;',
    'font-family:inherit;font-size:12px;line-height:1.5;color:#1E293B;background:#FAFAFA;overflow-y:auto;}',
    '.tc-pm-input:focus{outline:none;border-color:#2563EB;background:#fff;}',
    '.tc-pm-match{border:1px solid #E2E8F0;border-radius:4px;background:#F8FAFC;padding:4px 6px;font-size:11px;flex-shrink:0;max-height:85px;overflow-y:auto;}',
    '.tc-pm-match summary{cursor:pointer;color:#1D4ED8;font-weight:600;}',
    '.tc-pm-hint{color:#64748B;margin:4px 0;line-height:1.4;}',
    '.tc-pm-param{display:flex;align-items:center;gap:5px;padding:2px 0;word-break:break-all;}',
    '.tc-pm-param input{margin:0;accent-color:#2563EB;}',
    '.tc-pm-param span{color:#334155;}',
    '.tc-pm-actions{display:flex;gap:6px;flex-shrink:0;padding-top:2px;}',
    '.tc-pm-btn{flex:1;padding:6px;border-radius:4px;font-size:12px;font-weight:700;cursor:pointer;}',
    '.tc-pm-save{background:#2563EB;color:#fff;border:none;}',
    '.tc-pm-save:hover{background:#1D4ED8;}',
    '.tc-pm-cancel{background:#F1F5F9;color:#475569;border:1px solid #CBD5E1;font-weight:600;}',
    '.tc-pm-cancel:hover{background:#E2E8F0;}'
  ].join('');

  function linkify(text, container) {
    container.textContent = '';
    if (!text) {
      var empty = h('span', 'tc-pm-text empty', '(내용 없음)');
      container.appendChild(empty);
      return;
    }
    var URL_REGEX = /(https?:\/\/[^\s<]+|www\.[^\s<]+)/gi;
    var lastIdx = 0;
    var match;
    while ((match = URL_REGEX.exec(text)) !== null) {
      if (match.index > lastIdx) {
        container.appendChild(document.createTextNode(text.slice(lastIdx, match.index)));
      }
      var rawUrl = match[0];
      var href = rawUrl;
      if (!/^https?:\/\//i.test(href)) {
        href = 'https://' + href;
      }
      var a = document.createElement('a');
      a.className = 'tc-pm-link';
      a.href = href;
      a.textContent = rawUrl;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.title = href + ' (새 탭에서 열기)';
      a.addEventListener('click', function (e) {
        e.stopPropagation();
        window.open(this.href, '_blank');
      });
      a.addEventListener('mousedown', function (e) {
        e.stopPropagation();
      });
      container.appendChild(a);
      lastIdx = URL_REGEX.lastIndex;
    }
    if (lastIdx < text.length) {
      container.appendChild(document.createTextNode(text.slice(lastIdx)));
    }
  }

  function ensureUi() {
    var root = document.body || document.documentElement;
    if (!root) return null; // document_start 시점에는 아직 DOM이 없을 수 있다.

    var host = document.getElementById(HOST_ID);
    if (!host) {
      host = document.createElement('div');
      host.id = HOST_ID;
      host.style.cssText = 'all:initial;position:fixed;top:0;left:0;width:0;height:0;z-index:2147483647;';

      // 메모 내부 이벤트가 페이지로 버블링되지 않도록 host 경계에서 차단한다.
      ISOLATED_EVENTS.forEach(function (type) {
        host.addEventListener(type, function (e) { e.stopPropagation(); }, false);
      });

      root.appendChild(host);
      shadowRoot = host.attachShadow({ mode: 'open' });
      var style = document.createElement('style');
      style.textContent = STYLE;
      shadowRoot.appendChild(style);
      cardLayer = document.createElement('div');
      cardLayer.className = 'tc-pm-layer';
      shadowRoot.appendChild(cardLayer);
    } else if (!shadowRoot || !shadowRoot.host) {
      shadowRoot = host.shadowRoot;
      cardLayer = shadowRoot ? shadowRoot.querySelector('.tc-pm-layer') : null;
    }
    return cardLayer;
  }

  // ---------------------------------------------------------------- 카드
  function buildMatchBox(pageKey) {
    var page = store[pageKey] || {};
    var box = h('details', 'tc-pm-match');
    box.appendChild(h('summary', null, '⚙ 주소 매칭 설정'));
    box.appendChild(h('div', 'tc-pm-hint', '기본은 주소 전체로 매칭합니다. 아래 파라미터를 체크하면 매칭에서 무시합니다.'));

    var params = [];
    var hash = '';
    try {
      var u = new URL(pageUrl(pageKey));
      u.searchParams.forEach(function (v, k) { params.push([k, v]); });
      hash = u.hash;
    } catch (e) {}

    var ignore = {};
    (page.ignoreParams || []).forEach(function (p) { ignore[p] = true; });

    if (params.length === 0) {
      box.appendChild(h('div', 'tc-pm-hint', '무시할 주소 파라미터가 없습니다.'));
    } else {
      params.forEach(function (pair) {
        var row = h('label', 'tc-pm-param');
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = !!ignore[pair[0]];
        cb.dataset.param = pair[0];
        var span = h('span', null, pair[0] + ' = ' + (pair[1] || ''));
        row.appendChild(cb);
        row.appendChild(span);
        box.appendChild(row);
      });
    }

    if (hash) {
      var hrow = h('label', 'tc-pm-param');
      var hcb = document.createElement('input');
      hcb.type = 'checkbox';
      hcb.dataset.ignoreHash = '1';
      hcb.checked = !!page.ignoreHash;
      hrow.appendChild(hcb);
      hrow.appendChild(h('span', null, '해시(#) 무시 : ' + hash.slice(0, 24)));
      box.appendChild(hrow);
    }
    return box;
  }

  function applyMatchFromCard(card, pageKey) {
    var page = store[pageKey];
    if (!page) return;

    // 현재 URL에 없는 무시 파라미터도 보존한다.
    var params = (page.ignoreParams || []).slice();
    card.querySelectorAll('.tc-pm-match input[data-param]').forEach(function (cb) {
      var name = cb.dataset.param;
      var idx = params.indexOf(name);
      if (cb.checked && idx === -1) params.push(name);
      else if (!cb.checked && idx !== -1) params.splice(idx, 1);
    });

    var hashCb = card.querySelector('.tc-pm-match input[data-ignore-hash]');
    page.ignoreParams = params;
    page.ignoreHash = hashCb ? hashCb.checked : !!page.ignoreHash;
  }

  function attachDrag(card, grip, memo) {
    grip.addEventListener('mousedown', function (e) {
      e.preventDefault();
      e.stopPropagation();
      var rect = card.getBoundingClientRect();
      var startX = e.clientX;
      var startY = e.clientY;
      var origX = rect.left;
      var origY = rect.top;
      var dragging = true;

      function onMove(ev) {
        if (!dragging) return;
        var pos = clampPosition(origX + (ev.clientX - startX), origY + (ev.clientY - startY));
        card.style.left = pos.x + 'px';
        card.style.top = pos.y + 'px';
      }
      function onUp() {
        dragging = false;
        document.removeEventListener('mousemove', onMove, true);
        document.removeEventListener('mouseup', onUp, true);
        var r = card.getBoundingClientRect();
        memo.x = Math.round(r.left);
        memo.y = Math.round(r.top);
        memo.updatedAt = Date.now();
        saveStore();
      }
      document.addEventListener('mousemove', onMove, true);
      document.addEventListener('mouseup', onUp, true);
    });
  }

  function attachBubbleDragAndClick(card, bubble, memo, onExpand) {
    bubble.addEventListener('mousedown', function (e) {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      var rect = card.getBoundingClientRect();
      var startX = e.clientX;
      var startY = e.clientY;
      var origX = rect.left;
      var origY = rect.top;
      var moved = false;
      var dragging = true;

      function onMove(ev) {
        if (!dragging) return;
        var dx = ev.clientX - startX;
        var dy = ev.clientY - startY;
        if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
          moved = true;
        }
        var pos = clampPosition(origX + dx, origY + dy);
        card.style.left = pos.x + 'px';
        card.style.top = pos.y + 'px';
      }
      function onUp() {
        dragging = false;
        document.removeEventListener('mousemove', onMove, true);
        document.removeEventListener('mouseup', onUp, true);
        var r = card.getBoundingClientRect();
        memo.x = Math.round(r.left);
        memo.y = Math.round(r.top);
        memo.updatedAt = Date.now();
        saveStore();

        if (!moved) {
          onExpand();
        }
      }
      document.addEventListener('mousemove', onMove, true);
      document.addEventListener('mouseup', onUp, true);
    });
  }

  function buildCard(pageKey, memo, startEditing) {
    var card = h('div', 'tc-pm-card');
    card.dataset.memoId = memo.id;
    var pos = clampPosition(memo.x, memo.y);
    card.style.left = pos.x + 'px';
    card.style.top = pos.y + 'px';
    if (memo.collapsed) {
      card.classList.add('collapsed');
    } else {
      var initW = Math.max(memo.width || CARD_WIDTH, 240);
      var initH = Math.max(memo.height || CARD_HEIGHT, 240);
      card.style.width = initW + 'px';
      card.style.height = initH + 'px';
    }
    if (startEditing) card.classList.add('editing');

    // 1. 최소화 시 표시되는 스마트폰 앱 스타일 원모양 버블
    var bubble = h('div', 'tc-pm-bubble');
    var bubbleIcon = h('span', null, '📝');
    bubble.appendChild(bubbleIcon);
    bubble.title = (memo.title ? memo.title : '페이지 메모') + '\n(클릭하여 펼치기)';
    card.appendChild(bubble);

    attachBubbleDragAndClick(card, bubble, memo, function () {
      memo.collapsed = false;
      memo.updatedAt = Date.now();
      card.classList.remove('collapsed');
      var cw = Math.max(memo.width || CARD_WIDTH, 240);
      var ch = Math.max(memo.height || CARD_HEIGHT, 240);
      card.style.width = cw + 'px';
      card.style.height = ch + 'px';
      saveStore();
    });

    // 2. 카드 상단 헤더 (주소는 숨기고 작성한 제목 또는 기본 문구 표시)
    var head = h('div', 'tc-pm-head');
    var grip = h('span', 'tc-pm-grip', '⠿');
    var title = h('span', 'tc-pm-title', memo.title || '페이지 메모');
    title.title = memo.title || '페이지 메모';
    var btnCollapse = h('button', 'tc-pm-icon', '─');
    btnCollapse.type = 'button';
    btnCollapse.title = '접기 (알람 원모양으로 최소화)';
    var btnSendApp = h('button', 'tc-pm-icon', '📥');
    btnSendApp.type = 'button';
    btnSendApp.title = '바로업무로 보내기 (바탕화면 메모로 저장)';
    var btnEdit = h('button', 'tc-pm-icon', '✎');
    btnEdit.type = 'button';
    btnEdit.title = '편집';
    var btnDel = h('button', 'tc-pm-icon tc-pm-del', '✕');
    btnDel.type = 'button';
    btnDel.title = '삭제';
    head.appendChild(grip);
    head.appendChild(title);
    head.appendChild(btnCollapse);
    head.appendChild(btnSendApp);
    head.appendChild(btnEdit);
    head.appendChild(btnDel);

    // 3. 카드 본문 (링크 자동 감지 클릭 지원)
    var body = h('div', 'tc-pm-body');
    var textView = h('div', 'tc-pm-text');
    linkify(memo.text, textView);

    // 4. 에디터 (헤더 제목 입력 필드 + 본문 입력)
    var editor = h('div', 'tc-pm-editor');
    var titleInput = h('input', 'tc-pm-title-input');
    titleInput.type = 'text';
    titleInput.placeholder = '메모 제목 (선택사항)';
    titleInput.value = memo.title || '';

    var ta = h('textarea', 'tc-pm-input');
    ta.value = memo.text || '';
    ta.placeholder = '이 페이지에 대한 메모를 입력하세요 (링크 주소 입력 시 자동 연결)';
    var matchBox = buildMatchBox(pageKey);
    var actions = h('div', 'tc-pm-actions');
    var btnSave = h('button', 'tc-pm-btn tc-pm-save', '저장');
    btnSave.type = 'button';
    var btnCancel = h('button', 'tc-pm-btn tc-pm-cancel', '취소');
    btnCancel.type = 'button';
    actions.appendChild(btnSave);
    actions.appendChild(btnCancel);
    editor.appendChild(titleInput);
    editor.appendChild(ta);
    editor.appendChild(matchBox);
    editor.appendChild(actions);

    body.appendChild(textView);
    body.appendChild(editor);
    card.appendChild(head);
    card.appendChild(body);

    attachDrag(card, head, memo);

    // 카드 크기 조절 후 마우스 업 시 크기 저장
    card.addEventListener('mouseup', function () {
      if (!memo.collapsed && !card.classList.contains('collapsed')) {
        var resizedW = Math.round(card.offsetWidth);
        var resizedH = Math.round(card.offsetHeight);
        if (resizedW > 120 && resizedH > 80 && (resizedW !== memo.width || resizedH !== memo.height)) {
          memo.width = resizedW;
          memo.height = resizedH;
          memo.updatedAt = Date.now();
          saveStore();
        }
      }
    });

    btnCollapse.addEventListener('click', function () {
      memo.collapsed = true;
      memo.updatedAt = Date.now();
      card.classList.add('collapsed');
      card.style.width = '';
      card.style.height = '';
      saveStore();
    });

    btnSendApp.addEventListener('click', function () {
      var mTitle = memo.title || '웹 페이지 메모';
      var mText = memo.text || '';
      var curUrl = pageUrl(pageKey);
      var qs = 'type=memo&title=' + encodeURIComponent(mTitle) +
               '&desc=' + encodeURIComponent(mText) +
               '&url=' + encodeURIComponent(curUrl);
      var targetUrl = 'taskcalendar://add?' + qs;
      chrome.runtime.sendMessage({ action: 'openProtocolUrl', url: targetUrl });
    });

    btnEdit.addEventListener('click', function () {
      titleInput.value = memo.title || '';
      ta.value = memo.text || '';
      card.classList.add('editing');
      titleInput.focus();
    });

    btnSave.addEventListener('click', function () {
      memo.title = titleInput.value.trim();
      memo.text = ta.value.replace(/\s+$/, '');
      memo.updatedAt = Date.now();
      applyMatchFromCard(card, pageKey);
      saveStore();
      render();
    });

    function exitEditing() {
      if (!memo.text && !memo.title) {
        removeMemo(pageKey, memo.id);
      } else {
        card.classList.remove('editing');
      }
    }

    // ESC 로 편집 종료 (페이지로 키가 새어나가지 않도록 preventDefault + 격리)
    titleInput.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        exitEditing();
      }
    });
    ta.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        exitEditing();
      }
    });

    btnCancel.addEventListener('click', exitEditing);

    btnDel.addEventListener('click', function () {
      if (window.confirm('이 페이지 메모를 삭제할까요?')) {
        removeMemo(pageKey, memo.id);
      }
    });

    return card;
  }

  // ---------------------------------------------------------------- 렌더 / CRUD
  function render(opts) {
    opts = opts || {};
    var layer = ensureUi();
    if (!layer) return;
    layer.textContent = '';

    var currentUrl = location.href;
    Object.keys(store).forEach(function (key) {
      var page = store[key] || {};
      var matchCurrent = normalizeUrl(currentUrl, page.ignoreParams, page.ignoreHash);
      if (normalizeUrl(pageUrl(key), page.ignoreParams, page.ignoreHash) !== matchCurrent) return;

      var memos = Array.isArray(page.memos) ? page.memos : [];
      memos.forEach(function (memo) {
        layer.appendChild(buildCard(key, memo, opts.editId === memo.id));
      });
    });

    if (opts.editId) {
      var card = layer.querySelector('[data-memo-id="' + opts.editId + '"]');
      if (card) {
        var inp = card.querySelector('.tc-pm-title-input') || card.querySelector('.tc-pm-input');
        if (inp) inp.focus();
      }
    }
  }

  function removeMemo(pageKey, memoId) {
    var page = store[pageKey];
    if (!page || !Array.isArray(page.memos)) return;
    page.memos = page.memos.filter(function (m) { return m.id !== memoId; });
    if (page.memos.length === 0) delete store[pageKey];
    saveStore();
    render();
  }

  function addNewMemo() {
    loadStore().then(function (loaded) {
      store = loaded;
      var currentUrl = location.href;
      var key = findMatchingPageKey(currentUrl);
      if (!key) {
        key = currentUrl;
        store[key] = {
          url: currentUrl,
          ignoreParams: [],
          ignoreHash: false,
          memos: [],
          schema: SCHEMA_VERSION,
          createdAt: Date.now()
        };
      }
      if (!Array.isArray(store[key].memos)) store[key].memos = [];

      var now = Date.now();
      var memo = {
        id: uid(),
        title: '',
        text: '',
        color: '#2563EB',
        x: window.innerWidth - CARD_WIDTH - 24,
        y: 90 + (store[key].memos.length * 28),
        width: CARD_WIDTH,
        height: CARD_HEIGHT,
        collapsed: false,
        createdAt: now,
        updatedAt: now
      };
      store[key].memos.push(memo);
      saveStore().then(function () {
        render({ editId: memo.id });
      });
    });
  }

  // ---------------------------------------------------------------- 수명주기
  function onUrlMaybeChanged() {
    if (location.href === lastHref) return;
    lastHref = location.href;
    render();
  }

  function bootstrap() {
    loadStore().then(function (loaded) {
      store = loaded;
      render();
    });

    // document_start 주입 대비: DOM 준비 후 한 번 더 렌더
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () { render(); });
    }

    window.addEventListener('popstate', onUrlMaybeChanged);
    window.addEventListener('hashchange', onUrlMaybeChanged);
    ['pushState', 'replaceState'].forEach(function (fn) {
      var orig = history[fn];
      if (typeof orig !== 'function') return;
      history[fn] = function () {
        var result = orig.apply(this, arguments);
        window.setTimeout(onUrlMaybeChanged, 0);
        return result;
      };
    });
    window.setInterval(onUrlMaybeChanged, 1500);

    var resizeTimer = null;
    window.addEventListener('resize', function () {
      if (resizeTimer) window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(function () { render(); }, 200);
    });

    chrome.storage.onChanged.addListener(function (changes, area) {
      if (area !== 'local' || !changes[STORE_KEY]) return;
      // 다른 탭/관리 페이지에서 변경된 경우에만 반영 (편집 중이면 건너뜀)
      if (cardLayer && cardLayer.querySelector('.tc-pm-card.editing')) return;
      var value = changes[STORE_KEY].newValue;
      store = value && typeof value === 'object' ? value : {};
      render();
    });

    chrome.runtime.onMessage.addListener(function (msg, sender, sendResponse) {
      if (msg && msg.action === 'startPageMemo') {
        addNewMemo();
        sendResponse({ ok: true });
        return true;
      }
      return false;
    });
  }

  bootstrap();
})();
