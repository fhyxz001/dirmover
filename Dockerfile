# DirMover 运行镜像：内置 Python 依赖，无需联网
FROM python:3.11-slim

# tzdata 用于让 TZ 环境变量生效（cron 按本地时区触发）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DIRMOVER_DATA=/data \
    DIRMOVER_LOG_DIR=/logs \
    DIRMOVER_HOST=0.0.0.0 \
    DIRMOVER_PORT=8000

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN mkdir -p /data /logs

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"

# 迁移的源/目标目录需要挂载进容器，任务里填容器内路径
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
