package io.github.sparkcyf.voicesipagent.privileged.shell

import io.github.sparkcyf.voicesipagent.privileged.CallAudioStatus
import org.junit.Assert.*
import org.junit.Test

class LocalOutputMuteTest {
    private class Audio : LocalOutputMute.AudioControl {
        override var mode = 2
        override var voiceCallMuted = false
        val modes = mutableListOf<Int>()
        val streamRequests = mutableListOf<Boolean>()
        var acceptMute = true
        var acceptUnmute = true
        var acceptMode = true
        var failMuteAcknowledgement = false
        var failUnmute = false
        var failMode = false

        override fun requestMode(mode: Int) {
            modes += mode
            if (failMode) throw SecurityException("mode denied")
            if (acceptMode) this.mode = mode
        }
        override fun requestVoiceCallMute(muted: Boolean) {
            streamRequests += muted
            if (!muted && failUnmute) throw IllegalStateException("audio service unavailable")
            if (if (muted) acceptMute else acceptUnmute) voiceCallMuted = muted
        }
        override fun awaitMode(wanted: Int) = mode
        override fun awaitVoiceCallMute(wanted: Boolean): Boolean {
            if (wanted && failMuteAcknowledgement) throw IllegalStateException("mute acknowledgement lost")
            return voiceCallMuted
        }
    }

    @Test fun directCallMutesOutputWithoutChangingModeAndRestoresOnce() {
        val audio = Audio()
        val mute = LocalOutputMute(audio)
        assertEquals("muted: voice-call stream", mute.mute(false))
        assertTrue(audio.voiceCallMuted)
        assertEquals(2, audio.mode)
        assertTrue(audio.modes.isEmpty())
        assertEquals("restored voice-call stream (tap closed)", mute.restore("tap closed"))
        assertFalse(audio.voiceCallMuted)
        assertEquals("not muted", mute.restore("close again"))
        assertEquals(listOf(true, false), audio.streamRequests)
    }

    @Test fun redirectionWithdrawsOurModeRequestRatherThanSettingTelecomsMode() {
        val audio = Audio()
        val mute = LocalOutputMute(audio)
        assertEquals("muted: mode 2->5", mute.mute(true))
        assertEquals(5, audio.mode)
        assertTrue(audio.streamRequests.isEmpty())
        assertEquals("restored redirection mode (tap closed), mode=0", mute.restore("tap closed"))
        assertEquals(listOf(5, 0), audio.modes)
    }

    @Test fun initiallyMutedStreamIsNeverUnmutedByUs() {
        val audio = Audio().apply { voiceCallMuted = true }
        val mute = LocalOutputMute(audio)
        mute.beginCall()
        assertEquals("already muted: voice-call stream (preserved)", mute.mute(false))
        mute.mute(false)
        assertEquals(CallAudioStatus.PRESERVED, mute.observedMute())
        assertFalse(mute.ownsOutputChange)
        assertEquals("preserved existing voice-call stream mute (tap closed)", mute.restore("tap closed"))
        assertFalse(mute.restorationPending)
        assertTrue(audio.voiceCallMuted)
        mute.beginCall()
        assertTrue(audio.voiceCallMuted)
        assertEquals(CallAudioStatus.NOT_REQUESTED, mute.observedMute())
        assertTrue(audio.streamRequests.isEmpty())
    }

    @Test fun repeatedMuteDoesNotOverwriteTheOriginalUnmutedState() {
        val audio = Audio()
        val mute = LocalOutputMute(audio)
        mute.mute(false)
        assertEquals("already muted: voice-call stream", mute.mute(false))
        mute.restore("tap closed")
        assertEquals(listOf(true, false), audio.streamRequests)
        assertFalse(audio.voiceCallMuted)
    }

    @Test fun noGlobalChangeIsMadeOutsideACellularCall() {
        val audio = Audio().apply { mode = 0 }
        val mute = LocalOutputMute(audio)
        assertEquals("skipped: mode=0", mute.mute(false))
        assertEquals("skipped: mode=0", mute.mute(true))
        assertEquals("not muted", mute.restore("tap closed"))
        assertTrue(audio.modes.isEmpty())
        assertTrue(audio.streamRequests.isEmpty())
    }

