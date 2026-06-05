import torch
import re
import easyocr
from ultralytics import YOLO
import streamlit as st

def correct_vietnamese_plate(text, vehicle_class):
    char_list = list(re.sub(r'[^A-Z0-9]', '', text))
    if len(char_list) < 7 or len(char_list) > 9: return text
    letter_map = {'0': 'D', '1': 'T', '2': 'Z', '3': 'E', '4': 'A', '5': 'S', '6': 'G', '7': 'T', '8': 'B', '9': 'P'}
    number_map = {'A': '4', 'G': '6', 'B': '8', 'O': '0', 'D': '0', 'S': '5', 'Z': '2', 'I': '1', 'T': '7', 'J': '3', 'L': '4', 'U': '0', 'E': '3', 'F': '7'}
    for i in range(min(2, len(char_list))):
        if char_list[i] in number_map: char_list[i] = number_map[char_list[i]]
    if len(char_list) > 2:
        if char_list[2] in letter_map: char_list[2] = letter_map[char_list[2]]
        elif char_list[2].isdigit(): char_list[2] = letter_map.get(char_list[2], 'X')
    start_idx = 3 if vehicle_class in ['car', 'truck', 'bus'] else len(char_list) - 4
    for i in range(start_idx, len(char_list)):
        if char_list[i] in number_map: char_list[i] = number_map[char_list[i]]
    if len(char_list) == 9 and char_list[4] in number_map: char_list[4] = number_map[char_list[4]]
    res = "".join(char_list)
    return res[:4] + "-" + res[4:] if len(res) == 9 or (len(res) == 8 and res[3].isalpha()) else res[:3] + "-" + res[3:]

@st.cache_resource
def load_models():
    device = 0 if torch.cuda.is_available() else 'cpu'
    model = YOLO("yolov8small/best_small.pt", task='detect')
    reader = easyocr.Reader(['en'], gpu=True if device == 0 else False)
    return model, reader, device