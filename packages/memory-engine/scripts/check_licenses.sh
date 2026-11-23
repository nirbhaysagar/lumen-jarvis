#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

echo "=== Running Lumen Memory Engine Dependency License Audit ==="

FAIL_ON="GNU General Public License v2 (GPLv2);GNU General Public License v3 (GPLv3);GNU Affero General Public License v3;GPL;AGPL;GNU General Public License;GNU Affero General Public License"

# 1. Check for forbidden copyleft licenses
"${ROOT_DIR}/.venv/bin/pip-licenses" --from=mixed --fail-on="${FAIL_ON}"

# 2. Export Markdown table to LICENSES.md
"${ROOT_DIR}/.venv/bin/pip-licenses" --from=mixed --format=markdown > "${SCRIPT_DIR}/../LICENSES.md"

echo "✅ License check passed: No GPL or AGPL licenses detected."
echo "📄 LICENSES.md updated."
