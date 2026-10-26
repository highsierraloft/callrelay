// CallRelay changes, 2026-10-26: selected-SIM gateway and community packaging. See UPSTREAM.md.
package io.github.sparkcyf.voicesipagent.telephony

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.telecom.PhoneAccount
import android.telecom.PhoneAccountHandle
import android.telecom.TelecomManager
import android.telephony.SubscriptionManager
import android.telephony.TelephonyManager

/** A saved subscription in a SIM slot. A missing or changed subscription never falls back. */
data class SimPolicy(
    val slotIndex: Int = -1,
    val subscriptionId: Int = SubscriptionManager.INVALID_SUBSCRIPTION_ID,
    val countryIso: String = "",
    val allowIncoming: Boolean = false,
)

class SimAccountSelector(context: Context) {
    private val app = context.applicationContext
    private val subscriptions = app.getSystemService(SubscriptionManager::class.java)
    private val telecom = app.getSystemService(TelecomManager::class.java)
    private val telephony = app.getSystemService(TelephonyManager::class.java)

    private fun checkPermissions() {
        check(listOf(Manifest.permission.READ_PHONE_STATE, Manifest.permission.READ_PHONE_NUMBERS).all {
            app.checkSelfPermission(it) == PackageManager.PERMISSION_GRANTED
        }) { "Phone state and phone account permissions are required" }
    }

    @android.annotation.SuppressLint("MissingPermission")
    fun statusLine(): String {
        checkPermissions()
        return (0..1).joinToString(" ") { slot ->
            val subscription = subscriptions.getActiveSubscriptionInfoForSimSlotIndex(slot)
            val country = subscription?.countryIso?.lowercase()?.takeIf { it.matches(Regex("[a-z]{2}")) } ?: "unknown"
            "slot${slot + 1}_active=${subscription != null} slot${slot + 1}_country=$country"
        }
    }

    @android.annotation.SuppressLint("MissingPermission")
    fun pin(slotIndex: Int, countryIso: String): SimPolicy {
        checkPermissions()
        require(slotIndex in 0..1 && countryIso.matches(Regex("[a-z]{2}"))) { "Invalid SIM selection" }
        val subscription = checkNotNull(subscriptions.getActiveSubscriptionInfoForSimSlotIndex(slotIndex)) {
            "Selected SIM is not active"
        }
        check(subscription.countryIso.equals(countryIso, ignoreCase = true)) { "Selected SIM country does not match" }
        val policy = SimPolicy(slotIndex, subscription.subscriptionId, countryIso)
        resolve(policy)
        return policy
    }

    @android.annotation.SuppressLint("MissingPermission")
    fun resolve(policy: SimPolicy): PhoneAccountHandle {
        checkPermissions()
        check(policy.slotIndex in 0..1 && policy.subscriptionId >= 0 &&
            policy.countryIso.matches(Regex("[a-z]{2}"))) { "Select a SIM before enabling the gateway" }
        val subscription = checkNotNull(subscriptions.getActiveSubscriptionInfoForSimSlotIndex(policy.slotIndex)) {
            "Selected SIM is not active"
        }
        check(subscription.subscriptionId == policy.subscriptionId &&
            subscription.countryIso.equals(policy.countryIso, ignoreCase = true)) { "Selected SIM changed" }
        val matches = telecom.callCapablePhoneAccounts.orEmpty().filter { handle ->
            telecom.getPhoneAccount(handle)?.hasCapabilities(PhoneAccount.CAPABILITY_SIM_SUBSCRIPTION) == true &&
                telephony.createForPhoneAccountHandle(handle)?.subscriptionId == policy.subscriptionId
        }
        check(matches.size == 1) { "Selected SIM has no unique calling account" }
        return matches.single()
    }
}
