FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
# config.yaml and data/storageState.json are provided at runtime via volume mounts
# (see docker-compose.yml), so they are intentionally not baked into the image.
ENV CONFIG_PATH=/app/config.yaml STORAGE_STATE=/app/data/storageState.json
CMD ["python", "-m", "src.main"]
