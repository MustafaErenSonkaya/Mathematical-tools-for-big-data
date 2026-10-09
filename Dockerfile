# Image for the outlier-detection app.
FROM python:3.11-slim

# Unbuffered output so `docker compose logs` shows alerts immediately;
# no .pyc files because the source code is read-only for the app user.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first: this layer is cached and only rebuilt
# when requirements.txt changes, not on every code edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Run as an unprivileged user. If the app is ever compromised, the attacker
# is not root inside the container. data/ is the only writable folder
# (alerts.jsonl); a new named volume mounted there inherits this ownership.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown appuser:appuser /app/data

COPY config.yaml pytest.ini ./
COPY src ./src
COPY tests ./tests

USER appuser

EXPOSE 8000
CMD ["python", "-m", "src.main", "--metrics"]
