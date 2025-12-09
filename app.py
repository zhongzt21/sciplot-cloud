import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
from supabase import create_client
from datetime import datetime, timedelta
import matplotlib.ticker as ticker
import re

# ================= 1. 配置区域 =================
# 替换为你的 Supabase 项目 URL 和 Key (都在同一个项目里)
SUPABASE_URL = "https://vetupomjinhylqpxnrhn.supabase.co"
SUPABASE_KEY = "sb_publishable_MpHqZeFn_U-lM19lpEBtMA_NR3Mx3mO"

# 表名配置
TABLE_SENSORS = "sensor_measurements"
TABLE_RAIN = "weather_logs"

# 正则表达式 (与之前清洗脚本一致)
# 匹配格式：ID(可选) + 物理量 + 空格 + 表征(忽略) + 单位(可选)
REGEX_PATTERN = re.compile(r"^([a-zA-Z0-9]+)(?:号)?([\u4e00-\u9fa5]+)\s+([\u4e00-\u9fa5]+)(?:[\(（](.+)[\)）])?(?:\.\d+)?$")

# 绘图字体配置
plt.rcParams['font.family'] = 'Times New Roman'
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['font.sans-serif'] = ['SimHei', 'Arial Unicode MS', 'Microsoft YaHei']

# ================= 2. 核心功能函数 =================
@st.cache_resource
def init_connection():
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as e:
        st.error(f"数据库连接失败: {e}")
        return None

supabase = init_connection()

def parse_excel_file(uploaded_file):
    """解析上传的 Excel 文件 (核心清洗逻辑)"""
    try:
        # header=2 读取第三行
        df = pd.read_excel(uploaded_file, header=2)
    except Exception as e:
        return None, f"文件读取失败: {e}"

    # 锁定时间列
    df.columns.values[0] = 'timestamp_fixed'
    processed_data = []
    log_messages = []
    
    # 遍历列
    for col_name in df.columns[1:]:
        col_str = str(col_name).strip()
        # 跳过垃圾列
        if col_str.startswith("原始数据") or "Unnamed" in col_str:
            continue

        match = REGEX_PATTERN.search(col_str)
        if match:
            raw_id = match.group(1)
            var_type = match.group(2)
            unit = match.group(4) if match.group(4) else ""
            final_sensor_id = f"{raw_id}号"
            
            # 提取数据
            current_series = pd.to_numeric(df[col_name], errors='coerce')
            
            for ts, val in zip(df['timestamp_fixed'], current_series):
                if pd.isna(ts): continue
                
                # 构造数据行
                processed_data.append({
                    "timestamp": ts.isoformat() if hasattr(ts, 'isoformat') else str(ts),
                    "sensor_id": final_sensor_id,
                    "variable_type": var_type,
                    "unit": unit,
                    "value": None if pd.isna(val) else float(val)
                })
    
    return processed_data, f"解析完成，提取到 {len(processed_data)} 条数据"

def upload_to_supabase(data_list):
    """批量上传数据"""
    if not supabase: return False, "数据库未连接"
    
    batch_size = 1000
    total = len(data_list)
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    try:
        for i in range(0, total, batch_size):
            batch = data_list[i:i+batch_size]
            supabase.table(TABLE_SENSORS).upsert(batch).execute()
            
            # 更新进度
            progress = min((i + batch_size) / total, 1.0)
            progress_bar.progress(progress)
            status_text.text(f"正在上传... {int(progress*100)}%")
            
        status_text.text("✅ 上传完成！")
        return True, "成功写入数据库"
    except Exception as e:
        return False, f"上传中断: {e}"

# ... (保留原有的 get_sensor_data, get_rainfall_data, process_data 函数) ...
# 为了节省篇幅，这里假设你保留了之前代码中的这三个读取和处理函数
# 请务必把它们粘贴在这里！如果不记得了，我可以再发一遍。
# ↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓
def get_sensor_data(start_time, end_time):
    if not supabase: return pd.DataFrame()
    try:
        response = supabase.table(TABLE_SENSORS).select("*").gte("timestamp", start_time.isoformat()).lte("timestamp", end_time.isoformat()).order("timestamp").execute()
        df = pd.DataFrame(response.data)
        if not df.empty:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df['value'] = pd.to_numeric(df['value'], errors='coerce')
        return df
    except: return pd.DataFrame()

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
# ↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑↑

# ================= 3. 页面主程序 =================
st.set_page_config(page_title="SciPlot Cloud", layout="wide")
st.title("📊 SciPlot Cloud - 自动化科研绘图平台")

# 创建两个标签页
tab1, tab2 = st.tabs(["📈 数据绘图", "📂 数据上传 (管理员)"])

