/* Interruptible interface feedback. The ledger remains authoritative. */
(() => {
  const duration = 160;
  const easing = 'cubic-bezier(0.22, 1, 0.36, 1)';
  const running = new WeakMap();
  const requests = new WeakMap();
  const next = element => { const token = {}; requests.set(element, token); return token; };
  const sheets = new WeakSet();
  const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const mobile = () => matchMedia('(max-width: 639px)').matches;
  const snapshot = element => {
    const style = getComputedStyle(element);
    return {opacity: style.opacity, transform: style.transform};
  };
  function clear(element) { element.style.opacity = ''; element.style.transform = ''; element.style.willChange = ''; }
  function stop(element) {
    const frame = snapshot(element);
    const old = running.get(element);
    if (old) { running.delete(element); old.animation.cancel(); }
    element.style.opacity = frame.opacity;
    element.style.transform = frame.transform;
    return frame;
  }
  async function transition(element, destination, initial) {
    const wasRunning = running.has(element);
    const current = stop(element);
    const from = !wasRunning && initial ? initial : current;
    if (reduced() || typeof element.animate !== 'function') { clear(element); return true; }
    element.style.willChange = 'transform, opacity';
    const animation = element.animate([from, destination], {duration, easing, fill: 'both'});
    const record = {animation}; running.set(element, record);
    try {
      await animation.finished;
      if (running.get(element) !== record) return false;
      running.delete(element); animation.cancel(); clear(element); return true;
    } catch (_) {
      // Cancellation is a normal retarget, not an error or a completed close.
      return false;
    }
  }
  const rest = {opacity: '1', transform: 'translateY(0px) scale(1)'};
  const exitFrame = () => ({opacity: mobile() ? '1' : '0', transform: mobile() ? 'translateY(100%)' : 'translateY(6px) scale(.98)'});
  function open(dialog) {
    next(dialog);
    const alreadyOpen = dialog.open;
    if (!alreadyOpen) dialog.showModal();
    return transition(dialog, rest, alreadyOpen ? null : exitFrame());
  }
  async function close(dialog) {
    if (!dialog.open) { reset(dialog); return true; }
    const token = next(dialog);
    const settled = await transition(dialog, exitFrame());
    if (!settled || requests.get(dialog) !== token) return false;
    dialog.close();
    return settled;
  }
  function insert(row) { next(row); return transition(row, rest, {opacity: '0', transform: 'translateY(-4px)'}); }
  async function remove(row) {
    const token = next(row);
    const settled = await transition(row, {opacity: '0', transform: 'translateY(-4px)'});
    if (!settled || requests.get(row) !== token) return false;
    row.remove();
    return settled;
  }
  function reset(element) { next(element); stop(element); clear(element); }
  function bindSheet(dialog, requestClose) {
    if (sheets.has(dialog)) return;
    const handle = dialog.querySelector('[data-sheet-handle]');
    if (!handle) return;
    sheets.add(dialog);
    let drag = null;
    const busy = () => dialog.querySelector('form[aria-busy="true"]');
    handle.addEventListener('pointerdown', event => {
      if (!mobile() || !dialog.open || busy() || drag || event.isPrimary === false || event.button !== 0) return;
      const frame = stop(dialog);
      // A pointer can take over during an interrupted entrance without snapping to zero.
      let base = 0;
      const matrix = frame.transform.match(/^matrix\([^,]+,[^,]+,[^,]+,[^,]+,[^,]+,\s*([^\)]+)\)$/);
      const translated = frame.transform.match(/^translateY\((-?[\d.]+)px\)/);
      if (matrix) base = Number(matrix[1]) || 0;
      else if (translated) base = Number(translated[1]) || 0;
      drag = {id: event.pointerId, start: event.clientY, distance: 0, base};
      handle.setPointerCapture?.(event.pointerId); event.preventDefault();
    });
    handle.addEventListener('pointermove', event => {
      if (!drag || drag.id !== event.pointerId) return;
      drag.distance = Math.max(0, event.clientY - drag.start);
      if (!reduced()) dialog.style.transform = `translateY(${Math.min(dialog.getBoundingClientRect().height, Math.max(0, drag.base + event.clientY - drag.start))}px)`;
      event.preventDefault();
    });
    function finish(event, cancelled) {
      if (!drag || drag.id !== event.pointerId) return;
      const dismiss = !cancelled && drag.distance > 64 && mobile() && !busy();
      drag = null;
      try { handle.releasePointerCapture?.(event.pointerId); } catch (_) {}
      if (dismiss) requestClose();
      else transition(dialog, rest);
    }
    handle.addEventListener('pointerup', event => finish(event, false));
    handle.addEventListener('pointercancel', event => finish(event, true));
    dialog.addEventListener('close', () => { drag = null; reset(dialog); });
  }
  window.XpenseMotion = {open, close, insert, remove, reset, bindSheet};
})();



