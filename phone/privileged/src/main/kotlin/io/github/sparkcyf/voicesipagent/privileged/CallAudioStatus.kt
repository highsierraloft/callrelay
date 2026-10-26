package io.github.sparkcyf.voicesipagent.privileged

import android.os.Bundle

/** Android-reported state; neither mute flags nor an open tap prove physical audio routing. */
data class CallAudioStatus(
    val active: Boolean = false,
    val backend: String = "",
    val muteRequested: Boolean = false,
    val outputMute: String = NOT_REQUESTED,
    val muteDetail: String = "",
    val ownsOutputChange: Boolean = false,
    val restorationPending: Boolean = false,
    val restoreDetail: String = "",
    val voiceCallStreamMuted: Boolean? = null,
    /** Direct capture failed right after voice-call stream mute; the app may remember this per device. */
    val directMuteIncompatible: Boolean = false,
    /** In-call recoveries (record re-creates, backend fallbacks); empty when none happened. */
    val recoveryDetail: String = "",
    /** App-local query availability, deliberately excluded from the Binder payload. */
    val available: Boolean = true,
) {
    val backendLabel: String get() = when (backend) {
        "direct" -> "Direct telephony"
        "redirection-helper", "redirection-manual" -> "Cellular call redirection"
        else -> "Unavailable"
    }

    fun toBundle() = Bundle().apply {
        putBoolean("active", active)
        putString("backend", backend)
        putBoolean("mute_requested", muteRequested)
        putString("output_mute", outputMute)
        putString("mute_detail", muteDetail)
        putBoolean("owns_output_change", ownsOutputChange)
        putBoolean("restoration_pending", restorationPending)
        putString("restore_detail", restoreDetail)
        voiceCallStreamMuted?.let { putBoolean("voice_call_stream_muted", it) }
        putBoolean("direct_mute_incompatible", directMuteIncompatible)
        putString("recovery_detail", recoveryDetail)
    }

    companion object {
        const val NOT_REQUESTED = "not_requested"
        const val APPLIED = "applied"
        const val PRESERVED = "preserved"
        const val NOT_APPLIED = "not_applied"
        /** Refused for this call: the method broke call audio capture on this device. */
        const val UNSUPPORTED = "unsupported"
        const val UNKNOWN = "unknown"

        fun fromBundle(bundle: Bundle) = CallAudioStatus(
            active = bundle.getBoolean("active"),
            backend = bundle.getString("backend").orEmpty(),
            muteRequested = bundle.getBoolean("mute_requested"),
            outputMute = bundle.getString("output_mute") ?: NOT_REQUESTED,
            muteDetail = bundle.getString("mute_detail").orEmpty(),
            ownsOutputChange = bundle.getBoolean("owns_output_change"),
            restorationPending = bundle.getBoolean("restoration_pending"),
            restoreDetail = bundle.getString("restore_detail").orEmpty(),
            voiceCallStreamMuted = if (bundle.containsKey("voice_call_stream_muted"))
                bundle.getBoolean("voice_call_stream_muted") else null,
            directMuteIncompatible = bundle.getBoolean("direct_mute_incompatible"),
            recoveryDetail = bundle.getString("recovery_detail").orEmpty(),
        )
    }
}
