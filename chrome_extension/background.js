function setupContextMenu() {
  try {
    chrome.contextMenus.removeAll(() => {
      chrome.contextMenus.create({
        id: "taskcalendar-add",
        title: "바로업무로 보내기",
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
    selectedText: '', // 내용(본문)은 보안상 절대 추출하지 않음
    linkText: '',
    linkUrl: targetLinkUrl || '',
    metaText: '',
    author: '',
    department: '',
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

  if (targetSelection && typeof targetSelection === 'string' && targetSelection.trim()) {
    result.selectedText = targetSelection.trim();
    result.desc = targetSelection.trim();
  }

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
    // 4. 내용: 보안상 수집하지 않음
    result.descOverride = '';
    result.selectedText = '';
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
    str = str.replace(/[:\s]*$/, '').trim();
    str = str.replace(/^(?:\[?\d{1,3}\]?|LV\.?\s*\d{1,3})\s+/i, '').trim();
    if (/^(조회|조회수|추천|추천수|비추|댓글|등록일|작성일|수정일)\s*:?\s*\d+/i.test(str)) return '';
    if (/^\d+$/.test(str.replace(/,/g, ''))) return '';
    if (/^\d{1,4}[-./]\d{1,2}[-./]\d{1,2}/.test(str)) return '';
    if (/^\d{1,2}:\d{2}/.test(str)) return '';
    if (/^(공지|알림|선택|새창|삭제|수정|답글|댓글|조회|추천|비추|다운로드|목록|전체|인기|Hit|No|IP|PC|모바일|추천수|조회수|글쓴이|작성자|기안자|상태|일반)$/i.test(str)) return '';
    if (/^\d+\s*(KB|MB|GB|B|건|개|원|명|페이지)$/i.test(str)) return '';
    if (str.length < 1 || str.length > 50) return '';
    return str;
  }

  // [헬퍼] 부서/관련근거 검증 및 정제 함수
  function cleanDepartment(str) {
    if (!str) return '';
    str = str.replace(/[\s\n\r\t]+/g, ' ').trim();
    str = str.replace(/^(부서|기안부서|담당부서|소속|소속부서|부서명|발신부서|수신부서|처리부서|관련근거)[:\s]*/i, '').trim();
    str = str.replace(/[:\s]*$/, '').trim();
    if (/^(조회|조회수|추천|추천수|비추|댓글|등록일|작성일|수정일)\s*:?\s*\d+/i.test(str)) return '';
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

  // [헬퍼] 정규식 패턴 추출 (사용자정의 패턴 및 괄호 추출)
  function applyExtractPattern(text, pattern) {
    if (!text || !pattern || typeof pattern !== 'string') return text || '';
    var p = pattern.trim();
    if (!p) return text;
    try {
      if (p === '대괄호' || p === 'bracket') p = '\\[(.*?)\\]';
      else if (p === '소괄호' || p === 'paren') p = '\\((.*?)\\)';
      else if (p === '첫단어' || p === 'first_word') p = '^([^\\s]+)';
      var reg = new RegExp(p);
      var match = text.match(reg);
      if (match) {
        return (match[1] !== undefined ? match[1] : match[0]).trim();
      }
    } catch(e) {}
    return text;
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
    var departmentCellIdx = -1;
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
      } else if (/^(부서|기안부서|담당부서|소속|소속부서|부서명|발신부서|수신부서|dept|department)$/i.test(ht)) {
        departmentCellIdx = col;
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
    if (departmentCellIdx >= 0 && departmentCellIdx < cells.length) {
      result.department = cleanDepartment(cells[departmentCellIdx].innerText || cells[departmentCellIdx].textContent || '');
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
    if (!result.department) {
      var deptEl = container.querySelector('[class*="dept"], [class*="department"], [class*="group"], [class*="team"], [class*="part"]');
      if (deptEl) result.department = cleanDepartment(deptEl.innerText || deptEl.textContent || '');
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
  if (!result.author || !result.detectedDate || !result.linkText || !result.department) {
    try {
      if (!result.linkText) {
        var titleElem = document.querySelector('h1.title, h2.title, .view_title, .art_title, .board_view_title, .subject, h3.title, .top_title, #docTitle, #subject, #txtTitle, .doc_title, .docTitle, .viewTitle, .view-title, td.subject, span.subject, div.view_subject, p.subject');
        if (titleElem) {
          result.linkText = (titleElem.innerText || '').replace(/[\s\n\r\t]+/g, ' ').trim();
        }
      }
      if (!result.department) {
        var deptElem = document.querySelector('.dept, .department, .drafter_dept, .user_dept, [class*="dept"], [class*="department"], #dept, #department, #drafterDept, td.dept, span.dept, .org_name');
        if (deptElem) {
          result.department = cleanDepartment(deptElem.innerText || deptElem.textContent || '');
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

  // 8. [전략 3.5] 공공/기업 전자결재·온나라·그룹웨어 표(Table) 헤더 및 레이블 스마트 자동 매칭
  if (!result.department || !result.author || !result.detectedDate || !result.linkText) {
    try {
      var allTables = document.querySelectorAll('table');
      for (var ti = 0; ti < allTables.length; ti++) {
        var tbl = allTables[ti];
        var rows = tbl.rows;
        if (!rows) continue;
        for (var ri = 0; ri < rows.length; ri++) {
          var row = rows[ri];
          var cells = row.cells;
          if (!cells) continue;
          for (var ci = 0; ci < cells.length; ci++) {
            var cell = cells[ci];
            var cText = (cell.innerText || cell.textContent || '').replace(/[\s\n\r\t]+/g, '').trim();
            var nextCell = (ci + 1 < cells.length) ? cells[ci + 1] : null;
            if (!nextCell) continue;

            var valText = (nextCell.innerText || nextCell.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();

            // 부서 감지
            if (!result.department && /^(기안부서|담당부서|소속부서|발신부서|수신부서|처리부서|소속|소속기관|부서|부서명)$/.test(cText)) {
              var cleanD = cleanDepartment(valText);
              if (cleanD) result.department = cleanD.substring(0, 50);
            }
            // 기안자/작성자 감지
            if (!result.author && /^(기안자|작성자|담당자|기안인|작성인|등록자|글쓴이|기안자명|작성자명)$/.test(cText)) {
              var cleanA = cleanAuthor(valText);
              if (cleanA) result.author = cleanA.substring(0, 50);
            }
            // 기안일자/등록일자 감지
            if (!result.detectedDate && /^(기안일자|기안일|작성일자|작성일|등록일자|등록일|시행일자|일자)$/.test(cText)) {
              var normD = normalizeDate(valText);
              if (normD) result.detectedDate = normD.substring(0, 50);
            }
            // 문서제목 감지
            if (!result.linkText && /^(문서제목|제목|안건명|과제명)$/.test(cText)) {
              if (valText && valText.length >= 2) result.linkText = valText.substring(0, 50);
            }
          }
        }
      }

      // DL / DT / DD 구조 검사
      var allDts = document.querySelectorAll('dt');
      for (var di = 0; di < allDts.length; di++) {
        var dt = allDts[di];
        var dtText = (dt.innerText || dt.textContent || '').replace(/[\s\n\r\t]+/g, '').trim();
        var dd = dt.nextElementSibling;
        if (dd && dd.tagName === 'DD') {
          var ddText = (dd.innerText || dd.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
          if (!result.department && /^(기안부서|담당부서|소속부서|발신부서|부서|소속)$/.test(dtText)) {
            var cDep = cleanDepartment(ddText);
            if (cDep) result.department = cDep.substring(0, 50);
          }
          if (!result.author && /^(기안자|작성자|담당자|등록자|글쓴이)$/.test(dtText)) {
            var cAut = cleanAuthor(ddText);
            if (cAut) result.author = cAut.substring(0, 50);
          }
          if (!result.detectedDate && /^(기안일자|작성일자|등록일자|일자)$/.test(dtText)) {
            var nDate = normalizeDate(ddText);
            if (nDate) result.detectedDate = nDate.substring(0, 50);
          }
        }
      }
    } catch(e) {}
  }

  // [헬퍼] 사이트 맞춤 규칙 요소 스마트 매칭 (행 내부 상대 선택자, 레이블 매칭 및 하위 경로 유연 탐색)
  function queryCustomElement(rootNode, selector) {
    if (!rootNode || !selector || typeof selector !== 'string') return null;
    selector = selector.trim();
    if (!selector || selector === '__none__') return null;

    // 1. :has-text("...") 유사 선택자 지원 (예: th:has-text("기안부서") + td)
    var hasTextMatch = selector.match(/^([a-z0-9_-]+):has-text\("([^"]+)"\)\s*\+\s*([a-z0-9_-]+)(?:\s+(.+))?$/i);
    if (hasTextMatch) {
      var prefixTag = hasTextMatch[1].toUpperCase();
      var targetText = hasTextMatch[2].replace(/[\s\n\r\t]+/g, '').trim();
      var nextTag = hasTextMatch[3].toUpperCase();
      var subChildSel = hasTextMatch[4] ? hasTextMatch[4].trim() : '';

      var doc = (rootNode === document ? document : (rootNode.ownerDocument || document));
      var candidates = doc.querySelectorAll(prefixTag);
      for (var ci = 0; ci < candidates.length; ci++) {
        var cEl = candidates[ci];
        var cText = (cEl.innerText || cEl.textContent || '').replace(/[\s\n\r\t]+/g, '').trim();
        if (cText === targetText || (targetText.length >= 2 && cText.includes(targetText))) {
          var sibling = cEl.nextElementSibling;
          while (sibling && sibling.tagName !== nextTag) {
            sibling = sibling.nextElementSibling;
          }
          if (sibling) {
            if (subChildSel) {
              var subEl = sibling.querySelector(subChildSel);
              if (subEl) return subEl;
            }
            return sibling;
          }
        }
      }
    }

    try {
      var found = rootNode.querySelector(selector);
      if (found) return found;
    } catch(e) {}

    // 선택자에 li, a, tr 등의 조상 태그 경로가 포함되어 있다면 행 내부 서브 선택자 시도
    var parts = selector.split(/\s*>\s*/);
    for (var i = 0; i < parts.length - 1; i++) {
      var subSel = parts.slice(i + 1).join(' > ');
      try {
        var foundSub = (rootNode.querySelector && rootNode.querySelector(subSel)) || document.querySelector(subSel);
        if (foundSub) return foundSub;
      } catch(e) {}
    }

    var lastPart = parts[parts.length - 1];
    if (lastPart && lastPart !== selector) {
      try {
        var foundLast = (rootNode.querySelector && rootNode.querySelector(lastPart)) || document.querySelector(lastPart);
        if (foundLast) return foundLast;
      } catch(e) {}
    }

    if (rootNode !== document) {
      try {
        var docFound = document.querySelector(selector);
        if (docFound) return docFound;
        if (lastPart && lastPart !== selector) {
          var docLast = document.querySelector(lastPart);
          if (docLast) return docLast;
        }
      } catch(e) {}
    }

    return null;
  }

  // [전략 4] 사이트별 맞춤 규칙(Custom Rule: 선택자 & 사용자정의 서식 & 추출 패턴) 적용
  var ruleSelectors = (customRule && customRule.selectors) ? customRule.selectors : (customRule || {});
  var ruleTemplates = (customRule && customRule.templates) ? customRule.templates : {};
  var rulePatterns = (customRule && customRule.patterns) ? customRule.patterns : {};

  if (ruleSelectors && typeof ruleSelectors === 'object') {
    var rootEl = container || document;

    // 제목 맞춤 규칙
    if (ruleSelectors.title === '__none__') {
      result.linkText = '';
    } else if (ruleSelectors.title) {
      var tEl = queryCustomElement(rootEl, ruleSelectors.title);
      if (tEl) {
        var tText = (tEl.innerText || tEl.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
        if (tText) {
          if (rulePatterns && rulePatterns.title) tText = applyExtractPattern(tText, rulePatterns.title);
          result.linkText = tText.substring(0, 50);
        }
      }
    }

    // 분류 맞춤 규칙
    if (ruleSelectors.category === '__none__') {
      result.category = '';
    } else if (ruleSelectors.category) {
      var cEl = queryCustomElement(rootEl, ruleSelectors.category);
      if (cEl) {
        var cText = (cEl.innerText || cEl.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
        if (cText) {
          if (rulePatterns && rulePatterns.category) cText = applyExtractPattern(cText, rulePatterns.category);
          result.category = cText.substring(0, 50);
        }
      }
    }

    // 부서 맞춤 규칙
    if (ruleSelectors.department === '__none__') {
      result.department = '';
    } else if (ruleSelectors.department) {
      var dEl = queryCustomElement(rootEl, ruleSelectors.department);
      if (dEl) {
        var dText = (dEl.innerText || dEl.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
        dText = dText.replace(/^(부서|기안부서|담당부서|소속|소속부서|부서명|발신부서|수신부서|처리부서|관련근거)[:\s]*/i, '').trim();
        dText = dText.replace(/[:\s]*$/, '').trim();
        if (dText) {
          if (rulePatterns && rulePatterns.department) dText = applyExtractPattern(dText, rulePatterns.department);
          result.department = dText.substring(0, 50);
        }
      }
    }

    // 기안자/작성자 맞춤 규칙
    if (ruleSelectors.author === '__none__') {
      result.author = '';
    } else if (ruleSelectors.author) {
      var aEl = queryCustomElement(rootEl, ruleSelectors.author);
      if (aEl) {
        var aText = (aEl.innerText || aEl.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
        aText = aText.replace(/^(글쓴이|작성자|기안자|담당자|작성인|등록자|닉네임|by)[:\s]*/i, '').trim();
        aText = aText.replace(/[:\s]*$/, '').trim();
        aText = aText.replace(/^(?:\[?\d{1,3}\]?|LV\.?\s*\d{1,3})\s+/i, '').trim();
        if (aText) {
          if (rulePatterns && rulePatterns.author) aText = applyExtractPattern(aText, rulePatterns.author);
          result.author = aText.substring(0, 50);
        }
      }
    }

    // 상태 맞춤 규칙
    if (ruleSelectors.status === '__none__') {
      result.status = '';
    } else if (ruleSelectors.status) {
      var sEl = queryCustomElement(rootEl, ruleSelectors.status);
      if (sEl) {
        var sText = (sEl.innerText || sEl.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
        if (sText) {
          if (rulePatterns && rulePatterns.status) sText = applyExtractPattern(sText, rulePatterns.status);
          result.status = sText.substring(0, 50);
        }
      }
    }

    // 날짜 맞춤 규칙
    if (ruleSelectors.date === '__none__') {
      result.detectedDate = '';
    } else if (ruleSelectors.date) {
      var dEl = queryCustomElement(rootEl, ruleSelectors.date);
      if (dEl) {
        var dText = normalizeDate(dEl.innerText || dEl.textContent || '');
        if (dText) result.detectedDate = dText.substring(0, 50);
      }
    }

    // 내용/비고 맞춤 규칙
    if (ruleSelectors.desc === '__none__') {
      result.selectedText = '';
      result.descOverride = '';
      result.desc = '';
    } else if (ruleSelectors.desc) {
      var deEl = queryCustomElement(rootEl, ruleSelectors.desc);
      if (deEl) {
        var deText = (deEl.innerText || deEl.textContent || '').trim();
        if (deText) {
          if (rulePatterns && rulePatterns.desc) deText = applyExtractPattern(deText, rulePatterns.desc);
          result.selectedText = deText;
          result.descOverride = deText;
          result.desc = deText;
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
      '제목': (rulePatterns && rulePatterns.title) ? applyExtractPattern(result.linkText || '', rulePatterns.title) : (result.linkText || ''),
      'title': (rulePatterns && rulePatterns.title) ? applyExtractPattern(result.linkText || '', rulePatterns.title) : (result.linkText || ''),
      '분류': (rulePatterns && rulePatterns.category) ? applyExtractPattern(result.category || '', rulePatterns.category) : (result.category || ''),
      'category': (rulePatterns && rulePatterns.category) ? applyExtractPattern(result.category || '', rulePatterns.category) : (result.category || ''),
      '기안자': (rulePatterns && rulePatterns.author) ? applyExtractPattern(result.author || '', rulePatterns.author) : (result.author || ''),
      '작성자': (rulePatterns && rulePatterns.author) ? applyExtractPattern(result.author || '', rulePatterns.author) : (result.author || ''),
      'author': (rulePatterns && rulePatterns.author) ? applyExtractPattern(result.author || '', rulePatterns.author) : (result.author || ''),
      '부서': (rulePatterns && rulePatterns.department) ? applyExtractPattern(result.department || '', rulePatterns.department) : (result.department || ''),
      'department': (rulePatterns && rulePatterns.department) ? applyExtractPattern(result.department || '', rulePatterns.department) : (result.department || ''),
      'dept': (rulePatterns && rulePatterns.department) ? applyExtractPattern(result.department || '', rulePatterns.department) : (result.department || ''),
      '상태': (rulePatterns && rulePatterns.status) ? applyExtractPattern(result.status || '', rulePatterns.status) : (result.status || ''),
      'status': (rulePatterns && rulePatterns.status) ? applyExtractPattern(result.status || '', rulePatterns.status) : (result.status || ''),
      '날짜': result.detectedDate || '',
      'date': result.detectedDate || '',
      '비고': (rulePatterns && rulePatterns.desc) ? applyExtractPattern(result.desc || result.descOverride || result.selectedText || '', rulePatterns.desc) : (result.desc || result.descOverride || result.selectedText || ''),
      '내용': (rulePatterns && rulePatterns.desc) ? applyExtractPattern(result.desc || result.descOverride || result.selectedText || '', rulePatterns.desc) : (result.desc || result.descOverride || result.selectedText || ''),
      'desc': (rulePatterns && rulePatterns.desc) ? applyExtractPattern(result.desc || result.descOverride || result.selectedText || '', rulePatterns.desc) : (result.desc || result.descOverride || result.selectedText || ''),
      '출처': result.linkUrl || '',
      'url': result.linkUrl || ''
    };

    function resolveTemplate(tpl) {
      if (!tpl || typeof tpl !== 'string') return '';
      return tpl.replace(/\{([^{}]+)\}/g, function(match, key) {
        var filter = '';
        var k = key.trim();
        var colonIdx = k.search(/[:|]/);
        if (colonIdx !== -1) {
          filter = k.substring(colonIdx + 1).trim();
          k = k.substring(0, colonIdx).trim().toLowerCase();
        } else {
          k = k.toLowerCase();
        }

        var val = match;
        if (k === '제목' || k === 'title') val = baseValues.title;
        else if (k === '분류' || k === 'category') val = baseValues.category;
        else if (k === '기안자' || k === '작성자' || k === 'author') val = baseValues.author;
        else if (k === '부서' || k === 'department' || k === 'dept') val = baseValues.department;
        else if (k === '상태' || k === 'status') val = baseValues.status;
        else if (k === '날짜' || k === 'date') val = baseValues.date;
        else if (k === '내용' || k === '본문' || k === '비고' || k === 'desc') val = baseValues.desc;
        else if (k === '출처' || k === '링크' || k === 'url') val = baseValues.url;

        if (filter && val && val !== match) {
          val = applyExtractPattern(val, filter);
        }
        return val;
      });
    }

    if (ruleTemplates.title && ruleTemplates.title.trim()) {
      result.linkText = resolveTemplate(ruleTemplates.title.trim());
    }
    if (ruleTemplates.category && ruleTemplates.category.trim()) {
      result.category = resolveTemplate(ruleTemplates.category.trim());
    }
    if (ruleTemplates.department && ruleTemplates.department.trim()) {
      result.department = resolveTemplate(ruleTemplates.department.trim());
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
      result.desc = result.descOverride;
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

  result.linkText = limit50(result.linkText);
  result.author = limit50(result.author);
  result.department = limit50(result.department);
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

  // 본문 / 내용 요소 판별 함수 (HTML 태그가 포함되어 있거나 문단, 영역, 서식 태그를 가진 경우)
  function isContentOrBodyElement(el) {
    if (!el) return true;
    var tag = el.tagName ? el.tagName.toUpperCase() : '';
    // 1. 태그 자체가 본문/문단/컨테이너인 경우
    if (['P', 'ARTICLE', 'SECTION', 'BLOCKQUOTE', 'PRE', 'MAIN', 'BODY', 'FORM', 'FIELDSET', 'TEXTAREA'].includes(tag)) {
      return true;
    }
    // 2. 내부에 문단(p), 줄바꿈(br), 블록(div), 테이블 등 구조/서식 태그가 포함되어 있는 경우
    if (el.querySelector && el.querySelector('p, br, div, table, ul, ol, blockquote, pre, hr, article, section')) {
      return true;
    }
    // 3. 자식 태그가 2개 이상이거나 서식 태그가 복합적인 경우
    if (el.children && el.children.length >= 2) {
      return true;
    }
    // 4. innerHTML에 문단/서식 태그가 포함되어 있는 경우
    var html = el.innerHTML || '';
    if (/<(p|br|div|table|ul|ol|li|blockquote|pre|hr)\b/i.test(html)) {
      return true;
    }
    // 5. 텍스트 자체에 줄바꿈(\n)이 포함된 경우
    var rawText = el.innerText || el.textContent || '';
    if (rawText.includes('\n')) {
      return true;
    }
    return false;
  }

  function collectFromRoot(searchRoot, isLocal) {
    var all = Array.from(searchRoot.querySelectorAll('*'));
    if (isLocal) all.unshift(searchRoot);

    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'IFRAME', 'SVG', 'PATH'].includes(el.tagName)) continue;

      var text = (el.innerText || el.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
      var href = (el.tagName === 'A' ? (el.href || el.getAttribute('href') || '') : '');
      var title = (el.getAttribute('title') || '').trim();
      var alt = (el.getAttribute('alt') || '').trim();

      // 50자 초과 요소 및 본문/태그 포함 요소는 절대 후보에 포함하지 않음 (본문 문단, 긴 영역 등 매칭 차단)
      if (!isUrl && (text.length > 50 || isContentOrBodyElement(el))) continue;
      if (title.length > 50) title = '';
      if (alt.length > 50) alt = '';

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
    return { success: false, error: '❌ 50자 이내 일치 항목 없음' };
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

  var bestText = (best.innerText || best.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
  if (!isUrl && (bestText.length > 50 || isContentOrBodyElement(best))) {
    return { success: false, error: '❌ 본문/태그 포함 항목은 선택할 수 없습니다 (50자 이내 텍스트만 가능)' };
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

  function buildSelector(el) {
    if (!el || el === document.body || el === document.documentElement) return '';

    // 1. 고유 ID 검사
    if (el.id && !/^\d+$/.test(el.id) && !/^[:_]/.test(el.id) && !el.id.includes('__') && !el.id.startsWith('tc-')) {
      try {
        var idSel = '#' + CSS.escape(el.id);
        if (document.querySelectorAll(idSel).length === 1) return idSel;
      } catch(e) {}
    }

    // 2. 표(Table) 헤더/레이블 기반 매칭 (한국 공공 전자결재/공문서/게시판 최적화)
    var td = el.closest ? el.closest('td, th') : null;
    if (td) {
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

    // 3. 안정적인 상위 컨테이너 탐색
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
      for (var k = 0; k < classes.length; k++) {
        var cls = '.' + CSS.escape(classes[k]);
        try {
          if (document.querySelectorAll(cls).length === 1) return cls;
        } catch(e) {}
      }
      if (stableAncestor && ancestorSel) {
        for (var m = 0; m < classes.length; m++) {
          var subCls = ancestorSel + ' .' + CSS.escape(classes[m]);
          try {
            if (document.querySelectorAll(subCls).length === 1) return subCls;
          } catch(e) {}
        }
      }
    }

    // 5. 상위 컨테이너 기준 경로 탐색
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

  var selector = buildSelector(best);
  var matchedVal = isUrl ? (best.href || best.getAttribute('href') || '') : (best.innerText || best.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();
  if (!isUrl && matchedVal) {
    matchedVal = matchedVal.replace(/\s+["'”’`]+$/g, '').trim();
    matchedVal = matchedVal.replace(/\s*(&(quot|#34|#39|apos);)+\s*$/gi, '').trim();
    const dQ = (matchedVal.match(/"/g) || []).length;
    if (dQ % 2 !== 0 && matchedVal.endsWith('"')) matchedVal = matchedVal.slice(0, -1).trim();
    const sQ = (matchedVal.match(/'/g) || []).length;
    if (sQ % 2 !== 0 && matchedVal.endsWith("'")) matchedVal = matchedVal.slice(0, -1).trim();
  }

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

    var popupWidth = 470;
    var popupHeight = 460;

    chrome.windows.getLastFocused({ windowTypes: ['normal'] }, (win) => {
      var createOptions = {
        url: "popup.html",
        type: "popup",
        width: popupWidth,
        height: popupHeight,
        focused: true
      };

      if (!chrome.runtime.lastError && win && typeof win.left === 'number' && typeof win.width === 'number') {
        createOptions.left = Math.round(win.left + (win.width - popupWidth) / 2);
        createOptions.top = Math.round(win.top + (win.height - popupHeight) / 2);
      }

      chrome.windows.create(createOptions, (newWin) => {
        if (newWin) {
          popupWindowId = newWin.id;
        }
      });
    });
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
        if (r0 && (r0.linkText || r0.selectedText || r0.hasTarget || r0.author || r0.department || r0.detectedDate)) {
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

    // 작성자 또는 부서 또는 날짜 감지
    if (r.author && r.author.trim()) score += 40;
    if (r.department && r.department.trim()) score += 40;
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

// 백그라운드 전용 텍스트 정제 헬퍼
function bgLimit50(str) {
  if (!str || typeof str !== 'string') return '';
  let s = str.replace(/[\s\n\r\t]+/g, ' ').trim();
  s = s.replace(/\s+["'”’`]+$/g, '').trim();
  s = s.replace(/\s*(&(quot|#34|#39|apos);)+\s*$/gi, '').trim();
  const dQuotes = (s.match(/"/g) || []).length;
  if (dQuotes % 2 !== 0 && s.endsWith('"')) s = s.slice(0, -1).trim();
  const sQuotes = (s.match(/'/g) || []).length;
  if (sQuotes % 2 !== 0 && s.endsWith("'")) s = s.slice(0, -1).trim();
  return s.substring(0, 50).trim();
}

function bgCleanAuthor(str) {
  if (!str) return '';
  str = str.replace(/[\s\n\r\t]+/g, ' ').trim();
  str = str.replace(/^(글쓴이|작성자|기안자|담당자|작성인|등록자|닉네임|by)[:\s]*/i, '').trim();
  str = str.replace(/[:\s]*$/, '').trim();
  str = str.replace(/^(?:\[?\d{1,3}\]?|LV\.?\s*\d{1,3})\s+/i, '').trim();
  if (/^(조회|조회수|추천|추천수|비추|댓글|등록일|작성일|수정일)\s*:?\s*\d+/i.test(str)) return '';
  if (/^\d+$/.test(str.replace(/,/g, ''))) return '';
  if (/^\d{1,4}[-./]\d{1,2}[-./]\d{1,2}/.test(str)) return '';
  if (/^\d{1,2}:\d{2}/.test(str)) return '';
  if (/^(공지|알림|선택|새창|삭제|수정|답글|댓글|조회|추천|비추|다운로드|목록|전체|인기|Hit|No|IP|PC|모바일|추천수|조회수|글쓴이|작성자|기안자|상태|일반)$/i.test(str)) return '';
  if (/^\d+\s*(KB|MB|GB|B|건|개|원|명|페이지)$/i.test(str)) return '';
  return str.substring(0, 50);
}

function bgCleanDepartment(str) {
  if (!str) return '';
  str = str.replace(/[\s\n\r\t]+/g, ' ').trim();
  str = str.replace(/^(부서|기안부서|담당부서|소속|소속부서|부서명|발신부서|수신부서|처리부서|관련근거)[:\s]*/i, '').trim();
  str = str.replace(/[:\s]*$/, '').trim();
  if (/^(조회|조회수|추천|추천수|비추|댓글|등록일|작성일|수정일)\s*:?\s*\d+/i.test(str)) return '';
  if (/^\d+$/.test(str.replace(/,/g, ''))) return '';
  if (/^\d{1,4}[-./]\d{1,2}[-./]\d{1,2}/.test(str)) return '';
  if (/^\d{1,2}:\d{2}/.test(str)) return '';
  if (/^(공지|알림|선택|새창|삭제|수정|답글|댓글|조회|추천|비추|다운로드|목록|전체|인기|Hit|No|IP|PC|모바일|추천수|조회수|글쓴이|작성자|기안자|상태|일반)$/i.test(str)) return '';
  if (/^\d+\s*(KB|MB|GB|B|건|개|원|명|페이지)$/i.test(str)) return '';
  return str.substring(0, 50);
}

function bgNormalizeDate(raw) {
  if (!raw) return '';
  raw = raw.replace(/[\s\n\r\t]+/g, ' ').trim();
  var m4 = raw.match(/\b(20\d{2}|19\d{2})[-./](\d{1,2})[-./](\d{1,2})\b/);
  if (m4) {
    return m4[1] + '-' + String(m4[2]).padStart(2, '0') + '-' + String(m4[3]).padStart(2, '0');
  }
  var m2 = raw.match(/\b(\d{2})[-./](\d{1,2})[-./](\d{1,2})\b/);
  if (m2) {
    var yr = parseInt(m2[1], 10);
    var fullYr = yr <= 60 ? (2000 + yr) : (1900 + yr);
    return fullYr + '-' + String(m2[2]).padStart(2, '0') + '-' + String(m2[3]).padStart(2, '0');
  }
  var mMd = raw.match(/\b(\d{1,2})[-./](\d{1,2})\b/);
  if (mMd && parseInt(mMd[1], 10) >= 1 && parseInt(mMd[1], 10) <= 12 && parseInt(mMd[2], 10) >= 1 && parseInt(mMd[2], 10) <= 31) {
    var curY = new Date().getFullYear();
    return curY + '-' + String(mMd[1]).padStart(2, '0') + '-' + String(mMd[2]).padStart(2, '0');
  }
  if (/\b\d{1,2}:\d{2}(:\d{2})?\b|\b\d+\s*분\s*전\b|\b\d+\s*시간\s*전\b|\b(오늘|방금)\b/.test(raw)) {
    var td = new Date();
    return td.getFullYear() + '-' + String(td.getMonth() + 1).padStart(2, '0') + '-' + String(td.getDate()).padStart(2, '0');
  }
  if (/\b어제\b/.test(raw)) {
    var yd = new Date();
    yd.setDate(yd.getDate() - 1);
    return yd.getFullYear() + '-' + String(yd.getMonth() + 1).padStart(2, '0') + '-' + String(yd.getDate()).padStart(2, '0');
  }
  return '';
}

function bgApplyExtractPattern(text, pattern) {
  if (!text || !pattern || typeof pattern !== 'string') return text || '';
  var p = pattern.trim();
  if (!p) return text;
  try {
    if (p === '대괄호' || p === 'bracket') p = '\\[(.*?)\\]';
    else if (p === '소괄호' || p === 'paren') p = '\\((.*?)\\)';
    else if (p === '첫단어' || p === 'first_word') p = '^([^\\s]+)';
    var reg = new RegExp(p);
    var match = text.match(reg);
    if (match) {
      return (match[1] !== undefined ? match[1] : match[0]).trim();
    }
  } catch(e) {}
  return text;
}

// 팝업과의 통신 메시지 리스너 (DOM 검색 및 재추출)
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "matchElementInTab") {
    if (request.sampleText && request.sampleText.length > 50) {
      sendResponse({ success: false, error: "50자를 초과하는 검색어는 검색할 수 없습니다." });
      return true;
    }
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
      templates: (rawC && rawC.templates) ? rawC.templates : (request.templates || {}),
      patterns: (rawC && rawC.patterns) ? rawC.patterns : (request.patterns || {})
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
    // Chrome에서 커스텀 프로토콜(taskcalendar://) 호출은 반드시 chrome.tabs.create로 새 탭을 생성해야 윈도우 셸로 안정적으로 전달됩니다.
    chrome.tabs.create({ url: urlToOpen, active: false }, (t) => {
      setTimeout(() => {
        if (t && t.id) chrome.tabs.remove(t.id).catch(() => {});
      }, 1500);
    });
    sendResponse({ success: true });
    return true;
  }

  if (request.action === "elementPickerCompleted") {
    var pTab = sender.tab;
    if (pTab) {
      chrome.tabs.sendMessage(pTab.id, { action: "stopElementPicker" }).catch(() => {});
    }

    var targetField = request.targetField || 'all';
    var pickedText = (request.pickedText || '').trim();
    var pickedSelector = (request.pickedSelector || '').trim();

    chrome.storage.local.get(['tcPreservedFormData', 'tc_site_rules'], (res) => {
      var preserved = (res && res.tcPreservedFormData) || null;
      var siteRules = (res && res.tc_site_rules) || {};

      if (preserved) {
        var fieldToAssign = (targetField && targetField !== 'all') ? targetField : 'desc';
        if (targetField === 'all') {
          // 상단 '🎯 항목 직접 찍기'를 누른 경우:
          // 비고가 비어있거나, 찍은 내용이 본문/비고인 경우 비고(desc)로 자동 배정
          if (!preserved.desc || (pickedText && pickedText !== preserved.title)) {
            fieldToAssign = 'desc';
          } else {
            fieldToAssign = 'title';
          }
        }

        var domain = preserved.siteDomain || (pTab && pTab.url ? getDomainFromUrl(pTab.url) : '');
        var dRules = (domain && siteRules[domain]) ? siteRules[domain] : null;
        var extVal = (request.extractedValue !== undefined) ? (request.extractedValue + '').trim() : '';
        var autoPattern = (request.autoPattern || '').trim();

        if (fieldToAssign === 'department') {
          var dVal = extVal || pickedText;
          var cDept = bgCleanDepartment(dVal);
          preserved.department = bgLimit50(cDept || dVal);
          preserved.departmentChecked = true;
        } else if (fieldToAssign === 'author') {
          var aVal = extVal || pickedText;
          var cAuth = bgCleanAuthor(aVal);
          preserved.author = bgLimit50(cAuth || aVal);
          preserved.authorChecked = true;
        } else if (fieldToAssign === 'title') {
          var tVal = extVal || pickedText;
          preserved.title = bgLimit50(tVal);
          preserved.linkText = preserved.title;
          preserved.titleChecked = true;
        } else if (fieldToAssign === 'category') {
          var cVal = extVal || pickedText;
          preserved.category = bgLimit50(cVal);
          preserved.categoryChecked = true;
        } else if (fieldToAssign === 'date') {
          var nd = bgNormalizeDate(extVal || pickedText);
          preserved.detectedDate = nd || bgLimit50(extVal || pickedText);
          preserved.date = preserved.detectedDate;
          preserved.dateChecked = true;
        } else if (fieldToAssign === 'status') {
          var sVal = extVal || pickedText;
          preserved.status = bgLimit50(sVal) || '등록';
          preserved.statusChecked = true;
        } else if (fieldToAssign === 'desc') {
          var bVal = extVal || pickedText;
          preserved.desc = bVal.replace(/[\r\t]+/g, ' ').trim().substring(0, 1000);
          preserved.descChecked = true;
        }

        if (!preserved.pendingRuleUpdates) preserved.pendingRuleUpdates = {};
        if (pickedSelector) {
          preserved.pendingRuleUpdates[fieldToAssign] = {
            selector: pickedSelector,
            sample: (extVal || pickedText).substring(0, 50)
          };
        }

        // 🎯 [핵심 개선] 직접 찍기 완료 즉시 해당 도메인의 맞춤 규칙(tc_site_rules)에 영구 저장!
        // 등록 버튼을 누르지 않거나 팝업을 닫더라도 다음 글부터 100% 자동 인식되도록 즉각 반영
        if (domain && pickedSelector && fieldToAssign) {
          if (!siteRules[domain]) {
            siteRules[domain] = {
              selectors: {},
              templates: {},
              patterns: {},
              samples: {},
              domain: domain,
              updatedAt: Date.now()
            };
          }
          if (!siteRules[domain].selectors) siteRules[domain].selectors = {};
          if (!siteRules[domain].patterns) siteRules[domain].patterns = {};
          if (!siteRules[domain].samples) siteRules[domain].samples = {};

          siteRules[domain].selectors[fieldToAssign] = pickedSelector;
          siteRules[domain].samples[fieldToAssign] = (extVal || pickedText).substring(0, 50);
          if (autoPattern) {
            siteRules[domain].patterns[fieldToAssign] = autoPattern;
          } else {
            // 새로 직접 찍었으므로 이전 2차 정규식 추출 규칙은 일단 제거 (팝업 모달에서 결정됨)
            delete siteRules[domain].patterns[fieldToAssign];
          }
          siteRules[domain].updatedAt = Date.now();
          preserved.hasCustomRule = true;

          chrome.storage.local.set({ tc_site_rules: siteRules });
        }

        var restoredData = {
          selectedText: '',
          linkText: preserved.title || preserved.linkText || '',
          linkUrl: preserved.url || preserved.linkUrl || '',
          metaText: '',
          author: preserved.author || '',
          department: preserved.department || '',
          category: preserved.category || '',
          status: preserved.status || '등록',
          detectedDate: preserved.date || preserved.detectedDate || '',
          desc: preserved.desc || '',
          pageTitle: preserved.title || preserved.pageTitle || '',
          pageUrl: preserved.url || preserved.pageUrl || '',
          siteDomain: preserved.siteDomain || '',
          hasCustomRule: !!preserved.hasCustomRule,
          originTabId: preserved.originTabId,
          originFrameId: preserved.originFrameId || 0,
          checkedStates: {
            title: preserved.titleChecked !== false,
            department: preserved.departmentChecked !== false,
            author: preserved.authorChecked !== false,
            category: preserved.categoryChecked !== false,
            status: preserved.statusChecked !== false,
            date: preserved.dateChecked !== false,
            desc: preserved.descChecked !== false,
            url: preserved.urlChecked === true
          },
          selectedType: preserved.selectedType || preserved.type || 'task',
          targetPickField: fieldToAssign,
          justPicked: {
            field: fieldToAssign,
            rawText: pickedText,
            selector: pickedSelector
          },
          pendingRuleUpdates: preserved.pendingRuleUpdates || {}
        };

        chrome.storage.local.remove(['tcPreservedFormData', 'tcPickTargetField'], () => {
          openOrFocusPopup(restoredData);
        });
        sendResponse({ success: true });
        return;
      }

      if (pTab) {
        triggerCapture(pTab, request.linkUrl || "", pickedText, request.frameUrl || pTab.url || "", sender.frameId);
      }
      sendResponse({ success: true });
    });
    return true;
  }

  if (request.action === "elementPickerCancelled") {
    chrome.storage.local.get(['tcPreservedFormData'], (res) => {
      var preserved = (res && res.tcPreservedFormData) || null;
      if (preserved) {
        var restoredData = {
          selectedText: '',
          linkText: preserved.title || preserved.linkText || '',
          linkUrl: preserved.url || preserved.linkUrl || '',
          metaText: '',
          author: preserved.author || '',
          department: preserved.department || '',
          category: preserved.category || '',
          status: preserved.status || '등록',
          detectedDate: preserved.date || preserved.detectedDate || '',
          desc: preserved.desc || '',
          pageTitle: preserved.title || preserved.pageTitle || '',
          pageUrl: preserved.url || preserved.pageUrl || '',
          siteDomain: preserved.siteDomain || '',
          hasCustomRule: !!preserved.hasCustomRule,
          originTabId: preserved.originTabId,
          originFrameId: preserved.originFrameId || 0,
          checkedStates: {
            title: preserved.titleChecked !== false,
            department: preserved.departmentChecked !== false,
            author: preserved.authorChecked !== false,
            category: preserved.categoryChecked !== false,
            status: preserved.statusChecked !== false,
            date: preserved.dateChecked !== false,
            desc: preserved.descChecked !== false,
            url: preserved.urlChecked === true
          },
          selectedType: preserved.selectedType || preserved.type || 'task',
          targetPickField: preserved.targetPickField || 'all'
        };
        chrome.storage.local.remove(['tcPreservedFormData', 'tcPickTargetField'], () => {
          openOrFocusPopup(restoredData);
        });
      }
    });
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

  var finalLinkText = limit50(captured.linkText || "");
  var finalLinkUrl = captured.linkUrl || linkUrl || "";
  var finalSelectedText = ""; // 보안상 본문 내용은 절대 수집하지 않음 (항목 직접 찍기 시에도 차단)

  if (!finalLinkText) {
    finalLinkText = limit50(captured.pageTitle || (tab && tab.title) || "");
  }

  var effectivePageUrl = captured.frameUrl || finalLinkUrl || pageUrl;
  var effectiveDomain = getDomainFromUrl(effectivePageUrl) || domain;

  var finalDesc = (captured.desc || captured.descOverride || captured.selectedText || "").replace(/[\r\t]+/g, ' ').trim().substring(0, 1000);
  if (!finalDesc && selection && typeof selection === 'string') {
    var selText = selection.replace(/[\r\t]+/g, ' ').trim();
    if (selText && selText !== finalLinkText) {
      finalDesc = selText.substring(0, 1000);
    }
  }

  const data = {
    selectedText: finalSelectedText,
    linkText: finalLinkText,
    linkUrl: finalLinkUrl,
    metaText: limit50(captured.metaText || ""),
    author: limit50(captured.author || ""),
    department: limit50(captured.department || ""),
    category: limit50(captured.category || ""),
    status: limit50(captured.status || "등록"),
    detectedDate: limit50(captured.detectedDate || ""),
    desc: finalDesc,
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
      department: "",
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
      templates: (rawRule && rawRule.templates) ? rawRule.templates : {},
      patterns: (rawRule && rawRule.patterns) ? rawRule.patterns : {}
    };
    var hasCustomRule = !!(rawRule && ((rawRule.selectors && Object.keys(rawRule.selectors).some(k => rawRule.selectors[k])) || (rawRule.templates && Object.keys(rawRule.templates).some(k => rawRule.templates[k])) || (rawRule.patterns && Object.keys(rawRule.patterns).some(k => rawRule.patterns[k])) || Object.keys(rawRule).some(k => rawRule[k])));

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
