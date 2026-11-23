const toggle = document.getElementById('theme-toggle');
const sidebar = document.getElementById('app-sidebar');
const menuToggle = document.getElementById('mobile-menu-toggle');
const privacyToggle = document.getElementById('privacy-toggle');
const collapseToggle = document.getElementById('sidebar-collapse');

function updateToggle() {
  if (!toggle) return;
  const dark = document.documentElement.dataset.bsTheme === 'dark';
  toggle.innerHTML = `<i class="bi bi-${dark ? 'sun' : 'moon-stars'}" aria-hidden="true"></i><span>${dark ? 'Light mode' : 'Dark mode'}</span>`;
  toggle.setAttribute('aria-pressed', String(dark));
}

if (toggle) {
  updateToggle();
  toggle.addEventListener('click', () => {
    const theme = document.documentElement.dataset.bsTheme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.bsTheme = theme;
    try { localStorage.setItem('expense-theme', theme); } catch (_) {}
    updateToggle();
    window.dispatchEvent(new Event('themechange'));
  });
}

function updatePrivacyToggle() {
  if (!privacyToggle) return;
  const enabled = document.body.classList.contains('privacy-mode');
  privacyToggle.setAttribute('aria-pressed', String(enabled));
  privacyToggle.innerHTML = `<i class="bi bi-${enabled ? 'eye' : 'eye-slash'}" aria-hidden="true"></i><span>${enabled ? 'Show amounts' : 'Privacy mode'}</span>`;
}

try {
  if (localStorage.getItem('expense-privacy') === 'on') document.body.classList.add('privacy-mode');
} catch (_) {}
updatePrivacyToggle();
if (privacyToggle) {
  privacyToggle.addEventListener('click', () => {
    const enabled = document.body.classList.toggle('privacy-mode');
    try { localStorage.setItem('expense-privacy', enabled ? 'on' : 'off'); } catch (_) {}
    updatePrivacyToggle();
  });
}

function updateCollapseToggle() {
  if (!collapseToggle) return;
  const collapsed = document.body.classList.contains('sidebar-collapsed');
  collapseToggle.setAttribute('aria-pressed', String(collapsed));
  collapseToggle.setAttribute('aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar');
  collapseToggle.title = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
  collapseToggle.innerHTML = `<i class="bi bi-${collapsed ? 'layout-sidebar' : 'layout-sidebar-inset'}" aria-hidden="true"></i><span>${collapsed ? 'Expand sidebar' : 'Collapse sidebar'}</span>`;
}
try {
  if (localStorage.getItem('expense-sidebar') === 'collapsed') document.body.classList.add('sidebar-collapsed');
} catch (_) {}
updateCollapseToggle();
if (collapseToggle) {
  collapseToggle.addEventListener('click', () => {
    const collapsed = document.body.classList.toggle('sidebar-collapsed');
    try { localStorage.setItem('expense-sidebar', collapsed ? 'collapsed' : 'expanded'); } catch (_) {}
    updateCollapseToggle();
  });
}

function closeMobileNav() {
  document.body.classList.remove('nav-open');
  if (menuToggle) {
    menuToggle.setAttribute('aria-expanded', 'false');
    menuToggle.innerHTML = '<i class="bi bi-list" aria-hidden="true"></i>';
  }
}

if (menuToggle && sidebar) {
  menuToggle.addEventListener('click', () => {
    const open = document.body.classList.toggle('nav-open');
    menuToggle.setAttribute('aria-expanded', String(open));
    menuToggle.innerHTML = `<i class="bi bi-${open ? 'x-lg' : 'list'}" aria-hidden="true"></i>`;
  });
  document.querySelectorAll('.sidebar-link').forEach(link => link.addEventListener('click', closeMobileNav));
  document.addEventListener('click', event => {
    if (document.body.classList.contains('nav-open') && !sidebar.contains(event.target) && !menuToggle.contains(event.target)) closeMobileNav();
  });
}


