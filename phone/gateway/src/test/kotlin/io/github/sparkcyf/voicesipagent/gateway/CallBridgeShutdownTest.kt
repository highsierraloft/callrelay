package io.github.sparkcyf.voicesipagent.gateway

import io.github.sparkcyf.voicesipagent.voice.HangupCause
import io.github.sparkcyf.voicesipagent.voice.LegDirection
import io.github.sparkcyf.voicesipagent.voice.LegState
import io.github.sparkcyf.voicesipagent.voice.Party
import org.junit.After
import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class CallBridgeShutdownTest {
    private val cell = FakeEndpoint("cellular")
    private val sip = FakeEndpoint("sip")
    private val logs = CopyOnWriteArrayList<String>()
    private val sessionEnded = CountDownLatch(1)
    private val bridge = newBridge().also { bridge ->
        bridge.addListener { if (it.endedAtMs != null) sessionEnded.countDown() }
        bridge.start()
    }

    private fun newBridge() = CallBridge(
        sideA = cell,
        sideB = sip,
        routeAtoB = { "302" },
        routeBtoA = { it.target },
        config = GatewayConfig(),
        log = { logs += it },
    )

    @After fun tearDown() = bridge.stop()

    @Test fun capturedIncomingHandlersRejectCallsAfterStop() {
        val handlers = listOf(cell.handler!!, sip.handler!!)
        bridge.stop()
        bridge.drain()

        handlers.forEachIndexed { index, handler ->
            val leg = FakeLeg("late-$index", LegDirection.INBOUND, Party("302"), "12345")
            leg.remoteProceeding()
            handler.onIncomingCall(leg)
            assertEquals(LegState.ENDED, leg.state)
            assertEquals(HangupCause.UNAVAILABLE, leg.endCause)
        }
        assertTrue(cell.dialed.isEmpty())
        assertTrue(sip.dialed.isEmpty())
        assertNull(bridge.session)
    }

    @Test fun stopRejectsQueuedCallsAndCleansUpInProgressDial() {
        val entered = CountDownLatch(1)
        val release = CountDownLatch(1)
        val cellPort = FakePort(isClockSource = true, fill = 11)
        sip.onDial = { leg ->
            leg.remoteAnswered()
            leg.mediaUp(FakePort(isClockSource = false, fill = 22))
            entered.countDown()
            check(release.await(5, TimeUnit.SECONDS))
        }
        try {
            val inbound = cell.incoming("12345")
            assertTrue(entered.await(2, TimeUnit.SECONDS))
            inbound.mediaUp(cellPort)
            val queuedCell = cell.incoming("23456")
            val queuedSip = sip.incoming("302", target = "34567")
            bridge.stop()
            release.countDown()
            assertTrue(sessionEnded.await(2, TimeUnit.SECONDS))
            bridge.drain()

            assertEquals(HangupCause.UNAVAILABLE, queuedCell.endCause)
            assertEquals(HangupCause.UNAVAILABLE, queuedSip.endCause)
            assertTrue(cell.dialed.isEmpty())
            assertEquals(LegState.ENDED, inbound.state)
            assertEquals(LegState.ENDED, sip.dialed.single().state)
            assertNull(bridge.session)
            assertNull(cellPort.clock)
            assertTrue(logs.toString(), logs.none { it.startsWith("bridge task failed:") })

            // Endpoint callbacks can still arrive after the bridge's executor has shut down.
            inbound.remoteDtmf('5')
            inbound.mediaUp(cellPort)
            assertTrue(sip.dialed.single().dtmf.isEmpty())
        } finally {
            release.countDown()
        }
    }

    @Test fun repeatedStopDoesNotClearReplacementHandlers() {
        bridge.stop()
        bridge.drain()
        val replacement = newBridge().also { it.start() }
        try {
            bridge.stop()
            assertNotNull(cell.handler)
            assertNotNull(sip.handler)
            val inbound = sip.incoming("302", target = "12345")
            repeat(3) { replacement.drain() }
            assertEquals(1, cell.dialed.size)
            assertSame(inbound, replacement.session?.inbound)
        } finally {
            replacement.stop()
            replacement.drain()
        }
    }
}
