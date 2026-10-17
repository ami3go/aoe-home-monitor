"""Home Monitor worker for Agent of Empires.

Reads the Home AI collector's dashboard JSON (per-host CPU/memory/disk readings, 15-minute history and
subscription usage) and pushes a dashboard home pane plus the state for the "monitor" page.
JSON-RPC 2.0 over newline-delimited stdio, standard library only.
"""

from __future__ import annotations

import itertools
import json
import os
import shlex
import subprocess
import sys
import threading
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RPC_TIMEOUT = 10.0
HTTP_TIMEOUT = 10.0
WARN, DANGER = 80.0, 92.0
DISK_WARN, DISK_DANGER = 85.0, 95.0

_ids = itertools.count(1)
_pending: dict[int, dict[str, Any]] = {}
_pending_lock = threading.Lock()
_out_lock = threading.Lock()
_wake = threading.Event()
_history = {"host": None, "range": "7d"}
RANGE_DAYS = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}
# Outside the plugin directory so updates keep it (fs.write).
LAYOUT_FILE = Path.home() / ".cache/home-ai/aoe-dashboard-layout.json"
PANEL_IDS = ("sessions", "servers", "subs")


def load_layout() -> dict[str, Any]:
    try:
        return json.loads(LAYOUT_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_layout(params: dict[str, Any]) -> None:
    """Dashboard panel order, hidden panels, wide panels and column count, shared by every viewer."""
    order = [p for p in params.get("order") or [] if p in PANEL_IDS]
    layout = {"order": order + [p for p in PANEL_IDS if p not in order],
              "hidden": [p for p in params.get("hidden") or [] if p in PANEL_IDS],
              "wide": [p for p in params.get("wide") or [] if p in PANEL_IDS],
              "cols": params.get("cols") if params.get("cols") in ("auto", "1", "2", "3") else "auto"}
    LAYOUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = LAYOUT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(layout))
    os.replace(tmp, LAYOUT_FILE)
    call("ui.state.set", {"slot": "page", "id": "dashboard", "payload": {"layout": layout}})


def log(msg: str) -> None:
    sys.stderr.write(f"[home-monitor] {msg}\n")
    sys.stderr.flush()


def send(obj: dict[str, Any]) -> None:
    with _out_lock:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def call(method: str, params: dict[str, Any]) -> Any:
    msg_id = next(_ids)
    slot: dict[str, Any] = {"event": threading.Event()}
    with _pending_lock:
        _pending[msg_id] = slot
    send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
    if not slot["event"].wait(RPC_TIMEOUT):
        with _pending_lock:
            _pending.pop(msg_id, None)
        raise TimeoutError(f"{method} timed out")
    if "error" in slot:
        raise RuntimeError(f"{method}: {slot['error']}")
    return slot.get("result")


def reader() -> None:
    for raw in sys.stdin:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        if "method" not in msg:
            with _pending_lock:
                slot = _pending.pop(msg.get("id"), None)
            if slot is not None:
                slot.update({"error": msg["error"]} if "error" in msg else {"result": msg.get("result")})
                slot["event"].set()
            continue
        method = str(msg.get("method", ""))
        tail = method.rsplit(".", 1)[-1]
        if tail == "layout_save":
            params = msg.get("params") or {}
            params = params.get("params", params) if isinstance(params, dict) else {}
            threading.Thread(target=lambda: save_layout(params), daemon=True).start()
        if tail == "power_switch":
            params = msg.get("params") or {}
            params = params.get("params", params) if isinstance(params, dict) else {}
            threading.Thread(target=lambda: power_switch(str(params.get("action") or "")), daemon=True).start()
        if tail == "wake":
            params = msg.get("params") or {}
            params = params.get("params", params) if isinstance(params, dict) else {}
            threading.Thread(target=lambda: wake_host(str(params.get("host") or "")), daemon=True).start()
        if tail == "history_select":
            params = msg.get("params") or {}
            params = params.get("params", params) if isinstance(params, dict) else {}
            if isinstance(params.get("host"), str):
                _history["host"] = params["host"]
            if params.get("range") in RANGE_DAYS:
                _history["range"] = params["range"]
            threading.Thread(target=push_history, daemon=True).start()
        if tail in {"refresh", "changed"}:
            _wake.set()
        if msg.get("id") is not None:
            if tail in {"refresh", "status"}:
                send({"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": True}})
            else:
                send({"jsonrpc": "2.0", "id": msg["id"],
                      "error": {"code": -32601, "message": f"unknown method {method}"}})
    os._exit(0)