    @Test fun ignoredMuteIsReportedAndItsPossiblyDelayedRequestIsUndone() {
        val audio = Audio().apply { acceptMute = false }
        val mute = LocalOutputMute(audio)
        assertTrue(mute.mute(false).startsWith("failed: voice-call stream mute not applied"))
        assertEquals("restored voice-call stream (tap closed)", mute.restore("tap closed"))
        assertEquals(listOf(true, false), audio.streamRequests)
    }

    @Test fun appliedMuteWithLostAcknowledgementStillRestores() {
        val audio = Audio().apply { failMuteAcknowledgement = true }
        val mute = LocalOutputMute(audio)
        assertTrue(mute.mute(false).contains("IllegalStateException: mute acknowledgement lost"))
        assertTrue(audio.voiceCallMuted)
        assertEquals("restored voice-call stream (tap closed)", mute.restore("tap closed"))
        assertFalse(audio.voiceCallMuted)
    }

    @Test fun failedRestoreKeepsOwnershipUntilARetrySucceeds() {
        val audio = Audio()
        val mute = LocalOutputMute(audio)
        mute.beginCall()
        mute.mute(false)
        audio.failUnmute = true
        val failed = mute.restore("tap closed")
        assertTrue(failed.contains("restoration pending"))
        assertEquals(failed, mute.lastRestoreResult)
        assertTrue(mute.restorationPending)
        assertTrue(mute.ownsOutputChange)
        assertTrue(runCatching { mute.beginCall() }.isFailure)
        assertEquals(failed, mute.lastRestoreResult)
        assertTrue(audio.voiceCallMuted)
        audio.failUnmute = false
        val restored = mute.restore("retry")
        assertEquals("restored voice-call stream (retry)", restored)
        assertFalse(mute.restorationPending)
        assertFalse(mute.ownsOutputChange)
        assertFalse(audio.voiceCallMuted)
        assertEquals(listOf(true, false, false), audio.streamRequests)
        assertEquals("not muted", mute.restore("again"))
        assertEquals(restored, mute.lastRestoreResult)
        mute.beginCall()
        assertFalse(mute.requested)
        assertEquals("", mute.lastMuteResult)
        assertEquals("", mute.lastRestoreResult)
    }

    @Test fun ignoredUnmuteDoesNotReportSuccessOrForgetOwnership() {
        val audio = Audio()
        val mute = LocalOutputMute(audio)
        mute.mute(false)
        audio.acceptUnmute = false
        assertTrue(mute.restore("tap closed").startsWith("failed: voice-call stream still muted"))
        audio.acceptUnmute = true
        assertEquals("restored voice-call stream (retry)", mute.restore("retry"))
    }

    @Test fun userUnmutingDoesNotCauseAnotherMuteOrFalseSuccessReport() {
        val audio = Audio()
        val mute = LocalOutputMute(audio)
        mute.mute(false)
        audio.voiceCallMuted = false
        assertTrue(mute.mute(false).contains("no longer muted"))
        assertEquals(listOf(true), audio.streamRequests)
        mute.restore("tap closed")
        assertFalse(audio.voiceCallMuted)
    }

    @Test fun failedModeRequestStillWithdrawsAPossiblyAppliedRequest() {
        val audio = Audio().apply { failMode = true }
        val mute = LocalOutputMute(audio)
        assertTrue(mute.mute(true).contains("SecurityException: mode denied"))
        audio.failMode = false
        assertEquals("restored redirection mode (tap closed), mode=0", mute.restore("tap closed"))
        assertEquals(listOf(5, 0), audio.modes)
    }

    @Test fun ignoredModeRestoreRetainsThePendingRequest() {
        val audio = Audio()
        val mute = LocalOutputMute(audio)
        mute.mute(true)
        audio.acceptMode = false
        assertTrue(mute.restore("tap closed").contains("restoration pending"))
        audio.acceptMode = true
        assertEquals("restored redirection mode (retry), mode=0", mute.restore("retry"))
    }

    @Test fun observedMuteFollowsAndroidStateIndependentlyOfTheRequest() {
        val audio = Audio().apply { acceptMute = false }
        val mute = LocalOutputMute(audio)
        mute.beginCall()
        assertEquals(CallAudioStatus.NOT_REQUESTED, mute.observedMute())
        mute.mute(false)
        assertTrue(mute.requested)
        assertEquals(CallAudioStatus.NOT_APPLIED, mute.observedMute())
        audio.voiceCallMuted = true
        assertEquals(CallAudioStatus.APPLIED, mute.observedMute())
        audio.voiceCallMuted = false
        assertEquals(CallAudioStatus.NOT_APPLIED, mute.observedMute())
    }

