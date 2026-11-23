import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Minimal DOM boundary only: the complete production controller owns filtering/rendering.
class Node {
  constructor(tagName = 'DIV') { this.tagName = tagName; this.children = []; this.dataset = {}; this.listeners = new Map(); this.classList = {add() {}}; this.textContent = ''; }
  append(...children) { for (const child of children) this.children.push(...(child.fragment ? child.children : [child])); }
  replaceChildren(...children) { this.children = []; this.append(...children); }
  addEventListener(type, listener) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(listener); }
  emit(type, target = this) { for (const listener of this.listeners.get(type) || []) listener({target, preventDefault() {}}); }
  querySelectorAll() { return []; }
  querySelector() { return null; }
}
const category = {name: 'category', value: '', tagName: 'SELECT', type: 'select-one'};
const form = new Node('FORM'); form.elements = [category]; form.reportValidity = () => true;
const clear = new Node('A');
const exportLink = new Node('A'); exportLink.href = 'https://xpense.test/expenses/export';
form.querySelector = selector => selector === '[data-filter-clear]' ? clear : selector === '[data-filter-export]' ? exportLink : null;
const root = new Node(); root.dataset.snapshotUrl = '/expenses/snapshot';
const tbody = new Node('TBODY'); root.querySelector = selector => selector === 'tbody' ? tbody : null;
const home = new Node(), homeBody = new Node(); home.querySelector = () => homeBody;
const sheet = new Node('DIALOG'); sheet.id = 'transaction-filters-sheet';
const status = new Node(), error = new Node(), pager = new Node();
const elements = new Map([['transaction-filters',form],['filter-home',home],['transaction-filters-sheet',sheet],['filter-sheet-body',new Node()],['local-filter-status',status],['filter-error',error],['ledger-pagination',pager]]);
const markupRows = [1,2].map(id => { const row = new Node('TR'); row.dataset.transactionId = String(id); return row; });
const document = {
  documentElement: new Node(), querySelector: selector => selector === '#ledger-results' ? root : null,
  getElementById: id => elements.get(id), dispatchEvent() {},
  createDocumentFragment() { const fragment = new Node(); fragment.fragment = true; return fragment; },
  createElement(tag) { const element = new Node(tag.toUpperCase()); if (tag === 'template') element.content = {querySelectorAll: () => markupRows}; return element; }
};
const location = {search: ''};
const historyCalls = [];
let respond;
const pendingFetch = new Promise(resolve => {respond = resolve;});
const window = {history: {replaceState(_state, _title, url) {historyCalls.push(url); location.search = url;}}, addEventListener() {}};
class FormData { constructor(element) {this.entries = element.elements.map(field => [field.name, field.value]);} [Symbol.iterator]() {return this.entries[Symbol.iterator]();} }
const context = {window, document, location, FormData, URL, URLSearchParams, Intl, performance, AbortController, setTimeout, clearTimeout, CustomEvent: class {constructor(type) {this.type = type;}}, matchMedia: () => ({matches:false,addEventListener(){}}), fetch: () => pendingFetch};
vm.runInNewContext(fs.readFileSync('app/static/js/ledger-filters.js','utf8'), context);
assert.equal(status.textContent, 'Preparing fast filters…', 'snapshot must still be pending');
category.value = '2';
form.emit('change', category);
respond({ok:true,json:async () => ({complete:true,today:'2026-11-01',html:'<server-rendered-rows>',rows:[
  {id:1,date:'2026-11-01',category_id:1,category:'Food',account_id:null,kind:'expense',amount_minor:100,notes:'Lunch'},
  {id:2,date:'2026-11-01',category_id:2,category:'Travel',account_id:null,kind:'expense',amount_minor:200,notes:'Metro'}
]})});
for (let turn=0;turn<12;turn++) await Promise.resolve();
assert.equal(error.textContent, '', 'the real render must finish without DOM-stub exceptions');
const renderedIds = tbody.children.filter(row => row.dataset.transactionId).map(row => Number(row.dataset.transactionId));
assert.deepEqual(renderedIds, [2], 'changing category while snapshot loads must render the current selection');
assert.equal(new URLSearchParams(location.search).get('category'), '2', 'URL must reflect the selection rendered after preparation');
assert.equal(status.textContent, '1 matching transactions · Filters ready');
assert.equal(category.value, '2', 'preparation must preserve the selected control');
assert.equal(new URL(exportLink.href).searchParams.get('category'), '2', 'export must use the same active filter');
assert.ok(historyCalls.length, 'preparation must synchronise history when it applies a queued selection');
console.log('PASS: category changed during delayed snapshot preparation stays consistent across rows, controls, status, URL and export.');