def setting(key: str, default: Any) -> Any:
    try:
        value = (call("config.get", {"key": key}) or {}).get("value")
    except Exception as exc:  # noqa: BLE001
        log(f"config.get {key}: {exc}")
        return default
    return default if value is None else value


def fetch(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(urllib.request.Request(url), timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def pct(used: float | None, total: float | None) -> float | None:
    return round(100.0 * used / total, 1) if used is not None and total else None


def tone(value: float | None, warn: float = WARN, danger: float = DANGER) -> str:
    if value is None:
        return "neutral"
    return "danger" if value >= danger else "warn" if value >= warn else "success"


def summarize(host: dict[str, Any]) -> dict[str, Any]:
    """Current numbers and history series for one host, as plain data for the pane and the page."""
    reading = host.get("reading") or {}
    mem = pct((reading.get("memTotal") or 0) - (reading.get("memAvailable") or 0), reading.get("memTotal"))
    disks = [pct(d.get("used"), d.get("total")) for d in reading.get("disks") or []]
    disk = max((d for d in disks if d is not None), default=None)
    history = host.get("history") or []
    return {
        "id": host.get("id"), "name": host.get("name") or host.get("id"),
        "up": host.get("status") == "ok" and not host.get("error"),
        "error": host.get("error"), "checkedAt": host.get("checkedAt"),
        "os": reading.get("os"), "cores": reading.get("cores"),
        "cpu": reading.get("cpu"), "mem": mem, "disk": disk, "load": reading.get("load"),
        "memTotal": reading.get("memTotal"),
        "memUsed": (reading.get("memTotal") - reading.get("memAvailable"))
        if reading.get("memTotal") and reading.get("memAvailable") is not None else None,
        "uptime": reading.get("uptime"), "rates": host.get("rates") or {},
        "cpuSeries": [round(h["cpuAvg"], 1) if h.get("cpuAvg") is not None else 0 for h in history],
        "memSeries": [round(h["memAvg"], 1) if h.get("memAvg") is not None else 0 for h in history],
        "diskSeries": [round(h["diskMax"], 1) if h.get("diskMax") is not None else 0 for h in history],
        "disks": [{"mount": d.get("mount") or "?", "total": d.get("total"), "used": d.get("used"),
                   "pct": pct(d.get("used"), d.get("total"))} for d in reading.get("disks") or []],
        "rx": (host.get("rates") or {}).get("rx"), "tx": (host.get("rates") or {}).get("tx"),
        "rxSeries": [round(h.get("rxRate") or 0) for h in history],
        "txSeries": [round(h.get("txRate") or 0) for h in history],
    }


def human_bytes(value: float | None, rate: bool = False) -> str:
    if value is None:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    i = 0
    while value >= 1000 and i < len(units) - 1:
        value /= 1000.0
        i += 1
    text = f"{value:.0f}" if value >= 100 or i == 0 else f"{value:.1f}"
    return f"{text} {units[i]}{'/s' if rate else ''}"


def subscriptions(hosts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per provider plan, de-duplicated across hosts; the same account is never summed."""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for host in hosts:
        for provider in host.get("providers") or []:
            usage = provider.get("usage") or {}
            if usage.get("status") != "fresh":
                continue
            key = (provider.get("toolId") or "?", usage.get("plan") or "")
            entry = seen.setdefault(key, {"tool": provider.get("label") or key[0], "plan": key[1],
                                          "windows": usage.get("windows") or [], "hosts": []})
            entry["hosts"].append(host.get("name") or host.get("id"))
    return list(seen.values())


TOOL_NAME = {"codex": "Codex", "claude": "Claude", "grok": "Grok", "antigravity": "Antigravity",
             "kimi": "Kimi", "opencode": "OpenCode"}
STATUS_KO = {"logged_in": "로그인됨 · 사용량 미확인", "expired": "토큰 만료", "failed": "조회 실패", "unknown": "확인 불가"}


def fetch_usage(command: str) -> dict[str, Any] | None:
    """Optional per-host subscription usage from a command printing {"hosts": [{"host", "providers": [...]}]}."""
    if not command.strip():
        return None
    try:
        done = subprocess.run([os.path.expanduser(a) for a in shlex.split(command)], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=120)
        return json.loads(done.stdout)
    except Exception as exc:  # noqa: BLE001
        log(f"usage command: {exc}")
        return None


_wake_notes: dict[str, str] = {}


def wake_host(host: str) -> None:
    """Run the configured wake command for an allow-listed host (dashboard "켜기" button)."""
    allowed = {h.strip() for h in setting("wake_hosts", []) or [] if isinstance(h, str)}
    template = str(setting("wake_command", "") or "")
    if host not in allowed or "{host}" not in template:
        log(f"wake refused for {host!r}")
        return
    _wake_notes[host] = "켜는 중… " + datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")
    _wake.set()
    try:
        args = [os.path.expanduser(a).replace("{host}", host) for a in shlex.split(template)]
        done = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        _wake_notes[host] = (done.stdout or done.stderr).strip().splitlines()[-1][:120] if (done.stdout or done.stderr) \
            else f"종료 코드 {done.returncode}"
    except Exception as exc:  # noqa: BLE001
        _wake_notes[host] = f"실패: {exc}"[:120]
    _wake.set()


_power_note = {"text": ""}


def fetch_power(command: str) -> dict[str, Any] | None:
    """Optional smart power strip status: a command printing JSON (switch, power_w, today_wh, month_wh, history)."""
    if not command.strip():
        return None
    try:
        done = subprocess.run([os.path.expanduser(a) for a in shlex.split(command)], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=40)
        data = json.loads(done.stdout) if done.stdout.strip() else {"error": (done.stderr or "no output").strip()[-200:]}
    except Exception as exc:  # noqa: BLE001
        data = {"error": str(exc)[:200]}
    data["note"] = _power_note["text"]
    return data


def power_switch(action: str) -> None:
    template = str(setting("power_switch_command", "") or "")
    if action not in ("on", "off") or "{action}" not in template:
        log(f"power switch refused: {action!r}")
        return
    _power_note["text"] = ("켜는 중… " if action == "on" else "끄는 중… ") + datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")
    _wake.set()
    try:
        args = [os.path.expanduser(a).replace("{action}", action) for a in shlex.split(template)]
        done = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        _power_note["text"] = ((done.stdout or done.stderr).strip().splitlines() or [""])[-1][:120]
    except Exception as exc:  # noqa: BLE001
        _power_note["text"] = f"실패: {exc}"[:120]
    _wake.set()


def fetch_vpn(command: str) -> dict[str, Any] | None:
    """Optional VPN status from a command printing `state: X (name)`, `started:`, `expires:`, `route a -> if` lines."""
    if not command.strip():
        return None
    try:
        done = subprocess.run([os.path.expanduser(a) for a in shlex.split(command)], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=30)
    except Exception as exc:  # noqa: BLE001
        return {"state": "UNKNOWN", "error": str(exc)[:200]}
    info: dict[str, Any] = {"routes": [], "connected": done.returncode == 0}
    for line in done.stdout.splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key == "state":
            state, _, name = value.partition(" ")
            info["state"], info["name"] = state, name.strip("()")
        elif key in ("started", "expires"):
            info[key] = value.replace(" (auto-disconnect)", "")
        elif line.startswith("route "):
            info["routes"].append(line[len("route "):])
        elif line.startswith("home "):
            info["local"] = line[len("home "):]
    return info


def usage_blocks(usage: dict[str, Any]) -> list[dict[str, Any]]:
    """One section per host listing every logged-in subscription, its account and its windows."""
    blocks = []
    for host in usage.get("hosts") or []:
        rows = []
        for p in host.get("providers") or []:
            windows = p.get("windows") or []
            top = max((w["used"] for w in windows if isinstance(w.get("used"), (int, float))), default=None)
            detail = " · ".join(f"{w.get('label')} {w.get('used'):.0f}%" for w in windows
                                if isinstance(w.get("used"), (int, float)))
            rows.append({"kind": "row", "prefix": TOOL_NAME.get(p.get("tool"), p.get("tool")),
                         "label": p.get("plan") or "-",
                         "sublabel": " · ".join(filter(None, [p.get("account"), detail])),
                         "value": f"{top:.0f}%" if top is not None else STATUS_KO.get(p.get("status"), p.get("status")),
                         "value_tone": tone(top, 70, 90) if top is not None else
                         ("danger" if p.get("status") in ("expired", "failed") else "neutral"),
                         "tooltip": p.get("reason") or None})
        if host.get("error"):
            rows.append({"kind": "note", "tone": "danger", "text": host["error"]})
        if not rows:
            rows.append({"kind": "note", "text": "로그인된 구독 없음"})
        blocks.append({"kind": "section", "title": f"구독 · {host.get('host')}", "boxed": True, "collapsible": True,
                       "children": rows})
    return blocks


def host_block(s: dict[str, Any]) -> dict[str, Any]:
    if not s["up"]:
        return {"kind": "section", "title": s["name"], "boxed": True, "value": "연결 끊김", "value_tone": "danger",
                "children": [{"kind": "note", "tone": "danger", "text": s["error"] or "측정값 없음"}]}
    worst = max((s["cpu"] or 0, s["mem"] or 0), default=0)
    head_tone = "danger" if tone(s["disk"], DISK_WARN, DISK_DANGER) == "danger" or tone(worst) == "danger" \
        else "warn" if tone(s["disk"], DISK_WARN, DISK_DANGER) == "warn" or tone(worst) == "warn" else "success"
    fmt = lambda v: "-" if v is None else f"{v:.0f}%"  # noqa: E731
    bands = [{"at": WARN, "tone": "warn"}, {"at": DANGER, "tone": "danger"}]
    children: list[dict[str, Any]] = [
        {"kind": "sparkline", "values": s["cpuSeries"][-96:] or [0], "max": 100, "bands": bands,
         "caption": "CPU 24시간"},
        {"kind": "sparkline", "values": s["memSeries"][-96:] or [0], "max": 100, "bands": bands,
         "caption": "메모리 24시간"},
        {"kind": "row", "prefix": "네트워크", "label": f"↓ {human_bytes(s['rx'], True)}  ↑ {human_bytes(s['tx'], True)}",
         "mono": True},
        {"kind": "sparkline", "values": s["rxSeries"][-96:] or [0], "caption": "받기 24시간"},
        {"kind": "sparkline", "values": s["txSeries"][-96:] or [0], "caption": "보내기 24시간"},
    ]
    for d in s["disks"]:
        children.append({"kind": "row", "prefix": "디스크", "label": d["mount"],
                         "sublabel": f"{human_bytes(d['used'])} / {human_bytes(d['total'])}",
                         "value": fmt(d["pct"]), "value_tone": tone(d["pct"], DISK_WARN, DISK_DANGER), "mono": True})
        if d["pct"] is not None:
            children.append({"kind": "bar", "segments": [
                {"value": d["pct"], "tone": tone(d["pct"], DISK_WARN, DISK_DANGER)},
                {"value": max(0.0, 100 - d["pct"]), "tone": "neutral"}]})
    return {"kind": "section", "title": s["name"], "boxed": True, "collapsible": True,
            "collapsed": head_tone == "success",
            "value": f"CPU {fmt(s['cpu'])} · 메모리 {fmt(s['mem'])} · 디스크 {fmt(s['disk'])} · ↓{human_bytes(s['rx'], True)}",
            "value_tone": head_tone, "children": children}


DASHBOARD_LINK = {"kind": "action", "label": "대시보드 열기", "href": "/plugins/dev.hyunwook.home-monitor/dashboard",
                  "icon": "layout-dashboard", "variant": "primary"}
HOME_PANE = {"title": "대시보드", "icon": "layout-dashboard", "blocks": [DASHBOARD_LINK]}


def build(data: dict[str, Any], usage: dict[str, Any] | None = None,
          vpn: dict[str, Any] | None = None, power: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Two home panes and two pages: server health, and subscription usage."""
    hosts = [summarize(h) for h in data.get("hosts") or []]
    subs = subscriptions(data.get("hosts") or [])
    up = sum(1 for h in hosts if h["up"])
    problems = [h["name"] for h in hosts if not h["up"]] + [
        f"{h['name']} {d['mount']} {d['pct']:.0f}%" for h in hosts if h["up"] for d in h["disks"]
        if (d["pct"] or 0) >= DISK_WARN] + [
        f"{h['name']} 메모리 {h['mem']:.0f}%" for h in hosts if h["up"] and (h["mem"] or 0) >= DANGER]
    now = datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")
    blocks: list[dict[str, Any]] = [DASHBOARD_LINK]
    if problems:
        blocks.append({"kind": "callout", "tone": "danger" if up < len(hosts) else "warn",
                       "title": "확인할 서버가 있습니다", "detail": " · ".join(problems)})
    blocks.extend(host_block(h) for h in hosts)
    blocks.append({"kind": "action", "label": "지금 새로고침", "method": "refresh", "icon": "refresh-cw"})
    servers = {"title": "서버 상태", "icon": "activity", "blocks": blocks,
               "footer": {"text": f"갱신 {now}", "value": f"서버 {up}/{len(hosts)}",
                          "tone": "success" if up == len(hosts) else "danger", "icon": "refresh-cw"}}
    if usage:
        sub_blocks = [DASHBOARD_LINK] + usage_blocks(usage)
        accounts = sum(len(h.get("providers") or []) for h in usage.get("hosts") or [])
        hot = [f"{h.get('host')} {TOOL_NAME.get(p.get('tool'), p.get('tool'))} {w['used']:.0f}%"
               for h in usage.get("hosts") or [] for p in h.get("providers") or [] for w in p.get("windows") or []
               if isinstance(w.get("used"), (int, float)) and w["used"] >= 80]
        if hot:
            sub_blocks.insert(1, {"kind": "callout", "tone": "warn", "title": "한도에 가까운 구독",
                                  "detail": " · ".join(hot)})
        footer_value = f"계정 {accounts}개"
    else:
        rows = []
        for sub in subs:
            windows = sub["windows"]
            top = max((w.get("usedPercent") or 0 for w in windows), default=None)
            rows.append({"kind": "row", "prefix": sub["tool"], "label": sub["plan"] or "-",
                         "sublabel": " · ".join(f"{w.get('label')} {w.get('usedPercent', '?')}%" for w in windows),
                         "value": "-" if top is None else f"{top:.0f}%", "value_tone": tone(top, 70, 90)})
        sub_blocks = [{"kind": "section", "title": "구독 사용량", "boxed": True, "children": rows or
                       [{"kind": "note", "text": "확인된 구독이 없습니다."}]}]
        footer_value = f"구독 {len(subs)}개"
    sub_blocks.append({"kind": "action", "label": "지금 새로고침", "method": "refresh", "icon": "refresh-cw"})
    subscriptions_pane = {"title": "구독 상태", "icon": "gauge", "blocks": sub_blocks,
                          "footer": {"text": f"갱신 {now}", "value": footer_value, "tone": "neutral"}}
    return {
        "servers": servers, "subscriptions": subscriptions_pane,
        "monitor": {"observedAt": data.get("observedAt"), "updated": now, "hosts": hosts, "vpn": vpn,
                    "power": power,
                    "wakeable": [h for h in setting("wake_hosts", []) or [] if isinstance(h, str)],
                    "wakeNotes": dict(_wake_notes)},
        "subscriptions_page": {"updated": now, "subscriptions": subs, "usage": (usage or {}).get("hosts")},
    }


_known_hosts: list[dict[str, str]] = []


def collector_base(url: str) -> str:
    from urllib.parse import urlparse
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}"


def history_payload(base: str, hosts: list[dict[str, str]], host: str, rng: str) -> dict[str, Any]:
    """Selected host and range: chart points, busiest hours and the weekday x hour profile."""
    from urllib.parse import quote
    hist = fetch(f"{base}/api/monitor/history?host={quote(host)}&range={rng}")
    prof = fetch(f"{base}/api/monitor/profile?host={quote(host)}&days={RANGE_DAYS[rng]}")
    keep = ("at", "up", "cpuAvg", "cpuMax", "memAvg", "memMax", "diskMax", "rxRate", "txRate", "rxBytes", "txBytes")
    r1 = lambda v: round(v, 1) if isinstance(v, (int, float)) else v  # noqa: E731
    points = [{k: r1(p.get(k)) for k in keep} for p in hist.get("points") or []]
    rows = [{k: r1(p.get(k)) for k in keep} for p in hist.get("rows") or []]
    cells = [{k: r1(c.get(k)) for k in ("dow", "hour", "cpuAvg", "cpuMax", "memAvg", "rxRate", "txRate", "samples")}
             for c in prof.get("cells") or []]
    return {"hosts": hosts, "host": host, "range": rng, "rowSeconds": hist.get("rowSeconds"),
            "firstSampleAt": hist.get("firstSampleAt"), "points": points, "rows": rows[:120], "cells": cells,
            "peaks": [{k: r1(p.get(k)) for k in keep} for p in prof.get("peaks") or []],
            "busiestNet": [{k: r1(p.get(k)) for k in keep} for p in prof.get("busiestNet") or []],
            "updated": datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")}


def push_history() -> None:
    try:
        base = collector_base(setting("source_url", "http://127.0.0.1:4317/api/dashboard?range=24h"))
        if not _known_hosts:
            return
        host = _history["host"] if any(h["id"] == _history["host"] for h in _known_hosts) else _known_hosts[0]["id"]
        _history["host"] = host
        payload = history_payload(base, _known_hosts, host, _history["range"])
    except Exception as exc:  # noqa: BLE001
        payload = {"error": str(exc), "hosts": _known_hosts, "host": _history["host"], "range": _history["range"]}
    try:
        call("ui.state.set", {"slot": "page", "id": "history", "payload": payload})
    except Exception as exc:  # noqa: BLE001
        log(f"history push: {exc}")


def refresh() -> int:
    url = setting("source_url", "http://127.0.0.1:4317/api/dashboard?range=24h")
    try:
        data = fetch(url)
        _known_hosts[:] = [{"id": h.get("id"), "name": h.get("name") or h.get("id")} for h in data.get("hosts") or []
                           if h.get("id")]
        out = build(data, fetch_usage(str(setting("usage_command", "") or "")),
                    fetch_vpn(str(setting("vpn_command", "") or "")),
                    fetch_power(str(setting("power_command", "") or "")))
    except Exception as exc:  # noqa: BLE001
        failed = {"kind": "callout", "tone": "danger", "title": "수집기에 연결할 수 없습니다", "detail": f"{url}: {exc}"}
        out = {"servers": {"title": "서버 상태", "icon": "activity", "blocks": [failed],
                           "footer": {"text": "갱신 실패", "tone": "danger"}},
               "subscriptions": {"title": "구독 상태", "icon": "gauge", "blocks": [failed],
                                 "footer": {"text": "갱신 실패", "tone": "danger"}},
               "monitor": {"error": str(exc), "hosts": []},
               "subscriptions_page": {"error": str(exc), "subscriptions": [], "usage": None}}
    # The home screen only links to the dashboard page; the panes' content lives in the pages.
    call("ui.state.set", {"slot": "home-pane", "id": "servers", "payload": HOME_PANE})
    call("ui.state.set", {"slot": "page", "id": "dashboard", "payload": {"layout": load_layout()}})
    call("ui.state.set", {"slot": "page", "id": "monitor", "payload": out["monitor"]})
    call("ui.state.set", {"slot": "page", "id": "subscriptions", "payload": out["subscriptions_page"]})
    push_history()
    return max(15, int(setting("refresh_secs", 60)))


def main() -> None:
    threading.Thread(target=reader, daemon=True).start()
    while True:
        try:
            delay = refresh()
        except Exception as exc:  # noqa: BLE001 - keep the worker alive
            log(f"refresh failed: {exc}")
            delay = 30
        _wake.wait(delay)
        _wake.clear()


if __name__ == "__main__":
    main()
