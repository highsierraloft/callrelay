package io.github.sparkcyf.voicesipagent.pjsua2

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.CompletionException
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.TimeoutException
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

class PjsuaWorkerTest {
    @Test fun failedDestructionFailsTheReleaseCompletion() {
        val errors = CopyOnWriteArrayList<Throwable>()
        val failure = IllegalStateException("destroy failed")
        val worker = PjsuaWorker({}, { Thread.sleep(1) }, { throw failure }, { errors += it })
        worker.start()
        val released = worker.releaseCompletion()
        worker.stop()
        val reported = assertThrows(CompletionException::class.java) { released.join() }
        assertSame(failure, reported.cause)
        assertEquals(listOf(failure), errors)
        assertFalse(worker.running)
    }

    @Test fun explicitRestartWaitsForFailedLibraryDestructionBeforeInitializingAgain() {
        val attempts = AtomicInteger()
        val failPoll = CountDownLatch(1)
        val releasing = CountDownLatch(1)
        val finishRelease = CountDownLatch(1)
        val restartEntered = CountDownLatch(1)
        val starter = Executors.newSingleThreadExecutor()
        val worker = PjsuaWorker(
            initialize = { attempts.incrementAndGet() },
            poll = {
                if (attempts.get() == 1) {
                    check(failPoll.await(3, TimeUnit.SECONDS))
                    error("poll failed")
                }
                Thread.sleep(1)
            },
            release = {
                if (attempts.get() == 1) {
                    releasing.countDown()
                    check(finishRelease.await(3, TimeUnit.SECONDS))
                }
            },
            onFailure = { throw AssertionError(it) },
            onTerminated = {},
        )
        try {
            worker.start()
            failPoll.countDown()
            assertTrue(releasing.await(2, TimeUnit.SECONDS))
            val restart = starter.submit { restartEntered.countDown(); worker.start() }
            assertTrue(restartEntered.await(2, TimeUnit.SECONDS))
            assertThrows(TimeoutException::class.java) { restart.get(100, TimeUnit.MILLISECONDS) }
            assertEquals(1, attempts.get())
            finishRelease.countDown()
            restart.get(3, TimeUnit.SECONDS)
            assertEquals(2, attempts.get())
            assertTrue(worker.running)
        } finally {
            failPoll.countDown()
            finishRelease.countDown()
            starter.shutdown()
            assertTrue(starter.awaitTermination(4, TimeUnit.SECONDS))
            worker.stop()
        }
    }

    @Test fun failedPollNotifiesOnceAndDrainsDeferredCleanupBeforeDestruction() {
        val events = CopyOnWriteArrayList<String>()
        val errors = CopyOnWriteArrayList<Throwable>()
        val polling = CountDownLatch(1)
        val failPoll = CountDownLatch(1)
        val destroyed = CountDownLatch(1)
        val owner = AtomicReference<Thread>()
        val failure = IllegalStateException("poll failed")
        lateinit var worker: PjsuaWorker
        worker = PjsuaWorker(
            initialize = { owner.set(Thread.currentThread()) },
            poll = {
                polling.countDown()
                check(failPoll.await(3, TimeUnit.SECONDS))
                // Native callbacks can defer deletion before libHandleEvents itself throws.
                worker.post { events += "callback delete" }
                throw failure
            },
            release = { events += "destroy"; destroyed.countDown() },
            onFailure = { errors += it },
            onTerminated = {
                assertSame(failure, it)
                assertSame(owner.get(), Thread.currentThread())
                assertFalse(worker.running)
                events += "failed"
                worker.post { error("one delete failed") }
                worker.post { events += "failure delete" }
                // External commands must not be admitted once failure handling begins.
                val accepted = AtomicReference<Boolean>()
                Thread { accepted.set(worker.post { events += "late control" }) }.apply { start(); join(1000) }
                assertEquals(false, accepted.get())
            },
        )
        try {
            worker.start()
            assertTrue(polling.await(2, TimeUnit.SECONDS))
            worker.post { events += "queued control" }
            failPoll.countDown()
            assertTrue(destroyed.await(3, TimeUnit.SECONDS))
        } finally {
            failPoll.countDown()
            worker.stop()
        }
        assertEquals(listOf("failed", "queued control", "callback delete", "failure delete", "destroy"), events)
        assertEquals(listOf("one delete failed"), errors.map { it.message })
        assertFalse(worker.running)
    }

