"""
animation_engine.py - 动态矢量动画渲染引擎
基于 PIL + 数学动效缓动函数 + FFmpeg rawvideo 管道编码
实现 0 图像 Token 消耗、极速 1080P/720P 矢量动画视频合成。
"""

import os
import sys
import math
import time
import subprocess
import shutil
from typing import Any, Dict, List, Optional, Tuple, Callable
from PIL import Image, ImageDraw, ImageFont

try:
    import imageio_ffmpeg
    DEFAULT_FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    DEFAULT_FFMPEG = shutil.which("ffmpeg")

# 字体路径（Windows 首选微软雅黑）
FONT_BOLD_CANDIDATES = [
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
]
FONT_REGULAR_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
]

def find_font_path(candidates: List[str]) -> Optional[str]:
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None

SYSTEM_FONT_BOLD = find_font_path(FONT_BOLD_CANDIDATES)
SYSTEM_FONT_REGULAR = find_font_path(FONT_REGULAR_CANDIDATES) or SYSTEM_FONT_BOLD

_FONT_CACHE: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}

def get_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    font_path = SYSTEM_FONT_BOLD if bold else SYSTEM_FONT_REGULAR
    if not font_path:
        return ImageFont.load_default()
    key = (font_path, size)
    if key not in _FONT_CACHE:
        try:
            _FONT_CACHE[key] = ImageFont.truetype(font_path, size)
        except Exception:
            try:
                _FONT_CACHE[key] = ImageFont.load_default()
            except Exception:
                pass
    return _FONT_CACHE.get(key, ImageFont.load_default())

# ============================================================
# 主题配色系统
# ============================================================
THEMES = {
    "cyber_dark": {
        "name": "科技赛博 (默认)",
        "bg_dark": (10, 14, 23),
        "grid_line": (16, 24, 40),
        "card_bg": (18, 25, 42),
        "card_border": (45, 65, 100),
        "primary": (0, 210, 255),       # 霓虹青
        "secondary": (165, 90, 255),    # 紫罗兰
        "accent": (0, 230, 130),       # 荧光绿
        "warning": (255, 185, 0),      # 明亮金
        "danger": (255, 75, 75),       # 警示红
        "orange": (255, 110, 40),      # 活力橙
        "text_main": (245, 248, 255),
        "text_muted": (145, 160, 185),
        "capsule_bg": (12, 18, 30),
        "capsule_border": (45, 65, 105),
    },
    "modern_blue": {
        "name": "优雅商务蓝",
        "bg_dark": (12, 20, 36),
        "grid_line": (18, 32, 56),
        "card_bg": (20, 34, 58),
        "card_border": (45, 80, 135),
        "primary": (56, 189, 248),
        "secondary": (96, 165, 250),
        "accent": (52, 211, 153),
        "warning": (251, 191, 36),
        "danger": (248, 113, 113),
        "orange": (251, 146, 60),
        "text_main": (240, 249, 255),
        "text_muted": (148, 163, 184),
        "capsule_bg": (15, 26, 46),
        "capsule_border": (50, 85, 140),
    },
    "luxury_gold": {
        "name": "黑金尊贵",
        "bg_dark": (16, 15, 18),
        "grid_line": (32, 28, 24),
        "card_bg": (28, 25, 26),
        "card_border": (85, 70, 45),
        "primary": (245, 195, 65),     # 钛金黄
        "secondary": (212, 160, 23),
        "accent": (74, 222, 128),
        "warning": (250, 204, 21),
        "danger": (244, 63, 94),
        "orange": (249, 115, 22),
        "text_main": (254, 252, 245),
        "text_muted": (180, 170, 160),
        "capsule_bg": (24, 20, 22),
        "capsule_border": (90, 75, 50),
    },
    "vibrant_purple": {
        "name": "极光霓虹紫",
        "bg_dark": (18, 12, 28),
        "grid_line": (32, 20, 50),
        "card_bg": (30, 22, 48),
        "card_border": (85, 55, 125),
        "primary": (192, 132, 252),
        "secondary": (244, 114, 182),
        "accent": (45, 212, 191),
        "warning": (250, 204, 21),
        "danger": (251, 113, 133),
        "orange": (251, 146, 60),
        "text_main": (250, 245, 255),
        "text_muted": (175, 160, 195),
        "capsule_bg": (24, 16, 38),
        "capsule_border": (95, 60, 140),
    },
    "terminal_green": {
        "name": "极客黑客绿",
        "bg_dark": (8, 14, 10),
        "grid_line": (16, 28, 20),
        "card_bg": (14, 26, 18),
        "card_border": (35, 75, 45),
        "primary": (34, 197, 94),      # 矩阵绿
        "secondary": (52, 211, 153),
        "accent": (0, 210, 255),
        "warning": (234, 179, 8),
        "danger": (239, 68, 68),
        "orange": (249, 115, 22),
        "text_main": (240, 253, 244),
        "text_muted": (134, 170, 145),
        "capsule_bg": (10, 20, 14),
        "capsule_border": (35, 80, 50),
    }
}

