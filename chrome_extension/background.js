chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "taskcalendar-add",
    title: "📋 TaskCalendar에 등록",
    contexts: ["page", "selection", "link", "image"]
  });
});

// 스마트 추출 엔진 v2.0 (모든 유형의 게시판, 커뮤니티, 전자결재/온나라 및 상세페이지 자동 대응)
function extractRowData(targetLinkUrl, targetSelection) {
  var result = {
    selectedText: targetSelection || (window.getSelection() ? window.getSelection().toString() : '') || '',
    linkText: '',
    linkUrl: targetLinkUrl || '',
    metaText: '',
    author: '',
    category: '',
    postNo: '',
    detectedDate: '',
    views: '',
    votes: ''
  };

  var target = document.__tcTarget;
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
  var trContainer = startEl && startEl.closest ? startEl.closest('tr') : null;
  var container = trContainer || (startEl && startEl.closest ? startEl.closest('li, article, div.bx, div.total_wrap, div.news_wrap, div.view_wrap, div.list_item, div.ub-content, div.post, div.item, div.board_list') : null);

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
    // 접두어 제거 (작성자:, 글쓴이:, 기안자:, 담당자:, by 등)
    str = str.replace(/^(글쓴이|작성자|기안자|담당자|작성인|등록자|닉네임|by)[:\s]*/i, '').trim();
    // 회원 등급/레벨 번호/아이콘 텍스트 제거: e.g. "[6] 엄마재또...", "[7] 디디디디...", "6 MANBUNG", "Lv.10 ...", "LV 5 ..."
    str = str.replace(/^(?:\[?\d{1,3}\]?|LV\.?\s*\d{1,3})\s+/i, '').trim();
    // 순수 숫자(10115727, 173 등)는 절대 작성자가 아님!
    if (/^\d+$/.test(str.replace(/,/g, ''))) return '';
    // 날짜나 시간 패턴은 작성자가 아님
    if (/^\d{1,4}[-./]\d{1,2}[-./]\d{1,2}/.test(str)) return '';
    if (/^\d{1,2}:\d{2}/.test(str)) return '';
    // 시스템/UI 예약어 배제
    if (/^(공지|알림|선택|새창|삭제|수정|답글|댓글|조회|추천|비추|다운로드|목록|전체|인기|Hit|No|IP|PC|모바일|추천수|조회수|글쓴이|작성자|기안자|상태|일반)$/i.test(str)) return '';
    // 파일 단위/용량 배제
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

    // 헤더 행 탐색
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

    // 헤더로 매칭된 컬럼에서 값 추출
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

    // 헤더로 author 또는 date를 못 구한 경우: 각 TD 셀 내용/클래스 기반 정밀 분석
    if (!result.author || !result.detectedDate) {
      for (var cIdx = 0; cIdx < cells.length; cIdx++) {
        var cell = cells[cIdx];
        var cellText = (cell.innerText || cell.textContent || '').replace(/[\s\n\r\t]+/g, ' ').trim();

        // 제목 셀은 스킵 (linkEl을 포함하는 셀)
        if ((linkEl && cell.contains(linkEl)) || (result.linkText && cellText.includes(result.linkText))) {
          continue;
        }

        // 날짜/시간 탐색
        if (!result.detectedDate) {
          var dt = normalizeDate(cellText);
          if (dt) {
            result.detectedDate = dt;
            continue;
          }
        }

        // 작성자 탐색
        if (!result.author) {
          var authorTarget = cell.querySelector('[class*="author"], [class*="writer"], [class*="nick"], [class*="member"], [class*="user"], [class*="name"], a[href*="member"], a[href*="user"], a[href*="bbs"], a[onclick*="member"], a[onclick*="user"]') || cell;
          var cand = cleanAuthor(authorTarget.innerText || authorTarget.textContent || '');
          if (cand) {
            result.author = cand;
            continue;
          }
        }

        // 게시글 번호 탐색 (순수 4자리 이상 숫자)
        if (!result.postNo && /^\d{4,}$/.test(cellText)) {
          result.postNo = cellText;
          continue;
        }

        // 조회수 탐색 (순수 1~7자리 숫자)
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

    // 여전히 못 찾았다면 텍스트 토큰 분해
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

  // [전략 3] 상세 본문 페이지 탐색 (게시글 상세 뷰 페이지에서 우클릭한 경우)
  if (!result.author || !result.detectedDate || !result.linkText) {
    try {
      if (!result.linkText) {
        var titleElem = document.querySelector('h1.title, h2.title, .view_title, .art_title, .board_view_title, .subject, h3.title, .top_title');
        if (titleElem) {
          result.linkText = (titleElem.innerText || '').replace(/[\s\n\r\t]+/g, ' ').trim();
        }
      }
      if (!result.author) {
        var authorElem = document.querySelector('.writer, .author, .nick, .user_name, .info_author, [class*="writer"], [class*="author"]');
        if (authorElem) {
          result.author = cleanAuthor(authorElem.innerText || authorElem.textContent || '');
        }
      }
      if (!result.detectedDate) {
        var dateElem = document.querySelector('.date, .time, time, .regdate, .created_at, [class*="date"], [class*="time"]');
        if (dateElem) {
          result.detectedDate = normalizeDate(dateElem.innerText || dateElem.textContent || '');
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

  // 6. 제목에서 말머리/카테고리 자동 추출 (e.g. "[속보]", "[유머/감동]", "[보험상담실]")
  if (!result.category && result.linkText) {
    var catFromTitle = extractCategory(result.linkText);
    if (catFromTitle) {
      result.category = catFromTitle;
    }
  }

  // 7. 페이지 상단 게시판 명칭 감지 (자유게시판, 공지사항, 업무게시판 등)
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

  // 8. 구조화된 메타 텍스트 조립
  var metaParts = [];
  if (result.postNo) metaParts.push('게시글 번호: ' + result.postNo);
  if (result.author) metaParts.push('작성자: ' + result.author);
  if (result.detectedDate) metaParts.push('등록일: ' + result.detectedDate);
  if (result.views) metaParts.push('조회: ' + result.views);
  if (result.votes) metaParts.push('추천: ' + result.votes);
  if (result.category) metaParts.push('분류: ' + result.category);

  result.metaText = metaParts.join('\n');

  return result;
}

var popupWindowId = null;

function openOrFocusPopup(data) {
  chrome.storage.local.set({ taskCalendarData: data }, () => {
    if (popupWindowId !== null) {
      chrome.windows.get(popupWindowId, (existingWin) => {
        if (!chrome.runtime.lastError && existingWin) {
          // 이미 팝업 창이 열려 있는 경우: 창을 맨 앞으로 활성화하고 내용 갱신
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

    // 단일 팝업 창 생성 (중복 방지)
    chrome.windows.create(
      {
        url: "popup.html",
        type: "popup",
        width: 430,
        height: 515,
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

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== "taskcalendar-add") return;

  var clickedLinkUrl = info.linkUrl || "";
  var clickedSelection = info.selectionText || "";

  if (!tab.url || tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://")) {
    const data = {
      selectedText: clickedSelection,
      linkText: "",
      linkUrl: clickedLinkUrl,
      metaText: "",
      author: "",
      detectedDate: "",
      pageTitle: tab.title || "",
      pageUrl: clickedLinkUrl || tab.url || "",
      originTabId: null
    };
    openOrFocusPopup(data);
    return;
  }

  chrome.scripting.executeScript(
    {
      target: { tabId: tab.id },
      func: extractRowData,
      args: [clickedLinkUrl, clickedSelection]
    },
    (results) => {
      if (chrome.runtime.lastError) {
        console.warn("[TaskCalendar BG] Error:", chrome.runtime.lastError.message);
      }
      var captured = (results && results[0] && results[0].result) || {};
      console.log('[TaskCalendar BG] captured:', JSON.stringify(captured));

      var finalLinkText = captured.linkText || "";
      var finalLinkUrl = captured.linkUrl || clickedLinkUrl || "";
      var finalSelectedText = captured.selectedText || clickedSelection || "";

      // 만약 링크 텍스트를 못 찾았는데 짧은 텍스트를 선택한 상태라면 선택 텍스트를 제목으로 보완
      if (!finalLinkText && finalSelectedText && finalSelectedText.length <= 150) {
        finalLinkText = finalSelectedText.trim();
      }

      const data = {
        selectedText: finalSelectedText,
        linkText: finalLinkText,
        linkUrl: finalLinkUrl,
        metaText: captured.metaText || "",
        author: captured.author || "",
        category: captured.category || "",
        detectedDate: captured.detectedDate || "",
        pageTitle: tab.title || "",
        pageUrl: finalLinkUrl || info.pageUrl || tab.url || "",
        originTabId: tab.id
      };
      openOrFocusPopup(data);
    }
  );
});
