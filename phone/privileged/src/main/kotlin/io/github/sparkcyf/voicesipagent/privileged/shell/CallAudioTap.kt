package io.github.sparkcyf.voicesipagent.privileged.shell

import android.content.Context
import android.annotation.SuppressLint
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.os.ParcelFileDescriptor
import android.os.Build
import android.os.SystemClock
import android.system.ErrnoException
import android.system.Os
import android.system.OsConstants
import android.util.Log
import io.github.sparkcyf.voicesipagent.privileged.AudioMethod
import io.github.sparkcyf.voicesipagent.voice.PcmFormat
import java.io.FileDescriptor

/**
 * Shell-side call audio tap: downlink capture -> socket, socket -> uplink injection.
 * PCM stays in memory; only counters are logged.
 *
 * Capture failures are recovered in place where [TapRecovery] allows it (direct record re-create,
 * AUTO backend fallback), so the app's socket and the call survive them.
 */
internal class CallAudioTap(
    private val ctx: Context,
    private val method: Int,
    sampleRate: Int,
    private val hooks: Hooks,
) {
    /** Called from tap threads without holding the tap monitor. */
    interface Hooks {
        /** Withdraw our direct-mode voice-call stream mute after a capture failure; true if we owned one. */
        fun withdrawMuteForCaptureFailure(failure: String): Boolean
        /** Restore owned output changes before the backend is replaced. */
        fun beforeBackendSwitch()
        /** Re-apply a requested output mute for the new backend. */
        fun afterBackendSwitch(usedMethod: String)
        /** Runs once when the tap shuts down, from whichever thread closed it. */
        fun onClosed()
    }

    private val pcm = PcmFormat(sampleRate)
    private val rate = pcm.sampleRate
    private val frameBytes = pcm.frameBytes
    // Replaced by recoveries: the record only on the capture thread, the track while [switching].
    @Volatile private lateinit var record: AudioRecord
    @Volatile private lateinit var track: AudioTrack
    private lateinit var local: FileDescriptor
    private lateinit var recovery: TapRecovery
    @Volatile private var running = false
    @Volatile private var switching = false
    val isOpen: Boolean get() = running
    private var closed = false
    private var backendFrames = 0L
    private var backendPeak = 0
    private val recoveries = ArrayList<String>()
    val recoveryDetail: String get() = synchronized(recoveries) { recoveries.joinToString("; ") }
    private var downThread: Thread? = null
    private var upThread: Thread? = null
    private var modeThread: Thread? = null
    private val audioManager by lazy { ShellEnv.audioManager(ctx) }
    private var backendFailures = ""

    @Volatile var usedMethod = "none"; private set
    @Volatile var downFrames = 0L; private set
    @Volatile var upFrames = 0L; private set
    /** Max sample magnitude seen over the whole tap. */
    @Volatile var downPeak = 0; private set
    @Volatile var upPeak = 0; private set
    /** Frames whose peak exceeds [VOICE_THRESHOLD], i.e. not silence. */
    @Volatile var downVoiced = 0L; private set
    @Volatile var upVoiced = 0L; private set
    private var windowDown = 0
    private var windowUp = 0
    /** Frame timing: downlink frame completions, uplink packet arrivals, uplink write blocking. */
    private val downGap = GapStats()
    private val upGap = GapStats()
    private val upWrite = GapStats()
    @Volatile var error: String? = null; private set

    /** Opens the audio paths and returns the app's end of the socket pair. */
    fun open(): ParcelFileDescriptor {
        val app = FileDescriptor()
        var result: ParcelFileDescriptor? = null
        try {
            openAudio()
            local = FileDescriptor()
            Os.socketpair(OsConstants.AF_UNIX, OsConstants.SOCK_SEQPACKET, 0, local, app)
            val pfd = ParcelFileDescriptor.dup(app)
            result = pfd
            Os.close(app)
            running = true
            startRecord()
            startTrack()
            downThread = Thread(::downLoop, "tap-down").apply { priority = Thread.MAX_PRIORITY; start() }
            upThread = Thread(::upLoop, "tap-up").apply { priority = Thread.MAX_PRIORITY; start() }
            modeThread = Thread({
                while (running) {
                    SystemClock.sleep(200)
                    if (running && !isCellularMode(audioManager.mode)) {
                        error = "cellular audio mode lost: ${audioManager.mode}"
                        close()
                    }
                }
            }, "tap-mode").apply { isDaemon = true; start() }
            Log.i(TAG, "tap open method=$usedMethod rate=$rate frameBytes=$frameBytes")
            return pfd
        } catch (t: Throwable) {
            error = "open at $rate Hz: ${describe(t)}"
            runCatching { result?.close() }
            runCatching { Os.close(app) }
            close()
            throw t
        }
    }

    private fun openAudio() {
        require(method in AudioMethod.AUTO..AudioMethod.LEGACY) { "unknown audio method $method" }
        recovery = TapRecovery(method)
        check(isCellularMode(audioManager.mode)) { "not in a cellular call: audio mode=${audioManager.mode}" }
        val failures = ArrayList<String>()
        for (backend in recovery.plan) {
            try {
                openBackend(backend)
                recovery.opened(backend)
                if (failures.isNotEmpty()) backendFailures = failures.joinToString("; ")
                return
            } catch (t: Throwable) {
                failures += "${label(backend)}: ${describe(t)}"
                releaseAudio()
            }
        }
        throw IllegalStateException(failures.joinToString("; "))
    }

    private fun openBackend(backend: TapBackend) {
        val capabilities = AudioCapabilities.inspect(ctx)
        when (backend) {
            TapBackend.REDIRECTION -> {
                // Use the framework helpers when process attribution matches the helper identity;
                // otherwise construct equivalent objects with the explicit attributed context.
                check(capabilities.redirect) { capabilities.detail }
                openRedirected()
            }
            TapBackend.DIRECT -> {
                check(capabilities.direct) { capabilities.detail }
                openLegacy()
                usedMethod = "direct"
            }
        }
    }

    private fun label(backend: TapBackend) = when (backend) {
        TapBackend.REDIRECTION -> "official"
        TapBackend.DIRECT -> "legacy"
    }

    private fun startRecord() {
        record.startRecording()
        check(record.recordingState == AudioRecord.RECORDSTATE_RECORDING) { "record did not start at $rate Hz" }
    }

    private fun startTrack() {
        // Two frames of silence absorb scheduling jitter between the app and the track.
        check(track.write(ByteArray(frameBytes * 2), 0, frameBytes * 2, AudioTrack.WRITE_NON_BLOCKING) >= 0) {
            "track prefill failed at $rate Hz"
        }
        track.play()
        check(track.playState == AudioTrack.PLAYSTATE_PLAYING) { "track did not start at $rate Hz" }
    }

    // AudioCapabilities checks the helper UID's grants before either backend opens. Lint only
    // sees the APK permissions, which are deliberately not used for privileged audio.
    @SuppressLint("MissingPermission")
    private fun openRedirected() {
        check(ShellEnv.isPstnCallAudioInterceptable() != false) { "PSTN audio interception unavailable" }
        // Helpers are usable only when their process-default attribution is valid. If Shizuku's
        // bootstrap uses the gateway package, use equivalent construction with the helper context.
        val helperFailure = runCatching {
            check(ShellEnv.hasMatchingDefaultAudioAttribution(ctx)) {
                "process-default audio attribution does not match helper; using explicit context"
            }
            record = AudioManager::class.java.getMethod("getCallDownlinkExtractionAudioRecord", AudioFormat::class.java)
                .invoke(audioManager, format(AudioFormat.CHANNEL_IN_MONO)) as AudioRecord
            track = AudioManager::class.java.getMethod("getCallUplinkInjectionAudioTrack", AudioFormat::class.java)
                .invoke(audioManager, format(AudioFormat.CHANNEL_OUT_MONO)) as AudioTrack
            check(record.state == AudioRecord.STATE_INITIALIZED && track.state == AudioTrack.STATE_INITIALIZED)
        }.exceptionOrNull()
        if (helperFailure == null) {
            usedMethod = "redirection-helper"
            return
        }
        releaseAudio()
        backendFailures = "helper: ${describe(helperFailure)}"
        val pstn = runCatching { AudioManager::class.java.getField("CALL_REDIRECT_PSTN").getInt(null) }.getOrDefault(1)
        val int = Int::class.javaPrimitiveType
        // Attributes must match the AudioManager helpers exactly: without them the record falls back to the
        // mic and the track to media playback on the local speaker, and nothing reaches the call.
        val recAttr = AudioAttributes.Builder().also {
            AudioAttributes.Builder::class.java.getMethod("setInternalCapturePreset", int)
                .invoke(it, MediaRecorder.AudioSource.VOICE_DOWNLINK)
        }.build()
        val rb = AudioRecord.Builder().setAudioFormat(format(AudioFormat.CHANNEL_IN_MONO))
        setContext(rb)
        AudioRecord.Builder::class.java.getMethod("setAudioAttributes", AudioAttributes::class.java).invoke(rb, recAttr)
        AudioRecord.Builder::class.java.getMethod("setCallRedirectionMode", int).invoke(rb, pstn)
        record = rb.build()
        try {
            check(record.state == AudioRecord.STATE_INITIALIZED) { "PSTN record not initialized at $rate Hz" }
            val trackAttr = AudioAttributes.Builder().setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).also {
                AudioAttributes.Builder::class.java.getMethod("setSystemUsage", int).invoke(it, USAGE_CALL_ASSISTANT)
            }.build()
            val tb = AudioTrack.Builder().setAudioAttributes(trackAttr)
                .setAudioFormat(format(AudioFormat.CHANNEL_OUT_MONO))
            setContext(tb)
            AudioTrack.Builder::class.java.getMethod("setCallRedirectionMode", int).invoke(tb, pstn)
            track = tb.build()
            check(track.state == AudioTrack.STATE_INITIALIZED) { "PSTN track not initialized at $rate Hz" }
            usedMethod = "redirection-manual"
            Log.i(TAG, "redirected: record src=${record.audioSource} dev=${record.routedDevice?.type} " +
                "track usage=${track.audioAttributes.usage}")
        } catch (t: Throwable) {
            record.release()
            if (::track.isInitialized) track.release()
            throw t
        }
    }

    /** AirSIM-style fallback for devices without the official interception API. */
    @SuppressLint("MissingPermission")
    private fun openLegacy() {
        record = createLegacyRecord()
        try {
            val minTrack = AudioTrack.getMinBufferSize(rate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
            check(minTrack > 0) { "track buffer size $minTrack at $rate Hz" }
            track = AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build(),
                )
                .setAudioFormat(format(AudioFormat.CHANNEL_OUT_MONO))
                .setBufferSizeInBytes(maxOf(minTrack, frameBytes * 10))
                .setTransferMode(AudioTrack.MODE_STREAM).also(::setContext).build()
            check(track.state == AudioTrack.STATE_INITIALIZED) { "telephony track not initialized at $rate Hz" }
            val am = ShellEnv.audioManager(ctx)
            val tx = am.getDevices(AudioManager.GET_DEVICES_OUTPUTS).firstOrNull { it.type == AudioDeviceInfo.TYPE_TELEPHONY }
            if (tx == null || !track.setPreferredDevice(tx)) {
                error(if (tx == null) "TYPE_TELEPHONY output unavailable" else "telephony preferred-device request rejected")
            }
        } catch (t: Throwable) {
            record.release()
            if (::track.isInitialized) track.release()
            throw t
        }
    }

    @SuppressLint("MissingPermission")
    private fun createLegacyRecord(): AudioRecord {
        val minRec = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        check(minRec > 0) { "record buffer size $minRec at $rate Hz" }
        val created = AudioRecord.Builder().setAudioSource(MediaRecorder.AudioSource.VOICE_DOWNLINK)
            .setAudioFormat(format(AudioFormat.CHANNEL_IN_MONO)).setBufferSizeInBytes(maxOf(minRec, frameBytes * 10))
            .also(::setContext).build()
        if (created.state != AudioRecord.STATE_INITIALIZED) {
            created.release()
            error("VOICE_DOWNLINK record not initialized at $rate Hz")
        }
        return created
    }

    private fun setContext(builder: Any) {
        AudioBuilderContext.apply(builder, ctx, Context::class.java) {
            ShellEnv.prepareAudioAttribution(ctx)
        }
    }

    private fun releaseAudio() {
        if (::record.isInitialized) runCatching { record.release() }
        if (::track.isInitialized) runCatching { track.release() }
    }

    private fun isCellularMode(mode: Int) = mode == AudioManager.MODE_IN_CALL ||
        (Build.VERSION.SDK_INT >= 33 && mode == 5)

    private fun downLoop() {
        val buf = ByteArray(frameBytes)
        try {
            var offset = 0
            while (running) {
                val n = record.read(buf, offset, frameBytes - offset, AudioRecord.READ_BLOCKING)
                if (n <= 0) {
                    if (!running) break
                    offset = 0
                    if (recoverCapture("record read $n at $rate Hz")) continue
                    break
                }
                offset += n
                if (offset < frameBytes) continue
                offset = 0
                downGap.mark(SystemClock.elapsedRealtimeNanos())
                val p = peak(buf)
                if (p > downPeak) downPeak = p
                if (p > VOICE_THRESHOLD) downVoiced++
                if (p > windowDown) windowDown = p
                check(Os.write(local, buf, 0, frameBytes) == frameBytes) { "short downlink packet write" }
                if (++downFrames % 250 == 0L) {
                    // Every 5 s: levels only, never audio content.
                    Log.i(TAG, "levels t=${downFrames / 50}s downPeak5s=$windowDown upPeak5s=$windowUp up=$upFrames" +
                        " downGap5s=${downGap.takeWindowMax()}ms upGap5s=${upGap.takeWindowMax()}ms" +
                        " upWrite5s=${upWrite.takeWindowMax()}ms")
                    windowDown = 0
                    windowUp = 0
                }
                backendFrames++
                if (p > backendPeak) backendPeak = p
                val action = recovery.onDownlinkFrames(backendFrames, backendPeak)
                if (action != null && !perform(action, "downlink digital zero for ${backendFrames / 50}s")) break
            }
        } catch (e: ErrnoException) {
            if (running && e.errno != OsConstants.EPIPE && e.errno != OsConstants.ECONNRESET) error = "down: ${e.message}"
        } catch (t: Throwable) {
            if (running) error = "down: $t"
        } finally {
            close()
        }
    }

    private fun upLoop() {
        val buf = ByteArray(frameBytes + 1)
        try {
            while (running) {
                val n = Os.read(local, buf, 0, buf.size)
                if (n <= 0) break // peer closed
                check(n == frameBytes) { "uplink packet size $n, expected $frameBytes" }
                val arrived = SystemClock.elapsedRealtimeNanos()
                upGap.mark(arrived)
                val p = peak(buf, n)
                if (p > upPeak) upPeak = p
                if (p > VOICE_THRESHOLD) upVoiced++
                if (p > windowUp) windowUp = p
                var off = 0
                while (off < n && running) {
                    val t = track
                    val w = t.write(buf, off, n - off, AudioTrack.WRITE_BLOCKING)
                    if (w <= 0) {
                        // The capture thread is replacing the backend: drop the rest of this frame.
                        if (switching || t !== track) break
                        error = "track write $w at $rate Hz"; running = false; break
                    }
                    off += w
                }
                upWrite.record((SystemClock.elapsedRealtimeNanos() - arrived) / 1_000_000)
                upFrames++
            }
        } catch (e: ErrnoException) {
            if (running && e.errno != OsConstants.ECONNRESET) error = "up: ${e.message}"
        } catch (t: Throwable) {
            if (running) error = "up: $t"
        } finally {
            close()
        }
    }

    /** Runs on the capture thread. Returns true when capture continues. */
    private fun recoverCapture(failure: String): Boolean {
        val withdrawn = recovery.backend == TapBackend.DIRECT &&
            runCatching { hooks.withdrawMuteForCaptureFailure(failure) }.getOrDefault(false)
        val why = if (withdrawn) "$failure with voice-call stream muted" else failure
        return perform(recovery.onCaptureFailure(withdrawn), why)
    }

    private fun perform(action: TapRecovery.Action, why: String): Boolean = when (action) {
        TapRecovery.Action.Close -> { error = why; false }
        TapRecovery.Action.RecreateRecord -> recreateRecord(why)
        is TapRecovery.Action.Switch -> switchBackend(action.backend, why)
    }

    private fun recreateRecord(why: String): Boolean {
        SystemClock.sleep(RECOVERY_SETTLE_MS)
        val ok = synchronized(this) {
            if (closed) return false
            try {
                runCatching { record.stop() }
                runCatching { record.release() }
                record = createLegacyRecord()
                startRecord()
                true
            } catch (t: Throwable) {
                error = "$why; record re-create failed: ${describe(t)}"
                false
            }
        }
        if (ok) note("re-created direct record after $why")
        return ok
    }

    private fun switchBackend(target: TapBackend, why: String): Boolean {
        runCatching { hooks.beforeBackendSwitch() }.onFailure { Log.w(TAG, "output restore before switch failed: $it") }
        val from = usedMethod
        val ok = synchronized(this) {
            if (closed) return false
            switching = true
            try {
                runCatching { record.stop() }
                runCatching { track.pause(); track.flush(); track.stop() }
                releaseAudio()
                check(isCellularMode(audioManager.mode)) { "not in a cellular call: audio mode=${audioManager.mode}" }
                openBackend(target)
                recovery.opened(target)
                startRecord()
                startTrack()
                backendFrames = 0
                backendPeak = 0
                true
            } catch (t: Throwable) {
                error = "$why; switch to ${label(target)} failed: ${describe(t)}"
                false
            } finally {
                switching = false
            }
        }
        if (!ok) return false
        note("switched $from -> $usedMethod after $why")
        runCatching { hooks.afterBackendSwitch(usedMethod) }.onFailure { Log.w(TAG, "output mute after switch failed: $it") }
        return true
    }

    private fun note(event: String) {
        synchronized(recoveries) { recoveries += event }
        Log.w(TAG, "recovery: $event")
    }

    @Synchronized
    fun close() {
        if (closed) return
        closed = true
        running = false
        try { Os.shutdown(local, OsConstants.SHUT_RDWR) } catch (_: Throwable) {}
        try { record.stop() } catch (_: Throwable) {}
        try { track.pause(); track.flush(); track.stop() } catch (_: Throwable) {}
        try { record.release() } catch (_: Throwable) {}
        try { track.release() } catch (_: Throwable) {}
        try { Os.close(local) } catch (_: Throwable) {}
        downThread = null
        upThread = null
        modeThread = null
        Log.i(TAG, "tap closed ${stats()}")
        runCatching(hooks::onClosed).onFailure { Log.w(TAG, "onClosed failed: $it") }
    }

    fun stats() = "method=$usedMethod rate=$rate frameBytes=$frameBytes down=$downFrames up=$upFrames downPeak=$downPeak upPeak=$upPeak " +
        "downVoiced=$downVoiced upVoiced=$upVoiced" +
        " backendFailures=$backendFailures preferred=${if (::track.isInitialized) runCatching { track.preferredDevice?.type }.getOrNull() else null}" +
        " routed=${if (::track.isInitialized) runCatching { track.routedDevice?.type }.getOrNull() else null}" +
        " downGap[$downGap] upGap[$upGap] upWrite[$upWrite]" +
        recoveryDetail.let { if (it.isEmpty()) "" else " recoveries=[$it]" } +
        (error?.let { " error=$it" } ?: "")

    private fun peak(b: ByteArray, size: Int = b.size): Int {
        var p = 0
        var i = 0
        while (i + 1 < size) {
            val s = kotlin.math.abs(((b[i + 1].toInt() shl 8) or (b[i].toInt() and 0xff)).toShort().toInt())
            if (s > p) p = s
            i += 2
        }
        return p
    }

    private fun format(mask: Int) = AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT)
        .setSampleRate(rate).setChannelMask(mask).build()

    private fun describe(t: Throwable): String {
        var c = t
        while (c is java.lang.reflect.InvocationTargetException && c.cause != null) c = c.cause!!
        return "${c.javaClass.simpleName}: ${c.message}"
    }

    companion object {
        private const val TAG = "VsaTap"
        private const val VOICE_THRESHOLD = 300
        /** Lets AudioService apply an unmute before the record is rebuilt. */
        private const val RECOVERY_SETTLE_MS = 200L
        /** Hidden AudioAttributes.USAGE_CALL_ASSISTANT, as used by getCallUplinkInjectionAudioTrack. */
        private const val USAGE_CALL_ASSISTANT = 17
    }
}
