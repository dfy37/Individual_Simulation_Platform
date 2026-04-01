"""
后端常量与默认配置
"""
from pathlib import Path

# ── 目录 ──────────────────────────────────────────────────
BACKEND_DIR   = Path(__file__).parent                          # backend/
RESULTS_DIR   = BACKEND_DIR / "results"
PROFILES_PATH       = BACKEND_DIR / "users" / "student_profiles_expanded.json"
RELATIONSHIPS_PATH  = BACKEND_DIR / "users" / "relationships.json"

RESULTS_DIR.mkdir(exist_ok=True)

# ── 复旦校区附近 AOI ID ────────────────────────────────────
# 邯郸校区（lat≈31.2985, lon≈121.5037）附近
# 文科图书馆 / 学生活动中心 / 思源楼 / 史带楼 / 校医院
# 理科图书馆 / 烈士雕塑纪念广场 / 东区学生公寓 / 南区学生公寓 / 北区学生公寓
HANDAN_AOI_IDS = [
    500060674, 500059682, 500063519, 500063520, 500059675,
    500059685, 500063512, 500063531, 500063533, 500062299,
]
# 江湾校区（lat≈31.3345, lon≈121.4963）附近
# 江湾校区主AOI / 江湾生活园区 / 第二附属学校
# 悠方购物中心 / 新江湾城体育中心 / 新江湾城滑板公园
# 新江湾尚景园 / 祥生御江湾 / 嘉誉湾 / campus内无名AOI
JIANGWAN_AOI_IDS = [
    500059140, 500060511, 500062064,
    500059283, 500059233, 500059273,
    500060512, 500060513, 500061419, 500061394,
]
FUDAN_AOI_IDS = HANDAN_AOI_IDS + JIANGWAN_AOI_IDS

# ── 仿真参数默认值 ─────────────────────────────────────────
DEFAULT_PARAMS = {
    "num_agents":   10,
    "num_steps":    12,
    "tick_seconds": 3600,
    "concurrency":  5,
    "start_time":   "2024-09-02 08:00:00",
}

# ── Agent 回退坐标（复旦邯郸校区）─────────────────────────
FALLBACK_LNG = 121.503
FALLBACK_LAT = 31.298
