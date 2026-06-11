.PHONY: help dev-backend dev-frontend dev test test-backend test-frontend lint lint-backend lint-frontend clean install-backend install-frontend

help:  ## 显示帮助信息
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ─────────── 开发 ───────────

dev-backend:  ## 启动后端开发服务器
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-frontend:  ## 启动前端开发服务器
	cd frontend && npm run dev

dev:  ## 启动 Docker 数据库服务
	docker compose up -d db chroma

# ─────────── 安装 ───────────

install-backend:  ## 安装后端依赖（含开发依赖）
	cd backend && pip install -r requirements.txt -r requirements-dev.txt

install-frontend:  ## 安装前端依赖
	cd frontend && npm install

# ─────────── 测试 ───────────

test: test-backend test-frontend  ## 运行全部测试

test-backend:  ## 运行后端测试（pytest）
	cd backend && pytest -v --tb=short --color=yes

test-backend-watch:  ## 运行后端测试（watch 模式）
	cd backend && pytest -v --tb=short -f

test-frontend:  ## 运行前端测试（vitest）
	cd frontend && npx vitest run --reporter=verbose

test-frontend-watch:  ## 运行前端测试（watch 模式）
	cd frontend && npx vitest

# ─────────── 代码质量 ───────────

lint: lint-backend lint-frontend  ## 运行全部代码检查

lint-backend:  ## 后端代码检查（ruff）
	cd backend && ruff check app tests
	cd backend && ruff format --check app tests

lint-backend-fix:  ## 后端代码自动修复
	cd backend && ruff check app tests --fix
	cd backend && ruff format app tests

lint-frontend:  ## 前端代码检查（tsc + eslint）
	cd frontend && npx tsc --noEmit
	cd frontend && npm run lint

# ─────────── 构建 ───────────

build-frontend:  ## 构建前端生产包
	cd frontend && npm run build

# ─────────── 清理 ───────────

clean:  ## 清理构建产物和缓存
	rm -rf frontend/.next frontend/node_modules
	rm -rf backend/__pycache__ backend/app/__pycache__ backend/app/*/__pycache__
	rm -rf backend/tests/__pycache__
