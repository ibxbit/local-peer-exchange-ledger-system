FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Create persistent data directories
RUN mkdir -p /app/instance /app/instance/reports

EXPOSE 8000

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000

CMD ["python", "run.py"]
