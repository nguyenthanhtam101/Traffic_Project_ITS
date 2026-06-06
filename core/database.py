import os
import pandas as pd
from sqlalchemy import create_engine, text

# Chuỗi kết nối tới Postgres trên Docker
DATABASE_URL = "postgresql://postgres.zrvmvkhhozxxznpwdwnk:nguyenthanhtam21@aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres"
engine = create_engine(DATABASE_URL)

# Dữ liệu hạt giống (Seed Data) - Sẽ tự động nạp vào Postgres ở lần chạy đầu tiên
DEFAULT_CAMS = {
    "Phan Đăng Lưu - Thích Quảng Đức": {"url": "https://opencctv.org/api/feed/vietnam-hcmc-6623e8da6f998a001b2524a6?src=seo&t=1780241548&s=wro4za?_t=1780241555818", "lat": 10.8033, "lon": 106.6845, "img": "images/phan_dang_luu.png"},
    "Quang Trung - Số 625": {"url": "https://opencctv.org/api/feed/vietnam-hcmc-662b558c1afb9c00172d8ed2?src=seo&t=1780240551&s=1o5xily?_t=1780240566392", "lat": 10.8351, "lon": 106.6635, "img": "images/quang_trung.png"},
    "Trần Quang Khải - Trần Khắc Chân": {"url": "https://opencctv.org/api/feed/vietnam-hcmc-662b86c41afb9c00172dd31c?src=seo&t=1780241333&s=9urj6i?_t=1780241352918", "lat": 10.7919, "lon": 106.6911, "img": "images/tran_quang_khai.png"},
    "Tô Ngọc Vân - TX25": {"url": "https://opencctv.org/api/feed/vietnam-hcmc-5a6065c58576340017d06615?src=seo&t=1780241372&s=o210aw?_t=1780241374734", "lat": 10.8797, "lon": 106.6780, "img": "images/to_ngoc_van.png"},
    "Quốc Lộ 13 - Cầu Ông Dầu": {"url": "https://opencctv.org/api/feed/vietnam-hcmc-6623f4df6f998a001b2528eb?src=seo&t=1780241415&s=z7uevy?_t=1780241430001", "lat": 10.8362, "lon": 106.7138, "img": "images/ql13.png"},
    "CMT8 - Bùi Thị Xuân": {"url": "https://opencctv.org/api/feed/vietnam-hcmc-662b7ce71afb9c00172dc676?src=seo&t=1780241458&s=1pavu2k?_t=1780241464047", "lat": 10.7726, "lon": 106.6911, "img": "images/cmt8.png"},
    "Nguyễn Thị Định - Đường D": {"url": "https://opencctv.org/api/feed/vietnam-hcmc-583f969161cfea0012cf68f7?src=seo&t=1780241484&s=c4mxi8?_t=1780241489064", "lat": 10.7647, "lon": 106.7814, "img": "images/nguyen_thi_dinh.png"},
    "QL1 - Tỉnh lộ 10B": {"url": "https://opencctv.org/api/feed/vietnam-hcmc-59ca317602eb490011a0a408?src=seo&t=1780241510&s=jrne20?_t=1780241515253", "lat": 10.7492, "lon": 106.5940, "img": "images/ql1.png"},
    "An Dương Vương - Trần Phú": {"url": "https://opencctv.org/api/feed/vietnam-hcmc-66b1c1bf779f740018673ef2?src=seo&t=1780241616&s=yqjgmo?_t=1780241628799", "lat": 10.7339, "lon": 106.7032, "img": "images/an_duong_vuong.png"},
    "Bạch Đằng - Đặng Văn Sâm": {"url": "https://opencctv.org/api/feed/vietnam-hcmc-662b56c51afb9c00172d9071?src=seo&t=1780241745&s=zadxiw?_t=1780241748181", "lat": 10.8146, "lon": 106.6717, "img": "images/bach_dang.png"}
}

