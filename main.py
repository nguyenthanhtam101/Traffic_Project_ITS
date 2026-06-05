# Ctr + Shift + P -> Python: Select Interpreter -> Chọn Python có môi trường doan_giao_thong
import os
import streamlit as st
import pandas as pd
import plotly.io as pio
import bcrypt
import time
import base64
from sqlalchemy import text

# Import kết nối Database
from core.database import engine, init_db

# Bật thư viện xử lý ảnh trùng lặp (tránh lỗi crash)
os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'
pio.templates.default = "plotly_dark"

# Khởi tạo giao diện trang web (BẮT BUỘC PHẢI GỌI ĐẦU TIÊN)
st.set_page_config(page_title="Hệ Thống ITS HCMC", layout="wide", page_icon="🚦")

# =========================================================
# 👉 CSS HACK: VÒNG XOAY LOADING CHUYÊN NGHIỆP TRUNG TÂM & CHỐNG MỜ MÀN HÌNH
# =========================================================
st.markdown("""
    <style>
    /* 1. Tắt hoàn toàn hiệu ứng mờ (dimming) màn hình khi Streamlit Load */
    [data-testid="stAppViewContainer"], 
    [data-testid="stMainBlockContainer"], 
    .stApp {
        opacity: 1 !important;
        transition: none !important;
    }

    /* 2. Bắt lấy thanh trạng thái tải mặc định, mang ra giữa làm Popup */
    [data-testid="stStatusWidget"] {
        position: fixed !important;
        top: 50% !important;
        left: 50% !important;
        transform: translate(-50%, -50%) !important;
        background-color: rgba(14, 17, 23, 0.95) !important;
        border: 2px solid #00E5FF !important;
        padding: 8px 20px !important;
        border-radius: 12px !important;
        box-shadow: 0px 0px 30px rgba(0, 229, 255, 0.4) !important;
        z-index: 999999 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
    }
    
    
    
    /* 4. Trang trí font chữ báo trạng thái */
    [data-testid="stStatusWidget"] span {
        color: #00E5FF !important;
        font-size: 18px !important;
        font-weight: bold !important;
    }
    
    /* 5. Ẩn nút Stop mặc định */
    [data-testid="stStatusWidget"] button {
        display: none !important;
    }
    </style>
""", unsafe_allow_html=True)

# Khởi tạo Database ngay từ đầu để tránh lỗi "UndefinedTable"
try:
    init_db()
except Exception as e:
    st.error(f"Lỗi kết nối DB: {e}")

# =========================================================
# HÀM BẢO MẬT: BĂM PASS & MÃ HÓA URL (SESSION TẠM)
# =========================================================
def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def check_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def register_user(username, password, full_name):
    with engine.connect() as conn:
        res = conn.execute(text("SELECT id FROM users WHERE username=:u"), {"u": username}).fetchone()
        if res: return False 
        
        hashed = hash_password(password)
        conn.execute(text("INSERT INTO users (username, password_hash, full_name) VALUES (:u, :p, :f)"),
                     {"u": username, "p": hashed, "f": full_name})
        conn.commit()
        return True

def authenticate_user(username, password):
    with engine.connect() as conn:
        res = conn.execute(text("SELECT password_hash, full_name FROM users WHERE username=:u"), {"u": username}).fetchone()
        if res:
            if check_password(password, res[0]):
                return res[1] 
    return None

# 👉 HÀM MÃ HÓA & GIẢI MÃ TÀI KHOẢN TRÊN THANH URL
def encode_session(username):
    return base64.b64encode(username.encode('utf-8')).decode('utf-8')

def decode_session(token):
    try:
        return base64.b64decode(token.encode('utf-8')).decode('utf-8')
    except:
        return None

