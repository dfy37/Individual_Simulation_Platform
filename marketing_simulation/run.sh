#!/bin/bash

# 1. 设置仿真目录与数据目录（脚本自动定位当前文件夹）
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}" )" && pwd)"
SIM_DIR="${SCRIPT_DIR}/simulation"
DATA_DIR="${SCRIPT_DIR}/data"

mkdir -p "${DATA_DIR}"

export MARS_SIMULATION_DIR="${SIM_DIR}"
export MARS_SIM_DATA_DIR="${SIM_DIR}"   # 兼容旧版变量
export MARS_PROFILE_PATH="${DATA_DIR}/oasis_agent_init.csv"
export MARS_ENV_FILE="${DATA_DIR}/.env"
export MARS_DB_PATH="${DATA_DIR}/oasis_database.db"
export MARS_INTERVENTION_PATH="${SIM_DIR}/intervention_messages.csv"
export MARS_MODEL_BASE_URL="${MARS_MODEL_BASE_URL:-https://api.example.com/v1}"  # 请按需修改
export MARS_MODEL_API_KEY="${MARS_MODEL_API_KEY:-$DEFAULT_API_KEY}"  # 请替换为真实 key

# 2. (可选) 检查并安装缺失依赖，如需自动安装请取消下方注释
# pip install streamlit pandas python-dotenv openai tqdm camel-python torch transformers scipy matplotlib

# 3. 启动 Streamlit 应用
echo "🚀 正在启动 MARS 社交营销模拟控制台..."
echo "脚本目录: $MARS_SIMULATION_DIR"
echo "数据目录: $DATA_DIR"
echo "模型 API: $MARS_MODEL_BASE_URL"
if [[ "$MARS_MODEL_API_KEY" == "$DEFAULT_API_KEY" ]]; then
	echo "⚠️ 未配置 MARS_MODEL_API_KEY，请编辑 run.sh 填写真实密钥。" >&2
else
	echo "模型密钥: 已加载"
fi

streamlit run "${SCRIPT_DIR}/streamlit_app.py"