import streamlit as st
from datetime import datetime
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# Import API Key từ file cấu hình (Không hardcode ở đây nữa)
from config import TOMTOM_API_KEY
from core.tomtom_api import fetch_realtime_traffic

# 👉 Import hàm lấy danh sách nút giao động từ Database
from core.database import get_tomtom_hotspots

def render_tab_livemap():
    # Gọi dữ liệu nút giao từ PostgreSQL thay vì danh sách cứng
    HCM_HOTSPOTS = get_tomtom_hotspots()

    # Kiểm tra xem Database có đang trống không
    if not HCM_HOTSPOTS:
        st.warning("⚠️ Chưa có nút giao nào trong cơ sở dữ liệu. Vui lòng sang Tab Quản Trị (Admin) để thêm Nút giao TomTom!")
        return

    # Nếu người dùng quên chưa nhập API Key vào file config.py
    if TOMTOM_API_KEY == "ĐIỀN_API_KEY_CỦA_BẠN_VÀO_ĐÂY" or not TOMTOM_API_KEY:
        st.error("⚠️ Điền API Key của TomTom trong file config.py để kích hoạt hệ thống Live.")
    else:
        col_info, col_btn = st.columns([5, 1])
        with col_info:
            st.markdown("<h3 style='color: #00E5FF;'>🌍 TRUNG TÂM ĐIỀU HÀNH MẠNG LƯỚI (LIVE API)</h3>", unsafe_allow_html=True)
            st.write(f"📡 Trạng thái: **Đang truyền phát (Live)** | Mạng lưới: **{len(HCM_HOTSPOTS)} Nút giao (TP.HCM)**")
        with col_btn:
            st.write("")
            if st.button("🔄 Cập nhật", use_container_width=True):
                fetch_realtime_traffic.clear()
                st.rerun()

        with st.spinner("🌍 Đang đồng bộ dữ liệu từ vệ tinh (Xin chờ 15s để chống Spam API)..."):
            # Truyền từ điển Hotspots mới lấy từ DB vào hàm fetch API
            df_live = fetch_realtime_traffic(TOMTOM_API_KEY, HCM_HOTSPOTS)
            if not df_live.empty:
                st.session_state['live_history'] = pd.concat([st.session_state['live_history'], df_live]).drop_duplicates()

        if not df_live.empty:
            avg_speed_city = df_live['Tốc Độ (km/h)'].mean()
            avg_congestion = df_live['Mức Ùn Tắc (%)'].mean()
            
            g1_live, g2_live, g3_live = st.columns(3)
            with g1_live:
                fig_spd_live = go.Figure(go.Indicator(
                    mode="gauge", value=avg_speed_city, title={'text': "Tốc độ TB Mạng lưới (km/h)", 'font': {'size': 18}},
                    gauge={'axis': {'range': [0, 80]}, 'bar': {'color': "#00E5FF"}, 'bgcolor': "rgba(255,255,255,0.1)"}
                ))
                fig_spd_live.add_annotation(x=0.5, y=0.15, text=f"<b>{avg_speed_city:.1f}</b>", font=dict(size=40, color="#00E5FF"), showarrow=False)
                fig_spd_live.update_layout(height=250, margin=dict(l=20, r=20, t=60, b=10), paper_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_spd_live, use_container_width=True)
                
            with g2_live:
                fig_cong_live = go.Figure(go.Indicator(
                    mode="gauge", value=avg_congestion, title={'text': "Chỉ số Ùn tắc (%)", 'font': {'size': 18}},
                    gauge={'axis': {'range': [0, 100]}, 'bar': {'color': "#FF4B4B" if avg_congestion > 50 else "#FFD700"},
                           'steps': [{'range': [0, 30], 'color': "rgba(0, 255, 127, 0.2)"}, {'range': [60, 100], 'color': "rgba(255, 75, 75, 0.3)"}],
                           'bgcolor': "rgba(255,255,255,0.1)"}
                ))
                fig_cong_live.add_annotation(x=0.5, y=0.15, text=f"<b>{avg_congestion:.1f}</b>", font=dict(size=40, color="#FF4B4B" if avg_congestion > 50 else "#FFD700"), showarrow=False)
                fig_cong_live.update_layout(height=250, margin=dict(l=20, r=20, t=60, b=10), paper_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_cong_live, use_container_width=True)
                
            with g3_live:
                # =========================================================
                # KHỐI ĐỒNG HỒ & CẢNH BÁO TÍCH HỢP (FLEXBOX CHUẨN UX)
                # =========================================================
                now = datetime.now()
                current_time = now.strftime("%H:%M:%S")
                current_date = now.strftime("%d/%m/%Y")
                ket_xe_count = len(df_live[df_live['Trạng Thái'] == 'Kẹt Cứng'])
                
                st.markdown(f"""
<div style="display: flex; flex-direction: column; gap: 15px; height: 250px; margin-top: 15px;">
    <div style="background-color: rgba(255,255,255,0.03); flex: 1; border-radius: 10px; border: 1px solid rgba(255,255,255,0.1); display: flex; flex-direction: column; justify-content: center; align-items: center; box-shadow: 0 4px 10px rgba(0,0,0,0.2);">
        <p style="color: #888; font-size: 12px; margin: 0 0 5px 0; font-weight: bold; text-transform: uppercase; letter-spacing: 1px;">⏱️ Thời Gian Ghi Nhận</p>
        <h2 style="color: #00E5FF; font-size: 38px; margin: 0; line-height: 1; font-family: 'Courier New', Courier, monospace; text-shadow: 0 0 10px rgba(0,229,255,0.4);">{current_time}</h2>
        <p style="color: #bbb; font-size: 14px; margin: 5px 0 0 0; letter-spacing: 2px;">{current_date}</p>
    </div>
    <div style="background-color: rgba(255,255,255,0.05); flex: 1; border-radius: 10px; border: 1px solid rgba(255,255,255,0.1); display: flex; flex-direction: column; justify-content: center; align-items: center;">
        <p style="color: white; margin: 0 0 5px 0; font-size: 16px; font-weight: bold;">Cảnh Báo Điểm Đen</p>
        <h1 style="color: {'#FF4B4B' if ket_xe_count > 0 else '#00FF7F'}; font-size: 45px; margin: 0; line-height: 1;">{ket_xe_count}</h1>
        <p style="color: gray; font-size: 13px; margin: 5px 0 0 0;">Nút giao đang bị Kẹt Cứng</p>
    </div>
</div>
""", unsafe_allow_html=True)

            st.markdown("---")
            c_map, c_tree = st.columns([1.5, 1])
            with c_map:
                st.markdown("#### 🗺️ Bản Đồ Không Gian (Spatial GIS Map)")
                fig_map = px.scatter_mapbox(
                    df_live, lat="Lat", lon="Lon", color="Mức Ùn Tắc (%)", size="Mức Ùn Tắc (%)",
                    hover_name="Nút Giao", hover_data=["Khu Vực", "Tốc Độ (km/h)", "Trạng Thái"],
                    color_continuous_scale="RdYlGn_r", size_max=20, zoom=11.5, mapbox_style="carto-darkmatter"
                )
                fig_map.update_layout(height=550, margin={"r":0,"t":30,"l":0,"b":0})
                st.plotly_chart(fig_map, use_container_width=True)
            with c_tree:
                st.markdown("#### 🗂️ Phân Cấp Ùn Tắc Theo Khu Vực (Treemap)")
                fig_tree = px.treemap(
                    df_live, path=[px.Constant("TP.HCM"), "Khu Vực", "Nút Giao"],
                    values="Mức Ùn Tắc (%)", color="Tốc Độ (km/h)", color_continuous_scale="RdYlGn", color_continuous_midpoint=30
                )
                fig_tree.update_layout(height=550, margin={"r":0,"t":30,"l":0,"b":0}, paper_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_tree, use_container_width=True)

            st.markdown("---")
            c_line, c_bar = st.columns([1.5, 1])
            with c_line:
                st.markdown("#### 📈 Diễn Biến Tốc Độ (Live Trend)")
                df_hist = st.session_state['live_history']
                if len(df_hist['Thời Gian Fetch'].unique()) > 1:
                    fig_line = px.line(df_hist, x="Thời Gian Fetch", y="Tốc Độ (km/h)", color="Nút Giao", markers=True)
                    fig_line.update_layout(
                        height=450, paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        xaxis=dict(showgrid=False), 
                        yaxis=dict(showgrid=True, 
                        gridcolor="rgba(255,255,255,0.1)"), 
                        margin={"r":0,"t":50,"l":0,"b":0}, 
                        legend=dict(orientation="h", y=-0.2))
                    st.plotly_chart(fig_line, use_container_width=True)
                else:
                    st.info("⏳ Hãy bấm nút **'Cập nhật'** thêm vài lần để vẽ Biểu đồ diễn biến thời gian thực!")
            with c_bar:
                st.markdown("#### 🏆 Top Nút Giao Ùn Tắc Nhất")
                df_rank = df_live.sort_values(by="Mức Ùn Tắc (%)", ascending=True).tail(8)
                fig_bar = px.bar(
                    df_rank, 
                    x="Mức Ùn Tắc (%)", 
                    y="Nút Giao", 
                    orientation='h', 
                    color="Mức Ùn Tắc (%)", 
                    color_continuous_scale="RdYlGn_r",
                    text="Trạng Thái"
                )
                fig_bar.update_traces(textposition='inside')
                fig_bar.update_layout(height=450, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", margin={"r":0,"t":50,"l":0,"b":0})
                st.plotly_chart(fig_bar, use_container_width=True)