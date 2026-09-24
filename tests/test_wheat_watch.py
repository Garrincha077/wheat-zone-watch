import unittest

from wheat_watch import Checks, Evaluation, Metrics, apply_hysteresis, build_message, trigger_requirements


def metrics(bar_date: str = "2026-09-24") -> Metrics:
    return Metrics(
        bar_date=bar_date,
        price=705.5,
        atr14=15.7,
        ema10=720.4,
        ema20=722.8,
        sma50=706.4,
        sma30w=664.4,
        distance_sma50_atr=-0.06,
        ema_spread_atr=0.16,
        ema10_slope_atr_day=-0.18,
        ema20_slope_atr_day=-0.09,
    )


def stage2_checks() -> Checks:
    return Checks(
        sma50_rising=True,
        sma30w_rising=True,
        structure_valid=True,
        not_extended=True,
        price_near_sma50=True,
        ema_compressed=True,
        recent_pullback_compression=True,
        ema10_flattening_or_rising=False,
        ema20_flattening_or_rising=False,
        ema10_rising=False,
        ema20_non_falling=False,
        at_least_one_short_ema_rising=False,
        ema10_above_ema20=False,
        close_above_short_emas=False,
        bullish_reexpansion=False,
    )


class WheatWatchTests(unittest.TestCase):
    def test_trigger_checklist_marks_missing_items(self):
        checks = stage2_checks()
        requirement_map = dict(trigger_requirements(checks))
        self.assertTrue(requirement_map["SMA50 rising"])
        self.assertTrue(requirement_map["Recent pullback + EMA compression armed"])
        self.assertFalse(requirement_map["EMA10 rising"])
        self.assertFalse(requirement_map["EMA10 > EMA20"])

    def test_message_is_checklist_first(self):
        evaluation = Evaluation(2, False, metrics(), stage2_checks())
        message = build_message("ZW=F", 2, evaluation)
        self.assertIn("2/4 WATCH CLOSELY", message)
        self.assertIn("✅ SMA50 rising", message)
        self.assertIn("❌ EMA10 rising", message)
        self.assertIn("Still needed for 4/4:", message)

    def test_downgrade_requires_two_distinct_daily_bars(self):
        previous = {
            "effective_stage": 3,
            "pending_downgrade_bars": 0,
            "last_bar_date": "2026-09-23",
            "last_notified_stage": 3,
        }
        evaluation_day1 = Evaluation(2, False, metrics("2026-09-24"), stage2_checks())
        effective1, state1 = apply_hysteresis(evaluation_day1, previous)
        self.assertEqual(effective1, 3)
        self.assertEqual(state1["pending_downgrade_bars"], 1)

        effective_same, state_same = apply_hysteresis(evaluation_day1, state1)
        self.assertEqual(effective_same, 3)
        self.assertEqual(state_same["pending_downgrade_bars"], 1)

        evaluation_day2 = Evaluation(2, False, metrics("2026-09-25"), stage2_checks())
        effective2, state2 = apply_hysteresis(evaluation_day2, state_same)
        self.assertEqual(effective2, 2)
        self.assertEqual(state2["pending_downgrade_bars"], 0)

    def test_hard_invalidation_is_immediate(self):
        previous = {
            "effective_stage": 4,
            "pending_downgrade_bars": 0,
            "last_bar_date": "2026-09-23",
            "last_notified_stage": 4,
        }
        evaluation = Evaluation(0, True, metrics("2026-09-24"), stage2_checks())
        effective, state = apply_hysteresis(evaluation, previous)
        self.assertEqual(effective, 0)
        self.assertEqual(state["pending_downgrade_bars"], 0)

    def test_upgrade_is_immediate(self):
        previous = {
            "effective_stage": 2,
            "pending_downgrade_bars": 0,
            "last_bar_date": "2026-09-24",
            "last_notified_stage": 2,
        }
        evaluation = Evaluation(4, False, metrics("2026-09-24"), stage2_checks())
        effective, _ = apply_hysteresis(evaluation, previous)
        self.assertEqual(effective, 4)


if __name__ == "__main__":
    unittest.main()
