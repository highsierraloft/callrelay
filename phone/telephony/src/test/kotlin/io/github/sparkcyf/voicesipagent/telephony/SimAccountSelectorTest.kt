// CallRelay changes, 2026-10-26: selected-SIM gateway and community packaging. See UPSTREAM.md.
package io.github.sparkcyf.voicesipagent.telephony

import android.content.Context
import android.content.pm.PackageManager
import android.telecom.PhoneAccount
import android.telecom.PhoneAccountHandle
import android.telecom.TelecomManager
import android.telephony.SubscriptionInfo
import android.telephony.SubscriptionManager
import android.telephony.TelephonyManager
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.mockito.ArgumentMatchers.*
import org.mockito.Mockito.*

class SimAccountSelectorTest {
    private val context = mock(Context::class.java)
    private val subscriptions = mock(SubscriptionManager::class.java)
    private val telecom = mock(TelecomManager::class.java)
    private val telephony = mock(TelephonyManager::class.java)
    private val selected = mock(SubscriptionInfo::class.java)
    private val protectedAccount = mock(PhoneAccountHandle::class.java)
    private val selectedAccount = mock(PhoneAccountHandle::class.java)
    private lateinit var selector: SimAccountSelector

    @Before fun setup() {
        doReturn(context).`when`(context).applicationContext
        doReturn(PackageManager.PERMISSION_GRANTED).`when`(context).checkSelfPermission(anyString())
        doReturn(subscriptions).`when`(context).getSystemService(SubscriptionManager::class.java)
        doReturn(telecom).`when`(context).getSystemService(TelecomManager::class.java)
        doReturn(telephony).`when`(context).getSystemService(TelephonyManager::class.java)
        doReturn(selected).`when`(subscriptions).getActiveSubscriptionInfoForSimSlotIndex(1)
        doReturn(42).`when`(selected).subscriptionId
        doReturn("es").`when`(selected).countryIso
        doReturn(listOf(protectedAccount, selectedAccount)).`when`(telecom).callCapablePhoneAccounts
        for ((handle, id) in listOf(protectedAccount to 7, selectedAccount to 42)) {
            val account = mock(PhoneAccount::class.java)
            doReturn(true).`when`(account).hasCapabilities(PhoneAccount.CAPABILITY_SIM_SUBSCRIPTION)
            doReturn(account).`when`(telecom).getPhoneAccount(handle)
            val scoped = mock(TelephonyManager::class.java)
            doReturn(id).`when`(scoped).subscriptionId
            doReturn(scoped).`when`(telephony).createForPhoneAccountHandle(handle)
        }
        selector = SimAccountSelector(context)
    }

    @Test fun pinsOnlyTheSelectedSlotAndResolvesItsAccount() {
        val policy = selector.pin(1, "es")
        assertEquals(SimPolicy(1, 42, "es"), policy)
        assertSame(selectedAccount, selector.resolve(policy))
        verify(subscriptions, never()).getActiveSubscriptionInfoForSimSlotIndex(0)
        verify(telecom, never()).getDefaultOutgoingPhoneAccount(anyString())
        assertFalse(policy.allowIncoming)
    }

    @Test fun missingSelectedSimCannotFallBackToTheOtherAccount() {
        doReturn(null).`when`(subscriptions).getActiveSubscriptionInfoForSimSlotIndex(1)
        assertThrows(IllegalStateException::class.java) { selector.resolve(SimPolicy(1, 42, "es")) }
        verify(telecom, never()).getDefaultOutgoingPhoneAccount(anyString())
    }

    @Test fun replacementSubscriptionRequiresExplicitReselection() {
        doReturn(43).`when`(selected).subscriptionId
        assertThrows(IllegalStateException::class.java) { selector.resolve(SimPolicy(1, 42, "es")) }
    }

    @Test fun wrongCountryAndMissingPolicyAreRefused() {
        doReturn("ua").`when`(selected).countryIso
        assertThrows(IllegalStateException::class.java) { selector.pin(1, "es") }
        assertThrows(IllegalStateException::class.java) { selector.resolve(SimPolicy()) }
    }

    @Test fun absentOrAmbiguousAccountIsRefused() {
        doReturn(listOf(protectedAccount)).`when`(telecom).callCapablePhoneAccounts
        assertThrows(IllegalStateException::class.java) { selector.resolve(SimPolicy(1, 42, "es")) }
        doReturn(listOf(selectedAccount, selectedAccount)).`when`(telecom).callCapablePhoneAccounts
        assertThrows(IllegalStateException::class.java) { selector.resolve(SimPolicy(1, 42, "es")) }
    }

    @Test fun permissionLossNeverFallsBack() {
        doReturn(PackageManager.PERMISSION_DENIED).`when`(context).checkSelfPermission(anyString())
        assertThrows(IllegalStateException::class.java) { selector.resolve(SimPolicy(1, 42, "es")) }
        verify(subscriptions, never()).getActiveSubscriptionInfoForSimSlotIndex(anyInt())
    }
}