    @Test fun taskFailureLeavesTheWorkerRunningAndNormalStopDoesNotReportTermination() {
        val errors = CopyOnWriteArrayList<Throwable>()
        val terminations = AtomicInteger()
        val settled = CountDownLatch(1)
        val failure = IllegalStateException("command failed")
        val worker = PjsuaWorker({}, { Thread.sleep(1) }, {},
            onFailure = { errors += it }, onTerminated = { terminations.incrementAndGet() })
        try {
            worker.start()
            worker.post { throw failure }
            worker.post { settled.countDown() }
            assertTrue(settled.await(2, TimeUnit.SECONDS))
            assertTrue(worker.running)
        } finally { worker.stop() }
        assertEquals(listOf(failure), errors)
        assertEquals(0, terminations.get())
    }

    @Test fun stoppingDrainsDeferredDeletesBeforeNativeDestructionAndRestartsOnANewThread() {
        val events = mutableListOf<String>()
        val thread = AtomicReference<Thread>()
        val entered = CountDownLatch(1)
        val finishTask = CountDownLatch(1)
        val worker = PjsuaWorker({ thread.set(Thread.currentThread()); events += "initialize" },
            { Thread.sleep(1) }, { events += "destroy" }, { throw AssertionError(it) })
        worker.start()
        val first = thread.get()
        worker.post {
            entered.countDown()
            assertTrue(finishTask.await(2, TimeUnit.SECONDS))
            events += "account cleanup"
            worker.post { events += "delete wrappers" }
        }
        assertTrue(entered.await(2, TimeUnit.SECONDS))
        val stopEntered = CountDownLatch(1)
        val stopper = Thread { stopEntered.countDown(); worker.stop() }.apply { start() }
        assertTrue(stopEntered.await(2, TimeUnit.SECONDS))
        finishTask.countDown()
        stopper.join(3000)
        assertFalse(stopper.isAlive)
        assertFalse(first.isAlive)
        assertFalse(worker.running)
        assertEquals(listOf("initialize", "account cleanup", "delete wrappers", "destroy"), events)
        worker.start()
        assertNotSame(first, thread.get())
        worker.stop()
        worker.stop()
        assertEquals(2, events.count { it == "destroy" })
    }

    @Test fun timersAndLatePostsFromStoppedLibraryCannotReachTheNextLifetime() {
        val leaked = AtomicInteger()
        val worker = PjsuaWorker({}, { Thread.sleep(1) }, {}, { throw AssertionError(it) })
        worker.start()
        worker.schedule(100) { leaked.incrementAndGet() }
        worker.every(100) { leaked.incrementAndGet() }
        worker.stop()
        worker.post { leaked.incrementAndGet() }
        worker.start()
        val settled = CountDownLatch(1)
        worker.schedule(200) { settled.countDown() }
        assertTrue(settled.await(2, TimeUnit.SECONDS))
        worker.stop()
        assertEquals(0, leaked.get())
    }

    @Test fun failedInitializationIsReleasedAndDoesNotPreventRetry() {
        val attempts = AtomicInteger()
        val releases = AtomicInteger()
        val terminations = AtomicInteger()
        val worker = PjsuaWorker({ if (attempts.incrementAndGet() == 1) error("native init failed") },
            { Thread.sleep(1) }, { releases.incrementAndGet() }, { throw AssertionError(it) },
            onTerminated = { terminations.incrementAndGet() })
        try { worker.start(); fail("initialization should fail") } catch (_: IllegalStateException) {}
        assertFalse(worker.running)
        assertEquals(1, releases.get())
        worker.start()
        worker.stop()
        assertEquals(2, releases.get())
        assertEquals(0, terminations.get())
    }
}
