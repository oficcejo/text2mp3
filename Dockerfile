FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖（soundfile 需要 libsndfile）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 安装生产级 WSGI 服务器
RUN pip install --no-cache-dir gunicorn==23.0.0

# 复制应用代码
COPY app.py .
COPY templates/ templates/
COPY static/ static/

# 运行时目录（通过卷挂载持久化）
RUN mkdir -p output cloned_voices

EXPOSE 5000

ENV FLASK_ENV=production

# 使用 gunicorn 启动，4 个 worker，绑定所有接口
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--timeout", "120", "app:app"]
