"""Streamlit 仪表盘入口。

启动方式：
    cd superHealth
    PYTHONPATH=src streamlit run src/superhealth/dashboard/app.py --server.port=8505
"""

from superhealth.log_config import setup_logging

setup_logging()

import streamlit as st

st.set_page_config(
    page_title="健康仪表盘",
    page_icon="❤️",
    layout="wide",
    initial_sidebar_state="expanded",
)


def _login_gate() -> bool:
    """返回 True 表示已通过验证。"""
    if st.session_state.get("authenticated"):
        return True

    from superhealth.config import load as load_config
    from superhealth.config import save_dashboard_password, verify_password

    config = load_config()
    stored = config.dashboard.password
    if not stored:  # 未配置密码则直接放行
        st.session_state["authenticated"] = True
        return True

    st.title("健康仪表盘")
    st.caption("请输入访问密码")
    pwd = st.text_input(
        "密码",
        type="password",
        value="",
        key="login_pwd",
    )
    remember = st.checkbox(
        "记住密码", key="login_remember", value=False
    )
    if st.button("登录", key="login_btn"):
        if verify_password(pwd, stored):
            st.session_state["authenticated"] = True
            if remember:
                save_dashboard_password(pwd)
            else:
                save_dashboard_password("")
            st.rerun()
        else:
            st.error("密码错误")
    return False


if not _login_gate():
    st.stop()


from superhealth.dashboard.views import (
    config_page,
    correlations,
    experiment_page,
    export,
    historical_review,
    lab_results,
    overview,
    prediction,
    preferences_page,
    trends,
)

PAGES = {
    "今日概览": overview,
    "历史回顾": historical_review,
    "个人偏好": preferences_page,
    "实验追踪": experiment_page,
    "状态趋势": trends,
    "化验趋势": lab_results,
    "相关性分析": correlations,
    "预测分析": prediction,
    "报告导出": export,
    "系统配置": config_page,
}

with st.sidebar:
    st.title("健康仪表盘")
    st.divider()
    page_name = st.radio("导航", list(PAGES.keys()))
    st.divider()

    from superhealth.config import load as load_config

    cfg = load_config()
    if cfg.dashboard.password and st.session_state.get("authenticated"):
        if st.button("退出登录", key="logout_btn"):
            st.session_state.pop("authenticated", None)
            # 清理旧版可能残留在 URL 中的 token
            if "token" in st.query_params:
                st.query_params.pop("token")
            st.rerun()

PAGES[page_name].render()
