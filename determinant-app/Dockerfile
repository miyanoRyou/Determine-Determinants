# Small Debian based Python image
FROM python:3.12-slim
# All following paths are relative to /app
WORKDIR /app
# No .pyc files in the image, and unbuffered output so logs show up immediately
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Copy only requirements first so the install layer is cached until they change
COPY requirements.txt .
# Install dependencies without keeping pip's download cache
RUN pip install --no-cache-dir -r requirements.txt
# Application code and the static frontend (tests and docs are not needed at runtime)
COPY main.py .
COPY static ./static
# Documentation only: the port uvicorn listens on below
EXPOSE 8000
# Bind to all interfaces so the published port is reachable. Keep --workers 1: calculations, progress
# jobs, the rate limiter and the export slots live in this process's memory, so a second worker would
# not see them and steps, progress or cancel requests could reach a worker that has no such id (404).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
