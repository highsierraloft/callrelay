import unittest
from unittest.mock import patch

from controller import APP_NAMESPACE, PACKAGE, PERMISSION, inspect_and_recover


class FakeAdb:
    serial = "test-phone"
    gateway_status = "result=1 ready=false takeover=off audioReady=false loopback=off sim_slot=1 sim_pinned=true incoming=off"

    def __init__(
        self, states="  mCallState=0\n", model="ExamplePhone", unlocked=True, shizuku=True
    ):
        self.states = states
        self.model = model
        self.unlocked = unlocked
        self.shizuku = shizuku
        self.actions = []

    def command(self, *args, **_):
        return 0, "device"

    def shell(self, *args, **_):
        self.actions.append(args)
        if args == ("getprop", "ro.product.model"):
            return 0, self.model
        if args == ("getprop", "sys.boot_completed"):
            return 0, "1"
        if args == ("am", "get-started-user-state", "0"):
            return 0, "RUNNING_UNLOCKED" if self.unlocked else "RUNNING_LOCKED"
        if args == ("dumpsys", "telephony.registry"):
            return 0, self.states
        if args[:2] == ("pm", "path"):
            return 0, "package:/data/app/test/base.apk"
        if args == ("dumpsys", "package", PACKAGE):
            return 0, PERMISSION + ": granted=true"
        if args == ("pidof", "shizuku_server"):
            return (0, "42") if self.shizuku else (1, "")
        if args[0].endswith("libshizuku.so"):
            self.shizuku = True
            return 0, "started"
        if args[:2] == ("am", "broadcast"):
            return 0, self.gateway_status
        if args == ("getprop", "gsm.sim.state"):
            return 0, "LOADED,ABSENT"
        return 0, ""


class RecoveryTests(unittest.TestCase):
    def test_second_sim_is_explicit_and_the_first_sim_cannot_make_it_ready(self):
        adb = FakeAdb(model="ExamplePhone")
        adb.gateway_status = "result=1 ready=true takeover=on audioReady=true loopback=off sim_slot=2 sim_pinned=true incoming=off"
        result = inspect_and_recover(adb, "ExamplePhone", 2)
        self.assertEqual(result["selected_sim_slot"], 2)
        self.assertFalse(result["sim_ready"])
        self.assertFalse(result["call_ready"])
        wrong = inspect_and_recover(adb, "ExamplePhone", 1)
        self.assertEqual(wrong["phase"], "gateway_sim_policy_mismatch")

    def test_invalid_slot_is_rejected_without_phone_access(self):
        adb = FakeAdb()
        with self.assertRaises(ValueError):
            inspect_and_recover(adb, "ExamplePhone", 3)
        self.assertEqual(adb.actions, [])

    def test_busy_phone_does_not_restart_or_change_apps(self):
        adb = FakeAdb(states="mCallState=2\n", shizuku=False)
        self.assertEqual(
            inspect_and_recover(adb, "ExamplePhone")["phase"], "call_in_progress_no_recovery"
        )
        self.assertFalse(any(action[0] in ("pm", "pidof") for action in adb.actions))

    def test_unknown_call_state_fails_closed(self):
        adb = FakeAdb(states="unavailable")
        self.assertEqual(inspect_and_recover(adb, "ExamplePhone")["phase"], "call_state_unknown")

    def test_selected_inbound_contract_is_observed_without_active_call_recovery(self):
        adb = FakeAdb(states="mCallState=1\nmCallState=0\n", shizuku=False)
        adb.gateway_status = "result=1 ready=true takeover=on audioReady=true loopback=off sim_slot=1 sim_pinned=true incoming=selected-only inbound_contract=2"
        result = inspect_and_recover(adb, "ExamplePhone")
        self.assertEqual(result["inbound_contract"], 2)
        self.assertTrue(result["incoming_selected_only"])
        self.assertFalse(result["incoming_calls_disabled"])
        self.assertEqual(result["phase"], "call_in_progress_no_recovery")
        self.assertFalse(any(action[0] in ("pm", "pidof") for action in adb.actions))
        self.assertEqual(
            [action[-1] for action in adb.actions if action[:2] == ("am", "broadcast")], ["status"]
        )

    def test_locked_boot_waits_for_owner_unlock(self):
        adb = FakeAdb(unlocked=False)
        self.assertEqual(
            inspect_and_recover(adb, "ExamplePhone")["phase"], "waiting_for_user_unlock"
        )
        self.assertFalse(any(action[0] == "pm" for action in adb.actions))

    def test_wrong_device_is_never_modified(self):
        adb = FakeAdb(model="different-phone")
        self.assertEqual(inspect_and_recover(adb, "ExamplePhone")["phase"], "unexpected_device")
        self.assertEqual(len(adb.actions), 1)

    def test_recovery_does_not_enable_calling_or_echo(self):
        adb = FakeAdb(shizuku=False)
        result = inspect_and_recover(adb, "ExamplePhone")
        self.assertEqual(result["phase"], "standby")
        self.assertEqual(result["recovered"], ["shizuku"])
        self.assertFalse(result["call_ready"])
        self.assertFalse(
            any(
                "takeover" in action or "loopback" in action or "CALL" in action
                for action in adb.actions
            )
        )

    def test_async_shizuku_start_is_confirmed_before_reporting_recovery(self):
        class DelayedAdb(FakeAdb):
            remaining = 2

            def shell(self, *args, **kwargs):
                result = super().shell(*args, **kwargs)
                if args == ("pidof", "shizuku_server") and self.shizuku and self.remaining:
                    self.remaining -= 1
                    return 1, ""
                return result

        adb = DelayedAdb(shizuku=False)
        with patch("controller.time.sleep"):
            result = inspect_and_recover(adb, "ExamplePhone")
        self.assertEqual(result["phase"], "standby")
        self.assertIn("shizuku", result["recovered"])

    def test_guarded_package_uses_original_class_namespace(self):
        adb = FakeAdb(shizuku=False)
        result = inspect_and_recover(adb, "ExamplePhone")
        self.assertTrue(result["control_ready"])
        self.assertIn(
            ("am", "start", "-W", "-n", PACKAGE + "/" + APP_NAMESPACE + ".MainActivity"),
            adb.actions,
        )

    def test_other_sim_or_incoming_handling_cannot_report_ready(self):
        for status in [
            "result=1 ready=true audioReady=true sim_slot=2 sim_pinned=true incoming=off",
            "result=1 ready=true audioReady=true sim_slot=1 sim_pinned=true incoming=on",
            "result=1 ready=true audioReady=true sim_slot=1 sim_pinned=false incoming=off",
        ]:
            adb = FakeAdb()
            adb.gateway_status = status
            result = inspect_and_recover(adb, "ExamplePhone")
            self.assertFalse(result["control_ready"])
            self.assertEqual(result["phase"], "gateway_sim_policy_mismatch")


if __name__ == "__main__":
    unittest.main()
