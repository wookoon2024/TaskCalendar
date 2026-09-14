// content.js - 우클릭 대상, 마우스 호버 대상 및 화면 직접 찍기(피커) 기능
(function() {
  // [헬퍼] 대상 요소에서 최적의 행/카드 컨테이너 탐색 (Gmail tr.zA, 게시판 tr, li, article 등)
  function findRowContainer(target) {
    if (!target) return null;
    var a = target.closest ? target.closest('a') : null;

    // 1. 가장 가까운 단일 항목(tr, [role="row"], li, article) 탐색
    var container = null;
    if (target.closest) {
      container = target.closest('tr, [role="row"], li, article');
    }
    if (!container && a && a.closest) {
      container = a.closest('tr, [role="row"], li, article');
    }

    // 2. ul 또는 ol의 직계 자식 탐색
    if (!container) {
      var list = (target.closest ? target.closest('ul, ol') : null) || (a && a.closest ? a.closest('ul, ol') : null);
      if (list) {
        var curr = target || a;
        while (curr && curr.parentElement && curr.parentElement !== list) {
          curr = curr.parentElement;
        }
        if (curr && curr.parentElement === list) {
          container = curr;
        }
      }
    }

    // 3. 개별 카드/아이템 클래스 탐색
    if (!container && target.closest) {
      var card = target.closest('[class*="card"], [class*="item"], [class*="row"], [class*="post"], div.bx');
      if (card) {
        var cls = (card.className || '').toLowerCase();
        if (!cls.includes('list') && !cls.includes('grid') && !cls.includes('wrap') && !cls.includes('total')) {
          container = card;
        }
      }
    }

    // 4. a 태그 자체가 블록형 카드인 경우
    if (!container && a && a.childElementCount >= 1 && (a.offsetHeight >= 24 || a.offsetWidth >= 100)) {
      container = a;
    }

    // 5. 반복되는 형제 요소 탐색
    if (!container && target) {
      var p = target;
      for (var i = 0; i < 4 && p && p.parentElement && p.parentElement !== document.body; i++) {
        var parent = p.parentElement;
        if (parent && parent.children.length >= 2) {
          var sameTagCount = 0;
          for (var k = 0; k < parent.children.length; k++) {
            if (parent.children[k].tagName === p.tagName) sameTagCount++;
          }
          if (sameTagCount >= 2 && p.offsetHeight >= 20) {
            container = p;
            break;
          }
        }
        p = parent;
      }
    }

    return container || a || target;
  }

  // 1. 마우스 호버(Hover) 위치 상시 추적 (툴바 클릭이나 단축키 실행 시 마우스가 올려져 있던 행 타겟팅)
  window.addEventListener('mousemove', function(e) {
    document.__tcHoverTarget = e.target;
    document.__tcHoverContainer = findRowContainer(e.target);
    document.__tcLastMouseMoveTime = Date.now();
  }, { passive: true, capture: true });

  // 2. 우클릭 대상 캡처 (Capture Phase로 웹앱의 자체 이벤트 간섭 방지)
  window.addEventListener('contextmenu', function(e) {
    var target = e.target;
    document.__tcTarget = target;
    var a = target && target.closest ? target.closest('a') : null;
    document.__tcLink = a;
    document.__tcContainer = findRowContainer(target);
    document.__tcLastContextMenuTime = Date.now();
  }, true);

  // 3. 🎯 화면에서 직접 찍기 (Visual Element Picker)
  var isPickerActive = false;
  var pickerHighlightEl = null;
  var pickerBanner = null;

  function startElementPicker() {
    if (isPickerActive) return;
    isPickerActive = true;

    // 상단 안내 배너 생성 (최상위 창에서만 1개 노출)
    if (window === window.top && !pickerBanner) {
      pickerBanner = document.createElement('div');
      pickerBanner.id = '__tc_picker_banner';
      pickerBanner.innerHTML = '<div style="display:flex;align-items:center;gap:12px;">' +
        '<span style="font-size:18px;">🎯</span>' +
        '<span style="font-weight:bold;font-size:13px;color:#fff;">등록할 메일이나 게시글 행을 마우스로 클릭하세요</span>' +
        '<span style="font-size:11px;color:#e0e7ff;background:rgba(255,255,255,0.2);padding:2px 8px;border-radius:4px;">취소: ESC</span>' +
        '</div>';
      Object.assign(pickerBanner.style, {
        position: 'fixed',
        top: '16px',
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: '2147483647',
        background: '#6C5CE7',
        padding: '8px 18px',
        borderRadius: '24px',
        boxShadow: '0 8px 24px rgba(108, 92, 231, 0.45), 0 2px 6px rgba(0,0,0,0.15)',
        fontFamily: '-apple-system, BlinkMacSystemFont, "Malgun Gothic", sans-serif',
        pointerEvents: 'none',
        userSelect: 'none'
      });
      document.body.appendChild(pickerBanner);
    }

    // 하이라이트 테두리 상자 생성
    if (!pickerHighlightEl) {
      pickerHighlightEl = document.createElement('div');
      pickerHighlightEl.id = '__tc_picker_highlight';
      Object.assign(pickerHighlightEl.style, {
        position: 'absolute',
        pointerEvents: 'none',
        zIndex: '2147483646',
        border: '2.5px dashed #6C5CE7',
        backgroundColor: 'rgba(108, 92, 231, 0.12)',
        borderRadius: '5px',
        boxSizing: 'border-box',
        transition: 'top 0.06s ease, left 0.06s ease, width 0.06s ease, height 0.06s ease',
        display: 'none'
      });
      document.body.appendChild(pickerHighlightEl);
    }

    document.addEventListener('mousemove', onPickerMouseMove, true);
    document.addEventListener('click', onPickerClick, true);
    document.addEventListener('keydown', onPickerKeyDown, true);
  }

  function stopElementPicker() {
    isPickerActive = false;
    if (pickerBanner && pickerBanner.parentNode) {
      pickerBanner.parentNode.removeChild(pickerBanner);
      pickerBanner = null;
    }
    if (pickerHighlightEl && pickerHighlightEl.parentNode) {
      pickerHighlightEl.parentNode.removeChild(pickerHighlightEl);
      pickerHighlightEl = null;
    }
    document.removeEventListener('mousemove', onPickerMouseMove, true);
    document.removeEventListener('click', onPickerClick, true);
    document.removeEventListener('keydown', onPickerKeyDown, true);
  }

  function onPickerMouseMove(e) {
    if (!isPickerActive || !pickerHighlightEl) return;
    var target = e.target;
    if (target === pickerBanner || target === pickerHighlightEl) return;
    var container = findRowContainer(target) || target;
    var rect = container.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      pickerHighlightEl.style.display = 'block';
      pickerHighlightEl.style.top = (rect.top + window.scrollY) + 'px';
      pickerHighlightEl.style.left = (rect.left + window.scrollX) + 'px';
      pickerHighlightEl.style.width = rect.width + 'px';
      pickerHighlightEl.style.height = rect.height + 'px';
    }
  }

  function onPickerClick(e) {
    if (!isPickerActive) return;
    e.preventDefault();
    e.stopPropagation();

    var target = e.target;
    var container = findRowContainer(target) || target;
    document.__tcTarget = target;
    document.__tcContainer = container;
    document.__tcHoverTarget = target;
    document.__tcHoverContainer = container;
    var a = target && target.closest ? target.closest('a') : null;
    if (!a && container) {
      a = container.querySelector('a[href]');
    }
    document.__tcLink = a;

    // 블록 드래그 텍스트 해제 (내용 수집 원천 차단)
    try {
      if (window.getSelection) {
        window.getSelection().removeAllRanges();
      }
    } catch(err) {}

    stopElementPicker();

    // 백그라운드에 선택 완료 알림
    try {
      chrome.runtime.sendMessage({
        action: "elementPickerCompleted",
        linkUrl: a ? a.href : "",
        frameUrl: window.location.href
      });
    } catch(err) {}
  }

  function onPickerKeyDown(e) {
    if (e.key === 'Escape') {
      stopElementPicker();
      try {
        chrome.runtime.sendMessage({ action: "elementPickerCancelled" });
      } catch(err) {}
    }
  }

  // 메시지 리스너
  chrome.runtime.onMessage.addListener(function(msg, sender, sendResponse) {
    if (msg.action === "startElementPicker") {
      startElementPicker();
      sendResponse({ success: true });
      return true;
    } else if (msg.action === "stopElementPicker") {
      stopElementPicker();
      sendResponse({ success: true });
      return true;
    }
  });
})();
