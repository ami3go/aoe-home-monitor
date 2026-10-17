# aoe-home-monitor

[Agent of Empires](https://github.com/agent-of-empires/agent-of-empires)(AoE) 대시보드에서 집 서버 여러 대의 상태를 한 번에 보려고 만든 플러그인입니다. 에이전트 세션은 AoE와 [aoe-fleet](https://github.com/hyunwook711/aoe-fleet)으로 보고, 서버의 CPU·메모리·디스크와 구독 사용량은 이 플러그인으로 봅니다.

An AoE plugin that shows server health (CPU, memory, disk, 24 h history) and subscription usage for a small home fleet: a dashboard home pane plus a full page. UI text is Korean.

## 화면

- 페이지 "대시보드": 세션(aoe-fleet가 있으면 모든 장비), 서버 상태, 구독 상태 패널. 창이 넓으면 나란히, 좁으면 한 줄로 쌓이고 패널마다 따로 스크롤. 패널 제목을 끌어 순서를 바꾸고, "넓게"로 두 칸을 차지하게 하고, 보이기/숨기기와 열 수(자동·1·2·3)를 고를 수 있음. 배치는 `~/.cache/home-ai/aoe-dashboard-layout.json`에 저장되어 모든 브라우저가 같이 씀. 세션을 누르면 그 세션이 열림.
- 홈 패널 "대시보드": 대시보드 페이지로 가는 버튼 하나.
- 페이지 "서버 모니터링"과 같은 내용의 (예전) 홈 패널 "서버 상태": 서버마다 CPU·메모리 현재값과 24시간 그래프, 네트워크 받기·보내기 속도와 그래프, 마운트된 디스크별 사용량. 연결이 끊기거나 디스크(마운트별) 85%·메모리 92%를 넘으면 맨 위에 경고.
- 홈 패널 "구독 상태": 서버마다 로그인된 계정과 사용량(`usage_command`를 넣은 경우), 80%를 넘은 한도 경고. 같은 계정을 여러 서버가 써도 사용량을 더하지 않음.
- 페이지 "서버 모니터링": 서버 카드 격자(네트워크 그래프, 디스크 막대).
- 페이지 "서버 기록": 서버와 기간(24시간·7일·30일·90일)을 골라 CPU·메모리·네트워크·디스크 그래프, 요일 × 시간대 히트맵(언제 많이 쓰는지), CPU·네트워크가 가장 많았던 시간, 시간별·일별 요약. 수집기의 `/api/monitor/history`와 `/api/monitor/profile`을 씁니다.
- 페이지 "구독 사용량": 서버·서비스·요금제·계정·사용량 표.

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
  "rates": {"rx": 52000, "tx": 1600000},
  "history": [{"cpuAvg": 10.1, "memAvg": 50.2, "diskMax": 12.0, "rxRate": 52000, "txRate": 1600000}],
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
| `vpn_command` | (비움) | VPN 상태를 출력하는 명령(선택). `state: CONNECTED (이름)`, `started:`, `expires:`, `route IP -> 인터페이스` 줄을 읽어 대시보드 서버 상태 맨 위에 보여줍니다 |
| `wake_hosts` / `wake_command` | (비움) | 꺼진 서버 칸에 "켜기 (WOL)" 버튼을 보일 서버 id 목록과, `{host}` 자리에 id가 들어가는 켜기 명령(예: Wake-on-LAN 도구). 목록에 없는 서버는 거부합니다 |
| `refresh_secs` | 60 | 갱신 주기(초) |

## 테스트

```bash
python3 -m unittest discover -s tests -v
```

## License

MIT
