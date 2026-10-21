import { requestAction, state } from "./core.js";
import { loadDraft, saveDraft, finishDraft, queueDraft, flushDrafts } from "./platform/thread-drafts.js";
import { openThreadDialog, closeThreadDialog } from "./platform/thread-dialog.js";

const el = (tag, text = "") => { const node = document.createElement(tag); node.textContent = text; return node; };
const button = (text, action) => { const node = el("button", text); node.type = "button"; node.addEventListener("click", action); return node; };
const option = (value, text) => { const node = el("option", text); node.value = value; return node; };
let panel, actor, mode = "feed", generation = 0, parent = "", root = "", cursor = "", draft, busy = false;
let actorSelect, scopeSelect, modeSelect, search, editor, status, list, more, composer, visibility, writer;
let pendingRefresh = false;
let unreadSelect, readVisible, counter, submitButton, backToFeed;
const visibleUnread = new Map();
const identities = () => state.session?.identities || [];
const actorName = (id) => identities().find((identity) => identity.id === id)?.username || id;
const isVisible = () => panel && !panel.classList.contains("hidden") && state.activeList === "threads" && state.session?.user;
const isCurrent = (version) => version === generation && isVisible();
const draftStorage = () => { try { return window.localStorage; } catch (_) { return null; } };

function api(action, payload = {}, writing = false, identity = actor) {
  const url = `/threads/api/${action}`;
  return requestAction(`threads.${action}`, writing ? url : `${url}?${new URLSearchParams(payload)}`, {
    headers: { "X-Acting-Identity": identity },
    ...(writing ? { method: "POST", body: JSON.stringify(payload) } : {}),
  });
}
function persist() { if (draft && editor && actor) { draft.body = editor.value; draft.visibility = visibility.value; saveDraft(draftStorage(), actor, parent, draft); } }
function syncSubmit() { if (submitButton) submitButton.disabled = busy || !editor.value.trim(); }
function onDraftInput() {
  if (!draft || !actor) return;
  draft.body = editor.value; draft.visibility = visibility.value;
  queueDraft(draftStorage(), actor, parent, draft);
  counter.textContent = `${editor.value.length}/2,000자${parent ? " · 선택한 글에 답글" : ""}`;
  syncSubmit();
}
function restore() {
  draft = loadDraft(draftStorage(), actor, parent);
  editor.value = draft.body;
  visibility.value = draft.visibility === "followers" ? "followers" : "public";
  writer.textContent = `@${actorName(actor)}로 ${parent ? "답글" : "게시물"} 작성 중`;
  visibility.hidden = Boolean(parent);
  counter.textContent = `${editor.value.length}/2,000자${parent ? " · 선택한 글에 답글" : ""}`;
  syncSubmit();
}
function chooseReply(id, identity) {
  persist(); actor = identity; actorSelect.value = identity; parent = id; restore(); editor.focus();
}
async function mutate(action, payload, identity = actor) {
  if (busy) return;
  busy = true; syncSubmit();
  const version = generation;
  try { await api(action, payload, true, identity); if (isCurrent(version)) { status.textContent = "반영했습니다."; await load(); } }
  catch (error) { if (isCurrent(version)) status.textContent = error.message; }
  finally { busy = false; syncSubmit(); }
}
function postDialog(post, action) {
  if (busy) return;
  const identity = post.viewer_identity_id, version = generation;
  const editing = action === "edit", reporting = action === "report";
  const settings = editing ? { title: `@${actorName(identity)}의 내용 수정`, value: post.body, confirmLabel: "수정 저장" }
    : reporting ? { title: `@${actorName(identity)}로 신고`, value: "", minLength: 5, maxLength: 500, confirmLabel: "신고하기" }
    : { title: action === "delete" ? "게시물 삭제" : "활동 ID 차단", confirmLabel: action === "delete" ? "삭제하기" : "차단하기" };
  openThreadDialog({ ...settings, description: editing ? "수정한 내용은 작성 ID로 저장됩니다. Ctrl/⌘+Enter로 저장할 수 있어요."
    : reporting ? "신고 사유를 5~500자로 입력해 주세요. 사유는 공개되지 않습니다."
    : action === "delete" ? "내용이 지워지고 기존 답글의 연결은 유지됩니다."
    : `@${actorName(identity)}에서 @${post.author.username}를 차단합니다. 다른 소유 ID에는 전파되지 않습니다.`,
    onConfirm: async value => {
      busy = true; syncSubmit();
      try {
        const payload = editing ? { id: post.id, body: value, originalBody: post.body }
          : reporting ? { id: post.id, reason: value }
          : action === "delete" ? { id: post.id } : { identityId: post.author_identity_id, enabled: true };
        await api(action, payload, true, identity);
        if (isCurrent(version)) { status.textContent = "반영했습니다."; void load(); }
      } finally { busy = false; syncSubmit(); }
    },
  });
}
function card(post) {
  const node = el("article");
  node.className = "thread-card";
  node.id = `thread-${post.id}`;
  const identity = post.viewer_identity_id;
  node.append(el("strong", `@${post.author.username}`), el("small", `${post.visibility === "followers" ? "팔로워 공개" : "전체 공개"} · ${new Date(post.created_at).toLocaleString()}${post.edited_at ? " · 수정됨" : ""}`));
  if (post.parent_id) node.append(el("small", post.reply_to
    ? `@${post.reply_to.author.username}에게 답글: ${post.reply_to.deleted ? "삭제된 내용" : post.reply_to.body}`
    : "열람할 수 없는 글에 대한 답글"));
  node.append(el("p", post.deleted ? "삭제된 내용입니다." : post.body));
  node.append(el("small", `이 카드의 활동 ID: @${actorName(identity)}`));
  const actions = el("div"); actions.className = "thread-actions";
  if (!post.deleted) {
    actions.append(button(`${post.liked ? "♥ 취소" : "♡ 좋아요"} ${post.like_count}`, () => mutate("like", { id: post.id, enabled: !post.liked }, identity)));
    actions.append(button(`답글 ${post.reply_count}`, async () => {
      if (mode !== "detail") { persist(); actor = identity; actorSelect.value = identity; root = post.root_id || post.id; mode = "detail"; parent = post.id; restore(); await load(); }
      else chooseReply(post.id, identity);
    }));
    if (post.author_identity_id === identity) {
      actions.append(button("수정", () => postDialog(post, "edit")));
      actions.append(button("삭제", () => postDialog(post, "delete")));
    } else {
      actions.append(button(post.following ? "팔로우 취소" : "팔로우", () => mutate("follow", { identityId: post.author_identity_id, enabled: !post.following }, identity)));
      actions.append(button("차단", () => postDialog(post, "block")));
      actions.append(button("신고", () => postDialog(post, "report")));
    }
  }
  node.append(actions); return node;
}
function notification(item) {
  const node = el("article"); node.className = "thread-card";
  const names = { mention: "멘션", reply: "답글", like: "좋아요", follow: "팔로우" };
  if (!item.read_at) visibleUnread.set(item.id, item.recipient_identity_id);
  node.append(el("strong", `@${item.actor.username} · ${names[item.kind]}`), el("small", `받은 ID: @${item.recipient.username} · ${item.read_at ? "읽음" : "안 읽음"}`), el("p", item.post_body));
  if (!item.read_at) node.append(button("이 ID에서 읽음 처리", () => mutate("read", { id: item.id }, item.recipient_identity_id)));
  if (item.post_id) node.append(button("대화 보기", async () => { persist(); actor = item.recipient_identity_id; actorSelect.value = actor; root = item.post_id; parent = root; mode = "detail"; restore(); await load(); }));
  return node;
}
async function readShown() {
  if (busy || !visibleUnread.size) return;
  busy = true; readVisible.disabled = true;
  syncSubmit();
  const version = generation, groups = new Map();
  for (const [id, recipient] of visibleUnread) groups.set(recipient, [...(groups.get(recipient) || []), id]);
  try {
    for (const [recipient, ids] of groups) for (let i = 0; i < ids.length; i += 40) {
      if (!isCurrent(version)) return;
      await api("read_many", { ids: ids.slice(i, i + 40) }, true, recipient);
    }
    if (isCurrent(version)) await load();
  } catch (error) { if (isCurrent(version)) status.textContent = error.message; }
  finally { busy = false; syncSubmit(); readVisible.disabled = !visibleUnread.size; }
}
async function load(append = false) {
  if (pendingRefresh && append) return;
  const version = ++generation;
  pendingRefresh = true;
  more.disabled = true;
  if (!append) { cursor = ""; list.replaceChildren(); visibleUnread.clear(); }
  readVisible.hidden = unreadSelect.hidden = mode !== "notifications";
  readVisible.disabled = true;
  status.textContent = "불러오는 중…";
  composer.hidden = !["feed", "detail"].includes(mode);
  backToFeed.hidden = mode !== "detail";
  try {
    const action = mode === "blocked" || mode === "following-list" ? "relationships" : mode;
    const data = await api(action, { scope: scopeSelect.value, mode: modeSelect.value, q: search.value, id: root, cursor, unread: unreadSelect.value, kind: mode === "blocked" ? "block" : "follow" });
    if (!isCurrent(version)) return;
    if (data.post && !append) { root = data.post.id; list.append(card(data.post)); }
    if (data.focused_reply && !append) { list.append(el("strong", "선택한 답글"), card(data.focused_reply)); }
    for (const item of data.items) {
      if (mode === "notifications") list.append(notification(item));
      else if (["people", "blocked", "following-list"].includes(mode)) {
        const node = el("article"); node.className = "thread-card";
        const identity = actor;
        node.append(el("strong", `@${item.username} · ${item.display_name}`));
        if (item.id !== identity) node.append(button(mode === "blocked" ? "이 ID의 차단 해제" : mode === "following-list" ? "팔로우 취소" : "팔로우", () => mutate(mode === "blocked" ? "block" : "follow", { identityId: item.id, enabled: mode === "people" }, identity)));
        list.append(node);
      } else if (!document.getElementById(`thread-${item.id}`)) list.append(card(item));
    }
    cursor = data.next_cursor;
    more.hidden = !cursor;
    status.textContent = list.childElementCount ? "" : cursor ? "이 구간에 표시할 내용이 없습니다. 더 보기를 눌러 주세요." : "표시할 내용이 없습니다.";
  } catch (error) { if (isCurrent(version)) status.textContent = error.message; }
  finally { if (version === generation) { more.disabled = false; pendingRefresh = false; readVisible.disabled = !visibleUnread.size; } }
}
async function publish(event) {
  event.preventDefault(); if (busy || !editor.value.trim()) return;
  persist(); busy = true;
  const version = generation, identity = actor, replyTo = parent, savedDraft = { ...draft };
  const submit = composer.querySelector('button[type="submit"]'); submit.disabled = true;
  try {
    await api("create", { body: savedDraft.body, clientId: savedDraft.clientId, parentId: replyTo, visibility: savedDraft.visibility }, true, identity);
    // Do not erase text edited or an ID switched while the request was in flight.
    finishDraft(draftStorage(), identity, replyTo, savedDraft);
    if (isVisible() && actor === identity && parent === replyTo) { restore(); void load(); }
  } catch (error) { if (isCurrent(version)) status.textContent = error.message; }
  finally { busy = false; syncSubmit(); }
}
function selectMode(value) { persist(); mode = value; parent = ""; root = ""; restore(); void load(); }
export function openThreads() {
  if (!state.session?.user) return;
  if (isVisible()) return;
  if (!panel) {
    panel = document.getElementById("threads-panel");
    actorSelect = el("select"); actorSelect.setAttribute("aria-label", "스레드 활동 ID");
    actorSelect.addEventListener("change", () => { persist(); actor = actorSelect.value; restore(); void load(); });
    scopeSelect = el("select"); scopeSelect.setAttribute("aria-label", "피드와 알림 범위"); scopeSelect.append(option("all", "모든 내 ID 통합"), option("identity", "선택한 ID만")); scopeSelect.addEventListener("change", () => void load());
    modeSelect = el("select"); modeSelect.setAttribute("aria-label", "피드 종류"); modeSelect.append(option("public", "전체 피드"), option("following", "팔로잉 피드")); modeSelect.addEventListener("change", () => selectMode("feed"));
    const filters = el("div"); filters.className = "thread-toolbar"; filters.append(actorSelect, scopeSelect, modeSelect);
    const nav = el("div"); nav.className = "thread-toolbar";
    for (const [value, name] of [["feed", "피드"], ["notifications", "알림함"], ["following-list", "팔로잉 관리"], ["blocked", "차단 관리"]]) nav.append(button(name, () => selectMode(value)));
    nav.append(button("새로고침", () => void load()));
    backToFeed = button("← 피드로 돌아가기", () => selectMode("feed")); backToFeed.hidden = true; nav.append(backToFeed);
    unreadSelect = el("select"); unreadSelect.setAttribute("aria-label", "알림 읽음 필터");
    unreadSelect.append(option("0", "모든 알림"), option("1", "안 읽은 알림만")); unreadSelect.addEventListener("change", () => void load());
    readVisible = button("표시된 알림 읽음", readShown); nav.append(unreadSelect, readVisible);
    search = el("input"); search.placeholder = "게시물 검색 / 정확한 @사용자명"; search.maxLength = 100; search.setAttribute("aria-label", "스레드 검색");
    search.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault(); selectMode(mode === "people" ? "people" : "feed");
      }
    });
    nav.append(search, button("게시물 검색", () => selectMode("feed")), button("사용자 찾기", () => selectMode("people")));
    composer = el("form"); composer.addEventListener("submit", publish);
    writer = el("strong"); editor = el("textarea"); editor.maxLength = 2000; editor.setAttribute("aria-label", "스레드 내용"); editor.placeholder = "무슨 생각을 하고 있나요? @사용자명으로 멘션할 수 있어요."; editor.addEventListener("input", onDraftInput);
    editor.setAttribute("aria-keyshortcuts", "Control+Enter Meta+Enter");
    editor.addEventListener("keydown", event => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault(); if (!submitButton.disabled) composer.requestSubmit();
      }
    });
    counter = el("small");
    visibility = el("select"); visibility.setAttribute("aria-label", "게시물 공개 범위"); visibility.append(option("public", "전체 공개"), option("followers", "팔로워 공개"));
    visibility.addEventListener("change", persist);
    submitButton = el("button", "게시하기"); submitButton.type = "submit";
    composer.append(writer, editor, counter, el("small", "Ctrl/⌘+Enter로 게시 · Enter로 줄바꿈"), visibility, submitButton, button("새 게시물 작성", () => selectMode("feed")));
    status = el("p"); status.setAttribute("role", "status"); list = el("section"); list.setAttribute("aria-label", "스레드 목록"); more = button("더 보기", () => void load(true));
    panel.append(filters, nav, composer, status, list, more);
  }
  actor = state.session.active_identity_id; actorSelect.replaceChildren(...identities().map((identity) => option(identity.id, `@${identity.username}`))); actorSelect.value = actor;
  parent = ""; root = ""; mode = "feed"; restore(); panel.classList.remove("hidden"); void load();
}
export function closeThreads() {
  closeThreadDialog();
  if (!panel || panel.classList.contains("hidden")) return;
  persist(); generation++; pendingRefresh = false;
  panel.classList.add("hidden"); list.replaceChildren(); editor.value = ""; draft = null;
}
// Refresh only an idle notification inbox; never overwrite an active draft.
window.setInterval(() => { if (isVisible() && mode === "notifications" && !document.hidden && !pendingRefresh && !busy && !cursor) void load(); }, 20000);
window.addEventListener("pagehide", flushDrafts);
document.addEventListener("visibilitychange", () => { if (document.hidden) flushDrafts(); });
