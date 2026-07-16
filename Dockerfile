FROM python:3.11.11-slim

WORKDIR /app

RUN addgroup --system --gid 1001 dashboard && \
    adduser --system --uid 1001 --gid 1001 --home /app --shell /sbin/nologin dashboard

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY frontend/ /frontend/

RUN mkdir -p /logs && chown dashboard:dashboard /logs

EXPOSE 8600

USER dashboard

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8600"]
