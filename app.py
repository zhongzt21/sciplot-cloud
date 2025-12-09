import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from supabase import create_client
from datetime import datetime, timedelta
import re
import os
import requests

# ================= 1. 配置区域 =================
SUPABASE_URL = "https://vetupomjinhylqpxnrhn.supabase.co"
SUPABASE_KEY = "sb_publishable_MpHqZeFn_U-lM19lpEBtMA_NR3Mx3mO"

TABLE_SENSORS = "sensor_measurements"
TABLE_RAIN = "weather_logs"

# 正则表达式
REGEX_PATTERN = re.compile(r"^([a-zA-Z0-9]+)(?:号)?([\u4e00-\u9fa5]+)\s+([\u4e00-\u9fa5]+)(?:[\(（](.+)[\)）])?(?:\.\d+)?$")

# ================= 2. 核心功能函数 (优先加载数据库) =================
@st.cache_resource
def init_connection():
    """初始化数据库连接，带详细报错"""
    # 检查用户是否忘了填 Key
    if "你的_SUPABASE" in SUPABASE_URL:
        st.error("❌ 错误：请在代码第 12-13 行填入你自己的 Supabase URL 和 Key！")
        return None
        
    try:
        client = create_client(SUPABASE_URL, SUPABASE_KEY)
        return client
    except Exception as e:
        st.error(f"❌ 数据库连接失败: {e}")
        return None

supabase = init_connection()

# ================= 3. 字体修复 (非阻塞模式) =================
@st.cache_resource
def get_chinese_font():
    """尝试获取中文字体，失败则静默跳过，不卡死程序"""
    font_name = "SimHei.ttf"
    if not os.path.exists(font_name):
        try:
            # 尝试下载
            url = "https://github.com/StellarCN/scp_zh/raw/master/fonts/SimHei.ttf"
            response = requests.get(url, timeout=5) # 5秒超时，防止卡死
            with open(font_name, "wb") as f:
                f.write(response.content)
        except:
            # 下载失败也不报错，直接返回 None，保证程序能跑
            pass

    try:
        return fm.FontProperties(fname=font_name)
    except:
        return None

zh_font = get_chinese_font()

# ================= 4. 数据处理逻辑 =================
def parse_excel_file(uploaded_file):
    try:
        df = pd.read_excel(uploaded_file, header=2)
    except Exception as e:
        return None, f"文件读取失败: {e}"

    df.columns.values[0] = 'timestamp_fixed'
    processed_data = []
    
    for col_name in df.columns[1:]:
        col_str = str(col_name).strip()
        if col_str.startswith("原始数据") or "Unnamed" in col_str:
            continue

        match = REGEX_PATTERN.search(col_str)
        if match:
            raw_id = match.group(1)
            var_type = match.group(2)
            unit = match.group(4) if match.group(4) else ""
            final_sensor_id = f"{raw_id}号"
            
            current_series = pd.to_numeric(df[col_name], errors='coerce')
            
            for ts, val in zip(df['timestamp_fixed'], current_series):
                if pd.isna(ts): continue
                processed_data.append({
                    "timestamp": ts.isoformat() if hasattr(ts, 'isoformat') else str(ts),
                    "sensor_id": final_sensor_id,
                    "variable_type": var_type,
                    "unit": unit,
                    "value": None if pd.isna(val) else float(val)
                })
    return processed_data, f"解析完成，提取到 {len(processed_data)} 条数据"

def upload_to_supabase(data_list):
    if not supabase: return False, "数据库未连接"
    batch_size = 500
    total = len(data_list)
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    try:
        for i in range(0, total, batch_size):
            batch = data_list[i:i+batch_size]
            supabase.table(TABLE_SENSORS).upsert(
                batch, 
                on_conflict="timestamp, sensor_id, variable_type", 
                ignore_duplicates=True
            ).execute()
            progress = min((i + batch_size) / total, 1.0)
            progress_bar.progress(progress)
            status_text.text(f"正在上传... {int(progress*100)}%")
        status_text.text("✅ 上传完成！")
        return True, "成功写入数据库"
    except Exception as e:
        return False, f"上传中断: {e}"

