import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import time

# Bật nền tối cực ngầu cho biểu đồ
import plotly.io as pio
pio.templates.default = "plotly_dark"

# ==========================================
# CẤU HÌNH API TOMTOM & MẠNG LƯỚI
# ==========================================
TOMTOM_API_KEY = "1Wa4kgOJxHIrECvCEjAPE82U6bDDT864" 

# Mạng lưới 15 điểm nóng giao thông TP.HCM (Kèm phân loại Quận)
HCM_HOTSPOTS = {
    # Quận 1 & Trung tâm
    "Hầm Thủ Thiêm (Q1)": "10.7716,106.7133",
    "Ngã 6 Phù Đổng (Q1)": "10.7714,106.6923",
    "Vòng xoay Điện Biên Phủ (Q1)": "10.7915,106.7011",
    # Quận Bình Thạnh & Phú Nhuận
    "Ngã Tư Hàng Xanh (Bình Thạnh)": "10.8015,106.7111",
    "Cầu Sài Gòn (Bình Thạnh)": "10.7984,106.7233",
    "Ngã Tư Phú Nhuận (Phú Nhuận)": "10.7981,106.6775",
    # Quận Tân Bình & Gò Vấp
    "Vòng Xoay Lăng Cha Cả (Tân Bình)": "10.7986,106.6575",
    "Ngã Tư Bảy Hiền (Tân Bình)": "10.7932,106.6527",
    "Vòng xoay Phạm Văn Đồng (Gò Vấp)": "10.8202,106.6874",
    # Quận 10 & Quận 3
    "Vòng Xoay Dân Chủ (Q3)": "10.7935,106.6806",
    "Ngã 7 Lý Thái Tổ (Q10)": "10.7672,106.6745",
    "Cầu Vượt 3/2 (Q10)": "10.7735,106.6722",
    # Cửa ngõ & Khu Nam
    "Cầu Kênh Tẻ (Q4)": "10.7548,106.6975",
    "Ngã tư Nguyễn Văn Linh (Q7)": "10.7339,106.7032",
    "Ngã tư An Sương (Q12)": "10.8333,106.6136"
}

# ==========================================
# KHỞI TẠO BỘ NHỚ LƯU VẾT (HISTORY)
# ==========================================
if 'live_history' not in st.session_state:
    st.session_state['live_history'] = pd.DataFrame()

# ==========================================
# HÀM KÉO DỮ LIỆU (ĐÃ THÊM TÍNH NĂNG CHỐNG CHẶN API)
# ==========================================
@st.cache_data(ttl=30)
def fetch_realtime_traffic(api_key, hotspots):
    data_records = []
    fetch_time = datetime.now()
    
    for name, coords in hotspots.items():
        url = f"https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json?key={api_key}&point={coords}"
        try:
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                flow = res.json().get('flowSegmentData', {})
                curr_speed = flow.get('currentSpeed', 0)
                free_speed = flow.get('freeFlowSpeed', 1)
                
                # Tính Toán Chỉ Số Ùn Tắc
                cong_pct = round((1 - (curr_speed / free_speed)) * 100, 1)
                cong_pct = max(0, min(100, cong_pct))
                
                if cong_pct > 60: status = "Kẹt Cứng"
                elif cong_pct > 30: status = "Ùn Ứ"
                else: status = "Thông Thoáng"
                
                # Tách tên Quận từ chuỗi
                district = name.split('(')[-1].replace(')', '').strip()
                node_name = name.split('(')[0].strip()
                lat, lon = map(float, coords.split(','))
                
                data_records.append({
                    "Thời Gian Fetch": fetch_time,
                    "Khu Vực": district,
                    "Nút Giao": node_name,
                    "Lat": lat, "Lon": lon,
                    "Tốc Độ (km/h)": curr_speed,
                    "Mức Ùn Tắc (%)": cong_pct,
                    "Trạng Thái": status
                })
            else:
                print(f"Bị chặn ở {name} - Mã lỗi: {res.status_code}")
        except Exception as e:
            print(f"Lỗi mạng ở {name}: {e}")
            
        # VŨ KHÍ BÍ MẬT: Nghỉ 0.3 giây trước khi gọi trạm tiếp theo để tránh bị TomTom block
        time.sleep(0.3)
        
    return pd.DataFrame(data_records)

