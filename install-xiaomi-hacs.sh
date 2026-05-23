#!/usr/bin/env bash
# =============================================================================
# install-xiaomi-hacs.sh — 安装 xiaomi_home 插件到 Home Assistant
# =============================================================================
# 用途: 将小米官方 xiaomi_home 插件安装到 HA custom_components 目录
# 方案: 直接从 GitHub 克隆并复制 (比 HACS 安装更直接可靠)
# 目标: 192.168.66.68 上的 HA Docker 容器
# =============================================================================
set -euo pipefail

# ---- 配置 ----
TARGET_HOST="${TARGET_HOST:-192.168.66.68}"
TARGET_USER="${TARGET_USER:-root}"
HA_CONFIG_DIR="/opt/homeassistant/config"
HA_CUSTOM_COMPONENTS="${HA_CONFIG_DIR}/custom_components"
PLUGIN_REPO="https://github.com/XiaoMi/ha_xiaomi_home.git"
PLUGIN_NAME="xiaomi_home"
PLUGIN_CLONE_DIR="/tmp/ha_xiaomi_home"
HA_CONTAINER="homeassistant"

# ---- 颜色输出 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $(date '+%H:%M:%S') $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $(date '+%H:%M:%S') $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date '+%H:%M:%S') $*"; }
log_step()  { echo -e "${CYAN}[STEP]${NC}  $(date '+%H:%M:%S') $*"; }

# ---- 清理函数 ----
cleanup() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        log_error "安装中断 (exit code: $exit_code)"
        log_info "可手动重试: bash $0"
    fi
}
trap cleanup EXIT

# =============================================================================
# Step 1: 检查前置条件
# =============================================================================
log_step "Step 1/6: 检查前置条件"

# 1a: 检查目标主机 SSH
log_info "  检查 SSH 连接 ${TARGET_USER}@${TARGET_HOST}..."
if ! ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new "${TARGET_USER}@${TARGET_HOST}" "echo OK" &>/dev/null; then
    log_error "无法 SSH 连接到 ${TARGET_USER}@${TARGET_HOST}"
    log_error "请先配置 SSH key: ssh-copy-id ${TARGET_USER}@${TARGET_HOST}"
    exit 1
fi
log_info "  ✓ SSH 可达"

# 1b: 检查 HA 容器是否运行
log_info "  检查 HA 容器状态..."
HA_STATUS=$(ssh "${TARGET_USER}@${TARGET_HOST}" "docker ps --filter name=${HA_CONTAINER} --format '{{.Status}}'" 2>/dev/null || true)
if [ -z "$HA_STATUS" ]; then
    log_error "HA 容器未运行。请先部署 HA 再安装插件。"
    log_info "  部署命令: bash deploy-ha.sh"
    exit 1
fi
log_info "  ✓ HA 容器状态: ${HA_STATUS}"

# 1c: 检查 custom_components 目录
log_info "  检查 custom_components 目录..."
ssh "${TARGET_USER}@${TARGET_HOST}" "mkdir -p ${HA_CUSTOM_COMPONENTS}" || {
    log_error "无法创建 ${HA_CUSTOM_COMPONENTS}"
    exit 1
}
log_info "  ✓ ${HA_CUSTOM_COMPONENTS} 已就绪"

# =============================================================================
# Step 2: 清理旧版本（如果存在）
# =============================================================================
log_step "Step 2/6: 检查 & 清理旧版本"

EXISTING_PLUGIN=$(ssh "${TARGET_USER}@${TARGET_HOST}" "test -d ${HA_CUSTOM_COMPONENTS}/${PLUGIN_NAME} && echo 'YES' || echo 'NO'" 2>/dev/null)
if [ "$EXISTING_PLUGIN" = "YES" ]; then
    log_warn "检测到已有 ${PLUGIN_NAME} 安装，将备份并移除..."
    BACKUP_NAME="${HA_CUSTOM_COMPONENTS}/${PLUGIN_NAME}.bak.$(date +%Y%m%d_%H%M%S)"
    ssh "${TARGET_USER}@${TARGET_HOST}" "mv ${HA_CUSTOM_COMPONENTS}/${PLUGIN_NAME} ${BACKUP_NAME}" || {
        log_error "无法备份旧版本"
        exit 1
    }
    log_info "  ✓ 旧版本已备份到: ${BACKUP_NAME}"
else
    log_info "  ✓ 未检测到已有安装"
fi

# =============================================================================
# Step 3: 克隆官方仓库
# =============================================================================
log_step "Step 3/6: 克隆 xiaomi_home 官方仓库"

ssh "${TARGET_USER}@${TARGET_HOST}" "rm -rf ${PLUGIN_CLONE_DIR}"
ssh "${TARGET_USER}@${TARGET_HOST}" "git clone --depth 1 ${PLUGIN_REPO} ${PLUGIN_CLONE_DIR}" || {
    log_error "GitHub 克隆失败，请检查网络连接。"
    log_info "  如果 GitHub 不可达，可尝试镜像:"
    log_info "  git clone https://hub.fastgit.xyz/XiaoMi/ha_xiaomi_home.git ${PLUGIN_CLONE_DIR}"
    exit 1
}
log_info "  ✓ 仓库已克隆到 ${PLUGIN_CLONE_DIR}"

# =============================================================================
# Step 4: 安装插件到 custom_components
# =============================================================================
log_step "Step 4/6: 安装 xiaomi_home 插件"

