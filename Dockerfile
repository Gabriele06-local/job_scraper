FROM python:3.11-slim

WORKDIR /app

# System deps for lxml / BeautifulSoup
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt1-dev \
    cron \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Health check: verify the health file was written within the last 5 hours
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "\
import json, sys, os;\
from datetime import datetime, timezone, timedelta;\
p='/tmp/health.json';\
sys.exit(0 if os.path.exists(p) and \
    (datetime.now(timezone.utc) - datetime.fromisoformat(json.load(open(p))['last_run_at'])) < timedelta(hours=5) \
    else 1)"

# Default: run the import command (docker-compose overrides per service)
CMD ["python", "-m", "import_service.cli", "import"]
