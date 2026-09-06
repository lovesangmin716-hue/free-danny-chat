# Colorless architecture

Colorless uses one action pipeline on the client and one command pipeline on the server. Feature code owns product rules; platform code owns transport, lifecycle, state notifications, and event delivery.

## Client flow

```text
view intent
  -> feature function
  -> requestAction(name, request)
  -> action pipeline
  -> HTTP client
  -> feature state update
  -> render
```

`frontend/src/app/entrypoints/main.js` is the source entrypoint. It imports the feature graph as native ES Modules; every cross-file dependency is an explicit `import`/`export` binding rather than a shared global or an HTML script-order contract. esbuild resolves that graph into the minified production entrypoint `src/colorless/web/assets/js/main.js`. Only generated browser artifacts are included in the Python package, leaving the source graph available for future web, desktop, and mobile clients without serving it in production.

- `platform/store.js`: named state transactions and subscriptions
- `platform/http.js`: JSON parsing and normalized HTTP errors
- `platform/pipeline.js`: action lifecycle, deduplication, commit, effects, and failures
- `platform/events.js`: SSE transport plus typed realtime event registration and dispatch
- `platform/icons.js`: accessible SVG controls shared by static and dynamic views
- `platform/image-processing.js`: cancellable Worker boundary whose fingerprinted URL is injected during the build
- `core.js`: shared state, DOM references, API actions, and registered lifecycle hooks
- `action-bar.js`: tab-scoped chat, friend, notification, identity visibility, and input modes

Feature modules must not call `fetch` directly or publish new browser globals. Network work goes through `requestAction`, SSE messages go through `realtimeEvents`, and dependencies cross files only through module imports. Core-to-feature callbacks use `registerCoreHooks`, keeping the core independent from feature implementations and avoiding cyclic initialization.

## Server flow

```text
HTTP route
  -> authentication and request limits
  -> run_json_command
  -> ApplicationServices
  -> StateStore
  -> CommandOutcome(data, status, events)
  -> response
  -> event delivery
```

`ApplicationServices` contains feature commands without writing HTTP responses. A command either raises `CommandFailure` or returns `CommandOutcome`. This keeps validation, persistence, response status, and emitted events in one explicit result.

Binary upload handlers retain specialized parsing, but use the same stores and event contracts after validation.

### Python module boundaries

- `config.py`: environment loading, limits, security policy, and immutable application constants
- `web_resources.py`: packaged HTML/assets, fingerprints, compression caches, and content negotiation
- `observability.py`: request/SSE metrics, safe route labels, process probes, and structured logging
- `utils.py`: identifiers, cursors, profile/image validation, cookies, passwords, and phone normalization
- `runtime.py`: bounded in-memory session, upload grant, rate-limit, presence, OAuth, and verification stores
- `cache.py`: bounded single-flight TTL cache
- `integrations.py`: pooled outbound HTTP plus shared Supabase request headers
- `persistence.py`: normalized SQLite and Supabase repositories
- `state.py`: state indexes, normalized repository coordination, and chat domain persistence boundary
- `application.py`: feature commands and explicit `CommandOutcome` domain events
- `realtime.py`: durable event outbox, replay, cross-instance consumption, and presence cleanup
- `http/auth.py`: local/social authentication and phone verification routes
- `http/messaging.py`: session, profile, room, message, presence, and SSE routes
- `http/uploads.py`: profile/room images and attachment transfer routes
- `http/context.py`: live composition-root dependency view used by route mixins
- `server.py`: composition root, shared HTTP transport/dispatch, and process lifecycle

Lower-level modules do not import `server.py`. Route mixins receive a live `HandlerContext` view of the composition root, so tests and deployments can replace runtime collaborators without circular imports. `StateStore` receives presence through `bind_presence`, application services receive stores through their constructor, and the durable broker receives delivery/cleanup callbacks. The composition root performs this wiring, which keeps startup direction acyclic and leaves future desktop and mobile clients dependent on HTTP/realtime contracts rather than Python implementation details.

## Rules

1. Views emit intent and render state; they do not use `fetch` or `EventSource` directly.
2. Feature requests have stable action names such as `friends.add` or `messages.send`.
3. Realtime event handlers mutate state inside named store transactions.
4. HTTP handlers perform transport work; application services perform feature work.
5. StateStore remains the persistence boundary for SQLite and Supabase.
6. Domain events are returned with command results and published only after the HTTP response is attempted.