# --- TAB 1: 绘图功能 (原有的逻辑) ---
with tab1:
    with st.sidebar:
        st.header("1. 绘图控制")
        c1, c2 = st.columns(2)
        start_date = c1.date_input("开始日期", datetime.now() - timedelta(days=7))
        end_date = c2.date_input("结束日期", datetime.now())
        show_rainfall = st.checkbox("叠加降雨量", value=True)
        
        st.header("2. 数据清洗")
        ma_window = st.slider("平滑窗口", 1, 20, 1)
        spike_thresh = st.number_input("去噪阈值", 0.0, step=0.1)

        st.header("3. 模式选择")
        plot_mode = st.radio("分窗逻辑", ["按【号码】自动分窗", "按【物理量】自动分窗", "自定义选择"])
        
        st.markdown("---")
        fetch_btn = st.button("🔄 刷新图表数据", type="primary")

    # 数据加载
    if fetch_btn or 'raw_data' not in st.session_state:
        with st.spinner("加载中..."):
            t_start = datetime.combine(start_date, datetime.min.time())
            t_end = datetime.combine(end_date, datetime.max.time())
            st.session_state['raw_data'] = get_sensor_data(t_start, t_end)
            st.session_state['rain_data'] = get_rainfall_data(t_start, t_end) if show_rainfall else pd.DataFrame()

    # 绘图逻辑 (简化版引用)
    if 'raw_data' in st.session_state and not st.session_state['raw_data'].empty:
        df = st.session_state['raw_data']
        df_rain = st.session_state.get('rain_data', pd.DataFrame())
        
        # ... (这里完全沿用之前的绘图代码，为了代码简洁我省略了中间的if-else分窗逻辑，
        #      请务必保留之前代码中 "all_ids = ..." 到 "st.pyplot(fig)" 的所有内容)
        #      ↓↓ 把之前代码的第 125 行到 200 行复制放在这里 ↓↓
        
        all_ids = sorted(df['sensor_id'].unique())
        all_vars = sorted(df['variable_type'].unique())
        plots_config = []

        if plot_mode == "自定义选择":
            num = st.number_input("窗口数量", 1, 10, 1)
            for i in range(num):
                c1, c2 = st.columns(2)
                ids = c1.multiselect(f"图{i+1} 号码", all_ids, key=f"id{i}")
                vars_ = c2.multiselect(f"图{i+1} 物理量", all_vars, key=f"v{i}")
                if ids and vars_: plots_config.append({"title":f"Custom {i+1}","ids":ids,"vars":vars_})
        elif plot_mode == "按【号码】自动分窗":
            t_ids = st.multiselect("选择号码", all_ids, default=all_ids)
            t_vars = st.multiselect("选择物理量", all_vars, default=all_vars)
            for sid in t_ids: plots_config.append({"title":f"{sid}","ids":[sid],"vars":t_vars})
        elif plot_mode == "按【物理量】自动分窗":
            t_vars = st.multiselect("选择物理量", all_vars, default=all_vars)
            t_ids = st.multiselect("选择号码", all_ids, default=all_ids)
            for v in t_vars: plots_config.append({"title":f"{v}","ids":t_ids,"vars":[v]})

        if st.button("🎨 生成图表", key="btn_plot") and plots_config:
            for config in plots_config:
                fig, ax1 = plt.subplots(figsize=(10, 4))
                has_data = False
                for sid in config['ids']:
                    for vtype in config['vars']:
                        sub = df[(df['sensor_id']==sid)&(df['variable_type']==vtype)].sort_values('timestamp')
                        if not sub.empty:
                            has_data = True
                            y = process_data(sub['value'], ma_window, spike_thresh)
                            ax1.plot(sub['timestamp'], y, label=f"{sid}-{vtype}")
                
                ax2 = ax1.twinx()
                if show_rainfall and not df_rain.empty:
                    ax2.plot(df_rain['timestamp'], df_rain['value'], 'b--', alpha=0.5, label='Rain')
                    ax2.set_ylabel("Rain")
                else: ax2.set_yticks([])

                ax1.set_title(config['title'])
                ax1.tick_params(top=True, direction='in')
                ax2.tick_params(direction='in')
                ax1.grid(True, linestyle=':')
                if has_data: ax1.legend(loc='best')
                st.pyplot(fig)
                st.markdown("---")

# --- TAB 2: 数据上传功能 (新增) ---
with tab2:
    st.header("📂 上传新的 Excel 数据文件")
    st.info("💡 请上传 .xls 或 .xlsx 文件。系统将根据格式自动识别并合并到数据库中。")
    
    uploaded_file = st.file_uploader("拖拽文件到此处", type=['xls', 'xlsx'])
    
    if uploaded_file:
        st.write("正在解析文件...")
        data, msg = parse_excel_file(uploaded_file)
        
        if data:
            st.success(msg)
            st.write("📋 **数据预览 (前5行):**")
            preview_df = pd.DataFrame(data).head()
            st.dataframe(preview_df)
            
            st.warning("⚠️ 注意：点击下方按钮将把数据写入云端数据库，此操作不可撤销。")
            if st.button("🚀 确认上传并更新数据库"):
                success, upload_msg = upload_to_supabase(data)
                if success:
                    st.success(upload_msg)
                    st.balloons() # 撒花庆祝
                else:
                    st.error(upload_msg)
        else:
            st.error(msg)