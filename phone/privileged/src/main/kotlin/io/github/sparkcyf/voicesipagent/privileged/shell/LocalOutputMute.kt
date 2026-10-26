package io.github.sparkcyf.voicesipagent.privileged.shell

import android.media.AudioManager
import io.github.sparkcyf.voicesipagent.privileged.CallAudioStatus

/** Owns only our output-mute changes. The caller serializes access and ties restoration to the tap. */
internal class LocalOutputMute(
    private val audio: AudioControl,
    /** Monotonic milliseconds. */
    private val clock: () -> Long = { System.nanoTime() / 1_000_000 },
) {
    interface AudioControl {
        val mode: Int
        val voiceCallMuted: Boolean
        fun requestMode(mode: Int)
        fun requestVoiceCallMute(muted: Boolean)
        /** Return the observed state after a bounded wait; -1 means let the mode settle. */
        fun awaitMode(wanted: Int): Int
        fun awaitVoiceCallMute(wanted: Boolean): Boolean
    }

    private enum class Method { REDIRECT_MODE, VOICE_CALL_STREAM, PRESERVE_STREAM_MUTE }
    private var method: Method? = null
    var requested = false; private set
    var lastMuteResult = ""; private set
    var lastRestoreResult = ""; private set
    var restorationPending = false; private set
    private var requestState = CallAudioStatus.NOT_REQUESTED
    private var streamMutedAt = 0L
    /** Why voice-call stream mute is refused for the rest of this call, if it is. */
    var streamMuteUnsupported: String? = null; private set
    /** Direct capture failed right after our stream mute: likely a device trait, not a call glitch. */
    var directMuteIncompatible = false; private set
    val ownsOutputChange: Boolean
        get() = method == Method.REDIRECT_MODE || method == Method.VOICE_CALL_STREAM

    /** [streamMuteBlocked]: refuse voice-call stream mute for this call (known incompatible device). */
    fun beginCall(streamMuteBlocked: String? = null) {
        check(!ownsOutputChange) { "Local playback restoration is still pending" }
        method = null
        requested = false
        lastMuteResult = ""
        lastRestoreResult = ""
        restorationPending = false
        requestState = CallAudioStatus.NOT_REQUESTED
        streamMuteUnsupported = streamMuteBlocked
        directMuteIncompatible = false
    }

    fun observedMute(): String = try {
        when (method) {
            Method.REDIRECT_MODE -> if (audio.mode == MODE_CALL_REDIRECT) CallAudioStatus.APPLIED else CallAudioStatus.NOT_APPLIED
            Method.VOICE_CALL_STREAM -> if (audio.voiceCallMuted) CallAudioStatus.APPLIED else CallAudioStatus.NOT_APPLIED
            Method.PRESERVE_STREAM_MUTE -> if (audio.voiceCallMuted) CallAudioStatus.PRESERVED else CallAudioStatus.NOT_APPLIED
            null -> requestState
        }
    } catch (_: Exception) {
        CallAudioStatus.UNKNOWN
    }

    fun mute(useRedirectMode: Boolean): String {
        requested = true
        return applyMute(useRedirectMode).also { lastMuteResult = it }
    }

    private fun applyMute(useRedirectMode: Boolean): String = reportFailure("mute") {
        method?.let {
            val stillMuted = when (it) {
                Method.REDIRECT_MODE -> audio.mode == MODE_CALL_REDIRECT
                else -> audio.voiceCallMuted
            }
            return@reportFailure if (stillMuted) "already muted: ${description(it)}"
                else "failed: ${description(it)} no longer muted; restoration pending"
        }
        if (!useRedirectMode) streamMuteUnsupported?.let {
            requestState = CallAudioStatus.UNSUPPORTED
            return@reportFailure "unsupported: $it"
        }
        val before = audio.mode
        if (before != AudioManager.MODE_IN_CALL) {
            requestState = CallAudioStatus.NOT_APPLIED
            return@reportFailure "skipped: mode=$before"
        }
        if (useRedirectMode) {
            // Record ownership before calling: a request may apply even if its acknowledgement fails.
            method = Method.REDIRECT_MODE
            audio.requestMode(MODE_CALL_REDIRECT)
            val after = audio.awaitMode(MODE_CALL_REDIRECT)
            if (after == MODE_CALL_REDIRECT) "muted: mode $before->$after"
            else "failed: redirection mode not applied (mode=$after); restoration pending"
        } else if (audio.voiceCallMuted) {
            method = Method.PRESERVE_STREAM_MUTE
            "already muted: voice-call stream (preserved)"
        } else {
            method = Method.VOICE_CALL_STREAM
            streamMutedAt = clock()
            audio.requestVoiceCallMute(true)
            if (audio.awaitVoiceCallMute(true)) "muted: voice-call stream"
            else "failed: voice-call stream mute not applied; restoration pending"
        }
    }

    fun restore(why: String): String {
        if (method == null) return "not muted"
        return applyRestore(why).also {
            lastRestoreResult = it
            restorationPending = ownsOutputChange
        }
    }

    private fun applyRestore(why: String): String = reportFailure("restore") {
        val owned = method ?: return@reportFailure "not muted"
        when (owned) {
            Method.PRESERVE_STREAM_MUTE -> {
                method = null
                "preserved existing voice-call stream mute ($why)"
            }
            Method.REDIRECT_MODE -> {
                // NORMAL withdraws our mode request, allowing Telecom's request to take over.
                audio.requestMode(AudioManager.MODE_NORMAL)
                val after = audio.awaitMode(-1)
                if (after == MODE_CALL_REDIRECT) {
                    "failed: redirection mode still active ($why); restoration pending"
                } else {
                    method = null
                    "restored redirection mode ($why), mode=$after"
                }
            }
            Method.VOICE_CALL_STREAM -> {
                // Always undo an owned request, even if the mute was not observed yet. Do not change
                // the volume index: ADJUST_UNMUTE restores output at the user's current call volume.
                audio.requestVoiceCallMute(false)
                if (audio.awaitVoiceCallMute(false)) {
                    "failed: voice-call stream still muted ($why); restoration pending"
                } else {
                    method = null
                    "restored voice-call stream ($why)"
                }
            }
        }
    }

    /**
     * Direct capture stopped while our voice-call stream mute was in place. Withdraws that mute and
     * refuses it for the rest of the call. Returns the restore result, or null if we own no stream
     * mute (a preserved user mute is never touched).
     */
    fun withdrawForCaptureFailure(failure: String): String? {
        if (method != Method.VOICE_CALL_STREAM) return null
        if (clock() - streamMutedAt <= MUTE_FAILURE_WINDOW_MS) directMuteIncompatible = true
        val reason = "voice-call stream mute stopped direct capture ($failure)"
        streamMuteUnsupported = reason
        requestState = CallAudioStatus.UNSUPPORTED
        lastMuteResult = "unsupported: $reason"
        return restore("direct capture failed")
    }

    private inline fun reportFailure(action: String, block: () -> String): String = try {
        block()
    } catch (e: Exception) {
        if (action == "mute") requestState = CallAudioStatus.UNKNOWN
        "failed: $action ${e.javaClass.simpleName}: ${e.message}" +
            if (method != null) "; restoration pending" else ""
    }

    private fun description(method: Method): String = when (method) {
        Method.REDIRECT_MODE -> "redirection mode"
        Method.VOICE_CALL_STREAM -> "voice-call stream"
        Method.PRESERVE_STREAM_MUTE -> "voice-call stream (preserved)"
    }

    companion object {
        /** AudioManager.MODE_CALL_REDIRECT (API 33, system API). */
        const val MODE_CALL_REDIRECT = 5
        /** Capture failures this soon after our stream mute are attributed to the mute. */
        const val MUTE_FAILURE_WINDOW_MS = 3_000L
    }
}
