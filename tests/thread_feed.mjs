import assert from 'node:assert/strict';
import { build } from '../frontend/node_modules/esbuild/lib/main.js';

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.listeners = new Map(); this.attributes = {}; this.classes = new Set(); this.scrollTop = 0; }
  get value() { return this._value ?? (this.tagName === 'select' ? this.children[0]?.value : '') ?? ''; }
  set value(value) { this._value = value; }
  get classList() { return { contains: key => this.classes.has(key), add: key => this.classes.add(key), remove: key => this.classes.delete(key) }; }
  get childElementCount() { return this.children.length; }
  append(...nodes) { for (const node of nodes) { node.parentElement = this; this.children.push(node); } }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(name, handler) { this.listeners.set(name, handler); }
  async fire(name, options = {}) { return this.listeners.get(name)?.({ preventDefault() {}, ...options }); }
  focus() { document.activeElement = this; }
  showModal() { this.open = true; }
  close() { this.open = false; void this.fire('close'); }
  remove() { this.parentElement.children = this.parentElement.children.filter(node => node !== this); }
  querySelector() { return descendants(this).find(node => node.tagName === 'button' && node.type === 'submit'); }
}
const descendants = node => node.children.flatMap(child => [child, ...descendants(child)]);
const shell = new Element('main'), panel = new Element('section'); panel.id = 'threads-panel'; panel.classes.add('hidden'); shell.append(panel);
let modalOpen = false;
globalThis.document = { body: shell, hidden: false, createElement: tag => new Element(tag), addEventListener() {}, querySelector: () => modalOpen ? {} : null,
  getElementById: id => [shell, ...descendants(shell)].find(node => node.id === id) };
let poll;
const storage = new Map();
globalThis.window = { localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
  setInterval(callback) { poll = callback; }, addEventListener() {} };
const me = { id: 'mine', username: 'viewer' }, alternate = { id: 'alternate', username: 'other-self' }, peer = { id: 'peer', username: '한글', display_name: '한글' };
globalThis.threadState = { activeList: 'threads', session: { user: me, active_identity_id: me.id, identities: [me, alternate] } };
const post = (body = 'A public post', viewer = me.id) => ({ id: 'post-1', author: peer, author_identity_id: peer.id,
  viewer_identity_id: viewer, body, visibility: 'public', created_at: '2026-10-21', like_count: 0, reply_count: 0 });
const requests = [];
let respond = request => Promise.resolve(request.url.pathname.endsWith('/people') ? { items: [peer] } : { items: [post('A public post', request.actor)], next_cursor: 'older' });
globalThis.threadRequest = (name, url, options, policy) => {
  const request = { name, url: new URL(url, 'http://fixture.test'), actor: options.headers['X-Acting-Identity'], options, policy };
  requests.push(request); return respond(request);
};
const result = await build({ entryPoints: ['frontend/src/app/threads.js'], bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'core-fixture', setup(builder) {
    builder.onResolve({ filter: /\/core\.js$/ }, () => ({ path: 'core', namespace: 'fixture' }));
    builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: 'export const state=globalThis.threadState, requestAction=globalThis.threadRequest;' }));
  } }] });
const { openThreads, closeThreads } = await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`);
const settle = () => new Promise(resolve => setImmediate(resolve));
const find = text => descendants(panel).find(node => node.tagName === 'button' && node.textContent === text);
const select = label => descendants(panel).find(node => node.attributes['aria-label'] === label);
const list = () => select('스레드 목록');
openThreads(); await settle();
assert.equal(list().children.length, 1);
await find('@한글 · 게시물 보기').fire('click'); await settle();
assert.equal(requests.at(-1).url.searchParams.get('authorId'), peer.id);
assert.equal(requests.at(-1).actor, me.id, 'viewing a peer never impersonates that peer');
assert.equal(requests.at(-1).policy.policy, 'replace');

select('스레드 검색').value = '@한글';
await find('사용자 찾기').fire('click'); await settle();
await find('게시물 보기').fire('click'); await settle();
assert.equal(requests.at(-1).url.searchParams.get('authorId'), peer.id);
assert.equal(list().children.length, 1);
respond = () => Promise.reject(new Error('temporary network failure'));
await find('새로고침').fire('click'); await settle();
assert.equal(list().children.length, 1, 'failed refresh retains the current feed and pagination');

let complete;
respond = () => new Promise(resolve => { complete = resolve; });
select('스레드 활동 ID').value = alternate.id;
await select('스레드 활동 ID').fire('change');
assert.equal(list().children.length, 0, 'changing identity immediately clears the previous identity view');
assert.equal(requests.at(-1).actor, alternate.id);
complete({ items: [post('Alternate view', alternate.id)], next_cursor: 'older' }); await settle();

let earlier;
respond = () => new Promise(resolve => { earlier = resolve; });
await find('새로고침').fire('click');
respond = () => Promise.resolve({ items: [post('Latest response', alternate.id)], next_cursor: 'older' });
await find('새로고침').fire('click'); await settle();
earlier({ items: [post('Stale response')], next_cursor: '' }); await settle();
assert.equal(list().children[0].children.find(node => node.tagName === 'p').textContent, 'Latest response');

// The idle first page refreshes even when a "more" cursor exists.
let before = requests.length;
poll(); await settle(); assert.equal(requests.length, before + 1);
modalOpen = true; before = requests.length;
poll(); await settle(); assert.equal(requests.length, before, 'polling must not replace an open edit/report context');
modalOpen = false;
const editor = select('스레드 내용'); editor.value = '다음 글 초안'; await editor.fire('input');
before = requests.length; poll(); await settle(); assert.equal(requests.length, before, 'polling never interrupts an active draft');
editor.value = ''; await editor.fire('input'); shell.scrollTop = 200;
poll(); await settle(); assert.equal(requests.length, before, 'polling preserves a reader scrolled down');
shell.scrollTop = 0;
await find('← 전체 피드').fire('click'); await settle();
assert.equal(requests.at(-1).url.searchParams.get('authorId'), '');
assert.equal(requests.at(-1).url.searchParams.get('q'), '');
respond = request => request.options.method === 'POST' ? Promise.resolve({ok:true}) : Promise.reject(new Error('reload failed'));
await find('차단').fire('click');
const dialog = shell.children.find(node => node.tagName === 'dialog');
await dialog.children[0].fire('submit'); await settle();
assert.equal(list().children.length, 0, 'a successful block cannot retain revoked cards when its refresh fails');
closeThreads();
console.log('Thread feed: peer browsing, fixed actor, failed refresh, identity isolation, stale requests and idle polling OK');