// Ordinary forms retain their normal navigation; asynchronous forms own their pending state.
document.addEventListener('submit', event => {
  const form = event.target;
  if (form.matches('[data-async-form]') || event.defaultPrevented) return;
  if (form.getAttribute('aria-busy') === 'true') { event.preventDefault(); return; }
  if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) { event.preventDefault(); return; }
  form.setAttribute('aria-busy', 'true');
  const button = event.submitter || form.querySelector('button[type="submit"]');
  if (button) { button.disabled = true; button.setAttribute('aria-busy', 'true'); button.textContent = 'Saving…'; }
});
window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeMobileNav(); });

const dialogControls = new Map();
function wireDialog(dialog, closeSelector) {
  let previous;
  const motion = window.XpenseMotion;
  const busy = () => dialog.querySelector('form[aria-busy="true"]');
  const close = () => {
    if (busy()) return Promise.resolve(false);
    if (motion) return motion.close(dialog);
    dialog.close(); return Promise.resolve(true);
  };
  const open = opener => {
    if (typeof dialog.showModal !== 'function') return false;
    if (!dialog.open) previous = opener || document.activeElement;
    closeMobileNav();
    if (motion) motion.open(dialog);
    else if (!dialog.open) dialog.showModal();
    return true;
  };
  dialog.addEventListener('click', event => {
    if (event.target.closest(closeSelector)) { close(); return; }
    const rect = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) close();
  });
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
  dialog.addEventListener('close', () => {
    if (dialog.open) return;
    motion?.reset(dialog);
    if (previous?.isConnected) previous.focus();
  });
  motion?.bindSheet(dialog, close);
  dialogControls.set(dialog.id, {open, close});
  return open;
}
window.XpenseDialogs = {
  open: (id, opener) => dialogControls.get(id)?.open(opener),
  close: id => dialogControls.get(id)?.close()
};
const filterDialog = document.getElementById('transaction-filters-sheet');
if (filterDialog) {
  const openFilters = wireDialog(filterDialog, '[data-filter-close]');
  document.addEventListener('click', event => {
    const opener = event.target.closest('[data-filter-open]');
    if (opener && openFilters(opener)) { event.preventDefault(); filterDialog.querySelector('input:not([type="hidden"]),select,button')?.focus(); }
  });
}
document.querySelectorAll('dialog[data-dialog], dialog.neu-dialog').forEach(dialog => {
  const open = wireDialog(dialog, '[data-dialog-close]');
  const optional = [...dialog.querySelectorAll('.dialog-optional-field')];
  if (optional.length) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'btn btn-link dialog-more p-0 text-start'; button.textContent = 'More details'; button.setAttribute('aria-expanded', 'false'); optional[0].before(button);
    const showOptional = visible => {
      button.setAttribute('aria-expanded', String(visible)); button.textContent = visible ? 'Hide optional details' : 'More details';
      optional.forEach(field => field.classList.toggle('is-visible', visible));
    };
    button.addEventListener('click', () => showOptional(button.getAttribute('aria-expanded') !== 'true'));
    if (optional.some(field => field.querySelector('.is-invalid,[aria-invalid="true"],.invalid-feedback'))) showOptional(true);
  }
  const updateAccountFields = () => {
    const type = dialog.querySelector('[name="account_type"]');
    if (!type) return;
    ['provider', 'last_four'].forEach(name => { const field = dialog.querySelector(`[data-field-name="${name}"]`); if (field) field.hidden = type.value === 'cash' && !field.querySelector('.is-invalid,[aria-invalid="true"]'); });
  };
  document.querySelectorAll(`[data-dialog-open="${dialog.id}"]`).forEach(button => button.addEventListener('click', () => {
    if (open(button)) { updateAccountFields(); dialog.querySelector('input:not([type="hidden"]),select,textarea')?.focus(); }
  }));
  dialog.querySelector('[name="account_type"]')?.addEventListener('change', updateAccountFields);
  if (dialog.dataset.hasErrors === 'true' || dialog.querySelector('.is-invalid,[aria-invalid="true"]')) {
    updateAccountFields();
    if (open()) focusFormError(dialog);
  }
});

function focusFormError(container) {
  const field = container.querySelector('[aria-invalid="true"],.is-invalid');
  if (!field) return;
  for (const details of container.querySelectorAll('details')) {
    if (details.contains(field)) details.open = true;
  }
  field.focus();
}
document.querySelectorAll('form[data-has-errors="true"]').forEach(focusFormError);

