# Diseño de arquitectura — Lab Dashboard (angel1 + angel2)

**Estado:** propuesta, sin implementar. Basado en los dos informes técnicos (angel1, angel2) y en la inspección del código real del repo (`backend/app/*`, `frontend/static/js/app.js`).

---

## 0. Diagnóstico del estado actual (hechos, no supuestos)

Verificado en el código:

- `lab-dashboard` corre **solo en angel1** (contenedor Docker, puerto 8600/8000). No hay backend ni API en angel2.
- `backend/servers.json` define 3 hosts (`angel`, `angel1`, `angel2`) y el propio `lab-dashboard` se conecta **por SSH a sí mismo** como un host más.
- Pipeline por host: `commands.py` (comandos shell) → `ssh_client.py` (paramiko, pool de conexiones) → `parsers.py` (texto → dict) → `collector.py` (orquesta) → `monitor.py` (estado en memoria, históricos, ensambla `snapshot()`) → `main.py` (expone por WS `/ws` y REST `/api/status`).
- `autonomy.py` y `confidence.py` son módulos separados que consumen la salida de `parse_battery` — ya existe la separación "valor físico" vs. "metadato de calidad" (`power.reliability`, `power.validated`, `s["confidence"]`, `s["telemetry_health"]`), aplicada solo a batería/telemetría general, no a todas las métricas derivadas.
- El contrato de salida (`models.py::StatusResponse`) usa `servers: list[dict]` **sin tipar** el contenido interno — coincide con la Recomendación 2 del informe de angel1 (falta de modelos Pydantic anidados).
- Revisé `frontend/static/js/app.js`: **no encontré recálculo de física** (no hay `voltage * current`, no hay reconstrucción de `power_now`/`energy_now`/`autonomy_seconds`). Lo que hace es: `.toFixed()`, formateo de fechas, bucketing de segundos a intervalos de 5 min para mostrar (`Math.round(sec / 300) * 300`), y geometría de canvas para gráficos. **Esto ya cumple la filosofía pedida** — no hay que "corregir" el frontend, hay que no romper esta propiedad al extender.
- `power.power_now_raw_w` (valor sin suavizar) existe en el JSON del backend pero no está cableado en la UI — disponible, no usado.
- Las métricas ricas de `battery-audit` (wifi, cstates, brightness, ssh_local_latency_ms, peer_ping_ms) **no llegan al dashboard**: viven solo en CSV/reporte offline, en ambos servidores.
- `GET /api/autonomy/metrics` expone el estado interno del algoritmo pero es un `dict` suelto sin modelo — mismo problema de tipado.

**Diferencia física clave entre servidores** (no es un bug, es hardware distinto — debe reflejarse en la UI, no ocultarse ni promediarse):

| Campo | angel1 | angel2 |
|---|---|---|
| `current_now`, `charge_now`, `charge_full` | Disponibles | **No disponibles** (NA nativo) |
| `power_now_w` | Derivado (`current × voltage`) | **Medido nativo** por el EC |
| `energy_now_wh` | Derivado (`charge × voltage`) | Derivado, pero matemáticamente redundante con `capacity_pct` (no aporta resolución nueva) |
| Resolución de `power_now` | Suavizado (mediana+EMA), refresco EC ~10-14s | Escalón nativo ~7-8mW, refresco EC ~2-8s |
| `bat_cycle_count` | — | Siempre 0 (firmware no lo implementa) |
| Potencia en carga | Variable | Constante exacta 11.40W (rating del cargador, no medición) |

Esto confirma la recomendación 4 del informe angel1: cada métrica derivada necesita ir acompañada de su procedencia (medido/derivado/estimado) y confiabilidad — especialmente al mezclar hosts con hardware distinto en la misma UI.

---

## 1. Decisión de arquitectura: qué cambia y qué NO

### No cambia (ya está bien, según ambos informes)

- **SSH como mecanismo de recolección en esta fase.** El informe de angel2 recomienda una API REST local como evolución futura, pero eso implica desplegar un servicio nuevo en angel2 (fuera del alcance de "no inventar infraestructura no verificada"). **Se documenta como Fase 2 opcional** (sección 6), no como parte de este rediseño.
- **Patrón WebSocket (tiempo real) + REST (consultas puntuales).** Correcto en ambos informes, no se toca.
- **El frontend no debe calcular física.** Ya lo cumple. Se mantiene como regla dura.
- **Los algoritmos de autonomía y confidence no se tocan.** `autonomy.py` y `confidence.py` son la fuente de verdad; el dashboard los consume, no los reimplementa ni en el backend central ni en el frontend.

### Cambia (gaps identificados arriba)

