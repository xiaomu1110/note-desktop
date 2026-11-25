#!/usr/bin/env bash
# 发布 note-desktop v1.0.0 到 GitHub
#
# 前提：
#   1. gh auth status 显示已登录
#   2. 能访问 github.com（代理要开全局或规则里包含 github）
#
# 用法：
#   bash scripts/publish.sh            # 建仓 + 推送 + 建 Release
#   bash scripts/publish.sh --check    # 只检查前置条件

set -uo pipefail

REPO_NAME="note-desktop"
TAG="v1.0.0"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASSETS=(
  "$ROOT/dist/note-desktop.exe"
  "$ROOT/dist/note_desktop-1.0.0-py3-none-any.whl"
  "$ROOT/dist/note-desktop-mcp.zip"
  "$ROOT/dist/note-desktop-skill.zip"
)

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
info()  { printf '\033[36m%s\033[0m\n' "$*"; }

# ---------------------------------------------------------------- 前置检查

info "== 1. 检查前置条件 =="

if ! command -v gh >/dev/null 2>&1; then
  red "✗ 没装 gh CLI"
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  red "✗ gh 未登录。先运行：gh auth login"
  gh auth status 2>&1 | sed 's/^/    /'
  exit 1
fi
green "✓ gh 已登录"

ACCOUNT=$(gh api user --jq .login 2>/dev/null || echo "")
if [ -z "$ACCOUNT" ]; then
  red "✗ 连不上 GitHub API（代理/网络问题）"
  exit 1
fi
green "✓ GitHub 可达，账号：$ACCOUNT"

missing=0
for f in "${ASSETS[@]}"; do
  if [ ! -f "$f" ]; then
    red "✗ 缺少构建产物：$f"
    missing=1
  fi
done
if [ "$missing" -eq 1 ]; then
  red "  先构建：pyinstaller note-desktop.spec --noconfirm && python -m build --wheel"
  exit 1
fi
green "✓ 4 个产物齐全"

if [ "${1:-}" = "--check" ]; then
  green "前置条件全部满足"
  exit 0
fi

# ---------------------------------------------------------------- 建仓

info ""
info "== 2. 创建仓库 $ACCOUNT/$REPO_NAME =="
cd "$ROOT"

if gh repo view "$ACCOUNT/$REPO_NAME" >/dev/null 2>&1; then
  green "  仓库已存在，跳过创建"
else
  gh repo create "$REPO_NAME" --public \
    --description "常驻桌面、可拖动的极简便签，零第三方依赖，带本地 API 与 MCP" \
    --source=. --remote=origin 2>&1 | sed 's/^/    /'
  green "  已创建"
fi

# ---------------------------------------------------------------- 推送

info ""
info "== 3. 推送代码与 tag =="
if ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin "https://github.com/$ACCOUNT/$REPO_NAME.git"
fi

git push -u origin main 2>&1 | sed 's/^/    /' || { red "✗ main 推送失败"; exit 1; }
git push origin "$TAG" 2>&1 | sed 's/^/    /'   || { red "✗ tag 推送失败"; exit 1; }
green "  ✓ 已推送 main 与 $TAG"

# ---------------------------------------------------------------- Release

info ""
info "== 4. 创建 Release $TAG =="
if gh release view "$TAG" >/dev/null 2>&1; then
  green "  Release 已存在，改为追加资源"
  gh release upload "$TAG" "${ASSETS[@]}" --clobber 2>&1 | sed 's/^/    /'
else
  gh release create "$TAG" "${ASSETS[@]}" \
    --title "note-desktop v1.0.0" \
    --notes-file "$ROOT/.github/RELEASE_NOTES.md" 2>&1 | sed 's/^/    /' || {
      red "✗ Release 创建失败"; exit 1; }
fi

info ""
green "== 完成 =="
gh release view "$TAG" --json url,assets \
  --jq '"  Release: " + .url, ( .assets[] | "  - " + .name + "  " + ((.size/1024/1024*10|round)/10|tostring) + " MB" )'