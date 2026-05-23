#!/usr/bin/env bash
# =============================================================================
# deploy-ha.sh — Home Assistant Docker 一键部署脚本
# 将 HA Container 部署到 192.168.66.68 并与现有 HomeBrain 5 共存
# =============================================================================
set -euo pipefail

# ---- 配置 ----
TARGET_HOST="${TARGET_HOST:-192.168.66.68}"
TARGET_USER="${TARGET_USER:-root}"
HA_CONFIG_DIR="/opt/homeassistant/config"
COMPOSE_FILE="docker-compose.ha.yml"
COMPOSE_REMOTE_PATH="/root/${COMPOSE_FILE}"
HA_PORT=8123
STARTUP_WAIT=10
MAX_HEALTH_RETRIES=30
HEALTH_RETRY_INTERVAL=2

# ---- 颜色输出 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $(date '+%H:%M:%S') $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $(date '+%H:%M:%S') $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*"; }

# ---- 清理函数 ----
cleanup() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        log_error "部署中断 (exit code: $exit_code)，请检查上方日志。"
    fi
}
trap cleanup EXIT

# ---- Step 0: 本地文件检查 ----
log_info "Step 0: 检查本地 docker-compose 文件..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_LOCAL="${SCRIPT_DIR}/${COMPOSE_FILE}"

if [ ! -f "$COMPOSE_LOCAL" ]; then
    log_error "未找到 ${COMPOSE_LOCAL}，请确保在 homebrain-v2 目录下运行本脚本。"
    exit 1
fi
log_info "  ✓ ${COMPOSE_LOCAL} 存在"

# ---- Step 1: SSH 可达性检查 ----
log_info "Step 1: 检查目标主机 SSH 可达性..."
if ! ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new "${TARGET_USER}@${TARGET_HOST}" "echo OK" &>/dev/null; then
    log_error "无法 SSH 连接到 ${TARGET_USER}@${TARGET_HOST}"
    log_error "请确认 SSH key 已配置: ssh-copy-id ${TARGET_USER}@${TARGET_HOST}"
    exit 1
fi
log_info "  ✓ ${TARGET_USER}@${TARGET_HOST} SSH 可达"

# ---- Step 2: 检查 Docker 环境 ----
log_info "Step 2: 检查目标主机 Docker 环境..."
DOCKER_INFO=$(ssh "${TARGET_USER}@${TARGET_HOST}" "docker --version 2>/dev/null && docker compose version 2>/dev/null" || true)
if [ -z "$DOCKER_INFO" ]; then
    log_error "目标主机未安装 Docker 或 docker compose 插件，请先安装。"
    exit 1
fi
log_info "  ✓ Docker 环境可用"

# ---- Step 3: 创建配置目录 ----
log_info "Step 3: 创建 HA 配置目录..."
ssh "${TARGET_USER}@${TARGET_HOST}" "mkdir -p ${HA_CONFIG_DIR}" || {
    log_error "无法创建 ${HA_CONFIG_DIR}"
    exit 1
}
log_info "  ✓ ${HA_CONFIG_DIR} 已就绪"

# ---- Step 4: 上传 docker-compose 文件 ----
log_info "Step 4: 上传 docker-compose.ha.yml..."
scp -q "${COMPOSE_LOCAL}" "${TARGET_USER}@${TARGET_HOST}:${COMPOSE_REMOTE_PATH}" || {
    log_error "scp 上传失败"
    exit 1
}
log_info "  ✓ ${COMPOSE_REMOTE_PATH} 已上传"

# ---- Step 5: 拉取镜像 ----
log_info "Step 5: 拉取 HA 官方镜像..."
ssh "${TARGET_USER}@${TARGET_HOST}" "docker compose -f ${COMPOSE_REMOTE_PATH} pull" || {
    log_error "镜像拉取失败，请检查网络连接。"
    exit 1
}
log_info "  ✓ 镜像拉取完成"

# ---- Step 6: 启动容器 ----
log_info "Step 6: 启动 Home Assistant Container..."
ssh "${TARGET_USER}@${TARGET_HOST}" "docker compose -f ${COMPOSE_REMOTE_PATH} up -d" || {
    log_error "容器启动失败"
    exit 1
}
log_info "  ✓ 容器启动命令已执行"

# ---- Step 7: 等待服务就绪 ----
log_info "Step 7: 等待 HA Web UI 就绪 (初始启动可能需要 1-3 分钟)..."
sleep "${STARTUP_WAIT}"

retry=0
while [ $retry -lt $MAX_HEALTH_RETRIES ]; do
    if ssh "${TARGET_USER}@${TARGET_HOST}" "curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 http://127.0.0.1:${HA_PORT}" | grep -q "200"; then
        log_info "  ✓ HA Web UI 返回 HTTP 200 (等待 ${retry}×${HEALTH_RETRY_INTERVAL}s)"
        break
    fi
    retry=$((retry + 1))
    sleep "${HEALTH_RETRY_INTERVAL}"
done

if [ $retry -ge $MAX_HEALTH_RETRIES ]; then
    log_error "HA Web UI 在 ${MAX_HEALTH_RETRIES} 次重试后仍未就绪"
    log_warn "这可能是正常的——首次启动需要更长时间初始化。"
    log_warn "请手动检查: ssh ${TARGET_USER}@${TARGET_HOST} 'docker logs homeassistant --tail 50'"
    # 不退出，继续输出摘要
fi

# ---- Step 8: 部署结果摘要 ----
echo ""
echo "============================================================================="
echo -e "  ${GREEN}部署结果摘要${NC}"
echo "============================================================================="
echo "  HA 容器:       $(ssh "${TARGET_USER}@${TARGET_HOST}" 'docker ps --filter name=homeassistant --format "{{.Status}}"' 2>/dev/null || echo '未知')"
echo "  HA Web UI:     http://${TARGET_HOST}:${HA_PORT}"
echo "  HA 配置目录:   ${TARGET_USER}@${TARGET_HOST}:${HA_CONFIG_DIR}"
echo ""
echo "  下一步 (需要先生操作):"
echo "  1. 打开 http://${TARGET_HOST}:${HA_PORT} 完成 HA 初始化向导"
echo "  2. 安装 HACS → 添加 xiaomi_home 插件"
echo "  3. 登录米家账号，同步设备"
echo "  4. 创建 Long-Lived Access Token (用于 HomeBrain 集成)"
echo ""
echo "  检查中枢网关 firmware:"
echo "    bash check-gateway-fw.sh"
echo "============================================================================="
