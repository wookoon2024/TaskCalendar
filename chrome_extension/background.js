function setupContextMenu() {
  try {
    chrome.contextMenus.removeAll(() => {
      chrome.contextMenus.create({
        id: "taskcalendar-add",
        title: "TaskCalendar에 등록",
        contexts: ["page", "selection", "link", "image"]
      });
    });
  } catch(e) {}
}

// 초기화 및 이벤트 등록
setupContextMenu();
chrome.runtime.onInstalled.addListener(setupContextMenu);
chrome.runtime.onStartup.addListener(setupContextMenu);

// 스마트 추출 엔진 v2.1 (모든 유형의 게시판, 커뮤니티, 전자결재/온나라, 지메일 등 웹앱 자동 대응 + 사이트별 맞춤 규칙)
function extractRowData(targetLinkUrl, targetSelection, customRule) {
  var result = {
    selectedText: targetSelection || (window.getSelection() ? window.getSelection().toString() : '') || '',
    linkText: '',
    linkUrl: targetLinkUrl || '',
    metaText: '',
    author: '',
    category: '',
    status: '등록',
    postNo: '',
    detectedDate: '',
    views: '',
    votes: '',
    frameUrl: window.location.href || '',
    isIframe: (window !== window.top),
    lastActivity: Math.max(document.__tcLastContextMenuTime || 0, document.__tcLastMouseMoveTime || 0),
    hasTarget: !!(document.__tcTarget || document.__tcHoverTarget || document.__tcContainer)
  };

  // 대상 요소 스마트 해결 (명시적 타겟 > 드래그 텍스트 노드 > 체크된 행 > 마우스 호버 대상 > :hover 행)
  var target = document.__tcTarget;

  if (!target) {
    // 1. 화면에 드래그(블록 지정)된 텍스트가 있다면 해당 텍스트 노드의 부모
    try {
      var sel = window.getSelection ? window.getSelection() : null;
      if (sel && sel.rangeCount > 0 && !sel.isCollapsed) {
        var selNode = sel.anchorNode;
        var sEl = (selNode && selNode.nodeType === 3) ? selNode.parentElement : selNode;
        if (sEl && document.contains(sEl)) {
          target = sEl;
        }
      }
    } catch(e) {}
  }

  if (!target) {
    // 2. 체크박스가 체크되어 있거나 선택된(active/selected) 행 (지메일 tr.zA, 게시판 행)
    try {
      var checkedRow = document.querySelector('tr[aria-selected="true"], tr.zA:has([role="checkbox"][aria-checked="true"]), tr:has(input[type="checkbox"]:checked), tr.selected, tr.active, li.selected, li.active');
      if (checkedRow) {
        target = checkedRow;
      }
    } catch(e) {}
  }

  if (!target) {
    // 3. 마우스 커서가 최근에 머물렀던(Hover) 대상
    if (document.__tcHoverTarget && document.contains(document.__tcHoverTarget)) {
      target = document.__tcHoverTarget;
    }
  }

  if (!target) {
    // 4. 현재 :hover 상태인 행
    try {
      var hRow = document.querySelector('tr.zA:hover, tr:hover, li:hover, article:hover');
      if (hRow) {
        target = hRow;
      }
    } catch(e) {}
  }

  var linkEl = document.__tcLink;

  // 1. targetLinkUrl(우클릭한 링크 URL)이 넘어왔다면 정확한 <a> 태그 매칭
  if (targetLinkUrl) {
    try {
      var allA = document.querySelectorAll('a[href]');
      for (var i = 0; i < allA.length; i++) {
        var aTag = allA[i];
        var href = aTag.href || aTag.getAttribute('href') || '';
        if (href === targetLinkUrl) {
          linkEl = aTag;
          break;
        }
      }
      if (!linkEl) {
        for (var j = 0; j < allA.length; j++) {
          var h = allA[j].href || allA[j].getAttribute('href') || '';
          if (h && (h.includes(targetLinkUrl) || targetLinkUrl.includes(h))) {
            linkEl = allA[j];
            break;
          }
        }
      }
    } catch (e) {}
  }

  // 2. 우클릭 대상(target)에서 <a> 탐색
  if (!linkEl && target) {
    linkEl = target.closest ? target.closest('a') : null;
  }

  // 3. 링크 요소가 확인되었을 때 텍스트와 URL 확정
  if (linkEl) {
    result.linkUrl = targetLinkUrl || linkEl.href || '';
    var rawText = (linkEl.innerText || linkEl.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
    rawText = rawText.replace(/^(\[\s*\]|□|■|○|●|▶|▷|선택|새창)\s*/, '').trim();
    if (!rawText) {
      rawText = linkEl.getAttribute('title') || (linkEl.querySelector('img') ? linkEl.querySelector('img').alt : '');
    }
    result.linkText = rawText;
  }

  // 4. 컨테이너 탐색 (tr, li, article, div.bx 등)
  var startEl = linkEl || target;
  var trContainer = startEl && startEl.closest ? startEl.closest('tr, [role="row"]') : null;
  var container = document.__tcContainer || document.__tcHoverContainer || trContainer || (startEl && startEl.closest ? startEl.closest('li, article, div.bx, div.total_wrap, div.news_wrap, div.view_wrap, div.list_item, div.ub-content, div.post, div.item, div.board_list') : null);

  if (!container && startEl) {
    var curr = startEl.parentElement;
    for (var step = 0; step < 6 && curr && curr !== document.body; step++) {
      var fullText = (curr.innerText || '').replace(/[\s\n\r\t]+/g, ' ').trim();
      if (fullText.length >= 10 && fullText.length <= 500) {
        container = curr;
        break;
      }
      curr = curr.parentElement;
    }
  }

  // [특화] 지메일(Gmail) 및 웹메일 전용 고속 스마트 필드 감지
  var isGmail = (window.location.hostname || '').includes('mail.google.com') || (container && (container.classList.contains('zA') || container.querySelector('span.bog, span[email]')));
  if (isGmail && (container || trContainer)) {
    var gContainer = container || trContainer;
    // 1. 보낸사람 (작성자)
    if (!result.author) {
      var gmSender = gContainer.querySelector('span[email], span.bA4 span, span[name], .yX span');
      if (gmSender) {
        var sText = gmSender.getAttribute('name') || gmSender.getAttribute('email') || gmSender.innerText || gmSender.textContent || '';
        var cAuth = cleanAuthor(sText);
        if (cAuth) result.author = cAuth;
        else if (sText.trim()) result.author = sText.trim();
      }
    }
    // 2. 제목
    if (!result.linkText) {
      var gmSubj = gContainer.querySelector('span.bog, .y6 span, [data-thread-id]');
      if (gmSubj) {
        result.linkText = (gmSubj.innerText || gmSubj.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
      }
    }
    // 3. 날짜
    if (!result.detectedDate) {
      var gmDate = gContainer.querySelector('td.xW span[title], td.xW span, span.bi4');
      if (gmDate) {
        var dStr = gmDate.getAttribute('title') || gmDate.innerText || '';
        result.detectedDate = normalizeDate(dStr);
      }
    }
    // 4. 내용 (메일 본문 요약 스니펫)
    if (!result.descOverride && !result.selectedText) {
      var gmSnippet = gContainer.querySelector('span.y2');
      if (gmSnippet) {
        var snipText = (gmSnippet.innerText || gmSnippet.textContent || '').replace(/^[\s\-–—:]+/, '').trim();
        if (snipText) {
          result.descOverride = snipText;
          result.selectedText = snipText;
        }
      }
    }
    // 5. 스레드 직접 링크 (data-legacy-thread-id 또는 data-thread-id)
    var threadId = gContainer.getAttribute('data-legacy-thread-id') || gContainer.getAttribute('data-thread-id');
    if (threadId && !targetLinkUrl) {
      result.linkUrl = window.location.origin + window.location.pathname + '#inbox/' + threadId;
    }
  }

  // [헬퍼] 날짜 정규화 함수
  function normalizeDate(raw) {
    if (!raw) return '';
    raw = raw.replace(/[\s\n\r\t]+/g, ' ').trim();
    // 1) 4자리 연도: YYYY-MM-DD, YYYY.MM.DD, YYYY/MM/DD
    var m4 = raw.match(/\b(20\d{2}|19\d{2})[-./](\d{1,2})[-./](\d{1,2})\b/);
    if (m4) {
      return m4[1] + '-' + String(m4[2]).padStart(2, '0') + '-' + String(m4[3]).padStart(2, '0');
    }
    // 2) 2자리 연도: YY/MM/DD, YY-MM-DD, YY.MM.DD (e.g. 13/10/24, 20/12/02, 26-09-13)
    var m2 = raw.match(/\b(\d{2})[-./](\d{1,2})[-./](\d{1,2})\b/);
    if (m2) {
      var yr = parseInt(m2[1], 10);
      var fullYr = yr <= 60 ? (2000 + yr) : (1900 + yr);
      return fullYr + '-' + String(m2[2]).padStart(2, '0') + '-' + String(m2[3]).padStart(2, '0');
    }
    // 3) 월/일: MM-DD, MM/DD, MM.DD (e.g. 09-13, 09/13)
    var mMd = raw.match(/\b(\d{1,2})[-./](\d{1,2})\b/);
    if (mMd && parseInt(mMd[1], 10) >= 1 && parseInt(mMd[1], 10) <= 12 && parseInt(mMd[2], 10) >= 1 && parseInt(mMd[2], 10) <= 31) {
      var curY = new Date().getFullYear();
      return curY + '-' + String(mMd[1]).padStart(2, '0') + '-' + String(mMd[2]).padStart(2, '0');
    }
    // 4) 시간만 표기된 경우 (당일 게시물!): HH:MM:SS, HH:MM, N분 전, N시간 전, 오늘, 방금
    if (/\b\d{1,2}:\d{2}(:\d{2})?\b|\b\d+\s*분\s*전\b|\b\d+\s*시간\s*전\b|\b(오늘|방금)\b/.test(raw)) {
      var td = new Date();
      return td.getFullYear() + '-' + String(td.getMonth() + 1).padStart(2, '0') + '-' + String(td.getDate()).padStart(2, '0');
    }
    // 5) 어제
    if (/\b어제\b/.test(raw)) {
      var yd = new Date();
      yd.setDate(yd.getDate() - 1);
      return yd.getFullYear() + '-' + String(yd.getMonth() + 1).padStart(2, '0') + '-' + String(yd.getDate()).padStart(2, '0');
    }
    return '';
  }

  // [헬퍼] 작성자 검증 및 정제 함수
  function cleanAuthor(str) {
    if (!str) return '';
    str = str.replace(/[\s\n\r\t]+/g, ' ').trim();
    str = str.replace(/^(글쓴이|작성자|기안자|담당자|작성인|등록자|닉네임|by)[:\s]*/i, '').trim();
    str = str.replace(/^(?:\[?\d{1,3}\]?|LV\.?\s*\d{1,3})\s+/i, '').trim();
    if (/^\d+$/.test(str.replace(/,/g, ''))) return '';
    if (/^\d{1,4}[-./]\d{1,2}[-./]\d{1,2}/.test(str)) return '';
    if (/^\d{1,2}:\d{2}/.test(str)) return '';
    if (/^(공지|알림|선택|새창|삭제|수정|답글|댓글|조회|추천|비추|다운로드|목록|전체|인기|Hit|No|IP|PC|모바일|추천수|조회수|글쓴이|작성자|기안자|상태|일반)$/i.test(str)) return '';
    if (/^\d+\s*(KB|MB|GB|B|건|개|원|명|페이지)$/i.test(str)) return '';
    if (str.length < 1 || str.length > 50) return '';
    return str;
  }

  // [헬퍼] 말머리/카테고리 추출
  function extractCategory(str) {
    if (!str) return '';
    var m = str.match(/^\[([^\]]{2,15})\]/);
    if (m) {
      var cat = m[1].trim();
      if (!/^\d+$/.test(cat) && !/^(공지|알림|전체|선택)$/.test(cat)) {
        return cat;
      }
    }
    return '';
  }

  // [전략 1] 테이블 행 (TR) - 컬럼 인덱스 및 헤더 기반 스마트 매칭
  if (trContainer) {
    var cells = Array.from(trContainer.children).filter(function(el) {
      return el.tagName === 'TD' || el.tagName === 'TH';
    });

    var table = trContainer.closest('table');
    var headerTexts = [];
    if (table) {
      var headerRow = (table.tHead && table.tHead.rows && table.tHead.rows[0]) ||
                       (table.rows && table.rows[0] && table.rows[0] !== trContainer ? table.rows[0] : null) ||
                       table.querySelector('thead tr') ||
                       table.querySelector('tr:has(th)') ||
                       table.querySelector('tr');
      if (headerRow && headerRow !== trContainer) {
        var ths = Array.from(headerRow.children).filter(function(el) {
          return el.tagName === 'TD' || el.tagName === 'TH';
        });
        for (var h = 0; h < ths.length; h++) {
          var hText = (ths[h].innerText || ths[h].textContent || '').replace(/[\s\n\r\t]+/g, '').trim();
          headerTexts.push(hText);
        }
      }
    }

    var authorCellIdx = -1;
    var dateCellIdx = -1;
    var titleCellIdx = -1;
    var postNoCellIdx = -1;
    var viewsCellIdx = -1;
    var votesCellIdx = -1;
    var categoryCellIdx = -1;

    for (var col = 0; col < headerTexts.length; col++) {
      var ht = headerTexts[col];
      if (/^(글쓴이|작성자|기안자|담당자|작성인|등록자|이름|작성|author|writer|nick|user)$/i.test(ht)) {
        authorCellIdx = col;
      } else if (/^(등록일|작성일|일자|날짜|기안일|일시|등록일시|date|time)$/i.test(ht)) {
        dateCellIdx = col;
      } else if (/^(제목|과제명|안건|문서제목|건명|게시물|subject|title)$/i.test(ht)) {
        titleCellIdx = col;
      } else if (/^(번호|순번|문서번호|접수번호|글번호|no|num|idx|id)$/i.test(ht)) {
        postNoCellIdx = col;
      } else if (/^(조회|조회수|hit|hits|view|views|count|읽음)$/i.test(ht)) {
        viewsCellIdx = col;
      } else if (/^(추천|추천수|좋아요|공감|vote|good|like|비추|비추천)$/i.test(ht)) {
        votesCellIdx = col;
      } else if (/^(분류|카테고리|구분|유형|상태|결재상태|진행상태|처리상태|문서구분|업무구분|category|type|status)$/i.test(ht)) {
        categoryCellIdx = col;
      }
    }

    if (authorCellIdx >= 0 && authorCellIdx < cells.length) {
      result.author = cleanAuthor(cells[authorCellIdx].innerText || cells[authorCellIdx].textContent || '');
    }
    if (dateCellIdx >= 0 && dateCellIdx < cells.length) {
      result.detectedDate = normalizeDate(cells[dateCellIdx].innerText || cells[dateCellIdx].textContent || '');
    }
    if (postNoCellIdx >= 0 && postNoCellIdx < cells.length) {
      var pno = (cells[postNoCellIdx].innerText || '').replace(/[\s\n\r\t]+/g, '').trim();
      if (pno && pno.length <= 40) result.postNo = pno;
    }
    if (viewsCellIdx >= 0 && viewsCellIdx < cells.length) {
      result.views = (cells[viewsCellIdx].innerText || '').replace(/[\s\n\r\t]+/g, '').trim();
    }
    if (votesCellIdx >= 0 && votesCellIdx < cells.length) {
      result.votes = (cells[votesCellIdx].innerText || '').replace(/[\s\n\r\t]+/g, '').trim();
    }
    if (categoryCellIdx >= 0 && categoryCellIdx < cells.length) {
      var catRaw = (cells[categoryCellIdx].innerText || '').replace(/[\s\n\r\t]+/g, ' ').trim();
      if (catRaw && !/^(전체|all)$/i.test(catRaw)) result.category = catRaw;
    }
    if (!result.linkText && titleCellIdx >= 0 && titleCellIdx < cells.length) {
      var titleA = cells[titleCellIdx].querySelector('a');
      if (titleA) {
        result.linkText = (titleA.innerText || titleA.textContent || '').trim();
        if (!result.linkUrl) result.linkUrl = titleA.href || '';
      } else {
        result.linkText = (cells[titleCellIdx].innerText || '').trim();
      }
    }

    if (!result.author || !result.detectedDate) {
      for (var cIdx = 0; cIdx < cells.length; cIdx++) {
        var cell = cells[cIdx];
        var cellText = (cell.innerText || cell.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();

        if ((linkEl && cell.contains(linkEl)) || (result.linkText && cellText.includes(result.linkText))) {
          continue;
        }

        if (!result.detectedDate) {
          var dt = normalizeDate(cellText);
          if (dt) {
            result.detectedDate = dt;
            continue;
          }
        }

        if (!result.author) {
          var authorTarget = cell.querySelector('[class*="author"], [class*="writer"], [class*="nick"], [class*="member"], [class*="user"], [class*="name"], a[href*="member"], a[href*="user"], a[href*="bbs"], a[onclick*="member"], a[onclick*="user"]') || cell;
          var cand = cleanAuthor(authorTarget.innerText || authorTarget.textContent || '');
          if (cand) {
            result.author = cand;
            continue;
          }
        }

        if (!result.postNo && /^\d{4,}$/.test(cellText)) {
          result.postNo = cellText;
          continue;
        }

        if (!result.views && /^\d{1,7}$/.test(cellText)) {
          result.views = cellText;
          continue;
        }
      }
    }
  }

  // [전략 2] 리스트/카드형 컨테이너 (LI, DIV.bx, DIV.article 등)
  if (!trContainer && container) {
    if (!result.author) {
      var aEl = container.querySelector('[class*="author"], [class*="writer"], [class*="nick"], [class*="member"], [class*="user"], [class*="name"]');
      if (aEl) result.author = cleanAuthor(aEl.innerText || aEl.textContent || '');
    }
    if (!result.detectedDate) {
      var dEl = container.querySelector('time, [class*="date"], [class*="time"], span.info');
      if (dEl) result.detectedDate = normalizeDate(dEl.innerText || dEl.textContent || '');
    }
    if (!result.category) {
      var cEl = container.querySelector('[class*="category"], [class*="cate"], [class*="badge"], [class*="tag"]');
      if (cEl) {
        var cText = (cEl.innerText || '').replace(/[\s\[\]]/g, '').trim();
        if (cText && cText.length <= 15) result.category = cText;
      }
    }

    if (!result.author || !result.detectedDate) {
      var cText = container.innerText || '';
      if (result.linkText) cText = cText.replace(result.linkText, ' ');
      var remaining = cText.replace(/[\s\n\r\t]+/g, ' ').trim();

      if (!result.detectedDate) {
        var dt2 = normalizeDate(remaining);
        if (dt2) result.detectedDate = dt2;
      }

      var tokens = remaining.split(/\s+/).filter(function(t) { return t.length > 0; });
      for (var ti = 0; ti < tokens.length; ti++) {
        var tok = tokens[ti];
        if (/^\d+$/.test(tok)) {
          if (!result.views && parseInt(tok, 10) < 10000000) result.views = tok;
          continue;
        }
        if (!result.author) {
          var cand2 = cleanAuthor(tok);
          if (cand2) {
            result.author = cand2;
            continue;
          }
        }
      }
    }
  }

  // [전략 3] 상세 본문 페이지 및 전자결재/온나라 본문 탐색
  if (!result.author || !result.detectedDate || !result.linkText) {
    try {
      if (!result.linkText) {
        var titleElem = document.querySelector('h1.title, h2.title, .view_title, .art_title, .board_view_title, .subject, h3.title, .top_title, #docTitle, #subject, #txtTitle, .doc_title, .docTitle, .viewTitle, .view-title, td.subject, span.subject, div.view_subject, p.subject');
        if (titleElem) {
          result.linkText = (titleElem.innerText || '').replace(/[\s\n\r\t]+/g, ' ').trim();
        }
      }
      if (!result.author) {
        var authorElem = document.querySelector('.writer, .author, .nick, .user_name, .info_author, [class*="writer"], [class*="author"], #drafter, #writer, #docWriter, .drafter, td.drafter, span.drafter');
        if (authorElem) {
          result.author = cleanAuthor(authorElem.innerText || authorElem.textContent || '');
        }
      }
      if (!result.detectedDate) {
        var dateElem = document.querySelector('.date, .time, time, .regdate, .created_at, [class*="date"], [class*="time"], #draftDate, #regDate, #docDate, .draft_date, .doc_date');
        if (dateElem) {
          result.detectedDate = normalizeDate(dateElem.innerText || dateElem.textContent || '');
        }
      }
      // iframe 단독 문서 뷰어의 경우 document.title 활용
      if (!result.linkText && window !== window.top && document.title && document.title.trim()) {
        var dt = document.title.trim();
        if (!dt.includes('about:') && !dt.includes('javascript:') && !dt.startsWith('http://') && !dt.startsWith('https://')) {
          result.linkText = dt;
        }
      }
    } catch(e) {}
  }

  // 5. 제목 최적 링크 fallback
  if (!result.linkText && container) {
    var allLinks = container.querySelectorAll('a');
    var bestLink = null;
    var bestLen = 0;
    for (var k = 0; k < allLinks.length; k++) {
      var lEl = allLinks[k];
      var lText = (lEl.innerText || '').replace(/[\s\n\r\t]+/g, ' ').trim();
      if (/^(글쓴이|작성자|댓글|조회|추천|\d+)$/.test(lText)) continue;
      if (lText.length >= 4 && lText.length <= 150 && lText.length > bestLen) {
        bestLen = lText.length;
        bestLink = lEl;
      }
    }
    if (bestLink) {
      result.linkText = (bestLink.innerText || '').replace(/[\s\n\r\t]+/g, ' ').trim();
      result.linkUrl = bestLink.href || '';
    } else if (startEl) {
      result.linkText = (startEl.innerText || '').replace(/[\s\n\r\t]+/g, ' ').trim().substring(0, 100);
    }
  }

  // 6. 제목에서 말머리/카테고리 자동 추출
  if (!result.category && result.linkText) {
    var catFromTitle = extractCategory(result.linkText);
    if (catFromTitle) {
      result.category = catFromTitle;
    }
  }

  // 7. 페이지 상단 게시판 명칭 감지
  if (!result.category) {
    try {
      var bTitleEl = document.querySelector('.board_title, .page_title, h1.title, #board_title, .sub_title, div[class*="board_name"]');
      if (bTitleEl) {
        var bName = (bTitleEl.innerText || '').replace(/[\s\n\r\t]+/g, ' ').trim();
        bName = bName.replace(/\s*(입니다|게시판\s*입니다).*$/, '게시판').trim();
        bName = bName.replace(/^[▶▷>\s]+/, '').trim();
        if (bName && bName.length <= 15) {
          result.category = bName;
        }
      }
    } catch(e) {}
  }

  // [헬퍼] 사이트 맞춤 규칙 요소 스마트 매칭 (행 내부 상대 선택자 및 하위 경로 유연 탐색)
  function queryCustomElement(rootNode, selector) {
    if (!rootNode || !selector || typeof selector !== 'string') return null;
    selector = selector.trim();
    if (!selector || selector === '__none__') return null;

    try {
      var found = rootNode.querySelector(selector);
      if (found) return found;
    } catch(e) {}

    // 선택자에 li, a, tr 등의 조상 태그 경로가 포함되어 있다면 행 내부 서브 선택자 시도
    var parts = selector.split(/\s*>\s*/);
    for (var i = 0; i < parts.length - 1; i++) {
      var subSel = parts.slice(i + 1).join(' > ');
      try {
        var foundSub = rootNode.querySelector(subSel);
        if (foundSub) return foundSub;
      } catch(e) {}
    }

    var lastPart = parts[parts.length - 1];
    if (lastPart && lastPart !== selector) {
      try {
        var foundLast = rootNode.querySelector(lastPart);
        if (foundLast) return foundLast;
      } catch(e) {}
    }

    if (rootNode !== document) {
      try {
        var docFound = document.querySelector(selector);
        if (docFound) return docFound;
      } catch(e) {}
    }

    return null;
  }

  // [전략 4] 사이트별 맞춤 규칙(Custom Rule: 선택자 & 사용자정의 서식) 적용
  var ruleSelectors = (customRule && customRule.selectors) ? customRule.selectors : (customRule || {});
  var ruleTemplates = (customRule && customRule.templates) ? customRule.templates : {};

  if (ruleSelectors && typeof ruleSelectors === 'object') {
    var rootEl = container || document;

    // 제목 맞춤 규칙
    if (ruleSelectors.title === '__none__') {
      result.linkText = '';
    } else if (ruleSelectors.title) {
      var tEl = queryCustomElement(rootEl, ruleSelectors.title);
      if (tEl) {
        var tText = (tEl.innerText || tEl.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
        if (tText) result.linkText = tText;
      }
    }

    // 분류 맞춤 규칙
    if (ruleSelectors.category === '__none__') {
      result.category = '';
    } else if (ruleSelectors.category) {
      var cEl = queryCustomElement(rootEl, ruleSelectors.category);
      if (cEl) {
        var cText = (cEl.innerText || cEl.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
        if (cText) result.category = cText;
      }
    }

    // 기안자/작성자 맞춤 규칙
    if (ruleSelectors.author === '__none__') {
      result.author = '';
    } else if (ruleSelectors.author) {
      var aEl = queryCustomElement(rootEl, ruleSelectors.author);
      if (aEl) {
        var aText = cleanAuthor(aEl.innerText || aEl.textContent || '');
        if (aText) result.author = aText;
      }
    }

    // 상태 맞춤 규칙
    if (ruleSelectors.status === '__none__') {
      result.status = '';
    } else if (ruleSelectors.status) {
      var sEl = queryCustomElement(rootEl, ruleSelectors.status);
      if (sEl) {
        var sText = (sEl.innerText || sEl.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
        if (sText) result.status = sText;
      }
    }

    // 날짜 맞춤 규칙
    if (ruleSelectors.date === '__none__') {
      result.detectedDate = '';
    } else if (ruleSelectors.date) {
      var dEl = queryCustomElement(rootEl, ruleSelectors.date);
      if (dEl) {
        var dText = normalizeDate(dEl.innerText || dEl.textContent || '');
        if (dText) result.detectedDate = dText;
      }
    }

    // 내용 맞춤 규칙
    if (ruleSelectors.desc === '__none__') {
      result.selectedText = '';
      result.descOverride = '';
    } else if (ruleSelectors.desc) {
      var deEl = queryCustomElement(rootEl, ruleSelectors.desc);
      if (deEl) {
        var deText = (deEl.innerText || deEl.textContent || '').trim();
        if (deText) {
          result.selectedText = deText;
          result.descOverride = deText;
        }
      }
    }

    // 출처 URL 맞춤 규칙
    if (ruleSelectors.url === '__none__') {
      result.linkUrl = '';
    } else if (ruleSelectors.url) {
      var uEl = queryCustomElement(rootEl, ruleSelectors.url);
      if (uEl) {
        var uHref = uEl.href || uEl.getAttribute('href') || '';
        if (uHref) result.linkUrl = uHref;
      }
    }
  }

  // [전략 5] 사용자 정의 서식(Template: {제목}, {분류} 등 치환 태그) 적용
  if (ruleTemplates && typeof ruleTemplates === 'object') {
    var baseValues = {
      '제목': result.linkText || '',
      'title': result.linkText || '',
      '분류': result.category || '',
      'category': result.category || '',
      '기안자': result.author || '',
      '작성자': result.author || '',
      'author': result.author || '',
      '상태': result.status || '',
      'status': result.status || '',
      '날짜': result.detectedDate || '',
      'date': result.detectedDate || '',
      '내용': result.descOverride || result.selectedText || '',
      'desc': result.descOverride || result.selectedText || '',
      '출처': result.linkUrl || '',
      'url': result.linkUrl || ''
    };

    function resolveTemplate(tpl) {
      if (!tpl || typeof tpl !== 'string') return '';
      return tpl.replace(/\{([^{}]+)\}/g, function(match, key) {
        var k = key.trim().toLowerCase();
        if (k === '제목' || k === 'title') return baseValues.title;
        if (k === '분류' || k === 'category') return baseValues.category;
        if (k === '기안자' || k === '작성자' || k === 'author') return baseValues.author;
        if (k === '상태' || k === 'status') return baseValues.status;
        if (k === '날짜' || k === 'date') return baseValues.date;
        if (k === '내용' || k === '본문' || k === 'desc') return baseValues.desc;
        if (k === '출처' || k === '링크' || k === 'url') return baseValues.url;
        return match;
      });
    }

    if (ruleTemplates.title && ruleTemplates.title.trim()) {
      result.linkText = resolveTemplate(ruleTemplates.title.trim());
    }
    if (ruleTemplates.category && ruleTemplates.category.trim()) {
      result.category = resolveTemplate(ruleTemplates.category.trim());
    }
    if (ruleTemplates.author && ruleTemplates.author.trim()) {
      result.author = resolveTemplate(ruleTemplates.author.trim());
    }
    if (ruleTemplates.status && ruleTemplates.status.trim()) {
      result.status = resolveTemplate(ruleTemplates.status.trim());
    }
    if (ruleTemplates.date && ruleTemplates.date.trim()) {
      result.detectedDate = resolveTemplate(ruleTemplates.date.trim());
    }
    if (ruleTemplates.desc && ruleTemplates.desc.trim()) {
      result.descOverride = resolveTemplate(ruleTemplates.desc.trim());
      result.selectedText = result.descOverride;
    }
    if (ruleTemplates.url && ruleTemplates.url.trim()) {
      result.linkUrl = resolveTemplate(ruleTemplates.url.trim());
    }
  }

  // 8. 구조화된 메타 텍스트 조립
  var metaParts = [];
  if (result.postNo) metaParts.push('게시글 번호: ' + result.postNo);
  if (result.author) metaParts.push('작성자: ' + result.author);
  if (result.detectedDate) metaParts.push('등록일: ' + result.detectedDate);
  if (result.views) metaParts.push('조회: ' + result.views);
  if (result.votes) metaParts.push('추천: ' + result.votes);
  if (result.category) metaParts.push('분류: ' + result.category);
  if (result.status && result.status !== '등록') metaParts.push('상태: ' + result.status);

  result.metaText = metaParts.join('\n');

  // 9. 보안 및 전자결재 정보보호를 위한 길이 제한 (모든 필드 최대 50자 이내)
  function limit50(str) {
    if (!str || typeof str !== 'string') return '';
    return str.replace(/[\s\n\r\t]+/g, ' ').trim().substring(0, 50);
  }

  result.linkText = limit50(result.linkText);
  result.author = limit50(result.author);
  result.category = limit50(result.category);
  result.status = limit50(result.status || '등록');
  result.postNo = limit50(result.postNo);
  result.detectedDate = limit50(result.detectedDate);
  result.views = limit50(result.views);
  result.votes = limit50(result.votes);
  result.selectedText = limit50(result.selectedText);
  if (result.descOverride) result.descOverride = limit50(result.descOverride);

  return result;
}

// 탭 내에서 텍스트와 일치하는 DOM 요소를 찾아 최적 CSS 선택자 추출 및 시각적 강조
function findMatchingElementOnPage(sampleText, isUrl) {
  if (!sampleText || typeof sampleText !== 'string' || !sampleText.trim()) {
    return { success: false, error: '검색할 텍스트를 입력해주세요.' };
  }
  var query = sampleText.trim();
  var queryNorm = query.replace(/[\s\n\r\t]+/g, ' ').toLowerCase();

  var container = document.__tcContainer;
  var root = (container && container.contains && container.contains(document.__tcTarget || document.body)) ? container : null;
  if (root === document.body || root === document.documentElement) root = null;

  var candidates = [];

  function collectFromRoot(searchRoot, isLocal) {
    var all = Array.from(searchRoot.querySelectorAll('*'));
    if (isLocal) all.unshift(searchRoot);

    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'IFRAME', 'SVG', 'PATH'].includes(el.tagName)) continue;

      var text = (el.innerText || el.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
      var href = (el.tagName === 'A' ? (el.href || el.getAttribute('href') || '') : '');
      var title = (el.getAttribute('title') || '');
      var alt = (el.getAttribute('alt') || '');

      var tNorm = text.toLowerCase();
      var hNorm = href.toLowerCase();
      var titNorm = title.toLowerCase();
      var altNorm = alt.toLowerCase();

      var matchFound = false;
      var score = 0;
      var lenDiff = 999999;

      if (isUrl) {
        if (hNorm === queryNorm) {
          matchFound = true;
          score = 100;
          lenDiff = 0;
        } else if (hNorm.includes(queryNorm)) {
          matchFound = true;
          score = 80;
          lenDiff = href.length - query.length;
        } else if (tNorm === queryNorm && el.tagName === 'A') {
          matchFound = true;
          score = 90;
          lenDiff = 0;
        } else if (tNorm.includes(queryNorm) && el.tagName === 'A') {
          matchFound = true;
          score = 70;
          lenDiff = text.length - query.length;
        }
      } else {
        if (tNorm === queryNorm) {
          matchFound = true;
          score = 100;
          lenDiff = 0;
        } else if (titNorm === queryNorm || altNorm === queryNorm) {
          matchFound = true;
          score = 95;
          lenDiff = 0;
        } else if (tNorm.includes(queryNorm)) {
          matchFound = true;
          score = 80;
          lenDiff = text.length - query.length;
        } else if (queryNorm.includes(tNorm) && tNorm.length >= 2) {
          matchFound = true;
          score = 50;
          lenDiff = query.length - text.length;
        }
      }

      if (matchFound) {
        candidates.push({
          el: el,
          score: score + (isLocal ? 20 : 0),
          lenDiff: Math.abs(lenDiff),
          isLocal: isLocal
        });
      }
    }
  }

  if (root) {
    collectFromRoot(root, true);
  }
  if (candidates.length === 0) {
    collectFromRoot(document.body, false);
  }

  if (candidates.length === 0) {
    return { success: false, error: '페이지에서 일치하는 요소를 찾을 수 없습니다.' };
  }

  // 잎(leaf) 노드 우선 정렬
  candidates.sort(function(a, b) {
    if (a.score !== b.score) return b.score - a.score;
    if (a.lenDiff !== b.lenDiff) return a.lenDiff - b.lenDiff;
    return (a.el.childElementCount || 0) - (b.el.childElementCount || 0);
  });

  var best = candidates[0].el;
  if (isUrl && best.tagName !== 'A') {
    var aChild = best.querySelector('a') || (best.closest ? best.closest('a') : null);
    if (aChild) best = aChild;
  }

  // 웹페이지 내 보라색 하이라이트 효과 부여
  try {
    best.scrollIntoView({ behavior: 'smooth', block: 'center' });
    var prevOutline = best.style.outline;
    var prevShadow = best.style.boxShadow;
    best.style.outline = '3px solid #6C5CE7';
    best.style.boxShadow = '0 0 12px rgba(108, 92, 231, 0.8)';
    setTimeout(function() {
      best.style.outline = prevOutline;
      best.style.boxShadow = prevShadow;
    }, 2800);
  } catch(e) {}

  var scopeRoot = (root && root.contains(best)) ? root : document.body;

  function buildSelector(el, boundary) {
    if (!el || el === boundary) return '';

    if (el.id && !/^\d+$/.test(el.id)) {
      try {
        var idSel = '#' + CSS.escape(el.id);
        if (document.querySelectorAll(idSel).length === 1) return idSel;
      } catch(e) {}
    }

    if (el.className && typeof el.className === 'string') {
      var classes = el.className.trim().split(/\s+/).filter(function(c) {
        return c && !c.startsWith('tc-') && !c.includes(':') && !c.includes('/') && !/^\d+$/.test(c);
      });
      for (var k = 0; k < classes.length; k++) {
        var cls = '.' + CSS.escape(classes[k]);
        try {
          if (boundary.querySelectorAll(cls).length === 1) return cls;
        } catch(e) {}
      }
      if (classes.length > 0) {
        var tagCls = el.tagName.toLowerCase() + '.' + classes.map(function(c) { return CSS.escape(c); }).join('.');
        try {
          if (boundary.querySelectorAll(tagCls).length === 1) return tagCls;
        } catch(e) {}
      }
    }

    var path = [];
    var curr = el;
    while (curr && curr !== boundary && curr !== document.body && curr !== document.documentElement) {
      var tag = curr.tagName.toLowerCase();
      var parent = curr.parentElement;
      if (!parent) break;
      var siblings = Array.from(parent.children).filter(function(ch) { return ch.tagName === curr.tagName; });
      if (siblings.length > 1) {
        var idx = siblings.indexOf(curr) + 1;
        tag += ':nth-of-type(' + idx + ')';
      }
      path.unshift(tag);
      curr = parent;
    }
    return path.join(' > ');
  }

  var selector = buildSelector(best, scopeRoot);
  var matchedVal = isUrl ? (best.href || best.getAttribute('href') || '') : (best.innerText || best.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();

  return {
    success: true,
    selector: selector,
    matchedValue: matchedVal,
    isRelative: (scopeRoot === root)
  };
}

var popupWindowId = null;

function openOrFocusPopup(data) {
  chrome.storage.local.set({ taskCalendarData: data }, () => {
    if (popupWindowId !== null) {
      chrome.windows.get(popupWindowId, (existingWin) => {
        if (!chrome.runtime.lastError && existingWin) {
          chrome.windows.update(popupWindowId, { focused: true, drawAttention: true });
          chrome.runtime.sendMessage({ action: "reloadTaskCalendarData" }).catch(() => {});
          return;
        }
        findOrCreatePopup();
      });
    } else {
      findOrCreatePopup();
    }
  });
}

function findOrCreatePopup() {
  var popupUrl = chrome.runtime.getURL("popup.html");
  chrome.tabs.query({ url: popupUrl }, (tabs) => {
    if (tabs && tabs.length > 0) {
      var existingTab = tabs[0];
      popupWindowId = existingTab.windowId;
      chrome.windows.update(popupWindowId, { focused: true, drawAttention: true });
      chrome.tabs.update(existingTab.id, { active: true });
      chrome.runtime.sendMessage({ action: "reloadTaskCalendarData" }).catch(() => {});
      return;
    }

    chrome.windows.create(
      {
        url: "popup.html",
        type: "popup",
        width: 470,
        height: 430,
        focused: true
      },
      (newWin) => {
        if (newWin) {
          popupWindowId = newWin.id;
        }
      }
    );
  });
}

chrome.windows.onRemoved.addListener((windowId) => {
  if (windowId === popupWindowId) {
    popupWindowId = null;
  }
});

function getDomainFromUrl(url) {
  try {
    if (!url) return '';
    var u = new URL(url);
    return u.hostname || '';
  } catch(e) {
    return '';
  }
}

// 🎯 프레임 목록 중 최적의 결과(선택 텍스트, 클릭 대상, 최근 마우스 활동 등) 선별
function selectBestResult(results, targetFrameId) {
  if (!results || results.length === 0) return null;
  if (results.length === 1) return results[0];

  // 1. targetFrameId가 명시적으로 지정된 경우 해당 프레임 우선 검색
  if (typeof targetFrameId === 'number' && targetFrameId > 0) {
    for (var i = 0; i < results.length; i++) {
      if (results[i].frameId === targetFrameId) {
        var r0 = results[i].result;
        if (r0 && (r0.linkText || r0.selectedText || r0.hasTarget || r0.author || r0.detectedDate)) {
          return results[i];
        }
      }
    }
  }

  // 2. 가중치 기반 최적 프레임 선별
  var bestItem = null;
  var bestScore = -1;

  for (var j = 0; j < results.length; j++) {
    var item = results[j];
    var r = item.result;
    if (!r) continue;

    var score = 0;

    // 타겟 프레임 ID 일치 시 최고 가산점
    if (typeof targetFrameId === 'number' && item.frameId === targetFrameId) {
      score += 1000;
    }

    // 마우스 호버나 우클릭 대상이 존재했던 프레임
    if (r.hasTarget) score += 500;

    // 드래그 선택 텍스트가 있는 프레임
    if (r.selectedText && r.selectedText.trim()) score += 300;

    // 제목/링크텍스트 추출 성공
    if (r.linkText && r.linkText.trim()) {
      score += 150;
      if (r.linkText.trim().length >= 4 && r.linkText.trim().length <= 150) score += 50;
    }

    // 작성자 또는 날짜 감지
    if (r.author && r.author.trim()) score += 40;
    if (r.detectedDate && r.detectedDate.trim()) score += 40;

    // 최근 마우스/컨텍스트메뉴 활동 (10초 이내)
    if (r.lastActivity && r.lastActivity > 0) {
      var age = Date.now() - r.lastActivity;
      if (age < 10000) {
        score += Math.max(0, 100 - Math.floor(age / 100));
      }
    }

    // iframe 내부인데 제목/선택텍스트가 있으면 빈 탑 프레임보다 우선
    if (r.isIframe && (r.linkText || r.selectedText)) {
      score += 30;
    }

    if (score > bestScore) {
      bestScore = score;
      bestItem = item;
    }
  }

  return bestItem || results[0];
}

// 팝업과의 통신 메시지 리스너 (DOM 검색 및 재추출)
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "matchElementInTab") {
    var tabId = request.tabId;
    if (!tabId) {
      sendResponse({ success: false, error: "활성 탭 ID를 찾을 수 없습니다." });
      return true;
    }
    var execTarget = { tabId: tabId };
    if (typeof request.frameId === 'number') {
      execTarget.frameIds = [request.frameId];
    } else {
      execTarget.allFrames = true;
    }
    chrome.scripting.executeScript({
      target: execTarget,
      func: findMatchingElementOnPage,
      args: [request.sampleText, !!request.isUrl]
    }, (results) => {
      if (chrome.runtime.lastError) {
        sendResponse({ success: false, error: chrome.runtime.lastError.message });
        return;
      }
      var best = null;
      if (results && results.length > 0) {
        for (var r = 0; r < results.length; r++) {
          if (results[r] && results[r].result && results[r].result.success) {
            best = results[r].result;
            break;
          }
        }
      }
      var res = best || (results && results[0] && results[0].result) || { success: false, error: '응답이 없습니다.' };
      sendResponse(res);
    });
    return true; // 비동기 응답
  }

  if (request.action === "reExtractData") {
    var tabId2 = request.tabId;
    if (!tabId2) {
      sendResponse({ success: false, error: "활성 탭 ID를 찾을 수 없습니다." });
      return true;
    }
    var rawC = request.customRule;
    var ruleToUse = {
      selectors: (rawC && rawC.selectors) ? rawC.selectors : (rawC || {}),
      templates: (rawC && rawC.templates) ? rawC.templates : (request.templates || {})
    };
    var execTarget2 = { tabId: tabId2 };
    if (typeof request.frameId === 'number') {
      execTarget2.frameIds = [request.frameId];
    } else {
      execTarget2.allFrames = true;
    }
    chrome.scripting.executeScript({
      target: execTarget2,
      func: extractRowData,
      args: [request.linkUrl || "", request.selection || "", ruleToUse]
    }, (results2) => {
      if (chrome.runtime.lastError) {
        sendResponse({ success: false, error: chrome.runtime.lastError.message });
        return;
      }
      var chosen = selectBestResult(results2, request.frameId);
      var res2 = (chosen && chosen.result) || {};
      sendResponse({ success: true, data: res2 });
    });
    return true;
  }

  if (request.action === "openProtocolUrl") {
    var urlToOpen = request.url;
    var targetTabId = request.tabId;
    if (targetTabId) {
      chrome.tabs.update(targetTabId, { url: urlToOpen }, () => {
        if (chrome.runtime.lastError) {
          chrome.tabs.create({ url: urlToOpen, active: false }, (t) => {
            setTimeout(() => {
              if (t && t.id) chrome.tabs.remove(t.id).catch(() => {});
            }, 1500);
          });
        }
      });
    } else {
      chrome.tabs.create({ url: urlToOpen, active: false }, (t) => {
        setTimeout(() => {
          if (t && t.id) chrome.tabs.remove(t.id).catch(() => {});
        }, 1500);
      });
    }
    sendResponse({ success: true });
    return true;
  }

  if (request.action === "elementPickerCompleted") {
    var pTab = sender.tab;
    if (pTab) {
      // 모든 프레임의 피커 하이라이트/배너 정리
      chrome.tabs.sendMessage(pTab.id, { action: "stopElementPicker" }).catch(() => {});
      triggerCapture(pTab, request.linkUrl || "", "", request.frameUrl || pTab.url || "", sender.frameId);
    }
    sendResponse({ success: true });
    return true;
  }
});

function handleCaptureResults(results, tab, linkUrl, selection, pageUrl, domain, hasCustomRule, rawRule, targetFrameId) {
  var chosenItem = selectBestResult(results, targetFrameId);
  var captured = (chosenItem && chosenItem.result) || {};
  var originFrameId = (chosenItem && typeof chosenItem.frameId === 'number') ? chosenItem.frameId : (targetFrameId || 0);

  console.log('[TaskCalendar BG] chosen frameId:', originFrameId, 'captured:', JSON.stringify(captured));

  function limit50(str) {
    if (!str || typeof str !== 'string') return '';
    return str.replace(/[\s\n\r\t]+/g, ' ').trim().substring(0, 50);
  }

  var finalLinkText = limit50(captured.linkText || "");
  var finalLinkUrl = captured.linkUrl || linkUrl || "";
  var finalSelectedText = limit50(captured.selectedText || selection || "");

  if (!finalLinkText && finalSelectedText) {
    finalLinkText = finalSelectedText;
  }

  var effectivePageUrl = captured.frameUrl || finalLinkUrl || pageUrl;
  var effectiveDomain = getDomainFromUrl(effectivePageUrl) || domain;

  const data = {
    selectedText: finalSelectedText,
    linkText: finalLinkText,
    linkUrl: finalLinkUrl,
    metaText: limit50(captured.metaText || ""),
    author: limit50(captured.author || ""),
    category: limit50(captured.category || ""),
    status: limit50(captured.status || "등록"),
    detectedDate: limit50(captured.detectedDate || ""),
    pageTitle: limit50(tab.title || ""),
    pageUrl: effectivePageUrl,
    siteDomain: effectiveDomain,
    hasCustomRule: hasCustomRule,
    customRule: rawRule || {},
    originTabId: tab.id,
    originFrameId: originFrameId
  };
  openOrFocusPopup(data);
}

function triggerCapture(tab, clickedLinkUrl, clickedSelection, fallbackUrl, targetFrameId) {
  if (!tab) return;
  var linkUrl = clickedLinkUrl || "";
  var selection = clickedSelection || "";
  var pageUrl = fallbackUrl || tab.url || "";
  var domain = getDomainFromUrl(pageUrl);

  if (!tab.url || tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://")) {
    const data = {
      selectedText: selection,
      linkText: "",
      linkUrl: linkUrl,
      metaText: "",
      author: "",
      category: "",
      status: "등록",
      detectedDate: "",
      pageTitle: tab.title || "",
      pageUrl: linkUrl || pageUrl,
      siteDomain: domain,
      hasCustomRule: false,
      originTabId: null,
      originFrameId: 0
    };
    openOrFocusPopup(data);
    return;
  }

  // 저장된 도메인별 맞춤 규칙 확인
  chrome.storage.local.get(['tc_site_rules'], (storageRes) => {
    var siteRules = (storageRes && storageRes.tc_site_rules) || {};
    var rawRule = (domain && siteRules[domain]) || null;
    var ruleToUse = {
      selectors: (rawRule && rawRule.selectors) ? rawRule.selectors : (rawRule || {}),
      templates: (rawRule && rawRule.templates) ? rawRule.templates : {}
    };
    var hasCustomRule = !!(rawRule && ((rawRule.selectors && Object.keys(rawRule.selectors).some(k => rawRule.selectors[k])) || (rawRule.templates && Object.keys(rawRule.templates).some(k => rawRule.templates[k])) || Object.keys(rawRule).some(k => rawRule[k])));

    // 모든 프레임(iframe 포함) 대상으로 스크립트 실행하여 최적 결과 수집
    chrome.scripting.executeScript(
      {
        target: { tabId: tab.id, allFrames: true },
        func: extractRowData,
        args: [linkUrl, selection, ruleToUse]
      },
      (results) => {
        if (chrome.runtime.lastError) {
          console.warn("[TaskCalendar BG] Error executing in allFrames:", chrome.runtime.lastError.message);
          // allFrames 실패 시 최상위 프레임 단독 재시도
          chrome.scripting.executeScript(
            {
              target: { tabId: tab.id },
              func: extractRowData,
              args: [linkUrl, selection, ruleToUse]
            },
            (fallbackResults) => {
              handleCaptureResults(fallbackResults, tab, linkUrl, selection, pageUrl, domain, hasCustomRule, rawRule, targetFrameId);
            }
          );
          return;
        }
        handleCaptureResults(results, tab, linkUrl, selection, pageUrl, domain, hasCustomRule, rawRule, targetFrameId);
      }
    );
  });
}

// 1. 우클릭 컨텍스트 메뉴 클릭 (iframe 내 발생 시 info.frameId, info.frameUrl 전달)
chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== "taskcalendar-add") return;
  triggerCapture(tab, info.linkUrl || "", info.selectionText || "", info.frameUrl || info.pageUrl || "", info.frameId);
});

// 2. 확장 프로그램 툴바 아이콘 클릭 (Gmail 등 자체 우클릭 메뉴가 있는 사이트 대응)
if (chrome.action && chrome.action.onClicked) {
  chrome.action.onClicked.addListener((tab) => {
    triggerCapture(tab, "", "", tab.url || "");
  });
}

// 3. 단축키 실행 (Ctrl+Shift+K)
if (chrome.commands && chrome.commands.onCommand) {
  chrome.commands.onCommand.addListener((command) => {
    if (command === "open-taskcalendar-add") {
      chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        if (tabs && tabs.length > 0) {
          triggerCapture(tabs[0], "", "", tabs[0].url || "");
        }
      });
    }
  });
}
