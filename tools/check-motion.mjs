import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
let reduced = false;
let mobile = true;
const context = {window: {}, matchMedia: query => ({get matches() {return query.includes('reduced') ? reduced : mobile;}}), getComputedStyle: element => ({opacity: element.visual?.opacity || element.style.opacity || '1', transform: element.visual?.transform || element.style.transform || 'none'})};
vm.createContext(context);
vm.runInContext(fs.readFileSync('app/static/js/motion.js', 'utf8'), context);
const motion = context.window.XpenseMotion;
function element() {
  const listeners = {};
  const handle = {listeners: {}, addEventListener(type, callback) {this.listeners[type] = callback;}, setPointerCapture() {}, releasePointerCapture() {}};
  return {style: {}, open: false, animations: [], handle,
    showModal() {this.open = true;}, close() {this.open = false;}, remove() {this.removed = true;},
    getBoundingClientRect: () => ({height: 300}), querySelector(selector) {return selector === '[data-sheet-handle]' ? handle : this.busy ? {} : null;},
    addEventListener(type, callback) {listeners[type] = callback;},
    animate(frames, options) {
      let resolve, reject;
      const animation = {frames, options, finished: new Promise((ok, no) => {resolve = ok; reject = no;}), cancel() {this.cancelled = true; reject(new Error('cancelled'));}, finish() {resolve();}};
      this.animations.push(animation); return animation;
    }};
}
const dialog = element();
const opening = motion.open(dialog);
assert.equal(dialog.open, true);
assert.ok(dialog.animations[0].options.duration <= 160);
dialog.visual = {opacity: '.65', transform: 'matrix(1, 0, 0, 1, 0, 8)'};
const closing = motion.close(dialog);
assert.equal(dialog.animations[0].cancelled, true);
assert.equal(dialog.animations[1].frames[0].opacity, '.65');
assert.equal(dialog.animations[1].frames[0].transform, dialog.visual.transform);
const reopening = motion.open(dialog);
dialog.animations.at(-1).finish();
assert.equal(await opening, false); assert.equal(await closing, false); assert.equal(await reopening, true);
assert.equal(dialog.open, true); assert.equal(dialog.style.transform, '');
const row = element();
const removing = motion.remove(row); const reinserting = motion.insert(row);
row.animations.at(-1).finish();
assert.equal(await removing, false); assert.equal(await reinserting, true); assert.ok(!row.removed);
reduced = true;
const quiet = element(); await motion.open(quiet); await motion.close(quiet); assert.equal(quiet.animations.length, 0); assert.equal(quiet.open, false);
const quietRace = element(); quietRace.open = true; const staleClose = motion.close(quietRace); await motion.open(quietRace); assert.equal(await staleClose, false); assert.equal(quietRace.open, true);
const quietRow = element(); await motion.remove(quietRow); assert.equal(quietRow.removed, true);
reduced = false;
let dismissed = 0;
const sheet = element(); sheet.open = true; motion.bindSheet(sheet, () => {dismissed++;});
const pointer = (type, y, id = 1) => sheet.handle.listeners[type]({pointerId: id, clientY: y, button: 0, isPrimary: true, preventDefault() {}});
pointer('pointerdown', 100); pointer('pointermove', 50); assert.equal(sheet.style.transform, 'translateY(0px)');
pointer('pointermove', 900); assert.equal(sheet.style.transform, 'translateY(300px)');
pointer('pointerup', 900); assert.equal(dismissed, 1);
pointer('pointerdown', 100); pointer('pointermove', 164); pointer('pointerup', 164); assert.equal(dismissed, 1);
sheet.animations.at(-1).finish(); await Promise.resolve(); await Promise.resolve();
pointer('pointerdown', 100); pointer('pointermove', 190, 2); pointer('pointerup', 190, 2); assert.equal(dismissed, 1); pointer('pointercancel', 190); sheet.animations.at(-1).finish(); await Promise.resolve(); await Promise.resolve();
sheet.busy = true; pointer('pointerdown', 100); pointer('pointermove', 250); pointer('pointerup', 250); assert.equal(dismissed, 1); sheet.busy = false;
mobile = false; pointer('pointerdown', 100); pointer('pointermove', 250); pointer('pointerup', 250); assert.equal(dismissed, 1);
console.log('PASS: interrupted close/reopen preserves current frame, row removal superseded safely, reduced motion bypasses WAAPI, drag bounds/threshold/mobile-only behavior.');




