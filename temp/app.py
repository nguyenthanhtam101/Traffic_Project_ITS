import streamlit as st
import cv2
import numpy as np
import pandas as pd
import time

# =================================================================
# 1. THIẾT KẾ GIAO DIỆN CƠ BẢN (LAYOUT)
# =================================================================
st.set_page_config(page_title="ITS Dashboard", layout="wide")
st.title("📊 HỆ THỐNG GIÁM SÁT GIAO THÔNG THÔNG MINH (ITS)")

# Tạo thanh Sidebar bên trái để chứa các nút bấm điều khiển
st.sidebar.header("🎛️ KHỐI CẤU HÌNH HỆ THỐNG")

# Tính năng 1: Khung Upload ảnh/video
uploaded_file = st.sidebar.file_uploader("Tải video/ảnh lên để kiểm tra", type=["mp4", "avi", "jpg", "png"])

# Tính năng 2: Nút chọn Chức năng chính
mode = st.sidebar.selectbox("Chọn chế độ giám sát chính:", ["Đo Tốc Độ", "Bắt Vượt Đèn Đỏ", "Phân Tích Mật Độ"])

# Tính năng 3: Các tùy chọn cụ thể (Sub-options) cho từng chức năng
if mode == "Đo Tốc Độ":
    st.sidebar.subheader("Cài đặt Đo Tốc Độ")
    # Biến speed_limit này sẽ thay đổi Live, code AI đọc vào sẽ cập nhật ngay
    speed_limit = st.sidebar.slider("Giới hạn tốc độ cho phép (km/h):", min_value=30, max_value=120, value=55)
    st.sidebar.info(f"Hệ thống sẽ phạt nếu tốc độ > {speed_limit} km/h")

elif mode == "Bắt Vượt Đèn Đỏ":
    st.sidebar.subheader("Cài đặt Đèn Giao Thông")
    allow_moto_right = st.sidebar.checkbox("Xe máy được phép rẽ phải khi đèn đỏ", value=True)
    turn_threshold = st.sidebar.number_input("Ngưỡng Vector rẽ (pixel):", value=80)

# =================================================================
# 2. KHU VỰC HIỂN THỊ CHÍNH (MAIN PANEL)
# =================================================================
# Chia màn hình làm 2 cột: Cột 1 hiện Video - Cột 2 hiện Biểu đồ phân tích
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("🎥 Luồng Video Xử Lý Live")
    # Khung trống để Streamlit liên tục đổ ảnh OpenCV vào làm video
    video_placeholder = st.empty() 

with col2:
    st.subheader("📈 Phân Tích Thống Kê")
    # Tạo các thẻ KPI hiển thị số liệu nhanh
    kpi1, kpi2 = st.columns(2)
    kpi1.metric(label="Tổng số xe đã đếm", value=124)
    kpi2.metric(label="Số ca vi phạm", value=12, delta="+2 ca mới")

    # Vẽ biểu đồ phân tích (Streamlit hỗ trợ vẽ trực tiếp từ dataframe)
    st.write("Tỷ lệ các loại xe vi phạm:")
    chart_data = pd.DataFrame({
        'Loại xe': ['Ô tô', 'Xe máy', 'Xe tải', 'Xe buýt'],
        'Số ca': [5, 4, 2, 1]
    })
    st.bar_chart(chart_data.set_index('Loại xe'))

# =================================================================
# 3. KẾT NỐI VỚI VÒNG LẶP AI (SIMULATION)
# =================================================================
if uploaded_file is not None:
    st.success("Đã tải file thành công! Ấn nút chạy để bắt đầu xử lý.")
    if st.sidebar.button("▶️ BẮT ĐẦU CHẠY AI"):
        
        # ĐOẠN NÀY BẠN CHÈN VÒNG LẶP WHILE CAP.ISOPENED() CỦA BẠN VÀO
        # Ví dụ mô phỏng vòng lặp render khung hình AI
        for frame_idx in range(100):
            # 1. Đọc frame từ video, ném qua YOLOv8 xử lý, vẽ khung
            # (Ở đây mình giả lập tạo một khung ảnh ngẫu nhiên)
            mock_frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
            cv2.putText(mock_frame, f"Mode: {mode} | Frame: {frame_idx}", (50, 50), 0, 1, (0, 255, 0), 2)
            
            # Nếu đang ở chế độ tốc độ, in cái giới hạn tốc độ live ra màn hình
            if mode == "Đo Tốc Độ":
                cv2.putText(mock_frame, f"LIMIT: {speed_limit} km/h", (50, 100), 0, 1, (255, 0, 0), 2)
            
            # 2. Đẩy khung hình đã xử lý lên giao diện Web
            # Chuyển BGR (OpenCV) sang RGB (Web)
            rgb_frame = cv2.cvtColor(mock_frame, cv2.COLOR_BGR2RGB)
            video_placeholder.image(rgb_frame, channels="RGB", use_container_width=True)
            
            time.sleep(0.03) # Mô phỏng FPS