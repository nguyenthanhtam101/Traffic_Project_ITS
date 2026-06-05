import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import os
import io
from datetime import datetime, timedelta
from config import HEATMAP_CSV_PATH
# Import kết nối Database
from core.database import engine

def render_tab_analytics():
    st.markdown("<h3 style='color: #00E5FF;'>📈 BÁO CÁO PHÂN TÍCH TỪ CƠ SỞ DỮ LIỆU SQL</h3>", unsafe_allow_html=True)
    st.info("Hệ thống hiện đang trích xuất dữ liệu trực tiếp từ Database PostgreSQL.")

    # ==========================================
    # 1. BỘ LỌC DỮ LIỆU CHUYÊN NGHIỆP
    # ==========================================
    st.markdown("#### 🔍 Bộ Lọc Dữ Liệu")
    c_start, c_end, c_cam = st.columns(3)
    
    with c_start:
        start_date = st.date_input("📅 Từ ngày:", datetime.today() - timedelta(days=7))
    with c_end:
        end_date = st.date_input("📅 Đến ngày:", datetime.today())
    with c_cam:
        try:
            cam_list = ["Tất cả các trạm"] + list(pd.read_sql("SELECT name FROM cameras", engine)['name'].unique())
            selected_cam = st.selectbox("📍 Chọn trạm Camera:", cam_list)
        except:
            selected_cam = st.selectbox("📍 Chọn trạm Camera:", ["Tất cả các trạm"])

    btn_load_sql = st.button("🔄 TRÍCH XUẤT DỮ LIỆU", type="primary", use_container_width=True)

    # ==========================================
    # 2. TRUY VẤN VÀ LƯU VÀO BỘ NHỚ ĐỆM (SESSION STATE)
    # ==========================================
    if btn_load_sql:
        with st.spinner("Đang truy vấn Database..."):
            try:
                cam_filter = "" if selected_cam == "Tất cả các trạm" else f"AND camera_name = '{selected_cam}'"
                
                query = f"""
                    SELECT 
                        timestamp AS "Thời Gian", 
                        vehicle_id AS "ID Xe", 
                        vehicle_type AS "Loại Phương Tiện", 
                        speed AS "Tốc Độ (km/h)", 
                        plate_text AS "Biển Số", 
                        status AS "Trạng Thái", 
                        camera_name AS "Camera"
                    FROM traffic_events
                    WHERE timestamp::date >= '{start_date}' AND timestamp::date <= '{end_date}'
                    {cam_filter}
                    ORDER BY timestamp ASC
                """
                df = pd.read_sql(query, engine)

                df_heat = pd.DataFrame()
                if os.path.exists(HEATMAP_CSV_PATH):
                    df_heat = pd.read_csv(HEATMAP_CSV_PATH, names=['Thời Gian', 'Số Xe Kẹt'], encoding='utf-8', on_bad_lines='skip')
                    df_heat['Thời Gian'] = pd.to_datetime(df_heat['Thời Gian'], errors='coerce')
                    
                    start_ts = pd.to_datetime(start_date)
                    end_ts = pd.to_datetime(end_date) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)
                    df_heat = df_heat[(df_heat['Thời Gian'] >= start_ts) & (df_heat['Thời Gian'] <= end_ts)]

                st.session_state['analytics_df'] = df
                st.session_state['analytics_df_heat'] = df_heat
                st.session_state['analytics_loaded'] = True

            except Exception as e:
                st.error(f"❌ Lỗi truy vấn Database: {e}")
                return

    # ==========================================
    # 3. RENDER BIỂU ĐỒ TỪ BỘ NHỚ ĐỆM
    # ==========================================
    if st.session_state.get('analytics_loaded', False):
        df = st.session_state['analytics_df']
        df_heat = st.session_state['analytics_df_heat']

        if not df.empty:
            df['Thời Gian'] = pd.to_datetime(df['Thời Gian'], errors='coerce')
            df['Tốc Độ (km/h)'] = pd.to_numeric(df['Tốc Độ (km/h)'], errors='coerce').fillna(0)
            
            has_speed = (df['Tốc Độ (km/h)'] > 0).any()
            violations_df = df[~df['Trạng Thái'].isin(['Bình Thường', 'Normal', 'Low', 'Thông Thoáng', 'Lưu thông Tự do'])].copy()
            has_violations = not violations_df.empty

            st.markdown("---")
            total_vehicles = len(df['ID Xe'].unique()) 
            total_viol_count = len(violations_df) if has_violations else 0
            avg_speed = df[df['Tốc Độ (km/h)'] > 0]['Tốc Độ (km/h)'].mean() if has_speed else 0

            g1, g2, g3 = st.columns(3)
            with g1:
                fig_vol = go.Figure(go.Indicator(mode="number", value=total_vehicles, title={'text': "Tổng Lưu Lượng Xe", 'font': {'size': 18, 'color': 'white'}}, number={'font': {'color': '#00E5FF', 'size': 50}}))
                fig_vol.update_layout(height=200, margin=dict(l=10, r=10, t=50, b=10), paper_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_vol, use_container_width=True)
                
            with g2:
                fig_viol = go.Figure(go.Indicator(mode="number", value=total_viol_count, title={'text': "Tổng Lỗi Vi Phạm", 'font': {'size': 18, 'color': 'white'}}, number={'font': {'color': '#FF4B4B' if total_viol_count > 0 else '#00FF7F', 'size': 50}}))
                fig_viol.update_layout(height=200, margin=dict(l=10, r=10, t=50, b=10), paper_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_viol, use_container_width=True)
                
            with g3:
                if has_speed:
                    fig_spd = go.Figure(go.Indicator(mode="gauge", value=avg_speed, title={'text': "Tốc Độ TB Mạng Lưới (km/h)", 'font': {'size': 18, 'color': 'white'}}, gauge={'axis': {'range': [0, 100]}, 'bar': {'color': "#FFD700"}, 'bgcolor': "rgba(255,255,255,0.1)"}))
                    fig_spd.add_annotation(x=0.5, y=0.15, text=f"<b>{avg_speed:.1f}</b>", font=dict(size=40, color="#FFD700"), showarrow=False)
                    fig_spd.update_layout(height=200, margin=dict(l=10, r=10, t=50, b=10), paper_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(fig_spd, use_container_width=True)
                else:
                    st.markdown("""<div style='text-align:center; padding-top: 50px; color: gray;'><i>Dữ liệu tốc độ bằng 0. Tính năng này chưa được kích hoạt ở Camera hiện tại.</i></div>""", unsafe_allow_html=True)

            st.markdown("---")

            # ==========================================
            # 👉 TÍNH NĂNG MỚI: XEM ẢNH BẰNG CHỨNG VI PHẠM (SNAPSHOT) CÓ BỘ LỌC
            # ==========================================
            if has_violations:
                st.markdown("#### 📸 Trích Xuất Bằng Chứng Phạt Nguội (Snapshot Evidence)")
                st.caption("Tra cứu lại hình ảnh thực tế của các phương tiện được AI phát hiện vi phạm.")
                
                # Tạo bộ lọc (Filters)
                st.markdown("**🔍 Lọc danh sách vi phạm:**")
                violations_df['Ngày'] = violations_df['Thời Gian'].dt.date
                
                f_col1, f_col2, f_col3 = st.columns(3)
                with f_col1:
                    filter_date = st.selectbox("📅 Theo Ngày:", ["Tất cả"] + list(violations_df['Ngày'].unique()))
                with f_col2:
                    filter_type = st.selectbox("🚨 Theo Lỗi Vi Phạm:", ["Tất cả"] + list(violations_df['Trạng Thái'].unique()))
                with f_col3:
                    filter_veh = st.selectbox("🚗 Theo Loại Xe:", ["Tất cả"] + list(violations_df['Loại Phương Tiện'].unique()))

                # Áp dụng logic lọc dữ liệu
                filtered_viol_df = violations_df.copy()
                if filter_date != "Tất cả":
                    filtered_viol_df = filtered_viol_df[filtered_viol_df['Ngày'] == filter_date]
                if filter_type != "Tất cả":
                    filtered_viol_df = filtered_viol_df[filtered_viol_df['Trạng Thái'] == filter_type]
                if filter_veh != "Tất cả":
                    filtered_viol_df = filtered_viol_df[filtered_viol_df['Loại Phương Tiện'] == filter_veh]

                st.markdown("<br>", unsafe_allow_html=True)

                if filtered_viol_df.empty:
                    st.info("Không có dữ liệu vi phạm nào khớp với bộ lọc của bạn.")
                else:
                    # Tạo Format hiển thị trên thanh cuộn (Selectbox) đã được bổ sung Biển Số
                    viol_options = filtered_viol_df.apply(
                        lambda row: f"🕒 {row['Thời Gian'].strftime('%d/%m %H:%M:%S')} | Lỗi: {row['Trạng Thái']} | Xe: {row['Loại Phương Tiện']} | BS: {row['Biển Số']} (ID: {row['ID Xe']})", 
                        axis=1
                    ).tolist()
                    
                    selected_viol_str = st.selectbox("🚦 Chọn một sự kiện vi phạm để xem bằng chứng:", viol_options)
                    
                    if selected_viol_str:
                        idx = viol_options.index(selected_viol_str)
                        selected_row = filtered_viol_df.iloc[idx]
                        vid = selected_row['ID Xe']
                        
                        # BỐ CỤC: Khung thông tin phạt nguội bên trái, Ảnh bằng chứng bên phải
                        c_info, c_img = st.columns([1, 1.5])
                        
                        with c_info:
                            st.markdown(f"""
                            <div style='background-color: rgba(255, 75, 75, 0.1); padding: 20px; border-radius: 10px; border: 1px solid #FF4B4B; height: 100%;'>
                                <h4 style='color: #FF4B4B; margin-top:0;'>THÔNG TIN VI PHẠM</h4>
                                <hr style='border-color: rgba(255, 75, 75, 0.3); margin: 10px 0;'>
                                <p style='margin:5px 0;'>📍 <b>Vị trí:</b> {selected_row['Camera']}</p>
                                <p style='margin:5px 0;'>🕒 <b>Thời gian:</b> {selected_row['Thời Gian'].strftime('%d/%m/%Y %H:%M:%S')}</p>
                                <p style='margin:5px 0;'>🚗 <b>Loại xe:</b> {selected_row['Loại Phương Tiện']}</p>
                                <p style='margin:5px 0;'>🏷️ <b>Biển số:</b> <span style='color:#00E5FF; font-weight:bold;'>{selected_row['Biển Số']}</span></p>
                                <p style='margin:5px 0;'>🆔 <b>ID hệ thống:</b> {vid}</p>
                                <p style='margin:5px 0;'>⚡ <b>Tốc độ:</b> <span style='color:#FFD700; font-weight:bold;'>{selected_row['Tốc Độ (km/h)']} km/h</span></p>
                                <p style='margin:5px 0;'>🚨 <b>Hành vi:</b> <span style='color:#FF4B4B; font-weight:bold;'>{selected_row['Trạng Thái']}</span></p>
                            </div>
                            """, unsafe_allow_html=True)
                        
                        with c_img:
                            img_path = f"evidence/violation_{vid}.jpg"
                            if os.path.exists(img_path):
                                st.image(img_path, caption=f"Ảnh cắt từ Camera - Hệ thống ITS HCM", use_container_width=True)
                            else:
                                st.markdown(f"""
                                <div style='height: 100%; min-height: 250px; display: flex; align-items: center; justify-content: center; background-color: #1E1E1E; border: 1px dashed #555; border-radius: 10px;'>
                                    <div style='text-align:center; color: #888;'>
                                        <h1 style='margin:0;'>📷</h1>
                                        <p style='margin-top:10px;'>Không tìm thấy ảnh tại: <code>{img_path}</code><br><i>(Hệ thống ghi nhận sự kiện nhưng AI chưa lưu ảnh)</i></p>
                                    </div>
                                </div>
                                """, unsafe_allow_html=True)
            st.markdown("---")

            # PHÂN TÍCH HEATMAP 
            if not df_heat.empty:
                df_heat = df_heat.dropna()
                st.markdown("#### 🔥 Phân Tích Ùn Tắc Từ Bản Đồ Nhiệt (Heatmap)")
                fig_heat = px.area(df_heat, x="Thời Gian", y="Số Xe Kẹt", color_discrete_sequence=['#FF4500'])
                fig_heat.update_layout(height=400, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", margin=dict(l=0, r=0, t=50, b=0))
                fig_heat.update_traces(fillcolor="rgba(255, 69, 0, 0.3)")
                st.plotly_chart(fig_heat, use_container_width=True)
                st.markdown("---")

            # BIỂU ĐỒ CHU KỲ LƯU LƯỢNG & CƠ CẤU
            c_line, c_pie = st.columns([2, 1])
            with c_line:
                st.markdown("#### 📈 Diễn Biến Lưu Lượng Xe Theo Thời Gian")
                df['Thời Gian (Giờ:Phút)'] = df['Thời Gian'].dt.floor('min')
                df_trend = df.groupby('Thời Gian (Giờ:Phút)').size().reset_index(name='Số Xe')
                fig_trend = px.area(df_trend, x="Thời Gian (Giờ:Phút)", y="Số Xe", color_discrete_sequence=['#00E5FF'])
                fig_trend.update_layout(height=400, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", margin=dict(l=0, r=0, t=50, b=0))
                fig_trend.update_traces(fillcolor="rgba(0, 229, 255, 0.2)")
                st.plotly_chart(fig_trend, use_container_width=True)

            with c_pie:
                st.markdown("#### 🍩 Cơ Cấu Phương Tiện")
                df_types = df['Loại Phương Tiện'].value_counts().reset_index()
                df_types.columns = ['Loại Xe', 'Số Lượng']
                fig_pie = px.pie(df_types, names='Loại Xe', values='Số Lượng', hole=0.6, color_discrete_sequence=px.colors.qualitative.Pastel)
                fig_pie.update_traces(textposition='inside', textinfo='label+percent+value', textfont_size=14)
                fig_pie.update_layout(height=400, paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=0, r=0, t=50, b=0), showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5))
                st.plotly_chart(fig_pie, use_container_width=True)

            st.markdown("---")

            # BIỂU ĐỒ ĐỘNG
            c_dyn1, c_dyn2 = st.columns(2)
            
            with c_dyn1:
                if has_violations:
                    st.markdown("#### 🚨 Phân Tích Lỗi Vi Phạm")
                    df_viol_grouped = violations_df['Trạng Thái'].value_counts().reset_index()
                    df_viol_grouped.columns = ['Trạng Thái', 'Số Lượng']
                    fig_viol_pie = px.pie(df_viol_grouped, names='Trạng Thái', values='Số Lượng', color_discrete_sequence=['#FF4B4B', '#FF9800', '#FF1493'])
                    fig_viol_pie.update_traces(textposition='inside', textinfo='label+percent+value', textfont_size=14)
                    fig_viol_pie.update_layout(height=400, paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=0, r=0, t=50, b=0), legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5))
                    st.plotly_chart(fig_viol_pie, use_container_width=True)
                else:
                    st.success("Không phát hiện vi phạm nào (Đèn đỏ / Ngược chiều / Quá tốc độ) trong khoảng thời gian này.")

            with c_dyn2:
                if has_speed:
                    st.markdown("#### 📊 Tần Suất Tốc Độ Phương Tiện")
                    df_speed = df[df['Tốc Độ (km/h)'] > 0]
                    fig_hist = px.histogram(df_speed, x="Tốc Độ (km/h)", nbins=20, color_discrete_sequence=['#9D00FF'])
                    fig_hist.update_layout(height=400, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", xaxis=dict(showgrid=False), yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.1)"), margin=dict(l=0, r=0, t=50, b=0))
                    st.plotly_chart(fig_hist, use_container_width=True)

            with st.expander("📋 Xem chi tiết Bảng dữ liệu thô (SQL)"):
                st.dataframe(df.style.map(lambda x: "color: #FF4B4B; font-weight:bold;" if str(x) not in ["Bình Thường", "Normal", "Low", "Thông Thoáng", "Lưu thông Tự do"] and pd.notnull(x) else "", subset=['Trạng Thái']), use_container_width=True)

            # ==========================================
            # XUẤT BÁO CÁO (EXPORT EXCEL & CSV)
            # ==========================================
            st.markdown("---")
            st.markdown("#### 📥 Xuất Báo Cáo Dữ Liệu (Export Report)")
            st.info("💡 **MẸO XUẤT FILE PDF:** Để lưu toàn bộ các biểu đồ phân tích cực nét phía trên ra file PDF, hãy ấn tổ hợp phím **Ctrl + P** (hoặc Cmd + P) trên trình duyệt web của bạn và chọn mục **Lưu dưới dạng PDF**.")

            c_down1, c_down2 = st.columns(2)

            with c_down1:
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                    df_export = df.copy()
                    if 'Thời Gian' in df_export.columns:
                        df_export['Thời Gian'] = df_export['Thời Gian'].dt.tz_localize(None) 
                    df_export.to_excel(writer, sheet_name='Su_Kien_Giao_Thong', index=False)

                    if not df_heat.empty:
                        df_heat_export = df_heat.copy()
                        if 'Thời Gian' in df_heat_export.columns:
                            df_heat_export['Thời Gian'] = df_heat_export['Thời Gian'].dt.tz_localize(None)
                        df_heat_export.to_excel(writer, sheet_name='Du_Lieu_Un_Tac', index=False)

                st.download_button(
                    label="📊 TẢI BÁO CÁO EXCEL (.XLSX)",
                    data=buffer.getvalue(),
                    file_name=f"Bao_Cao_ITS_{start_date}_den_{end_date}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )

            with c_down2:
                csv_data = df.to_csv(index=False, encoding='utf-8-sig')
                st.download_button(
                    label="📄 TẢI DỮ LIỆU THÔ (.CSV)",
                    data=csv_data,
                    file_name=f"Raw_Data_{start_date}_den_{end_date}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
        else:
            st.warning("⚠️ Không có phương tiện nào lưu thông trong khoảng thời gian và trạm bạn đã chọn.")
    else:
        st.info("👈 Hãy chọn Thời gian, chọn Trạm và bấm [🔄 TRÍCH XUẤT DỮ LIỆU] để xem Báo cáo!")