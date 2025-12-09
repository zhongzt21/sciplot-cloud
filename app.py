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

# ================= 1. 配置区域 =================
SUPABASE_URL = "https://vetupomjinhylqpxnrhn.supabase.co"
SUPABASE_KEY = "sb_publishable_MpHqZeFn_U-lM19lpEBtMA_NR3Mx3mO"

TABLE_SENSORS = "sensor_measurements"
TABLE_RAIN = "weather_logs"

REGEX_PATTERN = re.compile(r"^([a-zA-Z0-9]+)(?:号)?([\u4e00-\u9fa5]+)\s+([\u4e00-\u9fa5]+)(?:[\(（](.+)[\)）])?(?:\.\d+)?$")

# ================= 2. 核心功能函数 =================
@st.cache_resource
def init_connection():
    if "你的_SUPABASE" in SUPABASE_URL:
        st.error("❌ 错误：请在代码第 13-14 行填入你自己的 Supabase URL 和 Key！")
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

# ================= 3. 数据处理逻辑 =================
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

# ================= 替换原有的 get_sensor_data =================
def get_sensor_data(start_time, end_time):
    if not supabase: return pd.DataFrame()
    try:
        # 1. 明确只查需要的列，防止数据量过大超时
        # 2. 增加 limit(100000)，防止默认的 1000 条限制截断数据
        response = supabase.table(TABLE_SENSORS) \
            .select("timestamp, sensor_id, variable_type, value, unit") \
            .gte("timestamp", start_time.isoformat()) \
            .lte("timestamp", end_time.isoformat()) \
            .limit(100000) \
            .order("timestamp").execute()
        
        df = pd.DataFrame(response.data)
        
        if not df.empty:
            # 强力清洗：任何非时间格式的都会变成 NaT (Not a Time)
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
            
            # 去除时区 (关键)
            if df['timestamp'].dt.tz is not None:
                df['timestamp'] = df['timestamp'].dt.tz_localize(None)
                
            # 强力清洗数值：非数字变成 NaN
            df['value'] = pd.to_numeric(df['value'], errors='coerce')
            
            # 剔除坏数据（时间无效 或 数值无效 的行直接丢掉）
            df = df.dropna(subset=['timestamp', 'value'])
            
        return df
    except Exception as e:
        # 如果出错，在侧边栏打印出来，而不是直接吞掉
        st.sidebar.error(f"⚠️ 传感器数据读取崩溃: {e}")
        return pd.DataFrame()

# ================= 替换原有的 get_rainfall_data =================
def get_rainfall_data(start_time, end_time):
    if not supabase: return pd.DataFrame()
    try:
        # 1. 同样增加 limit 防止截断
        response = supabase.table(TABLE_RAIN) \
            .select("created_at, rain_intensity") \
            .gte("created_at", start_time.isoformat()) \
            .lte("created_at", end_time.isoformat()) \
            .limit(100000) \
            .order("created_at").execute()
        
        df = pd.DataFrame(response.data)
        
        if not df.empty:
            df = df.rename(columns={"created_at": "timestamp", "rain_intensity": "value"})
            
            # 强力清洗时间
            df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
            
            # 去除时区 (关键，防止和传感器数据打架)
            if df['timestamp'].dt.tz is not None:
                df['timestamp'] = df['timestamp'].dt.tz_localize(None)
            
            # 强力清洗数值
            df['value'] = pd.to_numeric(df['value'], errors='coerce')
            
            # 剔除坏数据
            df = df.dropna(subset=['timestamp'])
            
            # 再次按时间排序，确保万无一失
            df = df.sort_values('timestamp')
            
        return df
    except Exception as e:
        st.sidebar.error(f"⚠️ 降雨数据读取崩溃: {e}")
        return pd.DataFrame()

def process_data(series, window_size, spike_threshold):
    if spike_threshold > 0:
        diff = series.diff().abs()
        mask = diff < spike_threshold
        series = series.where(mask)
    if window_size > 1:
        series = series.rolling(window=window_size, min_periods=1, center=True).mean()
    return series

# ================= 4. 页面主程序 =================
st.set_page_config(page_title="SciPlot Cloud", layout="wide")
st.title("📊 SciPlot Cloud - 自动化科研绘图平台")

if not supabase:
    st.warning("⚠️ 数据库未连接，请检查代码配置。")
    st.stop()

tab1, tab2 = st.tabs(["📈 数据绘图", "📂 数据上传"])

