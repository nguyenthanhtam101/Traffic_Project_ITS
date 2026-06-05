import cv2
import os

def main():
    # Tạo thư mục con để chứa các bức ảnh khó
    output_dir = "hard_negatives"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Đọc video gốc của bạn
    video_path = "vdtest.mp4"
    cap = cv2.VideoCapture(video_path)

    print("HUONG DAN TRICH XUAT ANH:")
    print("- Bấm phím SPACE để TẠM DỪNG / CHẠY TIẾP video.")
    print("- Bấm phím 's' để LƯU LẠI KHUNG HÌNH hiện tại (Lưu ảnh gốc nét nhất).")
    print("- Bấm phím 'q' để THOÁT.")

    saved_count = 0
    paused = False

    while cap.isOpened():
        # Nếu không bị tạm dừng thì mới đọc frame tiếp theo
        if not paused:
            success, frame = cap.read()
            if not success:
                print("Đã chạy hết video!")
                break
            
            # Chỉ thu nhỏ lúc hiển thị để vừa màn hình
            display_frame = cv2.resize(frame, (1280, 720))

        cv2.imshow("Trich Xuat Anh - Hard Negative", display_frame)

        key = cv2.waitKey(30) & 0xFF

        if key == ord('q'):
            break
        elif key == 32:  # Phím Space
            paused = not paused
        elif key == ord('s'):
            saved_count += 1
            # Lưu bức ảnh gốc (frame) chứ không lưu ảnh đã thu nhỏ (display_frame)
            img_name = f"{output_dir}/frame_loi_{saved_count}.jpg"
            cv2.imwrite(img_name, frame)
            print(f"Đã lưu thành công: {img_name}")

    cap.release()
    cv2.destroyAllWindows()

if __name__ == '__main__':
    main()