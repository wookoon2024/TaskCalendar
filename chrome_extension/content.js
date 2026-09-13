// content.js - 우클릭 대상 동기 저장 (document 프로퍼티)
document.addEventListener('contextmenu', function(e) {
  document.__tcTarget = e.target;
  var a = e.target && e.target.closest ? e.target.closest('a') : null;
  document.__tcLink = a;
}, true);