# ==========================================
# GIAO DIỆN UI
# ==========================================
st.set_page_config(page_title="HCMC ITS Command Center", layout="wide")
st.markdown("<h2 style='text-align: center; color: #00E5FF;'>🚦 TRUNG TÂM ĐIỀU HÀNH GIAO THÔNG ĐÔ THỊ (ITS)</h2>", unsafe_allow_html=True)

if TOMTOM_API_KEY == "ĐIỀN_API_KEY_CỦA_BẠN_VÀO_ĐÂY":
    st.error("⚠️ Điền API Key của TomTom để kích hoạt hệ thống.")
    st.stop()

col_info, col_btn = st.columns([5, 1])
with col_info:
    st.write(f"📡 Trạng thái kết nối: **Đang truyền phát (Live)** | Mạng lưới: **{len(HCM_HOTSPOTS)} Nút giao**")
with col_btn:
    if st.button("🔄 Quét Vệ Tinh (Cập nhật)", use_container_width=True):
        fetch_realtime_traffic.clear()
        st.rerun()

# 1. KÉO DỮ LIỆU & LƯU LỊCH SỬ
with st.spinner("🌍 Đang đồng bộ luồng dữ liệu từ vệ tinh..."):
    df_live = fetch_realtime_traffic(TOMTOM_API_KEY, HCM_HOTSPOTS)
    
    if not df_live.empty:
        # Append vào lịch sử để vẽ line chart
        st.session_state['live_history'] = pd.concat([st.session_state['live_history'], df_live]).drop_duplicates()