const budgetEditor = document.getElementById('budget-editor');
if (budgetEditor) {
  const form = budgetEditor.querySelector('form');
  ['category_id', 'month'].forEach(name => form.elements[name].addEventListener('change', () => {
    document.getElementById('budget-editor-title').textContent = 'Set a monthly limit';
    document.getElementById('budget-edit-status').textContent = 'Saving replaces any existing limit for this category and month.';
    form.querySelector('button[type="submit"]').textContent = 'Save budget';
  }));
  document.querySelectorAll('[data-budget-edit]').forEach(button => button.addEventListener('click', () => {
    form.elements.category_id.value = button.dataset.category;
    form.elements.month.value = button.dataset.month;
    form.elements.amount.value = button.dataset.amount;
    document.getElementById('budget-editor-title').textContent = 'Edit monthly limit';
    document.getElementById('budget-edit-status').textContent = `Editing ${button.dataset.categoryName} for ${button.dataset.month}. Saving replaces this limit.`;
    form.querySelector('button[type="submit"]').textContent = 'Update budget';
    form.elements.amount.focus();
    budgetEditor.scrollIntoView({block: 'nearest'});
  }));
}
const transactionActions = document.querySelectorAll('details.transaction-actions');
if (transactionActions.length) {
  const screen = window.matchMedia('(max-width: 639px)');
  const updateActions = () => transactionActions.forEach(details => { details.open = !screen.matches; });
  updateActions(); screen.addEventListener('change', updateActions);
}


