FROM python:3.11.11-slim

WORKDIR /app

RUN addgroup --system --gid 1001 dashboard && \
    adduser --system --uid 1001 --gid 1001 --home /app --shell /sbin/nologin dashboard

# iproute2 (ip neigh) e iputils-ping: usados por device_presence.py para
# detectar los dispositivos Companion/ADB por ARP + ping -- ver
# docker-compose.yml (network_mode: host) para por que hace falta ver la
# tabla ARP real del host en vez de la del bridge de Docker.
RUN apt-get update && apt-get install -y --no-install-recommends \
    iproute2 iputils-ping \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY frontend/ /frontend/

RUN mkdir -p /logs && chown dashboard:dashboard /logs

EXPOSE 8600

USER dashboard

# 127.0.0.1, no 0.0.0.0: con network_mode: host (ver docker-compose.yml) este
# 127.0.0.1 ES el del host -- bindear a todas las interfaces expondria el
# puerto directo en la LAN/tailnet, saltandose nginx y el hardening de UFW
# (ver el comentario extenso en docker-compose.yml sobre el incidente 2026-08-13).
CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8600"]