    @Test fun captureFailureRightAfterStreamMuteWithdrawsItAndRefusesItForTheCall() {
        val audio = Audio()
        var now = 1_000L
        val mute = LocalOutputMute(audio) { now }
        mute.beginCall()
        mute.mute(false)
        now += 500
        assertEquals("restored voice-call stream (direct capture failed)",
            mute.withdrawForCaptureFailure("record read 0 at 16000 Hz"))
        assertFalse(audio.voiceCallMuted)
        assertTrue(mute.directMuteIncompatible)
        assertFalse(mute.ownsOutputChange)
        assertEquals(CallAudioStatus.UNSUPPORTED, mute.observedMute())
        assertTrue(mute.lastMuteResult.startsWith("unsupported: voice-call stream mute stopped direct capture"))
        assertTrue(mute.mute(false).startsWith("unsupported:"))
        assertEquals(listOf(true, false), audio.streamRequests)
        // Redirection mode is a different mechanism and stays available after a backend switch.
        assertEquals("muted: mode 2->5", mute.mute(true))
        mute.restore("tap closed")
        audio.mode = 2 // The fake leaves NORMAL after withdrawal; the next call starts in IN_CALL.
        mute.beginCall()
        assertNull(mute.streamMuteUnsupported)
        assertFalse(mute.directMuteIncompatible)
        assertEquals("muted: voice-call stream", mute.mute(false))
    }

    @Test fun lateCaptureFailureIsRecoveredButNotBlamedOnTheDevice() {
        val audio = Audio()
        var now = 0L
        val mute = LocalOutputMute(audio) { now }
        mute.mute(false)
        now += LocalOutputMute.MUTE_FAILURE_WINDOW_MS + 1
        assertNotNull(mute.withdrawForCaptureFailure("record read 0"))
        assertFalse(mute.directMuteIncompatible)
        assertTrue(mute.mute(false).startsWith("unsupported:"))
    }

    @Test fun captureFailureNeverTouchesAMuteWeDoNotOwn() {
        val preserved = Audio().apply { voiceCallMuted = true }
        val mute = LocalOutputMute(preserved)
        mute.mute(false)
        assertNull(mute.withdrawForCaptureFailure("record read 0"))
        assertTrue(preserved.voiceCallMuted)
        assertFalse(mute.directMuteIncompatible)

        val redirected = Audio()
        val modeMute = LocalOutputMute(redirected)
        modeMute.mute(true)
        assertNull(modeMute.withdrawForCaptureFailure("record read 0"))
        assertEquals(5, redirected.mode)
        assertNull(LocalOutputMute(Audio()).withdrawForCaptureFailure("record read 0"))
    }

    @Test fun failedWithdrawalKeepsRestorationPendingButStillRefusesMute() {
        val audio = Audio()
        val mute = LocalOutputMute(audio)
        mute.mute(false)
        audio.acceptUnmute = false
        assertTrue(mute.withdrawForCaptureFailure("record read 0")!!.contains("restoration pending"))
        assertTrue(mute.restorationPending)
        assertTrue(mute.ownsOutputChange)
        assertEquals("already muted: voice-call stream", mute.mute(false))
        audio.acceptUnmute = true
        assertEquals("restored voice-call stream (tap closed)", mute.restore("tap closed"))
        assertTrue(mute.mute(false).startsWith("unsupported:"))
    }

    @Test fun deviceBlockRefusesStreamMuteFromTheStartOfTheCall() {
        val audio = Audio()
        val mute = LocalOutputMute(audio)
        mute.beginCall("it previously stopped direct capture on this device")
        assertEquals("unsupported: it previously stopped direct capture on this device", mute.mute(false))
        assertTrue(mute.requested)
        assertEquals(CallAudioStatus.UNSUPPORTED, mute.observedMute())
        assertTrue(audio.streamRequests.isEmpty())
        assertEquals("not muted", mute.restore("tap closed"))
        assertEquals("muted: mode 2->5", mute.mute(true))
    }
}
