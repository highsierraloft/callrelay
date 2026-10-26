package io.github.sparkcyf.voicesipagent.app

import org.junit.Assert.*
import org.junit.Test

class SipRegistrationGateTest {
    private var now = 1_000L
    private val gate = SipRegistrationGate(45_000) { now }
    private fun evaluate(ready: Boolean, active: Boolean = true) = gate.evaluate(active, ready)

    @Test fun holdsFromStartUntilFirstReady() {
        assertEquals(SipRegistrationGate.Decision(true), evaluate(ready = false))
        now += 600_000
        assertTrue(evaluate(ready = false).hold)
        assertEquals(SipRegistrationGate.Decision(false), evaluate(ready = true))
        assertFalse(gate.held)
    }

    @Test fun lossAfterReadyWaitsForGracePeriod() {
        evaluate(ready = true)
        val first = evaluate(ready = false)
        assertFalse(first.hold)
        assertEquals(46_000L, first.recheckAtMs)
        now += 44_999
        assertEquals(SipRegistrationGate.Decision(false, 46_000L), evaluate(ready = false))
        now += 1
        assertEquals(SipRegistrationGate.Decision(true), evaluate(ready = false))
        assertTrue(gate.held)
        now += 3_600_000
        assertTrue(evaluate(ready = false).hold)
    }

    @Test fun briefLossRestartsGracePeriod() {
        evaluate(ready = true)
        evaluate(ready = false)
        now += 30_000
        assertFalse(evaluate(ready = true).hold)
        now += 1_000
        val again = evaluate(ready = false)
        assertFalse(again.hold)
        assertEquals(now + 45_000, again.recheckAtMs)
        now += 30_000
        assertFalse(evaluate(ready = false).hold)
    }

    @Test fun readinessLiftsHoldImmediately() {
        evaluate(ready = true)
        evaluate(ready = false)
        now += 45_000
        assertTrue(evaluate(ready = false).hold)
        assertEquals(SipRegistrationGate.Decision(false), evaluate(ready = true))
        // A later loss gets a fresh grace period.
        now += 5_000
        assertFalse(evaluate(ready = false).hold)
    }

    @Test fun inactiveNeverHoldsAndRestartsHeld() {
        evaluate(ready = true)
        assertEquals(SipRegistrationGate.Decision(false), evaluate(ready = false, active = false))
        assertFalse(gate.held)
        // Option switched back on (or a new gateway run) while not ready: no grace without a ready period.
        assertTrue(evaluate(ready = false).hold)
    }

    @Test fun resetStartsHeldAgain() {
        evaluate(ready = true)
        gate.reset()
        assertTrue(evaluate(ready = false).hold)
    }

    @Test fun clockReadOnlyWhileTimingGrace() {
        val strict = SipRegistrationGate(45_000) { error("clock read") }
        assertFalse(strict.evaluate(active = false, ready = false).hold)
        assertTrue(strict.evaluate(active = true, ready = false).hold)
        assertFalse(strict.evaluate(active = true, ready = true).hold)
    }
}
