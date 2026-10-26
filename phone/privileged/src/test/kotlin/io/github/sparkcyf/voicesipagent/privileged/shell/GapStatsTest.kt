package io.github.sparkcyf.voicesipagent.privileged.shell

import org.junit.Assert.assertEquals
import org.junit.Test

class GapStatsTest {
    private val ms = 1_000_000L

    @Test fun firstMarkOnlySetsReference() {
        val g = GapStats()
        g.mark(5_000 * ms)
        assertEquals(0L, g.maxMs)
        assertEquals(0L, g.takeWindowMax())
    }

    @Test fun countsLongGapsAndResetsWindowMaximum() {
        val g = GapStats()
        var t = 0L
        g.mark(t)
        for (gap in listOf(20L, 20L, 60L, 20L, 140L, 20L)) { t += gap * ms; g.mark(t) }
        assertEquals(140L, g.maxMs)
        assertEquals(2L, g.over40)
        assertEquals(1L, g.over100)
        assertEquals(140L, g.takeWindowMax())
        t += 20 * ms; g.mark(t)
        assertEquals(20L, g.takeWindowMax())
        assertEquals(140L, g.maxMs)
        assertEquals("max=140ms >40=2 >100=1", g.toString())
    }

    @Test fun recordTracksBlockingDurations() {
        val g = GapStats()
        g.record(3); g.record(45)
        assertEquals(45L, g.maxMs)
        assertEquals(1L, g.over40)
    }
}
