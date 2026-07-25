# Auditoria de bateria y algoritmo hibrido de autonomia

## Contexto

Captura de 10 minutos, 200 muestras (cada 3s) en `angel1` y `angel2`
(`sample_data/raw_*.txt`, formato pipe-delimited, ver `simulate_hybrid.py`
para el parser). Resultado clave: en **angel2**, `power_now` reporto un
valor **constante** (11.40W) durante los 10 minutos completos mientras
`energy_now` si avanzaba con normalidad — el firmware entrega una lectura de
potencia obsoleta/cacheada, no instantanea. En **angel1** `power_now` fluctua
de forma realista (correlacionado con eventos de CPU detectados).

## Algoritmo hibrido (`backend/app/collectors/autonomy.py`)

Separado en etapas, cada una testeable de forma independiente:

| Etapa | Funcion | Que hace |
|---|---|---|
| Calcular | `rolling_power_avg30()` | Promedio movil de `power_now` en los ultimos 30s (Paso 1 — fuente principal, la mas estable segun la auditoria) |
| Calibrar | `evaluate()` | Cada 5 min compara el promedio de `power_now` contra la potencia derivada (`delta(energy_now)/delta(tiempo)`, Paso 2) |
| Decidir | `blended_power()` | Aplica el factor de calibracion solo si `evaluate()` confirmo firmware inconsistente (Paso 4/5) |

La lectura/conversion de campos (`power_now` vs `charge_now`+`current_now`+
`voltage_now`, segun exponga el firmware) ya vive en
`backend/app/collectors/parsers.py::parse_battery` — `autonomy.py` solo
consume su salida normalizada (`power_now_w`, `energy_now_wh`,
`energy_full_wh`), por lo que funciona igual sin importar el formato de
origen.

### Deteccion de firmware defectuoso (Paso 3)

Cada ciclo de 5 minutos (`CHECK_INTERVAL_S`), si `power_now` vario menos del
2% (`STALE_CV_THRESHOLD`) en la ventana **y** difiere mas del 15%
(`DIVERGENCE_THRESHOLD`) de la potencia derivada, cuenta como un ciclo de
sospecha (`stale_streak += 1`); si no, `stale_streak` vuelve a 0 de inmediato.
Solo tras **3 ciclos consecutivos** (`CONFIRM_CYCLES`, 15 minutos) se confirma
el modo `calibrado` — un unico ciclo raro no activa nada (modo intermedio
`firmware_inconsistente`, visible en logs pero sin afectar el numero
mostrado).

### Factor de calibracion (Paso 4)

Fijo en 20% (`CALIBRATION_WEIGHT = 0.2`): `potencia_usada = 80% *
promedio(power_now, 30s) + 20% * potencia_derivada`. Se eligio conservador
porque la auditoria mostro que `power_now` promediado es, incluso en el host
con firmware defectuoso, la señal con **menor varianza** de las evaluadas —
el objetivo del factor es corregir el **sesgo** (valor promedio incorrecto)
sin heredar el ruido de la derivada (que amplifica los saltos discretos de
`energy_now` en vez de suavizarlos, confirmado en la auditoria original).

### Auto-reversion (Paso 5)

No hay bandera "una vez roto, siempre calibrado" ni configuracion manual por
servidor. Cada ciclo de 5 min reevalua desde cero: en cuanto un ciclo deja de
mostrar el patron (power_now vuelve a variar de verdad), `stale_streak` cae a
0 y el modo vuelve a `normal` inmediatamente. Verificado con una prueba
sintetica: 20 min de `power_now` congelado -> confirma `calibrado`; luego 15
min de variacion realista (~CV 10%, como angel1) -> vuelve a `normal` solo.

## Compatibilidad de hardware

`convert()` en `simulate_hybrid.py` replica exactamente el fallback de
`parsers.parse_battery`: usa `power_now`/`energy_now` si el firmware los
expone, si no los deriva de `current_now`/`charge_now` * `voltage_now`. Los
dos hosts auditados usan formatos distintos (angel1: `charge_now`/
`current_now`; angel2: `energy_now`/`power_now`) y el algoritmo se comporto
correctamente en ambos sin ninguna rama especial por host.

## Resultados de la simulacion (`simulate_hybrid.py`)

Compara el algoritmo anterior (promedio simple sobre un buffer de ~120
muestras/6min, sin ventana de tiempo real) contra el hibrido, replayando los
CSV capturados. Referencia de "verdad": potencia real = delta total de
energia / tiempo total de la sesion (fisica, no depende de ninguna lectura
individual).

```
angel1 (power_now sano, 10 min):
  Error medio      actual=3.55W   hibrido=3.54W   (+0.2%) -- identico, sin
                                                     regresion en el host sano
  Modo hibrido: 100% del tiempo en "normal" (nunca calibra sin motivo)

angel2 (power_now congelado, 10 min -- no alcanza a confirmar, faltan datos
para 3 ciclos de 5 min):
  Error medio      actual=4.78W   hibrido=4.78W   (0.0%) -- igual, como se
                                                     espera (aun no calibro)

angel2 (power_now congelado, sesion larga simulada ~30 min -- 3 repeticiones
de la captura con energia acumulada continua, no un reset en diente de
sierra):
  Modos: normal=80, firmware_inconsistente=201, calibrado=319
  Error medio      actual=4.72W   hibrido=4.25W   MEJORA 10.1%
```

El CV de potencia/autonomia se ve "peor" en el escenario calibrado (4.7%
frente a 0.0%) porque el 0.0% del algoritmo anterior es el sintoma del bug
(un numero perfectamente estable porque esta *congelado*, no porque sea
correcto) — la metrica que importa para "mejora medible" es el error contra
la potencia real, y ahi el hibrido gana 10.1% en el escenario exacto para el
que fue diseñado, sin ningun costo en el host sano.

Reproducir: `python system/battery-audit/simulate_hybrid.py`

## Archivos

- `backend/app/collectors/autonomy.py` — algoritmo hibrido (nuevo)
- `backend/app/monitor.py` — integrado: registra cada muestra
  (`autonomy.record_sample`) y usa `autonomy.estimate()` en `snapshot()` en
  vez del promedio simple anterior
- `simulate_hybrid.py` — script de validacion (algoritmo actual vs hibrido)
- `sample_data/raw_angel1.txt`, `raw_angel2.txt` — capturas crudas de la
  auditoria (10 min, 200 muestras c/u), usadas por `simulate_hybrid.py`

## Logs en produccion

`evaluate()` loguea cada ciclo de 5 min a nivel INFO: modo, promedio de
`power_now`, potencia derivada, CV, y racha de ciclos sospechosos. Buscar
`autonomy calibration check` en los logs del backend para verificar el
comportamiento en un host real.
