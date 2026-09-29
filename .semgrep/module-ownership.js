function cardName(j) {
  var t = j.target || '';
  // ok: board-card-name-cleanup-outside-cardname
  t = t.replace(/x/, '');
  return t;
}
function normalizedCardName(j) { return cardName(j).toLowerCase(); }
function title(j) {
  // ruleid: board-card-name-cleanup-outside-cardname
  return j.target.replace(/x/, '');
}
