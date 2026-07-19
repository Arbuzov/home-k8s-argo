# otel-collector

Приёмник телеметрии Claude Code с воркстейшна. Мост между push-моделью
Claude Code и pull-моделью Prometheus.

```
Claude Code (Windows) --OTLP/http--> otel.whitediver.keenetic.link
                                        |
                                     collector
                                        |
                              prometheus exporter :8889
                                        |
                             job kubernetes-pods (по аннотациям)
```

Телеметрия Claude Code официальная, но в статусе beta — формат может меняться
между версиями.

## Метрики

```
claude_code_token_usage_tokens_total{type,model,query_source,skill_name,plugin_name,agent_name,mcp_server_name,effort,speed}
claude_code_cost_usage_USD_total{...}
claude_code_session_count_total{start_type}
claude_code_active_time_seconds_total{type}
claude_code_lines_of_code_count_total{type,model}
claude_code_commit_count_total
claude_code_pull_request_count_total
claude_code_code_edit_tool_decision_total{tool_name,decision,source,language}
```

На Max-подписке `cost_usage_USD` — фиктивные доллары, они не выставляются к
оплате. Ценность — как вес для атрибуции: сравнение «какой skill дороже»
корректно, абсолютная сумма бессмысленна.

## Настройка на стороне воркстейшна

Через `%USERPROFILE%\.claude\settings.json`, блок `env` (не переменные
окружения оболочки — те не переживают перезапуск терминала и зависят от того,
откуда запущен `claude`). Делается автоматически:

```powershell
cd C:\Users\info\Documents\git\claude-observability
.\windows\install.ps1 -OtelEndpoint https://otel.whitediver.keenetic.link
```

## Три грабли, каждая ломает всё молча

1. **Temporality.** По умолчанию OTel шлёт `delta`. Prometheus такие метрики
   дропает без ошибки и без warning'а — просто пустой дашборд.
   Лечится `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative`.

2. **Имена метрик.** Без Prometheus-naming приезжает `claude_code.token.usage`
   в dot-нотации. PromQL-запросы дашборда её не видят.

3. **`resource_to_telemetry_conversion`.** Выключено по умолчанию. Без него
   атрибуты ресурса не становятся лейблами, и вся панель «Attribution»
   схлопывается в одну колонку.

Плюс ограничение самого Claude Code: `OTEL_*` **не наследуются
субпроцессами** — хуки, MCP-серверы и Bash свою телеметрию не отдают. Это не
чинится настройкой.

## Кардинальность

`session.id` и `account.uuid` выключены в `install.ps1`
(`OTEL_METRICS_INCLUDE_SESSION_ID=false`). `session.id` создаёт новую серию на
каждую сессию Claude Code — самый жирный источник роста TSDB. Включать, только
если реально понадобится разбор по сессиям, и следить за
`prometheus_tsdb_head_series`.

`metric_expiration: 24h` в экспортере подчищает серии, которые перестали
обновляться.

## Связанное

- Дашборд: Grafana → AI Models → **Claude Code — Usage & Attribution** (`claude-code-usage`)
- Исходники и установщик: `claude-observability`
