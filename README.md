# aoe-home-monitor

[Agent of Empires](https://github.com/agent-of-empires/agent-of-empires)(AoE) 대시보드에서 집 서버 여러 대의 상태를 한 번에 보려고 만든 플러그인입니다. 에이전트 세션은 AoE와 [aoe-fleet](https://github.com/hyunwook711/aoe-fleet)으로 보고, 서버의 CPU·메모리·디스크와 구독 사용량은 이 플러그인으로 봅니다.

An AoE plugin that shows server health (CPU, memory, disk, 24 h history) and subscription usage for a small home fleet: a dashboard home pane plus a full page. UI text is Korean.

## 화면

- 홈 패널 "서버 상태": 서버마다 CPU·메모리·디스크 현재값과 24시간 그래프. 연결이 끊기거나 디스크 85%·메모리 92%를 넘으면 맨 위에 경고. 구독 사용량(같은 계정을 여러 서버가 써도 한 줄, 사용량은 더하지 않음).
- 페이지 "서버 모니터링": 서버 카드 격자와 구독 표.

## 필요한 것

- AoE 1.20 이상 (`page` 슬롯, API 16)
- Python 3.9 이상 (표준 라이브러리만 사용)
- 서버 측정값을 JSON으로 주는 수집기. 이 플러그인은 측정하지 않고 읽기만 합니다.

수집기 JSON 형식 (`hosts` 배열):

```json
{"observedAt": "…", "hosts": [{
  "id": "m1", "name": "m1", "status": "ok", "error": null,
  "reading": {"os": "Darwin", "cores": 10, "cpu": 12.5, "memTotal": 17179869184, "memAvailable": 8589934592,
              "disks": [{"mount": "/", "total": 494000000000, "used": 59000000000}]},
  "history": [{"cpuAvg": 10.1, "memAvg": 50.2, "diskMax": 12.0}],
  "providers": [{"toolId": "codex", "label": "Codex",
                 "usage": {"status": "fresh", "plan": "Business", "windows": [{"label": "Weekly", "usedPercent": 15}]}}]
}]}
```

`history`는 오래된 것부터, 15분 간격 기준 최근 96개를 그립니다. `providers`는 없어도 됩니다.

## 설치

```bash
aoe plugin install gh:hyunwook711/aoe-home-monitor
aoe serve --restart
```

요청하는 권한: `runtime.worker`, `net`(수집기 주소 조회), `session.read`(페이지가 브리지로 자기 상태를 읽음), `process.spawn`(`usage_command` 실행).

## 설정

| 키 | 기본값 | 설명 |
|---|---|---|
| `source_url` | `http://127.0.0.1:4317/api/dashboard?range=24h` | 수집기 JSON 주소 |
| `usage_command` | (비움) | 서버별 구독 사용량 JSON을 출력하는 명령(선택). `{"hosts": [{"host", "providers": [{"tool", "plan", "account", "status", "windows": [{"label", "used"}]}]}]}`. 넣으면 서버마다 로그인된 계정과 사용량을 보여줍니다 |
| `refresh_secs` | 60 | 갱신 주기(초) |

## 테스트

```bash
python3 -m unittest discover -s tests -v
```

## License

MIT
