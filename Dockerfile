
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gateway ./gateway
COPY config.example.yaml ./config.yaml

ENV CONFIG_PATH=/app/config.yaml
EXPOSE 8080
CMD ["python", "-m", "gateway.main"]
