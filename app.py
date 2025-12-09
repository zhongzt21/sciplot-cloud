import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.ticker as ticker
import matplotlib.dates as mdates
from supabase import create_client
from datetime import datetime, timedelta
import re
import os
import requests
import numpy as np

# ================= 1. 配置区域 =================
SUPABASE_URL = "https://vetupomjinhylqpxnrhn.supabase.co"
SUPABASE_KEY = "sb_publishable_MpHqZeFn_U-lM19lpEBtMA_NR3Mx3mO"

TABLE_SENSORS = "sensor_measurements"
TABLE_RAIN = "weather_logs"

REGEX_PATTERN = re.compile(r"^([a-zA-Z0-9]+)(?:号)?([\u4e00-\u9fa5]+)\s+([\u4e00-\u9fa5]+)(?:[\(（](.+)[\)）])?(?:\.\d+)?$")

# --- 🎨 科研级配色盘 (Nature/Science 风格) ---
SCI_COLORS = ['#E64B35', '#4DBBD5', '#00A087', '#3C5488', '#F39B7F', '#8491B4', '#91D1C2', '#DC0000']

# ================= 2. 核心功能函数 =================
@st.cache_resource
def init_connection():
    if "你的_SUPABASE" in SUPABASE_URL:
        st.error("❌ 错误：请在代码第 15-16 行填入你自己的 Supabase URL 和 Key！")
        return None
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        st.error(f"❌ 数据库连接失败: {e}")
        return None

supabase = init_connection()

@st.cache_resource
def get_chinese_font():
    font_name = "SimHei.ttf"
    if not os.path.exists(font_name):
        try:
            url = "https://github.com/StellarCN/scp_zh/raw/master/fonts/SimHei.ttf"
            response = requests.get(url, timeout=5)
            with open(font_name, "wb") as f:
                f.write(response.content)
        except: pass
    try:
        return fm.FontProperties(fname=font_name)
    except: return None

zh_font = get_chinese_font()

# ================= 3. 数据智能处理 =================

def optimize_dataframe(df, time_col='timestamp'):
    """智能降采样：防止数据量过大导致截断或卡顿"""
    MAX_POINTS_PER_SERIES = 5000
    if df.empty: return df
    total_rows = len(df)
    if total_rows < MAX_POINTS_PER_SERIES: return df

    min_t = df[time_col].min()
    max_t = df[time_col].max()
    time_span = max_t - min_t
    
    if time_span.days > 365: rule = '1D'
    elif time_span.days > 90: rule = '6H'
    elif time_span.days > 30: rule = '1H'
    elif time_span.days > 7: rule = '30T'
    else: return df
    
    st.toast(f"💡 数据量较大 ({total_rows}行)，已优化显示粒度: {rule}", icon="⚡")
    df = df.set_index(time_col)
    # 按传感器ID和类型分组降采样
    resampled = df.groupby(['sensor_id', 'variable_type', 'unit'])['value'].resample(rule).mean().reset_index()
    return resampled

def get_sensor_data(start_time, end_time):
    if not supabase: return pd.DataFrame()
    try:
        # 扩大查询上限到 50万
        response = supabase.table(TABLE_SENSORS) \
            .select("timestamp, sensor_id, variable_type, value, unit") \
            .gte("timestamp", start_time.isoformat()) \
            .lte("timestamp", end_time.isoformat()) \
            .limit(500000) \
            .order("timestamp").execute()
        
        df = pd.DataFrame(response.data)
        if not df.empty:
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
            if df['timestamp'].dt.tz is not None:
                df['timestamp'] = df['timestamp'].dt.tz_localize(None)
            df['value'] = pd.to_numeric(df['value'], errors='coerce')
            df = df.dropna(subset=['timestamp', 'value'])
            df = optimize_dataframe(df) # 调用优化
        return df
    except Exception as e:
        st.sidebar.error(f"⚠️ 传感器读取失败: {e}")
        return pd.DataFrame()

def get_rainfall_data(start_time, end_time):
    if not supabase: return pd.DataFrame()
    try:
        # 扩大查询上限到 50万
        response = supabase.table(TABLE_RAIN).select("created_at, rain_intensity") \
            .gte("created_at", start_time.isoformat()) \
            .lte("created_at", end_time.isoformat()) \
            .limit(500000) \
            .order("created_at").execute()
        
        df = pd.DataFrame(response.data)
        if not df.empty:
            df = df.rename(columns={"created_at": "timestamp", "rain_intensity": "value"})
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
            if df['timestamp'].dt.tz is not None:
                df['timestamp'] = df['timestamp'].dt.tz_localize(None)
            df['value'] = pd.to_numeric(df['value'], errors='coerce')
            df = df.dropna(subset=['timestamp'])
            df = df.sort_values('timestamp')
        return df
    except Exception as e:
        st.sidebar.error(f"降雨读取失败: {e}")
        return pd.DataFrame()

