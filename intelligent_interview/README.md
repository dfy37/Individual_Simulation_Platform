# robust_interview_demo

该目录符合 `DEMO_NAME/demo` 结构：
- 源码在 `demo/`
- 可直接独立运行，不依赖上级项目目录

## 启动
```bash
cd demo
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.sample.json config.json  # 填入你自己的 API Key
streamlit run app.py --server.port 8501
```

## 说明
- 预设回放数据来自 `demo/outputs_v2`（已做本地相对路径重写）
- `demo/examples/preset` 样例已内置为实际目录（非软链接）
- 请勿提交 `demo/config.json`
