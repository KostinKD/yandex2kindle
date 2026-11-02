'use strict';
(() => {
  const STORAGE = 'y2k-pages-demo-v1';
  const titles = [
    ['Письма с маяка', 'Проза', 'Пока свет горит, у каждого пути есть продолжение.'],
    ['Город после дождя', 'Проза', 'Знакомая улица меняется, когда перестаёшь торопиться.'],
    ['Сад на подоконнике', 'Эссе', 'Забота измеряется вниманием, а не размером сада.'],
    ['Карта тихих мест', 'Путешествия', 'Иногда лучший маршрут — тот, на котором есть время остановиться.'],
    ['Мастерская переплётчика', 'Эссе', 'Книга хранит не только слова, но и следы встреч с ними.'],
    ['Зимний трамвай', 'Проза', 'Чужое окно на мгновение становится частью твоей истории.'],
    ['Читать медленно', 'О чтении', 'Хорошая мысль не требует немедленно перевернуть страницу.'],
    ['На краю сентября', 'Проза', 'Осень начинается не с даты, а с первого замеченного листа.'],
    ['Поезд к морю', 'Путешествия', 'Дорога возвращает нам привычку смотреть в окно.'],
    ['Дом с круглым окном', 'Проза', 'У каждого дома есть история, которую слышно только в тишине.'],
    ['Записки о бумаге', 'О чтении', 'Оставленная на полях заметка — письмо будущему себе.'],
    ['Утренний маршрут', 'Эссе', 'Для маленького открытия достаточно выйти на пять минут раньше.'],
  ];
  const books = titles.map(([title, genre, line], i) => ({
    id: i + 1, title, genre, line, author: 'Демо-редакция', format: 'EPUB',
    chapters: [
      ['Первая страница', [
        `На столе лежала записная книжка с надписью «${title}». Её начали не ради большого замысла. Просто однажды захотелось сохранить несколько деталей, которые обычно исчезают к вечеру: рисунок света на стене, скрип двери, случайный разговор.`,
        'Утро было обычным. За окном кто-то переставлял велосипед, внизу открывалась пекарня. Казалось, день уже написан заранее. Но стоило задержаться у окна, как в привычной картине нашлось что-то новое.',
        line,
        'Эту фразу записали первой. За ней осталось свободное место — для всего, что ещё предстояло заметить. Книжка закрылась не сразу: приятно было знать, что никуда не нужно спешить.',
      ]],
      ['Между строк', [
        'К середине дня запись успела обрасти подробностями. Появились имена, направление ветра и маленький рисунок, который невозможно было принять за что-либо определённое. И всё-таки именно он точнее других заметок передавал настроение утра.',
        'Читая написанное, замечаешь расстояние между событием и воспоминанием. Одни подробности становятся ярче, другие исчезают. На полях хочется оставить вопрос, а не готовый ответ.',
        'Пауза тоже часть чтения. Иногда нужно закрыть книгу, пройти несколько шагов и позволить словам найти собственное место среди повседневных дел.',
        'На следующей странице пока ничего нет. Это не недостаток истории, а приглашение продолжить её в своём темпе.',
      ]],
      ['Возвращение', [
        'Вечером всё снова оказалось на своих местах: велосипед у стены, чашка на столе, книжка рядом с лампой. День почти закончился, но теперь в нём было несколько точек, к которым можно вернуться.',
        'Последнюю заметку сделали короткой. Не каждую мысль нужно объяснять до конца. Иногда достаточно сохранить её рядом с другими и перечитать через неделю.',
        'Так постепенно складывается личная библиотека: из книг, пометок и маленьких открытий. В ней нет правильной скорости и обязательного маршрута. Есть только следующая страница.',
      ]],
    ],
  }));
  const initial = () => ({ progress: {1: 1, 2: 2, 7: 3}, shelves: {1: 'Любимое', 3: 'Любимое', 4: 'На выходные', 9: 'На выходные'}, quotes: [
    {id: 'seed-1', book: 1, text: books[0].line}, {id: 'seed-7', book: 7, text: books[6].line},
  ], view: 'grid', font: 19, theme: 'system'});
  let state = initial();
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE));
    if (saved && typeof saved === 'object') {
      if (saved.progress && typeof saved.progress === 'object') for (const b of books) {
        const p = saved.progress[b.id]; if (Number.isInteger(p) && p >= 0 && p <= 3) state.progress[b.id] = p;
      }
      if (saved.shelves && typeof saved.shelves === 'object') for (const b of books) {
        if (['Любимое', 'На выходные', ''].includes(saved.shelves[b.id])) state.shelves[b.id] = saved.shelves[b.id];
      }
      if (Array.isArray(saved.quotes)) state.quotes = saved.quotes.filter(q => q && typeof q.id === 'string' && books.some(b => b.id === q.book) && typeof q.text === 'string' && q.text.length <= 2000).slice(0, 200);
      if (['grid', 'list'].includes(saved.view)) state.view = saved.view;
      if (Number.isInteger(saved.font) && saved.font >= 16 && saved.font <= 26) state.font = saved.font;
      if (['system', 'light', 'dark'].includes(saved.theme)) state.theme = saved.theme;
    }
  } catch { /* Demo remains usable without storage. */ }
  let filters = {q: '', status: 'all', shelf: '', sort: 'newest'};
  let toastTimer;
  let quoteSelection = '';
  const main = document.getElementById('main');
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const persist = () => { try { localStorage.setItem(STORAGE, JSON.stringify(state)); } catch { notify('Хранилище браузера недоступно. Изменения сохранятся до закрытия страницы.'); } };
  function notify(text) {
    const box = document.getElementById('notification'); box.textContent = text; box.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { box.hidden = true; }, 4000);
  }
  const progress = b => Math.round((state.progress[b.id] || 0) / 3 * 100);
  const cover = b => `<div class="book-cover cover-${(b.id - 1) % 4}" aria-hidden="true"><span class="cover-line">Демонстрационная коллекция · ${String(b.id).padStart(2,'0')}</span><span class="cover-title">${escape(b.title)}</span><span class="cover-author">Оригинальный демо-текст</span></div>`;
  const heading = (title, sub, action = '') => `<div class="workspace-heading"><div><span class="eyebrow">Личная библиотека / демо</span><h1>${title}</h1><p>${sub}</p></div>${action}</div>`;
  function cards(items) {
    return items.length ? `<div class="catalog-grid demo-covers ${state.view === 'list' ? 'demo-list' : ''}">${items.map(b => `<article class="catalog-book"><a class="catalog-cover" href="#book/${b.id}" aria-label="Открыть книгу «${escape(b.title)}»">${cover(b)}</a><div class="catalog-book-info"><div class="catalog-book-meta"><span class="format">EPUB</span><span>${escape(b.genre)}</span></div><h2><a href="#book/${b.id}">${escape(b.title)}</a></h2><p class="book-author">${b.author}</p><div class="reading-meter"><progress max="100" value="${progress(b)}" aria-label="Прочитано ${progress(b)}%"></progress><span>${progress(b)}%</span></div><a class="catalog-read" href="#read/${b.id}/${Math.min(state.progress[b.id] || 0, 2)}">${progress(b) ? 'Продолжить чтение' : 'Читать'} <span aria-hidden="true">→</span></a></div></article>`).join('')}</div>` : '<p class="demo-empty">Ничего не нашлось. Попробуйте изменить запрос или выбрать другую полку.</p>';
  }
  function filtered() {
    let result = books.filter(b => `${b.title} ${b.author} ${b.genre}`.toLocaleLowerCase('ru').includes(filters.q.toLocaleLowerCase('ru')) && (!filters.shelf || state.shelves[b.id] === filters.shelf) && (filters.status === 'all' || (filters.status === 'reading' && progress(b) > 0 && progress(b) < 100) || (filters.status === 'finished' && progress(b) === 100) || (filters.status === 'unread' && progress(b) === 0)));
    if (filters.sort === 'title') result.sort((a,b) => a.title.localeCompare(b.title,'ru'));
    if (filters.sort === 'newest') result.reverse();
    return result;
  }
  function updateResults() {
    const items = filtered(); document.getElementById('book-count').textContent = `Книг: ${items.length}`;
    document.getElementById('catalog-results').innerHTML = cards(items);
  }
  function library() {
    main.innerHTML = heading('Библиотека', '12 коротких историй, чтобы попробовать приложение.', '<a class="button secondary" href="#install">Добавить свои книги ↗</a>') + `
      <div class="shelf-strip"><div class="demo-shelves" role="group" aria-label="Полки">${['','Любимое','На выходные'].map(s => `<button type="button" data-shelf="${s}" aria-pressed="${filters.shelf === s}">${s || 'Все книги'}</button>`).join('')}</div></div>
      <form class="catalog-toolbar demo-toolbar" id="filters"><label class="catalog-search-field">Поиск<input type="search" id="search" placeholder="Название, жанр или автор" value="${escape(filters.q)}" maxlength="200"></label><label>Статус<select id="status">${[['all','Все книги'],['reading','Читаю'],['finished','Прочитано'],['unread','Не прочитано']].map(([v,t]) => `<option value="${v}" ${filters.status === v ? 'selected' : ''}>${t}</option>`).join('')}</select></label><label>Сортировка<select id="sort"><option value="newest">Сначала новые</option><option value="title" ${filters.sort === 'title' ? 'selected' : ''}>По названию</option></select></label></form>
      <div class="catalog-results-bar"><span id="book-count" aria-live="polite"></span><div class="view-switch" role="group" aria-label="Вид каталога"><button data-view="grid" aria-pressed="${state.view === 'grid'}">Сетка</button><button data-view="list" aria-pressed="${state.view === 'list'}">Список</button></div></div><div id="catalog-results"></div>`;
    updateResults();
    document.getElementById('filters').addEventListener('submit', e => e.preventDefault());
    document.getElementById('search').addEventListener('input', e => { filters.q = e.target.value; updateResults(); });
    for (const key of ['status','sort']) document.getElementById(key).addEventListener('change', e => { filters[key] = e.target.value; updateResults(); });
  }
  function bookPage(b) {
    main.innerHTML = `<p class="demo-note"><a href="#library">← Библиотека</a> · Оригинальный короткий текст для демонстрации ридера.</p><div class="book-detail demo-detail"><div class="demo-detail-cover demo-covers">${cover(b)}</div><div><span class="eyebrow">${escape(b.genre)} / EPUB</span><h1>${escape(b.title)}</h1><p class="book-detail-author">${b.author}</p><p class="detail-description">${escape(b.line)}<br>Три небольшие главы. Попробуйте прочитать, сохранить цитату и добавить книгу на полку.</p><div class="detail-actions"><a class="button" href="#read/${b.id}/${Math.min(state.progress[b.id] || 0, 2)}">${progress(b) ? 'Продолжить чтение' : 'Читать книгу'}</a><button class="button secondary" data-finish="${b.id}">${progress(b) === 100 ? 'Отметить непрочитанной' : 'Отметить прочитанной'}</button></div><div class="reading-meter"><progress max="100" value="${progress(b)}" aria-label="Прогресс чтения"></progress><span>${progress(b)}%</span></div><div class="demo-shelf-form"><label>Полка<select id="book-shelf"><option value="">Без полки</option>${['Любимое','На выходные'].map(s => `<option ${state.shelves[b.id] === s ? 'selected' : ''}>${s}</option>`).join('')}</select></label></div><a class="text-link" href="#highlights">Все цитаты →</a></div></div>`;
    document.getElementById('book-shelf').addEventListener('change', e => {state.shelves[b.id] = e.target.value; persist(); notify('Полка сохранена в этом браузере');});
  }
  function reader(b, page) {
    const [title, paragraphs] = b.chapters[page];
    quoteSelection = '';
    main.innerHTML = `<section class="demo-reader"><div class="reader-top"><a href="#book/${b.id}">← ${escape(b.title)}</a><span>Глава ${page + 1} из 3</span></div><div class="reader-tools"><label>Размер текста<input id="font-size" type="range" min="16" max="26" value="${state.font}"></label><button class="button secondary small" id="save-quote">Сохранить цитату</button><span class="muted" id="selection-hint">Выделите текст или сохраните ключевую фразу</span></div><article class="reader-paper" id="reader-text" style="font-size:${state.font}px"><span class="eyebrow">${escape(b.title)} / демонстрационный текст</span><h1>${escape(title)}</h1>${paragraphs.map(p => `<p>${escape(p)}</p>`).join('')}</article><div class="reader-bottom">${page > 0 ? `<a class="button secondary" href="#read/${b.id}/${page-1}">← Назад</a>` : '<span>Начало</span>'}<span>${Math.round((page + 1)/3*100)}%</span>${page < 2 ? `<a class="button" href="#read/${b.id}/${page+1}">Далее →</a>` : `<button class="button" data-complete="${b.id}">Завершить чтение</button>`}</div></section>`;
    if ((state.progress[b.id] || 0) < page) { state.progress[b.id] = page; persist(); }
    document.getElementById('font-size').addEventListener('input', e => { state.font = Number(e.target.value); document.getElementById('reader-text').style.fontSize = `${state.font}px`; persist(); });
    document.getElementById('save-quote').addEventListener('click', () => {
      const text = quoteSelection || b.line;
      if (state.quotes.some(q => q.book === b.id && q.text === text)) return notify('Эта цитата уже сохранена');
      state.quotes.unshift({id: `q-${Date.now()}-${Math.random().toString(36).slice(2)}`, book:b.id, text});
      state.quotes = state.quotes.slice(0,200); persist(); notify('Цитата сохранена. Найдите её в разделе «Цитаты».');
    });
  }
  function highlights() {
    main.innerHTML = heading('Цитаты', 'Мысли, к которым хочется вернуться.', '<button class="button secondary" id="export-quotes">Экспорт Markdown</button>') + `<div class="demo-quotes">${state.quotes.length ? state.quotes.map(q => {const b = books.find(b => b.id === q.book);return `<article class="demo-quote"><blockquote>${escape(q.text)}</blockquote><footer><a href="#book/${b.id}">${escape(b.title)} · ${b.author}</a><button data-delete-quote="${escape(q.id)}" aria-label="Удалить цитату из книги «${escape(b.title)}»">Удалить</button></footer></article>`;}).join('') : '<p class="demo-empty">Цитат пока нет. Откройте книгу и сохраните понравившуюся фразу.</p>'}</div>`;
    document.getElementById('export-quotes').addEventListener('click', () => {
      const text = '# Цитаты · Yandex2Kindle Demo\n\n' + state.quotes.map(q => `> ${q.text.replace(/\n/g,'\n> ')}\n\n— ${books.find(b => b.id === q.book).title}\n`).join('\n');
      const url = URL.createObjectURL(new Blob([text],{type:'text/markdown;charset=utf-8'})); const a = document.createElement('a'); a.href=url; a.download='yandex2kindle-demo-quotes.md'; a.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
    });
  }
  function dashboard() {
    const reading = books.filter(b => progress(b) > 0 && progress(b) < 100);
    main.innerHTML = heading('Моё чтение', 'Вернитесь к истории, на которой остановились.') + `<div class="demo-stats"><div><strong>${books.length}</strong><span>книг в библиотеке</span></div><div><strong>${reading.length}</strong><span>читаете сейчас</span></div><div><strong>${books.filter(b => progress(b) === 100).length}</strong><span>прочитано</span></div><div><strong>${state.quotes.length}</strong><span>сохранённых цитат</span></div></div><div class="section-heading"><h2>Продолжить чтение</h2><a href="#library">Вся библиотека →</a></div>${cards(reading)}<section class="workspace-section"><h2>Попробуйте за минуту</h2><p>Откройте книгу, перейдите к следующей главе и сохраните фразу. Прогресс появится здесь, а цитата — в соседнем разделе.</p></section>`;
  }
  function wishlist() {
    main.innerHTML = heading('Список чтения', 'Пример списка книг, которые хочется прочитать.') + '<p class="demo-note">В полной версии сюда можно импортировать список из Яндекс Книг. В демо подключения не выполняются.</p>' + cards(books.filter(b => progress(b) === 0).slice(0,4));
  }
  function devices() {
    main.innerHTML = heading('Книги на вашем устройстве', 'Выберите удобный способ читать свою библиотеку.') + `<div class="demo-install"><section class="workspace-section"><h2>Kindle · отправка по почте</h2><p>В установленном приложении укажите свой адрес Kindle и настройте почту отправителя. Отправка доступна со страницы книги.</p></section><section class="workspace-section"><h2>KOReader · каталог и синхронизация</h2><p>Подключите OPDS-каталог своего сервера, чтобы скачивать книги. Встроенная синхронизация KOReader сохраняет прогресс; плагин приложения отправляет цитаты на сайт.</p></section><section class="workspace-section"><h2>Обычный Kindle · цитаты</h2><p>Импортируйте файл <code>My Clippings.txt</code> со своего устройства. Постоянного автоматического обмена со штатной читалкой Kindle нет.</p></section><p class="demo-note">Это обзор подключения. У статического демо нет OPDS-сервера и отправки писем. Адреса и ключи здесь вводить не нужно.</p><a class="button" href="#install">Установить приложение →</a></div>`;
  }
  function install() {
    main.innerHTML = heading('Своя библиотека. У себя.', 'Демо закончилось — ваша коллекция только начинается.') + `<div class="demo-install"><p>Yandex2Kindle работает на вашем компьютере или сервере. Добавляйте свои книги, подключайте Kindle и храните цитаты рядом с библиотекой.</p><a class="button" href="https://github.com/KostinKD/yandex2kindle/releases/latest">Скачать последнюю версию ↗</a><ol><li>Установите и запустите <a class="text-link" href="https://www.docker.com/products/docker-desktop/" target="_blank" rel="noopener noreferrer">Docker Desktop ↗</a>.</li><li>Распакуйте архив. На macOS откройте <code>Запустить.command</code>, на Windows — <code>Запустить.bat</code>. На Linux с Docker Compose выполните <code>sh scripts/launch.sh</code>.</li><li>В открывшемся браузере создайте аккаунт и добавьте первую книгу. Подключения настраиваются на сайте; для чтения файлов ключи не нужны.</li></ol><p class="demo-note">Демо работает только в браузере. Здесь нет загрузки файлов, реальной отправки на Kindle и внешних подключений. Полная версия использует сервер; доступность внешних сервисов зависит от вашей сети.</p><p>Собственный код — Apache-2.0. <a class="text-link" href="https://github.com/KostinKD/yandex2kindle">Исходный код и инструкции ↗</a></p></div>`;
  }
  function route(focus = false) {
    const [page, rawId, rawChapter] = location.hash.slice(1).split('/');
    const b = books.find(b => b.id === Number(rawId));
    const pages = {library, dashboard, wishlist, highlights, devices, install};
    const knownPage = Object.prototype.hasOwnProperty.call(pages, page);
    if ((page === 'book' || page === 'read') && b) {
      if (page === 'book') bookPage(b); else reader(b, Math.min(2, Math.max(0, Number.parseInt(rawChapter,10) || 0)));
    } else if (knownPage) pages[page](); else library();
    const active = ['book','read'].includes(page) ? 'library' : (knownPage ? page : 'library');
    document.querySelectorAll('[data-page]').forEach(a => { if (a.dataset.page === active) a.setAttribute('aria-current','page'); else a.removeAttribute('aria-current'); });
    document.title = `${b && ['book','read'].includes(page) ? b.title : ({library:'Библиотека',dashboard:'Моё чтение',wishlist:'Список чтения',highlights:'Цитаты',devices:'Устройства',install:'Установить'}[active])} · Демо Yandex2Kindle`;
    document.body.classList.remove('nav-open'); document.getElementById('menu-toggle').setAttribute('aria-expanded','false');
    if (focus) { main.focus({preventScroll:true}); window.scrollTo(0,0); }
  }
  document.addEventListener('selectionchange', () => {
    const selection = window.getSelection(); const article = document.getElementById('reader-text');
    if (article && selection && article.contains(selection.anchorNode) && article.contains(selection.focusNode) && selection.toString().trim()) {
      quoteSelection = selection.toString().trim().slice(0,2000);
      document.getElementById('selection-hint').textContent = `Выделено символов: ${quoteSelection.length}`;
    }
  });
  document.addEventListener('click', e => {
    if (e.target.closest('a.skip')) {
      e.preventDefault(); main.focus({preventScroll:true}); main.scrollIntoView(); return;
    }
    const target = e.target.closest('button'); if (!target) return;
    if (target.hasAttribute('data-shelf')) { filters.shelf = target.dataset.shelf; library(); }
    if (target.dataset.view) { state.view = target.dataset.view; persist(); library(); }
    if (target.dataset.finish) { const id = target.dataset.finish; state.progress[id] = state.progress[id] === 3 ? 0 : 3; persist(); route(); }
    if (target.dataset.complete) { state.progress[target.dataset.complete] = 3; persist(); location.hash = 'dashboard'; notify('Книга прочитана. Прогресс сохранён.'); }
    if (target.dataset.deleteQuote) { state.quotes = state.quotes.filter(q => q.id !== target.dataset.deleteQuote); persist(); highlights(); notify('Цитата удалена'); }
  });
  document.getElementById('menu-toggle').addEventListener('click', e => { const open = document.body.classList.toggle('nav-open'); e.currentTarget.setAttribute('aria-expanded',String(open)); });
  document.getElementById('reset-demo').addEventListener('click', () => {
    if (!window.confirm('Сбросить прогресс, полки и цитаты демо в этом браузере?')) return;
    state = initial(); filters = {q:'',status:'all',shelf:'',sort:'newest'}; persist(); applyTheme(); route(); notify('Демонстрационная библиотека восстановлена');
  });
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  function applyTheme() { document.documentElement.dataset.theme = state.theme === 'system' ? (media.matches ? 'dark' : 'light') : state.theme; document.documentElement.style.colorScheme = document.documentElement.dataset.theme; document.getElementById('theme-select').value = state.theme; }
  document.getElementById('theme-select').addEventListener('change', e => { state.theme = e.target.value; persist(); applyTheme(); });
  if (media.addEventListener) media.addEventListener('change',applyTheme);
  window.addEventListener('hashchange', () => route(true));
  applyTheme(); route();
})();
