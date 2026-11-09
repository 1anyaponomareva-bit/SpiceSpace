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
      ['.month-card', 'clip-silver', CLIP],
      ['.season-paper', 'clip-silver', CLIP],
      ['.season-paper', 'pie', 'right:-14px;bottom:-24px;width:74px;transform:rotate(-5deg)'],
    ],
    winter: [
      ['.month-card', 'clip-hearts', CLIP],
      ['.season-paper', 'clip-hearts', CLIP],
      ['.season-paper', 'gift', 'right:-12px;bottom:-22px;width:64px;transform:rotate(6deg)'],
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
