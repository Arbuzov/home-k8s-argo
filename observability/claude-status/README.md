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

## ConfigMap — ГЕНЕРИРУЕТСЯ, НЕ РЕДАКТИРУЕТСЯ РУКАМИ

> **`configmap.yaml` — сгенерированный файл.** Раньше об этом предупреждал
> баннер в самом файле; по конвенции репо
> ([`CLAUDE.md`](../../CLAUDE.md)) манифесты комментариев не несут, поэтому
> предупреждение живёт здесь. Правка руками разъедется с источником и будет
> затёрта при следующей генерации.

Источник — `claude-observability/exporters/claude_status_exporter.py`.
Перегенерировать после **любого** изменения экспортера:

```powershell
cd C:\Users\info\Documents\git\claude-observability
.\scripts\render-configmap.ps1 `
  -OutFile ..\local-cluster-argo\observability\claude-status\configmap.yaml `
  -Namespace claude-status
```

Две вещи, которые делает скрипт и которые легко сломать вручную:

- **нормализует CRLF в LF** — с CRLF Python в контейнере падает на shebang;
- **держит YAML-обвязку в ASCII** — кириллический here-string ломается при
  ANSI-парсинге в PowerShell 5.1. Кириллица внутри самого Python (docstring,
  комментарии) при этом сохраняется, потому что приезжает из файла-источника,
  а не из литерала в скрипте.

Чтобы ConfigMap подхватился app-of-apps, в `observability/bootstrap.yaml`
маска `include` расширена на `*/configmap*.yaml` — см.
[`../README.md`](../README.md).

## Почему `project: default`

Как у `grafana` и `prometheus` в этой же папке. AppProject `observability` не
подходит по двум причинам сразу: в его `sourceRepos` нет `bjw-s-labs`, а в
`destinations` нет namespace `claude-status`.

## `persistence.tmp` — emptyDir на `/tmp`

Под запускается с `runAsUser: 65534` и read-only `$HOME`, а образ — голый
`python:3.13-slim`, который на старте делает `pip install prometheus-client`.
Без writable `/tmp` (плюс `HOME=/tmp` и `PYTHONUSERBASE=/tmp/.local` в env)
pip'у некуда писать и контейнер не стартует вообще.

## Связанное

- Дашборд: Grafana → AI Models → **Claude — Service Status** (`claude-status`)
- Правила и алерты: `claude-observability/deploy/rules/claude.rules.yaml`
- Исходники: `claude-observability`
