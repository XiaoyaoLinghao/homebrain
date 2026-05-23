# HomeBrain v2.0

智能家居本地决策中枢 — 设备层 (HA + xiaomi_home) → 决策层 (HomeBrain) → 交互层 (Web/LLM/Node-RED)

## 架构

```
┌──────────────────────────────────────────────────────┐
│                   交互层                              │
│     Web UI / LLM 自然语言 / Node-RED 自动化          │
├──────────────────────────────────────────────────────┤
│                   决策层 (HomeBrain)                  │
│  ┌────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │ HA Bridge  │  │ Scene Engine │  │ LLM Adapter │  │
│  │ REST+WS    │  │ YAML DSL     │  │ DeepSeek    │  │
│  │ 客户端     │  │ 场景自动化    │  │ 自然语言    │  │
│  └────────────┘  └──────────────┘  └─────────────┘  │
├──────────────────────────────────────────────────────┤
│                   设备层 (Home Assistant)             │
│     xiaomi_home 插件 + 内置 MQTT Broker              │
│     小米 / Aqara / 其他品牌设备统一接入               │
└──────────────────────────────────────────────────────┘
```

## 模块

| 模块 | 路径 | 职责 | 测试覆盖 |
|------|------|------|----------|
| HA Bridge | `src/ha_bridge/` | HA REST + WebSocket 客户端，设备状态读写与事件订阅 | 15 tests |
| Scene Engine | `src/scene_engine/` | 场景自动化引擎，YAML DSL 定义规则，HA 事件触发 | 31 tests |
| LLM Adapter | `src/llm_adapter/` | 自然语言控制，DeepSeek 函数调用 + HA 上下文构建 | 29 tests |

### HA Bridge (`src/ha_bridge/`)

- `client.py` — HA REST API 客户端 + WebSocket 事件流
- 支持 `state_changed` 事件订阅、服务调用、实体 CRUD
- 自动 Token 认证 + WebSocket 断线重连

### Scene Engine (`src/scene_engine/`)

- `engine.py` — 规则引擎核心：条件评估 + 动作执行
- `ha_transport.py` — HA API 适配层（替代 v1 MQTT transport）
- 规则定义：`rules_example.yaml`
- 触发模式：设备状态变化、时间调度、手动触发

### LLM Adapter (`src/llm_adapter/`)

- `adapter.py` — DeepSeek API 调用 + 函数工具定义
- `ha_context.py` — 从 HA Bridge 获取当前设备状态构建 LLM 上下文
- 端点：`POST /api/llm/chat` — 自然语言 → 设备控制

## 部署

### 前置条件

- Docker & Docker Compose v2
- 宿主机 IP 192.168.66.68（或修改脚本中的 IP）
- 小米网关在同一局域网

### 1. 部署 Home Assistant

```bash
./deploy-ha.sh
```

### 2. 安装 xiaomi_home 插件

```bash
./install-xiaomi-hacs.sh
```

### 3. 设备导入 + Token 创建

在 HA Web UI (`http://192.168.66.68:8123`) 中：
1. 完成初始设置
2. 在 "配置 → 设备与服务 → 添加集成" 中搜索 xiaomi_home，完成小米设备导入
3. 在 "个人资料 → 长期访问令牌" 创建 Token

### 4. 保存 Token

```bash
./save-ha-token.sh
```

### 5. 启动 HomeBrain

```bash
# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 HA_API_TOKEN 和 DEEPSEEK_API_KEY

# 启动
docker compose up -d
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `HA_API_URL` | Home Assistant REST API 地址 | `http://192.168.66.68:8123` |
| `HA_API_TOKEN` | HA 长期访问令牌 | 必填 |
| `DEEPSEEK_API_KEY` | DeepSeek API Key（LLM 功能需要） | 可选 |

## 网络拓扑

```
192.168.66.68 (AI Box) ─── 宿主机
  ├── Docker: homeassistant (host 网络, 8123)
  ├── Docker: homebrain-api (host 网络, 3000)
  └── Docker: xiaomi_home 插件 (HA 内置)
```

## 从 v1.x 迁移

参见 [MIGRATION.md](MIGRATION.md) — 废弃的 MQTT 直连方式已替换为 HA Bridge。

## 技术栈

- **语言**: Python 3.11+
- **框架**: FastAPI, asyncio
- **集成**: Home Assistant REST API + WebSocket
- **LLM**: DeepSeek API (OpenAI 兼容)
- **测试**: pytest (75 tests, 100% 模块覆盖)