if not df_live.empty:
    st.markdown("---")
    # ==========================================
    # 2. ĐỒNG HỒ KPI & TRẠNG THÁI MẠNG LƯỚI
    # ==========================================
    avg_speed_city = df_live['Tốc Độ (km/h)'].mean()
    avg_congestion = df_live['Mức Ùn Tắc (%)'].mean()
    
    g1, g2, g3 = st.columns(3)
    with g1:
        fig_spd = go.Figure(go.Indicator(
            mode="gauge+number", value=avg_speed_city, title={'text': "Tốc độ T.Bình Mạng lưới (km/h)", 'font': {'size': 18}},
            gauge={'axis': {'range': [0, 80]}, 'bar': {'color': "#00E5FF"}, 'bgcolor': "rgba(255,255,255,0.1)"}
        ))
        # Kéo cao lên 250, đẩy lề trên 't' lên 60 để chữ Title hiển thị trọn vẹn
        fig_spd.update_layout(height=250, margin=dict(l=20, r=20, t=60, b=10), paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_spd, use_container_width=True)

    with g2:
        fig_cong = go.Figure(go.Indicator(
            mode="gauge+number", value=avg_congestion, title={'text': "Chỉ số Ùn tắc (Congestion Index)", 'font': {'size': 18}},
            gauge={
                'axis': {'range': [0, 100]}, 'bar': {'color': "#FF4B4B" if avg_congestion > 50 else "#FFD700"},
                'steps': [{'range': [0, 30], 'color': "rgba(0, 255, 127, 0.2)"}, {'range': [60, 100], 'color': "rgba(255, 75, 75, 0.3)"}],
                'bgcolor': "rgba(255,255,255,0.1)"
            }
        ))
        # Đồng bộ height=250 và t=60
        fig_cong.update_layout(height=250, margin=dict(l=20, r=20, t=60, b=10), paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_cong, use_container_width=True)

    with g3:
        ket_xe_count = len(df_live[df_live['Trạng Thái'] == 'Kẹt Cứng'])
        # Tăng height lên 250px (thay vì 200px) để cao bằng 2 đồng hồ bên cạnh
        st.markdown(f"""
        <div style="background-color: rgba(255,255,255,0.05); padding: 25px 15px; border-radius: 10px; height: 250px; display: flex; flex-direction: column; justify-content: center; align-items: center; border: 1px solid rgba(255,255,255,0.1);">
            <h4 style="color: white; margin-bottom: 10px; font-size: 18px;">Cảnh Báo Điểm Đen</h4>
            <h1 style="color: {'#FF4B4B' if ket_xe_count > 0 else '#00FF7F'}; font-size: 60px; margin: 0;">{ket_xe_count}</h1>
            <p style="color: gray; font-size: 15px; margin-top: 10px;">Nút giao đang bị Kẹt Cứng</p>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("---")

    # ==========================================
    # 3. GIS MAP & TREEMAP PHÂN CẤP (BIỂU ĐỒ MỚI CHUYÊN SÂU)
    # ==========================================
    c_map, c_tree = st.columns([1.5, 1])
    
    with c_map:
        st.markdown("#### 🗺️ Bản Đồ Không Gian (Spatial GIS Map)")
        fig_map = px.scatter_mapbox(
            df_live, lat="Lat", lon="Lon", 
            color="Mức Ùn Tắc (%)", size="Mức Ùn Tắc (%)",
            hover_name="Nút Giao", hover_data=["Khu Vực", "Tốc Độ (km/h)", "Trạng Thái"],
            color_continuous_scale="RdYlGn_r", size_max=20, zoom=11.5, mapbox_style="carto-darkmatter"
        )
        fig_map.update_layout(height=550, margin={"r":0,"t":30,"l":0,"b":0})
        st.plotly_chart(fig_map, use_container_width=True)

    with c_tree:
        st.markdown("#### 🗂️ Phân Cấp Ùn Tắc Theo Khu Vực (Treemap)")
        # Biểu đồ Treemap cực kỳ pro để xem Khu vực nào đang gánh áp lực lớn nhất
        fig_tree = px.treemap(
            df_live, path=[px.Constant("TP.HCM"), "Khu Vực", "Nút Giao"],
            values="Mức Ùn Tắc (%)", color="Tốc Độ (km/h)",
            color_continuous_scale="RdYlGn",
            color_continuous_midpoint=30
        )
        fig_tree.update_layout(height=550, margin={"r":0,"t":30,"l":0,"b":0}, paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_tree, use_container_width=True)

    # ==========================================
    # 4. LIVE TIME-SERIES & RANKING
    # ==========================================
    c_line, c_bar = st.columns([1.5, 1])
    
    with c_line:
        st.markdown("#### 📈 Diễn Biến Tốc Độ Theo Thời Gian (Live Trend)")
        df_hist = st.session_state['live_history']
        
        # Chỉ vẽ line chart nếu đã bấm Cập nhật ít nhất 2 lần (có dữ liệu lịch sử)
        if len(df_hist['Thời Gian Fetch'].unique()) > 1:
            fig_line = px.line(
                df_hist, x="Thời Gian Fetch", y="Tốc Độ (km/h)", color="Nút Giao",
                markers=True, title="Tốc độ thay đổi qua các lần quét"
            )
            fig_line.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.1)"),
                margin={"r":0,"t":30,"l":0,"b":0},
                legend=dict(orientation="h", y=-0.2)
            )
            st.plotly_chart(fig_line, use_container_width=True)
        else:
            st.info("⏳ Hãy bấm nút **'Quét Vệ Tinh (Cập nhật)'** thêm vài lần để hệ thống tích lũy dữ liệu và vẽ Biểu đồ diễn biến thời gian thực!")

    with c_bar:
        st.markdown("#### 🏆 Top Nút Giao Ùn Tắc Nhất")
        df_rank = df_live.sort_values(by="Mức Ùn Tắc (%)", ascending=True).tail(8) # Chỉ hiện top 8 cho gọn
        fig_bar = px.bar(
            df_rank, x="Mức Ùn Tắc (%)", y="Nút Giao", orientation='h', 
            color="Mức Ùn Tắc (%)", color_continuous_scale="Reds", text="Trạng Thái"
        )
        fig_bar.update_traces(textposition='inside')
        fig_bar.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", margin={"r":0,"t":0,"l":0,"b":0})
        st.plotly_chart(fig_bar, use_container_width=True)

else:
    st.error("Lỗi mất kết nối đến API TomTom.")