function setPending(form, pending) {
  form.setAttribute('aria-busy', String(pending));
  form.querySelectorAll('button[type="submit"]').forEach(button => {
    if (pending) { button.dataset.idleLabel = button.textContent; button.textContent = 'Saving…'; }
    else button.textContent = button.dataset.idleLabel || 'Save';
    button.disabled = pending;
  });
}
async function saveRequest(url, options) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 20000);
  try { return await fetch(url, {...options, signal: controller.signal}); }
  catch (error) {
    if (error.name === 'AbortError') throw new Error('The save is taking too long. Check your ledger before trying again; the server may have received it.');
    throw new Error('Connection lost. Check your ledger before trying again; the server may have received it.');
  } finally { window.clearTimeout(timeout); }
}
async function readResponse(response) {
  let result;
  try { result = await response.json(); } catch (_) { throw new Error('The server could not save this entry. Check your connection and try again.'); }
  if (!response.ok) {
    const error = new Error(result.error || 'Check the highlighted fields and try again.');
    error.fields = result.errors; throw error;
  }
  return result;
}
function pendingTransaction(form) {
  const tbody = document.querySelector('.transaction-table tbody');
  if (!tbody) return null;
  const row = document.createElement('tr'); row.className = 'pending-transaction'; row.setAttribute('aria-busy', 'true');
  const cell = document.createElement('td'); cell.colSpan = 6;
  cell.textContent = `Saving ${form.elements.kind.value}: ${form.elements.amount.value} ${form.elements.account_id.selectedOptions[0]?.textContent.match(/\b[A-Z]{3}\s*$/)?.[0]?.trim() || 'INR'} · ${form.elements.category_id.selectedOptions[0]?.textContent || 'Transaction'}…`;
  row.append(cell); tbody.prepend(row); window.XpenseMotion?.insert(row); return row;
}
const composer = document.getElementById('expense-composer');
if (composer) {
  const form = document.getElementById('compose-form');
  const status = document.getElementById('compose-status');
  const amount = document.getElementById('compose-amount');
  const account = document.getElementById('compose-account');
  const updateCurrency = () => {
    const name = account.selectedOptions[0]?.textContent || '';
    const currency = name.match(/\b[A-Z]{3}\s*$/)?.[0]?.trim() || 'INR';
    document.getElementById('compose-amount-label').textContent = `Amount · ${currency}`;
  };
  account.addEventListener('change', updateCurrency); updateCurrency();
  const open = wireDialog(composer, '[data-compose-close]');
  const openComposer = opener => { if (open(opener)) { amount.focus(); return true; } return false; };
  document.addEventListener('click', event => {
    const opener = event.target.closest('[data-compose]');
    if (!opener) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (openComposer(opener)) event.preventDefault();
  });
  document.addEventListener('keydown', event => {
    const editable = event.target.closest('input,textarea,select,[contenteditable]:not([contenteditable="false"])');
    if (event.defaultPrevented || event.repeat || editable || document.querySelector('dialog[open]') || document.querySelector('.modal.show')) return;
    const command = (event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === 'k';
    const c = !event.metaKey && !event.ctrlKey && !event.altKey && event.key.toLowerCase() === 'c';
    if ((command || c) && openComposer()) event.preventDefault();
  });
  composer.querySelectorAll('[data-compose-kind]').forEach(button => button.addEventListener('click', () => {
    form.elements.kind.value = button.dataset.composeKind;
    composer.querySelectorAll('[data-compose-kind]').forEach(option => option.setAttribute('aria-pressed', String(option === button)));
  }));
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (form.getAttribute('aria-busy') === 'true') return;
    form.querySelectorAll('[aria-invalid]').forEach(field => field.removeAttribute('aria-invalid'));
    form.querySelectorAll('[data-error-for]').forEach(field => { field.textContent = ''; });
    const body = new FormData(form);
    const temporary = pendingTransaction(form);
    setPending(form, true); status.classList.remove('field-error'); status.textContent = 'Saving transaction…';
    try {
      const response = await saveRequest(form.action, { method: 'POST', headers: { Accept: 'application/json' }, body });
      const result = await readResponse(response);
      status.textContent = 'Transaction saved.';
      if (temporary) temporary.remove();
      // Refresh server-computed totals and ledger groups only after the save is confirmed.
      window.location.assign(result.redirect || window.location.href);
    } catch (error) {
      temporary?.remove(); status.classList.add('field-error'); status.textContent = error.message;
      let first;
      Object.entries(error.fields || {}).forEach(([name, messages]) => {
        const input = form.elements.namedItem(name);
        const message = [...form.querySelectorAll('[data-error-for]')].find(item => item.dataset.errorFor === name);
        if (message) message.textContent = Array.isArray(messages) ? messages.join(' ') : String(messages);
        if (input?.setAttribute) { input.setAttribute('aria-invalid', 'true'); first ||= input; }
      });
      if (first) { const details = first.closest('details'); if (details) details.open = true; first.focus(); }
      setPending(form, false);
    }
  });
}
const linkedDialog = document.getElementById('linked-composer');
if (linkedDialog) {
  const form = document.getElementById('linked-form');
  const mode = document.getElementById('linked-mode');
  const status = document.getElementById('linked-status');
  const open = wireDialog(linkedDialog, '[data-linked-close]');
  let recordId;
  const updateMode = () => {
    const split = mode.value === 'split';
    document.getElementById('refund-fields').hidden = split;
    document.getElementById('split-fields').hidden = !split;
    document.querySelectorAll('#refund-fields input').forEach(input => { input.disabled = split; input.required = !split; });
    document.querySelectorAll('#split-fields input,#split-fields select').forEach(input => { input.disabled = !split; input.required = split; });
  };
  mode.addEventListener('change', updateMode);
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-refund]');
    if (!button) return;
    recordId = button.dataset.refund; mode.value = 'refund'; status.textContent = '';
    const splitOption = mode.querySelector('option[value="split"]');
    splitOption.disabled = button.hasAttribute('data-refund-only');
    splitOption.hidden = splitOption.disabled;
    document.getElementById('refund-amount').value = button.dataset.amount;
    document.getElementById('refund-amount').max = button.dataset.amount;
    document.getElementById('linked-original').textContent = `Available to refund: ${button.dataset.amount}`;
    document.getElementById('split-category-1').value = button.dataset.category;
    document.getElementById('split-amount-1').value = button.dataset.amount;
    document.getElementById('split-amount-2').value = '';
    document.getElementById('linked-notes').value = '';
    updateMode(); if (open(button)) document.getElementById('refund-amount').focus();
  });
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (form.getAttribute('aria-busy') === 'true') return;
    const notes = document.getElementById('linked-notes').value;
    const split = mode.value === 'split';
    const payload = split ? {parts: [1, 2].map(part => ({category_id: Number(document.getElementById(`split-category-${part}`).value), amount: document.getElementById(`split-amount-${part}`).value, notes}))} : { amount: document.getElementById('refund-amount').value, date: document.getElementById('refund-date').value, notes };
    setPending(form, true); status.classList.remove('field-error'); status.textContent = 'Saving…';
    try {
      const response = await saveRequest(`/expenses/${encodeURIComponent(recordId)}/${split ? 'split' : 'refund'}`, {method: 'POST', headers: {'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRFToken': form.querySelector('[name="csrf_token"]').value}, body: JSON.stringify(payload)});
      await readResponse(response); window.location.reload();
    } catch (error) { status.classList.add('field-error'); status.textContent = error.message; setPending(form, false); }
  });
}




