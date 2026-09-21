# Image de production MBDV - Prospection
# Fonctionne partout ou Docker est disponible (Render, Railway, Fly.io, VPS...)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render injecte la variable PORT ; run.py la lit automatiquement.
CMD ["python3", "run.py"]
