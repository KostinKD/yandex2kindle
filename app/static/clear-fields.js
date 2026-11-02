/* ES5 for older Kindle browsers. No network requests are needed to clear text. */
(function () {
  var fields = document.querySelectorAll('input, textarea');
  for (var i = 0; i < fields.length; i++) {
    var field = fields[i];
    var type = (field.type || 'text').toLowerCase();
    if (field.readOnly || field.disabled || !/^(text|search|password|email|url|tel|number|textarea)$/.test(type)) continue;
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'clear-field';
    button.appendChild(document.createTextNode('Очистить'));
    button.setAttribute('aria-label', 'Очистить поле ' + (field.getAttribute('aria-label') || field.name || 'ввода'));
    button.onclick = (function (input) {
      return function () {
        input.value = '';
        var event = document.createEvent('HTMLEvents');
        event.initEvent('input', true, false);
        input.dispatchEvent(event);
        input.focus();
      };
    })(field);
    if (document.querySelector('.app-shell')) {
      var wrapper = document.createElement('span');
      wrapper.className = 'field-control';
      field.parentNode.insertBefore(wrapper, field);
      wrapper.appendChild(field);
      wrapper.appendChild(button);
    } else {
      field.parentNode.insertBefore(button, field.nextSibling);
    }
  }
})();