## Account and activity identity boundary

The request actor, identity lifecycle, recipient routing and future social actor contract are specified in [IDENTITIES.md](IDENTITIES.md).

Authentication and social activity use different identifiers. `accounts.id` is the private, immutable login and enforcement key. `users.id` is an activity identity key; one account owns at most three user rows, each with its own globally unique `username`, display name, profile, friendships, rooms, messages, reads, and presence.

Sessions persist both `account_id` and `active_user_id`. Creating or switching an identity always verifies that the target `users.account_id` matches the session account. Browser responses expose activity identities but never their owning `account_id`; the owner-only session response includes only the owned identity list required by the MY switcher and all-ID chat. Password hashes, phone numbers, age group, and gender live only in the account record.

Existing installations are migrated without rewriting social foreign keys: every legacy user receives a deterministic account, remains the first activity identity, and existing friendship, room, message, and read references continue to point at the same `users.id`. SQLite and Supabase enforce the three-identity limit at the persistence boundary as well as in the application service.

## Profile pixel editor performance

The 32×32 profile editor is one 320×320 canvas and one live status node. The previous DOM grid created 1,024 button nodes and attached three listeners to each button every time the profile opened (3,072 per-cell listener registrations per open). The canvas implementation creates no nodes when reopened and installs six delegated canvas listeners only once. One canvas is in the Tab order; arrow keys move the logical cursor, Space/Enter paints, and Delete/Backspace erases.

Browser measurement at 390×844: zero `.pixel-cell` buttons, one canvas, and a zero-button DOM delta after closing and reopening the profile. End-to-end automation timing was 292 ms on first open and 273 ms on reopen; those values include browser-control round trips and are recorded as a coarse regression reference rather than pure scripting time.

### Profile-art resource split

The 1,024 `#rrggbb` strings no longer live in user rows or the in-process user index. Nonblank art is stored in `profile_art` as exactly 3,072 RGB bytes; blank art has no row. `/session`, `/me`, `/friends`, `/rooms`, and the legacy messenger response contain only versioned thumbnail metadata, while the owner-only `/profile/pixels` endpoint expands the original after the editor opens. Pixel-art thumbnails are small PNG responses at `/profile-art/{user_id}/thumbnail?v={version}` with a one-year immutable browser cache. Existing string-array rows migrate to the binary resource on normalized-state load and are verified by a lossless round-trip test. CI also runs the 10,000-user storage fixture and enforces the 4KB packed-art and 200-byte avatar-metadata budgets.

`python tests/profile_art_benchmark.py` measured the 10,000-user compact fixture on Windows: 5,373,952 bytes of RSS growth and 2,300,000 bytes of user-row JSON. The equivalent legacy pixel-array JSON is 102,410,000 bytes. All-nonblank packed originals use 30,720,000 bytes (3,072 per user); blank originals use zero bytes. A synthetic 1,000-friend response test confirms zero pixel arrays and less than 200 bytes of avatar metadata per friend.

## Presence rendering

Friend and direct-room presence lookups use `friendByUsername`, `roomById`, and `roomIdsByPeerUsername` indexes. Realtime presence updates mutate only the indexed records and enqueue the username in a `requestAnimationFrame` batch. The active view patches its existing avatar/status descendants through `friendNodes` or `roomNodes`; list containers are not cleared, inactive lists are not rendered, and focused rows and scroll positions stay intact.

## SSE capacity and queue policy

The current standard-library server dedicates one daemon thread to each SSE connection, so deployment capacity is deliberately bounded rather than advertised as unbounded async I/O. The Render free profile targets 25 concurrent realtime users and sets `MAX_SSE_CONNECTIONS=32`, leaving headroom for reconnect overlap. Recommended limits are 32 for Render free/small 512 MB instances, 64 for a 1 GB single instance, and 128 only after load testing a 2 GB or larger instance. Deployments that need more than 128 concurrent realtime users should move `/events` to an ASGI service or managed pub/sub rather than raising the thread limit.

