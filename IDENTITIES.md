# Activity identities — issue #25

## Implemented account boundary

`accounts.id` is private authentication/enforcement data. `users.id` is the stable activity identity; existing social foreign keys keep their values during migration. `users.account_id` is never a social actor. A maximum of three **active** identities is enforced both by the application and database triggers. Disabled rows retain their usernames and references; they do not consume an active slot.

Public profiles omit account IDs, login providers and masked phone numbers. The owner-only `/session` and `/me` account object may contain authentication metadata and the list of owned identities. Recipient identity metadata is added to a private SSE or sync delivery copy, never to a shared event that could reveal ownership to another account.

## Request actor contract

- Browser requests capture `X-Acting-Identity` before asynchronous work. JSON clients may instead send `acting_identity_id`. A conflicting header and body is rejected with 403.
- The server resolves the identity against the authenticated account and current durable account/identity status. A client-supplied account ID is never an authority.
- Room requests additionally require the explicitly selected identity to be a participant (public rooms retain their public access rule). An explicitly selected nonparticipant cannot silently fall back to another owned identity. Legacy clients without an actor retain automatic room identity selection.
- Reads use the same optional actor header/query. HTTP response cache entries include the actor. Request results are also guarded by the existing authentication generation.
- Messages expose `sender_identity_id`; `username` is retained for existing clients. Message mutations recheck active status within the database transaction.
- SSE and `/sync` delivery include `actor_identity_id` and the recipient's own `recipient_identity_ids`. The common broker record does not include owner linkage.
- Upload grants, completion and cleanup use the identity captured at upload start. Presence leases use session plus identity, so one ID cannot overwrite another ID's online status.

The session cookie is not reissued when switching IDs. A detached chat window captures its identity in its URL and subsequent requests. The main window can open two additional named chat windows (three total); browser popup settings and screen placement remain under user control. On mobile the existing single conversation and recent-room controls remain available.

`GET /identities/unread` returns owner-only `{counts: {identity_id: count}, total}` over all accessible rooms, independent of the loaded list page. A room containing two owned identities contributes separately to each identity's count. The selected identity's room view determines whose read position advances.

## Lifecycle and abuse policy

`POST /identities/disable` accepts `{identityId}`. The caller must first switch to a different active ID in the same account. This guarantees at least one active identity remains, including concurrent requests. Disabling is a soft operation: existing messages, files, friendships, room membership and read positions remain referentially intact. Sessions using that identity move to the caller's active identity without issuing a new cookie. New activity and cached-session impersonation are denied; restoring a disabled ID is not exposed in the UI. Usernames are not recycled.

An owner may create friendships and DMs between their own identities; ownership is not automatically disclosed. Profile and relationship changes do not propagate between identities. Optional public ownership linking is not provided.

Account suspension uses `accounts.status` and blocks every identity. Existing ticket-specific moderation retains its narrower scope. Account closure should first set account status inactive and invalidate sessions; hard deletion or anonymization requires a separately approved retention workflow, not cascading database deletion from a public endpoint. Retained conversation records must not be described as erased. Administrative ownership inspection is limited to the service-role/operator boundary; no general-user ownership lookup is exposed.

Message creation, edit, reaction, delete and identity lifecycle limits have independent identity keys and a shared account ceiling, without IP in either key. Defaults per minute are create 120/360, edit 60/180, reaction 180/540, delete 60/180 (identity/account). Identity create/disable each allow 10/30 per hour. Existing preauthentication IP controls remain. Counters are process-local; a multi-instance deployment needs a shared limiter for an exact fleet-wide ceiling.

## Threads integration and remaining work

Posts and replies use `author_identity_id`, follows/blocks use `actor_identity_id`/`target_identity_id`, likes use `actor_identity_id`, and notifications use `recipient_identity_id`/`actor_identity_id`, all referencing `users.id`. The implemented APIs use the ownership resolver, transaction-level active-actor validation, identity-keyed drafts, and identity-local blocks. See [THREADS.md](THREADS.md) for the UI, API and database migration.

The Threads text feed, posts/comments/replies, likes/follows/mentions, notification inboxes, block/report UI and actor/privacy tests are implemented. Account erasure/anonymization remains the separately authorized retention workflow described above; issue #25 requires that policy to be documented, not a public hard-delete endpoint. Media posts and an operator report-review screen are not part of this first Threads version.

Creating or disabling an identity now closes only that account's existing SSE subscriptions and discards their queued payloads. The browser reconnects with its durable cursor and the current identity list, so an existing window subscribes to a newly created ID and stops consuming data queued for a disabled ID. Other accounts stay connected. Switching IDs still does not replace the session cookie.

## Issue #25 acceptance audit

| Completion condition | Implementation and verification |
| --- | --- |
| At most three active identities | Application checks and SQLite/Supabase identity-limit constraints; `test_disable_releases_capacity_preserves_history_and_revokes_cached_identity` and existing storage tests |
| Independent profiles, relations, chat, reads and presence | Identity foreign keys and session+identity presence leases; three-profile/three-DM unread regression test, Threads identity-local relation tests |
| Unified chat and correct replies | Account-scoped room summaries plus room-pinned request actors; `identity-context.js`, `identity-ui.js`, Work mode and request-isolation tests |
| Cannot impersonate a different room identity | HTTP actor ownership resolver and transaction validation; pinned-sender, cross-account and message membership tests |
| Correct realtime recipient identities | Owner-private event enrichment and lifecycle SSE reconnection; peer-linkage and owner-only queue invalidation tests |
| Lossless first-identity migration | Stable `users.id`, rerunnable migration marker and retained social foreign keys; legacy SQLite migration plus normalized-state tests |
| Identity actors in Threads | Author/actor/recipient identity columns and shared authorization; `test_threads.py` covers posts, replies, likes, follows, mentions and inboxes |
| No public ownership links | Public-profile filtering, private event copies and identity-scoped HTTP cache; profile/event privacy and `http_cache.mjs` tests |
| Cross-account spoofing blocked | Header/body mismatch, foreign identity and disabled/suspended actor tests in `test_identity.py` and `test_threads.py` |
| Disable, closure, suspension and retention policy | Lifecycle and abuse policy above: soft-disable, username retention, inactive account/session invalidation and separate erasure process |
| Account/identity abuse limits | Separate keys and shared account ceiling documented above; existing message mutation rate-limit tests |

This audit covers the issue's listed completion conditions. Production application, data deletion and identity schema changes are not performed by the audit or its tests.

## Deployment and verification

Back up the database, apply `src/colorless/database/supabase-schema.sql` and then `src/colorless/database/threads-schema.sql`, run remote preflight, then deploy the application. The SQL is rerunnable and preserves existing identity IDs and social references. Roll back application code without deleting identity or Threads rows; older applications must not be used to bypass inactive status.

`tests/test_identity.py` exercises real HTTP ownership, fixed senders, private profiles, disable/replacement, retained messages and suspended-account caches. `tests/identity_context.mjs` covers request capture and per-identity client behavior. Existing migration, message transaction, multi-instance and frontend tests remain in CI.
