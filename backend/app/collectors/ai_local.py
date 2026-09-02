"""Collector para servidores dedicados a inferencia de IA local (llm-api,
FastAPI propio corriendo junto a llama.cpp server -- ver /opt/llm-server en
Servidor 2). Solo se llama cuando el host ya respondió por SSH (ver
monitor._poll_server): esta API vive en el mismo host, así que si SSH está
caído esto también lo estará, y no vale la pena ni el intento.

No inventa nada que la API no exponga: si un campo no viene, se deja en None
y el frontend decide no mostrar esa sección. El detalle de tokens es el único
dato "en vivo" que expone llm-api (adentro del último mensaje de la conversación
más reciente) -- no hay contador acumulado histórico en la API actual."""

import logging
import time

import aiohttp

logger = logging.getLogger("dashboard")

# TTLs propios, independientes del POLL_INTERVAL del SSH: esta es una API HTTP
# aparte y no hace falta pegarle tan seguido como al resto de las métricas.
STATS_TTL = 5
MODELS_TTL = 60
LAST_REQUEST_TTL = 15

# host -> (fetched_at, resultado)
_stats_cache: dict[str, tuple[float, dict]] = {}
_models_cache: dict[str, tuple[float, dict]] = {}
_last_request_cache: dict[str, tuple[float, dict | None]] = {}


async def _get_json(session: aiohttp.ClientSession, url: str, api_key: str, timeout: float) -> dict | None:
    try:
        async with session.get(
            url, headers={"X-API-Key": api_key}, timeout=aiohttp.ClientTimeout(total=timeout)
        ) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except (aiohttp.ClientError, TimeoutError):
        return None


async def _fetch_stats(session, base_url, api_key, timeout) -> dict | None:
    return await _get_json(session, f"{base_url}/stats", api_key, timeout)


async def _fetch_models(session, base_url, api_key, timeout) -> dict | None:
    return await _get_json(session, f"{base_url}/models", api_key, timeout)


async def _fetch_last_request(session, base_url, api_key, timeout) -> dict | None:
    """Tokens/tok-per-sec del último mensaje de asistente en la conversación
    más reciente -- es el único dato de consumo real que expone la API hoy
    (no hay endpoint de acumulado). None si nunca hubo una conversación o el
    último mensaje todavía no tiene métricas guardadas (streaming en curso)."""
    convs = await _get_json(session, f"{base_url}/conversations", api_key, timeout)
    if not convs or not convs.get("conversations"):
        return None
    conv_id = convs["conversations"][0]["id"]
    detail = await _get_json(session, f"{base_url}/conversations/{conv_id}", api_key, timeout)
    if not detail or not detail.get("messages"):
        return None
    for msg in reversed(detail["messages"]):
        if msg.get("role") == "assistant" and msg.get("tokens_completion") is not None:
            return {
                "tokens_prompt": msg.get("tokens_prompt"),
                "tokens_completion": msg.get("tokens_completion"),
                "tokens_per_sec": msg.get("tokens_per_sec"),
                "at": msg.get("created_at") if "created_at" in msg else None,
            }
    return None


async def _cached(cache: dict, host: str, ttl: float, fetcher):
    now = time.monotonic()
    cached = cache.get(host)
    if cached and now - cached[0] < ttl:
        return cached[1]
    result = await fetcher()
    cache[host] = (now, result)
    return result


async def collect(host: str, base_url: str, api_key: str, timeout: float) -> dict:
    """Devuelve el bloque `ai` del snapshot de este host. `reachable=False`
    distingue "servidor SSH accesible pero la API de IA no responde" (proceso
    caído, puerto cerrado) de "servidor apagado" (que ya ni siquiera aparece
    en el snapshot, ver monitor._build_snapshot)."""
    if not api_key:
        return {"reachable": False, "error": "AI_API_KEY no configurada"}

    async with aiohttp.ClientSession() as session:
        stats = await _cached(_stats_cache, host, STATS_TTL, lambda: _fetch_stats(session, base_url, api_key, timeout))
        if stats is None:
            return {"reachable": False}

        models = await _cached(_models_cache, host, MODELS_TTL, lambda: _fetch_models(session, base_url, api_key, timeout))
        last_request = await _cached(
            _last_request_cache, host, LAST_REQUEST_TTL,
            lambda: _fetch_last_request(session, base_url, api_key, timeout),
        )

    models_list = (models or {}).get("models", [])
    return {
        "reachable": True,
        "runtime_status": stats.get("runtime_status"),
        "model_loaded": stats.get("model_loaded"),
        "active_model": stats.get("active_model"),
        "active_model_label": stats.get("active_model_label"),
        "cpu_freq_mhz": stats.get("cpu_freq_mhz"),
        "cpu_freq_warning": stats.get("cpu_freq_warning"),
        "cpu_freq_warning_text": stats.get("cpu_freq_warning_text"),
        "uptime_s": stats.get("uptime_s"),
        "models_total": len(models_list) or None,
        "models": [
            {"id": m["id"], "label": m["label"], "active": m["active"]}
            for m in models_list
        ],
        "last_request": last_request,
    }
