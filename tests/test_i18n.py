"""Offline localization regression tests (no credentials or live collectors)."""
import ast
import json
from pathlib import Path
import re
import sys
import unittest
from unittest import mock
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 can run the worker and other tests.
    tomllib = None

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import i18n
import worker


class CatalogTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("ko")

    def test_default_and_unknown_language_keep_korean(self):
        i18n.set_language("ko")
        self.assertEqual(i18n.t("구독 사용량"), "구독 사용량")
        for invalid in ("ja", "", None, {}):
            i18n.set_language(invalid)
            self.assertEqual(i18n.get_language(), "ko")

    def test_switch_both_ways_without_changing_user_values(self):
        i18n.set_language("en")
        self.assertEqual(i18n.t("구독 사용량"), "Subscription usage")
        self.assertEqual(i18n.t("지금 새로고침"), "Refresh now")
        self.assertEqual(i18n.t("unrecognized source {name}"), "unrecognized source {name}")
        source = "실패: {p0}" if "실패: {p0}" in i18n.MESSAGES else "OpenUsage 연결 실패: {p0}"
        self.assertEqual(i18n.t(source, p0="<script>{p1}</script>"),
                         i18n.MESSAGES[source].replace("{p0}", "<script>{p1}</script>"))
        i18n.set_language("ko")
        self.assertEqual(i18n.t("구독 사용량"), "구독 사용량")

    def test_catalog_covers_worker_messages_and_keeps_format_fields(self):
        from string import Formatter
        tree = ast.parse((ROOT / "worker.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tr":
                if node.args and isinstance(node.args[0], ast.Constant):
                    self.assertIn(node.args[0].value, i18n.MESSAGES)
        for source, english in i18n.MESSAGES.items():
            fields = lambda s: sorted((name, spec, conv) for _, name, spec, conv in Formatter().parse(s) if name is not None)
            self.assertEqual(fields(source), fields(english), source)
            self.assertNotRegex(english, r"[가-힣]")

    def test_manifest_has_discoverable_language_setting(self):
        if tomllib is None:
            self.skipTest("Manifest parsing requires Python 3.11+")
        manifest = tomllib.loads((ROOT / "aoe-plugin.toml").read_text())
        setting = next(s for s in manifest["settings"] if s["key"] == "language")
        self.assertEqual(setting["type"], "select")
        self.assertEqual(setting["options"], ["ko", "en"])
        self.assertEqual(setting["default"], "ko")
        self.assertIn("Language", setting["label"])


class EnglishMonitorTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("ko")

    def test_english_panels_status_and_live_switch(self):
        from test_worker import DATA
        with mock.patch.object(worker, "setting", side_effect=lambda key, default: default):
            i18n.set_language("en")
            out = worker.build(DATA)
            self.assertEqual(out["servers"]["title"], "Server status")
            self.assertEqual(out["servers"]["footer"]["value"], "Servers 2/3")
            self.assertEqual(worker.home_pane()["title"], "Dashboard")
            self.assertNotRegex(json.dumps(out, ensure_ascii=False), r"[가-힣]")
            i18n.set_language("ko")
            self.assertEqual(worker.build(DATA)["servers"]["title"], "서버 상태")
            self.assertEqual(worker.home_pane()["title"], "대시보드")

    def test_page_catalog_fits_bridge_and_layout_only_updates_keep_language(self):
        i18n.set_language("en")
        for data in ({"layout": {}}, {"error": "offline"}):
            payload = worker.page_payload("dashboard", data)
            self.assertEqual(payload["language"], "en")
            self.assertEqual(payload["messages"]["서버 모니터링"], "Server monitoring")
            self.assertLess(len(json.dumps(payload, ensure_ascii=False).encode()), 64 * 1024)
            self.assertNotIn("language", data)  # caller-owned state is unchanged
        self.assertNotIn("messages", worker.page_payload("history", {}))
        i18n.set_language("ko")
        self.assertEqual(worker.page_payload("dashboard", {})["messages"], {})

    def test_refresh_translates_collector_failure(self):
        calls = []
        with mock.patch.object(worker, "setting", side_effect=lambda key, default: "en" if key == "language" else default), \
             mock.patch.object(worker, "fetch", side_effect=OSError("offline")), \
             mock.patch.object(worker, "call", side_effect=lambda method, params: calls.append((method, params)) or {}), \
             mock.patch.object(worker, "push_history"):
            worker.refresh()
        pane = next(p["payload"] for m, p in calls if p.get("slot") == "home-pane")
        self.assertEqual(pane["title"], "Dashboard")
        monitor = next(p["payload"] for m, p in calls if p.get("id") == "monitor")
        self.assertEqual(monitor["error"], "offline")
