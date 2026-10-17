import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import worker  # noqa: E402

GiB = 1024 ** 3
DATA = {
    "observedAt": "2026-10-17T07:00:00Z",
    "hosts": [
        {"id": "local", "name": "main", "status": "ok", "error": None,
         "reading": {"os": "Darwin", "cores": 12, "cpu": 30.0, "memTotal": 32 * GiB, "memAvailable": 16 * GiB,
                     "disks": [{"total": 100, "used": 90}, {"total": 100, "used": 10}]},
         "history": [{"cpuAvg": 10.0, "memAvg": 50.0, "diskMax": 90.0}, {"cpuAvg": None, "memAvg": 51.0}],
         "providers": [{"toolId": "codex", "label": "Codex", "usage": {"status": "fresh", "plan": "Business",
                        "windows": [{"label": "Weekly", "usedPercent": 15}]}}]},
        {"id": "sub", "name": "sub", "status": "ok", "error": None,
         "reading": {"cpu": 5.0, "memTotal": 16 * GiB, "memAvailable": 8 * GiB, "disks": []},
         "providers": [{"toolId": "codex", "label": "Codex", "usage": {"status": "fresh", "plan": "Business",
                        "windows": [{"label": "Weekly", "usedPercent": 15}]}},
                       {"toolId": "grok", "label": "Grok", "usage": {"status": "stale", "plan": "x"}}]},
        {"id": "gone", "name": "gone", "status": "error", "error": "ssh timeout"},
    ],
}


class WorkerTests(unittest.TestCase):
    def test_summary_numbers_and_series(self):
        s = worker.summarize(DATA["hosts"][0])
        self.assertEqual((s["mem"], s["disk"], s["up"]), (50.0, 90.0, True))
        self.assertEqual(s["cpuSeries"], [10.0, 0])

    def test_panes_and_pages(self):
        out = worker.build(DATA)
        servers, subs = out["servers"], out["subscriptions"]
        self.assertEqual(servers["footer"]["value"], "서버 2/3")
        self.assertEqual(servers["blocks"][0]["href"], "/plugins/dev.hyunwook.home-monitor/dashboard")
        callout = servers["blocks"][1]
        self.assertIn("gone", callout["detail"])
        self.assertIn("main ? 90%", callout["detail"])  # per-mount disk warning
        main = [b for b in servers["blocks"] if b["kind"] == "section"][0]
        kinds = [c.get("prefix") for c in main["children"] if c["kind"] == "row"]
        self.assertEqual(kinds, ["네트워크", "디스크", "디스크"])
        self.assertEqual(subs["title"], "구독 상태")
        self.assertEqual(len(out["subscriptions_page"]["subscriptions"]), 1)
        self.assertEqual(len(out["monitor"]["hosts"][0]["disks"]), 2)
        for value in out.values():
            self.assertLess(len(json.dumps(value).encode()), 64 * 1024)

    def test_usage_split_into_subscription_pane(self):
        usage = {"hosts": [{"host": "m4", "providers": [{"tool": "codex", "plan": "Team", "account": "a@x",
                                                         "status": "fresh", "windows": [{"label": "Weekly", "used": 85}]}]}]}
        out = worker.build(DATA, usage)
        titles = [b.get("title") for b in out["subscriptions"]["blocks"]]
        self.assertIn("한도에 가까운 구독", titles)
        self.assertIn("구독 · m4", titles)
        self.assertNotIn("구독 · m4", [b.get("title") for b in out["servers"]["blocks"]])
        self.assertEqual(worker.human_bytes(1653454.5, True), "1.7 MB/s")


if __name__ == "__main__":
    unittest.main()
