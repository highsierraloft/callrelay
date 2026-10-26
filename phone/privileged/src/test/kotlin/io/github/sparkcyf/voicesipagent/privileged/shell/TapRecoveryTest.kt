package io.github.sparkcyf.voicesipagent.privileged.shell

import io.github.sparkcyf.voicesipagent.privileged.AudioMethod
import io.github.sparkcyf.voicesipagent.privileged.shell.TapRecovery.Action
import org.junit.Assert.*
import org.junit.Test

class TapRecoveryTest {
    @Test fun autoOrderIsTheSingleConfiguredConstant() {
        assertEquals(TapRecovery.AUTO_ORDER, TapRecovery(AudioMethod.AUTO).plan)
        assertEquals(listOf(TapBackend.REDIRECTION, TapBackend.DIRECT), TapRecovery.AUTO_ORDER)
        assertEquals(listOf(TapBackend.REDIRECTION), TapRecovery(AudioMethod.OFFICIAL).plan)
        assertEquals(listOf(TapBackend.DIRECT), TapRecovery(AudioMethod.LEGACY).plan)
        assertTrue(runCatching { TapRecovery(7) }.isFailure)
    }

    @Test fun directMuteFailureThenTransientFailureEachGetOneRecordRecreate() {
        val r = TapRecovery(AudioMethod.LEGACY).apply { opened(TapBackend.DIRECT) }
        assertEquals(Action.RecreateRecord, r.onCaptureFailure(muteWithdrawn = true))
        assertEquals(Action.RecreateRecord, r.onCaptureFailure(muteWithdrawn = false))
        assertEquals(Action.Close, r.onCaptureFailure(muteWithdrawn = false))
    }

    @Test fun transientFailureBeforeMuteStillLeavesTheMuteRecovery() {
        val r = TapRecovery(AudioMethod.LEGACY).apply { opened(TapBackend.DIRECT) }
        assertEquals(Action.RecreateRecord, r.onCaptureFailure(muteWithdrawn = false))
        assertEquals(Action.RecreateRecord, r.onCaptureFailure(muteWithdrawn = true))
        assertEquals(Action.Close, r.onCaptureFailure(muteWithdrawn = true))
    }

    @Test fun repeatedTransientFailureIsRecreatedOnlyOnce() {
        val r = TapRecovery(AudioMethod.LEGACY).apply { opened(TapBackend.DIRECT) }
        assertEquals(Action.RecreateRecord, r.onCaptureFailure(muteWithdrawn = false))
        assertEquals(Action.Close, r.onCaptureFailure(muteWithdrawn = false))
    }

    @Test fun autoRedirectionCaptureFailureFallsBackToDirect() {
        val r = TapRecovery(AudioMethod.AUTO).apply { opened(TapBackend.REDIRECTION) }
        assertEquals(Action.Switch(TapBackend.DIRECT), r.onCaptureFailure(muteWithdrawn = false))
        r.opened(TapBackend.DIRECT)
        assertEquals(Action.RecreateRecord, r.onCaptureFailure(muteWithdrawn = false))
        assertEquals(Action.Close, r.onCaptureFailure(muteWithdrawn = false))
    }

    @Test fun explicitRedirectionNeverFallsBack() {
        val r = TapRecovery(AudioMethod.OFFICIAL).apply { opened(TapBackend.REDIRECTION) }
        assertEquals(Action.Close, r.onCaptureFailure(muteWithdrawn = false))
        assertNull(r.onDownlinkFrames(TapRecovery.ZERO_DOWNLINK_FRAMES, 0))
    }

    @Test fun digitalZeroRedirectionDownlinkSwitchesToDirectAfterThreeSeconds() {
        val r = TapRecovery(AudioMethod.AUTO).apply { opened(TapBackend.REDIRECTION) }
        assertNull(r.onDownlinkFrames(TapRecovery.ZERO_DOWNLINK_FRAMES - 1, 0))
        assertEquals(Action.Switch(TapBackend.DIRECT), r.onDownlinkFrames(TapRecovery.ZERO_DOWNLINK_FRAMES, 0))
        r.opened(TapBackend.DIRECT)
        // DIRECT is last in AUTO: a silent downlink there is left alone.
        assertNull(r.onDownlinkFrames(TapRecovery.ZERO_DOWNLINK_FRAMES, 0))
    }

    @Test fun anyDownlinkSignalPassesTheCheckOnce() {
        val r = TapRecovery(AudioMethod.AUTO).apply { opened(TapBackend.REDIRECTION) }
        assertNull(r.onDownlinkFrames(TapRecovery.ZERO_DOWNLINK_FRAMES, 1))
        assertNull(r.onDownlinkFrames(TapRecovery.ZERO_DOWNLINK_FRAMES + 500, 0))
    }

    @Test fun directOpenedByAutoAfterRedirectionFailedAtOpenHasNoFurtherFallback() {
        val r = TapRecovery(AudioMethod.AUTO).apply { opened(TapBackend.DIRECT) }
        assertEquals(TapBackend.DIRECT, r.backend)
        assertNull(r.onDownlinkFrames(TapRecovery.ZERO_DOWNLINK_FRAMES, 0))
    }
}
