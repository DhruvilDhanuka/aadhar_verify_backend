FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_CONCURRENCY=4

RUN apt-get update && apt-get install -y --no-install-recommends \
    libzbar0 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "uvicorn[standard]"

COPY . .

EXPOSE 10000

CMD exec uvicorn app:app \
    --host 0.0.0.0 \
    --port ${PORT:-10000} \
    --workers ${WEB_CONCURRENCY:-4} \
    --loop uvloop \
    --http httptools