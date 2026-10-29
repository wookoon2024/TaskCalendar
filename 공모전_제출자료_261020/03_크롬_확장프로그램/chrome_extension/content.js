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
  var activeTargetField = 'all';

  function getBannerGuideText() {
    var fieldNames = {
      'department': '부서',
      'author': '기안자(작성자)',
      'title': '제목',
      'date': '날짜',
      'category': '분류',
      'status': '상태',
      'desc': '비고'
    };
    var label = fieldNames[activeTargetField];
    return label
      ? (label + '로 등록할 항목을 마우스로 클릭하세요')
      : '등록할 메일이나 게시글 행을 마우스로 클릭하세요';
  }

  function updateBannerText() {
    if (!pickerBanner) return;
    var textEl = pickerBanner.querySelector('.tc-picker-guide-text');
    if (textEl) {
      textEl.textContent = getBannerGuideText();
    }
  }

  function startElementPicker(targetField) {
    activeTargetField = targetField || 'all';
    if (isPickerActive) {
      updateBannerText();
      return;
    }
    isPickerActive = true;

    // 상단 안내 배너 생성 (최상위 창에서만 1개 노출)
    if (window === window.top && !pickerBanner) {
      pickerBanner = document.createElement('div');
      pickerBanner.id = '__tc_picker_banner';
      pickerBanner.innerHTML = '<div style="display:flex;align-items:center;gap:12px;">' +
        '<span style="font-size:18px;">🎯</span>' +
        '<span class="tc-picker-guide-text" style="font-weight:bold;font-size:13px;color:#fff;">' + getBannerGuideText() + '</span>' +
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
    if (target === pickerBanner || target === pickerHighlightEl || (pickerBanner && pickerBanner.contains(target))) return;

    var highlightTarget = target;
    if (activeTargetField === 'all') {
      highlightTarget = findRowContainer(target) || target;
    } else {
      highlightTarget = target;
    }

    var rect = highlightTarget.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      pickerHighlightEl.style.display = 'block';
      pickerHighlightEl.style.top = (rect.top + window.scrollY) + 'px';
      pickerHighlightEl.style.left = (rect.left + window.scrollX) + 'px';
      pickerHighlightEl.style.width = rect.width + 'px';
      pickerHighlightEl.style.height = rect.height + 'px';
    }
  }

  function buildElementSelector(el) {
    if (!el || el === document.body || el === document.documentElement) return '';

    // 1. el 자체의 고유 ID 검사 (순수 숫자, 동적 세션 식별자 제외)
    if (el.id && !/^\d+$/.test(el.id) && !/^[:_]/.test(el.id) && !el.id.includes('__') && !el.id.startsWith('tc-')) {
      try {
        var idSel = '#' + CSS.escape(el.id);
        if (document.querySelectorAll(idSel).length === 1) return idSel;
      } catch(e) {}
    }

    // 2. 표(Table) 헤더/레이블 기반 매칭 (한국 공공 전자결재/공문서/게시판 최적화)
    var td = el.closest ? el.closest('td, th') : null;
    if (td) {
      // 이전 th/td 형제 셀에 레이블이 있는 경우: <tr><th>기안부서</th><td>운영지원과</td></tr>
      var prev = td.previousElementSibling;
      while (prev && prev.tagName !== 'TH' && prev.tagName !== 'TD') {
        prev = prev.previousElementSibling;
      }
      if (prev) {
        var prevTxt = (prev.innerText || prev.textContent || '').replace(/[\s\n\r\t]+/g, '').trim();
        var isKnownLabel = /^(기안부서|담당부서|소속부서|발신부서|수신부서|처리부서|소속|소속기관|부서|부서명|기안자|작성자|담당자|기안인|작성인|등록자|글쓴이|기안자명|작성자명|기안일자|기안일|작성일자|작성일|등록일자|등록일|시행일자|일자|문서제목|제목|안건명|과제명|조회|조회수|추천|추천수)$/i.test(prevTxt);
        if (prevTxt && isKnownLabel) {
          var labelCandidate = prev.tagName.toLowerCase() + ':has-text("' + prevTxt + '") + ' + td.tagName.toLowerCase();
          if (el !== td) {
            var subPath = el.tagName.toLowerCase();
            if (el.className && typeof el.className === 'string') {
              var subCls = el.className.trim().split(/\s+/).filter(function(c) {
                return c && !c.startsWith('tc-') && !/^\d+$/.test(c);
              })[0];
              if (subCls) subPath += '.' + CSS.escape(subCls);
            }
            labelCandidate += ' ' + subPath;
          }
          return labelCandidate;
        }
      }
    }

    // 3. 안정적인 상위 컨테이너(ID 또는 대표 클래스) 탐색
    var stableAncestor = null;
    var ancestorSel = '';
    var currP = el.parentElement;
    while (currP && currP !== document.body && currP !== document.documentElement) {
      if (currP.id && !/^\d+$/.test(currP.id) && !currP.id.startsWith('tc-') && !currP.id.includes('__')) {
        try {
          var pIdSel = '#' + CSS.escape(currP.id);
          if (document.querySelectorAll(pIdSel).length === 1) {
            stableAncestor = currP;
            ancestorSel = pIdSel;
            break;
          }
        } catch(e) {}
      }
      if (currP.className && typeof currP.className === 'string') {
        var pClasses = currP.className.trim().split(/\s+/).filter(function(c) {
          return c && !c.startsWith('tc-') && !c.includes(':') && !/^\d+$/.test(c) &&
                 /view|content|article|board|post|detail|doc|sub-top|sub_top|bbs|item|wrap/i.test(c);
        });
        for (var pi = 0; pi < pClasses.length; pi++) {
          var pCls = '.' + CSS.escape(pClasses[pi]);
          try {
            if (document.querySelectorAll(pCls).length === 1) {
              stableAncestor = currP;
              ancestorSel = pCls;
              break;
            }
          } catch(e) {}
        }
        if (stableAncestor) break;
      }
      currP = currP.parentElement;
    }

    // 4. el 자체의 클래스 검사
    if (el.className && typeof el.className === 'string') {
      var classes = el.className.trim().split(/\s+/).filter(function(c) {
        return c && !c.startsWith('tc-') && !c.includes(':') && !c.includes('/') && !/^\d+$/.test(c);
      });
      // 4-a. 문서 전체에서 고유한 클래스
      for (var k = 0; k < classes.length; k++) {
        var cls = '.' + CSS.escape(classes[k]);
        try {
          if (document.querySelectorAll(cls).length === 1) return cls;
        } catch(e) {}
      }
      // 4-b. 상위 컨테이너 내부에서 고유한 클래스
      if (stableAncestor && ancestorSel) {
        for (var m = 0; m < classes.length; m++) {
          var subCls = ancestorSel + ' .' + CSS.escape(classes[m]);
          try {
            if (document.querySelectorAll(subCls).length === 1) return subCls;
          } catch(e) {}
        }
      }
    }

    // 5. 상위 컨테이너(또는 body) 기준 경로 탐색
    var boundary = stableAncestor || document.body;
    var path = [];
    var curr = el;
    while (curr && curr !== boundary && curr !== document.body && curr !== document.documentElement) {
      var tag = curr.tagName.toLowerCase();
      var parent = curr.parentElement;
      if (!parent) break;

      var stepSel = tag;
      var hasUniqueClassInParent = false;
      if (curr.className && typeof curr.className === 'string') {
        var clList = curr.className.trim().split(/\s+/).filter(function(c) {
          return c && !c.startsWith('tc-') && !c.includes(':') && !/^\d+$/.test(c);
        });
        if (clList.length > 0) {
          try {
            if (parent.querySelectorAll('.' + CSS.escape(clList[0])).length === 1) {
              stepSel = tag + '.' + CSS.escape(clList[0]);
              hasUniqueClassInParent = true;
            }
          } catch(e) {}
        }
      }

      if (!hasUniqueClassInParent) {
        var siblings = Array.from(parent.children).filter(function(ch) { return ch.tagName === curr.tagName; });
        if (siblings.length > 1) {
          var idx = siblings.indexOf(curr) + 1;
          stepSel = tag + ':nth-of-type(' + idx + ')';
        }
      }

      path.unshift(stepSel);
      curr = parent;
    }

    var localPath = path.join(' > ');
    var fullSelector = (ancestorSel ? (ancestorSel + ' > ' + localPath) : localPath);
    return fullSelector || localPath;
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

    // 비고/내용 필드인 경우에만 본문 컨테이너로 승격 (부서, 기안자, 날짜, 제목 등은 승격하지 않음!)
    var isBodyField = (activeTargetField === 'desc' || activeTargetField === 'all');
    var contentAncestor = (isBodyField && target.closest) ? target.closest(
      'td.board-contents, .board-contents, td.han, .view_content, .article_content, .post_content, .doc_content, [class*="board-contents"], [class*="view_content"], [class*="post_content"], [class*="article_body"], #articleBody, #board_content, #view_content'
    ) : null;
    var effectiveTarget = (contentAncestor && contentAncestor.contains(target)) ? contentAncestor : target;

    var pickedText = '';
    if (effectiveTarget) {
      if (effectiveTarget.value !== undefined && (effectiveTarget.tagName === 'INPUT' || effectiveTarget.tagName === 'TEXTAREA')) {
        pickedText = (effectiveTarget.value || '').trim();
      }
      if (!pickedText && effectiveTarget.querySelector) {
        var inputChild = effectiveTarget.querySelector('input, textarea');
        if (inputChild && inputChild.value) {
          pickedText = (inputChild.value || '').trim();
        }
      }
      if (!pickedText) {
        pickedText = (effectiveTarget.innerText || effectiveTarget.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
      }
      if (!pickedText) {
        pickedText = effectiveTarget.getAttribute('title') || effectiveTarget.getAttribute('alt') || effectiveTarget.getAttribute('placeholder') || '';
      }
    }

    if (isBodyField && contentAncestor && contentAncestor !== effectiveTarget) {
      var fullContentText = (contentAncestor.innerText || contentAncestor.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
      if (fullContentText && fullContentText.length > pickedText.length) {
        pickedText = fullContentText;
        effectiveTarget = contentAncestor;
      }
    }

    var pickedSelector = buildElementSelector(effectiveTarget);

    // 블록 드래그 텍스트 해제 (내용 수집 원천 차단)
    try {
      if (window.getSelection) {
        window.getSelection().removeAllRanges();
      }
    } catch(err) {}

    var completedField = activeTargetField;
    stopElementPicker();

    // 백그라운드에 선택 완료 알림 (웹페이지 변조 없음)
    try {
      chrome.runtime.sendMessage({
        action: "elementPickerCompleted",
        targetField: completedField,
        pickedText: pickedText,
        pickedSelector: pickedSelector,
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
      startElementPicker(msg.targetField);
      sendResponse({ success: true });
      return true;
    } else if (msg.action === "stopElementPicker") {
      stopElementPicker();
      sendResponse({ success: true });
      return true;
    }
  });
})();