# Dữ liệu hạt giống cho TomTom API
DEFAULT_TOMTOM = {
    "Hầm Thủ Thiêm (Q1)": "10.7716,106.7133",
    "Ngã 6 Phù Đổng (Q1)": "10.7714,106.6923",
    "Vòng xoay Điện Biên Phủ (Q1)": "10.7915,106.7011",
    "Ngã Tư Hàng Xanh (Bình Thạnh)": "10.8015,106.7111",
    "Cầu Sài Gòn (Bình Thạnh)": "10.7984,106.7233",
    "Ngã Tư Phú Nhuận (Phú Nhuận)": "10.7981,106.6775",
    "Vòng Xoay Lăng Cha Cả (Tân Bình)": "10.7986,106.6575",
    "Ngã Tư Bảy Hiền (Tân Bình)": "10.7932,106.6527",
    "Vòng xoay Phạm Văn Đồng (Gò Vấp)": "10.8202,106.6874",
    "Vòng Xoay Dân Chủ (Q3)": "10.7935,106.6806",
    "Ngã 7 Lý Thái Tổ (Q10)": "10.7672,106.6745",
    "Cầu Vượt 3/2 (Q10)": "10.7735,106.6722",
    "Cầu Kênh Tẻ (Q4)": "10.7548,106.6975",
    "Ngã tư Nguyễn Văn Linh (Q7)": "10.7339,106.7032",
    "Ngã tư An Sương (Q12)": "10.8333,106.6136"
}

def init_db():
    """Khởi tạo cấu trúc bảng và nạp dữ liệu Camera vào Database"""
    with engine.connect() as conn:
        # Bảng người dùng
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))
        # Tạo bảng chứa trạm Camera
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS cameras (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) UNIQUE NOT NULL,
                url TEXT NOT NULL,
                lat DOUBLE PRECISION,
                lon DOUBLE PRECISION,
                img VARCHAR(255)
            );
        """))
        # Tạo bảng chứa dữ liệu Sự kiện giao thông (Lưu từ AI)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS traffic_events (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                vehicle_id INT,
                vehicle_type VARCHAR(50),
                speed INT,
                plate_text VARCHAR(50),
                status VARCHAR(50),
                camera_name VARCHAR(255)
            );
        """))
        # Tạo bảng chứa danh sách nút giao TomTom
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tomtom_intersections (
                name VARCHAR(255) PRIMARY KEY,
                lat FLOAT,
                lon FLOAT
            )
        """))
        conn.commit()

        # TỰ ĐỘNG TẠO DỮ LIỆU: Đổ 10 trạm mẫu Camera
        check_cam = conn.execute(text("SELECT COUNT(*) FROM cameras")).scalar()
        if check_cam == 0:
            for name, data in DEFAULT_CAMS.items():
                conn.execute(text("INSERT INTO cameras (name, url, lat, lon, img) VALUES (:name, :url, :lat, :lon, :img)"),
                    {"name": name, "url": data['url'], "lat": data['lat'], "lon": data['lon'], "img": data['img']})
            conn.commit()
            print("✅ Đã nạp thành công 10 Camera mẫu vào PostgreSQL!")

        # TỰ ĐỘNG TẠO DỮ LIỆU: Đổ 15 nút giao mẫu TomTom
        check_tomtom = conn.execute(text("SELECT COUNT(*) FROM tomtom_intersections")).scalar()
        if check_tomtom == 0:
            for name, coords in DEFAULT_TOMTOM.items():
                lat, lon = coords.split(',')
                conn.execute(text("INSERT INTO tomtom_intersections (name, lat, lon) VALUES (:name, :lat, :lon)"),
                    {"name": name, "lat": float(lat), "lon": float(lon)})
            conn.commit()
            print("✅ Đã nạp thành công 15 Nút giao TomTom mẫu vào PostgreSQL!")

def get_all_cameras():
    """Lấy toàn bộ camera từ PostgreSQL đẩy lên cho Streamlit UI"""
    init_db() # Gọi hàm này để đảm bảo DB luôn sẵn sàng
    df = pd.read_sql("SELECT name, url, lat, lon, img FROM cameras", engine)
    # Trả về format Dictionary y hệt như cũ để UI không bị vỡ logic
    return df.set_index('name').to_dict('index')

def get_tomtom_hotspots():
    """Lấy danh sách nút giao TomTom từ DB chuyển thành Dictionary cho LiveMap"""
    init_db()
    df = pd.read_sql("SELECT name, lat, lon FROM tomtom_intersections", engine)
    hotspots_dict = {}
    for index, row in df.iterrows():
        hotspots_dict[row['name']] = f"{row['lat']},{row['lon']}"
    return hotspots_dict

def save_event_to_db(vehicle_id, v_type, speed, plate, status, cam_name):
    """Hàm này sẽ dùng ở bước sau, cho AI gọi để lưu thông tin xe vào Database"""
    query = text("""
        INSERT INTO traffic_events (vehicle_id, vehicle_type, speed, plate_text, status, camera_name)
        VALUES (:v_id, :v_type, :speed, :plate, :status, :cam_name)
    """)
    with engine.connect() as conn:
        conn.execute(query, {
            "v_id": vehicle_id, "v_type": v_type, "speed": speed,
            "plate": plate, "status": status, "cam_name": cam_name
        })
        conn.commit()