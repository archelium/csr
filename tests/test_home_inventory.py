"""Home inventory check: capacity readings, Sort pairing, over-budget lines, rewards."""
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_stats  # noqa: E402

DAY = "2026-10-24"
ME = "archelium"
PID = "204821712526"
HOME = "3170699229"


def line(hms, body):
    return f"<{DAY}T{hms}.000Z> {body}\n"


LOGIN = line("10:00:01", f"[Notice] <Legacy login response> Handle[{ME}] login ok")
ME_LINE = line("10:00:02", f"[Notice] <Spawn> Player {ME}[{PID}] spawned")


def request(hms, code, player=ME):
    return line(hms, f"[Notice] <RequestLocationInventory> Player[{player}] requested inventory "
                     f"for Location[{code}] [Team_CoreGameplayFeatures][Inventory]")


def reading(hms, used, cap=5000000000, loc=HOME, pid=PID):
    return line(hms, f"[Notice] <OnDragInventoryItemModifyTarget> Source[{pid}:Location:{loc}] "
                     f"Capacity[{cap}] UsedCapacity[{used}.000000] MovedCapacity[0.000000] "
                     f"CapacityAfter[0.000000] CapacityAfterPerc[0.0] | Target[INVALID] Capacity[-1] "
                     f"UsedCapacity[0.000000] [Team_CoreGameplayFeatures][Inventory]")


def queued(hms, n, loc=HOME, pid=PID):
    return line(hms, f"[Notice] <Inventory Mgmt Request Queued> Queued Request[{n}] Type[Sort] for '{ME}' "
                     f"[{pid}] Source Inventory[{pid}:Location:{loc}] Target Inventory[INVALID]. Source[NULL]")


def done(hms, n, result):
    return line(hms, f"[Notice] <Player Inventory Request Complete> Request[{n}] for '{ME}' [{PID}] "
                     f"Type[Sort] Result[{result}] Item[0] CanLockQueue[Yes].")


def budget(hms, size, hard=False, who=ME):
    if hard:
        head = (f"[Error] <Remote Method Over Budget> Remote Method size {size} is greater than budget "
                f"1000000 and will not be replicated.")
    else:
        head = (f"[Error] <Remote Method Over Budget (Soft Limit)> Remote Method size {size} is greater "
                f"than soft limit of 41000. Anything over budget of 1000000 bytes will not be replicated.")
    return line(hms, f'{head} rmName="pi.RmAuthorityProcessInventoryManagementOperation", '
                     f'component="SCPlayer", entityId={PID}, entityName="{who}" [Team_ActorFeatures][Network]')


def rewards(hms, n):
    return (line(hms, f'[Notice] <SHUDEvent_OnNotification> Added notification "You\'ve Earned: {n} Rewards')
            + line(hms, "Access Them at Your Primary Residence's Inventory")
            + line(hms, ': " [141] to queue. New queue size: 3 [Team_CoreGameplayFeatures][Missions][Comms]'))


def scan(text):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "Game.log")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(line("10:00:00", "Log started") + LOGIN + ME_LINE + text + line("11:00:00", "end"))
        return sc_stats.scan_log(p)


def session(date, inv):
    base = scan(line("10:00:00", "Log started") + line("11:00:00", "end"))
    y, m, d = (int(x) for x in date.split("-"))
    return dict(base, start=datetime(y, m, d, 20, 0), inv=inv)


def inv(used, cap=5000000000, code="Stanton4_NewBabbage", sorts=None, budget=None, rewards=0, loc=HOME):
    return {"locs": {loc: [used, cap, code]}, "sorts": {loc: sorts} if sorts else {},
            "budget": budget or [], "rewards": rewards}