# 验证仓库包含 custom_components 目录
PLUGIN_SRC="${PLUGIN_CLONE_DIR}/custom_components/${PLUGIN_NAME}"
PLUGIN_EXISTS=$(ssh "${TARGET_USER}@${TARGET_HOST}" "test -d ${PLUGIN_SRC} && echo 'YES' || echo 'NO'" 2>/dev/null)
if [ "$PLUGIN_EXISTS" != "YES" ]; then
    log_error "仓库中未找到 ${PLUGIN_SRC}"
    log_error "仓库结构可能已变更，请检查: ${PLUGIN_REPO}"
    exit 1
fi

ssh "${TARGET_USER}@${TARGET_HOST}" "cp -r ${PLUGIN_SRC} ${HA_CUSTOM_COMPONENTS}/" || {
    log_error "复制插件文件失败"
    exit 1
}
log_info "  ✓ 插件已安装到 ${HA_CUSTOM_COMPONENTS}/${PLUGIN_NAME}"

# 验证安装
INSTALLED_FILES=$(ssh "${TARGET_USER}@${TARGET_HOST}" "find ${HA_CUSTOM_COMPONENTS}/${PLUGIN_NAME} -type f | wc -l" 2>/dev/null || echo "0")
log_info "  ✓ 已安装 ${INSTALLED_FILES} 个文件"

# 检查关键文件
KEY_FILES=("manifest.json" "__init__.py" "config_flow.py")
for f in "${KEY_FILES[@]}"; do
    FOUND=$(ssh "${TARGET_USER}@${TARGET_HOST}" "test -f ${HA_CUSTOM_COMPONENTS}/${PLUGIN_NAME}/${f} && echo 'YES' || echo 'NO'")
    if [ "$FOUND" != "YES" ]; then
        log_warn "  ⚠ 关键文件 ${f} 未找到，插件可能不完整"
    else
        log_info "  ✓ ${f} 已确认"
    fi
done

# =============================================================================
# Step 5: 重启 Home Assistant
# =============================================================================
log_step "Step 5/6: 重启 HA 容器加载插件"

log_info "  正在重启 ${HA_CONTAINER}..."
ssh "${TARGET_USER}@${TARGET_HOST}" "docker restart ${HA_CONTAINER}" || {
    log_error "HA 重启失败"
    log_info "  尝试手动重启: ssh ${TARGET_USER}@${TARGET_HOST} 'docker restart ${HA_CONTAINER}'"
    exit 1
}
log_info "  ✓ 重启命令已执行"

# 等待 HA 启动
log_info "  等待 HA 重新就绪 (最多 90 秒)..."
WAIT_INITIAL=10
sleep "${WAIT_INITIAL}"

MAX_RETRIES=40
RETRY_INTERVAL=2
retry=0
HA_READY=false

while [ $retry -lt $MAX_RETRIES ]; do
    HTTP_CODE=$(ssh "${TARGET_USER}@${TARGET_HOST}" "curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 http://127.0.0.1:8123 2>/dev/null" || echo "000")
    if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "302" ] || [ "$HTTP_CODE" = "405" ]; then
        HA_READY=true
        log_info "  ✓ HA Web UI 已就绪 (HTTP ${HTTP_CODE}, 等待 ${retry}×${RETRY_INTERVAL}s)"
        break
    fi
    retry=$((retry + 1))
    sleep "${RETRY_INTERVAL}"
done

if [ "$HA_READY" = false ]; then
    log_warn "HA 在 ${MAX_RETRIES} 次重试后仍未完全就绪"
    log_warn "建议手动检查: ssh ${TARGET_USER}@${TARGET_HOST} 'docker logs ${HA_CONTAINER} --tail 50'"
    log_info "插件已安装，HA 启动后会自动加载。"
fi

# =============================================================================
# Step 6: 清理临时文件 & 输出摘要
# =============================================================================
log_step "Step 6/6: 清理 & 摘要"

ssh "${TARGET_USER}@${TARGET_HOST}" "rm -rf ${PLUGIN_CLONE_DIR}" 2>/dev/null || true
log_info "  ✓ 临时文件已清理"

echo ""
echo "============================================================================="
echo -e "  ${GREEN}xiaomi_home 插件安装完成${NC}"
echo "============================================================================="
echo ""
echo "  插件路径:     ${TARGET_USER}@${TARGET_HOST}:${HA_CUSTOM_COMPONENTS}/${PLUGIN_NAME}"
echo "  HA Web UI:    http://${TARGET_HOST}:8123"
echo ""
echo "  ═══════════════════════════════════════════════════════════════════"
echo "  下一步 — 请先生在 HA Web UI 操作:"
echo "  ═══════════════════════════════════════════════════════════════════"
echo ""
echo "  1. 打开 http://${TARGET_HOST}:8123 登录（首次需创建账户）"
echo "  2. 设置 (Settings) → 设备与服务 (Devices & Services)"
echo "  3. 右下角「添加集成」(ADD INTEGRATION)"
echo "  4. 搜索 "Xiaomi Home" → 选择并点击"
echo "  5. 点击 "Click here to login" → 小米 OAuth 登录"
echo "  6. 选择米家家庭 → 等待设备导入完成"
echo ""
echo "  详细操作指南: cat HA-SETUP-GUIDE.md"
echo ""
echo "============================================================================="
