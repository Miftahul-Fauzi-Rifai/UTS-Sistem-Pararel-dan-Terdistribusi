FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEDUP_DB_PATH=/app/data/dedup.db

WORKDIR /app

RUN adduser --disabled-password --gecos '' appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

USER appuser

EXPOSE 8080

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
