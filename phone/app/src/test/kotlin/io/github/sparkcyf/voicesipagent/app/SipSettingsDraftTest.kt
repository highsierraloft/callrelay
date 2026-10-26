package io.github.sparkcyf.voicesipagent.app

import io.github.sparkcyf.voicesipagent.privileged.AudioMethod
import io.github.sparkcyf.voicesipagent.privileged.CallAccess
import io.github.sparkcyf.voicesipagent.pjsua2.SipTransport
import org.junit.Assert.*
import org.junit.Test

class SipSettingsDraftTest {
    @Test fun invalidPortsRemainUnsavedAndCannotFallBackToADefault() {
        val saved = GatewaySettings()
        val draft = SipSettingsDraft()
        draft.syncSaved(saved)

        for (transport in SipTransport.entries) {
            draft.transport = transport
            for (input in listOf("", " ", "abc", "5060x", "5060.0", "0", "-1", "65536", "2147483647", "999999999999")) {
                draft.port = input
                assertNotNull("port '$input' should be invalid", draft.portError)
                assertTrue(draft.hasUnsavedChanges(saved))
                assertThrows(IllegalArgumentException::class.java) { draft.settingsUpdate() }
                assertEquals(input, draft.port)
            }
        }
    }

    @Test fun validPortsIncludingBoundariesCanBeSavedAfterAnError() {
        val saved = GatewaySettings()
        val draft = SipSettingsDraft()
        draft.syncSaved(saved)
        draft.port = "65536"
        assertNotNull(draft.portError)

        for (input in listOf("1", "5060", "5061", "15062", "65535")) {
            draft.port = input
            assertNull("port '$input' should be valid", draft.portError)
            val submitted = draft.settingsUpdate()(saved)
            assertEquals(input.toInt(), submitted.port)
            draft.syncSaved(submitted)
            assertFalse(draft.hasUnsavedChanges(submitted))
        }
    }

    @Test fun queuedSettingsStayCleanOnReopenAndPreserveFurtherEdits() {
        val applied = GatewaySettings(server = "saved.invalid", username = "saved-user")
        val queued = applied.copy(server = "queued.invalid")
        val draft = SipSettingsDraft()
        draft.syncSaved(applied, queued)
        assertEquals("queued.invalid", draft.server)
        assertFalse(draft.hasUnsavedChanges(applied, queued))

        draft.server = "unfinished.invalid"
        draft.password = "unfinished-password"
        val updated = queued.copy(server = "external.invalid", username = "external-user", sipDebugLog = true)
        draft.syncSaved(applied.copy(sipDebugLog = true), updated)

        assertEquals("unfinished.invalid", draft.server)
        assertEquals("unfinished-password", draft.password)
        assertEquals("external-user", draft.user)
        assertTrue(draft.hasUnsavedChanges(applied, updated))

        // Applying the queue preserves subsequent edits; saving them clears the unsaved indicator.
        draft.syncSaved(updated)
        assertEquals("unfinished.invalid", draft.server)
        assertEquals("unfinished-password", draft.password)
        assertTrue(draft.hasUnsavedChanges(updated))
        val submitted = draft.settingsUpdate()(updated)
        draft.syncSaved(updated, submitted)
        assertFalse(draft.hasUnsavedChanges(updated, submitted))
        draft.syncSaved(submitted)
        assertFalse(draft.hasUnsavedChanges(submitted))
    }

    @Test fun submissionCapturesClickTimeValuesWithoutReplacingOtherQueuedPreferences() {
        val draft = SipSettingsDraft()
        draft.syncSaved(GatewaySettings())
        draft.server = "submitted.invalid"
        draft.password = "submitted-password"
        val submit = draft.settingsUpdate()
        draft.server = "later.invalid"
        draft.password = "later-password"
        val latest = GatewaySettings(audioMethod = AudioMethod.LEGACY, callAccess = CallAccess.DIALER,
            muteEarpiece = false, sipDebugLog = true, keepDefaultDialer = true)
        val result = submit(latest)

        assertEquals("submitted.invalid", result.server)
        assertEquals("submitted-password", result.password)
        assertEquals(AudioMethod.LEGACY, result.audioMethod)
        assertEquals(CallAccess.DIALER, result.callAccess)
        assertFalse(result.muteEarpiece)
        assertTrue(result.sipDebugLog)
        assertTrue(result.keepDefaultDialer)
    }
}
