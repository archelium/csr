"""Per-recording telemetry summaries and session combine."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_telemetry as tlm  # noqa: E402
import sc_tlm_stats as st  # noqa: E402

WALLET = "actor.state.uec_balance"
LOCO = "actor.state.locomotion"
CREW = "actor.state.crew_role"
CLASS = "vehicle.state.class_name"
LANDED = "vehicle.state.flight.state.is_landed"
GEAR = "vehicle.state.flight.state.gear"
SPEED = "vehicle.state.flight.instruments.speed"
GMETER = "vehicle.state.flight.instruments.g_meter.current"
BOOST = "vehicle.state.flight.instruments.boost.engaged"
BOOST_CRIT = "vehicle.state.flight.instruments.boost.critical"
H2 = "vehicle.state.flight.instruments.hydrogen_fuel"
QT = "vehicle.state.flight.instruments.quantum_fuel"
SHLD = "vehicle.state.annunciators.shld"
QDRV = "vehicle.state.annunciators.qdrv"
PROX = "vehicle.state.annunciators.prox"
SHIELDS = "vehicle.state.power.shield_generators"
OXY = "actor.vitals.oxygen.current"
OXY_MAX = "actor.vitals.oxygen.max"
HEALTH = "actor.vitals.health.current"
HEALTH_MAX = "actor.vitals.health.max"
HUNGER = "actor.vitals.hunger.current"
HUNGER_MAX = "actor.vitals.hunger.max"
INTOX = "actor.vitals.intoxication_level"
RAD = "actor.environment.ambient_radiation_rate"
EXT = "actor.environment.external_temperature"
ESP = "vehicle.state.flight.settings.ifcs_esp.active"
CPRED = "vehicle.state.flight.settings.course_prediction"
PRESET = "weapons.preset_name"
CM0T = "weapons.countermeasures[0].type"
CM0A = "weapons.countermeasures[0].amount"
CM1T = "weapons.countermeasures[1].type"
CM1A = "weapons.countermeasures[1].amount"

POWER_X = "vehicle.state.power.engines"
HEADER = {"v": 1, "channel": "LIVE", "build": "1234", "started": "2026-11-24T19:50:00"}


class SummaryBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "20261124-195000.jsonl.gz")

    def record(self, steps, detail="full", groups=None, close=True, header=None):
        w = tlm.RecordingWriter(self.path, header or HEADER, detail,
                                tlm.DEFAULT_GROUPS if groups is None else groups)
        for t, flat, *ev in steps:
            w.add(t, flat, ev[0] if ev else [])
        if close:
            w.close()
        return w

    def sum(self, steps, **kw):
        self.record(steps, **kw)
        return st.summarize(self.path)["sum"]


class SummaryTests(SummaryBase):
    def test_wallet(self):
        steps = [(i * 1.0, {WALLET: b}) for i, b in enumerate([1000, 1500, 1200, 1200, 2000])]
        s = self.sum(steps)
        self.assertEqual((s["uec_start"], s["uec_end"]), (1000, 2000))
        self.assertEqual((s["uec_earned"], s["uec_spent"]), (1300, 300))
        self.assertEqual((s["uec_max_rise"], s["uec_max_drop"]), (800, 300))
        self.assertEqual(s["uec_series"][0], ["2026-11-24T19:50:00", 1000])
        self.assertEqual(s["uec_series"][-1], ["2026-11-24T19:50:04", 2000])
        self.assertEqual(len(s["uec_series"]), 4)

    def test_wallet_series_thinned_keeps_ends_and_big_steps(self):
        steps = [(i * 0.1, {WALLET: 1000 + i}) for i in range(500)]
        steps[250] = (25.0, {WALLET: 9000})
        s = self.sum(steps)
        series = s["uec_series"]
        self.assertLessEqual(len(series), 200)
        self.assertEqual(series[0][1], 1000)
        self.assertEqual(series[-1][1], 1499)
        self.assertIn(9000, [p[1] for p in series])
        self.assertEqual(series, sorted(series, key=lambda p: p[0]))
        self.assertEqual(s["uec_max_rise"], 9000 - 1249)

    def test_wallet_null_balance_skipped(self):
        s = self.sum([(0.0, {WALLET: 100}), (1.0, {WALLET: None}), (2.0, {WALLET: 150})])
        self.assertEqual((s["uec_start"], s["uec_end"], s["uec_earned"]), (100, 150, 50))

    def test_header_fields(self):
        self.record([(0.0, {WALLET: 1}), (90.0, {WALLET: 2})])
        out = st.summarize(self.path)
        self.assertEqual(out["file"], "20261124-195000.jsonl.gz")
        self.assertEqual((out["channel"], out["build"], out["v"]), ("LIVE", "1234", st.SUMMARY_VERSION))
        self.assertEqual(out["started"], "2026-11-24T19:50:00")
        self.assertEqual(out["ended"], "2026-11-24T19:51:30")
        self.assertIn("wallet", out["groups"])

    def test_empty_recording_is_none(self):
        self.record([])
        self.assertIsNone(st.summarize(self.path))
        self.assertIsNone(st.summarize(os.path.join(self.tmp.name, "nope.jsonl.gz")))

    def test_flight_takeoff_landing_distance(self):
        seated = {LOCO: "SEATED", CLASS: "X"}
        steps = [(0.0, dict(seated, **{LANDED: True})), (2.0, dict(seated, **{SPEED: 100.0})),
                 (12.0, dict(seated, **{LANDED: True}))]
        s = self.sum(steps)
        self.assertEqual((s["takeoffs"], s["landings"]), (1, 1))
        self.assertAlmostEqual(s["dist_m"], 1000, delta=1)
        self.assertAlmostEqual(s["t_flying"], 10, delta=0.1)
        self.assertEqual(s["top_speed"], 100.0)

    def test_quiet_stretch_counts_in_full(self):
        s = self.sum([(0.0, {LOCO: "ON_FOOT"}), (60.0, {LOCO: "EVA"})])
        self.assertEqual(s["t_on_foot"], 60)

    def test_close_end_time_counts_last_stretch(self):
        w = self.record([(0.0, {LOCO: "ON_FOOT"}), (30.0, {LOCO: "ON_FOOT"})], close=False)
        w.close()
        out = st.summarize(self.path)
        self.assertEqual(out["sum"]["t_on_foot"], 30)
        self.assertEqual(out["ended"], "2026-11-24T19:50:30")

    def test_wallet_zero_is_dropped_by_proto3(self):
        s = self.sum([(0.0, {WALLET: 500, LOCO: "ON_FOOT"}), (1.0, {LOCO: "ON_FOOT"}),
                      (2.0, {WALLET: 300, LOCO: "ON_FOOT"})])
        self.assertEqual((s["uec_spent"], s["uec_earned"]), (500, 300))
        self.assertEqual(s["uec_end"], 300)

    def test_wallet_starts_at_first_explicit_balance(self):
        s = self.sum([(0.0, {LOCO: "ON_FOOT"}), (1.0, {LOCO: "ON_FOOT", WALLET: 82000000}),
                      (2.0, {LOCO: "ON_FOOT", WALLET: 82000000})])
        self.assertEqual(s["uec_start"], 82000000)
        self.assertEqual((s["uec_earned"], s["uec_spent"]), (0, 0))
        self.assertEqual(s["uec_series"][0][1], 82000000)

    def test_wallet_missing_without_actor_state_is_unknown(self):
        s = self.sum([(0.0, {WALLET: 500}), (1.0, {HEALTH: 0.5}), (2.0, {WALLET: 300})],
                     groups=["wallet", "vitals"])
        self.assertEqual((s["uec_spent"], s["uec_earned"]), (200, 0))

    def test_vehicle_leaving_is_not_a_takeoff(self):
        steps = [(0.0, {LOCO: "SEATED", CLASS: "X", LANDED: True}),
                 (1.0, {LOCO: "ON_FOOT"}),
                 (2.0, {LOCO: "SEATED", CLASS: "X", LANDED: True})]
        s = self.sum(steps)
        self.assertEqual((s["takeoffs"], s["landings"]), (0, 0))
        self.assertEqual(s["t_flying"], 0)

    def test_no_vehicle_flight_numbers_are_zero(self):
        s = self.sum([(0.0, {LOCO: "ON_FOOT"}), (1.0, {LOCO: "ON_FOOT"})])
        self.assertEqual((s["top_speed"], s["max_g"], s["dist_m"], s["t_flying"]), (0, 0, 0, 0))

    def test_peaks_count_for_records(self):
        w = tlm.RecordingWriter(self.path, HEADER, "normal", tlm.DEFAULT_GROUPS)
        base = {LOCO: "SEATED", CLASS: "X"}
        for i in range(20):
            g = 9.8 if i == 3 else 1.0
            w.add(i * 0.1, dict(base, **{GMETER: g, SPEED: 100.0 + (50.0 if i == 4 else 0)}), [])
        w.close()
        s = st.summarize(self.path)["sum"]
        self.assertEqual(s["max_g"], 9.8)
        self.assertEqual(s["top_speed"], 150.0)

    def test_low_peaks_count_for_min_temp(self):
        w = tlm.RecordingWriter(self.path, HEADER, "normal", tlm.DEFAULT_GROUPS)
        for i in range(20):
            w.add(i * 0.1, {EXT: -50.0 if i == 2 else 20.0}, [])
        w.close()
        s = st.summarize(self.path)["sum"]
        self.assertEqual((s["min_ext_temp"], s["max_ext_temp"]), (-50.0, 20.0))

    def test_nav_inference(self):
        base = {LOCO: "SEATED", CLASS: "X", LANDED: False}
        steps = [(float(i), dict(base, **{SHLD: "OFF", QDRV: "NOMINAL", SPEED: float(i)}))
                 for i in range(4)]
        steps += [(float(i), dict(base, **{SHLD: "NOMINAL", QDRV: "NOMINAL", SPEED: float(i)}))
                  for i in range(4, 11)]
        s = self.sum(steps)
        self.assertAlmostEqual(s["t_nav"], 4, delta=0.01)
        self.assertAlmostEqual(s["t_scm"], 6, delta=0.01)
        self.assertAlmostEqual(s["t_flying"], 10, delta=0.01)

    def test_nav_needs_ship_group(self):
        base = {LOCO: "SEATED", CLASS: "X", SHLD: "OFF", QDRV: "NOMINAL"}
        s = self.sum([(0.0, base), (1.0, dict(base))], groups=["you", "flight", "events"])
        self.assertIn("t_flying", s)
        self.assertNotIn("t_nav", s)
        self.assertNotIn("t_scm", s)
        self.assertNotIn("warn", s)

    def test_warn_transitions(self):
        base = {LOCO: "SEATED", CLASS: "X"}
        seq = ["NOMINAL", "MINOR_ON", "MAJOR_ON", "NOMINAL", "MAJOR_ON"]
        s = self.sum([(float(i), dict(base, **{PROX: v})) for i, v in enumerate(seq)])
        self.assertEqual(s["warn"], {"prox": 2})

    def test_warn_flashing_to_on_is_one(self):
        base = {LOCO: "SEATED", CLASS: "X"}
        seq = ["OFF", "MINOR_FLASHING", "MINOR_ON"]
        s = self.sum([(float(i), dict(base, **{PROX: v})) for i, v in enumerate(seq)])
        self.assertEqual(s["warn"], {"prox": 1})

    def test_warn_first_observation_not_counted(self):
        base = {LOCO: "SEATED", CLASS: "X"}
        s = self.sum([(0.0, dict(base, **{PROX: "MAJOR_ON"})), (1.0, dict(base, **{PROX: "NOMINAL"}))])
        self.assertEqual(s["warn"], {})

    def test_group_off_means_absent(self):
        groups = [g for g in tlm.DEFAULT_GROUPS if g != "vitals"]
        s = self.sum([(0.0, {OXY: 0.0, HEALTH: 0.5, WALLET: 5}), (1.0, {WALLET: 6})], groups=groups)
        for k in ("t_no_air", "min_health", "t_starving", "t_dehydrated", "max_intox"):
            self.assertNotIn(k, s)
        self.assertIn("uec_start", s)

    def test_group_off_flight_and_wallet(self):
        s = self.sum([(0.0, {WALLET: 5, SPEED: 1.0, LOCO: "ON_FOOT"}), (1.0, {WALLET: 6})],
                     groups=["you"])
        for k in ("uec_start", "top_speed", "dist_m", "takeoffs", "t_flying", "t_ifcs"):
            self.assertNotIn(k, s)
        self.assertIn("t_on_foot", s)

    def test_you_times(self):
        steps = [(0.0, {LOCO: "ON_FOOT"}), (2.0, {LOCO: "EVA"}), (3.0, {LOCO: "SEATED", CREW: "PILOT"}),
                 (5.0, {LOCO: "SEATED", CREW: "GUNNER"}), (6.0, {LOCO: "SEATED", CREW: "CREW"}),
                 (7.0, {LOCO: "ON_FOOT"})]
        s = self.sum(steps)
        self.assertEqual((s["t_on_foot"], s["t_eva"], s["t_seated"]), (2, 1, 4))
        self.assertEqual((s["t_pilot"], s["t_gunner"], s["t_crew"]), (2, 1, 1))

    def test_events(self):
        steps = [(0.0, {}, ["ACTOR_DOWNED"]), (1.0, {}, ["ACTOR_DEAD", "ACTOR_RESPAWN"]),
                 (2.0, {}, ["ACTOR_ENTER_SEAT"]), (3.0, {}, ["ACTOR_ENTER_SEAT", "ACTOR_DOWNED"])]
        s = self.sum(steps)
        self.assertEqual((s["n_downed"], s["n_dead"], s["n_respawn"], s["n_seat"]), (2, 1, 1, 2))
        s = self.sum([(t, {WALLET: int(t)}, ev) for t, _, ev in steps], groups=["wallet"])
        self.assertNotIn("n_downed", s)

    def test_vitals(self):
        steps = [(0.0, {OXY: 1.0, OXY_MAX: 1.0, HEALTH: 0.9, HEALTH_MAX: 1.0, HUNGER: 0.5, HUNGER_MAX: 1.0,
                        INTOX: 0}),
                 (1.0, {OXY_MAX: 1.0, HEALTH: 0.4, HEALTH_MAX: 1.0, HUNGER_MAX: 1.0, INTOX: 2}),
                 (4.0, {OXY: 0.3, OXY_MAX: 1.0, HEALTH: 0.4, HEALTH_MAX: 1.0, HUNGER: 0.2,
                        HUNGER_MAX: 1.0, INTOX: 1}),
                 (5.0, {OXY: 0.3, OXY_MAX: 1.0, HEALTH: 0.4, HEALTH_MAX: 1.0, HUNGER: 0.2,
                        HUNGER_MAX: 1.0, INTOX: 1})]
        s = self.sum(steps)
        self.assertEqual(s["t_no_air"], 3)
        self.assertEqual(s["t_starving"], 3)
        self.assertEqual(s["min_health"], 0.4)
        self.assertEqual(s["max_intox"], 2)
        self.assertEqual(s["t_dehydrated"], 0)

    def test_vitals_gauge_absent_is_unknown_not_zero(self):
        s = self.sum([(0.0, {HEALTH: 0.9, HEALTH_MAX: 1.0}), (3.0, {HEALTH: 0.9, HEALTH_MAX: 1.0})])
        self.assertEqual(s["t_no_air"], 0)
        self.assertEqual(s["t_starving"], 0)

    def test_surroundings(self):
        s = self.sum([(0.0, {RAD: 0.1, EXT: 20.0}), (1.0, {RAD: 0.5, EXT: -40.0}),
                      (2.0, {RAD: 0.2, EXT: 90.0})])
        self.assertEqual((s["max_radiation"], s["max_ext_temp"], s["min_ext_temp"]), (0.5, 90.0, -40.0))
        s = self.sum([(0.0, {WALLET: 1})])
        self.assertNotIn("min_ext_temp", s)
        self.assertNotIn("max_radiation", s)

    def test_gear_boost_fuel(self):
        base = {LOCO: "SEATED", CLASS: "X"}
        steps = [(0.0, dict(base, **{GEAR: "DEPLOYED", H2: 0.9, QT: 0.8})),
                 (1.0, dict(base, **{GEAR: "RETRACTING", H2: 0.8, QT: 0.8, BOOST: True})),
                 (2.0, dict(base, **{GEAR: "RETRACTED", H2: 0.85, QT: 0.5, BOOST: True, BOOST_CRIT: True})),
                 (3.0, dict(base, **{GEAR: "RETRACTED", H2: 0.7, QT: 0.5})),
                 (4.0, dict(base, **{GEAR: "DEPLOYED", H2: 0.7, QT: 0.5})),
                 (5.0, dict(base, **{GEAR: "RETRACTED", H2: 0.7, QT: 0.5, BOOST_CRIT: True}))]
        s = self.sum(steps)
        self.assertEqual(s["gear_cycles"], 2)
        self.assertEqual(s["t_boost"], 2)
        self.assertEqual(s["n_boost_critical"], 2)
        self.assertAlmostEqual(s["h2_used"], 0.1 + 0.15, places=6)
        self.assertAlmostEqual(s["qt_used"], 0.3, places=6)

    def test_shields_off_flying_and_ifcs(self):
        base = {LOCO: "SEATED", CLASS: "X", LANDED: False, POWER_X: True}
        steps = [(0.0, dict(base, **{SHIELDS: True, ESP: True})),
                 (2.0, dict(base, **{ESP: True, CPRED: True})),
                 (5.0, dict(base, **{SHIELDS: True, LANDED: True, ESP: True})),
                 (6.0, dict(base, **{LANDED: True}))]
        s = self.sum(steps)
        self.assertEqual(s["t_shields_off_flying"], 3)
        self.assertEqual(s["t_ifcs"], {"ifcs_esp": 5, "course_prediction": 3})

    def test_shields_off_needs_power_message(self):
        base = {LOCO: "SEATED", CLASS: "X"}
        s = self.sum([(0.0, base), (5.0, dict(base, **{SPEED: 1.0})), (6.0, base)])
        self.assertEqual(s["t_shields_off_flying"], 0)

    def test_ifcs_needs_flight_group(self):
        base = {LOCO: "SEATED", CLASS: "X", ESP: True}
        s = self.sum([(0.0, base), (1.0, base)], groups=["you", "settings"])
        self.assertNotIn("t_ifcs", s)

    def test_countermeasures_and_presets(self):
        steps = [(0.0, {PRESET: "A", CM0T: "DECOY", CM0A: 10, CM1T: "NOISE", CM1A: 5}),
                 (1.0, {PRESET: "A", CM0T: "DECOY", CM0A: 8, CM1T: "NOISE", CM1A: 5}),
                 (3.0, {PRESET: "B", CM0T: "DECOY", CM0A: 8, CM1T: "NOISE", CM1A: 4}),
                 (4.0, {PRESET: "B", CM0T: "DECOY", CM1T: "NOISE", CM1A: 4}),
                 (5.0, {})]
        s = self.sum(steps)
        self.assertEqual(s["cm_used"], {"DECOY": 10, "NOISE": 1})
        self.assertEqual(s["presets"], {"A": 3, "B": 2})

    def test_cut_off_recording_summarizes(self):
        w = tlm.RecordingWriter(self.path, HEADER, "full", tlm.DEFAULT_GROUPS)
        for i in range(400):
            w.add(i * 0.1, {WALLET: 1000 + i * 7, LOCO: "ON_FOOT", HEALTH: 0.5 + (i % 5) / 10}, [])
        w._sync()
        with open(self.path, "rb") as fh:
            data = fh.read()
        crash = os.path.join(self.tmp.name, "crash.jsonl.gz")
        with open(crash, "wb") as fh:
            fh.write(data[:-9])
        head, rows = tlm.read_recording(crash)
        self.assertEqual(head["channel"], "LIVE")
        self.assertGreater(len(rows), 100)
        out = st.summarize(crash)
        self.assertIsNotNone(out)
        self.assertEqual(out["sum"]["uec_start"], 1000)
        self.assertGreater(out["sum"]["uec_end"], 1000)
        w.close()


class CombineTests(SummaryBase):
    def entry(self, name, steps, **kw):
        self.path = os.path.join(self.tmp.name, name)
        self.record(steps, **kw)
        return st.summarize(self.path)

    def test_combine(self):
        a = self.entry("a.jsonl.gz", [
            (0.0, {WALLET: 1000, LOCO: "ON_FOOT", HEALTH: 0.6, EXT: 10.0, SPEED: 5.0, PRESET: "A"}),
            (1.0, {WALLET: 1500, LOCO: "ON_FOOT", HEALTH: 0.6, EXT: 30.0, SPEED: 5.0, PRESET: "A"}),
            (2.0, {WALLET: 1200, LOCO: "ON_FOOT", HEALTH: 0.6, EXT: 10.0, PRESET: "A"})])
        b = self.entry("b.jsonl.gz", [
            (0.0, {WALLET: 3000, LOCO: "EVA", HEALTH: 0.2, EXT: -5.0, PRESET: "B"}),
            (3.0, {WALLET: 3100, LOCO: "EVA", HEALTH: 0.9, EXT: 5.0, PRESET: "A"}),
            (4.0, {WALLET: 2000, LOCO: "ON_FOOT", HEALTH: 0.9, EXT: 5.0, PRESET: "A"})])
        s = st.combine([a, b])
        self.assertEqual((s["uec_start"], s["uec_end"]), (1000, 2000))
        self.assertEqual((s["uec_earned"], s["uec_spent"]), (500 + 100, 300 + 1100))
        self.assertEqual((s["uec_max_rise"], s["uec_max_drop"]), (500, 1100))
        self.assertEqual(len(s["uec_series"]), len(a["sum"]["uec_series"]) + len(b["sum"]["uec_series"]))
        self.assertEqual((s["t_on_foot"], s["t_eva"]), (2, 4))
        self.assertEqual(s["min_health"], 0.2)
        self.assertEqual((s["max_ext_temp"], s["min_ext_temp"]), (30.0, -5.0))
        self.assertEqual(s["presets"], {"A": 3, "B": 3})
        self.assertEqual(s["top_speed"], 5.0)

    def test_combine_sorts_by_started(self):
        a = self.entry("a.jsonl.gz", [(0.0, {WALLET: 1000}), (1.0, {WALLET: 1500})])
        b = self.entry("b.jsonl.gz", [(0.0, {WALLET: 1500}), (1.0, {WALLET: 900})],
                       header=dict(HEADER, started="2026-11-24T21:00:00"))
        c = {"started": None, "sum": {"uec_start": 7, "uec_end": 7}}
        self.assertEqual(st.combine([b, a]), st.combine([a, b]))
        self.assertEqual(st.combine([c, b, a]), st.combine([a, b, c]))
        s = st.combine([b, a, c])
        self.assertEqual((s["uec_start"], s["uec_end"]), (1000, 7))
        self.assertEqual(s["uec_series"][0][1], 1000)

    def test_combine_absent_stays_absent(self):
        a = self.entry("a.jsonl.gz", [(0.0, {WALLET: 5}), (1.0, {WALLET: 6})], groups=["wallet"])
        b = self.entry("b.jsonl.gz", [(0.0, {HEALTH: 0.5}), (1.0, {HEALTH: 0.4})], groups=["vitals"])
        self.assertEqual(sorted(st.combine([a]).keys()), sorted(a["sum"].keys()))
        s = st.combine([a, b])
        self.assertEqual(s["uec_start"], 5)
        self.assertEqual(s["min_health"], 0.4)
        self.assertEqual(st.combine([]), {})

    def test_combine_dict_keys_and_series_thinning(self):
        a = {"sum": {"warn": {"prox": 1}, "cm_used": {"DECOY": 2},
                     "uec_series": [["2026-11-24T19:50:%02d" % (i % 60), i] for i in range(300)]}}
        b = {"sum": {"warn": {"prox": 2, "hit": 1}, "cm_used": {"NOISE": 1},
                     "uec_series": [["2026-11-24T20:50:%02d" % (i % 60), i] for i in range(300)]}}
        s = st.combine([a, b])
        self.assertEqual(s["warn"], {"prox": 3, "hit": 1})
        self.assertEqual(s["cm_used"], {"DECOY": 2, "NOISE": 1})
        self.assertLessEqual(len(s["uec_series"]), 400)


if __name__ == "__main__":
    unittest.main()