class ScanLogTests(unittest.TestCase):
    def test_session_keeps_the_maximum_reading_not_the_last(self):
        s = scan(request("10:05:00", "Stanton4_NewBabbage")
                 + reading("10:05:01", 6812842155) + reading("10:05:02", 1933000000))
        self.assertEqual(s["inv"]["locs"][HOME], [6812842155, 5000000000, "Stanton4_NewBabbage"])

    def test_micro_scu_converts_to_scu_in_the_summary(self):
        v = SummaryTests().finalize([session(DAY, inv(6812842155))])
        home = v["home"]
        self.assertEqual((home["used"], home["cap"], home["pct"]), (6813, 5000, 136.3))

    def test_location_named_by_the_code_requested_most_before_readings(self):
        s = scan(request("10:05:00", "Stanton4_NewBabbage") + reading("10:05:01", 100)
                 + request("10:06:00", "RR_MIC_L1") + reading("10:06:01", 200)
                 + request("10:07:00", "Stanton4_NewBabbage") + reading("10:07:01", 300))
        self.assertEqual(s["inv"]["locs"][HOME][2], "Stanton4_NewBabbage")

    def test_sort_requests_pair_with_their_result(self):
        s = scan(queued("10:10:00", 7) + done("10:10:01", 7, "Failed")
                 + queued("10:11:00", 8) + done("10:11:01", 8, "Succeed")
                 + queued("10:12:00", 9) + done("10:12:01", 9, "Failed")
                 + queued("10:13:00", 10))                      # never completed
        self.assertEqual(s["inv"]["sorts"], {HOME: [1, 2]})

    def test_soft_and_hard_over_budget_for_own_entity_only(self):
        s = scan(budget("10:20:00", 758979) + budget("10:21:00", 1022222, hard=True)
                 + budget("10:22:00", 900000, who="someone"))
        self.assertEqual(s["inv"]["budget"], [[758979, False], [1022222, True]])

    def test_rewards_sum_across_a_multi_line_notice(self):
        s = scan(rewards("10:30:00", 12) + rewards("10:31:00", 5))
        self.assertEqual(s["inv"]["rewards"], 17)

    def test_no_inventory_lines_gives_none(self):
        self.assertIsNone(scan(line("10:05:00", "[Notice] <Something> else"))["inv"])


class SummaryTests(unittest.TestCase):
    def finalize(self, sessions):
        agg = sc_stats.blank_patch()
        for s in sessions:
            sc_stats.fold(agg, s)
        return sc_stats.finalize(agg)["inventory"]

    def test_home_is_the_location_read_in_most_sessions(self):
        other = "111"
        elsewhere = {"locs": {other: [9000000000, 10000000000, "RR_MIC_L1"]},
                     "sorts": {}, "budget": [], "rewards": 0}
        v = self.finalize([
            session("2026-10-01", elsewhere),
            session("2026-10-02", inv(2000000000)),
            session("2026-10-03", inv(3000000000)),
        ])
        self.assertEqual(v["home"]["name"], "New Babbage")
        self.assertEqual(v["home"]["sessions"], 2)
        self.assertEqual([r[0] for r in v["others"]], ["MIC-L1 Shallow Frontier Station"])

    def test_months_hold_the_highest_percent_and_sorts_add_up(self):
        v = self.finalize([
            session("2026-09-05", inv(2500000000, sorts=[0, 1])),
            session("2026-09-20", inv(4000000000, sorts=[1, 2])),
            session("2026-10-02", inv(6812842155)),
        ])
        self.assertEqual(v["home"]["months"], [["2026-09", 80.0], ["2026-10", 136.3]])
        self.assertEqual(v["home"]["sorts"], [1, 3])

    def test_largest_request_comes_from_the_latest_session_that_logged_one(self):
        v = self.finalize([
            session("2026-10-01", inv(1, budget=[[1431535, True], [758979, False]])),
            session("2026-10-02", inv(1, budget=[[750341, False], [759000, False]])),
            session("2026-10-03", inv(1)),
        ])
        self.assertEqual(v["req"], {"kb": 759, "pct": 75.9, "date": "2026-10-02"})
        self.assertEqual(v["hard"], [1, "2026-10-01"])

    def test_rewards_total(self):
        v = self.finalize([session("2026-10-01", inv(1, rewards=12)),
                           session("2026-10-02", inv(1, rewards=5))])
        self.assertEqual(v["rewards"], 17)

    def test_old_session_without_the_key_folds_cleanly(self):
        old = session("2026-03-01", None)
        del old["inv"]
        self.assertIsNone(self.finalize([old]))
        v = self.finalize([old, session("2026-10-02", inv(2500000000))])
        self.assertEqual(v["home"]["pct"], 50.0)


if __name__ == "__main__":
    unittest.main()
