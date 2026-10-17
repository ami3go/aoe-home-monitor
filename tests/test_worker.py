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

    def test_pane_and_page(self):
        pane, page = worker.build(DATA)
        self.assertEqual(pane["footer"]["value"], "서버 2/3")
        callout = pane["blocks"][0]
        self.assertEqual(callout["kind"], "callout")
        self.assertIn("gone", callout["detail"])
        self.assertIn("main 디스크 90%", callout["detail"])
        sections = [b for b in pane["blocks"] if b["kind"] == "section"]
        self.assertEqual(sections[0]["value_tone"], "warn")
        self.assertEqual(sections[2]["value"], "연결 끊김")
        subs = page["subscriptions"]
        self.assertEqual(len(subs), 1)  # same plan on two hosts is one account; stale usage is skipped
        self.assertEqual(subs[0]["hosts"], ["main", "sub"])
        self.assertLess(len(json.dumps(page).encode()), 64 * 1024)
        self.assertLess(len(json.dumps(pane).encode()), 64 * 1024)


if __name__ == "__main__":
    unittest.main()