# ============================================================
# 动效缓动函数
# ============================================================
def ease_out_cubic(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 1.0 - math.pow(1.0 - x, 3)

def ease_in_out_quad(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return 2.0 * x * x if x < 0.5 else 1.0 - math.pow(-2.0 * x + 2.0, 2) / 2.0

def spring_overshoot(x: float, overshoot: float = 1.2) -> float:
    x = max(0.0, min(1.0, x))
    return 1.0 - math.cos(x * math.pi * 0.5 * overshoot) * (1.0 - x)

# ============================================================
# 基础几何绘制辅助
# ============================================================
def draw_rounded_rect(draw: ImageDraw.ImageDraw, bbox: Tuple[int, int, int, int], radius: int,
                      fill: Optional[Tuple[int, ...]] = None,
                      outline: Optional[Tuple[int, ...]] = None,
                      width: int = 1):
    x1, y1, x2, y2 = bbox
    if x2 <= x1 or y2 <= y1:
        return
    radius = min(radius, (x2 - x1) // 2, (y2 - y1) // 2)
    draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=fill, outline=outline, width=width)

def truncate_text(text: Any, max_chars: int) -> str:
    text = str(text if text is not None else "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars - 1] + "…"

def wrap_text_to_lines(text: Any, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    text = str(text if text is not None else "").strip()
    if not text:
        return []
    lines = []
    for paragraph in text.split("\n"):
        current_line = ""
        for ch in paragraph:
            test_line = current_line + ch
            try:
                w = font.getlength(str(test_line))
            except Exception:
                w = len(test_line) * 14
            if w <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = ch
        if current_line:
            lines.append(current_line)
    return lines

# ============================================================
# 8 大核心视觉组件绘制函数
# ============================================================

# 1. cards_grid: 亮点矩阵卡片 (2~4 张弹性弹入卡片)
def render_cards_grid(draw: ImageDraw.ImageDraw, scene: Dict[str, Any], progress: float, t: float,
                      theme: Dict[str, Any], W: int, H: int):
    content = scene.get("content", {})
    raw_cards = content.get("cards", [])
    if not raw_cards:
        raw_cards = [
            {"title": "🌟 核心亮点", "desc": "高品质矢量动画，毫秒级音画同步", "badge": "极速"},
            {"title": "⚡ 纯代码压制", "desc": "零生图 Token 消耗，即开即用", "badge": "免费"},
            {"title": "🎙️ MiMo 官方音色", "desc": "深度集成全套官方、自设计与克隆音色", "badge": "高质"},
            {"title": "📐 智能分镜编排", "desc": "大语言模型结构化提炼，8 种视觉版式", "badge": "智能"}
        ]
    
    cards = []
    for i, c in enumerate(raw_cards):
        if isinstance(c, dict):
            badge = str(c.get("badge") if c.get("badge") is not None else f"0{i+1}")
            title = str(c.get("title") or "")
            desc = str(c.get("desc") or c.get("description") or "")
            cards.append({"badge": badge, "title": title, "desc": desc})
        else:
            cards.append({"badge": f"0{i+1}", "title": str(c), "desc": ""})
            
    num_cards = min(4, len(cards))
    start_y = int(H * 0.26)
    card_h = int(H * 0.17)
    gap_y = 20
    
    card_colors = [theme["primary"], theme["accent"], theme["warning"], theme["secondary"]]
    
    if num_cards <= 2:
        for i in range(num_cards):
            c = cards[i]
            col = card_colors[i % len(card_colors)]
            c_prog = ease_out_cubic(max(0.0, min(1.0, (progress - 0.1 - i * 0.12) * 2.5)))
            if c_prog <= 0:
                continue
            slide_x = int((1.0 - c_prog) * 40)
            box = (int(W * 0.05) + slide_x, start_y + i * (card_h * 2 + gap_y), int(W * 0.95), start_y + i * (card_h * 2 + gap_y) + card_h * 2 - 20)
            draw_rounded_rect(draw, box, 18, fill=theme["card_bg"], outline=col, width=2)
            
            badge = str(c["badge"] or "HIGHLIGHT")
            draw_rounded_rect(draw, (box[0] + 30, box[1] + 30, box[0] + 160, box[1] + 70), 10, fill=(30, 45, 75), outline=col, width=1)
            draw.text((box[0] + 45, box[1] + 38), badge, fill=col, font=get_font(20, True))
            
            draw.text((box[0] + 180, box[1] + 32), c["title"], fill=theme["text_main"], font=get_font(34, True))
            desc_lines = wrap_text_to_lines(c["desc"], get_font(22, False), box[2] - box[0] - 60)
            for di, dl in enumerate(desc_lines[:3]):
                draw.text((box[0] + 35, box[1] + 95 + di * 36), dl, fill=theme["text_muted"], font=get_font(22, False))
    else:
        col_w = int((W * 0.9 - 30) / 2)
        for i in range(num_cards):
            c = cards[i]
            col = card_colors[i % len(card_colors)]
            r = i // 2
            col_idx = i % 2
            
            c_prog = ease_out_cubic(max(0.0, min(1.0, (progress - 0.1 - i * 0.1) * 2.5)))
            if c_prog <= 0:
                continue
            slide_y = int((1.0 - c_prog) * 30)
            
            bx1 = int(W * 0.05) + col_idx * (col_w + 30)
            by1 = start_y + r * (card_h + gap_y) + slide_y
            bx2 = bx1 + col_w
            by2 = by1 + card_h
            
            draw_rounded_rect(draw, (bx1, by1, bx2, by2), 16, fill=theme["card_bg"], outline=col, width=2)
            
            badge = str(c["badge"] or f"0{i+1}")
            bw = int(get_font(18, True).getlength(badge)) + 24
            draw_rounded_rect(draw, (bx1 + 25, by1 + 20, bx1 + 25 + bw, by1 + 52), 8, fill=(20, 35, 60), outline=col, width=1)
            draw.text((bx1 + 37, by1 + 24), badge, fill=col, font=get_font(18, True))
            
            draw.text((bx1 + 35 + bw + 15, by1 + 22), truncate_text(c["title"], 22), fill=theme["text_main"], font=get_font(26, True))
            desc_lines = wrap_text_to_lines(c["desc"], get_font(19, False), col_w - 50)
            for di, dl in enumerate(desc_lines[:2]):
                draw.text((bx1 + 25, by1 + 68 + di * 30), dl, fill=theme["text_muted"], font=get_font(19, False))

# 2. comparison: 左右红绿痛点/优势对比
def render_comparison(draw: ImageDraw.ImageDraw, scene: Dict[str, Any], progress: float, t: float,
                      theme: Dict[str, Any], W: int, H: int):
    content = scene.get("content", {})
    
    # 支持 content.left (dict) 与 content.left_title / content.left_items
    left_data = content.get("left")
    if isinstance(left_data, dict):
        left_title = str(left_data.get("label") or left_data.get("title") or "❌ 传统方案 (痛点多 / 门槛高)")
        raw_left_items = left_data.get("items", [])
    else:
        left_title = str(content.get("left_title") or "❌ 传统方案 (痛点多 / 门槛高)")
        raw_left_items = content.get("left_items", ["耗时漫长，生图排队等待", "调用昂贵大模型 API 费用飙升", "文字模糊易扭曲变形", "格式依赖复杂，极难二次编辑"])
        
    left_items = []
    for it in raw_left_items:
        if isinstance(it, dict):
            left_items.append(str(it.get("title") or it.get("text") or it.get("label") or it.get("desc") or ""))
        else:
            left_items.append(str(it))
            
    right_data = content.get("right")
    if isinstance(right_data, dict):
        right_title = str(right_data.get("label") or right_data.get("title") or "✅ 本项目创新方案 (极速 / 纯净)")
        raw_right_items = right_data.get("items", [])
    else:
        right_title = str(content.get("right_title") or "✅ 本项目创新方案 (极速 / 纯净)")
        raw_right_items = content.get("right_items", ["0 图像 Token 消耗，纯矢量代码渲染", "内置流式 FFmpeg，几十秒 1080P 成片", "字字锐利清晰，排版毫厘精准", "支持一键更换 MiMo 配音与分镜微调"])
        
    right_items = []
    for it in raw_right_items:
        if isinstance(it, dict):
            right_items.append(str(it.get("title") or it.get("text") or it.get("label") or it.get("desc") or ""))
        else:
            right_items.append(str(it))
            
    y_top = int(H * 0.25)
    box_h = int(H * 0.56)
    box_w = int((W * 0.9 - 30) / 2)
    
    l_prog = ease_out_cubic(max(0.0, min(1.0, (progress - 0.05) * 2.5)))
    if l_prog > 0:
        lx1 = int(W * 0.05)
        ly1 = y_top + int((1.0 - l_prog) * 30)
        lx2 = lx1 + box_w
        ly2 = ly1 + box_h
        draw_rounded_rect(draw, (lx1, ly1, lx2, ly2), 16, fill=(24, 18, 22), outline=theme["danger"], width=2)
        draw.text((lx1 + 35, ly1 + 35), left_title, fill=theme["danger"], font=get_font(28, True))
        
        for idx, item in enumerate(left_items[:5]):
            iy = ly1 + 105 + idx * 58
            draw.text((lx1 + 35, iy), truncate_text(item, 26), fill=(225, 175, 175), font=get_font(21, False))

    r_prog = ease_out_cubic(max(0.0, min(1.0, (progress - 0.2) * 2.5)))
    if r_prog > 0:
        rx1 = int(W * 0.05) + box_w + 30
        ry1 = y_top + int((1.0 - r_prog) * 30)
        rx2 = rx1 + box_w
        ry2 = ry1 + box_h
        draw_rounded_rect(draw, (rx1, ry1, rx2, ry2), 16, fill=(16, 32, 28), outline=theme["accent"], width=2)
        draw.text((rx1 + 35, ry1 + 35), right_title, fill=theme["accent"], font=get_font(28, True))
        
        for idx, item in enumerate(right_items[:5]):
            iy = ry1 + 105 + idx * 58
            draw.text((rx1 + 35, iy), truncate_text(item, 26), fill=(180, 245, 215), font=get_font(21, True))

# 3. data_chart: 动态行情/折线走势与量化仪表
def render_data_chart(draw: ImageDraw.ImageDraw, scene: Dict[str, Any], progress: float, t: float,
                      theme: Dict[str, Any], W: int, H: int):
    content = scene.get("content", {})
    chart_title = str(content.get("chart_title") or content.get("title") or content.get("metric") or "⚡ 动态趋势与数据分析监控流")
    
    indicators = []
    raw_ind = content.get("indicators")
    if raw_ind and isinstance(raw_ind, list):
        for it in raw_ind:
            if isinstance(it, dict):
                indicators.append(str(it.get("label") or it.get("title") or it.get("name") or ""))
            else:
                indicators.append(str(it))
    elif "callouts" in content and isinstance(content["callouts"], list):
        for c in content["callouts"]:
            if isinstance(c, dict):
                indicators.append(f"{c.get('label', '')}: {c.get('value', '')}")
            else:
                indicators.append(str(c))
    elif "series" in content and isinstance(content["series"], list):
        for s in content["series"]:
            if isinstance(s, dict):
                indicators.append(f"{s.get('label', '')}: {s.get('value', '')} {s.get('unit', '')}".strip())
            else:
                indicators.append(str(s))
    if not indicators:
        indicators = ["EMA20 动态跟踪", "波动率自适应通道", "AI 结构特征提取"]
    
    y_top = int(H * 0.25)
    box_w = int(W * 0.9)
    box_h = int(H * 0.56)
    x1 = int(W * 0.05)
    x2 = x1 + box_w
    y1 = y_top
    y2 = y1 + box_h
    
    draw_rounded_rect(draw, (x1, y1, x2, y2), 18, fill=theme["card_bg"], outline=theme["card_border"], width=2)
    draw.text((x1 + 40, y1 + 30), chart_title, fill=theme["text_main"], font=get_font(28, True))
    
    kx_start = x1 + 50
    ky_base = y1 + int(box_h * 0.62)
    num_bars = 24
    bar_pitch = int((box_w - 100) / num_bars)
    
    for idx in range(num_bars):
        bx = kx_start + idx * bar_pitch
        wave_val = math.sin(idx * 0.6 + progress * 7.0) * 35 + (idx * 3.2)
        k_height = int(max(20, 45 + wave_val))
        is_green = (idx % 3 != 0)
        c = theme["accent"] if is_green else theme["danger"]
        
        draw.line([(bx + 10, ky_base - k_height - 20), (bx + 10, ky_base + 30)], fill=c, width=2)
        draw_rounded_rect(draw, (bx, ky_base - k_height, bx + 20, ky_base), 3, fill=c)
        
    ema_points = []
    for idx in range(num_bars):
        bx = kx_start + idx * bar_pitch + 10
        by = ky_base - int(max(15, 30 + math.sin(idx * 0.6 + progress * 7.0) * 26 + (idx * 3.0))) - 15
        ema_points.append((bx, by))
    for idx in range(len(ema_points) - 1):
        draw.line([ema_points[idx], ema_points[idx+1]], fill=theme["warning"], width=3)
        
    ind_text = "  ●  ".join(str(x) for x in indicators)
    draw.text((x1 + 40, y1 + int(box_h * 0.72)), f"● {ind_text}", fill=theme["warning"], font=get_font(20, False))
    
    status_text = str(content.get("status_text") or "🤖 智能引擎实时运算中：形态置信度 96.8%  ➔  最优策略自动匹配")
    draw_rounded_rect(draw, (x1 + 35, y1 + int(box_h * 0.81), x2 - 35, y2 - 25), 12, fill=(20, 35, 58), outline=theme["primary"], width=1)
    draw.text((x1 + 60, y1 + int(box_h * 0.83)), status_text, fill=theme["primary"], font=get_font(22, True))

# 4. steps_flow: 操作步骤流 / 向导 (Step 1 ➔ Step 2 ➔ Step 3)
def render_steps_flow(draw: ImageDraw.ImageDraw, scene: Dict[str, Any], progress: float, t: float,
                      theme: Dict[str, Any], W: int, H: int):
    content = scene.get("content", {})
    raw_steps = content.get("steps", [])
    if not raw_steps:
        raw_steps = [
            {"step": "Step 1", "title": "文案输入", "desc": "输入长文或教程脚本，系统智能解析语义"},
            {"step": "Step 2", "title": "结构化规划", "desc": "LLM 拆分分镜，自动匹配 8 大高质感矢量模板"},
            {"step": "Step 3", "title": "MiMo 配音", "desc": "并发合成官方与克隆音色，精准计算毫秒时间轴"},
            {"step": "Step 4", "title": "成片导出", "desc": "FFmpeg rawvideo 管道流式压制，1080P 秒级出片"}
        ]
        
    steps = []
    for i, st in enumerate(raw_steps):
        if isinstance(st, dict):
            badge = str(st.get("step") if st.get("step") is not None else f"0{i+1}")
            if badge.isdigit():
                badge = f"Step {badge}"
            title = str(st.get("title") or "")
            desc = str(st.get("desc") or st.get("description") or "")
            steps.append({"badge": badge, "title": title, "desc": desc})
        else:
            steps.append({"badge": f"Step {i+1}", "title": str(st), "desc": ""})
    
    y_top = int(H * 0.26)
    box_w = int(W * 0.9)
    step_count = min(4, len(steps))
    step_w = int((box_w - (step_count - 1) * 24) / step_count)
    step_h = int(H * 0.52)
    
    colors = [theme["primary"], theme["warning"], theme["secondary"], theme["accent"]]
    
    for i in range(step_count):
        st = steps[i]
        col = colors[i % len(colors)]
        s_prog = ease_out_cubic(max(0.0, min(1.0, (progress - 0.08 - i * 0.12) * 2.8)))
        if s_prog <= 0:
            continue
            
        sx1 = int(W * 0.05) + i * (step_w + 24)
        sy1 = y_top + int((1.0 - s_prog) * 35)
        sx2 = sx1 + step_w
        sy2 = sy1 + step_h
        
        draw_rounded_rect(draw, (sx1, sy1, sx2, sy2), 16, fill=theme["card_bg"], outline=col, width=2)
        
        badge = st["badge"]
        draw_rounded_rect(draw, (sx1 + 25, sy1 + 25, sx1 + 135, sy1 + 65), 10, fill=(24, 38, 64), outline=col, width=1)
        draw.text((sx1 + 35, sy1 + 33), badge, fill=col, font=get_font(20, True))
        
        draw.text((sx1 + 25, sy1 + 85), truncate_text(st["title"], 16), fill=theme["text_main"], font=get_font(28, True))
        draw.line([(sx1 + 25, sy1 + 130), (sx2 - 25, sy1 + 130)], fill=theme["card_border"], width=1)
        
        desc_lines = wrap_text_to_lines(st["desc"], get_font(19, False), step_w - 50)
        for di, dl in enumerate(desc_lines[:6]):
            draw.text((sx1 + 25, sy1 + 150 + di * 32), dl, fill=theme["text_muted"], font=get_font(19, False))
            
        if i < step_count - 1 and s_prog > 0.8:
            arr_x = sx2 + 4
            arr_y = sy1 + step_h // 2
            draw.text((arr_x, arr_y - 18), "➔", fill=theme["primary"], font=get_font(24, True))

# 5. metrics_grid: 核心数据仪表盘 / KPI 统计大方块
def render_metrics_grid(draw: ImageDraw.ImageDraw, scene: Dict[str, Any], progress: float, t: float,
                        theme: Dict[str, Any], W: int, H: int):
    content = scene.get("content", {})
    raw_metrics = content.get("metrics", [])
    if not raw_metrics:
        raw_metrics = [
            {"label": "生图 Token 消耗", "value": "0", "unit": "Tokens", "trend": "100% 免费"},
            {"label": "1080P 渲染耗时", "value": "< 30s", "unit": "秒", "trend": "提升 15x"},
            {"label": "文字排版清晰度", "value": "100%", "unit": "矢量保真", "trend": "无畸变"},
            {"label": "MiMo 配音延迟", "value": "毫秒级", "unit": "实时对齐", "trend": "零爆音"}
        ]
        
    metrics = []
    for m in raw_metrics:
        if isinstance(m, dict):
            metrics.append({
                "label": str(m.get("label") or m.get("name") or m.get("title") or ""),
                "value": str(m.get("value") if m.get("value") is not None else ""),
                "unit": str(m.get("unit") or ""),
                "trend": str(m.get("trend") or m.get("accent") or "")
            })
        else:
            metrics.append({"label": str(m), "value": "", "unit": "", "trend": ""})
            
    y_top = int(H * 0.26)
    m_count = min(6, len(metrics))
    cols = 2 if m_count <= 4 else 3
    rows = (m_count + cols - 1) // cols
    
    grid_w = int(W * 0.9)
    gap = 24
    card_w = int((grid_w - (cols - 1) * gap) / cols)
    card_h = int((H * 0.54 - (rows - 1) * gap) / rows)
    
    colors = [theme["primary"], theme["accent"], theme["warning"], theme["orange"], theme["secondary"]]
    
    for i in range(m_count):
        m = metrics[i]
        col = colors[i % len(colors)]
        r = i // cols
        c = i % cols
        
        m_prog = ease_out_cubic(max(0.0, min(1.0, (progress - 0.08 - i * 0.09) * 2.8)))
        if m_prog <= 0:
            continue
            
        mx1 = int(W * 0.05) + c * (card_w + gap)
        my1 = y_top + r * (card_h + gap) + int((1.0 - m_prog) * 30)
        mx2 = mx1 + card_w
        my2 = my1 + card_h
        
        draw_rounded_rect(draw, (mx1, my1, mx2, my2), 16, fill=theme["card_bg"], outline=col, width=2)
        draw.text((mx1 + 25, my1 + 22), truncate_text(m["label"], 20), fill=theme["text_muted"], font=get_font(20, False))
        
        val_str = truncate_text(m["value"], 12)
        draw.text((mx1 + 25, my1 + 56), val_str, fill=col, font=get_font(44, True))
        
        unit_str = m["unit"]
        trend_str = m["trend"]
        if unit_str or trend_str:
            tag_display = f"{unit_str}  ·  {trend_str}".strip(" ·")
            draw.text((mx1 + 25, my2 - 40), tag_display, fill=theme["text_main"], font=get_font(18, True))

# 6. code_terminal: 极客终端代码窗口 / 打字机高亮
def render_code_terminal(draw: ImageDraw.ImageDraw, scene: Dict[str, Any], progress: float, t: float,
                         theme: Dict[str, Any], W: int, H: int):
    content = scene.get("content", {})
    term_title = str(content.get("title") or "bash - text2mp3 animation pipeline")
    raw_lines = content.get("lines", [])
    if not raw_lines:
        raw_lines = [
            "$ python animation_mvp.py --model mimo-v2.5-tts --theme cyber_dark",
            "[INFO] Initializing dynamic vector renderer (1920x1080 @ 30 FPS)...",
            "[INFO] Synthesizing MiMo TTS voiceover for 6 storyboard scenes...",
            "[SUCCESS] Zero image token cost! Video pipe stream connected to FFmpeg stdin.",
            "[RENDER] 1080P animation generated successfully: output/animation.mp4"
        ]
    code_lines = []
    for l in raw_lines:
        if isinstance(l, dict):
            code_lines.append(str(l.get("code") or l.get("text") or l.get("line") or ""))
        else:
            code_lines.append(str(l))
            
    y_top = int(H * 0.25)
    x1 = int(W * 0.05)
    x2 = int(W * 0.95)
    y1 = y_top
    y2 = y1 + int(H * 0.56)
    
    draw_rounded_rect(draw, (x1, y1, x2, y2), 16, fill=(14, 18, 26), outline=(45, 65, 95), width=2)
    
    bar_h = 42
    draw_rounded_rect(draw, (x1, y1, x2, y1 + bar_h), 16, fill=(22, 28, 40), outline=(45, 65, 95), width=1)
    
    draw.ellipse([(x1 + 18, y1 + 15), (x1 + 30, y1 + 27)], fill=(255, 95, 87))
    draw.ellipse([(x1 + 38, y1 + 15), (x1 + 50, y1 + 27)], fill=(254, 188, 46))
    draw.ellipse([(x1 + 58, y1 + 15), (x1 + 70, y1 + 27)], fill=(40, 200, 64))
    
    draw.text((x1 + 90, y1 + 11), term_title, fill=theme["text_muted"], font=get_font(18, True))
    
    total_lines = len(code_lines)
    for idx, line in enumerate(code_lines):
        line_reveal = max(0.0, min(1.0, (progress - 0.1 - idx * 0.12) * 3.5))
        if line_reveal <= 0:
            continue
            
        line_chars = int(len(line) * line_reveal)
        cur_text = line[:line_chars]
        
        if line_reveal < 1.0 or (idx == total_lines - 1 and math.sin(t * 8) > 0):
            cur_text += " ▋"
            
        ly = y1 + bar_h + 25 + idx * 46
        
        col = theme["text_main"]
        if cur_text.startswith("$"):
            col = theme["warning"]
        elif "[INFO]" in cur_text:
            col = theme["primary"]
        elif "[SUCCESS]" in cur_text:
            col = theme["accent"]
        elif "[WARN]" in cur_text:
            col = theme["orange"]
        elif "[RENDER]" in cur_text:
            col = theme["secondary"]
            
        draw.text((x1 + 35, ly), cur_text, fill=col, font=get_font(21, False))

# 7. quote_focus: 观点聚焦 / 震撼发光名言金句
def render_quote_focus(draw: ImageDraw.ImageDraw, scene: Dict[str, Any], progress: float, t: float,
                       theme: Dict[str, Any], W: int, H: int):
    content = scene.get("content", {})
    quote_text = str(content.get("quote") or content.get("text") or "真正高生产力的 AI 工具，应当将计算成本与创作门槛降至零。")
    author = str(content.get("author") or content.get("highlight") or "—— 开源项目愿景")
    
    raw_points = content.get("highlights") or content.get("supporting_labels") or ["零生图开销", "极速出片", "超清矢量", "自由编排"]
    key_points = []
    if isinstance(raw_points, list):
        for p in raw_points:
            if isinstance(p, dict):
                key_points.append(str(p.get("label") or p.get("title") or p.get("text") or ""))
            else:
                key_points.append(str(p))
                
    y_top = int(H * 0.25)
    x1 = int(W * 0.08)
    x2 = int(W * 0.92)
    y1 = y_top
    y2 = y1 + int(H * 0.55)
    
    q_prog = ease_out_cubic(max(0.0, min(1.0, (progress - 0.05) * 2.5)))
    slide_y = int((1.0 - q_prog) * 35)
    
    draw_rounded_rect(draw, (x1, y1 + slide_y, x2, y2 + slide_y), 20, fill=theme["card_bg"], outline=theme["warning"], width=2)
    draw.text((x1 + 45, y1 + slide_y + 25), "“", fill=theme["warning"], font=get_font(68, True))
    
    quote_lines = wrap_text_to_lines(quote_text, get_font(34, True), x2 - x1 - 120)
    for qi, ql in enumerate(quote_lines[:3]):
        draw.text((x1 + 100, y1 + slide_y + 55 + qi * 55), ql, fill=theme["text_main"], font=get_font(34, True))
        
    draw.text((x2 - 320, y1 + slide_y + 55 + len(quote_lines) * 55 + 15), author, fill=theme["warning"], font=get_font(22, True))
    
    if key_points:
        tag_y = y2 + slide_y - 65
        tag_x = x1 + 50
        for tag in key_points[:4]:
            tag_str = str(tag)
            tw = int(get_font(19, True).getlength(tag_str)) + 30
            draw_rounded_rect(draw, (tag_x, tag_y, tag_x + tw, tag_y + 42), 10, fill=(25, 38, 62), outline=theme["primary"], width=1)
            draw.text((tag_x + 15, tag_y + 9), tag_str, fill=theme["primary"], font=get_font(19, True))
            tag_x += tw + 20

# 8. call_to_action: 尾声行动号召与开源项目引导 (去除B站信息，仅留GitHub与项目核心)
def render_call_to_action(draw: ImageDraw.ImageDraw, scene: Dict[str, Any], progress: float, t: float,
                          theme: Dict[str, Any], W: int, H: int):
    content = scene.get("content", {})
    action_title = str(content.get("title") or content.get("headline") or "🚀 立即开启你的高质感动画创作之旅")
    action_desc = str(content.get("desc") or content.get("slogan") or content.get("description") or "开源免费 · 0 图像 Token · 纯代码压制 · MiMo 官方高保真配音")
    github_url = str(content.get("github_url") or "https://github.com/oficcejo/text2mp3")
    
    y_top = int(H * 0.25)
    x1 = int(W * 0.08)
    x2 = int(W * 0.92)
    y1 = y_top
    y2 = y1 + int(H * 0.55)
    
    a_prog = ease_out_cubic(max(0.0, min(1.0, (progress - 0.05) * 2.5)))
    slide_y = int((1.0 - a_prog) * 35)
    
    draw_rounded_rect(draw, (x1, y1 + slide_y, x2, y2 + slide_y), 20, fill=theme["card_bg"], outline=theme["primary"], width=2)
    draw.text((x1 + 60, y1 + slide_y + 45), action_title, fill=theme["warning"], font=get_font(36, True))
    draw.text((x1 + 60, y1 + slide_y + 110), action_desc, fill=theme["text_main"], font=get_font(23, False))
    
    gh_box = (x1 + 60, y1 + slide_y + 175, x2 - 60, y1 + slide_y + 285)
    draw_rounded_rect(draw, gh_box, 14, fill=(16, 26, 44), outline=theme["accent"], width=2)
    
    pulse = math.sin(t * 4.0) * 0.5 + 0.5
    dot_color = (int(0 + 40 * pulse), int(230 + 25 * pulse), int(130 + 40 * pulse))
    draw.ellipse([(gh_box[0] + 30, gh_box[1] + 38), (gh_box[0] + 48, gh_box[1] + 56)], fill=dot_color)
    
    draw.text((gh_box[0] + 65, gh_box[1] + 25), "⭐ GitHub 开源项目 · 欢迎 Star 支持", fill=theme["accent"], font=get_font(24, True))
    draw.text((gh_box[0] + 65, gh_box[1] + 65), github_url, fill=theme["primary"], font=get_font(22, True))
    
    footer_msg = str(content.get("footer") or content.get("footer_msg") or "💬 欢迎在评论区交流讨论与提需求，欢迎去 GitHub 点个 Star 支持！")
    draw.text((x1 + 60, y2 + slide_y - 50), footer_msg, fill=theme["text_muted"], font=get_font(20, False))

# 调度分镜绘制映射表
RENDER_MAP = {
    "cards_grid": render_cards_grid,
    "comparison": render_comparison,
    "data_chart": render_data_chart,
    "steps_flow": render_steps_flow,
    "metrics_grid": render_metrics_grid,
    "code_terminal": render_code_terminal,
    "quote_focus": render_quote_focus,
    "call_to_action": render_call_to_action
}

# ============================================================
# 全局单帧渲染函数
# ============================================================
def render_single_frame(t: float, scenes: List[Dict[str, Any]], total_duration: float,
                        theme: Dict[str, Any], W: int, H: int) -> Image.Image:
    cur_scene = scenes[0]
    cur_progress = 0.0
    for sc in scenes:
        if sc.get("start_time", 0.0) <= t < sc.get("end_time", total_duration):
            cur_scene = sc
            dur = max(0.1, sc.get("duration", sc.get("end_time", 1.0) - sc.get("start_time", 0.0)))
            cur_progress = (t - sc.get("start_time", 0.0)) / dur
            break
    else:
        cur_scene = scenes[-1]
        cur_progress = 1.0

    img = Image.new("RGB", (W, H), theme["bg_dark"])
    draw = ImageDraw.Draw(img)
    
    # 1. 科技背景网格线
    grid_spacing = 60
    for x in range(0, W, grid_spacing):
        draw.line([(x, 0), (x, H)], fill=theme["grid_line"], width=1)
    for y in range(0, H, grid_spacing):
        draw.line([(0, y), (W, y)], fill=theme["grid_line"], width=1)
        
    # 2. 顶部标签与分镜主/副标题
    stitle = str(cur_scene.get("title") or "")
    ssubtitle = str(cur_scene.get("subtitle") or "")
    stag = str(cur_scene.get("tag") or "🌟 动态场景")
    
    tw = int(get_font(18, True).getlength(stag)) + 30
    draw_rounded_rect(draw, (int(W * 0.05), int(H * 0.04), int(W * 0.05) + tw, int(H * 0.04) + 38), 10,
                      fill=theme["card_bg"], outline=theme["primary"], width=1)
    draw.text((int(W * 0.05) + 15, int(H * 0.04) + 7), stag, fill=theme["primary"], font=get_font(18, True))
    
    t_prog = ease_out_cubic(min(1.0, cur_progress * 3.5))
    y_off = int((1.0 - t_prog) * 20)
    
    draw.text((int(W * 0.05), int(H * 0.09) + y_off), truncate_text(stitle, 38), fill=theme["text_main"], font=get_font(42, True))
    if ssubtitle:
        draw.text((int(W * 0.05), int(H * 0.15) + y_off), truncate_text(ssubtitle, 60), fill=theme["text_muted"], font=get_font(21, False))

    # 3. 核心视觉组件渲染
    stype = cur_scene.get("type", "cards_grid")
    renderer = RENDER_MAP.get(stype, render_cards_grid)
    renderer(draw, cur_scene, cur_progress, t, theme, W, H)
    
    # 4. 顶部全局进度条
    global_prog = max(0.0, min(1.0, t / max(0.01, total_duration)))
    draw.line([(0, 2), (int(W * global_prog), 2)], fill=theme["primary"], width=4)
    
    # 5. 右上角常驻 GitHub 仓库呼吸光晕徽标 (纯净展示，不含 B 站信息)
    pulse_gh = (math.sin(t * 3.5) + 1.0) * 0.5
    gh_border = (
        int(theme["primary"][0] * pulse_gh + theme["card_border"][0] * (1 - pulse_gh)),
        int(theme["primary"][1] * pulse_gh + theme["card_border"][1] * (1 - pulse_gh)),
        int(theme["primary"][2] * pulse_gh + theme["card_border"][2] * (1 - pulse_gh)),
    )
    gh_w = 460
    gh_box = (W - gh_w - int(W * 0.05), int(H * 0.04), W - int(W * 0.05), int(H * 0.04) + 42)
    draw_rounded_rect(draw, gh_box, 12, fill=(16, 24, 38), outline=gh_border, width=2)
    
    draw.ellipse([(gh_box[0] + 16, gh_box[1] + 16), (gh_box[0] + 26, gh_box[1] + 26)], fill=theme["accent"])
    draw.text((gh_box[0] + 36, gh_box[1] + 9), "GitHub", fill=theme["warning"], font=get_font(18, True))
    draw.text((gh_box[0] + 110, gh_box[1] + 9), "https://github.com/oficcejo/text2mp3", fill=theme["primary"], font=get_font(17, True))

    # 6. 底部半透明字幕胶囊
    narration = str(cur_scene.get("narration_text") or cur_scene.get("text") or "")
    if narration:
        sub_h = int(H * 0.1)
        sub_box = (int(W * 0.05), H - sub_h - 25, int(W * 0.95), H - 25)
        draw_rounded_rect(draw, sub_box, 18, fill=theme["capsule_bg"], outline=theme["capsule_border"], width=1)
        
        draw.text((sub_box[0] + 25, sub_box[1] + 18), "🎙️", fill=theme["primary"], font=get_font(26, False))
        
        sub_font = get_font(22, True)
        lines = wrap_text_to_lines(narration, sub_font, sub_box[2] - sub_box[0] - 110)
        for li, line in enumerate(lines[:2]):
            ly = sub_box[1] + 18 + li * 30
            draw.text((sub_box[0] + 75, ly), line, fill=theme["text_main"], font=sub_font)
            
    return img

# ============================================================
# 流式管道压制：视频渲染入口
# ============================================================
def render_animation_video(storyboard: Dict[str, Any], audio_file: str, output_file: str,
                           theme_name: str = "cyber_dark",
                           width: int = 1920, height: int = 1080, fps: int = 30,
                           progress_callback: Optional[Callable[[float, int, int, float], None]] = None) -> None:
    scenes = storyboard.get("scenes", [])
    if not scenes:
        raise ValueError("Storyboard contains no scenes")
        
    total_duration = storyboard.get("total_duration", 0.0)
    if total_duration <= 0.0:
        total_duration = max(sc.get("end_time", 0.0) for sc in scenes)
    if total_duration <= 0.0:
        total_duration = len(scenes) * 5.0

    total_frames = max(1, int(total_duration * fps))
    theme = THEMES.get(theme_name, THEMES["cyber_dark"])
    
    os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
    
    ffmpeg_exe = DEFAULT_FFMPEG or "ffmpeg"
    
    ffmpeg_cmd = [
        ffmpeg_exe, "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{width}x{height}",
        "-pix_fmt", "rgb24",
        "-r", str(fps),
        "-i", "-",               # 从 stdin 管道输入逐帧视频数据
        "-i", audio_file,        # 音频输入
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "veryfast",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_file
    ]
    
    proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    
    start_time = time.time()
    try:
        for f_idx in range(total_frames):
            t = f_idx / fps
            frame_img = render_single_frame(t, scenes, total_duration, theme, width, height)
            raw_bytes = frame_img.tobytes()
            proc.stdin.write(raw_bytes)
            
            if progress_callback and (f_idx % 30 == 0 or f_idx == total_frames - 1):
                elapsed = time.time() - start_time
                fps_actual = (f_idx + 1) / max(0.001, elapsed)
                pct = (f_idx + 1) / total_frames
                progress_callback(pct, f_idx + 1, total_frames, fps_actual)
                
        proc.stdin.close()
        _, stderr = proc.communicate()
        
        if proc.returncode != 0:
            err_msg = stderr.decode('utf-8', errors='ignore')
            raise RuntimeError(f"FFmpeg encoding failed: {err_msg}")
            
    except Exception as e:
        if proc.poll() is None:
            proc.kill()
        raise e
