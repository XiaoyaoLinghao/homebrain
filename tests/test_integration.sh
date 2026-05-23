#!/usr/bin/env bash
# =============================================================================
# HomeBrain v2.0 — 集成测试脚本
# 验证 HA Bridge、Scene Engine、LLM Adapter 的端到端连通性
# =============================================================================

set -euo pipefail

# ---- Configuration ----
HA_API_URL="${HA_API_URL:-http://192.168.66.68:8123}"
HA_API_TOKEN="${HA_API_TOKEN:-}"
DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TEST_REPORT="${SCRIPT_DIR}/integration_report_$(date +%Y%m%d_%H%M%S).txt"
PASS=0
FAIL=0
SKIP=0

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# ---- Helpers ----
log_section() {
    echo "" | tee -a "$TEST_REPORT"
    echo "============================================================" | tee -a "$TEST_REPORT"
    echo "  $1" | tee -a "$TEST_REPORT"
    echo "============================================================" | tee -a "$TEST_REPORT"
}

pass() {
    echo -e "  ${GREEN}[PASS]${NC} $1" | tee -a "$TEST_REPORT"
    PASS=$((PASS + 1))
}

fail() {
    echo -e "  ${RED}[FAIL]${NC} $1 — $2" | tee -a "$TEST_REPORT"
    FAIL=$((FAIL + 1))
}

skip() {
    echo -e "  ${YELLOW}[SKIP]${NC} $1 — $2" | tee -a "$TEST_REPORT"
    SKIP=$((SKIP + 1))
}

summary() {
    echo "" | tee -a "$TEST_REPORT"
    echo "============================================================" | tee -a "$TEST_REPORT"
    echo "  TEST SUMMARY" | tee -a "$TEST_REPORT"
    echo "============================================================" | tee -a "$TEST_REPORT"
    echo -e "  ${GREEN}Passed:${NC} $PASS" | tee -a "$TEST_REPORT"
    echo -e "  ${RED}Failed:${NC} $FAIL" | tee -a "$TEST_REPORT"
    echo -e "  ${YELLOW}Skipped:${NC} $SKIP" | tee -a "$TEST_REPORT"
    echo "  Total:  $((PASS + FAIL + SKIP))" | tee -a "$TEST_REPORT"
    echo "" | tee -a "$TEST_REPORT"
    echo "  Report saved to: $TEST_REPORT" | tee -a "$TEST_REPORT"
}

# ---- Init ----
> "$TEST_REPORT"
echo "HomeBrain v2.0 Integration Test" | tee -a "$TEST_REPORT"
echo "Date: $(date)" | tee -a "$TEST_REPORT"
echo "HA API URL: $HA_API_URL" | tee -a "$TEST_REPORT"
echo "" | tee -a "$TEST_REPORT"

# =============================================================================
# 1. Prerequisites Check
# =============================================================================
log_section "1. Prerequisites"

if command -v python3 &>/dev/null; then
    pass "python3 available: $(python3 --version)"
else
    fail "python3 not found" ""
fi

if python3 -c "import pytest" 2>/dev/null; then
    pass "pytest available"
else
    fail "pytest not installed" "pip install pytest"
fi

if python3 -c "import aiohttp" 2>/dev/null; then
    pass "aiohttp available"
else
    fail "aiohttp not installed" "pip install aiohttp"
fi

# =============================================================================
# 2. HA API Connectivity
# =============================================================================
log_section "2. HA API Connectivity"

if [ -z "$HA_API_TOKEN" ]; then
    skip "HA_API_TOKEN not set" "export HA_API_TOKEN=..."
