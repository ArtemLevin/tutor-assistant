# Hardware soak и физическая проверка записи

GitHub Actions подтверждает source-level contracts, но не заменяет реальный Windows
WASAPI/device lifecycle. Stable release разрешён только после физической проверки
на машине преподавателя и review sanitized evidence.

## Сбор и проверка evidence

```powershell
tutor-assistant --config config\app.yaml hardware-soak
tutor-assistant --config config\app.yaml hardware-soak --evidence .\observations.json --output .\hardware-soak.json --strict
```

Автоматическая инвентаризация читает только session manifests и sizes, агрегирует длительность,
chunks/dropped blocks и irreversible hashed device fingerprints. Она не копирует записи,
имена учеников, темы или транскрипты в evidence report.

## Обязательные сценарии

| Scenario key | Проверка |
| --- | --- |
| `long_recording` | Непрерывная запись обеих дорожек не менее двух часов |
| `repeated_lifecycle` | Минимум 20 повторов start → recording → stop |
| `microphone_disconnect_reconnect` | Отключение и повторное подключение микрофона |
| `playback_endpoint_change` | Изменение Windows playback endpoint |
| `forced_process_termination` | Не менее пяти kill/restart/recovery случаев |
| `single_channel_degradation` | Корректное восстановление microphone-only и system-only |
| `parallel_review` | Запись ученика A при просмотре материалов ученика B |
| `background_workload` | Backup/maintenance уступают критическим recording transitions |

## Формат evidence

```json
{
  "metrics": {
    "cumulative_recording_seconds": 72000,
    "longest_recording_seconds": 7200,
    "start_stop_cycles": 20,
    "forced_recovery_cases": 5,
    "device_disruption_cases": 5,
    "lost_recoverable_recordings": 0,
    "unexplained_unhandled_crashes": 0
  },
  "scenarios": {
    "long_recording": true,
    "repeated_lifecycle": true,
    "microphone_disconnect_reconnect": true,
    "playback_endpoint_change": true,
    "forced_process_termination": true,
    "single_channel_degradation": true,
    "parallel_review": true,
    "background_workload": true
  }
}
```

Unknown fields are discarded. A missing scenario, lost recoverable recording or unexplained
unhandled crash blocks acceptance even when every unit test passes. Physical results must
never be fabricated; do not mark hardware conditions complete before executing them.