def get_sensor_data(start_time, end_time):
    if not supabase: return pd.DataFrame()
    try:
        response = supabase.table(TABLE_SENSORS).select("*").gte("timestamp", start_time.isoformat()).lte("timestamp", end_time.isoformat()).order("timestamp").execute()
        df = pd.DataFrame(response.data)
        if not df.empty:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df['value'] = pd.to_numeric(df['value'], errors='coerce')
        return df
    except Exception as e:
        # ⚠️ 这里会把具体的查询错误显示出来
        st.sidebar.error(f"查询出错: {e}")
        return pd.DataFrame()

def get_rainfall_data(start_time, end_time):
    if not supabase: return pd.DataFrame()
    try:
        response = supabase.table(TABLE_RAIN).select("created_at, rain_intensity").gte("created_at", start_time.isoformat()).lte("created_at", end_time.isoformat()).order("created_at").execute()
        df = pd.DataFrame(response.data)
        if not df.empty:
            df = df.rename(columns={"created_at": "timestamp", "rain_intensity": "value"})
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df['value'] = pd.to_numeric(df['value'], errors='coerce')
        return df
    except: return pd.DataFrame()

def process_data(series, window_size, spike_threshold):
    if spike_threshold > 0:
        diff = series.diff().abs()
        mask = diff < spike_threshold
        series = series.where(mask)
    if window_size > 1:
        series = series.rolling(window=window_size, min_periods=1, center=True).mean()
    return series

# ================= 5. 页面主程序 =================
st.set_page_config(page_title="SciPlot Cloud", layout="wide")
st.title("📊 SciPlot Cloud - 自动化科研绘图平台")

# --- 状态检查 ---
if not supabase:
    st.warning("⚠️ 数据库未连接，请检查代码配置。")
    st.stop() # 停止运行后续代码

tab1, tab2 = st.tabs(["📈 数据绘图", "📂 数据上传"])

