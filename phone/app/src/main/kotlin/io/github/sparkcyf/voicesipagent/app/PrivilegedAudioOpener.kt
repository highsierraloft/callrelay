package io.github.sparkcyf.voicesipagent.app

import android.os.ParcelFileDescriptor
import io.github.sparkcyf.voicesipagent.privileged.CallAudioStatus
import io.github.sparkcyf.voicesipagent.privileged.PcmSocketPort
import io.github.sparkcyf.voicesipagent.privileged.ShizukuConnector
import io.github.sparkcyf.voicesipagent.telephony.CallAudioHandle
import io.github.sparkcyf.voicesipagent.telephony.CallAudioOpener
import io.github.sparkcyf.voicesipagent.voice.PcmFormat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.util.concurrent.Executor
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** Glue: the telephony layer's audio source, backed by the Shizuku shell service. */
class PrivilegedAudioOpener(
    private val shizuku: ShizukuConnector,
    private val method: () -> Int,
    private val muteEarpiece: () -> Boolean,
    private val format: () -> PcmFormat,
    private val createPort: (ParcelFileDescriptor, PcmFormat, (String) -> Unit) -> PcmSocketPort = ::PcmSocketPort,
    private val onStatusChanged: () -> Unit = {},
    /** Per-device record that voice-call stream mute stops direct capture. */
    private val directMuteBlocked: () -> Boolean = { false },
    private val onDirectMuteIncompatible: () -> Unit = {},
    private val cleanupExecutor: Executor = audioCleanupExecutor,
) : CallAudioOpener {
    private val lock = Any()
    private var generation = 0L
    private val _status = MutableStateFlow(CallAudioStatus())
    val status: StateFlow<CallAudioStatus> = _status
    private var requestedMute: Boolean? = null
    private var muteCommandFailure = ""

    private fun notifyStatusChanged() { runCatching { onStatusChanged() } }

    private fun publish(value: CallAudioStatus) {
        if (_status.value == value) return
        _status.value = value
        // Reporting must never break ownership of a working audio tap.
        notifyStatusChanged()
    }

    fun refreshStatus() = synchronized(lock) {
        val svc = shizuku.service ?: return@synchronized helperDisconnected()
        val previous = _status.value
        val reported = runCatching { CallAudioStatus.fromBundle(svc.callAudioStatus()) }.getOrElse {
            publish(previous.copy(available = false))
            return@synchronized
        }
        var current = reported
        if (current.active && requestedMute == true && !current.muteRequested) {
            current = current.copy(muteRequested = true, outputMute = CallAudioStatus.UNKNOWN,
                muteDetail = muteCommandFailure.ifEmpty { "Mute request could not be confirmed." })
        }
        // A replacement helper cannot restore a stream mute owned by a dead process. Keep that
        // uncertainty until Android reports it unmuted; never unmute a new user's change ourselves.
        if (previous.restorationPending && !current.active && !current.ownsOutputChange &&
            current.restoreDetail.isEmpty() && current.voiceCallStreamMuted != false) {
            current = current.copy(restorationPending = true, restoreDetail = previous.restoreDetail,
                backend = current.backend.ifEmpty { previous.backend })
        }
        if (current.directMuteIncompatible && !previous.directMuteIncompatible) {
            runCatching { onDirectMuteIncompatible() }
        }
        publish(current)
    }

    fun helperDisconnected() = synchronized(lock) {
        val previous = _status.value
        val uncertain = previous.restorationPending ||
            (previous.backend == "direct" && previous.ownsOutputChange) ||
            (previous.active && previous.muteRequested && !previous.available)
        publish(previous.copy(active = false, available = false, outputMute = CallAudioStatus.UNKNOWN,
            ownsOutputChange = false, restorationPending = uncertain,
            restoreDetail = if (uncertain) "Helper disconnected; local playback restoration could not be confirmed."
                else previous.restoreDetail))
    }

    fun retryRestoration() = synchronized(lock) {
        refreshStatus()
        if (_status.value.active || !_status.value.ownsOutputChange || !_status.value.available) return@synchronized
        runCatching { shizuku.service?.setLocalOutputMuted(false) }
        refreshStatus()
    }

    /** Confirm graceful restoration before the runtime drops the helper connection. */
    fun closeForShutdown() = synchronized(lock) {
        ++generation
        runCatching { shizuku.service?.closeCallAudio() }
        markClosed()
        refreshStatus()
    }

    private fun markClosed() {
        requestedMute = null
        val previous = _status.value
        val uncertain = previous.restorationPending || previous.ownsOutputChange ||
            (previous.active && previous.muteRequested && !previous.available)
        publish(previous.copy(active = false, available = false, ownsOutputChange = false,
            restorationPending = uncertain,
            restoreDetail = if (uncertain) "Local playback restoration could not be confirmed." else previous.restoreDetail))
    }

    override fun open(onLost: (String) -> Unit): CallAudioHandle = synchronized(lock) {
        val svc = shizuku.service ?: error("privileged service not connected (${shizuku.state})")
        val pcm = format()
        val selectedMethod = method()
        val suppressLocal = muteEarpiece()
        val ticket = ++generation
        // Best effort: without it the helper still recovers in-call, after a short capture gap.
        runCatching { svc.setDirectOutputMuteBlocked(suppressLocal && directMuteBlocked()) }
        val pfd = try { svc.openCallAudio(selectedMethod, pcm.sampleRate) } catch (e: Throwable) {
            refreshStatus()
            throw e
        }
        requestedMute = suppressLocal
        muteCommandFailure = ""
        // Keep the new tap pollable even when its first status transaction fails.
        publish(_status.value.copy(active = true, available = false, backend = "",
            muteRequested = suppressLocal, outputMute = CallAudioStatus.UNKNOWN, muteDetail = ""))
        var constructed: PcmSocketPort? = null
        val port = try {
            createPort(pfd, pcm) { reason ->
                // The reader thread must not make Binder calls. Runtime polling/handle close will
                // collect the helper's final restoration report.
                try { onLost(reason) } finally { notifyStatusChanged() }
            }.also { constructed = it; it.start() }
        } catch (e: Throwable) {
            runCatching { constructed?.close() }.exceptionOrNull()?.let(e::addSuppressed)
            runCatching { pfd.close() }
            runCatching { svc.closeCallAudio() }.exceptionOrNull()?.let(e::addSuppressed)
            markClosed()
            refreshStatus()
            throw e
        }
        // A diagnostic Binder transaction must never turn an open tap into an unowned resource.
        runCatching { AppLog.i("call audio tap opened: ${runCatching { svc.audioStats() }.getOrDefault("stats unavailable")}") }
        if (suppressLocal) {
            // Best effort: status/command failures must not discard working call audio.
            val result = runCatching { svc.setLocalOutputMuted(true) }.getOrElse {
                "failed: ${it.message}".also { detail -> muteCommandFailure = detail }
            }
            runCatching { AppLog.i("earpiece mute: $result") }
        }
        refreshStatus()
        object : CallAudioHandle {
            private val closed = AtomicBoolean()
            override val port = port
            override fun close() {
                if (!closed.compareAndSet(false, true)) return
                // Telecom calls close on main. Never acquire the IPC lock here: an open/status
                // transaction may already hold it while blocked in the helper or audio HAL.
                try { port.close() } finally {
                    cleanupExecutor.execute {
                        synchronized(lock) {
                            // A newer open (or shutdown) may overtake this queued close. It owns
                            // helper cleanup now; closing this socket must not close its tap.
                            if (ticket != generation) return@synchronized
                            runCatching { svc.closeCallAudio() }
                            markClosed()
                            refreshStatus()
                            runCatching {
                                AppLog.i("call audio tap closed: in=${port.framesIn} out=${port.framesOut} " +
                                    runCatching { svc.audioStats() }.getOrDefault(""))
                            }
                        }
                    }
                }
            }
        }
    }

    companion object {
        // Serialize remote closes without keeping a thread alive while the gateway is idle.
        private val audioCleanupExecutor = ThreadPoolExecutor(0, 1, 1, TimeUnit.SECONDS,
            LinkedBlockingQueue()) { task -> Thread(task, "audio-cleanup").apply { isDaemon = true } }
    }
}
