## MARS 社交营销模拟控制台

此目录包含一套 Streamlit 面板，用于编排干预、维护态度指标并启动 OASIS 社交营销模拟。代码与数据被拆分为两个并列文件夹：

- `simulation/`：所有模拟脚本（`oasis_test_grouping.py`、干预/评估模块等）。
- `data/`：运行数据，如 `.env`、`oasis_agent_init.csv` 与 `oasis_database.db`。

### 快速上手
编辑 `run.sh` 中的 `MARS_MODEL_BASE_URL`、`MARS_MODEL_API_KEY` 行即可替换模型服务配置。例如：

```bash
export MARS_MODEL_BASE_URL=""
export MARS_MODEL_API_KEY=""
```
```bash
cd Individual_Simulation_Platform/marketing_simulation
bash run.sh
```

### run.sh 做了什么？

1. 自动定位自身目录，设定 `SIM_DIR=.../simulation`、`DATA_DIR=.../data`，并在缺失时创建数据目录。
2. 导出面板运行所需的环境变量：
	- `MARS_SIMULATION_DIR` / `MARS_SIM_DATA_DIR`：指向模拟脚本所在目录。
	- `MARS_PROFILE_PATH`：默认使用 `data/oasis_agent_init.csv`。
	- `MARS_DB_PATH`：默认使用 `data/oasis_database.db`。
	- `MARS_INTERVENTION_PATH`：指向 `simulation/intervention_messages.csv`。
	- `MARS_ENV_FILE`：设置为 `data/.env`，用于存储 API 密钥。
	- `MARS_MODEL_BASE_URL`：OpenAI 兼容接口地址，由 `run.sh` 设置。
	- `MARS_MODEL_API_KEY`：对应接口的密钥，同样在 `run.sh` 中配置。
3. 使用绝对路径执行 `streamlit_app.py`，因此可在任意位置调用该脚本。



