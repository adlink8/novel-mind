.PHONY: help dev-backend dev-frontend dev test lint clean

help:  ## 显示帮助信息
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

dev-backend:  ## 启动后端开发服务器
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend:  ## 启动前端开发服务器
	cd frontend && npm run dev

dev:  ## 启动 Docker 数据库服务
	docker compose up -d db chroma

test:  ## 运行后端测试
	cd backend && pip install -q aiosqlite pytest pytest-asyncio httpx && pytest

lint:  ## 前端 TypeScript 类型检查
	cd frontend && npx tsc --noEmit

clean:  ## 清理构建产物
	rm -rf frontend/.next frontend/node_modules backend/__pycache__ backend/app/__pycache__ backend/app/*/__pycache__
