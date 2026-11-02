(() => {
  const field = document.getElementById('bootstrap-token');
  if (!field) return;
  field.value = location.hash.slice(1);
  history.replaceState(null, '', location.pathname);
  const help = document.getElementById('setup-key-help');
  if (help) help.hidden = !!field.value;
  const form = field.form;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button[type=submit]');
    const message = form.querySelector('.form-message');
    if (!field.value) { help.hidden = false; help.scrollIntoView({block:'center'}); return; }
    button.disabled = true;
    message.textContent = 'Создаём вашу библиотеку…';
    try {
      const response = await fetch(form.action, {method:'POST',body:new FormData(form)});
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Проверьте логин и пароль.');
      }
      location.assign(response.redirected ? response.url : '/settings');
    } catch (error) { message.textContent = error.message || 'Нет соединения. Попробуйте ещё раз.'; }
    finally { button.disabled = false; }
  });
})();
