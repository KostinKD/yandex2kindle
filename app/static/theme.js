(() => {
  const key = 'yandex2kindle-theme';
  const valid = value => ['light', 'dark', 'system'].includes(value);
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  let preference = 'system';
  try {
    const saved = localStorage.getItem(key);
    if (valid(saved)) preference = saved;
  } catch { /* Theme switching also works when browser storage is unavailable. */ }
  const apply = () => {
    const theme = preference === 'system' ? (media.matches ? 'dark' : 'light') : preference;
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    const select = document.getElementById('theme-select');
    if (select) select.value = preference;
  };
  apply(); // Runs before the stylesheet to prevent a flash of the wrong theme.
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.getElementById('theme-select')?.addEventListener('change', event => {
      preference = valid(event.target.value) ? event.target.value : 'system';
      try { localStorage.setItem(key, preference); } catch {}
      apply();
    });
  });
  const systemChanged = () => { if (preference === 'system') apply(); };
  if (media.addEventListener) media.addEventListener('change', systemChanged);
  else media.addListener(systemChanged);
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) {
      preference = valid(event.newValue) ? event.newValue : 'system';
      apply();
    }
  });
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch(() => {});
    });
  }
})();