Each connection has a 32-event queue. A full queue means the client is too slow: the subscriber is removed, `queue_drops_total` increments, and a sentinel closes its SSE loop so the browser reconnects and resynchronizes through `/sync?after_revision=`. Heartbeats run every 10 seconds, bounding normal broken-socket detection. `/metrics` reports active/accepted/rejected/disconnected connections, enqueued events, queue drops, heartbeats, subscriber count, process RSS, and active thread count.

Run the automated capacity probe against a local or staging account:

```text
python tests/sse_load.py --username USER --password PASSWORD --connections 20
```

The probe holds the requested SSE connections, measures `/health` p50/p95 while they are active, closes them, and fails unless all resources return to the pre-test active count within two heartbeat intervals.

Local Windows measurement on 2026-08-19 with one browser connection plus 20 probe connections: all 20 were accepted, 40/40 health probes succeeded, p50 was 15.409 ms and p95 was 16.097 ms. The server reported 27 active threads and 46,014,464 bytes RSS under load. Within two heartbeat intervals it returned to the one browser SSE connection, 7 active threads, 45,285,376 bytes RSS, and 20 recorded disconnects. No queue drops or server-side connection-reset traces occurred.

## HTTP request admission and slow-client policy

`ChatServer` admits at most 64 request threads, including SSE, and rejects excess sockets with a small `503` response before starting another thread. At most 16 admitted requests may read bodies concurrently, leaving capacity for bodyless health checks and ordinary API traffic. The defaults reserve at least eight general-request slots beyond the configured SSE limit.

An unbuffered request stream and a socket peek gate require the complete HTTP header terminator within five seconds; incomplete request lines or headers are closed instead of being interpreted as complete by the standard-library parser. JSON and form bodies have a ten-second absolute read deadline. Binary uploads, including the 8MB attachment limit, have a separate 30-second deadline. Unknown POST bodies use the same bounded reader while being discarded, so they cannot bypass admission control.

`/metrics` exposes active, accepted, and rejected request counts plus header timeouts, body timeouts, body-reader rejections, and all configured request/body limits. `render.yaml` pins these values so deployment behavior does not depend on implicit platform timeouts. Socket-level regression tests hold partial headers and partial bodies open, verify bounded rejection/closure, and confirm that health and a normal login still complete while slow body readers are occupied.

## Operations observability and SLO gate

Every completed request receives an `X-Request-ID` and emits one JSON log record with a normalized route, status, latency, request/response bytes, and a short SHA-256 user identifier. Query strings, bodies, headers, usernames, passwords, session/upload tokens, OAuth codes, messages, and signed URLs are excluded. A supplied request ID is reused only after a strict character/length check. Automated tests correlate the response header to the log and search the captured record for seeded secrets.

`/live` is process liveness. `/ready` additionally probes the database and migration marker, persistence lag/error/pending work, durable-event outbox, request admission and body-reader capacity; Render routes health checks to readiness. `/metrics` is the JSON dashboard source for overall and normalized-route request p50/p95/p99/max, 5xx and byte counters, SSE queues/delivery, persistence, and CPU/RSS/thread/FD saturation. The full SLO, alert thresholds and field-to-panel mapping are versioned in `OPERATIONS.md`.

`python tests/operations_load.py --profile smoke` starts an isolated real HTTP server and exercises login, messenger/read paths, concurrent durable message sends, SSE fan-out, signed upload grant/transfer/complete, and an injected database outage. It fails on message p95 above 300ms, read p95 above 500ms, realtime p95 above one second, 5xx above 0.1%, unexpected 4xx, queue drops, incomplete SSE fan-out, failed upload/readiness, or failure to remove readiness during the outage. GitHub Actions runs this after the unit suite; longer load/spike/soak profiles use the same fixture and output schema.

## Browser security boundary

`ChatHandler.end_headers()` is the single security-header boundary for HTML, JSON, assets, uploads, redirects, SSE, and errors. It denies framing with both CSP `frame-ancestors 'none'` and `X-Frame-Options: DENY`, disables MIME sniffing, restricts referrers and sensitive browser capabilities, and uses `Cross-Origin-Opener-Policy: same-origin-allow-popups` so Google consent popups remain usable. HTTPS responses add one-year HSTS.

