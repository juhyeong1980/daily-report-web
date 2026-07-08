FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/

ENV DATA_DIR=/data
VOLUME /data

EXPOSE 8000
WORKDIR /app/backend
CMD ["gunicorn", "-b", "0.0.0.0:8000", "-w", "2", "app:app"]
