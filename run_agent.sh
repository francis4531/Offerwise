#!/bin/bash
#
# OfferWise Autonomous Test Runner
# ================================
# Quick start script for running automated tests
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║     OfferWise Autonomous Test Agent v2.0                  ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Check for required tools
echo "Checking dependencies..."

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Python3 not found. Please install Python 3.8+${NC}"
    exit 1
fi

# Check Python packages
python3 -c "import playwright" 2>/dev/null || {
    echo -e "${YELLOW}Installing playwright...${NC}"
    pip install playwright
    playwright install chromium
}

python3 -c "import anthropic" 2>/dev/null || {
    echo -e "${YELLOW}Installing anthropic...${NC}"
    pip install anthropic
}

python3 -c "from fpdf import FPDF" 2>/dev/null || {
    echo -e "${YELLOW}Installing fpdf2...${NC}"
    pip install fpdf2
}

echo -e "${GREEN}✓ All dependencies installed${NC}"

# Check for API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo -e "${YELLOW}"
    echo "⚠️  ANTHROPIC_API_KEY not set"
    echo "   The agent can still run but won't use Claude for smart decisions."
    echo "   Set it with: export ANTHROPIC_API_KEY=sk-..."
    echo -e "${NC}"
fi

# Get URL
if [ -z "$1" ]; then
    echo -e "${YELLOW}Usage: ./run_agent.sh <url> [count] [concurrency]${NC}"
    echo ""
    echo "Examples:"
    echo "  ./run_agent.sh https://offerwise.com              # 100 tests, 5 concurrent"
    echo "  ./run_agent.sh https://offerwise.com 50           # 50 tests, 5 concurrent"  
    echo "  ./run_agent.sh https://offerwise.com 100 10       # 100 tests, 10 concurrent"
    echo ""
    exit 1
fi

URL=$1
COUNT=${2:-100}
CONCURRENCY=${3:-5}

echo ""
echo -e "${GREEN}Configuration:${NC}"
echo "  URL:         $URL"
echo "  Tests:       $COUNT"
echo "  Concurrency: $CONCURRENCY browsers"
echo ""

# Create output directories
mkdir -p test_reports screenshots

# Run the agent
echo -e "${GREEN}🚀 Starting autonomous test agent...${NC}"
echo ""

python3 agent_autonomous.py \
    --url "$URL" \
    --count "$COUNT" \
    --concurrency "$CONCURRENCY"

echo ""
echo -e "${GREEN}✅ Test run complete!${NC}"
echo ""
echo "View results:"
echo "  • MTurk Dashboard: $URL/admin/turk"
echo "  • JSON Report: ./test_reports/"
echo ""
