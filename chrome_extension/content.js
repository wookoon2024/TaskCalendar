// content.js - 우클릭 대상 및 개별 항목 컨테이너 동기 저장
document.addEventListener('contextmenu', function(e) {
  var target = e.target;
  document.__tcTarget = target;
  var a = target && target.closest ? target.closest('a') : null;
  document.__tcLink = a;

  // 1. target(우클릭한 요소)에서 가장 가까운 단일 항목(li, tr, article) 탐색
  var container = null;
  if (target && target.closest) {
    container = target.closest('tr, li, article');
  }
  if (!container && a && a.closest) {
    container = a.closest('tr, li, article');
  }

  // 2. ul 또는 ol의 직계 자식 탐색
  if (!container) {
    var list = (target && target.closest ? target.closest('ul, ol') : null) || (a && a.closest ? a.closest('ul, ol') : null);
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

  // 3. 개별 카드/아이템 클래스 탐색 (list, grid, wrap 등 전체 묶음 컨테이너는 제외)
  if (!container) {
    var checkEl = target || a;
    if (checkEl && checkEl.closest) {
      var card = checkEl.closest('[class*="card"], [class*="item"], [class*="row"], [class*="post"], div.bx');
      if (card) {
        var cls = (card.className || '').toLowerCase();
        if (!cls.includes('list') && !cls.includes('grid') && !cls.includes('wrap') && !cls.includes('total')) {
          container = card;
        }
      }
    }
  }

  // 4. a 태그 자체가 블록형 카드인 경우 (자식 태그가 있고 높이가 일정 이상)
  if (!container && a && a.childElementCount >= 1 && (a.offsetHeight >= 24 || a.offsetWidth >= 100)) {
    container = a;
  }

  // 5. 반복되는 형제 요소 탐색 (동일 태그 형제가 2개 이상인 부모의 자식)
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

  document.__tcContainer = container || a || target;
}, true);
