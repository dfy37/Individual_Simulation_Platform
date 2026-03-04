import json
import os

import pandas as pd
import streamlit as st

from db import get_conn, init_db
from preset_module import render_preset_module
from live_module import render_live_module

st.set_page_config(page_title="智能访谈", page_icon="🧭", layout="wide")
ASSETS_FLOW_DIR = os.path.join(os.path.dirname(__file__), "assets", "flow")


def _load_css() -> None:
    css_path = os.path.join(os.path.dirname(__file__), "styles.css")
    if os.path.exists(css_path):
        with open(css_path, "r", encoding="utf-8") as f:
            st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)


def render_intro() -> None:
    st.title("智能访谈")
    st.caption("面向突发事件干扰场景的个体访谈系统：持续获取可追溯、高价值个体信息。")

    st.markdown("### 项目目标")
    st.markdown(
        "本项目聚焦“含突发事件的个体访谈”：在拒答、情绪波动、前后矛盾、低配合等干扰下，"
        "系统仍尝试通过阶段规划与恢复策略推进访谈，尽量减少信息流失。"
    )
    st.markdown(
        "输出数据不仅包含最终答案，还包含事件触发轨迹、应对策略、恢复效果与过程日志，"
        "用于更全面地理解个体的行为倾向、表达习惯与决策模式。"
    )

    st.markdown("## 核心能力")
    st.markdown(
        """
<div class="cap-grid">
  <div class="cap-card">
    <h4>事件识别</h4>
    <p>识别拒答、跑题、情绪波动与矛盾信息，标注事件类型与发生轮次。</p>
  </div>
  <div class="cap-card">
    <h4>阶段推进</h4>
    <p>按阶段目标规划问题顺序，不被问卷顺序死绑定，支持动态调整提问策略。</p>
  </div>
  <div class="cap-card">
    <h4>恢复机制</h4>
    <p>在异常后执行安抚、改问、回收与澄清，尽量把 deferred 信息拉回可用答案。</p>
  </div>
  <div class="cap-card">
    <h4>过程追溯</h4>
    <p>完整记录状态、动作、策略、事件与追问决策，便于复盘与研究分析。</p>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


#    st.markdown("### 工作流总览")
#    svg_path = os.path.join(ASSETS_FLOW_DIR, "auto_interview_pipeline.svg")
#    png_path = os.path.join(ASSETS_FLOW_DIR, "auto_interview_pipeline.png")
#    if os.path.exists(svg_path):
#        with open(svg_path, "r", encoding="utf-8") as f:
#            st.markdown(
#                f"<div class='flow-image-wrap'><div class='flow-svg-host'>{f.read()}</div></div>",
#                unsafe_allow_html=True,
#            )
#    elif os.path.exists(png_path):
#        st.image(png_path, use_container_width=True)
#    else:
#        st.info("流程图资源未找到，请先在 assets/flow 放置 svg 或 png。")

    st.markdown("### 如何使用")
    st.markdown(
        """
<div class="use-grid">
  <div class="use-card">
    <h4>1) 预设回放：观察系统与AI扮演的受访者之间的对话</h4>
    <ul>
      <li>查看事件如何触发：拒答、矛盾、低配合等。</li>
      <li>查看每轮策略：继续追问、改问、延后回收还是收束。</li>
      <li>对比最终回填与真实答案，定位误差来源。</li>
    </ul>
  </div>
  <div class="use-card">
    <h4>2) 实时访谈：让系统对真实用户做深度采集</h4>
    <ul>
      <li>与访谈员直接对话，系统实时推进阶段目标。</li>
      <li>出现不想回答时，系统优先尝试低压恢复，不直接中断。</li>
      <li>会话结束自动产出结构化结果与个体画像。</li>
    </ul>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def render_dashboard(conn) -> None:
    st.subheader("研究数据看板")

    sessions = pd.read_sql_query("SELECT * FROM sessions ORDER BY id DESC", conn)
    msgs = pd.read_sql_query("SELECT * FROM messages ORDER BY id DESC", conn)
    feedback = pd.read_sql_query("SELECT * FROM feedback ORDER BY id DESC", conn)

    c1, c2, c3 = st.columns(3)
    c1.metric("会话数", len(sessions))
    c2.metric("消息数", len(msgs))
    c3.metric("反馈数", len(feedback))

    if not sessions.empty:
        st.markdown("#### 最近会话")
        st.dataframe(sessions.head(20), use_container_width=True)

    if not msgs.empty:
        st.markdown("#### 事件分布")
        evt = msgs[msgs["event_type"].notna() & (msgs["event_type"] != "none")]
        if not evt.empty:
            st.bar_chart(evt["event_type"].value_counts())
        else:
            st.info("当前暂无异常事件数据。")

    if not feedback.empty:
        st.markdown("#### 回访评分")
        st.dataframe(feedback.head(20), use_container_width=True)

    with st.expander("导出提示", expanded=False):
        st.code(
            "sqlite3 demo_data/demo.db '.tables'\n"
            "sqlite3 demo_data/demo.db 'select * from sessions limit 10;'"
        )



def main() -> None:
    _load_css()

    conn = get_conn()
    init_db(conn)

    show_dashboard = os.environ.get("DEMO_SHOW_DASHBOARD", "false").strip().lower() in {"1", "true", "yes"}

    tab_names = ["项目介绍", "预设回放", "实时访谈"]
    if show_dashboard:
        tab_names.append("数据看板")
    tabs = st.tabs(tab_names)

    with tabs[0]:
        render_intro()
    with tabs[1]:
        render_preset_module(conn)
    with tabs[2]:
        render_live_module(conn)
    if show_dashboard and len(tabs) > 3:
        with tabs[3]:
            render_dashboard(conn)


if __name__ == "__main__":
    main()
