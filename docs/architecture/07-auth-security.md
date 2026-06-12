# 07 — 认证与安全架构

安全是 NovelMind 的架构基线。所有资源访问必须经过认证和权限校验。

## 安全原则

```
任何 Novel / Chapter / AIModelConfig / TextChunk / RAG 操作都不能跨 owner 访问。
跨用户资源统一返回 404（不暴露资源是否存在）。
```

## 认证机制

### 双通道认证

| 通道 | 方式 | 使用场景 |
|---|---|---|
| HttpOnly Cookie | `access_token` Cookie, SameSite Lax | 前端浏览器请求 |
| Bearer Token | `Authorization: Bearer <token>` | API 客户端、测试、外部工具 |

**来源**: `backend/app/core/security.py`（5.2KB）

### JWT 配置

```python
# JWT 签发参数
{
    "sub": str(user.id),
    "iss": "novel-mind",          # 发行者
    "aud": "novel-mind-api",      # 受众
    "exp": datetime + timedelta(hours=24)
}

# JWT 验证
- 签名验证（HMAC-SHA256）
- iss/aud 校验
- 过期时间检查
- 数据库确认用户存在且 is_active
```

### 认证中间件链

```
请求进入
  → TrailingSlash 中间件（URL 规范化）
  → RequestLogging 中间件（结构化日志，URL 脱敏）
  → CORS 中间件（Origin 头校验）
  → dependencies.get_current_user
    → Cookie 提取：request.cookies.get("access_token")
    → Cookie 不存在 → 回退 Bearer：Authorization header
    → decode_jwt(token)
    → 查询 DB 确认用户存在且 is_active
  → 路由处理函数（current_user 已注入）
```

**来源**: `backend/app/api/dependencies.py`、`backend/app/core/security.py`、`backend/app/main.py`

### Cookie 安全策略

```python
response.set_cookie(
    key="access_token",
    value=token,
    httponly=True,           # JS 不可读
    secure=settings.ENV == "production",  # 生产环境 HTTPS only
    samesite="lax",          # 防止 CSRF
    max_age=86400            # 24 小时
)
```

---

## 密码安全

### bcrypt 密码哈希

**来源**: `backend/app/core/security.py`

```python
# 注册时
hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())

# 登录时
bcrypt.checkpw(password.encode("utf-8"), user.hashed_password.encode("utf-8"))
```

### 超长密码防御

**规则**: 拒绝超过 **72 UTF-8 字节**的密码（bcrypt 算法上限）。

```python
if len(password.encode("utf-8")) > 72:
    raise HTTPException(400, "密码过长（最多 72 字节）")
```

已在注册和登录端点应用。

---

## API Key 加密

### 加密方案

**来源**: `backend/app/core/crypto.py`（2.5KB）

```
写入: 明文 API Key → Fernet 加密 → "enc:v1:" + base64(密文) → 存入库
读取: 密文 → 去掉 "enc:v1:" 前缀 → Fernet 解密 → 明文
```

### Key Rotation

支持密钥轮换，旧密文可用 `previous_key` 解密：

```python
# 环境变量
NOVELMIND_ENCRYPTION_KEY=<current_fernet_key>
NOVELMIND_PREVIOUS_ENCRYPTION_KEY=<old_key>  # 可选

# 轮换流程
1. 设置 NOVELMIND_PREVIOUS_ENCRYPTION_KEY = 旧密钥
2. 设置 NOVELMIND_ENCRYPTION_KEY = 新密钥
3. 读取时先用当前密钥解密，失败则用 previous_key
4. 写入时自动用新密钥加密
5. 全部迁移后移除 previous_key
```

### 旧明文兼容

如果数据库中仍存有未加密的旧 API Key（无 `enc:v1` 前缀），自动识别并返回明文，同时在下一次写入时升级为密文。

---

## Owner 资源隔离

### 隔离规则

| 资源 | 列表 | 详情 | 更新 | 删除 | 特殊操作 |
|---|---|---|---|---|---|
| Novel | WHERE owner_id = user.id | 校验 owner | 校验 owner | 校验 owner + 文件补偿 | 章节查询间接校验 |
| Chapter | 通过 novel.owner_id | 通过 novel.owner_id | 通过 novel.owner_id | — | — |
| AIModelConfig | WHERE owner_id = user.id | 校验 owner | 校验 owner | 校验 owner | 测试连接/设为默认均校验 |
| TextChunk | 通过 novel.owner_id | 通过 novel.owner_id | — | — | — |
| ImportJob | 通过 novel.owner_id | 通过 novel.owner_id | — | — | — |

