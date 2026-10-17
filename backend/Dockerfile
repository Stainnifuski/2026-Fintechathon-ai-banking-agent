FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
ARG INSTALL_MARKET_DATA=true
COPY backend/requirements-market.txt .
RUN if [ "$INSTALL_MARKET_DATA" = "true" ]; then pip install --no-cache-dir -r requirements-market.txt; fi

# 复制应用代码
COPY backend/ ./backend/
COPY config/ ./config/
COPY scripts/ ./scripts/
ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.revision=$SOURCE_REVISION
ENV SOURCE_REVISION=$SOURCE_REVISION
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
