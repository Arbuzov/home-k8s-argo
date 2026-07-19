# claude-status

Экспортер статуса сервисов Anthropic в Prometheus. Порт 9102, скрейпится
существующим job'ом `kubernetes-pods` по аннотациям `prometheus.io/*`.

Источник данных: `https://status.anthropic.com/api/v2/summary.json` — публичный
Atlassian Statuspage, без авторизации.

## Зачем в кластере

Парная часть — `claude_usage_exporter` — живёт на воркстейшне, потому что
читает OAuth-токен из профиля пользователя. Но воркстейшн спит, а статус
сервиса нужно писать 24/7: пробел в графике доступности как раз в момент
аварии обесценивает график.

## Метрики

```
claude_status_component_up{component,status}        # info, всегда 1
claude_status_component_health{component}           # 1 / 0.66 / 0.5 / 0.33 / 0
claude_status_indicator_up{indicator,description}
claude_status_active_incidents{impact}
claude_status_scrape_success
```

`component_health` — числовой маппинг статуса, нужен для state timeline и
`avg_over_time` (расчёт доступности). `component_up` — тот же статус строкой
в лейбле, удобен для таблиц.

## Почему не json_exporter

Statuspage отдаёт статус строкой (`operational`, `degraded_performance`, ...).
Маппинг строки в число в json_exporter требует jsonpath-акробатики с
filter-выражениями; в Python это один dict. Экспортер и так пришлось бы писать
ради `claude_usage_exporter` — второй использует тот же подход.

## Почему ConfigMap, а не свой образ

130 строк Python не стоят registry, CI и тега. Цена: `pip install` при старте
пода (~5с) и зависимость от PyPI в момент рестарта. Если начнёт мешать —
собрать образ из `claude-observability/Dockerfile` в ghcr.io и заменить
`image` + `command`.

## ConfigMap

**Не редактировать `configmap.yaml` руками** — разъедется с источником.
Он генерируется из `claude-observability/exporters/claude_status_exporter.py`:

```powershell
cd C:\Users\info\Documents\git\claude-observability
.\scripts\render-configmap.ps1 `
  -OutFile ..\local-cluster-argo\observability\claude-status\configmap.yaml `
  -Namespace claude-status
```

Скрипт нормализует CRLF в LF: с CRLF Python в контейнере падает на shebang.

Чтобы ConfigMap подхватился app-of-apps, в `observability/bootstrap.yaml`
маска `include` расширена на `*/configmap*.yaml`.

## Связанное

- Дашборд: Grafana → AI Models → **Claude — Service Status** (`claude-status`)
- Правила и алерты: `claude-observability/deploy/rules/claude.rules.yaml`
- Исходники: `claude-observability`
