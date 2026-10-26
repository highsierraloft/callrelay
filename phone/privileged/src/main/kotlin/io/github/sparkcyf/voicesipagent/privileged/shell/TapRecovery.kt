package io.github.sparkcyf.voicesipagent.privileged.shell

import io.github.sparkcyf.voicesipagent.privileged.AudioMethod

/** Capture/injection backends the tap can open. */
internal enum class TapBackend { REDIRECTION, DIRECT }

/**
 * Pure decision logic for keeping a call's audio tap alive. [CallAudioTap] performs the actions;
 * this class only tracks which recoveries the current tap has already used.
 */
internal class TapRecovery(method: Int) {
    sealed interface Action {
        /** Rebuild only the capture record of the current backend, at the same rate. */
        data object RecreateRecord : Action
        /** Release the current backend and open [backend] mid-call. */
        data class Switch(val backend: TapBackend) : Action
        data object Close : Action
    }

    /** Backends this tap may use, in order. Only AUTO has more than one. */
    val plan: List<TapBackend> = when (method) {
        AudioMethod.AUTO -> AUTO_ORDER
        AudioMethod.OFFICIAL -> listOf(TapBackend.REDIRECTION)
        AudioMethod.LEGACY -> listOf(TapBackend.DIRECT)
        else -> throw IllegalArgumentException("unknown audio method $method")
    }

    private var current = -1
    private var muteRecoveryUsed = false
    private var transientRecoveryUsed = false
    private var zeroCheckDone = false

    val backend: TapBackend? get() = plan.getOrNull(current)

    /** Records which plan entry actually opened, so fallbacks only move forward. */
    fun opened(backend: TapBackend) {
        val index = plan.indexOf(backend)
        require(index >= 0) { "$backend is not in $plan" }
        current = index
        // Each backend gets its own record recoveries and downlink check.
        muteRecoveryUsed = false
        transientRecoveryUsed = false
        zeroCheckDone = false
    }

    /**
     * The record returned <= 0. [muteWithdrawn] is true when our own voice-call stream mute was
     * just withdrawn because of this failure.
     */
    fun onCaptureFailure(muteWithdrawn: Boolean): Action {
        if (backend == TapBackend.DIRECT) {
            if (muteWithdrawn && !muteRecoveryUsed) {
                muteRecoveryUsed = true
                return Action.RecreateRecord
            }
            if (!transientRecoveryUsed) {
                transientRecoveryUsed = true
                return Action.RecreateRecord
            }
        }
        return next()?.let(Action::Switch) ?: Action.Close
    }

    /**
     * Called for each captured frame with the current backend's frame count and peak. Returns a
     * switch when the downlink stayed digital zero for [ZERO_DOWNLINK_FRAMES]; checked once.
     */
    fun onDownlinkFrames(frames: Long, peak: Int): Action? {
        if (zeroCheckDone || frames < ZERO_DOWNLINK_FRAMES) return null
        zeroCheckDone = true
        if (peak != 0) return null
        return next()?.let(Action::Switch)
    }

    private fun next(): TapBackend? = plan.getOrNull(current + 1)

    companion object {
        /**
         * AUTO order. Field reports (issue #1: POCO F6/F3, vivo V30 Lite, Galaxy A15, Redmi Note 13 Pro)
         * only got two-way audio from DIRECT, but redirection is the only backend verified on the
         * Pixel 11 Pro. Decide direct-first after testing DIRECT on the Pixel 11 Pro and moto.
         */
        val AUTO_ORDER = listOf(TapBackend.REDIRECTION, TapBackend.DIRECT)

        /** 3 s of 20 ms frames; a live cellular downlink carries at least comfort noise by then. */
        const val ZERO_DOWNLINK_FRAMES = 150L
    }
}
