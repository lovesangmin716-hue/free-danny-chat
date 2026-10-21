import assert from 'node:assert/strict';
import { openThreadDialog, closeThreadDialog } from '../frontend/src/app/platform/thread-dialog.js';

// Minimal event-driven DOM double; exercise the real handlers without a browser dependency.
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = new Map(); this.attributes = {}; this.value = ''; }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(name, handler) { this.listeners.set(name, handler); }
  async fire(name, options = {}) {
    const event = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...options };
    await this.listeners.get(name)?.(event);
    return event;
  }
  showModal() { this.open = true; }
  close() { if (this.open) { this.open = false; void this.fire('close'); } }
  remove() { this.parent.children = this.parent.children.filter(node => node !== this); }
  focus() { document.activeElement = this; }
  requestSubmit() { this.submission = this.fire('submit'); }
}
globalThis.document = { createElement: tag => new Element(tag), body: new Element('body') };
function elements() {
  const dialog = document.body.children[0], form = dialog.children[0];
  const controls = form.children.at(-1);
  return { dialog, form, input: form.children.find(node => node.tagName === 'textarea'),
    feedback: form.children.find(node => node.attributes.role === 'status'), cancel: controls.children[0], submit: controls.children[1] };
}
let attempts = 0, submitted;
openThreadDialog({ title: 'Edit as author', description: 'Edit safely', value: 'Original', confirmLabel: 'Save',
  onConfirm: async value => { attempts++; submitted = value; if (attempts === 1) throw new Error('409: edited in another window'); } });
let current = elements();
assert.equal(current.dialog.attributes['aria-labelledby'], 'thread-dialog-title');
assert.equal(document.activeElement, current.input);
current.input.value = 'My unsaved edit';
await current.input.fire('input');
await current.form.fire('submit');
assert.equal(current.dialog.open, true);
assert.equal(current.input.value, 'My unsaved edit');
assert.equal(current.feedback.textContent, '409: edited in another window');
assert.equal(current.input.disabled, false);
assert.equal(document.activeElement, current.input);
await current.form.fire('submit');
assert.equal(submitted, 'My unsaved edit');
assert.equal(document.body.children.length, 0);

let release, calls = 0;
openThreadDialog({ title: 'Report', description: 'Reason', value: '', minLength: 5, maxLength: 500, confirmLabel: 'Report',
  onConfirm: () => { calls++; return new Promise(resolve => { release = resolve; }); } });
current = elements();
assert.equal(current.submit.disabled, true);
current.input.value = '    ';
await current.input.fire('input');
await current.form.fire('submit');
assert.equal(calls, 0);
current.input.value = 'Valid reason';
await current.input.fire('input');
await current.input.fire('keydown', { key: 'Enter', ctrlKey: true, isComposing: true });
await current.input.fire('keydown', { key: 'Enter', metaKey: true, keyCode: 229 });
assert.equal(calls, 0, 'IME confirmation must not submit');
await current.input.fire('keydown', { key: 'Enter' });
assert.equal(calls, 0, 'plain Enter remains a newline');
await current.input.fire('keydown', { key: 'Enter', ctrlKey: true });
assert.equal(calls, 1);
assert.equal(current.cancel.disabled, true);
assert.equal((await current.dialog.fire('cancel')).defaultPrevented, true);
await current.form.fire('submit');
assert.equal(calls, 1, 'in-flight requests cannot be duplicated');
release(); await current.form.submission;
assert.equal(document.body.children.length, 0);

let confirmed = false;
openThreadDialog({ title: 'Delete', description: 'Keep replies', confirmLabel: 'Delete', onConfirm: () => { confirmed = true; } });
current = elements();
assert.equal(current.input, undefined);
assert.equal(document.activeElement, current.cancel);
await current.cancel.fire('click');
assert.equal(confirmed, false);
assert.equal(document.body.children.length, 0);
openThreadDialog({ title: 'Close on logout', description: '', value: 'private', confirmLabel: 'Save', onConfirm() {} });
closeThreadDialog();
assert.equal(document.body.children.length, 0);
console.log('Thread dialogs: retained failed edits, validation, IME, single submission, cancel and cleanup OK');
