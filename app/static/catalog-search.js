/* ES5 + XMLHttpRequest also work in older e-ink browsers. */
(function () {
  var target = document.getElementById('catalog-results');
  var path = window.location.pathname.replace(/\/$/, '');
  if (!target || /\/results$/.test(path) || !/[?&](q|author)=/.test(window.location.search)) return;
  target.setAttribute('aria-busy', 'true');
  target.innerHTML = '<div class="terminal-loader" role="status"><p class="terminal-command"><span aria-hidden="true">$ </span>поиск в каталоге<span class="terminal-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span><span class="terminal-cursor" aria-hidden="true">▌</span></p><p>Ожидаем ответ источника. Можно изменить запрос.</p></div>';
  // Let the browser paint the page and loader before starting network work.
  window.setTimeout(function () {
    var request = new XMLHttpRequest();
    request.open('GET', path + '/results' + window.location.search, true);
    request.timeout = 130000;
    function fail(message) {
      target.removeAttribute('aria-busy');
      target.textContent = message;
    }
    request.onload = function () {
      if (request.status !== 200) return fail('Не удалось выполнить поиск (HTTP ' + request.status + '). Повторите поиск.');
      var parsed = document.createElement('div');
      parsed.innerHTML = request.responseText;
      var results = parsed.querySelector('#catalog-results');
      if (!results) return fail('Не удалось загрузить результаты. Обновите страницу.');
      target.innerHTML = results.innerHTML;
      target.removeAttribute('aria-busy');
    };
    request.onerror = function () { fail('Нет соединения. Повторите поиск.'); };
    request.ontimeout = function () { fail('Источник слишком долго отвечает. Повторите поиск или выберите другой каталог.'); };
    request.send();
  }, 0);
}());
