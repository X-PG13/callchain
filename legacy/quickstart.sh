#!/usr/bin/env bash
# 一键分析目录：生成调用图 → 结构化索引 → 位置增强 → 枚举调用链

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "用法: $0 <analysis_root|owner/repo[:subdir]> [restrict_dir] [out_prefix]"
  echo "说明: analysis_root 是要分析的目录；也支持传 GitHub 仓库 owner/repo 或 owner/repo:subdir 自动拉取。"
  echo "      restrict_dir 是枚举调用链时的筛选子目录，默认当前分析目录。"
  echo "示例: $0 test_repos/smoke_repo . smoke_repo"
  echo "示例: $0 some_repo src my_core"
  echo "示例: $0 pallets/click:src/click . click_core"
  exit 2
fi

INPUT_SPEC="$1"
RESTRICT_DIR="${2:-.}"

# 定位工程根与脚本位置
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="$ROOT_DIR/scripts"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
DOWNLOAD_ROOT="$ROOT_DIR/downloaded_repos"

if [ -x "$VENV_PYTHON" ]; then
  PYTHON_BIN="$VENV_PYTHON"
else
  PYTHON_BIN="${PYTHON:-python3}"
fi

REPO_ROOT="$INPUT_SPEC"
if [ ! -d "$REPO_ROOT" ]; then
  REPO_SLUG=""
  REPO_SUBDIR="."

  if [[ "$INPUT_SPEC" =~ ^https?://github\.com/([^/]+)/([^/:]+?)(\.git)?(/tree/[^/]+/(.+))?$ ]]; then
    REPO_SLUG="${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
    if [ -n "${BASH_REMATCH[5]:-}" ]; then
      REPO_SUBDIR="${BASH_REMATCH[5]}"
    fi
  elif [[ "$INPUT_SPEC" =~ ^([^/]+)/([^/:]+)(:(.+))?$ ]]; then
    REPO_SLUG="${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
    if [ -n "${BASH_REMATCH[4]:-}" ]; then
      REPO_SUBDIR="${BASH_REMATCH[4]}"
    fi
  fi

  if [ -n "$REPO_SLUG" ]; then
    OWNER="${REPO_SLUG%%/*}"
    REPO_NAME="${REPO_SLUG##*/}"
    LOCAL_REPO_DIR="$DOWNLOAD_ROOT/$OWNER/$REPO_NAME"
    REMOTE_URL="https://github.com/$REPO_SLUG.git"

    mkdir -p "$DOWNLOAD_ROOT/$OWNER"
    if [ ! -d "$LOCAL_REPO_DIR/.git" ]; then
      echo "[INFO] 拉取 GitHub 仓库: $REPO_SLUG"
      git clone --depth 1 "$REMOTE_URL" "$LOCAL_REPO_DIR"
    else
      echo "[INFO] 复用已存在的仓库副本: $LOCAL_REPO_DIR"
    fi

    REPO_ROOT="$LOCAL_REPO_DIR/$REPO_SUBDIR"
  fi
fi

if [ ! -d "$REPO_ROOT" ]; then
  echo "[ERROR] 分析目录不存在: $REPO_ROOT"
  exit 2
fi

OUT_PREFIX="${3:-$(basename "$REPO_ROOT")}"  # 默认使用分析目录名

# 输出目录
test_output_dir="$ROOT_DIR/test_output"
mkdir -p "$test_output_dir"

CALLGRAPH_JSON="$test_output_dir/${OUT_PREFIX}_callgraph.json"
STRUCT_JSON="$test_output_dir/${OUT_PREFIX}_structure.json"
POSITIONS_JSON="$test_output_dir/${OUT_PREFIX}_positions.json"
ABS_REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"
ABS_RESTRICT_DIR="$(cd "$REPO_ROOT/$RESTRICT_DIR" && pwd)"
RESTRICT_BASENAME="$(basename "$ABS_RESTRICT_DIR")"

echo "[1/4] 生成调用图: $CALLGRAPH_JSON"
"$PYTHON_BIN" "$SCRIPTS_DIR/run_scalpel_callgraph.py" \
  --package "$REPO_ROOT" \
  --out "$CALLGRAPH_JSON"

echo "[2/4] 生成结构化索引: $STRUCT_JSON"
"$PYTHON_BIN" "$SCRIPTS_DIR/code_extractor.py" "$REPO_ROOT" "$STRUCT_JSON"

echo "[3/4] 位置增强: $POSITIONS_JSON"
"$PYTHON_BIN" "$SCRIPTS_DIR/pycg_jedi_positions.py" \
  --callgraph "$CALLGRAPH_JSON" \
  --structured "$STRUCT_JSON" \
  --project-root "$REPO_ROOT" \
  --output "$POSITIONS_JSON"

echo "[4/4] 枚举调用链"
if [[ "$RESTRICT_BASENAME" == "tests" || "$RESTRICT_BASENAME" == "test" || "$RESTRICT_BASENAME" == "testing" ]]; then
  "$PYTHON_BIN" "$SCRIPTS_DIR/enumerate_call_chains.py" \
    --input "$POSITIONS_JSON" \
    --restrict-dir "$ABS_RESTRICT_DIR" \
    --output "$test_output_dir/${OUT_PREFIX}_chains.jsonl" \
    --summary "$test_output_dir/${OUT_PREFIX}_chains_summary.json" \
    --max-depth 40 \
    --only-cross-file \
    --head-in-restrict-has-outside
else
  "$PYTHON_BIN" "$SCRIPTS_DIR/enumerate_call_chains.py" \
    --input "$POSITIONS_JSON" \
    --restrict-dir "$ABS_RESTRICT_DIR" \
    --output "$test_output_dir/${OUT_PREFIX}_chains.jsonl" \
    --summary "$test_output_dir/${OUT_PREFIX}_chains_summary.json" \
    --max-depth 40 \
    --only-cross-file
fi

echo "完成。链条输出: $test_output_dir/${OUT_PREFIX}_chains.jsonl"
echo "摘要输出: $test_output_dir/${OUT_PREFIX}_chains_summary.json"

# 若严格筛选下链为空，自动运行一次放宽筛选
SUMMARY_JSON="$test_output_dir/${OUT_PREFIX}_chains_summary.json"
NEED_RELAXED=$(SUMMARY_JSON="$SUMMARY_JSON" "$PYTHON_BIN" - <<'PY'
import json, os
p = os.environ.get('SUMMARY_JSON', '')
try:
    with open(p, 'r', encoding='utf-8') as f:
        d = json.load(f)
    print('1' if int(d.get('chains_written', 0)) == 0 else '0')
except Exception:
    # 解析失败时也做一次放宽尝试
    print('1')
PY
)

if [ "$NEED_RELAXED" = "1" ]; then
  echo "检测到当前筛选下链为空，尝试运行放宽筛选（不要求跨文件，不强制链中包含筛选目录外节点）。"
  "$PYTHON_BIN" "$SCRIPTS_DIR/enumerate_call_chains.py" \
    --input "$POSITIONS_JSON" \
    --restrict-dir "$ABS_RESTRICT_DIR" \
    --output "$test_output_dir/${OUT_PREFIX}_chains_relaxed.jsonl" \
    --summary "$test_output_dir/${OUT_PREFIX}_chains_relaxed_summary.json" \
    --max-depth 40
  echo "放宽筛选完成。链条输出: $test_output_dir/${OUT_PREFIX}_chains_relaxed.jsonl"
  echo "摘要输出: $test_output_dir/${OUT_PREFIX}_chains_relaxed_summary.json"
fi
