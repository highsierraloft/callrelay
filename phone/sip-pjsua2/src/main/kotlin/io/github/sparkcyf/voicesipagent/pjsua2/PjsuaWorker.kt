package io.github.sparkcyf.voicesipagent.pjsua2

import java.util.concurrent.CountDownLatch
import java.util.concurrent.CompletableFuture
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.ScheduledThreadPoolExecutor
import java.util.concurrent.TimeUnit

/** Owns one native-library lifetime. Timers and queued work cannot cross a stop/restart. */
internal class PjsuaWorker(
    private val initialize: () -> Unit,
    private val poll: () -> Unit,
    private val release: () -> Unit,
    private val onFailure: (Throwable) -> Unit,
    /** Runs on the owner thread before deferred cleanup and native destruction. */
    private val onTerminated: (Throwable) -> Unit = onFailure,
) {
    private class Session {
        val queue = LinkedBlockingQueue<() -> Unit>()
        val timers = ScheduledThreadPoolExecutor(1) { task ->
            Thread(task, "pjsua-timer").apply { isDaemon = true }
        }.apply { removeOnCancelPolicy = true }
        @Volatile var stopping = false
        val released = CompletableFuture<Unit>()
        lateinit var thread: Thread
    }

    private val lock = Any()
    @Volatile private var session: Session? = null
    val running: Boolean get() = session?.let { !it.stopping } == true

    fun start() = synchronized(lock) {
        session?.let {
            if (it.thread.isAlive) {
                if (!it.stopping) return@synchronized
                // A manual restart can arrive while failure cleanup is still destroying the library.
                check(Thread.currentThread() !== it.thread) { "cannot restart pjsua2 inside its callback" }
                it.thread.join(5000)
                check(!it.thread.isAlive) { "pjsua2 shutdown did not complete" }
            }
            session = null
        }
        val current = Session()
        val ready = CountDownLatch(1)
        var failure: Throwable? = null
        current.thread = Thread({
            try {
                try { initialize() } catch (t: Throwable) { failure = t; return@Thread }
                finally { ready.countDown() }
                while (true) {
                    drain(current)
                    if (current.stopping && current.queue.isEmpty()) break
                    poll()
                }
            } catch (t: Throwable) {
                beginStop(current)
                onTerminated(t)
            }
            finally {
                beginStop(current)
                // A failed poll may already have queued wrapper deletions from native callbacks.
                // The termination handler also ends calls and queues their remaining cleanup.
                try { drain(current) } finally {
                    try {
                        release()
                        current.released.complete(Unit)
                    } catch (t: Throwable) {
                        current.released.completeExceptionally(t)
                        onFailure(t)
                    }
                }
            }
        }, "pjsua").apply { isDaemon = true; priority = Thread.MAX_PRIORITY - 1 }
        session = current
        current.thread.start()
        ready.await()
        failure?.let {
            current.thread.join()
            session = null
            throw IllegalStateException("pjsua2 init failed: ${it.message}", it)
        }
    }

    private fun drain(current: Session) {
        while (true) {
            val task = current.queue.poll() ?: return
            try { task() } catch (t: Throwable) { onFailure(t) }
        }
    }

    private fun beginStop(current: Session) = synchronized(current) {
        current.stopping = true
        current.timers.shutdownNow()
    }

    private fun enqueue(current: Session, task: () -> Unit): Boolean {
        // Native callbacks may enqueue object deletion during shutdown. Drain those on their own
        // thread, while ignoring late external callbacks and timer work from the previous lifetime.
        return synchronized(current) {
            if (session === current && (!current.stopping || Thread.currentThread() === current.thread)) current.queue.add(task)
            else false
        }
    }

    fun post(task: () -> Unit): Boolean = session?.let { enqueue(it, task) } ?: false

    fun schedule(delay: Long, task: () -> Unit): ScheduledFuture<*> {
        val current = session ?: error("pjsua2 stopped")
        return current.timers.schedule({ enqueue(current, task) }, delay, TimeUnit.MILLISECONDS)
    }

    fun every(period: Long, task: () -> Unit): ScheduledFuture<*> {
        val current = session ?: error("pjsua2 stopped")
        return current.timers.scheduleWithFixedDelay({ enqueue(current, task) }, period, period, TimeUnit.MILLISECONDS)
    }

    /** Actual native release, independently of the caller's bounded stop wait. */
    fun releaseCompletion(): CompletableFuture<Unit> = session?.released ?: CompletableFuture.completedFuture(Unit)

    /** Called after the endpoint has removed its account and calls; never on the pjsua thread. */
    fun stop() {
        val current = session ?: return
        check(Thread.currentThread() !== current.thread) { "cannot stop pjsua2 inside its callback" }
        beginStop(current)
        current.thread.join(5000)
        check(!current.thread.isAlive) { "pjsua2 shutdown did not complete" }
        check(current.timers.awaitTermination(1, TimeUnit.SECONDS)) { "pjsua2 timer shutdown did not complete" }
        synchronized(lock) { if (session === current) session = null }
    }
}
