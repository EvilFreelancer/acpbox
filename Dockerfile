FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 8080

# Runs uvicorn (via gateway.main) with one worker = one ACP process. For N ACP instances use:
# CMD ["uvicorn", "gateway.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "8"]
CMD ["python", "-m", "gateway.main"]
