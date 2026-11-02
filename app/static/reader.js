import { Overlayer } from './foliate/overlayer.js';
// Yandex2Kindle Web EPUB Reader Controller
(function() {
  'use strict';

  const bookId = window.Y2K_BOOK_ID;
  const bookUrl = window.Y2K_BOOK_URL;

  const view = document.getElementById('view');
  const spinner = document.getElementById('loading-spinner');
  const header = document.getElementById('header');
  const footer = document.getElementById('footer');
  const chapterLabel = document.getElementById('chapter-label');
  const progressBar = document.getElementById('progress-bar');
  const progressTrack = document.getElementById('progress-track');
  const progressPercent = document.getElementById('progress-percent');
  const relocateInfo = document.getElementById('relocate-info');

  const tocDrawer = document.getElementById('toc-drawer');
  const settingsDrawer = document.getElementById('settings-drawer');
  const btnToc = document.getElementById('btn-toc');
  const btnSettings = document.getElementById('btn-settings');
  const btnFullscreen = document.getElementById('btn-fullscreen');
  const btnCloseToc = document.getElementById('btn-close-toc');
  const btnCloseSettings = document.getElementById('btn-close-settings');
  const tocList = document.getElementById('toc-list');

  const highlightBar = document.getElementById('highlight-bar');
  const btnCreateHighlight = document.getElementById('btn-create-highlight');

  let currentFraction = 0;
  let currentCfi = '';
  let saveTimer = null;
  let currentSelectionText = '';
  let selectionCfi = '';
  let savedHighlights = [];
  let readerReady = false;
  const status = document.getElementById('reader-status');
  let statusTimer;
  function notify(message) {
    status.textContent = message; status.hidden = false;
    clearTimeout(statusTimer); statusTimer = setTimeout(() => { status.hidden = true; }, 5000);
  }
  function readPreference(key, fallback) {
    try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
  }
  function savePreference(key, value) {
    try { localStorage.setItem(key, value); } catch {}
  }
  async function checkResponse(response) {
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(typeof data.detail === 'string' ? data.detail : `Ошибка HTTP ${response.status}`);
    }
    return response;
  }
  function drawHighlights() {
    for (const item of savedHighlights) {
      if (item.cfi?.startsWith('epubcfi(')) view.addAnnotation({ value: item.cfi, color: item.color }).catch(() => {});
    }
  }
  async function loadHighlights() {
    const response = await checkResponse(await fetch(`/api/books/${bookId}/highlights`));
    savedHighlights = await response.json();
    drawHighlights();
  }

  // Preferences
  let currentTheme = readPreference('y2k_reader_theme', 'light');
  let currentFont = readPreference('y2k_reader_font', 'serif');
  let currentFontSize = parseInt(readPreference('y2k_reader_font_size', '100'), 10);
  let currentMargin = parseInt(readPreference('y2k_reader_margin', '40'), 10);

  function applyTheme(theme) {
    currentTheme = theme;
    savePreference('y2k_reader_theme', theme);
    document.body.className = `theme-${theme}`;
    document.querySelectorAll('.theme-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.theme === theme);
    });
    updateRendererStyles();
  }

  function applyFont(font) {
    currentFont = font;
    savePreference('y2k_reader_font', font);
    document.querySelectorAll('.font-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.font === font);
    });
    updateRendererStyles();
  }

  function applyFontSize(size) {
    currentFontSize = Math.max(70, Math.min(200, size));
    savePreference('y2k_reader_font_size', currentFontSize);
    const display = document.getElementById('font-size-display');
    if (display) display.textContent = `${currentFontSize}%`;
    updateRendererStyles();
  }

  function updateRendererStyles() {
    if (!view || !view.renderer || !view.renderer.setStyles) return;
    
    let fontCss = "Georgia, serif";
    if (currentFont === 'sans') fontCss = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
    if (currentFont === 'system') fontCss = "system-ui, sans-serif";

    let color = "#1c1b18";
    let bg = "#faf8f5";
    if (currentTheme === 'sepia') { color = "#3e2d1d"; bg = "#f5eedb"; }
    else if (currentTheme === 'dark') { color = "#d2d2d8"; bg = "#141416"; }
    else if (currentTheme === 'eink') { color = "#000000"; bg = "#ffffff"; }

    const css = `
      body {
        font-family: ${fontCss} !important;
        font-size: ${currentFontSize}% !important;
        color: ${color} !important;
        background-color: ${bg} !important;
        line-height: 1.6 !important;
        padding: 0 ${currentMargin}px !important;
      }
      p { margin-bottom: 0.8em !important; }
      a { color: inherit !important; }
      img { max-width: 100% !important; height: auto !important; }
    `;
    try {
      view.renderer.setStyles(css);
    } catch (e) {
      console.warn("Could not set renderer styles:", e);
    }
  }

  // Progress Sync
  async function fetchSavedProgress() {
    try {
      const res = await fetch(`/api/books/${bookId}/progress`);
      if (res.ok) {
        return await res.json();
      }
    } catch (e) {
      console.warn("Failed to fetch initial progress:", e);
    }
    return { progress: 0, position: "" };
  }

  function scheduleSaveProgress(fraction, cfi) {
    currentFraction = fraction;
    currentCfi = cfi || '';
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        await fetch(`/api/books/${bookId}/progress`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            progress: currentFraction,
            position: currentCfi
          })
        });
      } catch (e) {
        console.warn("Failed to save progress:", e);
      }
    }, 1200);
  }

  // TOC
  function buildTOC(toc) {
    tocList.innerHTML = '';
    if (!toc || !toc.length) {
      tocList.innerHTML = '<li class="toc-item">Оглавление отсутствует</li>';
      return;
    }
    function renderItems(items, depth) {
      for (const item of items) {
        const li = document.createElement('li');
        const a = document.createElement('a');
        a.className = 'toc-item';
        a.textContent = (depth > 0 ? '— '.repeat(depth) : '') + (item.label || 'Глава');
        a.href = '#';
        a.addEventListener('click', (e) => {
          e.preventDefault();
          if (item.href) {
            view.goTo(item.href);
            closeDrawers();
          }
        });
        li.appendChild(a);
        tocList.appendChild(li);
        if (item.subitems && item.subitems.length) {
          renderItems(item.subitems, depth + 1);
        }
      }
    }
    renderItems(toc, 0);
  }

  function closeDrawers() {
    if (tocDrawer) tocDrawer.hidden = true;
    if (settingsDrawer) settingsDrawer.hidden = true;
  }

  // Initialize
  async function init() {
    await customElements.whenDefined('foliate-view');
    applyTheme(currentTheme);
    applyFont(currentFont);
    applyFontSize(currentFontSize);

    const initialProgress = await fetchSavedProgress();

    // View relocate event
    view.addEventListener('relocate', (e) => {
      const detail = e.detail;
      if (!detail) return;

      const fraction = typeof detail.fraction === 'number' ? detail.fraction : 0;
      const pct = Math.round(fraction * 100);

      progressBar.style.width = `${pct}%`;
      progressPercent.textContent = `${pct}%`;

      if (detail.tocItem && detail.tocItem.label) {
        chapterLabel.textContent = detail.tocItem.label;
      }

      currentFraction = fraction;
      currentCfi = detail.cfi || '';
      if (readerReady) scheduleSaveProgress(fraction, currentCfi);
    });

    view.addEventListener('draw-annotation', event => {
      const colors = { yellow: '#e5c75c', green: '#8caf80', blue: '#91b8cc', pink: '#d8a5ad' };
      event.detail.draw(Overlayer.highlight, { color: colors[event.detail.annotation.color] || colors.yellow });
    });
    view.addEventListener('create-overlay', drawHighlights);
    view.addEventListener('load', event => {
      event.detail.doc.addEventListener('keydown', handleNavigationKey);
    });
    try {
      await loadHighlights();
    } catch { notify('Не удалось загрузить сохранённые цитаты.'); }
    try {
      await view.open(bookUrl);
      if (spinner) spinner.style.display = 'none';

      // Restore position if present
      if (initialProgress && (initialProgress.position || initialProgress.progress > 0)) {
        try {
          if (initialProgress.position?.startsWith('epubcfi(')) {
            await view.init({ lastLocation: initialProgress.position });
          } else {
            await view.init({ showTextStart: true });
            if (initialProgress.progress > 0) await view.goToFraction(initialProgress.progress);
          }
        } catch (posErr) {
          console.warn("Could not restore saved position, opening start:", posErr);
          await view.init({ showTextStart: true });
          if (initialProgress.progress > 0) await view.goToFraction(initialProgress.progress);
        }
      } else {
        await view.init({ showTextStart: true });
      }

      // Populate TOC
      if (view.book && view.book.toc) {
        buildTOC(view.book.toc);
      }

      updateRendererStyles();
      readerReady = true;
      drawHighlights();
    } catch (err) {
      console.error("Error opening book:", err);
      if (spinner) {
        spinner.style.display = '';
        spinner.replaceChildren();
        const message = document.createElement('p');
        message.textContent = 'Не удалось открыть книгу: ' + (err.message || 'Ошибка формата');
        spinner.append(message);
      }
      return;
    }

    // Detect selection in iframe to offer highlight creation
    setInterval(() => {
      try {
        const contents = view.renderer?.getContents?.() || [];
        let selectedText = '';
        let selectedCfi = '';
        for (const item of contents) {
          const sel = item.doc?.defaultView?.getSelection?.();
          if (sel && sel.rangeCount && !sel.isCollapsed && sel.toString().trim()) {
            selectedText = sel.toString().trim();
            selectedCfi = view.getCFI(item.index, sel.getRangeAt(0).cloneRange());
            break;
          }
        }
        if (selectedText && selectedText.length > 2) {
          currentSelectionText = selectedText;
          selectionCfi = selectedCfi;
          highlightBar.hidden = false;
        } else if (!highlightBar.contains(document.activeElement)) {
          highlightBar.hidden = true;
        }
      } catch (e) {
        // cross-origin or detached iframe safety
      }
    }, 500);

    // Turn pages
    document.getElementById('tap-prev').addEventListener('click', () => {
      if (view.renderer && view.renderer.prev) view.renderer.prev();
    });

    document.getElementById('tap-next').addEventListener('click', () => {
      if (view.renderer && view.renderer.next) view.renderer.next();
    });

    window.addEventListener('keydown', handleNavigationKey);

    // Highlight action
    btnCreateHighlight.addEventListener('click', async () => {
      if (!currentSelectionText || !selectionCfi || btnCreateHighlight.disabled) return;
      btnCreateHighlight.disabled = true;
      try {
        const note = prompt("Заметка к цитате (необязательно):", "");
        if (note === null) return;
        const response = await fetch(`/api/books/${bookId}/highlights`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: currentSelectionText,
            comment: note || '',
            cfi: selectionCfi,
            progress: currentFraction
          })
        });
        highlightBar.hidden = true;
        await checkResponse(response);
        await loadHighlights();
        currentSelectionText = ''; selectionCfi = '';
        for (const item of view.renderer?.getContents?.() || []) item.doc.defaultView.getSelection()?.removeAllRanges();
        notify('Цитата сохранена.');
      } catch (hlErr) {
        notify('Не удалось сохранить цитату: ' + hlErr.message);
      } finally {
        btnCreateHighlight.disabled = false;
      }
    });
  }

  function handleNavigationKey(e) {
    if (['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName) || e.target.isContentEditable) return;
    if (e.key === 'Escape') { closeDrawers(); return; }
    if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
    if (['ArrowRight','PageDown',' '].includes(e.key)) {
      e.preventDefault(); view.next();
    } else if (['ArrowLeft','PageUp'].includes(e.key)) {
      e.preventDefault(); view.prev();
    }
  }

  // Drawers and toolbar buttons
  btnToc.addEventListener('click', () => {
    settingsDrawer.hidden = true;
    tocDrawer.hidden = !tocDrawer.hidden;
  });

  btnSettings.addEventListener('click', () => {
    tocDrawer.hidden = true;
    settingsDrawer.hidden = !settingsDrawer.hidden;
  });

  btnCloseToc.addEventListener('click', () => tocDrawer.hidden = true);
  btnCloseSettings.addEventListener('click', () => settingsDrawer.hidden = true);

  btnFullscreen.addEventListener('click', () => {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  });

  // Theme buttons
  document.querySelectorAll('.theme-btn').forEach(btn => {
    btn.addEventListener('click', () => applyTheme(btn.dataset.theme));
  });

  // Font buttons
  document.querySelectorAll('.font-btn').forEach(btn => {
    btn.addEventListener('click', () => applyFont(btn.dataset.font));
  });

  // Font size buttons
  document.getElementById('btn-font-dec').addEventListener('click', () => applyFontSize(currentFontSize - 10));
  document.getElementById('btn-font-inc').addEventListener('click', () => applyFontSize(currentFontSize + 10));

  // Margin buttons
  document.getElementById('btn-margin-dec').addEventListener('click', () => {
    currentMargin = Math.max(10, currentMargin - 15);
    savePreference('y2k_reader_margin', currentMargin);
    updateRendererStyles();
  });
  document.getElementById('btn-margin-inc').addEventListener('click', () => {
    currentMargin = Math.min(100, currentMargin + 15);
    savePreference('y2k_reader_margin', currentMargin);
    updateRendererStyles();
  });

  // Progress track click
  progressTrack.addEventListener('click', (e) => {
    const rect = progressTrack.getBoundingClientRect();
    const fraction = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    view.goToFraction(fraction);
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
