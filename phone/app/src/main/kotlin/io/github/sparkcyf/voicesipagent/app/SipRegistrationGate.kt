package io.github.sparkcyf.voicesipagent.app

/**
 * Decides when the SIP account stays unregistered because the cellular side cannot carry calls,
 * so the PBX sees the trunk unavailable and can fail over. Readiness must exclude SIP itself.
 *
 * Until the cellular side has been ready once in this gateway run, SIP is held: startup and
 * switch-on never register and then unregister again. After that, a loss of readiness must last
 * [graceMs] before the hold starts, so brief Shizuku reconnects or health polls do not flap the
 * registration. Readiness lifts the hold at once. The caller defers applying a decision while
 * calls or a bridge session exist. Not thread-safe; the runtime uses it on its executor.
 */
internal class SipRegistrationGate(private val graceMs: Long, private val clock: () -> Long) {
    /** [recheckAtMs]: when to evaluate again if nothing else changes (the grace deadline). */
    data class Decision(val hold: Boolean, val recheckAtMs: Long? = null)

    private var everReady = false
    private var notReadySinceMs: Long? = null
    var held = false
        private set

    /** The next gateway run starts held again. */
    fun reset() {
        everReady = false
        notReadySinceMs = null
        held = false
    }

    /** [active]: the option is on and SIP would otherwise register. */
    fun evaluate(active: Boolean, ready: Boolean): Decision {
        if (!active) {
            reset()
            return Decision(false)
        }
        if (ready) {
            everReady = true
            notReadySinceMs = null
            held = false
            return Decision(false)
        }
        if (held) return Decision(true)
        if (!everReady) {
            held = true
            return Decision(true)
        }
        val now = clock()
        val deadline = (notReadySinceMs ?: now.also { notReadySinceMs = it }) + graceMs
        if (now >= deadline) {
            held = true
            return Decision(true)
        }
        return Decision(false, deadline)
    }
}