// Loan details belong to Loan / EMI commitments only.
const commitmentType = document.querySelector('.planning-form [name="commitment_type"]');
if (commitmentType) {
  const loanFields = ['lender', 'original_principal', 'outstanding', 'annual_rate',
    'tenure_months', 'processing_fee', 'loan_reference'];
  const updateLoanFields = () => {
    loanFields.forEach(name => {
      const field = commitmentType.form.querySelector(`.field-${name}`);
      if (field) field.hidden = commitmentType.value !== 'loan';
    });
  };
  commitmentType.addEventListener('change', updateLoanFields);
  updateLoanFields();
}

// Calendar reminders require an explicit opt-in for each open calendar tab.
const reminderButton = document.getElementById('browser-reminders');
if (reminderButton) {
  const status = document.getElementById('browser-reminder-status');
  let timer, previous, checking = false;
  const stop = message => {
    window.clearInterval(timer); timer = null;
    reminderButton.textContent = 'Enable desktop reminders';
    status.textContent = message;
  };
  const announce = snapshot => {
    const {date, month, due, soon, overdue} = snapshot.dataset;
    const signature = [date, month, due, soon, overdue].join(':');
    if (signature === previous) return;
    previous = signature;
    if (Number(due) + Number(soon) + Number(overdue) === 0) return;
    new window.Notification('Xpense payment reminders', {body: `${month}: ${due} due today, ${soon} in the next 7 days, ${overdue} overdue. Open the calendar for details.`, tag: 'xpense-calendar-reminders'});
  };
  const check = async () => {
    if (!timer || checking) return;
    if (window.Notification.permission !== 'granted') { stop('Desktop permission is off. Enable it in browser settings to receive reminders.'); return; }
    checking = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(window.location.href, {headers: {Accept: 'text/html'}, credentials: 'same-origin', signal: controller.signal});
      if (!response.ok) throw new Error();
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const snapshot = page.querySelector('#calendar-reminder-summary');
      if (!snapshot) { stop('Reminders stopped. Reload the calendar and sign in again.'); return; }
      if (!timer) return;
      announce(snapshot);
      status.textContent = 'Reminders enabled while this calendar tab stays open. The viewed month is checked once a minute.';
    } catch (_) {
      if (timer) status.textContent = 'Could not check reminders. Retrying while this calendar tab stays open.';
    } finally { checking = false; window.clearTimeout(timeout); }
  };
  if (!('Notification' in window) || !window.isSecureContext) {
    reminderButton.disabled = true;
    status.textContent = 'Desktop reminders are unavailable in this browser. Use the calendar or Notifications in the app.';
  } else reminderButton.addEventListener('click', async () => {
    if (timer) { stop('Desktop reminders stopped for this tab.'); return; }
    reminderButton.disabled = true;
    try {
      const permission = await window.Notification.requestPermission();
      if (permission !== 'granted') {
        status.textContent = 'Desktop reminders were not enabled. You can change notification permission in browser settings.';
        return;
      }
      announce(document.getElementById('calendar-reminder-summary'));
      timer = window.setInterval(check, 60000);
      reminderButton.textContent = 'Stop desktop reminders';
      status.textContent = 'Reminders enabled while this calendar tab stays open. The viewed month is checked once a minute.';
    } catch (_) { stop('Desktop reminders are unavailable. Use the calendar or Notifications in the app.'); }
    finally { reminderButton.disabled = false; }
  });
}
