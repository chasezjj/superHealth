# SuperHealth Docker 镜像
# 使用方式：
#   docker build -t superhealth .
#   docker run -p 8501:8501 -v $(pwd)/health.db:/app/health.db -v ~/.superhealth:/root/.superhealth superhealth

FROM python:3.12-slim

WORKDIR /app

# 安装系统依赖（Playwright 需要 chromium + 字体）
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
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

# 复制依赖文件并安装
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -e ".[all]"

# 安装 Playwright 浏览器
RUN playwright install chromium && playwright install-deps chromium

# 复制源码
COPY src/ ./src/
COPY schema.sql ./
COPY examples/ ./examples/

# 初始化数据库（如果没有挂载 health.db）
RUN python -c "from superhealth.database import init_db; init_db()"

# 默认暴露 Dashboard 端口
EXPOSE 8501

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1

# 默认启动 Dashboard
CMD ["streamlit", "run", "src/superhealth/dashboard/app.py", "--server.address=0.0.0.0"]
