from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
APP = ROOT / "frontend" / "src" / "app"


class FrontendHardeningContractTest(unittest.TestCase):
    def test_realtime_dedupe_is_shared_and_committed_after_dispatch(self) -> None:
        source = (APP / "platform" / "events.js").read_text(encoding="utf-8")
        self.assertIn("inFlightEventIds", source)
        self.assertIn("seenEventIds.has(id)", source)
        dispatch = source.index("await router.dispatch(payload, eventContext)")
        cursor = source.index('sessionStorage.setItem(cursorStorageKey, message.lastEventId)')
        self.assertLess(dispatch, cursor)

    def test_history_mode_and_room_outbox_protect_transient_state(self) -> None:
        chat = (APP / "chat.js").read_text(encoding="utf-8")
        interactions = (APP / "message-interactions.js").read_text(encoding="utf-8")
        self.assertIn("state.chatOutbox.put(roomId, pendingMessage)", chat)
        self.assertIn("state.chatOutbox.list(roomId)", chat)
        self.assertIn("retryData.completedAttachment", chat)
        self.assertIn("isPersistedOutboxReplacement(existing, message)", chat)
        self.assertIn("Object.assign(message, { pending: false, failed: false })", chat)
        self.assertIn("delete message.retry_data", chat)
        self.assertIn("&& !state.chatHistoryMode", interactions)
        self.assertIn("jumpToLatest: jumpToLatestChatMessages", chat)

    def test_reactions_are_idempotent_and_revision_aware(self) -> None:
        interactions = (APP / "message-interactions.js").read_text(encoding="utf-8")
        messenger = (APP / "messenger.js").read_text(encoding="utf-8")
        self.assertIn("messageId, emoji, reacted", interactions)
        self.assertIn("reactionMessageSnapshot", messenger)
        self.assertIn("scheduleMessageReconciliation", messenger)

    def test_auth_epoch_cancels_async_chat_work_and_resets_private_ui(self) -> None:
        core = (APP / "core.js").read_text(encoding="utf-8")
        chat = (APP / "chat.js").read_text(encoding="utf-8")
        interactions = (APP / "message-interactions.js").read_text(encoding="utf-8")
        work_mode = (APP / "work-mode.js").read_text(encoding="utf-8")
        self.assertIn("state.authEpoch !== authEpoch", core)
        self.assertIn('key: `${authScoped ? authEpoch : "public"}:${key}`', core)
        self.assertIn("state.messagesLoadController?.abort()", core)
        self.assertIn("state.messageReconciliationTimers.clear()", core)
        self.assertIn("state.messenger = { friends: [], discoverableUsers: [], rooms: [] }", core)
        self.assertIn("state.chatSearchRequestId += 1", core)
        self.assertIn("state.chatSearchResults = []", core)
        self.assertIn('state.activeList = "chats"', core)
        self.assertIn("state.actionBarByTab = {", core)
        self.assertIn('headerSearchInput.value = ""', core)
        self.assertIn("const active = () => state.authEpoch === authEpoch && !retryData.cancelled", chat)
        self.assertIn("authEpoch: state.authEpoch", chat)
        self.assertIn("state.composerEditController?.abort()", interactions)
        self.assertIn("registerCoreHooks({ resetWorkMode, syncWorkModeVisibility })", work_mode)

    def test_out_of_order_message_events_use_a_bounded_revision_journal(self) -> None:
        chat = (APP / "chat.js").read_text(encoding="utf-8")
        messenger = (APP / "messenger.js").read_text(encoding="utf-8")
        revisions = (APP / "platform" / "message-revisions.js").read_text(encoding="utf-8")
        self.assertIn("createMessageRevisionJournal(limit = 512)", revisions)
        self.assertIn("while (records.size > limit)", revisions)
        self.assertIn("state.messageEventJournal.checkpoint()", chat)
        self.assertIn("journalCheckpoint", chat)
        self.assertIn("state.messageEventJournal.snapshot", messenger)
        self.assertIn("state.messageEventJournal.tombstone", messenger)

    def test_realtime_room_events_preserve_recipient_unread_and_read_boundaries(self) -> None:
        app = (APP / "app.js").read_text(encoding="utf-8")
        chat = (APP / "chat.js").read_text(encoding="utf-8")
        messenger = (APP / "messenger.js").read_text(encoding="utf-8")
        self.assertIn("mergeAuthoritativeRoomSnapshot(currentRooms.get(room.id), room)", app)
        self.assertIn("state.roomsResetPending = true", app)
        self.assertIn("loadRoomsPage({ reset: true, render: pendingRender })", app)
        self.assertIn("mergeRealtimeRoomSnapshot(existingRoom, eventRoom)", messenger)
        self.assertIn("unreadAfterMessageCreated", messenger)
        self.assertGreaterEqual(messenger.count("mergeRealtimeRoomSnapshot("), 4)
        self.assertIn("room?.last_message?.id === payload.lastReadMessageId", messenger)
        self.assertIn(
            'applyMessageReaderToCurrentMessages(payload.username, "realtime.room-read", payload.lastReadMessageId)',
            messenger,
        )
        self.assertIn("readBoundaryIndex(state.messages, state.messageIndexes, lastReadMessageId)", chat)

    def test_profile_and_work_mode_mutations_are_identity_scoped(self) -> None:
        profile = (APP / "profile.js").read_text(encoding="utf-8")
        work_mode = (APP / "work-mode.js").read_text(encoding="utf-8")
        submit_guard = (APP / "platform" / "submit-guard.js").read_text(encoding="utf-8")
        self.assertIn("function captureProfileContext()", profile)
        self.assertIn("currentProfileIdentityKey() === context.identityKey", profile)
        self.assertIn("`profile.pixels:${profileContext.identityKey}`", profile)
        self.assertIn("workModeReplyGuard.reserve", work_mode)
        self.assertIn("clientMessageId: submission.clientMessageId", work_mode)
        self.assertIn("workModeReplyGuard.confirm(submission)", work_mode)
        self.assertIn("workModeReplyGuard.clear()", work_mode)
        self.assertIn('import { formatVoiceDuration } from "./voice.js"', work_mode)
        self.assertIn("export function createMessageSubmitGuard", submit_guard)

    def test_replace_pipeline_aborts_and_rejects_stale_generations(self) -> None:
        pipeline = (APP / "platform" / "pipeline.js").read_text(encoding="utf-8")
        self.assertIn("new AbortController()", pipeline)
        self.assertIn('definition.policy === "replace"', pipeline)
        self.assertIn("previous.controller.abort()", pipeline)
        self.assertIn("ensureCurrent();", pipeline)


if __name__ == "__main__":
    unittest.main()
