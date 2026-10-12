/* deep-roots theme toggle — dark/light with persistence.
   NOTE: the no-flash bootstrap runs inline in <head> of every page:
   <script>(function(){try{var t=localStorage.getItem('roots-theme');if(!t){t=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';}document.documentElement.setAttribute('data-theme',t);}catch(e){document.documentElement.setAttribute('data-theme','dark');}})();</script>
*/
(function () {
  function init() {
    var btn = document.querySelector('.theme-toggle');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var html = document.documentElement;
      var next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      html.setAttribute('data-theme', next);
      try { localStorage.setItem('roots-theme', next); } catch (e) {}
    });
    // keep multiple open tabs in sync
    window.addEventListener('storage', function (ev) {
      if (ev.key === 'roots-theme' && ev.newValue) {
        document.documentElement.setAttribute('data-theme', ev.newValue);
      }
    });
    // keyboard shortcut: t toggles the theme (unless typing)
    document.addEventListener('keydown', function (ev) {
      var tag = (ev.target && ev.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || ev.metaKey || ev.ctrlKey || ev.altKey) return;
      if (ev.key === 't' || ev.key === 'T') {
        btn.click();
      }
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else { init(); }
})();
