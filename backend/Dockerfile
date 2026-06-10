# =============================================================================
# NovelMind 后端 Dockerfile
# =============================================================================
# 构建步骤说明:
#   1. 基于 Python 3.12 slim 镜像（体积小，仅含运行时必需组件）
#   2. 设置工作目录为 /app
#   3. 先复制依赖清单并安装（利用 Docker 层缓存，依赖不变时不重新安装）
#   4. 复制全部源代码
#   5. 暴露 8000 端口（FastAPI 默认端口）
#   6. 启动 uvicorn ASGI 服务器，监听所有网络接口
# =============================================================================

FROM python:3.12-slim

WORKDIR /app

# 先复制依赖文件，利用 Docker 层缓存加速重复构建
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目源代码（.dockerignore 应排除 .venv、__pycache__ 等）
COPY . .

EXPOSE 8000

# 启动 FastAPI 应用
# --host 0.0.0.0: 允许容器外部访问
# --port 8000: 监听端口
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
