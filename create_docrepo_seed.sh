#!/bin/bash
# Create a seed archive for the persistent disk.
# Run once locally, then upload via admin dashboard "Seed Disk" button.
#
# Usage: ./create_docrepo_seed.sh
# Output: docrepo_files.tar.gz
#
# The archive contains all document files but NOT metadata/catalog.json
# (catalog ships with the app code, files live on the persistent disk)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$SCRIPT_DIR/document_repo"

if [ ! -d "$REPO_DIR" ]; then
    echo "❌ document_repo/ not found at $REPO_DIR"
    exit 1
fi

echo "📦 Creating seed archive from $REPO_DIR..."

# Create archive excluding metadata (already in repo) and hidden files
tar czf "$SCRIPT_DIR/docrepo_files.tar.gz" \
    -C "$SCRIPT_DIR" \
    --exclude='document_repo/metadata' \
    --exclude='document_repo/anonymized' \
    --exclude='.*' \
    document_repo/

SIZE=$(du -sh "$SCRIPT_DIR/docrepo_files.tar.gz" | cut -f1)
COUNT=$(tar tzf "$SCRIPT_DIR/docrepo_files.tar.gz" | grep -v '/$' | wc -l)

echo "✅ Created docrepo_files.tar.gz ($SIZE, $COUNT files)"
echo ""
echo "To seed the persistent disk:"
echo "  1. Go to Admin Dashboard → Doc Repo tab → 💾 Seed Disk button"
echo "  2. Select docrepo_files.tar.gz"
echo ""
echo "Or via curl:"
echo "  curl -X POST -F 'archive=@docrepo_files.tar.gz' \\"
echo "    'https://getofferwise.ai/api/docrepo/seed?admin_key=YOUR_KEY'"
