import assert from "node:assert/strict";

globalThis.window = { location: new URL("https://chat.example/app") };

const requests = [];
globalThis.fetch = async (url, options) => {
  const headers = options.headers;
  requests.push({ url, etag: headers.get("If-None-Match") || "" });
  if (requests.length === 1) {
    return new Response(JSON.stringify({ items: [{ id: "room-1" }] }), {
      status: 200,
      headers: {
        "Cache-Control": "private, no-cache",
        "Content-Type": "application/json",
        ETag: '"rooms-v1"',
      },
    });
  }
  if (requests.length === 2) {
    assert.equal(headers.get("If-None-Match"), '"rooms-v1"');
    return new Response(null, { status: 304, headers: { ETag: '"rooms-v1"' } });
  }
  assert.equal(headers.has("If-None-Match"), false);
  return new Response(JSON.stringify({ items: [{ id: "room-2" }] }), {
    status: 200,
    headers: {
      "Cache-Control": "private, no-cache",
      "Content-Type": "application/json",
      ETag: '"rooms-v2"',
    },
  });
};

const { createHttpClient } = await import("../frontend/src/app/platform/http.js");
const client = createHttpClient();
const initial = await client.request("/rooms?limit=30");
const revalidated = await client.request("/rooms?limit=30");
assert.deepEqual(revalidated, initial);

client.clearCache();
const refreshed = await client.request("/rooms?limit=30");
assert.deepEqual(refreshed, { items: [{ id: "room-2" }] });
assert.deepEqual(requests.map((request) => request.etag), ["", '"rooms-v1"', ""]);

console.log("HTTP ETag cache revalidation passed");

const actorRequests = [];
globalThis.fetch = async (url, options) => {
  const actor = options.headers.get("X-Acting-Identity");
  const etag = options.headers.get("If-None-Match");
  actorRequests.push({ actor, etag });
  if (etag) return new Response(null, { status: 304 });
  return new Response(JSON.stringify({ actor }), {
    status: 200, headers: { "Content-Type": "application/json", ETag: `"${actor}"` },
  });
};
const actorClient = createHttpClient();
for (const actor of ["id-a", "id-b", "id-a"]) {
  const result = await actorClient.request("/rooms", { headers: { "X-Acting-Identity": actor } });
  assert.equal(result.actor, actor);
}
assert.deepEqual(actorRequests.map(({ etag }) => etag), [null, null, '"id-a"']);
console.log("HTTP actor-specific cache isolation passed");
