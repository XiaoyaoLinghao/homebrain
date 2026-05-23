FROM python:3.12-slim

WORKDIR /app

# Copy and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source packages as top-level modules (ha_bridge, scene_engine, llm_adapter)
COPY src/ /app/

# Copy application entry point
COPY app.py .

EXPOSE 3002

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:3002/health')"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3002"]
