/* Private, bounded snapshots stay in this tab's memory. Server forms remain the fallback. */
(() => {
  const asciiFold = value => String(value || '').replace(/[A-Z]/g, letter => letter.toLowerCase());
  function minorFromInput(value) {
    if (!/^\d+(?:\.\d{1,2})?$/.test(value)) throw new Error('Enter a non-negative amount with at most two decimal places.');
    const [whole, fraction = ''] = value.split('.');
    const amount = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
    if (!Number.isSafeInteger(amount) || amount > 99999999999) throw new Error('Amount exceeds the supported limit.');
    return amount;
  }
  function createIndex(rows) {
    const prepared = rows.map(row => ({...row, notesFold: asciiFold(row.notes), categoryFold: asciiFold(row.category)}));
    const tie = (a, b) => b.id - a.id;
    const compareText = (a, b) => a < b ? -1 : a > b ? 1 : 0;
    const orders = {
      date_desc: (a, b) => compareText(b.date, a.date) || tie(a, b),
      date_asc: (a, b) => compareText(a.date, b.date) || tie(a, b),
      amount_desc: (a, b) => b.amount_minor - a.amount_minor || tie(a, b),
      amount_asc: (a, b) => a.amount_minor - b.amount_minor || tie(a, b),
      category: (a, b) => compareText(a.categoryFold, b.categoryFold) || tie(a, b)
    };
    return Object.fromEntries(Object.entries(orders).map(([key, compare]) => [key, [...prepared].sort(compare)]));
  }
  function select(index, params) {
    const sort = params.get('sort') || 'date_desc';
    if (!Object.hasOwn(index, sort)) throw new Error('Choose a valid sort order.');
    const start = params.get('start') || '', end = params.get('end') || '';
    for (const value of [start, end].filter(Boolean)) {
      const parsed = new Date(value + 'T00:00:00Z');
      if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== value) throw new Error('Enter a valid date.');
    }
    if (start && end && start > end) throw new Error('From date must not be after To date.');
    const min = params.get('min_amount') ? minorFromInput(params.get('min_amount')) : 0;
    const max = params.get('max_amount') ? minorFromInput(params.get('max_amount')) : 99999999999;
    if (min > max) throw new Error('Minimum amount must not exceed maximum amount.');
    const kind = params.get('kind') || '';
    if (kind && !['income', 'expense'].includes(kind)) throw new Error('Choose income or expense.');
    const category = params.get('category'), account = params.get('account');
    const query = asciiFold((params.get('q') || '').slice(0, 100));
    return index[sort].filter(row => (!start || row.date >= start) && (!end || row.date <= end) &&
      (!category || String(row.category_id) === category) && (!account || String(row.account_id) === account) &&
      (!kind || row.kind === kind) && row.amount_minor >= min && row.amount_minor <= max &&
      (!query || row.notesFold.includes(query) || row.categoryFold.includes(query)));
  }
  window.XpenseFilters = {minorFromInput, createIndex, select};
  const root = document.querySelector('#ledger-results');
  if (!root) return;
  const form = document.getElementById('transaction-filters');
  const home = document.getElementById('filter-home');
  const sheet = document.getElementById('transaction-filters-sheet');
  const sheetBody = document.getElementById('filter-sheet-body');
  const status = document.getElementById('local-filter-status');
  const error = document.getElementById('filter-error');
  const pager = document.getElementById('ledger-pagination');
  const tbody = root.querySelector('tbody');
  const mobile = matchMedia('(max-width: 639px)');
  const placeFilters = () => {
    if (mobile.matches) sheetBody.append(form);
    else { window.XpenseDialogs?.close(sheet.id); home.querySelector('.card-body').append(form); }
  };
  document.documentElement.classList.add('filters-enhanced');
  placeFilters(); mobile.addEventListener('change', placeFilters);
  const rows = new Map();
  let snapshot, index, ready = false, dirty = false;
  window.XpenseFilterMetrics = [];
  const paramsFromForm = () => {
    const params = new URLSearchParams();
    for (const [key, value] of new FormData(form)) if (value) params.set(key, value);
    return params;
  };
  const updateExport = params => {
    const link = form.querySelector('[data-filter-export]');
    const url = new URL(link.href); url.search = params.toString(); url.searchParams.delete('page'); link.href = url;
  };
  const dateLabels = new Map();
  const dateFormatter = new Intl.DateTimeFormat('en-GB', {day:'2-digit',month:'short',year:'numeric',timeZone:'UTC'});
  const dateLabel = value => {
    if (dateLabels.has(value)) return dateLabels.get(value);
    if (value === snapshot.today) return 'Today';
    const date = new Date(value + 'T00:00:00Z');
    const yesterday = new Date(snapshot.today + 'T00:00:00Z'); yesterday.setUTCDate(yesterday.getUTCDate() - 1);
    const label = value === yesterday.toISOString().slice(0, 10) ? 'Yesterday' : dateFormatter.format(date);
    dateLabels.set(value, label); return label;
  };
  function render(params, history = true) {
    const began = performance.now();
    try {
      const matches = select(index, params);
      const pages = Math.max(1, Math.ceil(matches.length / 20));
      const page = Math.max(1, Math.min(pages, Number.parseInt(params.get('page'), 10) || 1));
      const fragment = document.createDocumentFragment();
      let date;
      for (const record of matches.slice((page - 1) * 20, page * 20)) {
        if (date !== record.date && ['date_desc', 'date_asc'].includes(params.get('sort') || 'date_desc')) {
          const group = document.createElement('tr'); group.className = 'transaction-group';
          const cell = document.createElement('th'); cell.colSpan = 6; cell.scope = 'rowgroup'; cell.textContent = dateLabel(record.date); group.append(cell); fragment.append(group); date = record.date;
        }
        fragment.append(rows.get(record.id));
      }
      if (!matches.length) {
        const row = document.createElement('tr'), cell = document.createElement('td'); cell.colSpan = 6; cell.className = 'text-center py-4 text-body-secondary'; cell.textContent = 'No transactions match these filters. Clear filters or choose another date.'; row.append(cell); fragment.append(row);
      }
      tbody.replaceChildren(fragment);
      pager.replaceChildren();
      const label = document.createElement('span'); label.className = 'small text-body-secondary'; label.textContent = `${matches.length} records · Page ${page} of ${pages}`; pager.append(label);
      const controls = document.createElement('div'); controls.className = 'd-flex gap-2';
      for (const [text, number] of [['Previous', page - 1], ['Next', page + 1]]) if (number > 0 && number <= pages) {
        const link = document.createElement('a'); link.className = 'btn btn-light btn-sm'; link.textContent = text;
        const next = new URLSearchParams(params); next.set('page', number); link.href = '?' + next;
        link.addEventListener('click', event => { if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return; event.preventDefault(); render(next); }); controls.append(link);
      }
      pager.append(controls); error.textContent = ''; updateExport(params);
      if (history) window.history.replaceState(null, '', '?' + params.toString());
      status.textContent = `${matches.length} matching transactions · Filters ready`;
      const duration = performance.now() - began;
      root.dataset.filterDurationMs = duration.toFixed(3);
      window.XpenseFilterMetrics.push(duration); if (window.XpenseFilterMetrics.length > 100) window.XpenseFilterMetrics.shift();
      document.dispatchEvent(new CustomEvent('ledger:rendered'));
      return true;
    } catch (failure) { error.textContent = failure.message; return false; }
  }
  function apply() { dirty = true; if (ready && form.reportValidity()) render(paramsFromForm()); }
  form.addEventListener('input', event => { dirty = true; if (ready && !['date','number'].includes(event.target.type)) apply(); });
  form.addEventListener('change', apply);
  form.addEventListener('submit', event => {
    if (!ready) return;
    event.preventDefault();
    if (form.reportValidity() && render(paramsFromForm())) window.XpenseDialogs?.close(sheet.id);
  });
  form.querySelector('[data-filter-clear]').addEventListener('click', event => {
    if (!ready || event.ctrlKey || event.metaKey) return;
    event.preventDefault(); form.reset();
    for (const field of form.elements) if (field.name && field.tagName !== 'BUTTON') field.value = field.name === 'sort' ? 'date_desc' : '';
    render(paramsFromForm());
  });
  window.addEventListener('popstate', () => {
    const params = new URLSearchParams(location.search);
    for (const field of form.elements) if (field.name) field.value = params.get(field.name) || (field.name === 'sort' ? 'date_desc' : '');
    if (ready) render(params, false);
  });
  root.addEventListener('submit', async event => {
    const deletion = event.target.closest('form[data-ledger-delete]');
    if (!deletion || !ready) return;
    event.preventDefault(); if (deletion.getAttribute('aria-busy') === 'true' || !window.confirm(deletion.dataset.confirm)) return;
    const row = deletion.closest('tr[data-transaction-id]'), id = Number(row.dataset.transactionId);
    const body = new FormData(deletion); setPending(deletion, true); row.setAttribute('aria-busy','true');
    status.textContent = 'Deleting transaction…';
    try {
      const response = await saveRequest(deletion.action, {method:'POST',headers:{Accept:'application/json'},body});
      const result = await readResponse(response);
      if (result.id !== id) throw new Error('Could not confirm the deletion. Reload your ledger before trying again.');
      await window.XpenseMotion?.remove(row);
      snapshot.rows = snapshot.rows.filter(record => record.id !== id); rows.delete(id); index = createIndex(snapshot.rows); render(paramsFromForm());
      status.textContent = 'Transaction deleted.';
    } catch (failure) { row.removeAttribute('aria-busy'); setPending(deletion, false); error.textContent = failure.message; status.textContent = 'Transaction was not confirmed deleted. Reload to check its status.'; }
  });
  async function prepare() {
    status.textContent = 'Preparing fast filters…';
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(root.dataset.snapshotUrl, {cache:'no-store',headers:{Accept:'application/json'},signal:controller.signal});
      if (!response.ok) throw new Error('Snapshot unavailable');
      const data = await response.json();
      if (!data.complete) { status.textContent = 'Use Apply filters to search this ledger.'; return; }
      if (!data.rows.length) { status.textContent = 'Add a transaction to start filtering.'; return; }
      const template = document.createElement('template'); template.innerHTML = data.html;
      // This markup comes only from our same-origin, escaping Jinja row template.
      template.content.querySelectorAll('tr[data-transaction-id]').forEach(row => {
        row.querySelectorAll('form[data-confirm]').forEach(form => { form.dataset.asyncForm = ''; form.dataset.ledgerDelete = ''; });
        rows.set(Number(row.dataset.transactionId), row);
      });
      if (data.rows.length !== rows.size) throw new Error('Incomplete ledger markup');
      snapshot = data; index = createIndex(data.rows); ready = true; render(dirty ? paramsFromForm() : new URLSearchParams(location.search), dirty);
    } catch (_) { status.textContent = 'Fast filters unavailable. Apply filters still works.'; }
    finally { clearTimeout(timeout); }
  }
  prepare();
})();
