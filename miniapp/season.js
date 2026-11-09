/* Seasonal decoration: picks the season by date, then adds
   photo stickers (the owner's images in assets/season). Purely visual. */
(function () {
  function pickSeason() {
    var q = new URLSearchParams(window.location.search).get('season');
    if (q === 'off') return '';
    if (q === 'winter' || q === 'autumn') return q;
    var d = new Date();
    var m = d.getMonth(); // 0 = January
    var day = d.getDate();
    if (m === 11 || (m === 0 && day <= 14)) return 'winter';
    if (m === 10) return 'autumn';
    return '';
  }

  var season = pickSeason();
  if (!season) return;
  document.documentElement.setAttribute('data-season', season);

  var CLIP = 'left:50%;top:-34px;width:64px;transform:translateX(-50%) rotate(-3deg)';
  // [host selector, image, css] — positions relative to the host card.
  var STICKERS = {
    autumn: [
      ['.month-card', 'clip-leopard', CLIP],
      ['.week-card', 'mulled-wine', 'left:-14px;top:-34px;width:52px;transform:rotate(-8deg)'],
      ['.today-card', 'pie', 'right:-14px;bottom:-26px;width:78px;transform:rotate(-5deg)'],
      ['.times-card', 'plaid', 'left:-14px;top:-30px;width:56px;transform:rotate(-9deg)'],
      ['.season-paper', 'clip-leopard', CLIP],
      ['.season-paper', 'lantern', 'right:-10px;bottom:-24px;width:52px;transform:rotate(4deg)'],
    ],
    winter: [
      ['.month-card', 'clip-hearts', CLIP],
      ['.week-card', 'bow', 'left:-12px;top:-30px;width:44px;transform:rotate(-12deg)'],
      ['.today-card', 'gift', 'right:-14px;bottom:-22px;width:68px;transform:rotate(6deg)'],
      ['.times-card', 'star', 'left:-12px;top:-26px;width:46px;transform:rotate(-14deg)'],
      ['.season-paper', 'clip-hearts', CLIP],
      ['.season-paper', 'champagne', 'right:-6px;bottom:-22px;width:30px;transform:rotate(8deg)'],
      ['.season-paper', 'star', 'left:-12px;top:-14px;width:40px;transform:rotate(-18deg)'],
    ],
  };

  // The 12-week grid becomes a sheet of paper that the clip holds.
  function wrapCalendarPaper() {
    var grid = document.querySelector('.calendar-grid-wrap');
    var legend = document.querySelector('.calendar-legend');
    if (!grid || grid.parentNode.classList.contains('season-paper')) return;
    var paper = document.createElement('div');
    paper.className = 'season-paper';
    grid.parentNode.insertBefore(paper, grid);
    paper.appendChild(grid);
    if (legend) paper.appendChild(legend);
  }

  function decorate() {
    wrapCalendarPaper();
    STICKERS[season].forEach(function (s) {
      var host = document.querySelector(s[0]);
      if (!host || host.querySelector(':scope > .sticker[data-k="' + s[1] + '"]')) return;
      host.classList.add('has-sticker');
      var img = document.createElement('img');
      img.className = 'sticker';
      img.dataset.k = s[1];
      img.alt = '';
      img.setAttribute('aria-hidden', 'true');
      img.decoding = 'async';
      img.src = './assets/season/' + s[1] + '.webp';
      img.style.cssText = s[2];
      host.appendChild(img);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', decorate, { once: true });
  } else {
    decorate();
  }
})();