with tab1:
    with st.sidebar:
        st.header("1. 数据库侦探 🕵️")
        if st.button("🔍 检测数据时间范围"):
            try:
                # 简单查询边界
                res_s_min = supabase.table(TABLE_SENSORS).select("timestamp").order("timestamp", desc=False).limit(1).execute()
                res_s_max = supabase.table(TABLE_SENSORS).select("timestamp").order("timestamp", desc=True).limit(1).execute()
                res_r_min = supabase.table(TABLE_RAIN).select("created_at").order("created_at", desc=False).limit(1).execute()
                res_r_max = supabase.table(TABLE_RAIN).select("created_at").order("created_at", desc=True).limit(1).execute()

                st.info("📊 **传感器数据范围**:")
                if res_s_min.data: st.write(f"{res_s_min.data[0]['timestamp'][:10]} -> {res_s_max.data[0]['timestamp'][:10]}")
                else: st.write("无数据")

                st.info("🌧️ **降雨数据范围**:")
                if res_r_min.data: st.write(f"{res_r_min.data[0]['created_at'][:10]} -> {res_r_max.data[0]['created_at'][:10]}")
                else: st.write("无数据")
            except Exception as e:
                st.error(f"检测失败: {e}")

        st.markdown("---")
        st.header("2. 绘图控制")
        
        default_start = datetime.now() - timedelta(days=30)
        default_end = datetime.now()
        
        c1, c2 = st.columns(2)
        start_date = c1.date_input("开始日期", default_start)
        end_date = c2.date_input("结束日期", default_end)
        show_rainfall = st.checkbox("叠加降雨量", value=True)
        
        st.header("3. 数据清洗")
        ma_window = st.slider("平滑窗口", 1, 20, 1)
        spike_thresh = st.number_input("去噪阈值", 0.0, step=0.1)

        st.header("4. 模式选择")
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
            
            if df_sensor.empty and df_rain.empty:
                st.sidebar.warning(f"⚠️ 该时间段内无数据。")
            else:
                msg = []
                if not df_sensor.empty: msg.append(f"传感器 {len(df_sensor)} 条")
                if not df_rain.empty: 
                    # 统计一下降雨总和，确认是不是全是0
                    total_rain = df_rain['value'].sum()
                    msg.append(f"降雨 {len(df_rain)} 条 (总量: {total_rain:.1f}mm)")
                st.sidebar.success(f"✅ 加载成功: {', '.join(msg)}")

    # 绘图逻辑
    if 'raw_data' in st.session_state:
        df = st.session_state['raw_data']
        df_rain = st.session_state.get('rain_data', pd.DataFrame())
        
        if not df.empty or not df_rain.empty:
            
            # 兼容空传感器数据的情况
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

            # 纯降雨图模式 (如果没传感器数据)
            if df.empty and not df_rain.empty:
                plots_config.append({"title":"降雨量概览", "ids":[], "vars":[]})

            if st.button("🎨 生成图表", key="btn_plot", type="primary") and plots_config:
                
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
                                # 黄金科研比例
                                fig, ax1 = plt.subplots(figsize=(10, 6)) 
                                
                                has_sensor_data = False
                                plotted_vars = set()
                                plotted_units = set()

                                # 1. 画传感器 (左轴)
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
                                                label_str = f"{sid}-{vtype} ({unit})"
                                                ax1.plot(sub['timestamp'], y, label=label_str, linewidth=1.5)
                                
                                # 2. 画降雨 (右轴) - 升级为填充图
                                ax2 = ax1.twinx()
                                has_rain_data = False
                                if show_rainfall and not df_rain.empty:
                                    # 科研标准画法：蓝色半透明填充
                                    ax2.fill_between(df_rain['timestamp'], df_rain['value'], color='#1f77b4', alpha=0.3, label='降雨量 (mm)')
                                    # 辅助线：轻轻勾勒轮廓
                                    ax2.plot(df_rain['timestamp'], df_rain['value'], color='#1f77b4', linewidth=1, alpha=0.6)
                                    
                                    # 强制Y轴从0开始，防止刻度乱飞
                                    ax2.set_ylim(bottom=0)
                                    has_rain_data = True
                                
                                # === 样式精修 ===
                                fp = zh_font if zh_font else None
                                
                                # 左轴标题
                                if has_sensor_data:
                                    if len(plotted_vars) == 1 and len(plotted_units) == 1:
                                        y_label = f"{list(plotted_vars)[0]} ({list(plotted_units)[0]})"
                                    else:
                                        y_label = "数值 (Value)"
                                    ax1.set_ylabel(y_label, fontproperties=fp, fontsize=12)
                                else:
                                    ax1.set_yticks([])
                                
                                # 下轴
                                ax1.set_xlabel("时间 (Time)", fontproperties=fp, fontsize=12)
                                ax1.xaxis.set_major_locator(ticker.MaxNLocator(nbins=6)) 
                                
                                # 标题
                                ax1.set_title(config['title'], fontproperties=fp, fontsize=14, fontweight='bold', pad=10)
                                
                                # 刻度与网格
                                ax1.tick_params(axis='both', direction='in', which='both', top=True, right=False, labeltop=False, labelright=False)
                                ax2.tick_params(axis='y', direction='in', right=True, labelright=False)
                                ax1.grid(True, linestyle=':', alpha=0.3)
                                
                                # 右轴标题
                                if has_rain_data:
                                    ax2.set_ylabel("降雨量 (mm)", fontproperties=fp, fontsize=12)
                                else:
                                    # 如果当前窗口内全是0或没数据，隐藏右轴
                                    ax2.set_yticks([])
                                
                                # 图例 (智能合并)
                                handles1, labels1 = ax1.get_legend_handles_labels()
                                handles2, labels2 = ax2.get_legend_handles_labels()
                                
                                # 只有当真的画了东西才显示图例
                                if handles1 or handles2:
                                    leg = ax1.legend(handles1 + handles2, labels1 + labels2, loc='best', frameon=False)
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


