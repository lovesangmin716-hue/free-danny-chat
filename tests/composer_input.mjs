import assert from 'node:assert/strict';
import { build } from '../frontend/node_modules/esbuild/lib/main.js';
import { createDraftWriter } from '../frontend/src/app/platform/draft-writer.js';

const saved = new Map();
let writes = 0, accesses = 0;
const storage = { setItem(key, value) { writes++; saved.set(key, value); }, removeItem(key) { saved.delete(key); } };
const writer = createDraftWriter(() => { accesses++; return storage; });
for (let index = 0; index < 100; index++) writer.queue('identity-A:room-A', `draft-${index}`);
assert.equal(accesses, 0, 'typing must not access synchronous browser storage');
writer.flush();
assert.equal(writes, 1);
assert.equal(saved.get('identity-A:room-A'), 'draft-99');
writer.queue('identity-A:room-A', 'old pending');
writer.remove('identity-A:room-A');
writer.queue('identity-B:room-B', 'another ID');
writer.flush();
assert.equal(saved.has('identity-A:room-A'), false, 'sending cancels stale queued drafts');
assert.equal(saved.get('identity-B:room-B'), 'another ID');
const unavailable = createDraftWriter(() => { throw new Error('storage denied'); });
unavailable.queue('key', 'memory remains with caller'); unavailable.flush(); unavailable.remove('key');
const lifecycle = new Map();
globalThis.window = { addEventListener: (name, callback) => lifecycle.set(name, callback) };
globalThis.document = { hidden: true, addEventListener: (name, callback) => lifecycle.set(name, callback) };
const lifecycleWriter = createDraftWriter(() => storage, true);
lifecycleWriter.queue('pagehide-draft', 'survives leaving'); lifecycle.get('pagehide')();
lifecycleWriter.queue('hidden-draft', 'survives background'); lifecycle.get('visibilitychange')();
assert.equal(saved.get('pagehide-draft'), 'survives leaving');
assert.equal(saved.get('hidden-draft'), 'survives background');

let reads = 0, heightWrites = 0, frameId = 0;
const frames = new Map();
globalThis.requestAnimationFrame = callback => { frames.set(++frameId, callback); return frameId; };
globalThis.cancelAnimationFrame = id => frames.delete(id);
globalThis.composerState = { selectedRoomId: 'room-A', composerContext: null };
globalThis.composerInput = { value: '', style: { set height(value) { heightWrites++; } }, get scrollHeight() { reads++; return 90; } };
let submissions = 0;
globalThis.composerForm = { requestSubmit() { submissions++; } };
globalThis.writeDraft = (room, value) => writer.queue(room, value);
const names = ['cancelChatComposerContextButton', 'chatAttachmentButton', 'chatComposerContext', 'chatComposerContextCopy',
  'chatComposerContextTitle', 'chatMessageList', 'chatNewMessagesButton', 'chatRoom',
  'closeMessageActionButton', 'getChatDraft', 'getDisplayName', 'messageActionDialog', 'messageActionReactions',
  'messageActionStatus', 'messageActionSummary', 'messageReadMenu', 'messageReadMenuCopy', 'messageReadMenuTitle',
  'requestAction', 'setAppStatus'];
const result = await build({ entryPoints: ['frontend/src/app/message-interactions.js'], bundle: true, write: false,
  format: 'esm', platform: 'node', plugins: [{ name: 'core-fixture', setup(builder) {
    builder.onResolve({ filter: /\/core\.js$/ }, () => ({ path: 'core', namespace: 'fixture' }));
    builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents:
      `export const state=globalThis.composerState, chatMessageInput=globalThis.composerInput, chatMessageForm=globalThis.composerForm;
       export const setChatDraft=globalThis.writeDraft, registerCoreHooks=()=>{};
       ${names.map(name => `export const ${name}=()=>{};`).join('\n')}` }));
  } }] });
const module = await import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`);
for (let index = 0; index < 100; index++) {
  composerInput.value = `한글 입력 ${index}`;
  module.handleComposerInput();
}
assert.equal(reads, 0, 'no forced layout during input handlers');
assert.equal(frames.size, 1, 'burst inputs coalesce to one animation frame');
const [id, callback] = frames.entries().next().value; frames.delete(id); callback();
assert.equal(reads, 1); assert.equal(heightWrites, 2);
module.handleComposerInput();
module.syncComposerHeight();
assert.equal(frames.size, 0, 'explicit send/room changes cancel stale resize work');
for (const options of [{isComposing:true}, {keyCode:229}, {shiftKey:true}]) {
  module.handleComposerKeydown({key:'Enter', preventDefault() {}, ...options});
}
assert.equal(submissions, 0, 'Korean composition confirmation and Shift+Enter do not send');
module.handleComposerKeydown({key:'Enter', preventDefault() {}});
assert.equal(submissions, 1);
writer.flush();
console.log('Composer: 100 inputs -> 0 synchronous storage/layout calls, 1 write and 1 frame; ID keys and cancellation OK');
