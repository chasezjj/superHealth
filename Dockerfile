# SuperHealth Docker 镜像
# 使用方式：
#   docker build -t superhealth .
#   docker run -p 8501:8501 \
#     -v $(pwd)/health.db:/app/health.db \
#     -v ~/.superhealth:/home/superhealth/.superhealth \
#     superhealth

FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-wqy-zenhei \
    fonts-wqy-microhei \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -s /bin/bash superhealth
WORKDIR /app

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[all]"

# Install Playwright browsers
RUN playwright install chromium && playwright install-deps chromium

# Copy source code
COPY src/ ./src/
COPY schema.sql ./
COPY examples/ ./examples/

# Switch to non-root user
RUN chown -R superhealth:superhealth /app
USER superhealth

# Initialize database (if no mounted health.db)
RUN python -c "from superhealth.database import init_db; init_db()"

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

CMD ["streamlit", "run", "src/superhealth/dashboard/app.py", "--server.address=0.0.0.0"]