# --- TAB 1: 绘图功能 ---
with tab1:
    with st.sidebar:
        st.header("1. 绘图控制")
        c1, c2 = st.columns(2)
        # 默认查询改为最近 30 天，防止查不到数据
        start_date = c1.date_input("开始日期", datetime.now() - timedelta(days=30))
        end_date = c2.date_input("结束日期", datetime.now())
        show_rainfall = st.checkbox("叠加降雨量", value=True)
        
        st.header("2. 数据清洗")
        ma_window = st.slider("平滑窗口", 1, 20, 1)
        spike_thresh = st.number_input("去噪阈值", 0.0, step=0.1)

        st.header("3. 模式选择")
        plot_mode = st.radio("分窗逻辑", ["按【号码】自动分窗", "按【物理量】自动分窗", "自定义选择"])
        
        st.markdown("---")
        fetch_btn = st.button("🔄 刷新图表数据", type="primary", use_container_width=True)

    # 数据加载
    if fetch_btn or 'raw_data' not in st.session_state:
        with st.spinner("正在连接数据库查询..."):
            t_start = datetime.combine(start_date, datetime.min.time())
            t_end = datetime.combine(end_date, datetime.max.time())
            
            df_sensor = get_sensor_data(t_start, t_end)
            df_rain = get_rainfall_data(t_start, t_end) if show_rainfall else pd.DataFrame()
            
            st.session_state['raw_data'] = df_sensor
            st.session_state['rain_data'] = df_rain
            
            if df_sensor.empty:
                st.sidebar.warning(f"⚠️ 在 {start_date} 至 {end_date} 期间未找到数据。请尝试调整日期范围。")
            else:
                st.sidebar.success(f"✅ 已加载 {len(df_sensor)} 条数据")

    # 绘图逻辑
    if 'raw_data' in st.session_state and not st.session_state['raw_data'].empty:
        df = st.session_state['raw_data']
        df_rain = st.session_state.get('rain_data', pd.DataFrame())
        
        all_ids = sorted(df['sensor_id'].unique())
        all_vars = sorted(df['variable_type'].unique())
        plots_config = []

        # 配置逻辑
        if plot_mode == "自定义选择":
            num = st.number_input("窗口数量", 1, 10, 1)
            for i in range(num):
                c1, c2 = st.columns(2)
                ids = c1.multiselect(f"图{i+1} 号码", all_ids, key=f"id{i}")
                vars_ = c2.multiselect(f"图{i+1} 物理量", all_vars, key=f"v{i}")
                if ids and vars_: plots_config.append({"title":f"自定义窗口 {i+1}","ids":ids,"vars":vars_})
        elif plot_mode == "按【号码】自动分窗":
            t_ids = st.multiselect("选择号码", all_ids, default=all_ids)
            t_vars = st.multiselect("选择物理量", all_vars, default=all_vars)
            for sid in t_ids: plots_config.append({"title":f"{sid} 数据","ids":[sid],"vars":t_vars})
        elif plot_mode == "按【物理量】自动分窗":
            t_vars = st.multiselect("选择物理量", all_vars, default=all_vars)
            t_ids = st.multiselect("选择号码", all_ids, default=all_ids)
            for v in t_vars: plots_config.append({"title":f"{v} 对比","ids":t_ids,"vars":[v]})

        # === 核心绘图部分 ===
        if st.button("🎨 生成图表", key="btn_plot", type="primary") and plots_config:
            
            # --- 智能网格布局 ---
            num_plots = len(plots_config)
            if num_plots == 1: cols_per_row = 1
            elif num_plots <= 4: cols_per_row = 2
            else: cols_per_row = 3
            
            for i in range(0, num_plots, cols_per_row):
                cols = st.columns(cols_per_row)
                for j in range(cols_per_row):
                    if i + j < num_plots:
                        config = plots_config[i + j]
                        with cols[j]:
                            fig, ax1 = plt.subplots(figsize=(8, 6)) 
                            
                            has_data = False
                            for sid in config['ids']:
                                for vtype in config['vars']:
                                    sub = df[(df['sensor_id']==sid)&(df['variable_type']==vtype)].sort_values('timestamp')
                                    if not sub.empty:
                                        has_data = True
                                        y = process_data(sub['value'], ma_window, spike_thresh)
                                        ax1.plot(sub['timestamp'], y, label=f"{sid}-{vtype}", linewidth=1.5)
                            
                            ax2 = ax1.twinx()
                            if show_rainfall and not df_rain.empty:
                                ax2.plot(df_rain['timestamp'], df_rain['value'], color='#1f77b4', linestyle='--', alpha=0.4, label='降雨量')
                            
                            # === 样式精修 (带字体回退保护) ===
                            # 如果 zh_font 下载失败，这里的 fontproperties 传 None 就不会报错，只是显示回方框
                            fp = zh_font if zh_font else None
                            
                            ax1.set_xlabel("时间 (Time)", fontproperties=fp, fontsize=12)
                            ax1.set_ylabel("数值 (Value)", fontproperties=fp, fontsize=12)
                            ax1.set_title(config['title'], fontproperties=fp, fontsize=14, fontweight='bold', pad=10)
                            
                            ax1.tick_params(axis='both', direction='in', which='both', top=True, right=False, labeltop=False, labelright=False)
                            ax2.tick_params(axis='y', direction='in', right=True, labelright=False)
                            ax2.set_ylabel("") 
                            ax1.tick_params(axis='x', top=True, labeltop=False)
                            ax1.grid(True, linestyle=':', alpha=0.3)
                            
                            if has_data:
                                lines1, labels1 = ax1.get_legend_handles_labels()
                                if show_rainfall:
                                    lines2, labels2 = ax2.get_legend_handles_labels()
                                    leg = ax1.legend(lines1 + lines2, labels1 + labels2, loc='best', frameon=False)
                                else:
                                    leg = ax1.legend(loc='best', frameon=False)
                                
                                if fp:
                                    for text in leg.get_texts():
                                        text.set_fontproperties(fp)
                            
                            st.pyplot(fig)

# --- TAB 2: 数据上传 ---
with tab2:
    st.header("📂 上传新的 Excel 数据文件")
    uploaded_file = st.file_uploader("拖拽文件到此处", type=['xls', 'xlsx'])
    if uploaded_file:
        data, msg = parse_excel_file(uploaded_file)
        if data:
            st.success(msg)
            if st.button("🚀 确认上传"):
                success, upload_msg = upload_to_supabase(data)
                if success: st.success(upload_msg)
                else: st.error(upload_msg)
        else:
            st.error(msg)