else
    # Test HA API root
    HA_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Authorization: Bearer $HA_API_TOKEN" \
        "$HA_API_URL/api/" 2>/dev/null || echo "000")

    if [ "$HA_RESPONSE" = "200" ]; then
        pass "HA API root accessible (HTTP $HA_RESPONSE)"
    else
        fail "HA API root unreachable" "HTTP $HA_RESPONSE from $HA_API_URL/api/"
    fi

    # Test entities list
    ENTITIES_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Authorization: Bearer $HA_API_TOKEN" \
        "$HA_API_URL/api/states" 2>/dev/null || echo "000")

    if [ "$ENTITIES_RESPONSE" = "200" ]; then
        ENTITY_COUNT=$(curl -s \
            -H "Authorization: Bearer $HA_API_TOKEN" \
            "$HA_API_URL/api/states" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
        pass "HA states API accessible — $ENTITY_COUNT entities found"
    else
        fail "HA states API unreachable" "HTTP $ENTITIES_RESPONSE"
    fi

    # Test services list
    SERVICES_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Authorization: Bearer $HA_API_TOKEN" \
        "$HA_API_URL/api/services" 2>/dev/null || echo "000")

    if [ "$SERVICES_RESPONSE" = "200" ]; then
        pass "HA services API accessible"
    else
        fail "HA services API unreachable" "HTTP $SERVICES_RESPONSE"
    fi
fi

# =============================================================================
# 3. HA Bridge Module Tests
# =============================================================================
log_section "3. HA Bridge Module"

cd "$PROJECT_DIR/src"

if python3 -m pytest ha_bridge/tests/test_ha_bridge.py -v --tb=short 2>&1 | tee -a "$TEST_REPORT"; then
    pass "HA Bridge unit tests passed"
else
    fail "HA Bridge unit tests failed" "see report for details"
fi

# =============================================================================
# 4. Scene Engine Smoke Test
# =============================================================================
log_section "4. Scene Engine"

if python3 -m pytest scene_engine/tests/test_engine.py -v --tb=short 2>&1 | tee -a "$TEST_REPORT"; then
    pass "Scene Engine unit tests passed"
else
    fail "Scene Engine unit tests failed" "see report for details"
fi

# Quick rule parse smoke test
RULES_FILE="$PROJECT_DIR/src/scene_engine/rules_example.yaml"
if [ -f "$RULES_FILE" ]; then
    if python3 -c "
import yaml, sys
with open('$RULES_FILE') as f:
    rules = yaml.safe_load(f)
print(f'Parsed {len(rules.get(\"rules\", rules) if isinstance(rules, dict) else rules)} rule(s)')
sys.exit(0)
" 2>/dev/null; then
        pass "Scene Engine — rules YAML parseable"
    else
        fail "Scene Engine — YAML parse failed" "check $RULES_FILE"
    fi
else
    skip "Scene Engine — no rules YAML found" ""
fi

# =============================================================================
# 5. LLM Adapter Smoke Test
# =============================================================================
log_section "5. LLM Adapter"

if python3 -m pytest llm_adapter/tests/test_adapter.py -v --tb=short 2>&1 | tee -a "$TEST_REPORT"; then
    pass "LLM Adapter unit tests passed"
else
    fail "LLM Adapter unit tests failed" "see report for details"
fi

# DeepSeek API connectivity (requires key)
if [ -z "$DEEPSEEK_API_KEY" ]; then
    skip "LLM Adapter live test skipped" "DEEPSEEK_API_KEY not set"
else
    LLM_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
        -H "Content-Type: application/json" \
        -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"hello"}],"max_tokens":10}' \
        "https://api.deepseek.com/v1/chat/completions" 2>/dev/null || echo "000")

    if [ "$LLM_RESPONSE" = "200" ]; then
        pass "DeepSeek API accessible (HTTP $LLM_RESPONSE)"
    else
        fail "DeepSeek API unreachable" "HTTP $LLM_RESPONSE"
    fi
fi

# =============================================================================
# 6. Module Import Chain
# =============================================================================
log_section "6. Module Import Chain"

cd "$PROJECT_DIR/src"

if python3 -c "
from ha_bridge import HAClient
from scene_engine import SceneEngine
from scene_engine.ha_transport import HATransport
from llm_adapter import LLMAdapter
from llm_adapter.ha_context import HAContextBuilder
print('All core modules imported successfully')
" 2>&1 | tee -a "$TEST_REPORT"; then
    pass "All core modules import cleanly"
else
    fail "Module import chain broken" "check traceback above"
fi

# =============================================================================
# Summary
# =============================================================================
summary

if [ $FAIL -gt 0 ]; then
    exit 1
fi
exit 0