The CSP defaults every resource to same-origin, blocks objects, and allowlists only Google Identity (`accounts.google.com`) and Kakao form navigation. Inline styles remain allowed because the main document currently owns its stylesheet; scripts do not allow inline code or `unsafe-eval`. Authenticated upload responses additionally retain `Content-Security-Policy: sandbox`, which intersects with the common policy when a PDF is opened directly.

## Static artifact and cache policy

The production font artifact contains one 200,272-byte Korean WOFF2 and no TTF/OTF files. The regular face is the only critical preload, uses `font-display: swap`, and the browser synthesizes heavier UI weights instead of downloading three additional full-Hangul faces. This reduces the font tree from roughly 5.5MB to 200KB and keeps the first font transfer below the 250KB budget.

Every production asset URL includes the first 12 hexadecimal characters of its SHA-256 content hash. The server independently computes the same manifest at startup and grants `public, max-age=31536000, immutable` only when the supplied hash matches. A stale or unversioned URL remains usable for an older HTML document but receives `no-cache`, preventing mismatched content from being pinned under an obsolete fingerprint. HTML uses an ETag with `no-cache, max-age=0` and returns `304` on revalidation. `Vary: Accept-Encoding` accompanies identity, gzip, and Brotli responses; already-compressed WOFF2 is not recompressed.

`tests/static_budget.py` fails on any TTF/OTF, unresolved source-module import, stale content hash, or budget overrun. The esbuild production output measures 151,729 bytes of JavaScript against the 256,000-byte raw budget, while the complete static artifact measures 426,719 bytes against 524,288. Entry HTML, standalone workers, and Worker URLs use content fingerprints. CI runs `npm run build:check` before the budget test so a source change without regenerated artifacts cannot be merged.

The browser fixture `/assets/static-load-benchmark.html` fetches the production index and every critical asset twice at a 390×844 mobile viewport. After bundling, the index exposes one application script plus the font preload instead of a multi-request module graph. A local Chromium run in the validation session completed the fixture successfully and recorded FCP at 148ms; cache state makes that localhost number diagnostic rather than a production performance claim. A throttled production LCP below 2.5 seconds remains a service-level candidate.

## Normalized persistence migration

`persistence.py` owns the row-level SQLite schema and transaction boundary. Its active tables mirror the Supabase schema: users and social accounts, friendships, rooms and members, messages, read positions, and sessions. Unique constraints enforce usernames, friend codes, social identities, friendship pairs, and message idempotency keys; foreign keys enforce membership references, and message paging uses a per-database insertion order with a room index.

Legacy `state_parts` are imported once under `BEGIN IMMEDIATE` and retained as rollback material. After that marker is committed, startup excludes every `messages:*` JSON part. Message pages are fetched from SQL on demand, and a message send synchronously inserts one row and updates its room in the same durable transaction. Normalized storage retains the complete message history; the 200-message limit applies only to legacy in-process compatibility data and API page bounds. The asynchronous compatibility writer updates room metadata only; it no longer serializes or rewrites a message array. `migrate_normalized.py` creates a consistent SQLite backup and compares source/target counts plus `foreign_key_check` results.

The Supabase repository implements the same contract through PostgREST. Stable ordered pagination crosses the platform's per-response row limit for startup indexes, while messages remain cursor-paged and never join the startup load. Multi-row user/social-account, room/member/read-position, message/retention, and session mutations execute inside `security definer` RPC transactions. Direct table access and RPC execution are revoked from `anon` and `authenticated`; only the server's `service_role` is granted access. The one-time import is retry-safe and records `app_migrations.normalized_state` only after all row batches succeed. The preserved `app_state` and a pre-cutover database snapshot are the rollback sources; after post-cutover writes begin, rollback requires restoring that snapshot because legacy JSON is intentionally no longer rewritten.

The reproducible scale probe is `python tests/storage_scale.py DATABASE --users 10000 --rooms 5000 --messages-per-room 200 --writes 100`. On local Windows on 2026-08-19, it created 1,000,000 message rows in 26.273 seconds. Opening the repository afterward took 3.378 ms with 27,766,784 bytes RSS; 100 durable message transactions measured p50 7.418 ms, p95 9.239 ms, and p99 16.189 ms. All 1,000,000 rows were present and `PRAGMA foreign_key_check` returned zero errors. This validates the issue targets of ready below 10 seconds and write p95 below 200 ms without loading message payloads into the process.

