# HomeBrain v1.x → v2.0 迁移指南

## 变更概述

v2.0 的核心变更是：**将所有小米设备交互从直接 MQTT 连接迁移到 Home Assistant xiaomi_home 插件**。

HomeBrain 不再直接与小米网关通信，而是通过 HA Bridge 模块与 Home Assistant 交互，HA 内部的 xiaomi_home 插件负责设备管理和状态同步。

---

## ❌ 已废弃

### 设备驱动层

| 文件/组件 | 替代方案 |
|-----------|----------|
| `src/drivers/xiaomi/mqtt_driver.py` | HA xiaomi_home 插件（HA 内置集成） |
| `src/drivers/xiaomi/gateway.py` | HA Bridge client — 通过 `state_changed` 事件订阅感知设备 |
| `src/drivers/xiaomi/device_registry.py` | HA 实体注册表 — 所有设备在 HA 中统一管理 |

### 容器/服务

| 容器 | 替代方案 |
|------|----------|
| `xiaomi-mqtt` 容器 | HA 内置 MQTT Broker（自动启动） |
| `homebrain-mqtt-listener` 容器 | `homebrain-api` 容器内 HA WebSocket 事件流 |

### 通信协议

| 旧方式 | 新方式 |
|--------|--------|
| 直接 MQTT topic 订阅 (`zigbee/+/state`) | HA WebSocket `state_changed` 事件 |
| 直接 MQTT publish (`zigbee/{device}/set`) | HA REST API `POST /api/services/{domain}/{service}` |
| 自定义设备发现协议 | HA 集成发现 + 设备自动注册 |

### 场景引擎 transport

| 文件/类 | 替代方案 |
|---------|----------|
| `src/scene_engine/mqtt_transport.py` | `src/scene_engine/ha_transport.py` |
| `MqttTransport` 类 | `HATransport` 类 |
| MQTT topic pattern 匹配 | HA entity_id + state 匹配 |
| MQTT `publish()` 动作 | HA `call_service()` 动作 |

---

## ✅ 新增

### 模块

| 模块 | 路径 | 说明 |
|------|------|------|
| HA Bridge | `src/ha_bridge/` | HA REST + WebSocket 客户端（15 tests） |
| Scene Engine HA Transport | `src/scene_engine/ha_transport.py` | 场景引擎 HA 事件适配层 |
| LLM Adapter HA Context | `src/llm_adapter/ha_context.py` | LLM 上下文构建器（从 HA 获取设备状态） |

### API 端点

| 端点 | 说明 |
|------|------|
| `POST /api/llm/chat` | 自然语言设备控制（DeepSeek function calling） |
| `GET /api/devices` | 通过 HA Bridge 获取所有设备状态 |
| `GET /api/devices/{entity_id}` | 获取单个设备状态 |
| `POST /api/devices/{entity_id}/control` | 控制单个设备 |

### 部署脚本

| 脚本 | 说明 |
|------|------|
| `deploy-ha.sh` | 一键部署 Home Assistant 容器 |
| `install-xiaomi-hacs.sh` | 安装 xiaomi_home 插件（HACS 方式） |
| `save-ha-token.sh` | 交互式创建并保存 HA Token |
| `check-gateway-fw.sh` | 检查网关固件兼容性 |

---

## 迁移步骤

### 1. 备份 v1.x 数据

```bash
# 备份场景规则
cp -r /opt/homebrain/scenes /opt/homebrain/scenes.v1.bak

# 备份设备注册表（如有）
cp /opt/homebrain/device_registry.json /opt/homebrain/device_registry.v1.bak
```

### 2. 部署 Home Assistant

```bash
cd /root/coding/homebrain-v2
./deploy-ha.sh
```

### 3. 安装 xiaomi_home 插件

```bash
./install-xiaomi-hacs.sh
```

### 4. 在 HA Web UI 中完成设备导入

1. 访问 `http://192.168.66.68:8123`
2. 完成初始设置
3. 添加 xiaomi_home 集成 → 录入小米账号
4. 所有小米设备自动出现在 HA 中
5. 创建长期访问令牌（个人资料 → 安全）

### 5. 迁移场景规则

场景规则从 MQTT topic 路径改为 HA entity_id：

```yaml
# v1.x (MQTT)
trigger:
  mqtt_topic: "zigbee/0x00158d0001e6d23a/state"
  condition: "payload.temperature > 30"

# v2.0 (HA)
trigger:
  entity_id: "sensor.aqara_temp_humidity_temperature"
  condition: "state > 30"
```

### 6. 启动 HomeBrain v2

```bash
./save-ha-token.sh
docker compose up -d
```

---

## 兼容性说明

- **场景规则 YAML 格式向后兼容** — 仅 `trigger`/`action` 中 transport 相关字段变更
- **LLM Adapter API 接口与 v1.x 一致** — 自然语言指令无需修改
- **不兼容 v1.x MQTT 配置** — 必须通过 HA 完成设备集成后再启动 HomeBrain
