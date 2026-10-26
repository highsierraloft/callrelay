package io.github.sparkcyf.voicesipagent.sip

import org.junit.Assert.*
import org.junit.Test
import java.net.DatagramSocket
import java.net.DatagramPacket
import java.net.InetAddress
import java.net.UnknownHostException
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

class SipShutdownTest {
    @Test fun lateRegistrationSuccessDuringUnregisterCannotRestoreReadiness() {
        DatagramSocket(0).use { peer ->
            peer.soTimeout = 2000
            val ua = SipUserAgent(SipConfig(SipAccount("127.0.0.1", peer.localPort, "test", "test"), localPort = 0))
            val readyCount = AtomicInteger()
            val stopped = CountDownLatch(1)
            ua.addStatusListener { _, status ->
                if (status.ready) readyCount.incrementAndGet()
                if (status.detail == "stopped") stopped.countDown()
            }
            try {
                ua.start()
                val packet = DatagramPacket(ByteArray(4096), 4096)
                peer.receive(packet)
                val registration = SipParser.parse(packet.data, packet.length) as SipRequest
                val stopping = CompletableFuture.runAsync { ua.stop() }
                var unregister: SipRequest
                do {
                    peer.receive(packet)
                    unregister = SipParser.parse(packet.data, packet.length) as SipRequest
                } while (unregister.headers["Expires"] != "0")

                // The original REGISTER succeeds after shutdown has already sent its un-REGISTER.
                for (request in listOf(registration, unregister)) {
                    val response = ua.buildResponse(request, 200).apply {
                        headers.set("Expires", request.headers["Expires"]!!)
                    }.encode()
                    peer.send(DatagramPacket(response, response.size, packet.socketAddress))
                }
                stopping.get(3, TimeUnit.SECONDS)
                assertTrue(stopped.await(2, TimeUnit.SECONDS))
                assertEquals(0, readyCount.get())
                assertFalse(ua.status.ready)
            } finally { ua.stop(timeoutMs = 50) }
        }
    }

    @Test fun resolutionFailureIsReportedAndRegistrationRecoversOnNetworkRefresh() {
        DatagramSocket(0).use { peer ->
            peer.soTimeout = 2000
            val attempts = AtomicInteger()
            val failed = CountDownLatch(1)
            val registered = CountDownLatch(1)
            val ua = SipUserAgent(SipConfig(SipAccount("127.0.0.1", peer.localPort, "test", "test"), localPort = 0),
                object : SipLog {
                    override fun info(msg: String) {}
                    override fun warn(msg: String, t: Throwable?) {}
                }, localAddressProvider = {
                    if (attempts.incrementAndGet() == 1) throw UnknownHostException("temporary resolution failure")
                    InetAddress.getLoopbackAddress()
                })
            ua.addStatusListener { _, status ->
                if (status.detail.startsWith("registration failed: cannot resolve")) failed.countDown()
                if (status.ready) registered.countDown()
            }
            try {
                ua.start()
                assertTrue(failed.await(2, TimeUnit.SECONDS))
                assertFalse(ua.status.ready)
                ua.refreshRegistration()
                val packet = DatagramPacket(ByteArray(4096), 4096)
                peer.receive(packet)
                val request = SipParser.parse(packet.data, packet.length) as SipRequest
                assertEquals("REGISTER", request.method)
                val response = ua.buildResponse(request, 200).apply { headers.set("Expires", "60") }.encode()
                peer.send(DatagramPacket(response, response.size, packet.socketAddress))
                assertTrue(registered.await(2, TimeUnit.SECONDS))
                assertEquals(2, attempts.get())
            } finally { ua.stop(timeoutMs = 50) }
        }
    }

    @Test fun unregisterTimeoutStillClosesSocketAndCancelsLongTransactionTimers() {
        DatagramSocket(0).use { peer ->
            val log = object : SipLog {
                override fun info(msg: String) {}
                override fun warn(msg: String, t: Throwable?) {}
            }
            val ua = SipUserAgent(SipConfig(SipAccount("127.0.0.1", peer.localPort, "test", "test"), localPort = 0), log)
            val ready = CountDownLatch(1)
            val thread = AtomicReference<Thread>()
            ua.addStatusListener { _, status ->
                if (status.detail == "registering") { thread.set(Thread.currentThread()); ready.countDown() }
            }
            try {
                ua.start()
                assertTrue(ready.await(2, TimeUnit.SECONDS))
                val port = ua.transport.localPort
                val lateTask = AtomicInteger()
                ua.schedule(60_000) { lateTask.incrementAndGet() }
                // The local peer deliberately never answers either REGISTER.
                assertTrue(ua.stop(timeoutMs = 50).isDone)
                // stop now waits for local release, independently of worker termination.
                DatagramSocket(port).use { assertEquals(port, it.localPort) }
                thread.get().join(2000)
                assertFalse(thread.get().isAlive)
                assertFalse(ua.status.ready)
                assertEquals("stopped", ua.status.detail)
                assertEquals(0, lateTask.get())
            } finally {
                if (thread.get()?.isAlive == true) ua.stop(timeoutMs = 50)
            }
        }
    }

    @Test fun socketReleaseCompletionSurvivesABoundedStopTimeout() {
        DatagramSocket(0).use { peer ->
            val warnings = java.util.concurrent.CopyOnWriteArrayList<String>()
            val ua = SipUserAgent(SipConfig(SipAccount("127.0.0.1", peer.localPort, "test", "test"), localPort = 0),
                object : SipLog {
                    override fun info(msg: String) {}
                    override fun warn(msg: String, t: Throwable?) { warnings += msg }
                })
            val entered = CountDownLatch(1)
            val release = CountDownLatch(1)
            try {
                ua.start()
                ua.post { entered.countDown(); check(release.await(5, TimeUnit.SECONDS)) }
                assertTrue(entered.await(2, TimeUnit.SECONDS))
                val port = ua.transport.localPort
                val stopped = ua.stop(timeoutMs = 10)
                assertFalse(stopped.isDone)
                assertTrue(warnings.any { it.contains("socket release wait timed out") })
                assertSame(stopped, ua.stop(timeoutMs = 0))
                release.countDown()
                stopped.get(3, TimeUnit.SECONDS)
                DatagramSocket(port).use { assertEquals(port, it.localPort) }
            } finally {
                release.countDown()
                ua.stop(timeoutMs = 10).get(3, TimeUnit.SECONDS)
            }
        }
    }
}