## Paged messenger bootstrap

The browser no longer loads or polls the legacy `/messenger` aggregate. Startup establishes a recovery revision through `/me`, then fetches only the first 30 `/friends` and `/rooms`; list scrolling follows opaque stable cursors. Friend rows omit profile pixel arrays, room rows omit group participants, and `/rooms/{id}/members` loads members only when a group is opened. SQLite and Supabase batch presence and latest-message lookups once per page, avoiding the previous per-entity query pattern.

SSE failure polling calls `/sync?after_revision=` and dispatches only durable recipient events through the same typed event router. Empty sync responses contain only the current revision and pagination flag; all split GET responses provide a private ETag and return `304` with an empty body for a matching `If-None-Match`. The client store merges entities by ID, so a presence/read event does not replace or rerender the full bootstrap payload.

`python tests/bootstrap_scale.py --count 1000 --iterations 20` builds 1,000 friends and 1,000 direct rooms, traverses every cursor page, and enforces p95 below 300 ms plus an initial gzip budget below 100 KB. Direct-room peer presence is fetched once per page rather than once per room. Local Windows measurement on 2026-08-27 produced a 31,627-byte raw / 2,278-byte gzip initial response total and 67.520 ms p95; all 1,000 friends and rooms were returned in 20 pages each without duplicates or omissions. The same fixture runs in CI so account-schema or query-count regressions fail the build.

Paged friends, rooms, legacy messenger summaries, and message history now hold the process-wide state lock only while validating access and copying a bounded snapshot. SQLite or Supabase reads for presence, latest messages, history, and read cursors happen after releasing that lock; message responses reacquire it and recheck room access before exposing data. A slow database read therefore cannot serialize unrelated presence, message, or room requests behind an in-process mutex. Concurrency regression tests pause repository calls and assert that the state lock remains available.

The browser HTTP client keeps a 64-entry LRU of same-origin JSON responses that carry an ETag. Repeated `/me`, `/friends`, `/rooms`, `/rooms/{id}/members`, and `/sync` reads send `If-None-Match`; a `304` reuses the parsed payload without transferring or parsing JSON again. The cache is memory-only and is cleared on every authentication boundary, while `no-store` responses are never retained. A Node contract test covers initial fill, conditional revalidation, `304` reuse, and explicit clearing.

## Attachment transfer boundary

Attachments use a three-step `grant → transfer → complete` contract. A grant is bound to one authenticated user, random object name, exact size, MIME type, and original basename. Its state starts as `pending`; only a successful object probe or local stream validation moves it to `completed`, and only completed grants can be consumed by a new message. Failed, canceled, and expired grants delete their orphan object during explicit discard or the broker's periodic maintenance pass. Expiry discovered opportunistically by any grant lookup is retained in a cleanup queue, and transient object-deletion failures are requeued for the next pass instead of leaking the orphan. Pending grants are capped per user by count and aggregate bytes.

With Supabase enabled, the server uses its service role only to create an object-scoped signed upload URL. The browser PUTs bytes directly to the private bucket, then `/uploads/complete` reads only the first 32 bytes with a Range request and checks total size, stored MIME, and file signature. Authorized downloads receive a short-lived signed URL through a `307`; unauthorized users receive neither upload nor download credentials. The service role key is never included in a browser response.

Without object storage, the same contract falls back to bounded server streaming. The handler admits a fixed number of body readers, writes 64KB chunks to a unique `.part` file under the upload root, honors one absolute deadline, validates the captured prefix, flushes it, and atomically renames it. Aborted or invalid transfers never create the final filename. Local reads stream 64KB chunks and implement single byte and suffix ranges with `206`, `Accept-Ranges`, `Content-Range`, and `416` handling.

### Image decode and memory boundary

Chat attachments, profile photos, and group-room photos use `ColorlessImageProcessing`, a one-worker-per-job boundary. A Worker reads at most 512KB of JPEG, PNG, or WebP metadata before decode, rejects either axis above 16,384 or more than 32 million source pixels, applies EXIF orientation through `createImageBitmap(..., {imageOrientation: "from-image"})`, requests decode-time resizing, draws with `OffscreenCanvas`, and encodes WebP away from the main thread. Transparent chat images keep alpha; square profile and room images intentionally composite onto white. Source bytes remain independently capped at 50MB.

