#!/usr/bin/env bash
# 把 moleplan skill 安装到 Hermes 的 skills 目录。
#
#   ./skills/moleplan/install.sh
#
# 目标目录可用 HERMES_SKILLS_DIR 覆盖(默认 ~/.hermes/skills/productivity/moleplan)。
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${HERMES_SKILLS_DIR:-$HOME/.hermes/skills/productivity/moleplan}"

mkdir -p "$DEST/scripts"
cp "$SRC/SKILL.md" "$DEST/SKILL.md"
cp "$SRC/scripts/moleplan.py" "$DEST/scripts/moleplan.py"
chmod +x "$DEST/scripts/moleplan.py"

echo "已安装到 $DEST"
echo
echo "接下来在 agent 的环境里配置密钥(在管理后台「Agent 密钥」页签发):"
echo "  MOLEPLAN_API_KEY=mpk_xxxx"
echo
echo "Hermes 用户把上面这行写进 ~/.hermes/.env,然后自检:"
echo "  hermes skills list | grep moleplan"
echo "  python3 $DEST/scripts/moleplan.py whoami"
