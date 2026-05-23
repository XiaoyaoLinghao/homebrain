#!/usr/bin/env bash
# =============================================================================
# save-ha-token.sh — 保存 Home Assistant Long-Lived Access Token
# =============================================================================
# 用途: 交互式读取 HA API Token，安全写入 HA 配置目录
# 用法: bash save-ha-token.sh
#       或远程执行: ssh root@192.168.66.68 'bash -s' < save-ha-token.sh
# =============================================================================
set -euo pipefail

# ---- 配置 ----
TARGET_HOST="${TARGET_HOST:-192.168.66.68}"
TARGET_USER="${TARGET_USER:-root}"
TOKEN_FILE="/opt/homeassistant/config/homebrain-token.txt"
HA_CONTAINER="homeassistant"

# ---- 颜色输出 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

echo ""
echo "╔═══════════════════════════════════════════════════════════════════════════╗"
echo "║          Home Assistant Long-Lived Access Token 保存工具                  ║"
echo "╚═══════════════════════════════════════════════════════════════════════════╝"
echo ""

# =============================================================================
# Step 1: 获取 Token 的指南
# =============================================================================
echo -e "${CYAN}━━━ 如何获取 HA Long-Lived Access Token ━━━${NC}"
echo ""
echo "  在 HA Web UI 中操作:"
echo "  1. 打开 http://${TARGET_HOST}:8123 并登录"
echo "  2. 点击左下角你的用户名（Profile）"
echo "  3. 滚动到最底部 → 安全 (Security)"
echo "  4. 点击「长期访问令牌」(Long-Lived Access Tokens)"
echo "  5. 点击「创建令牌」(CREATE TOKEN)"
echo "  6. 输入名称，如: HomeBrain"
echo "  7. 点击「确定」→ 复制生成的 Token"
echo ""
echo -e "  ${YELLOW}⚠️  Token 只显示一次，请务必保存！${NC}"
echo ""

# =============================================================================
# Step 2: 安全读取 Token
# =============================================================================
echo -e "${CYAN}━━━ 输入 Token ━━━${NC}"
echo ""

# 使用 read -s 隐藏输入
read -r -s -p "  请粘贴 HA Token (输入不可见): " HA_TOKEN
echo ""
echo ""

# 验证 Token 不为空
if [ -z "$HA_TOKEN" ]; then
    log_error "Token 不能为空。请重新运行本脚本。"
    exit 1
fi

# Token 格式验证（HA token 通常以 . 分隔，base64 编码）
if [[ ! "$HA_TOKEN" =~ ^[A-Za-z0-9._=+\/-]+$ ]]; then
    log_warn "Token 格式看起来不标准，但将继续保存。"
    log_warn "标准 HA Token 格式: 仅包含 A-Za-z0-9._=+-/ 字符。"
fi

# =============================================================================
# Step 3: 写入文件
# =============================================================================
echo -e "${CYAN}━━━ 写入 Token ━━━${NC}"
echo ""

# 写入 TOKEN_FILE
echo "${HA_TOKEN}" > "${TOKEN_FILE}"

if [ $? -ne 0 ]; then
    log_error "无法写入 ${TOKEN_FILE}"
    log_error "请检查目录权限: ls -ld $(dirname ${TOKEN_FILE})"
    exit 1
fi

# 设置权限 600（仅 owner 可读写）
chmod 600 "${TOKEN_FILE}"
log_info "  ✓ Token 已写入: ${TOKEN_FILE}"
log_info "  ✓ 权限已设置为 600 (仅 root 可读写)"

# =============================================================================
# Step 4: 验证 Token 有效性
# =============================================================================
echo ""
echo -e "${CYAN}━━━ 验证 Token 有效性 ━━━${NC}"
echo ""

log_info "正在测试 Token 是否有效..."

# 尝试调用 HA REST API
HTTP_CODE=$(curl -s -o /dev/null -w '%{http_code}' \
    -H "Authorization: Bearer ${HA_TOKEN}" \
    --connect-timeout 5 \
    "http://127.0.0.1:8123/api/" 2>/dev/null || echo "000")

if [ "$HTTP_CODE" = "200" ]; then
    log_info "  ✓ Token 验证通过！HA API 返回 HTTP 200"
elif [ "$HTTP_CODE" = "000" ]; then
    log_warn "  ⚠ 无法连接到 HA API (http://127.0.0.1:8123)"
    log_warn "  HA 容器是否在运行？运行 docker ps | grep ${HA_CONTAINER} 检查"
    log_warn "  Token 已保存，可在 HA 启动后手动验证"
else
    log_warn "  ⚠ HA API 返回 HTTP ${HTTP_CODE}"
    log_warn "  Token 可能无效或 HA 尚未完成初始化（需要先创建账户）"
    log_warn "  Token 已保存，请在 HA Web UI 创建账户后重新验证"
fi

# =============================================================================
# Step 5: 输出配置指南
# =============================================================================
echo ""
echo "============================================================================="
echo -e "  ${GREEN}Token 保存完成${NC}"
echo "============================================================================="
echo ""
echo "  Token 文件: ${TOKEN_FILE} (权限 600)"
echo ""
echo "  ═══════════════════════════════════════════════════════════════════"
echo "  HomeBrain .env 配置"
echo "  ═══════════════════════════════════════════════════════════════════"
echo ""
echo "  在 HomeBrain 的 .env 文件中添加以下行:"
echo ""
echo "    HA_API_TOKEN=${HA_TOKEN}"
echo "    HA_API_URL=http://${TARGET_HOST}:8123"
echo ""
echo "  如果 HA 和 HomeBrain 在同一 Docker 网络，可改为:"
echo ""
echo "    HA_API_TOKEN=${HA_TOKEN}"
echo "    HA_API_URL=http://homeassistant:8123"
echo ""
echo "  ═══════════════════════════════════════════════════════════════════"
echo "  安全提醒"
echo "  ═══════════════════════════════════════════════════════════════════"
echo ""
echo "  • Token 在 ${TOKEN_FILE} 中权限为 600 (仅 root 可读)"
echo "  • 禁止将 Token 提交到 Git 仓库"
echo "  • 禁止在代码中硬编码 Token"
echo "  • 如果 Token 泄露，在 HA Web UI 中立即吊销并重新创建"
echo ""
echo "============================================================================="

# 清除 shell 变量中的 Token
HA_TOKEN=""