Jobs are named by surface (`chat-attachment`, `profile-image`, or `room-image`). Starting a replacement terminates the previous Worker and rejects it with `AbortError`; removing an attachment, closing a chat/profile/room surface, or logging out uses the same cancellation boundary. Metadata, decode, resize, and encode stages update the existing live status UI. Every job has a 15-second timeout, and bitmaps are explicitly closed in Worker `finally` blocks.

The accelerated path requires `Worker`, `createImageBitmap`, `OffscreenCanvas`, and `OffscreenCanvas.convertToBlob`. Browsers without all four use the documented main-thread fallback with a lower 12-million-pixel ceiling and the same 16,384-axis ceiling; an image beyond that conservative budget is rejected with an explicit resolution error instead of attempting a large canvas allocation. Profile preview sources are reduced to a 2,048px edge in the Worker before the interactive crop bitmap reaches the main thread.

The server does not trust the browser result. It reads a bounded 512KB prefix locally or with an object-storage Range request, verifies the declared MIME signature, parses JPEG SOF, PNG, GIF, WebP, or ISO-BMFF `ispe` dimensions, and applies the same 32-million-pixel/16,384-axis ceiling before completing an attachment grant. Profile and room bundles additionally require exact 1024×1024 and 128×128 WebP dimensions.

The automated browser fixture is `/assets/image-worker-benchmark.html`. It creates a 4000×3000 JPEG in a fixture Worker, measures Long Tasks while converting it, verifies replacement-job cancellation, and sends a synthetic 50,000×50,000 PNG header through the metadata guard. Local Chromium measurement on 2026-08-19 completed the 12MP→2560×1920 conversion in 1,280.5ms with zero Long Tasks at or above 100ms; cancellation returned `AbortError`, the pixel bomb returned `image-dimensions-too-large`, and the console had no warnings or errors.

## Multi-instance consistency and realtime recovery

`realtime_events` is a shared durable event log used by every server instance. Domain events are assigned a globally ordered `revision`, unique `event_id`, `occurred_at`, room identifier when applicable, recipient set, and origin instance. Each server consumes that log on a 100 ms cursor and fans events into only its local bounded SSE queues. A failed publish enters a bounded local outbox and is retried with the same event ID, making an ambiguous retry idempotent. Metrics expose publish/consume failures, delivered/replayed events, reconnects, and observed p95 delivery latency.

SSE responses include the durable revision as the SSE `id`. The browser stores the last ID for a recreated `EventSource`, deduplicates event IDs, and sends its cursor on reconnect. The server replays every recipient-event page after that cursor, not only the first 500 records. `/sync?after_revision=` uses the same durable recipient log as the polling and reconnect recovery path. When a cursor predates retained event history, SSE and polling explicitly require a compact bootstrap reset instead of silently accepting an incomplete state; message history remains independently cursor-paged.

Presence uses shared leases rather than process-local online flags. Each instance/session lease is refreshed by SSE heartbeats and expires after 45 seconds. Any surviving instance removes expired leases, recomputes the user's aggregate rooms/emoji/online state, and publishes the change, so a crashed instance disappears within 60 seconds without a disconnect callback.

User and room rows carry optimistic revisions. A stale profile or full room/member update fails instead of overwriting another instance and the request reloads authoritative rows before returning `409`. Read positions are independent upserts, concurrent messages update only the current database room timestamp/revision, direct rooms have a unique participant-pair key, and message retries remain protected by the client-message unique index. The multi-instance tests run two brokers against one database, cover message/friend/room fan-out under one second, replay, publish retry, TTL cleanup, stale revision rejection, merged read cursors, and continued delivery after one broker stops during a rolling transition.

The process-level probe is `python tests/multi_instance.py`. On local Windows on 2026-08-19, two independent HTTP/SSE server processes sharing one SQLite database delivered the cross-instance message in 123.121 ms. The receiving server reported 852.732 ms event-delivery p95 across startup, presence, friend, room, and message events, zero publish/consume failures, and zero queue drops. After the sending process stopped, the surviving process committed another message and a replacement process replayed revision 7 after cursor 6; the duplicate client-message retry returned the original message ID.