1. **Tipar el contrato de salida con Pydantic** (`models.py`) — hoy `list[dict]` sin schema. Esto no es una funcionalidad nueva, es hacer explícito lo que ya existe implícitamente en `parsers.py`/`autonomy.py`/`confidence.py`. Beneficio directo: Swagger/OpenAPI autogenerado, y el frontend deja de tener que "adivinar" campos leyendo el backend.
2. **Exponer explícitamente el origen de cada métrica** (medido/derivado/estimado) en el contrato, no solo para batería — generalizar el patrón que ya existe en `confidence.py` a un campo `meta` por sección (`power.meta`, más adelante `cpu.meta` si aplica). Esto es lo que permite mostrar angel1 y angel2 lado a lado sin implicar falsamente que ambos midieron lo mismo de la misma forma.
3. **Snapshot inicial inmediato en el WebSocket** al conectar (hoy el cliente espera hasta el próximo `BROADCAST_INTERVAL`). Cambio quirúrgico en `main.py::ws_endpoint`.
4. **No usar el `power_now_raw_w` ni ninguna métrica no cableada como si no existiera** — el frontend ya tiene el dato disponible; si se decide mostrarlo (ej. en el panel avanzado, como comparación "crudo vs. suavizado"), es solo consumo de un campo que el backend ya entrega, cero cálculo nuevo.
5. **No agregar campos "inventados"** para angel2 donde el hardware no los tiene (`current_now`, `charge_now`) — el contrato debe permitir `null` explícito, y el frontend debe mostrar "no disponible en este hardware", no `0` ni omitir el campo silenciosamente.

---

## 2. Flujo de datos (estado objetivo, Fase 1 — sin nueva infraestructura)

```
angel1 (hardware + EC)                    angel2 (hardware + EC)
        │ sysfs                                    │ sysfs
        └──────────────┐              ┌────────────┘
                        │  SSH (paramiko, pool)     │
                        ▼                           ▼
              lab-dashboard (angel1, único proceso central)
              ├─ collectors/commands.py   → comandos por host (ya distintos
              │                              implícitamente: el firmware
              │                              responde vacío en campos no
              │                              soportados, angel2 ya lo maneja)
              ├─ collectors/parsers.py    → dict tipado por host
              ├─ collectors/autonomy.py   → autonomy_seconds, mode, reliability
              ├─ collectors/confidence.py → confidence, telemetry_health
              ├─ monitor.py               → snapshot() por host, históricos
              └─ models.py (NUEVO: tipado Pydantic anidado)
                        │
             ┌──────────┴──────────┐
             ▼                     ▼
        WS /ws (push 2s,      REST /api/status
        snapshot inicial      (fallback, mismo shape)
        al conectar — NUEVO)
                        │
                        ▼
              frontend/static/js/app.js
              (SOLO formateo/presentación — ya cumple esto)
```

No se introduce ningún proceso nuevo, ningún servicio en angel2, ninguna base de datos. Es el mismo pipeline documentado en ambos informes, con el contrato de salida endurecido.

---

## 3. Modelo de datos recomendado (contrato JSON)

Regla general: **todo campo derivado o estimado va acompañado de su procedencia**, sin excepción, para que agregar un tercer servidor con hardware distinto (mencionado como escenario futuro en el informe de angel1) no requiera rediseñar el contrato.

```jsonc
{
  "servers": [
    {
      "name": "Servidor 2",
      "host": "192.168.100.7",
      "online": true,
      "last_update": 1735689600.0,
      "cpu": { "percent": 12.4, "cores": 4, "temp": 52.1 },
      "mem": { "total": ..., "used": ..., "percent": 41.2 },
      "power": {
        "available": true,
        "percent": 78,
        "status": "Discharging",
        "voltage": 15.12,
        "power_now_w": 8.4,
        "power_now_raw_w": 8.7,
        "autonomy_seconds": 14400,
        "autonomy_mode": "calibrado",
        "reliability": "alta",
        "validated": true,
        "meta": {
          "power_now_w": { "origin": "derived", "source": "current_now * voltage_now", "smoothed": true },
          "autonomy_seconds": { "origin": "estimated", "model": "constant_power", "mape_pct": 38.6 }
        }
      },
      "confidence": { "...": "..." },
      "telemetry_health": { "...": "..." }
    },
    {
      "name": "Servidor 3",
      "host": "192.168.100.8",
      "power": {
        "available": true,
        "percent": 65,
        "power_now_w": 9.1,
        "current_now": null,
        "charge_now": null,
        "meta": {
          "power_now_w": { "origin": "measured", "source": "EC nativo", "smoothed": false },
          "current_now": { "origin": "unavailable", "reason": "hardware no lo expone (angel2)" }
        }
      }
    }
  ]
}
```

