import streamlit as st
import folium
from streamlit_folium import st_folium
import tempfile
import cv2
import threading
import queue
import csv
import os
import requests
import pandas as pd
import yt_dlp  
import base64
import urllib.parse
from datetime import datetime
from config import CSV_FILE_PATH, HEATMAP_CSV_PATH
from core.tracker_logic import run_opencv_setup, run_video_processing, run_opencctv_processing

# =====================================================================
# 🗄️ GỌI DATABASE ĐỂ LẤY DANH SÁCH CAMERA
# =====================================================================
from core.database import get_all_cameras

def fetch_weather(lat, lon):
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,wind_speed_10m&hourly=precipitation_probability&timezone=Asia/Bangkok&forecast_days=1"
        
        # 👉 FIX: Thêm User-Agent để API không tưởng là Bot spam, tăng thời gian chờ lên 10s chống rớt mạng
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(url, headers=headers, timeout=10)
        
        if res.status_code == 200:
            data = res.json()
            temp = data.get('current', {}).get('temperature_2m', '--')
            wind = data.get('current', {}).get('wind_speed_10m', '--')
            humidity = data.get('current', {}).get('relative_humidity_2m', '--')
            
            # Lấy xác suất mưa an toàn
            current_hour = datetime.now().hour
            try:
                rain_prob = data['hourly']['precipitation_probability'][current_hour]
            except:
                rain_prob = "--"
                
            return temp, wind, humidity, rain_prob
        else:
            return "--", "--", "--", "--"
    except:
        return "--", "--", "--", "--"

