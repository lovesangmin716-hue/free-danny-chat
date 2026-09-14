// Optional PostgreSQL/WASM integration test. PGLITE_MODULE may point to a
// separately installed @electric-sql/pglite/dist/index.js file URL.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
const { PGlite } = await import(process.env.PGLITE_MODULE || "@electric-sql/pglite");
const db = new PGlite();
try {
  await db.exec(`create role anon; create role authenticated; create role service_role;
    create table accounts(id text primary key,status text);
    create table users(id text primary key,account_id text references accounts(id),data jsonb);
    insert into accounts values('a','active');
    insert into users values('actor','a','{}'),('peer','a','{}');`);
  const base = await readFile(new URL("../src/colorless/database/supabase-schema.sql", import.meta.url), "utf8");
  const helper = base.match(/create or replace function public\.colorless_lock_active_identity\(actor_user_id text\)[\s\S]*?\$\$;/)[0];
  await db.exec(helper);
  const migration = await readFile(new URL("../src/colorless/database/threads-schema.sql", import.meta.url), "utf8");
  await db.exec(migration); await db.exec(migration);
  const commit = async (actor, revision, mutations) => (await db.query(
    "select colorless_threads_commit($1,$2,$3::jsonb) as result", [actor, revision, JSON.stringify(mutations)])).rows[0].result;
  const post = { id: "post", author_identity_id: "actor", parent_id: null, root_id: null, body: "Test", visibility: "public", client_id: "once", created_at: "now", edited_at: "", deleted: 0 };
  assert.deepEqual(await commit("actor", 0, [{ table: "thread_posts", row: post }]), { revision: 1 });
  assert.deepEqual(await commit("actor", 0, []), { error: "conflict" });
  assert.deepEqual(await commit("missing", 1, []), { error: "forbidden" });
  const edges = { actor_identity_id: "actor", target_identity_id: "peer", kind: "follow", created_at: "now" };
  const like = { actor_identity_id: "peer", post_id: "post", created_at: "now" };
  const notice = { id: "n", recipient_identity_id: "peer", actor_identity_id: "actor", post_id: "post", kind: "mention", created_at: "now", read_at: "" };
  const report = { id: "r", actor_identity_id: "peer", post_id: "post", reason: "Test report", created_at: "now" };
  const batch = [{ table: "thread_edges", row: edges }, { table: "thread_likes", row: like }, { table: "thread_notifications", row: notice }, { table: "thread_reports", row: report }];
  assert.deepEqual(await commit("actor", 1, batch), { revision: 2 });
  assert.deepEqual(await commit("actor", 2, batch), { revision: 3 });
  assert.equal(Number((await db.query("select like_count from thread_post_summary where id='post'")).rows[0].like_count), 1);
  await assert.rejects(commit("actor", 3, [{ table: "thread_posts", row: { ...post, body: "Must roll back" } }, { table: "accounts", row: {} }]), /invalid mutation table/);
  assert.equal((await db.query("select body from thread_posts")).rows[0].body, "Test");
  assert.deepEqual(await commit("actor", 3, [{ table: "thread_edges", row: edges, remove: true }, { table: "thread_likes", row: like, remove: true }, { table: "thread_notifications", row: { ...notice, read_at: "read" } }]), { revision: 4 });
  assert.equal((await db.query("select * from thread_edges")).rows.length, 0);
  assert.equal((await db.query("select read_at from thread_notifications")).rows[0].read_at, "read");
  await db.exec("update accounts set status='suspended'");
  assert.deepEqual(await commit("actor", 4, []), { error: "forbidden" });
  for (const role of ["anon", "authenticated"]) {
    await db.exec(`set role ${role}`);
    await assert.rejects(db.query("select * from thread_posts"), /permission denied/);
    await assert.rejects(db.query("select * from thread_post_summary"), /permission denied/);
    await assert.rejects(commit("actor", 4, []), /permission denied/);
    await db.exec("reset role");
  }
  console.log("PostgreSQL Threads migration rerun, all mutation types, rollback, CAS, suspension and grants OK");
} finally { await db.close(); }
