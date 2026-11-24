# syntax=docker/dockerfile:1
FROM node:20-slim AS webbuild
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ ./backend/
COPY --from=webbuild /app/frontend/dist ./frontend/dist
COPY samples/ ./samples/
COPY scripts/ ./scripts/
ENV DB_PATH=/app/data/leaseguard.db UPLOAD_DIR=/app/data/uploads USE_FIXTURES=true
RUN mkdir -p /app/data/uploads && useradd -m appuser && chown -R appuser /app/data
USER appuser
EXPOSE 8000
WORKDIR /app/backend
ENV PYTHONPATH=/app/backend
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