def parse_excel_file(uploaded_file):
    try:
        df = pd.read_excel(uploaded_file, header=2)
    except Exception as e: return None, str(e)
    df.columns.values[0] = 'timestamp_fixed'
    processed_data = []
    for col_name in df.columns[1:]:
        col_str = str(col_name).strip()
        if col_str.startswith("原始数据") or "Unnamed" in col_str: continue
        match = REGEX_PATTERN.search(col_str)
        if match:
            raw_id, var_type, unit = match.group(1), match.group(2), match.group(4) if match.group(4) else ""
            final_sensor_id = f"{raw_id}号"
            current_series = pd.to_numeric(df[col_name], errors='coerce')
            for ts, val in zip(df['timestamp_fixed'], current_series):
                if pd.isna(ts): continue
                processed_data.append({"timestamp": ts.isoformat() if hasattr(ts, 'isoformat') else str(ts), "sensor_id": final_sensor_id, "variable_type": var_type, "unit": unit, "value": None if pd.isna(val) else float(val)})
    return processed_data, f"解析完成 {len(processed_data)} 条"

def upload_to_supabase(data_list):
    if not supabase: return False, "No Connection"
    try:
        for i in range(0, len(data_list), 500):
            supabase.table(TABLE_SENSORS).upsert(data_list[i:i+500], on_conflict="timestamp, sensor_id, variable_type", ignore_duplicates=True).execute()
        return True, "Done"
    except Exception as e: return False, str(e)

def process_data(series, window_size, spike_threshold):
    if spike_threshold > 0: series = series.where(series.diff().abs() < spike_threshold)
    if window_size > 1: series = series.rolling(window=window_size, min_periods=1, center=True).mean()
    return series

# ================= 4. 页面主程序 =================
st.set_page_config(page_title="SciPlot Cloud", layout="wide")
st.title("📊 SciPlot Cloud - 自动化科研绘图平台")

if not supabase: st.stop()

tab1, tab2 = st.tabs(["📈 数据绘图", "📂 数据上传"])

