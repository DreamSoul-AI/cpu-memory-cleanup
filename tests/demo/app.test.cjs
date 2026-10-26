const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../demo/app.js'), 'utf8');

function preview(reduced = false) {
  const elements = {};
  const pending = new Map();
  let nextId = 0;
  const context = {
    document: { getElementById(id) {
      return elements[id] ??= { disabled: id === 'revisit', style: {},
        setAttribute(key, value) { this[key] = value; },
        addEventListener(_, callback) { this.click = callback; } };
    } },
    performance: { now: () => 0 }, matchMedia: () => ({ matches: reduced }),
    requestAnimationFrame(callback) { pending.set(++nextId, callback); return nextId; },
    cancelAnimationFrame(id) { pending.delete(id); }
  };
  vm.runInNewContext(source, context);
  return { elements, pending, tick(now) {
    const jobs = [...pending.values()]; pending.clear();
    for (const callback of jobs) callback(now);
  } };
}

test('cleaning is single flight and finishes at the preset values', () => {
  const p = preview(); p.elements.clean.click(); p.elements.clean.click();
  assert.equal(p.pending.size, 1); assert.equal(p.elements.clean.disabled, true);
  p.tick(700); assert.equal(p.elements.percent.textContent, 62);
  p.tick(1400); assert.equal(p.elements.percent.textContent, 48);
  assert.equal(p.elements.available.textContent, '8.32');
  assert.equal(p.elements.revisit.disabled, false);
});
test('revisit requires cleaning and increases occupancy without another cleaning', () => {
  const p = preview(); p.elements.revisit.click(); assert.equal(p.pending.size, 0);
  p.elements.clean.click(); p.tick(1400); p.elements.revisit.click(); p.elements.revisit.click();
  assert.equal(p.pending.size, 1); p.tick(1400);
  assert.equal(p.elements.percent.textContent, 69);
  assert.equal(p.elements.usage['aria-valuenow'], '69');
  assert.equal(p.elements.available.textContent, '4.96');
});
test('reset cancels pending animation and restores the initial state', () => {
  const p = preview(); p.elements.clean.click(); p.tick(700); p.elements.reset.click();
  assert.equal(p.pending.size, 0); assert.equal(p.elements.percent.textContent, 76);
  assert.equal(p.elements.available.textContent, '3.84');
  assert.equal(p.elements.clean.disabled, false); assert.equal(p.elements.revisit.disabled, true);
});
test('reduced motion completes without intermediate numeric animation', () => {
  const p = preview(true); p.elements.clean.click(); p.tick(0);
  assert.equal(p.elements.percent.textContent, 48); assert.equal(p.pending.size, 0);
});
