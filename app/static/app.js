const scrollToTop = document.querySelector('.scroll-to-top');
if (scrollToTop) {
  const updateScrollToTop = () => { scrollToTop.hidden = window.scrollY < 400; };
  window.addEventListener('scroll', updateScrollToTop, { passive: true });
  window.addEventListener('pageshow', updateScrollToTop);
  updateScrollToTop();
  scrollToTop.addEventListener('click', () => {
    window.scrollTo({ top: 0, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  });
}

const notice = (message) => {
  const element = document.querySelector('#notification');
  element.textContent = message;
  element.hidden = false;
  setTimeout(() => { element.hidden = true; }, 3000);
};
document.querySelectorAll('[data-copy]').forEach(button => {
  button.addEventListener('click', async () => {
    const input = button.parentElement.querySelector('input');
    try {
      if (!navigator.clipboard || !window.isSecureContext) throw new Error();
      await navigator.clipboard.writeText(input.value);
      notice('Адрес скопирован');
    } catch {
      input.focus(); input.select();
      notice('Адрес выделен — скопируйте его вручную');
    }
  });
});
document.addEventListener('submit', async event => {
    const form = event.target.closest('form[data-async]');
    if (!form) return;
    event.preventDefault();
    const button = event.submitter || form.querySelector('button[type="submit"], button:not([type])');
    const message = form.querySelector('.form-message');
    if (button?.disabled) return;
    if (form.hasAttribute('data-confirm') && form.dataset.confirmed !== 'yes') {
      form.dataset.confirmed = 'yes';
      button.textContent = 'Подтвердить удаление';
      return;
    }
    const original = button?.innerHTML;
    const data = new FormData(form);
    if (button) { button.disabled = true; button.textContent = 'Выполняется…'; }
    form.setAttribute('aria-busy', 'true');
    if (message) {
      message.className = 'form-message';
      message.textContent = new URL(form.action).pathname === '/integrations/import'
        ? 'Скачиваем книгу из каталога и добавляем в библиотеку. Медленный источник может отвечать несколько минут.'
        : new URL(form.action).pathname === '/highlights/import' ? 'Импортируем цитаты…'
        : form.enctype === 'multipart/form-data' ? 'Загружаем и добавляем книгу. Это может занять до двух минут.' : 'Сохраняем…';
    }
    try {
      const response = await fetch(form.action, { method: 'POST', body: data, headers: { Accept: 'application/json' } });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        const detail = typeof result.detail === 'string' ? result.detail : Array.isArray(result.detail) ? result.detail.map(item => item.msg).join('. ') : `Не удалось выполнить действие (HTTP ${response.status}).`;
        throw new Error(detail);
      }
      const destination = new URL(response.redirected ? response.url : window.location.href);
      if (form.dataset.returnAnchor) destination.hash = form.dataset.returnAnchor;
      window.location.assign(destination.href);
    } catch (error) {
      if (message) { message.className = 'form-message error'; message.textContent = error.message || 'Нет соединения. Попробуйте ещё раз.'; }
    } finally {
      form.removeAttribute('aria-busy');
      if (button) { button.disabled = false; button.innerHTML = original; }
    }
});
document.querySelectorAll('.drop-zone').forEach(zone => {
  const input = zone.querySelector('input[type=file]');
  const label = zone.querySelector('[data-file-label]');
  const update = () => {
    const file = input.files[0];
    input.setCustomValidity(file && file.size > 500 * 1024 * 1024 ? 'Максимальный размер файла — 500 МБ' : '');
    label.textContent = file ? file.name : 'Выберите или перетащите файл';
    if (file && !input.validity.valid) input.reportValidity();
  };
  input.addEventListener('change', update);
  zone.addEventListener('dragover', event => { event.preventDefault(); zone.classList.add('dragging'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragging'));
  zone.addEventListener('drop', event => {
    event.preventDefault(); zone.classList.remove('dragging');
    if (event.dataTransfer.files.length) {
      const transfer = new DataTransfer(); transfer.items.add(event.dataTransfer.files[0]);
      input.files = transfer.files; update();
    }
  });
});
document.querySelectorAll('a[href="#add-book"]').forEach(link => {
  link.addEventListener('click', () => { const panel = document.querySelector('#add-book'); if (panel) panel.open = true; });
});

const menuToggle = document.querySelector('[data-menu]');
if (menuToggle) {
  const closeMenu = () => {
    document.body.classList.remove('nav-open');
    menuToggle.setAttribute('aria-expanded', 'false');
  };
  menuToggle.addEventListener('click', () => {
    const open = document.body.classList.toggle('nav-open');
    menuToggle.setAttribute('aria-expanded', String(open));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && document.body.classList.contains('nav-open')) {
      closeMenu();
      menuToggle.focus();
    }
  });
}
document.querySelectorAll('[data-export]').forEach(link => {
  link.addEventListener('click', async event => {
    event.preventDefault();
    if (link.dataset.busy === 'yes') return;
    link.dataset.busy = 'yes';
    link.setAttribute('aria-disabled', 'true');
    const label = link.textContent;
    link.textContent = 'Готовим EPUB…';
    const message = document.createElement('p');
    message.className = 'form-message';
    message.setAttribute('role', 'status');
    link.closest('.book-actions').after(message);
    message.textContent = 'Подготавливаем EPUB локально. Это может занять до трёх минут.';
    try {
      const response = await fetch(link.href);
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(typeof result.detail === 'string' ? result.detail : 'Не удалось подготовить EPUB');
      }
      const blob = await response.blob();
      const disposition = response.headers.get('Content-Disposition') || '';
      const encoded = disposition.match(/filename\*=utf-8''([^;]+)/i);
      const simple = disposition.match(/filename="([^"]+)"/i);
      let filename = simple ? simple[1] : 'book.epub';
      if (encoded) { try { filename = decodeURIComponent(encoded[1]); } catch {} }
      const url = URL.createObjectURL(blob);
      const download = document.createElement('a');
      download.href = url; download.download = filename;
      document.body.append(download); download.click(); download.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      message.textContent = 'EPUB готов. Скачивание началось.';
    } catch (error) {
      message.className = 'form-message error';
      message.textContent = error.message || 'Не удалось скачать EPUB';
    } finally {
      link.textContent = label;
      link.removeAttribute('aria-disabled');
      delete link.dataset.busy;
    }
  });
});

