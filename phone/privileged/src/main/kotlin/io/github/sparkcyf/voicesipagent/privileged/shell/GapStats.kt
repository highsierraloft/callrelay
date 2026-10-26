package io.github.sparkcyf.voicesipagent.privileged.shell

/**
 * Timing diagnostics for one 20 ms frame stream: gaps between events (or blocking durations),
 * kept as counters only. One writer thread; readers may see slightly stale values.
 */
internal class GapStats {
    private var lastNanos = -1L
    @Volatile var maxMs = 0L; private set
    @Volatile var over40 = 0L; private set
    @Volatile var over100 = 0L; private set
    @Volatile private var windowMaxMs = 0L

    /** Records the gap since the previous [mark]; the first mark only sets the reference. */
    fun mark(nowNanos: Long) {
        if (lastNanos >= 0) record((nowNanos - lastNanos) / 1_000_000)
        lastNanos = nowNanos
    }

    fun record(ms: Long) {
        if (ms > maxMs) maxMs = ms
        if (ms > windowMaxMs) windowMaxMs = ms
        if (ms > 40) over40++
        if (ms > 100) over100++
    }

    /** Largest value since the previous call (the 5 s levels window). */
    fun takeWindowMax(): Long = windowMaxMs.also { windowMaxMs = 0 }

    override fun toString() = "max=${maxMs}ms >40=$over40 >100=$over100"
}
