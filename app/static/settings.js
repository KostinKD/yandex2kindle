(() => {
  const provider = document.getElementById('smtp-provider');
  if (!provider) return;
  const section = document.getElementById('smtp');
  const host = section.querySelector('[name=SMTP_HOST]');
  const port = section.querySelector('[name=SMTP_PORT]');
  const ssl = section.querySelector('[name=SMTP_SSL]');
  const presets = {yandex: 'smtp.yandex.ru', mail: 'smtp.mail.ru'};
  provider.value = Object.keys(presets).find(key => presets[key] === host.value) || 'other';
  provider.addEventListener('change', () => {
    if (!presets[provider.value]) return;
    host.value = presets[provider.value]; port.value = '465'; ssl.value = 'true';
  });
})();