def get_youtube_stream_url(url):
    ydl_opts = {'format': 'best[ext=mp4]/best', 'quiet': True, 'no_warnings': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info['url']
    except Exception as e: return None

def get_image_base64(img_path):
    if os.path.exists(img_path):
        with open(img_path, "rb") as img_file:
            encoded = base64.b64encode(img_file.read()).decode()
            mime = "image/png" if img_path.lower().endswith(".png") else "image/jpeg"
            return f"data:{mime};base64,{encoded}"
    return ""

def render_tab_camera():
    try:
        OPENCCTV_CAMS = get_all_cameras()
    except Exception as e:
        st.error(f"⚠️ Không thể kết nối tới Database Postgres. Vui lòng kiểm tra Docker! {e}")
        OPENCCTV_CAMS = {}

    # 👉 Sửa lỗi an toàn: Nếu trạm cũ bị xóa mất, tự động lùi về trạm đầu tiên trong danh sách
    if ('selected_cam' not in st.session_state) or (st.session_state['selected_cam'] not in OPENCCTV_CAMS):
        if OPENCCTV_CAMS:
            st.session_state['selected_cam'] = list(OPENCCTV_CAMS.keys())[0]

    st.sidebar.header("🎛️ KHỐI CẤU HÌNH CAMERA")
    
    tab_offline, tab_live, tab_api = st.sidebar.tabs(["📂 Offline", "🌐 YouTube", "🌍 OpenCCTV"])
    
    with tab_offline:
        uploaded_file = st.file_uploader("Tải Video Lên", type=["mp4", "avi", "mov", "jpg"], label_visibility="collapsed")
        btn_run_offline = st.button("▶ CHẠY VIDEO",type="primary" ,use_container_width=True)
            
    with tab_live:
        youtube_url = st.text_input("Link YouTube Live:", placeholder="Dán link (VD: Tokyo...)")
        btn_run_youtube = st.button("▶ CHẠY YOUTUBE", type="primary", use_container_width=True)
            
    with tab_api:
        st.info("📡 Nguồn: OpenCCTV.org (0.5s/Frame)")
        if OPENCCTV_CAMS:
            selected_cam_from_sb = st.selectbox("📍 Chọn Vị Trí Camera:", list(OPENCCTV_CAMS.keys()), index=list(OPENCCTV_CAMS.keys()).index(st.session_state['selected_cam']))
            if selected_cam_from_sb != st.session_state['selected_cam']:
                st.session_state['selected_cam'] = selected_cam_from_sb
                st.rerun()
            btn_run_cctv = st.button("▶ CHẠY CAMERA OPENCCTV", type="primary", use_container_width=True)
        else:
            st.warning("Đang kết nối Database để tải trạm...")
            btn_run_cctv = False
        
    st.sidebar.markdown("---")

    run_just_detect = st.sidebar.checkbox(" Nhận Diện Tự Do", value=False)
    
    if run_just_detect:
        run_speed, run_redlight, run_wrongway = False, False, False
        speed_limit_live = 55
        allow_moto_right, allow_car_right = False, False
        
        st.sidebar.markdown("### ⚙️ Tùy Chỉnh Chuyên Sâu")
        jam_threshold = st.sidebar.slider("Ngưỡng cảnh báo kẹt xe (Số xe):", 5, 50, 15)
        show_hm = st.sidebar.toggle("🔥 Bật/Tắt Heatmap (Live)", value=st.session_state['live_config']['show_heatmap'])
        st.session_state['live_config']['show_heatmap'] = show_hm
        run_heatmap = False 
    else:
        st.sidebar.markdown("### 🛠️ Kích Hoạt Tính Năng")
        run_speed    = st.sidebar.checkbox("⚡ Đo Tốc Độ", value=True)
        run_redlight = st.sidebar.checkbox("🚥 Bắt Vượt Đèn Đỏ", value=False)
        run_wrongway = st.sidebar.checkbox("⛔ Bắt Ngược Chiều", value=False)

        st.sidebar.markdown("### ⚙️ Tùy Chỉnh Chuyên Sâu")
        speed_limit_live = st.sidebar.slider("Giới hạn tốc độ (km/h):", 20, 120, 55)
        jam_threshold = st.sidebar.slider("Ngưỡng cảnh báo kẹt xe (Số xe):", 5, 50, 15)
        show_hm = st.sidebar.toggle("🔥 Bật/Tắt Heatmap (Live)", value=st.session_state['live_config']['show_heatmap'])
        st.session_state['live_config']['show_heatmap'] = show_hm
        run_heatmap = st.sidebar.checkbox("🎯 Đo Kẹt xe trong Vùng Tự Chọn", value=False) if show_hm else False
        allow_moto_right = st.sidebar.checkbox("✅ Cho phép Xe Máy rẽ phải", value=True)
        allow_car_right  = st.sidebar.checkbox("✅ Cho phép Ô Tô rẽ phải", value=False)

    if st.sidebar.button("DỪNG HỆ THỐNG", type="primary", use_container_width=True):
        if 'stop_event' in st.session_state: st.session_state['stop_event'].set()
        st.session_state['running'] = False
        st.session_state['video_done'] = False
        if 'last_frame' in st.session_state: del st.session_state['last_frame'] # Xóa đệm
        st.rerun()

    st.sidebar.markdown("---")
    if st.sidebar.button("🚪 ĐĂNG XUẤT", use_container_width=True):
        if 'stop_event' in st.session_state: st.session_state['stop_event'].set()
        st.session_state['running'] = False
        st.session_state['authenticated'] = False
        if 'last_frame' in st.session_state: del st.session_state['last_frame']
        st.query_params.clear()
        st.rerun()

    # =========================================================
    # BỐ CỤC GIAO DIỆN CHÍNH
    # =========================================================
    col1, col2 = st.columns([2.5, 1])
    
    with col1:
        title_placeholder = st.empty()
        current_title = st.session_state.get('selected_cam', 'Chưa có')
        if uploaded_file is not None:
            current_title = f"Video Offline: {uploaded_file.name}"
        elif youtube_url:
            current_title = "Luồng YouTube Live"
            
        title_placeholder.markdown(f"### 🎥 Nguồn phát: {current_title}")
        
        video_container = st.container(border=True)
        with video_container:
            stframe = st.empty()
            
            # 👉 BỘ NHỚ ĐỆM CHỐNG GIẬT MÀN HÌNH
            if st.session_state.get('running') and 'last_frame' in st.session_state:
                stframe.image(st.session_state['last_frame'], channels="RGB", use_container_width=True)
            elif not st.session_state.get('running'):
                stframe.markdown("<div style='text-align: center; padding: 120px 0; color: gray; border: 1px dashed rgba(255,255,255,0.2); border-radius: 5px;'><h1 style='font-size: 50px;'>📺</h1><h3>Khung Hiển Thị Camera</h3></div>", unsafe_allow_html=True)
            
    with col2:
        st.markdown("### 📊 Thông Số")
        kpi_container = st.container(border=True)
        with kpi_container:
            kpi_count = st.empty()
            kpi_viol  = st.empty()
            kpi_count.metric("Lưu lượng xe / Mật độ (API)", 0)
            kpi_viol.metric("Tổng vi phạm phát hiện", 0)
            
        st.markdown("---")
        if not run_just_detect: st.info("💡 **HƯỚNG DẪN SETUP:**\n- **Chuột Trái:** Chọn điểm.\n- **Chuột Phải:** Xóa 1 điểm.\n- Phím **'C'**: Xóa toàn bộ.\n- Phím **'ESC'**: Hủy.\n- Phím **ENTER**: Lưu.")

    status_msg = st.empty()
    if st.session_state.get('video_done'):
        status_msg.success("🎉 **HOÀN THÀNH!** Xin mời chuyển sang **Tab 2** để xem báo cáo.", icon="✅")

    st.markdown("---")
    if OPENCCTV_CAMS and st.session_state.get('selected_cam'):
        curr_cam_data = OPENCCTV_CAMS.get(st.session_state['selected_cam'], {})
        
        # =================================================================
        # 👉 FIX LỖI "Location values cannot contain NaNs" CHO TRẠM CHÍNH
        # =================================================================
        try:
            lat = float(curr_cam_data.get('lat', 10.762622))
            lon = float(curr_cam_data.get('lon', 106.660172))
            if pd.isna(lat) or pd.isna(lon):
                lat, lon = 10.762622, 106.660172
        except:
            lat, lon = 10.762622, 106.660172
        
        c_map, c_weather = st.columns([2, 1])
        with c_map:
            st.markdown(f"#### 🗺️ Bản Đồ Camera TP.Hồ Chí Minh")
            m = folium.Map(location=[lat, lon], zoom_start=13, tiles="CartoDB dark_matter")
            for cam_name, cam_data in OPENCCTV_CAMS.items():
                
                # =================================================================
                # 👉 FIX LỖI "Location values cannot contain NaNs" CHO TRẠM LÂN CẬN
                # =================================================================
                try:
                    c_lat = float(cam_data.get('lat'))
                    c_lon = float(cam_data.get('lon'))
                    if pd.isna(c_lat) or pd.isna(c_lon):
                        continue # Bỏ qua không vẽ trạm này
                except:
                    continue

                if cam_name == st.session_state['selected_cam']:
                    marker_color, icon_type = "red", "facetime-video"
                else:
                    marker_color, icon_type = "cadetblue", "info-sign"

                folium.Marker(
                    [c_lat, c_lon], popup=cam_name, tooltip=cam_name,
                    icon=folium.Icon(color=marker_color, icon=icon_type, prefix='glyphicon')
                ).add_to(m)

            map_data = st_folium(m, height=270, use_container_width=True, key="interactive_map")
            if map_data and map_data.get("last_object_clicked_tooltip"):
                clicked_cam = map_data["last_object_clicked_tooltip"]
                if clicked_cam in OPENCCTV_CAMS and clicked_cam != st.session_state.get('selected_cam'):
                    st.session_state['selected_cam'] = clicked_cam
                    if 'stop_event' in st.session_state: st.session_state['stop_event'].set()
                    st.session_state['running'] = False
                    st.rerun()

            st.caption(f"📍 Tọa độ GPS: {lat}, {lon} | Khu vực: TP.HCM, Việt Nam")

        with c_weather:
            st.markdown("#### ⛅ Thời Tiết Thực Tế")
            temp, wind, humidity, rain_prob = fetch_weather(lat, lon)
            st.markdown(f"""
<div style='background-color: rgba(255,255,255,0.05); padding: 15px 20px; border-radius: 10px; border: 1px solid rgba(255,255,255,0.1); height: 100%; text-align: center;'>
<h1 style='color: #00E5FF; margin: 0; font-size: 45px;'>{temp}°C</h1>
<p style='color: gray; font-size: 15px; margin-top: 5px; margin-bottom: 5px;'>TP. Hồ Chí Minh</p>
<hr style='border-color: rgba(255,255,255,0.1); margin: 10px 0;'>
<div style='display: flex; justify-content: space-between; text-align: left; font-size: 14px; color: #EEE;'>
<div>
<p style='margin: 5px 0;'>🍃 Gió: <b>{wind} km/h</b></p>
<p style='margin: 5px 0;'>💧 Độ ẩm: <b>{humidity}%</b></p>
</div>
<div>
<p style='margin: 5px 0;'>🌦️ Mưa: <b>{rain_prob}%</b></p>
<p style='margin: 5px 0;'>🚦 Feed: <b>0.5s</b></p>
</div>
</div>
</div>
""", unsafe_allow_html=True)

        # =========================================================
        # 👉 CHỐT CSS KHUNG ẢNH LÂN CẬN (ĐỀU TĂM TẮP 16:9, KHÔNG MÉO)
        # =========================================================
        st.markdown("#### 🔄 Khu Vực Lân Cận")
        
        st.markdown("""
        <style>
        /* Ép khung chứa ảnh thành tỉ lệ 16:9, ảnh bên trong tự động thu phóng (cover) không bị cắt méo */
        div[data-testid="column"] div[data-testid="stImage"] {
            border-radius: 8px 8px 0 0;
            border: 1px solid #444; border-bottom: none;
            background-color: #0E1117; 
            aspect-ratio: 16 / 9;
            overflow: hidden;
        }
        div[data-testid="column"] div[data-testid="stImage"] img {
            width: 100% !important;
            height: 100% !important;
            object-fit: cover !important; 
        }
        /* Style cho nút bấm */
        div[data-testid="column"] div[data-testid="stButton"] button {
            border-radius: 0 0 8px 8px !important;
            border: 1px solid #444 !important;
            background-color: #1E1E1E !important;
            padding: 5px 8px !important;
            font-size: 13px !important;
            font-weight: 500 !important;
            color: white !important;
            transition: all 0.2s ease-in-out !important;
            white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
            margin-top: -15px !important; 
        }
        div[data-testid="column"] div[data-testid="stButton"] button:hover {
            border-color: #FF4B4B !important; color: #FF4B4B !important;
        }
        </style>
        """, unsafe_allow_html=True)

        other_cams = [cam for cam in OPENCCTV_CAMS.keys() if cam != st.session_state['selected_cam']][:5]
        cols = st.columns(5)
        
        for idx, col in enumerate(cols):
            if idx < len(other_cams):
                cam_name = other_cams[idx]
                cam_thumbnail = OPENCCTV_CAMS[cam_name]["img"]
                
                with col:
                    if os.path.exists(cam_thumbnail):
                        st.image(cam_thumbnail, use_container_width=True)
                    else:
                        st.markdown("<div style='width: 100%; aspect-ratio: 16/9; background: #2E2E2E; display: flex; align-items: center; justify-content: center; border: 1px solid #444; border-bottom: none; border-radius: 8px 8px 0 0;'><span style='font-size: 20px; color: #555;'>📷 Trống</span></div>", unsafe_allow_html=True)

                    if st.button(cam_name, key=f"sim_{cam_name}", use_container_width=True):
                        st.session_state['selected_cam'] = cam_name
                        if 'stop_event' in st.session_state: st.session_state['stop_event'].set()
                        st.session_state['running'] = False
                        st.rerun()

    is_starting = False
    video_source = None
    is_cctv_mode = False

    if btn_run_offline and uploaded_file is not None:
        title_placeholder.markdown(f"### 🎥 Đang phân tích Video: {uploaded_file.name}")
        tfile = tempfile.NamedTemporaryFile(delete=False, suffix='.mp4')
        tfile.write(uploaded_file.read())
        tfile.flush()
        video_source = tfile.name
        is_starting = True

    elif btn_run_youtube and youtube_url:
        title_placeholder.markdown("### 🎥 Đang phân tích: YouTube Live")
        with st.spinner("⏳ Đang giải mã luồng Live từ YouTube..."):
            stream_url = get_youtube_stream_url(youtube_url)
            if stream_url:
                video_source = stream_url
                is_starting = True
            else:
                status_msg.error("❌ Không thể lấy luồng từ Link YouTube này! Hãy thử link khác.")

    elif btn_run_cctv and OPENCCTV_CAMS:
        title_placeholder.markdown(f"### 🎥 Camera Live: {st.session_state['selected_cam']}")
        cctv_url = OPENCCTV_CAMS[st.session_state['selected_cam']]['url']
        is_cctv_mode = True
        is_starting = True

    if is_starting:
        st.session_state['video_done'] = False 
        
        with open(CSV_FILE_PATH, mode='w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(['Thời Gian', 'ID Xe', 'Loại Phương Tiện', 'Tốc Độ (km/h)', 'Biển Số', 'Trạng Thái'])
        with open(HEATMAP_CSV_PATH, mode='w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(['Thời Gian', 'Số Xe Kẹt'])
        
        frame_queue, kpi_queue, stop_event = queue.Queue(maxsize=5), queue.Queue(maxsize=20), threading.Event()
        
        if is_cctv_mode:
            status_msg.empty()
            st.toast(f"✅ Đang lấy dữ liệu từ trạm {st.session_state['selected_cam']}...", icon="🌍")
            ai_thread = threading.Thread(target=run_opencctv_processing, args=(cctv_url, frame_queue, kpi_queue, stop_event), daemon=True)
            
        else:
            with st.spinner("Đang khởi tạo Camera..."):
                cap_init = cv2.VideoCapture(video_source)
                cap_init.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                ok, first_frame = cap_init.read()
                cap_init.release()

            if not ok: status_msg.error("❌ Lỗi! Không thể đọc khung hình từ Camera/Video này."); st.stop()
            orig_h, orig_w = first_frame.shape[:2]
            DISPLAY_W, DISPLAY_H = 1280, int(orig_h * (1280 / orig_w))
            first_frame = cv2.resize(first_frame, (DISPLAY_W, DISPLAY_H))

            if not run_just_detect:
                status_msg.info("⚙️ Hệ thống đang mở cửa sổ đồ họa (OpenCV) để bạn Setup. Hãy kiểm tra dưới thanh Taskbar!")
                setup_result = run_opencv_setup(first_frame, run_speed, run_redlight, run_wrongway, run_heatmap, DISPLAY_H, DISPLAY_W)
                if not setup_result['ok']: status_msg.warning(f"🛑 {setup_result['cancel_reason']}"); st.stop()
            else:
                setup_result = {'M_matrix': None, 'speed_polygon': None, 'rl_light_straight_roi': (0,0,0,0), 'rl_monitor_polygon': None, 'ww_polygon': None, 'ww_vector': None, 'heatmap_polygon': None}

            status_msg.empty() 
            st.toast("✅ Đã kết nối thành luồng Video! AI đang phân tích...", icon="🚀")
            ai_thread = threading.Thread(target=run_video_processing, args=(video_source, setup_result, run_speed, run_redlight, run_wrongway, run_heatmap, speed_limit_live, allow_moto_right, allow_car_right, jam_threshold, st.session_state['live_config'], DISPLAY_W, DISPLAY_H, frame_queue, kpi_queue, stop_event), daemon=True)
        
        st.session_state['ai_thread'] = ai_thread
        st.session_state['frame_queue'] = frame_queue
        st.session_state['kpi_queue'] = kpi_queue
        st.session_state['stop_event'] = stop_event
        st.session_state['running'] = True
        ai_thread.start()

    if st.session_state.get('running'):
        ai_thread = st.session_state['ai_thread']
        frame_queue = st.session_state['frame_queue']
        kpi_queue = st.session_state['kpi_queue']
        stop_event = st.session_state['stop_event']

        while ai_thread.is_alive() or not frame_queue.empty():
            try:
                msg = kpi_queue.get_nowait()
                if msg[0] == "KPI":
                    kpi_count.metric("Lưu lượng xe / Mật độ (API)", msg[1])
                    kpi_viol.metric("Tổng vi phạm phát hiện", msg[2])
                elif msg[0] == "DONE":
                    kpi_count.metric("Lưu lượng xe / Mật độ (API)", msg[1])
                    kpi_viol.metric("Tổng vi phạm phát hiện", msg[2])
                    st.session_state['video_done'] = True
                    st.session_state['running'] = False
                    st.balloons()
                    st.rerun() 
                    break
                elif msg[0] == "ERROR":
                    status_msg.error(f"❌ Lỗi AI: {msg[1]}")
                    st.session_state['running'] = False
                    break
            except queue.Empty: pass

            try:
                frame_rgb = frame_queue.get(timeout=0.05)
                # LƯU FRAME CUỐI VÀO ĐỆM ĐỂ CHỐNG GIẬT
                st.session_state['last_frame'] = frame_rgb 
                stframe.image(frame_rgb, channels="RGB", use_container_width=True)
            except queue.Empty: pass

        stop_event.set()
        ai_thread.join(timeout=10)