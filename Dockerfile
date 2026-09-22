FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY templates ./templates
COPY static ./static
# data/ (SQLite state + storageState.json + debug) is provided at runtime via a
# read-write volume mount (see docker-compose.yml).
ENV STORAGE_STATE=/app/data/storageState.json STATE_DB=/app/data/state.db
EXPOSE 8000
CMD ["uvicorn", "src.webapp:app", "--host", "0.0.0.0", "--port", "8000"]
