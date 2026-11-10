(() => {
  let state = {recording: false};
  const seen = new Set();
  const secret = /password|passwd|secret|token|otp|one.?time|credit.?card|card.?number|cvv|cvc/i;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const sensitive = el => {
    if (!(el instanceof Element)) return true;
    const meta = ['type', 'name', 'id', 'autocomplete', 'aria-label'].map(a => el.getAttribute(a) || '').join(' ');
    return secret.test(meta) || /cc-|current-password|new-password/.test(meta) || el.closest('[data-wl-private]');
  };
  function selector(el) {
    for (const attribute of ['data-testid', 'id', 'name', 'aria-label']) {
      const value = el.getAttribute(attribute);
      if (!value) continue;
      const candidate = `[${attribute}="${CSS.escape(value)}"]`;
      if (document.querySelectorAll(candidate).length === 1) return candidate;
    }
    const parts = [];
    let current = el;
    while (current && current !== document.documentElement) {
      const siblings = [...current.parentElement?.children || []].filter(x => x.tagName === current.tagName);
      parts.unshift(current.tagName.toLowerCase() + (siblings.length > 1 ? `:nth-of-type(${siblings.indexOf(current) + 1})` : ''));
      current = current.parentElement;
    }
    return parts.join(' > ');
  }
  function label(el) {
    return (el.getAttribute('aria-label') || el.labels?.[0]?.innerText || el.getAttribute('placeholder') ||
      el.getAttribute('name') || (el.matches('input,textarea,[contenteditable]') ? 'Field' : el.innerText) || el.tagName).trim().slice(0, 180);
  }
  function send(kind, el, value = '') {
    if (!state.recording || state.running || sensitive(el)) return;
    if (el.getRootNode() !== document) return; // Closed/open shadow widgets need an explicit adapter.
    chrome.runtime.sendMessage({type: 'event', step: {kind, label: label(el), selector: selector(el), value}}).catch(() => {});
  }
  document.addEventListener('click', event => {
    if (!event.isTrusted) return;
    const el = event.target.closest('button,a,input,[role="button"],[role="menuitem"],[role="tab"],[role="checkbox"]') || event.target;
    if (el.matches('input,textarea,select,[contenteditable]') && !el.matches('input[type=button],input[type=submit]')) return;
    send('click', el);
  }, true);
  document.addEventListener('change', event => {
    if (!event.isTrusted) return;
    const el = event.target;
    if (el.matches('input[type=checkbox],input[type=radio]')) send('check', el, String(el.checked));
    else if (el.matches('select')) send('select', el);
    else if (el.matches('input:not([type=file]),textarea,[contenteditable]')) send('fill', el);
  }, true);
  document.addEventListener('focusout', event => {
    if (event.target.matches('[contenteditable=true]')) send('fill', event.target);
  }, true);
  document.addEventListener('keydown', event => {
    if (event.isTrusted && ['Enter', 'Escape'].includes(event.key)) {
      if (event.target.matches('input,textarea,[contenteditable]')) send('fill', event.target);
      send('press', event.target, event.key);
    }
  }, true);

  async function refresh() {
    try { state = await chrome.runtime.sendMessage({type: 'state'}); }
    catch { state = {recording: false}; }
  }
  setInterval(refresh, 1000);
  refresh();

  async function execute(command) {
    if (seen.has(command.id)) throw new Error('Duplicate command blocked');
    seen.add(command.id);
    const step = command.step;
    if (new URL(location.href).origin !== new URL(step.frame || step.url).origin) throw new Error('Origin changed');
    let el;
    for (let i = 0; i < 40; i++) {
      const matches = document.querySelectorAll(step.selector);
      if (matches.length > 1) throw new Error('More than one matching control; demonstrate it again');
      el = matches[0];
      if (el && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden') break;
      el = null;
      await sleep(200);
    }
    if (!el) throw new Error('Control not found or visible; demonstrate it again');
    if (sensitive(el)) throw new Error('Protected field: complete this step manually');
    if (step.label && label(el) !== step.label) throw new Error('Control label changed; review this step before replay');
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') throw new Error('Control is disabled');
    el.scrollIntoView({block: 'center'});
    el.focus();
    if (step.kind === 'click') {
      el.click();
    } else if (step.kind === 'fill') {
      if (el.isContentEditable) el.textContent = step.value;
      else {
        const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
        if (!setter) throw new Error('This control needs a custom adapter');
        setter.call(el, step.value);
      }
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
    } else if (step.kind === 'select') {
      if (!(el instanceof HTMLSelectElement) || ![...el.options].some(o => o.value === step.value)) throw new Error('Select option not found');
      el.value = step.value;
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
    } else if (step.kind === 'check') {
      if (!(el instanceof HTMLInputElement) || !['checkbox', 'radio'].includes(el.type)) throw new Error('Not a checkbox or radio');
      const checked = step.value === 'true';
      if (el.checked !== checked) el.click();
      if (el.checked !== checked) throw new Error('Checkbox did not reach the requested state');
    } else if (step.kind === 'press') {
      // Synthetic keyboard events cannot reliably trigger browser defaults.
      // Never report successful replay of an Enter submission that did not occur.
      throw new Error('Keyboard step needs manual completion. Replace it with a button click or a manual step.');
    } else throw new Error('Unsupported browser action');
  }
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (message.type !== 'execute') return;
    execute(message.command).then(() => respond({ok: true})).catch(error => respond({ok: false, error: error.message}));
    return true;
  });
})();
