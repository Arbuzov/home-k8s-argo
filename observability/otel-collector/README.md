# otel-collector

Приёмник телеметрии Claude Code с воркстейшна. Мост между push-моделью
Claude Code и pull-моделью Prometheus.

```
Claude Code (Windows) --OTLP/http--> dev.whitediver.keenetic.link/otel
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
.\windows\install.ps1 -OtelEndpoint https://dev.whitediver.keenetic.link/otel
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
обновляться: метрика исчезает, если её не обновляли сутки, иначе серии мёртвых
сессий висят вечно.

## Почему `project: default`

Не `observability`: в `sourceRepos` того AppProject'а нет
`open-telemetry.github.io`, так что дочернее `Application` было бы отклонено.
То же самое у `grafana`, `prometheus` и `claude-status` в этой же папке.

## Что выключено в чарте

`presets`: `logsCollection`, `kubernetesAttributes`, `hostMetrics`,
`kubeletMetrics` — все `false`. По умолчанию чарт тянет сбор логов и метрик
хоста; здесь нужен **только** приём OTLP от Claude Code с воркстейшна, всё
остальное — лишняя нагрузка на Pi и лишняя кардинальность. Приёмники
`jaeger`/`zipkin`/`prometheus` и пайплайны `traces`/`logs` занулены по той же
причине.

## `attributes/scrub` — `user.email` удаляется

При OAuth-логине Claude Code кладёт `user.email` в атрибуты. Пользователь один,
поэтому как лейбл он бесполезен, а PII в TSDB — лишний. Процессор
`attributes/scrub` удаляет ключ до экспорта; он стоит в пайплайне между
`memory_limiter` и `batch`, то есть до того, как что-либо уедет в Prometheus.

## Ingress — путь на общем `dev.*`, а не свой хост

Ingress живёт как **path** на общем `dev.whitediver.keenetic.link`, а не на
отдельном `otel.*`. Так устроен весь homelab, и у `dev.*` уже есть и рабочий
внешний роутинг, и TLS-сертификат. Отдельный хост потребовал бы:

- своего сертификата — его нигде не было, nginx отдавал дефолтный;
- записи на внешнем прокси — её нет, оттуда прилетал 404.

`tls`-блок здесь **не нужен**: nginx мержит все ingress'ы одного хоста в один
`server`, и сертификат `dev.*` приезжает от соседей (prometheus / grafana / …).

Rewrite: `/otel(/|$)(.*)` → `/$2`, то есть `/otel/v1/metrics` → `/v1/metrics`.
OTLP сам дописывает `/v1/metrics` к `OTEL_EXPORTER_OTLP_ENDPOINT`, поэтому на
воркстейшне в endpoint'е указывается только `.../otel`.

Basic-auth берётся из секрета `mcp-basic-auth`, и он **обязан лежать в этом же
namespace** — nginx не читает секреты из чужих. Без него локация отдаёт **503**,
а не 401, что при отладке читается как «сломался backend», а не «нет секрета».

## Связанное

- Дашборд: Grafana → AI Models → **Claude Code — Usage & Attribution** (`claude-code-usage`)
- Исходники и установщик: `claude-observability`