with tab1:
    with st.sidebar:
        st.header("1. 数据库侦探")
        if st.button("🔍 检测范围"):
            try:
                min_s = supabase.table(TABLE_SENSORS).select("timestamp").order("timestamp", desc=False).limit(1).execute()
                max_s = supabase.table(TABLE_SENSORS).select("timestamp").order("timestamp", desc=True).limit(1).execute()
                min_r = supabase.table(TABLE_RAIN).select("created_at").order("created_at", desc=False).limit(1).execute()
                max_r = supabase.table(TABLE_RAIN).select("created_at").order("created_at", desc=True).limit(1).execute()
                st.info(f"传感器: {min_s.data[0]['timestamp'][:10] if min_s.data else '无'} -> {max_s.data[0]['timestamp'][:10] if max_s.data else '无'}")
                st.info(f"降雨: {min_r.data[0]['created_at'][:10] if min_r.data else '无'} -> {max_r.data[0]['created_at'][:10] if max_r.data else '无'}")
            except: st.error("检测失败")

        st.markdown("---")
        st.header("2. 绘图控制")
        
        default_start = datetime.now() - timedelta(days=30)
        c1, c2 = st.columns(2)
        start_date = c1.date_input("开始日期", default_start)
        end_date = c2.date_input("结束日期", datetime.now())
        show_rainfall = st.checkbox("叠加降雨量", value=True)
        
        st.header("3. 数据清洗")
        ma_window = st.slider("平滑窗口", 1, 20, 1)
        spike_thresh = st.number_input("去噪阈值", 0.0, step=0.1)
        plot_mode = st.radio("分窗逻辑", ["按【号码】自动分窗", "按【物理量】自动分窗", "自定义选择"])
        
        st.markdown("---")
        fetch_btn = st.button("🔄 刷新图表数据", type="primary", use_container_width=True)

    if fetch_btn or 'raw_data' not in st.session_state:
        with st.spinner("🚀 正在从云端拉取并优化数据 (上限50万条)..."):
            t_start = datetime.combine(start_date, datetime.min.time())
            t_end = datetime.combine(end_date, datetime.max.time())
            
            df_sensor = get_sensor_data(t_start, t_end)
            df_rain = get_rainfall_data(t_start, t_end) if show_rainfall else pd.DataFrame()
            
            st.session_state['raw_data'] = df_sensor
            st.session_state['rain_data'] = df_rain
            
            if df_sensor.empty and df_rain.empty:
                st.sidebar.warning("⚠️ 此时间段无数据")
            else:
                st.sidebar.success(f"✅ 就绪: 传感器{len(df_sensor)}条, 降雨{len(df_rain)}条")

    if 'raw_data' in st.session_state:
        df = st.session_state['raw_data']
        df_rain = st.session_state.get('rain_data', pd.DataFrame())
        
        if not df.empty or not df_rain.empty:
            all_ids = sorted(df['sensor_id'].unique()) if not df.empty else []
            all_vars = sorted(df['variable_type'].unique()) if not df.empty else []
            plots_config = []

            if not df.empty:
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

            if df.empty and not df_rain.empty:
                plots_config.append({"title":"降雨量概览", "ids":[], "vars":[]})

            if st.button("🎨 生成图表", key="btn_plot", type="primary") and plots_config:
                num_plots = len(plots_config)
                cols_per_row = 1 if num_plots == 1 else 2 if num_plots <= 4 else 3
                
                for i in range(0, num_plots, cols_per_row):
                    cols = st.columns(cols_per_row)
                    for j in range(cols_per_row):
                        if i + j < num_plots:
                            config = plots_config[i + j]
                            with cols[j]:
                                # 黄金科研比例
                                fig, ax1 = plt.subplots(figsize=(10, 6)) 
                                
                                has_sensor_data = False
                                plotted_vars = set()
                                plotted_units = set()
                                color_idx = 0

                                # 1. 画左轴 (科研配色 + 折线)
                                if not df.empty:
                                    for sid in config['ids']:
                                        for vtype in config['vars']:
                                            sub = df[(df['sensor_id']==sid)&(df['variable_type']==vtype)].sort_values('timestamp')
                                            if not sub.empty:
                                                has_sensor_data = True
                                                y = process_data(sub['value'], ma_window, spike_thresh)
                                                unit = sub['unit'].iloc[0] if pd.notna(sub['unit'].iloc[0]) else ""
                                                plotted_vars.add(vtype)
                                                plotted_units.add(unit)
                                                
                                                line_color = SCI_COLORS[color_idx % len(SCI_COLORS)]
                                                color_idx += 1
                                                
                                                ax1.plot(sub['timestamp'], y, label=f"{sid}-{vtype} ({unit})", 
                                                         color=line_color, linewidth=1.5, alpha=0.9)
                                
                                # 2. 画右轴 (降雨 - 纯折线，无Marker)
                                ax2 = ax1.twinx()
                                has_rain_data = False
                                if show_rainfall and not df_rain.empty:
                                    # 【核心修改】：纯折线，去掉 marker
                                    ax2.plot(df_rain['timestamp'], df_rain['value'], 
                                             color='#3C5488', # 深蓝
                                             linestyle='-',   # 实线
                                             linewidth=1.5,   # 稍粗一点，防止太细看不清
                                             alpha=0.8,       # 不透明度稍高
                                             label='降雨量 (mm)')
                                    
                                    # 保持 Y 轴从 0 开始
                                    ax2.set_ylim(bottom=0)
                                    has_rain_data = True
                                
                                # === 样式精修 ===
                                fp = zh_font if zh_font else None
                                
                                if has_sensor_data:
                                    if len(plotted_vars) == 1 and len(plotted_units) == 1:
                                        y_label = f"{list(plotted_vars)[0]} ({list(plotted_units)[0]})"
                                    else:
                                        y_label = "数值 (Value)"
                                    ax1.set_ylabel(y_label, fontproperties=fp, fontsize=12)
                                else:
                                    ax1.set_yticks([])
                                
                                ax1.set_xlabel("时间 (Time)", fontproperties=fp, fontsize=12)
                                ax1.xaxis.set_major_locator(ticker.MaxNLocator(nbins=6)) 
                                ax1.set_title(config['title'], fontproperties=fp, fontsize=14, fontweight='bold', pad=10)
                                
                                ax1.tick_params(axis='both', direction='in', which='both', top=True, right=False, labeltop=False, labelright=False)
                                ax2.tick_params(axis='y', direction='in', right=True, labelright=False)
                                
                                if has_rain_data:
                                    ax2.set_ylabel("降雨量 (mm)", fontproperties=fp, fontsize=12)
                                else:
                                    ax2.set_yticks([])
                                
                                ax1.grid(True, linestyle=':', alpha=0.3)
                                
                                if has_sensor_data or has_rain_data:
                                    lines1, labels1 = ax1.get_legend_handles_labels()
                                    lines2, labels2 = ax2.get_legend_handles_labels()
                                    leg = ax1.legend(lines1 + lines2, labels1 + labels2, loc='best', frameon=False)
                                    if fp:
                                        for text in leg.get_texts(): text.set_fontproperties(fp)
                                
                                st.pyplot(fig)

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