Puntos clave:
- `null` explícito + `meta.origin: "unavailable"` para campos que el hardware de ese servidor específicamente no soporta (caso angel2: `current_now`, `charge_now`, `time_to_empty_now`). Nunca se sintetiza un valor.
- `meta` es opcional por campo — no se exige para todo (cpu.percent no lo necesita, es un cálculo trivial y estable de `/proc/stat`), pero es obligatorio para cualquier campo bajo `power.*` que ya hoy distingue medido/derivado/estimado en los informes.
- Este modelo se implementa como submodelos Pydantic (`PowerMetrics`, `FieldMeta`, etc.) en `models.py`, reemplazando el `list[dict]` genérico actual.

---

## 4. Responsabilidades

### Servidores (angel1 SSH-target, angel2 SSH-target)
- Exponer sysfs/`, /proc` sin intervención (ya es así, no se toca).
- No se despliega nada nuevo en esta fase.

### Backend central (`lab-dashboard`, corre en angel1)
- Único punto de recolección (SSH a los 3 hosts, incluido sí mismo).
- Única fuente de cálculo: parsing, autonomía, confidence — nada de esto se duplica ni en otro backend ni en el frontend.
- Responsable de anotar procedencia (`meta.origin`) por campo derivado/estimado.
- Responsable del contrato tipado (Pydantic) que documenta exactamente qué puede/no puede faltar por host.

### Frontend
- Solo formateo, presentación, color, gráficos, orden, filtros — regla ya cumplida, se mantiene.
- Debe manejar explícitamente el caso `meta.origin === "unavailable"` mostrando "no disponible en este hardware" en vez de ocultar la fila o mostrar un placeholder ambiguo como `--` (que hoy se usa también para "aún no llegó el dato", ambigüedad a resolver).

---

## 5. Organización de código propuesta (mínima, quirúrgica)

No se propone reestructurar carpetas. Cambios dentro de los archivos existentes:

- `backend/app/models.py`: agregar `PowerMetrics`, `FieldMeta`, `CpuMetrics`, `MemMetrics`, etc., como submodelos de `StatusResponse`. Sustituye `servers: list[dict]` por `servers: list[ServerStatus]`.
- `backend/app/collectors/confidence.py` y `autonomy.py`: sin cambios de lógica; solo adaptar el shape de salida para poblar `meta` en vez de campos sueltos al mismo nivel (cambio de forma, no de cálculo).
- `backend/app/main.py::ws_endpoint`: enviar `monitor.snapshot()` inmediatamente tras `connect()`, antes de esperar el próximo tick del `_broadcast_loop`.
- `frontend/static/js/app.js`: adaptar los `setText`/lectores de campo a la nueva ubicación de `meta` (cambio de rutas de acceso a JSON, no de lógica de cálculo).

---

## 6. Fase 2 (opcional, no incluida en este rediseño)

Documentada porque el informe de angel2 la recomienda explícitamente, pero requiere desplegar infraestructura nueva (fuera del alcance actual):

- Servicio HTTP local liviano en angel2 (y eventualmente angel1) que sirva su propio JSON ya parseado, reemplazando el `SSH + cat sysfs` crudo.
- Ventaja: angel2 deja de depender de que `lab-dashboard` reimplemente su parsing; el dashboard central pasa de "recolector remoto" a "agregador de APIs locales".
- Esto es lo que deja la arquitectura "preparada para crecer" (nuevo servidor = nuevo endpoint a agregar a `servers.json`, sin tocar parsers), pero se posterga hasta que se apruebe como trabajo separado, ya que implica desplegar y mantener un proceso nuevo en cada host.

---

## 7. Qué NO se va a hacer

- No se fusiona con `servercontrol` (bug de migración de Alembic sin resolver, documentado en el informe de angel1).
- No se usan los CSV de `battery-audit` como fuente para el dashboard en vivo (son para auditoría offline).
- No se reimplementan `autonomy.py`/`confidence.py`/parsers en el frontend ni en un servicio intermedio.
- No se inventan métricas nuevas: los campos ricos de `battery-audit` (wifi, cstates, etc.) **no se agregan** en esta fase — quedan listados como candidatos (sección 3.5 del informe angel1) para una decisión explícita del usuario, no se agregan por iniciativa propia.

---

## 8. Próximo paso

Este documento no modifica código. Si se aprueba, la Fase 1 (secciones 3–5) se implementa como una serie de cambios quirúrgicos en `models.py`, `main.py`, `confidence.py`/`autonomy.py` (shape only) y `app.js` (rutas de acceso), sin tocar SSH, comandos, ni algoritmos de cálculo.