### 隔离实现

```python
# 列表查询
novels = await db.execute(
    select(Novel).where(Novel.owner_id == current_user.id)
)

# 详情校验
novel = await db.get(Novel, novel_id)
if not novel or novel.owner_id != current_user.id:
    raise HTTPException(404)  # 不暴露是否存在
```

**跨用户隔离已通过回归测试验证。**

---

## SSRF 防护

**来源**: `backend/app/core/url_security.py`（2.6KB）

当用户配置自定义 AI Provider URL 时，必须防止 SSRF（服务端请求伪造）。

### 防护层级

| 层级 | 检查内容 |
|---|---|
| **协议过滤** | 只允许 `http://` 和 `https://` |
| **凭据检测** | URL 中不允许含 `@`（防止 `user:pass@evil.com`） |
| **DNS 解析** | 解析 hostname 到 IP 地址 |
| **IPv4 校验** | 拒绝 `127.0.0.0/8`、`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16` |
| **IPv6 校验** | 拒绝 `::1`、`fe80::/10` 等 |
| **白名单** | 管理员可通过 `NOVELMIND_SSRF_ALLOWLIST` 配置精确主机白名单 |
| **私网显式配置** | 内网自托管模型需管理员显式添加到白名单 |

### 双重校验

自定义 URL 在**配置时**和**调用前**执行两次校验，防止 DNS rebinding 攻击。

---

## 上传安全

**来源**: `backend/app/services/novel_service.py`

| 防护 | 措施 |
|---|---|
| 文件类型 | 只接受 `.txt` |
| 路径遍历 | 随机文件名（uuid），不信任用户提供的文件名 |
| 目录 containment | 写入前校验路径在 `backend/uploads/` 范围内 |
| 大小限制 | 配置化上限 + 读取上限保护 |
| 失败补偿 | 数据库写入失败 → 删除文件；文件删除失败 → 不中断 |

---

## 响应最小化

| 端点 | 排除的敏感字段 |
|---|---|
| `GET /api/novels/{id}` | `source_path`（文件系统路径） |
| `GET /api/novels/{id}` | `chapters.content`（章节正文，只返回摘要） |
| `GET /api/novels/{id}/chapters` | `content`（章节列表不返回正文） |
| `GET /api/models/{id}` | `api_key`（只在创建/更新时接受） |
| AI Provider 错误 | 脱敏后返回（不暴露 API Key 或内部 URL） |

---

## 安全闭合项（已修复）

| 问题 | 状态 |
|---|---|
| 匿名和跨用户 IDOR | CLOSED：统一认证 + owner 依赖 |
| Cookie CSRF | CLOSED：写请求校验 Origin |
| bcrypt 超长密码 | CLOSED：拒绝 >72 bytes |
| 模型配置越权 | CLOSED：AIModelConfig 增加 owner_id |
| 自定义 URL SSRF | CLOSED：白名单 + DNS/IP 校验 |
| JWT/加密共用弱密钥 | CLOSED：独立密钥 + 生产校验 |
| 上传/数据库双写不一致 | CLOSED：失败补偿 |
| ORM/Alembic 漂移 | CLOSED：真实 PG upgrade/current/check |
| 数据库 URL 日志泄漏 | CLOSED：URL 脱敏 |

---

## 修改后验证

```bash
cd backend
source venv/Scripts/activate

# 安全测试
pytest tests/test_security.py tests/test_auth.py -v

# SSRF 测试
pytest tests/test_url_security.py -v

# 全量测试
pytest tests/ -v
```

## 禁止随意改动的部分

- **JWT 签名逻辑**（除非同步更新登录端点和所有 token 依赖）
- **bcrypt 配置**（更换算法需全量密码迁移）
- **Owner 隔离逻辑**（破坏会导致数据泄漏）
- **Fernet 加密密钥**（更换需 key rotation 流程）
- **SSRF 白名单**（放宽需安全评审）