# =========================================================
# KHỞI CHẠY HỆ THỐNG BẢO MẬT ĐĂNG NHẬP (AUTHENTICATION)
# =========================================================
if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
    
    # 👉 BỘ NHỚ ĐỆM ĐĂNG NHẬP: Kiểm tra URL khi người dùng bấm F5
    if "session" in st.query_params:
        saved_user = decode_session(st.query_params["session"])
        if saved_user == "admin":
            st.session_state['authenticated'] = True
            st.session_state['current_user_name'] = "Super Admin"
        elif saved_user:
            try:
                with engine.connect() as conn:
                    res = conn.execute(text("SELECT full_name FROM users WHERE username=:u"), {"u": saved_user}).fetchone()
                    if res:
                        st.session_state['authenticated'] = True
                        st.session_state['current_user_name'] = res[0]
            except:
                pass


def login_register_router():
    st.markdown("<br><br><br>", unsafe_allow_html=True)
    st.markdown("<h1 style='text-align: center; color: #00E5FF; text-shadow: 0 0 10px rgba(0,229,255,0.5);'>🚦 TRUNG TÂM ĐIỀU HÀNH ITS TP.HCM</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: gray; font-size: 18px;'>Hệ thống Giám sát & Quản lý Giao thông Thông minh</p>", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        modes = ["🔑 Đăng Nhập", "📝 Đăng Ký Tài Khoản"]

        # Dùng key để Streamlit tự quản lý state
        auth_mode = st.radio(
            "Điều hướng:",
            modes,
            key='auth_mode_radio',       
            horizontal=True,
            label_visibility="collapsed"
        )

        # =========== FORM ĐĂNG NHẬP ===========
        if auth_mode == "🔑 Đăng Nhập":
            with st.form("login_form"):
                st.markdown("### 🔐 ĐĂNG NHẬP HỆ THỐNG")
                username = st.text_input("👤 Tên tài khoản", placeholder="Nhập tài khoản...")
                password = st.text_input("🔑 Mật khẩu", type="password", placeholder="Nhập mật khẩu...")
                submit_log = st.form_submit_button("ĐĂNG NHẬP", type="primary", use_container_width=True)

                if submit_log:
                    if username == "admin" and password == "admin123":
                        st.session_state['authenticated'] = True
                        st.session_state['current_user_name'] = "Super Admin"
                        st.query_params["session"] = encode_session("admin") 
                        st.rerun()
                    else:
                        full_name = authenticate_user(username, password)
                        if full_name:
                            st.session_state['authenticated'] = True
                            st.session_state['current_user_name'] = full_name
                            st.query_params["session"] = encode_session(username) 
                            st.rerun()
                        else:
                            st.error("❌ Tài khoản hoặc mật khẩu không chính xác!")

        # =========== FORM ĐĂNG KÝ ===========
        elif auth_mode == "📝 Đăng Ký Tài Khoản":
            with st.form("register_form"):
                st.markdown("### 📝 TẠO TÀI KHOẢN MỚI")
                reg_name = st.text_input("🎫 Họ và tên hiển thị", placeholder="VD: Nguyễn Thành Tâm")
                reg_user = st.text_input("👤 Tên tài khoản (để đăng nhập)", placeholder="VD: tamnguyen")
                reg_pass = st.text_input("🔑 Mật khẩu", type="password")
                reg_pass_conf = st.text_input("🔑 Xác nhận mật khẩu", type="password")
                submit_reg = st.form_submit_button("ĐĂNG KÝ NGAY", type="primary", use_container_width=True)

                if submit_reg:
                    if not reg_name or not reg_user or not reg_pass:
                        st.error("❌ Vui lòng điền đầy đủ các thông tin!")
                    elif len(reg_pass) < 6:
                        st.error("❌ Mật khẩu phải có ít nhất 6 ký tự!")
                    elif reg_pass != reg_pass_conf:
                        st.error("❌ Mật khẩu xác nhận không khớp!")
                    else:
                        success = register_user(reg_user, reg_pass, reg_name)
                        if success:
                            st.success("✅ Đăng ký thành công! Hãy tiếp tục đăng nhập...")
                            st.session_state['auth_mode_radio'] = "🔑 Đăng Nhập"
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error("❌ Tên tài khoản đã tồn tại!")

if not st.session_state['authenticated']:
    login_register_router()
    st.stop() 

# =========================================================
# VÙNG KHÔNG GIAN ĐÃ XÁC THỰC
# =========================================================
st.markdown("""
    <style>
    header { background-color: transparent !important; }
    .stDeployButton { display: none !important; }
    .block-container {
        padding-top: 2rem !important; padding-bottom: 0rem !important; max-width: 95% !important;
    }
    div[data-testid="stTabs"] { overflow: visible !important; }
    section[data-testid="stMain"] div[data-testid="stTabs"] > div:first-child,
    section[data-testid="stMain"] div[data-baseweb="tab-list"],
    section[data-testid="stMain"] div[role="tablist"] {
        position: -webkit-sticky !important; position: sticky !important;
        top: 0px !important; z-index: 99999 !important; 
        background-color: #0E1117 !important; padding-top: 15px !important;
        padding-bottom: 10px !important; border-bottom: 1px solid rgba(255,255,255,0.2) !important;
    }
    section[data-testid="stMain"] button[data-baseweb="tab"] > div > span {
        font-size: 22px !important; font-weight: bold !important; color: #00E5FF !important; 
    }
    [data-testid="stSidebar"] div[data-baseweb="tab-list"] {
        background-color: #11151A !important; border-radius: 12px !important; padding: 5px !important;
    }
    [data-testid="stSidebar"] button[data-baseweb="tab"][aria-selected="true"] > div > span {
        color: #FF4B4B !important; 
    }
    [data-testid="stSidebar"] button[data-baseweb="tab"][aria-selected="true"] {
        border-bottom-color: #FF4B4B !important;
    }
    </style>
""", unsafe_allow_html=True)

user_display = st.session_state.get('current_user_name', 'Quản Trị Viên')
st.sidebar.markdown(f"### 👮‍♂️ Xin chào, {user_display}!")
st.sidebar.markdown("---")

st.markdown("<h2 style='text-align: center; color: #00E5FF; padding-top: 0; margin-top: -1rem;'>🚦 HỆ THỐNG GIÁM SÁT & ĐIỀU HÀNH GIAO THÔNG (ITS)</h2>", unsafe_allow_html=True)
st.markdown("---")

if 'live_config' not in st.session_state: st.session_state['live_config'] = {'show_heatmap': False}
if 'live_history' not in st.session_state: st.session_state['live_history'] = pd.DataFrame()

# =========================================================
# GỌI CÁC GIAO DIỆN TABS
# =========================================================
from ui.tab_camera import render_tab_camera
from ui.tab_analytics import render_tab_analytics
from ui.tab_livemap import render_tab_livemap
from ui.tab_admin import render_tab_admin 

is_admin = (st.session_state.get('current_user_name') == "Super Admin")

if is_admin:
    tab_monitor, tab_analytics, tab_live, tab_admin = st.tabs([
        "🎥  Giám Sát Trực Tiếp", 
        "📊  Phân Tích Dữ Liệu SQL", 
        "🌍  Bản Đồ Giao Thông Live", 
        "⚙️ Quản Trị (Admin)"
    ])
    with tab_monitor: render_tab_camera()
    with tab_analytics: render_tab_analytics()
    with tab_live: render_tab_livemap()
    with tab_admin: render_tab_admin() 
else:
    tab_monitor, tab_analytics, tab_live = st.tabs([
        "🎥  Giám Sát Trực Tiếp", 
        "📊  Phân Tích Dữ Liệu SQL", 
        "🌍  Bản Đồ Giao Thông Live"
    ])
    with tab_monitor: render_tab_camera()
    with tab_analytics: render_tab_analytics()
    with tab_live: render_tab_livemap()