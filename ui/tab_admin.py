import streamlit as st
import pandas as pd
from sqlalchemy import text
import time
from core.database import engine

def render_tab_admin():
    st.markdown("<h3 style='color: #00E5FF;'>⚙️ TRUNG TÂM QUẢN TRỊ HỆ THỐNG (ADMIN DASHBOARD)</h3>", unsafe_allow_html=True)
    
    if st.session_state.get('current_user_name') != "Super Admin":
        st.error("⛔ BẠN KHÔNG CÓ QUYỀN TRUY CẬP TRANG NÀY.")
        return
        
    st.info("💡 Tính năng tương tác trực tiếp với Database. Bấm [LƯU THAY ĐỔI] để đồng bộ thẳng vào PostgreSQL.")
    
    # 👉 THÊM TAB THỨ 4 ĐỂ QUẢN LÝ TOMTOM
    t_users, t_cams, t_events, t_tomtom = st.tabs([
        "👥 Quản lý Người dùng", 
        "🎥 Quản lý Camera", 
        "🚗 Dữ liệu Giao thông",
        "🌍 Nút giao TomTom"
    ])
    
    with t_users:
        st.markdown("#### Bảng Dữ Liệu Tài Khoản")
        try:
            df_users = pd.read_sql("SELECT id, username, full_name, created_at FROM users", engine)
            st.dataframe(df_users, use_container_width=True)
            
            st.markdown("#### 🗑️ Xóa Tài Khoản Vi Phạm")
            c1, c2 = st.columns([3, 1])
            with c1:
                user_to_delete = st.selectbox("Chọn tài khoản cần xóa:", df_users['username'].tolist() if not df_users.empty else [])
            with c2:
                st.markdown("<br>", unsafe_allow_html=True) 
                if st.button("XÓA TÀI KHOẢN NÀY", type="primary", use_container_width=True):
                    if user_to_delete == "admin":
                        st.warning("⚠️ Không thể xóa tài khoản Super Admin mặc định của hệ thống!")
                    else:
                        with engine.connect() as conn:
                            conn.execute(text("DELETE FROM users WHERE username=:u"), {"u": user_to_delete})
                            conn.commit()
                        st.success(f"✅ Đã xóa vĩnh viễn tài khoản: {user_to_delete}!")
                        time.sleep(1)
                        st.rerun()
        except Exception as e:
            st.error(f"Lỗi tải danh sách users: {e}")

    with t_cams:
        st.markdown("#### Danh Sách Trạm Camera Giao Thông")
        try:
            df_cams = pd.read_sql("SELECT * FROM cameras", engine)
            edited_cams = st.data_editor(
                df_cams, num_rows="dynamic", use_container_width=True, height=400, key="camera_editor"
            )
            
            if st.button("💾 LƯU THAY ĐỔI LÊN DATABASE POSTGRESQL", type="primary", key="save_cams"):
                with st.spinner("Đang đồng bộ dữ liệu..."):
                    clean_df = edited_cams.dropna(subset=['name'])
                    clean_df.to_sql("cameras", engine, if_exists="replace", index=False)
                
                st.success("✅ Cập nhật trạm Camera thành công! Hệ thống sẽ tự động làm mới ngay bây giờ.")
                time.sleep(1.5)
                st.rerun()
        except Exception as e:
            st.error(f"⚠️ Lỗi kết nối bảng 'cameras': {e}")

    with t_events:
        st.markdown("#### Dữ Liệu Sự Kiện Giao Thông (traffic_events)")
        st.caption("👇 Bạn có thể xem, chỉnh sửa tốc độ, biển số, thay đổi trạng thái hoặc xóa dòng dữ liệu bị sai.")
        try:
            df_events = pd.read_sql("SELECT * FROM traffic_events ORDER BY timestamp DESC", engine)
            
            edited_events = st.data_editor(
                df_events, 
                num_rows="dynamic", 
                use_container_width=True, 
                height=400, 
                key="events_editor"
            )
            
            if st.button("💾 LƯU THAY ĐỔI SỰ KIỆN LÊN DATABASE", type="primary", key="save_events"):
                with st.spinner("Đang đồng bộ dữ liệu sự kiện..."):
                    clean_events_df = edited_events.dropna(subset=['timestamp'])
                    clean_events_df.to_sql("traffic_events", engine, if_exists="replace", index=False)
                
                st.success("✅ Đã lưu cập nhật dữ liệu sự kiện giao thông thành công!")
                time.sleep(1.5)
                st.rerun()
        except Exception as e:
            st.error(f"⚠️ Chưa có dữ liệu bảng 'traffic_events' hoặc bảng đang trống. Lỗi chi tiết: {e}")

    # ====================================================
    # TAB 4: QUẢN LÝ NÚT GIAO TOMTOM API
    # ====================================================
    with t_tomtom:
        st.markdown("#### Danh Sách Nút Giao Giám Sát (TomTom API)")
        st.caption("👇 Nhập Tên nút giao và tọa độ (Vĩ độ - lat, Kinh độ - lon) để hệ thống gọi API quét dữ liệu kẹt xe. Không giới hạn số lượng!")
        
        try:
            # 1. Tự động tạo bảng tomtom_intersections nếu DB chưa có
            with engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS tomtom_intersections (
                        name VARCHAR(255) PRIMARY KEY,
                        lat FLOAT,
                        lon FLOAT
                    )
                """))
                conn.commit()
            
            # 2. Lấy dữ liệu hiện tại
            df_tomtom = pd.read_sql("SELECT name, lat, lon FROM tomtom_intersections", engine)
            
            # Nếu bảng mới tinh chưa có gì, tạo sẵn 2 trạm mẫu ở TP.HCM cho bạn dễ hình dung
            if df_tomtom.empty:
                df_tomtom = pd.DataFrame([
                    {"name": "Ngã Tư Hàng Xanh", "lat": 10.8000, "lon": 106.7112},
                    {"name": "Vòng Xoay Lăng Cha Cả", "lat": 10.8016, "lon": 106.6586}
                ])
            
            # 3. Mở bảng Editor cho phép chỉnh sửa
            edited_tomtom = st.data_editor(
                df_tomtom, 
                num_rows="dynamic", 
                use_container_width=True, 
                height=400, 
                key="tomtom_editor"
            )
            
            # 4. Lưu lại vào DB
            if st.button("💾 LƯU DANH SÁCH NÚT GIAO LÊN DATABASE", type="primary", key="save_tomtom"):
                with st.spinner("Đang đồng bộ mạng lưới TomTom..."):
                    # Xóa các dòng nhập lỗi thiếu tọa độ hoặc tên
                    clean_tomtom = edited_tomtom.dropna(subset=['name', 'lat', 'lon'])
                    clean_tomtom.to_sql("tomtom_intersections", engine, if_exists="replace", index=False)
                
                st.success("✅ Đã cập nhật mạng lưới nút giao TomTom thành công!")
                time.sleep(1.5)
                st.rerun()
                
        except Exception as e:
            st.error(f"⚠️ Lỗi xử lý bảng 'tomtom_intersections': {e}")