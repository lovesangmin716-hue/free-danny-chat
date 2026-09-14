import { requestAction, state } from "./core.js";
import { loadDraft, saveDraft, finishDraft } from "./platform/thread-drafts.js";

const el = (tag, text = "") => { const node = document.createElement(tag); node.textContent = text; return node; };
const button = (text, action) => { const node = el("button", text); node.type = "button"; node.addEventListener("click", action); return node; };
const option = (value, text) => { const node = el("option", text); node.value = value; return node; };
let panel, actor, mode = "feed", generation = 0, parent = "", root = "", cursor = "", draft, busy = false;
let actorSelect, scopeSelect, modeSelect, search, editor, status, list, more, composer, visibility, writer;
let pendingRefresh = false;
const identities = () => state.session?.identities || [];
const actorName = (id) => identities().find((identity) => identity.id === id)?.username || id;
const isCurrent = (version) => version === generation && panel?.open && state.session?.user;
const draftStorage = () => { try { return window.localStorage; } catch (_) { return null; } };

function api(action, payload = {}, writing = false, identity = actor) {
  const url = `/threads/api/${action}`;
  return requestAction(`threads.${action}`, writing ? url : `${url}?${new URLSearchParams(payload)}`, {
    headers: { "X-Acting-Identity": identity },
    ...(writing ? { method: "POST", body: JSON.stringify(payload) } : {}),
  });
}
function persist() { if (draft && editor && actor) { draft.body = editor.value; draft.visibility = visibility.value; saveDraft(draftStorage(), actor, parent, draft); } }
function restore() {
  draft = loadDraft(draftStorage(), actor, parent);
  editor.value = draft.body;
  visibility.value = draft.visibility === "followers" ? "followers" : "public";
  writer.textContent = `@${actorName(actor)}로 ${parent ? "답글" : "게시물"} 작성 중`;
  visibility.hidden = Boolean(parent);
}
function chooseReply(id, identity) {
  persist(); actor = identity; actorSelect.value = identity; parent = id; restore(); editor.focus();
}
async function mutate(action, payload, identity = actor) {
  if (busy) return;
  busy = true;
  const version = generation;
  try { await api(action, payload, true, identity); if (isCurrent(version)) { status.textContent = "반영했습니다."; await load(); } }
  catch (error) { if (isCurrent(version)) status.textContent = error.message; }
  finally { busy = false; }
}
function card(post) {
  const node = el("article");
  node.className = "thread-card";
  const identity = post.viewer_identity_id;
  node.append(el("strong", `@${post.author.username}`), el("small", `${post.visibility === "followers" ? "팔로워 공개" : "전체 공개"} · ${new Date(post.created_at).toLocaleString()}${post.edited_at ? " · 수정됨" : ""}`));
  if (post.parent_id) node.append(el("small", `답글 · ${post.parent_id === root ? "원문에 답글" : "댓글에 답글"}`));
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
      actions.append(button("수정", () => { const body = window.prompt(`@${actorName(identity)}의 내용 수정`, post.body); if (body !== null) void mutate("edit", { id: post.id, body }, identity); }));
      actions.append(button("삭제", () => { if (window.confirm(`@${actorName(identity)}로 작성한 이 내용을 삭제할까요? 내용은 비워지고 답글 연결은 유지됩니다.`)) void mutate("delete", { id: post.id }, identity); }));
    } else {
      actions.append(button(post.following ? "팔로우 취소" : "팔로우", () => mutate("follow", { identityId: post.author_identity_id, enabled: !post.following }, identity)));
      actions.append(button("차단", () => { if (window.confirm(`@${actorName(identity)}에서만 @${post.author.username}를 차단할까요? 다른 소유 ID에는 전파되지 않습니다.`)) void mutate("block", { identityId: post.author_identity_id, enabled: true }, identity); }));
      actions.append(button("신고", () => { const reason = window.prompt(`@${actorName(identity)}로 신고합니다. 사유를 5자 이상 입력해 주세요.`); if (reason !== null) void mutate("report", { id: post.id, reason }, identity); }));
    }
  }
  node.append(actions); return node;
}
function notification(item) {
  const node = el("article"); node.className = "thread-card";
  const names = { mention: "멘션", reply: "답글", like: "좋아요", follow: "팔로우" };
  node.append(el("strong", `@${item.actor.username} · ${names[item.kind]}`), el("small", `받은 ID: @${item.recipient.username} · ${item.read_at ? "읽음" : "안 읽음"}`), el("p", item.post_body));
  if (!item.read_at) node.append(button("이 ID에서 읽음 처리", () => mutate("read", { id: item.id }, item.recipient_identity_id)));
  if (item.post_id) node.append(button("대화 보기", async () => { persist(); actor = item.recipient_identity_id; actorSelect.value = actor; root = item.post_id; parent = root; mode = "detail"; restore(); await load(); }));
  return node;
}
async function load(append = false) {
  if (pendingRefresh && append) return;
  const version = ++generation;
  pendingRefresh = true;
  more.disabled = true;
  if (!append) { cursor = ""; list.replaceChildren(); }
  status.textContent = "불러오는 중…";
  composer.hidden = !["feed", "detail"].includes(mode);
  try {
    const action = mode === "blocked" || mode === "following-list" ? "relationships" : mode;
    const data = await api(action, { scope: scopeSelect.value, mode: modeSelect.value, q: search.value, id: root, cursor, kind: mode === "blocked" ? "block" : "follow" });
    if (!isCurrent(version)) return;
    if (data.post && !append) { root = data.post.id; list.append(card(data.post)); }
    for (const item of data.items) {
      if (mode === "notifications") list.append(notification(item));
      else if (["people", "blocked", "following-list"].includes(mode)) {
        const node = el("article"); node.className = "thread-card";
        const identity = actor;
        node.append(el("strong", `@${item.username} · ${item.display_name}`));
        if (item.id !== identity) node.append(button(mode === "blocked" ? "이 ID의 차단 해제" : mode === "following-list" ? "팔로우 취소" : "팔로우", () => mutate(mode === "blocked" ? "block" : "follow", { identityId: item.id, enabled: mode === "people" }, identity)));
        list.append(node);
      } else list.append(card(item));
    }
    cursor = data.next_cursor;
    more.hidden = !cursor;
    status.textContent = list.childElementCount ? "" : cursor ? "이 구간에 표시할 내용이 없습니다. 더 보기를 눌러 주세요." : "표시할 내용이 없습니다.";
  } catch (error) { if (isCurrent(version)) status.textContent = error.message; }
  finally { if (version === generation) { more.disabled = false; pendingRefresh = false; } }
}
async function publish(event) {
  event.preventDefault(); if (busy) return;
  persist(); busy = true;
  const version = generation, identity = actor, replyTo = parent, savedDraft = { ...draft };
  const submit = composer.querySelector('button[type="submit"]'); submit.disabled = true;
  try {
    await api("create", { body: savedDraft.body, clientId: savedDraft.clientId, parentId: replyTo, visibility: savedDraft.visibility }, true, identity);
    // Do not erase text edited or an ID switched while the request was in flight.
    finishDraft(draftStorage(), identity, replyTo, savedDraft);
    if (panel?.open && state.session?.user && actor === identity && parent === replyTo) { restore(); await load(); }
  } catch (error) { if (isCurrent(version)) status.textContent = error.message; }
  finally { busy = false; submit.disabled = false; }
}
function selectMode(value) { persist(); mode = value; parent = ""; root = ""; restore(); void load(); }
function openThreads() {
  if (!state.session?.user) return;
  if (!panel) {
    const style = el("style"); style.textContent = ".thread-panel{width:min(850px,92vw);height:88vh;box-sizing:border-box;overflow:auto;padding:18px}.thread-panel::backdrop{background:#0008}.thread-panel small{display:block;color:#555}.thread-panel select,.thread-panel input{min-width:0;max-width:100%;padding:8px}.thread-toolbar,.thread-actions{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.thread-panel button{width:auto;min-height:32px;padding:6px 10px;font-size:13px}.thread-card{border:1px solid #aaa;padding:14px;margin:12px 0;overflow-wrap:anywhere}.thread-card p{white-space:pre-wrap}.thread-panel textarea{width:100%;box-sizing:border-box;min-height:100px;padding:10px}.thread-panel form{border-bottom:1px solid #bbb;padding-bottom:12px}";
    document.head.append(style);
    panel = el("dialog"); panel.id = "threads-panel"; panel.className = "thread-panel"; panel.setAttribute("aria-label", "스레드");
    const heading = el("div"); heading.className = "thread-toolbar"; heading.append(el("h2", "스레드"), button("닫기", () => panel.close()));
    actorSelect = el("select"); actorSelect.setAttribute("aria-label", "스레드 활동 ID");
    actorSelect.addEventListener("change", () => { persist(); actor = actorSelect.value; restore(); void load(); });
    scopeSelect = el("select"); scopeSelect.setAttribute("aria-label", "피드와 알림 범위"); scopeSelect.append(option("all", "모든 내 ID 통합"), option("identity", "선택한 ID만")); scopeSelect.addEventListener("change", () => void load());
    modeSelect = el("select"); modeSelect.setAttribute("aria-label", "피드 종류"); modeSelect.append(option("public", "전체 피드"), option("following", "팔로잉 피드")); modeSelect.addEventListener("change", () => selectMode("feed"));
    const filters = el("div"); filters.className = "thread-toolbar"; filters.append(actorSelect, scopeSelect, modeSelect);
    const nav = el("div"); nav.className = "thread-toolbar";
    for (const [value, name] of [["feed", "피드"], ["notifications", "알림함"], ["following-list", "팔로잉 관리"], ["blocked", "차단 관리"]]) nav.append(button(name, () => selectMode(value)));
    nav.append(button("새로고침", () => void load()));
    search = el("input"); search.placeholder = "게시물 검색 / 정확한 @사용자명"; search.maxLength = 100; search.setAttribute("aria-label", "스레드 검색");
    nav.append(search, button("게시물 검색", () => selectMode("feed")), button("사용자 찾기", () => selectMode("people")));
    composer = el("form"); composer.addEventListener("submit", publish);
    writer = el("strong"); editor = el("textarea"); editor.maxLength = 2000; editor.setAttribute("aria-label", "스레드 내용"); editor.placeholder = "무슨 생각을 하고 있나요? @사용자명으로 멘션할 수 있어요."; editor.addEventListener("input", persist);
    visibility = el("select"); visibility.setAttribute("aria-label", "게시물 공개 범위"); visibility.append(option("public", "전체 공개"), option("followers", "팔로워 공개"));
    visibility.addEventListener("change", persist);
    const submit = el("button", "게시하기"); submit.type = "submit";
    composer.append(writer, editor, visibility, submit, button("새 게시물 작성", () => { persist(); parent = ""; restore(); }));
    status = el("p"); status.setAttribute("role", "status"); list = el("section"); list.setAttribute("aria-label", "스레드 목록"); more = button("더 보기", () => void load(true));
    panel.append(heading, filters, nav, composer, status, list, more); document.body.append(panel);
    panel.addEventListener("close", () => { persist(); generation++; list.replaceChildren(); editor.value = ""; draft = null; });
  }
  actor = state.session.active_identity_id; actorSelect.replaceChildren(...identities().map((identity) => option(identity.id, `@${identity.username}`))); actorSelect.value = actor;
  parent = ""; root = ""; mode = "feed"; restore(); panel.showModal(); void load();
}
const entry = button("스레드 · 게시물과 알림", openThreads);
document.getElementById("my-view").append(entry);
// Refresh only an idle notification inbox; never overwrite an active draft.
window.setInterval(() => { if (panel?.open && mode === "notifications" && !document.hidden && !pendingRefresh && !busy && !cursor) void load(); }, 20000);
