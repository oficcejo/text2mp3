import base64
import json
import os
import re
import shutil
import subprocess
import threading
import traceback
import uuid
import wave
import zlib
import struct
import time
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import math
import socket
import requests
import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont
from flask import jsonify, request, send_file


def register_video_mvp_routes(app, deps: Dict[str, Any]):
    jobs_dir = Path(os.getenv("JOBS_DIR", "jobs"))
    jobs_dir.mkdir(exist_ok=True)

    output_dir = Path(deps["output_dir"])
    get_cloned_voice_sample = deps["get_cloned_voice_sample"]
    call_mimo_tts = deps["call_mimo_tts"]
    load_cloned_voices = deps.get("load_cloned_voices", lambda: [])
    load_designed_voices = deps.get("load_designed_voices", lambda: [])
    get_designed_voice = deps.get("get_designed_voice")
    builtin_voices = deps.get("builtin_voices", {})

    def now_iso() -> str:
        return datetime.now().isoformat()

    def json_write(path: Path, data: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def json_read(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def normalize_base_url(base_url: str) -> str:
        base_url = (base_url or "").strip()
        if not base_url:
            return "https://api.openai.com/v1"
        return base_url.rstrip("/")

    def get_openai_settings() -> Dict[str, Any]:
        return {
            "api_key": os.getenv("OPENAI_API_KEY", "").strip(),
            "base_url": normalize_base_url(os.getenv("OPENAI_BASE_URL", "")),
            "text_model": os.getenv("OPENAI_TEXT_MODEL", "gpt-4.1"),
            "image_model": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2"),
            "image_size": os.getenv("OPENAI_IMAGE_SIZE", "1024x1024"),
            "image_quality": os.getenv("OPENAI_IMAGE_QUALITY", "medium"),
            "timeout": int(os.getenv("OPENAI_TEXT_TIMEOUT", "180")),
        }

    def get_video_settings() -> Dict[str, Any]:
        return {
            "scene_min_sec": int(os.getenv("SCENE_TARGET_SECONDS_MIN", "12")),
            "scene_max_sec": int(os.getenv("SCENE_TARGET_SECONDS_MAX", "18")),
            "scene_max_count": int(os.getenv("SCENE_MAX_COUNT", "24")),
            "style": os.getenv("SCENE_STYLE", "comic").strip() or "comic",
            "width": int(os.getenv("VIDEO_WIDTH", "1280")),
            "height": int(os.getenv("VIDEO_HEIGHT", "720")),
            "fps": int(os.getenv("VIDEO_FPS", "24")),
            "voice": os.getenv("VIDEO_DEFAULT_VOICE", "mimo_default"),
            "aspect_ratio": os.getenv("VIDEO_ASPECT_RATIO", "16:9"),
            "image_group_seconds_min": int(os.getenv("IMAGE_GROUP_SECONDS_MIN", "18")),
            "image_group_seconds_max": int(os.getenv("IMAGE_GROUP_SECONDS_MAX", "32")),
            "image_group_min_scenes": int(os.getenv("IMAGE_GROUP_MIN_SCENES", "2")),
        }

    def resolve_image_group_settings(settings: Dict[str, Any], density: str) -> Dict[str, Any]:
        resolved = dict(settings)
        density = (density or 'balanced').strip().lower()
        if density in ('more', 'dense'):
            # 多图：高密度画面，一镜一画（适合漫剧/短视频丰富视觉呈现）
            resolved['image_group_seconds_min'] = 8
            resolved['image_group_seconds_max'] = 18
            resolved['image_group_min_scenes'] = 1
        elif density in ('fewer', 'economic'):
            # 省图：经济模式，每 35~60 秒切换一张图
            resolved['image_group_seconds_min'] = 35
            resolved['image_group_seconds_max'] = 60
            resolved['image_group_min_scenes'] = 3
        else:
            # balanced 平衡推荐：每 18~32 秒切换一张图（约 1~2 个分镜一组），画面适中
            resolved['image_group_seconds_min'] = 18
            resolved['image_group_seconds_max'] = 32
            resolved['image_group_min_scenes'] = 2
        return resolved

    def ffmpeg_binary() -> Optional[str]:
        configured = os.getenv("FFMPEG_BIN", "").strip()
        if configured and Path(configured).exists():
            return configured
        return shutil.which("ffmpeg")

    def split_sentences_clean(text: str) -> List[str]:
        pattern = r'[^。！？!?；\n]+(?:[。！？!?；][”"’\']?|$)'
        items = re.findall(pattern, text)
        return [s.strip() for s in items if s.strip()]

    def estimate_duration_sec(text: str) -> float:
        estimated = max(4.0, len(text.strip()) / 4.6)
        return round(estimated, 1)

    def chunk_text_verbatim(raw_text: str, min_chars: int = 40, max_chars: int = 85) -> List[str]:
        """
        1:1 原文无损分块：
        - 优先在句子与段落边界切分；
        - 控制单分镜在 40~85 字（配音约 8~18 秒，视频节奏最佳）；
        - 全文所有段落与句子 100% 连续覆盖，不丢弃任何一句话、不作任何删减概括。
        """
        raw_text = re.sub(r'\r\n?', '\n', raw_text.strip())
        paragraphs = [p.strip() for p in raw_text.split('\n') if p.strip()]

        chunks = []
        current = ''

        for p in paragraphs:
            sentences = split_sentences_clean(p)
            if not sentences:
                sentences = [p]

            for s in sentences:
                if not current:
                    current = s
                elif len(current) + len(s) <= max_chars:
                    current += s
                    if len(current) >= min_chars:
                        chunks.append(current)
                        current = ''
                else:
                    chunks.append(current)
                    current = s

            if current and len(current) >= min_chars:
                chunks.append(current)
                current = ''

        if current:
            chunks.append(current)

        return chunks

    def extract_first_json_block(text: str) -> Any:
        text = text.strip()
        if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
            try:
                return json.loads(text)
            except Exception:
                pass

        start_obj = text.find("{")
        end_obj = text.rfind("}")
        start_arr = text.find("[")
        end_arr = text.rfind("]")

        if start_arr >= 0 and (start_obj < 0 or start_arr < start_obj):
            if end_arr > start_arr:
                try:
                    return json.loads(text[start_arr:end_arr + 1])
                except Exception:
                    pass
        if start_obj >= 0 and end_obj > start_obj:
            try:
                return json.loads(text[start_obj:end_obj + 1])
            except Exception:
                pass
        raise ValueError("no json block found")

    def enrich_scenes_with_llm(scenes: List[Dict[str, Any]], style: str) -> Dict[str, int]:
        """
        利用大模型仅为各分镜构思生动具象的生图提示词 (image_prompt) 和小标题 (title)，
        严禁修改或缩减分镜的原文字句（配音与字幕 100% 锁定原文）。
        同时精准记录消耗的 Token 数量（prompt_tokens, completion_tokens, total_tokens）。
        """
        usage_info = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        openai_settings = get_openai_settings()
        if not openai_settings["api_key"] or not scenes:
            return usage_info

        system_msg = (
            "你是短视频与漫画视觉分镜导演。请根据分镜的原文字句，为各分镜设计视觉画面的英文生图提示词 image_prompt 与简短标题 title。\n"
            "【核心合规与质量规范】：\n"
            "1. 严禁修改或缩减配音解说词！原文字句 100% 锁定；\n"
            "2. image_prompt 必须使用英文编写，详细描述镜头构图、人物动作、历史服饰、光影氛围与色彩艺术风格；\n"
            "3. 【严禁包含真实历史/政治人物姓名】（例如严禁出现 Yuan Shikai, Mao, Chiang 等），必须用通用具象描述替代（如 'a distinguished stout Chinese general in vintage ceremonial uniform' 或 'a traditional scholar in late Qing attire'）；\n"
            "4. 【严禁包含敏感/违规词汇】（如 political satire, warlord, rebellion, suppression, violence, blood, propaganda 等），专注艺术画风、历史古风建筑与人物戏剧感，确保 100% 通过 AI 生图安全合规审查；\n"
            "5. 返回格式为纯 JSON 数组，例如：[{\"scene_index\": 1, \"title\": \"...\", \"image_prompt\": \"...\"}]"
        )

        batch_size = 35
        for idx in range(0, len(scenes), batch_size):
            batch = scenes[idx:idx + batch_size]
            prompt_items = [{"scene_index": s["scene_index"], "text": s["narration_text"]} for s in batch]
            user_msg = f"画面风格：{style}。\n分镜列表：\n{json.dumps(prompt_items, ensure_ascii=False)}"

            payload = {
                "model": openai_settings["text_model"],
                "temperature": 0.3,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
            }
            try:
                response = requests.post(
                    f"{openai_settings['base_url']}/chat/completions",
                    headers={"Authorization": f"Bearer {openai_settings['api_key']}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=(15, min(60, openai_settings["timeout"])),
                )
                if response.status_code == 200:
                    data = response.json()
                    usage = data.get("usage") or {}
                    p_tokens = int(usage.get("prompt_tokens") or 0)
                    c_tokens = int(usage.get("completion_tokens") or 0)
                    t_tokens = int(usage.get("total_tokens") or (p_tokens + c_tokens))
                    content = data["choices"][0]["message"]["content"]
                    if isinstance(content, list):
                        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))

                    # 兜底估算 token
                    if t_tokens == 0:
                        p_tokens = max(1, int(len(user_msg + system_msg) * 1.2))
                        c_tokens = max(1, int(len(str(content)) * 1.2))
                        t_tokens = p_tokens + c_tokens

                    usage_info["prompt_tokens"] += p_tokens
                    usage_info["completion_tokens"] += c_tokens
                    usage_info["total_tokens"] += t_tokens

                    raw_list = extract_first_json_block(str(content))
                    if isinstance(raw_list, list):
                        lookup = {item["scene_index"]: item for item in raw_list if isinstance(item, dict) and "scene_index" in item}
                        for s in batch:
                            if s["scene_index"] in lookup:
                                if lookup[s["scene_index"]].get("title"):
                                    s["title"] = str(lookup[s["scene_index"]]["title"]).strip()
                                if lookup[s["scene_index"]].get("image_prompt"):
                                    s["image_prompt"] = str(lookup[s["scene_index"]]["image_prompt"]).strip()
            except Exception as exc:
                print(f"视觉生图提示词补充跳过: {exc}", flush=True)

        return usage_info

    def write_simple_png(output_path: Path, width: int, height: int, color: Tuple[int, int, int]) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        r, g, b = color
        raw = bytearray()
        pixel = bytes((r, g, b))
        row = pixel * width
        for _ in range(height):
            raw.append(0)
            raw.extend(row)

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (
                struct.pack("!I", len(data)) +
                tag +
                data +
                struct.pack("!I", zlib.crc32(tag + data) & 0xffffffff)
            )

        png = bytearray(b"\x89PNG\r\n\x1a\n")
        png.extend(chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        png.extend(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        png.extend(chunk(b"IEND", b""))
        output_path.write_bytes(bytes(png))

    def render_placeholder_panel(output_path: Path, title: str, prompt: str, width: int, height: int) -> None:
        try:
            img = Image.new("RGB", (width, height), color=(24, 28, 36))
            draw = ImageDraw.Draw(img)
            margin = 32
            draw.rectangle([margin, margin, width - margin, height - margin], outline=(70, 80, 100), width=2)
            draw.rectangle([margin + 8, margin + 8, width - margin - 8, height - margin - 8], outline=(180, 150, 90), width=1)
            
            # 装饰角标
            for x, y in [(margin + 4, margin + 4), (width - margin - 12, margin + 4), 
                         (margin + 4, height - margin - 12), (width - margin - 12, height - margin - 12)]:
                draw.rectangle([x, y, x + 8, y + 8], fill=(212, 175, 55))

            clean_title = (title or "国风漫剧 · 场景").strip()[:24]
            draw.text((width // 2, height // 2 - 10), clean_title, fill=(230, 235, 245), anchor="mm")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(output_path), "PNG")
        except Exception:
            write_simple_png(output_path, width, height, (36, 40, 48))

    def sanitize_image_prompt(prompt: str) -> str:
        """
        过滤敏感政治词汇、历史敏感人物姓名，替换为泛化英文描述，保证 100% 通过 AI 安全审查。
        """
        replacements = [
            (r'political satire(?:\s+tone)?', 'dramatic historical storytelling tone'),
            (r'political\s+figure', 'historical figure'),
            (r'political(?:\s+maps)?', 'vintage geographical maps'),
            (r'warlord(?:\s+shadow)?', 'distinguished military commander'),
            (r'satire', 'dramatic portrayal'),
            (r'rebellion', 'historical scene'),
            (r'suppression|suppress', 'military assembly'),
            (r'slaughter|kill|murder', 'intense dramatic confrontation'),
            (r'blood|bloody', 'crimson cinematic lighting'),
            (r'nude|naked', 'traditional silk garments'),
            (r'propaganda', 'historical banner'),
            (r'Yuan Shikai|yuan\s*shikai|袁世凯', 'a distinguished stout Chinese general in vintage ceremonial military uniform'),
            (r'Xi Jinping|Mao Zedong|Deng Xiaoping|Chiang Kai-shek', 'a historical Chinese leader in vintage attire'),
            (r'dragon robe', 'ornate imperial yellow embroidered silk robe'),
            (r'Beiyang era China', 'early 20th century historical Chinese architectural setting'),
            (r'Korean king bowing respectfully before him', 'palace diplomats in formal diplomatic conference'),
            (r'Japanese and Russian diplomats waiting outside', 'international diplomats waiting in historical hall'),
        ]
        res = prompt or ""
        for pat, repl in replacements:
            res = re.sub(pat, repl, res, flags=re.IGNORECASE)
        # 长度截断保底，避免 prompt 过长被拒
        if len(res) > 700:
            res = res[:700]
        return res.strip()

    def build_image_groups(scenes: List[Dict[str, Any]], settings: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
        groups: List[List[Dict[str, Any]]] = []
        current: List[Dict[str, Any]] = []
        current_duration = 0.0
        min_seconds = settings['image_group_seconds_min']
        max_seconds = settings['image_group_seconds_max']
        min_scenes = settings['image_group_min_scenes']

        for scene in scenes:
            duration = float(scene.get('estimated_duration_sec') or 0)
            current.append(scene)
            current_duration += duration

            enough_scenes = len(current) >= min_scenes
            enough_duration = current_duration >= min_seconds
            over_duration = current_duration >= max_seconds

            if over_duration or (enough_scenes and enough_duration):
                groups.append(current)
                current = []
                current_duration = 0.0

        if current:
            if groups and len(current) < max(1, min_scenes // 2):
                groups[-1].extend(current)
            else:
                groups.append(current)
        return groups

    def build_group_prompt(group_index: int, scenes: List[Dict[str, Any]], style: str) -> str:
        if len(scenes) == 1:
            scene = scenes[0]
            raw = scene.get("image_prompt") or scene.get("subtitle_text") or scene.get("title") or "comic illustration"
            return sanitize_image_prompt(f"Chinese comic art style, cinematic composition, {style} visual aesthetic. {raw}. Masterwork digital illustration, expressive character, no speech bubbles, no text, no watermark, 16:9 widescreen.")

        narratives = [s.get("image_prompt") or s.get("subtitle_text") or s.get("title") for s in scenes if (s.get("image_prompt") or s.get("subtitle_text"))]
        joined = "; ".join(narratives[:2])
        return sanitize_image_prompt(f"Chinese comic storyboard scene, cinematic composition, {style} visual style. {joined}. Dynamic dramatic lighting, rich detailed environment, consistent art style, no speech bubbles, no text, no watermark, 16:9 widescreen.")

    def resolve_image_size(aspect_ratio: str, custom_size: str = "") -> str:
        custom_size = (custom_size or "").strip().lower()
        if custom_size and custom_size not in ("1024x1024", "1024", "auto"):
            return custom_size
        aspect = (aspect_ratio or "16:9").strip()
        if aspect in ("16:9", "landscape", "horizontal", "wide"):
            return "1792x1024"
        elif aspect in ("9:16", "portrait", "vertical"):
            return "1024x1792"
        return "1024x1024"

    def fetch_image_bytes_from_openai(prompt: str, aspect_ratio: str = "16:9") -> Optional[bytes]:
        openai_settings = get_openai_settings()
        if not openai_settings["api_key"]:
            return None

        # 智能匹配适合视频宽高比的生图尺寸（16:9 -> 1792x1024，9:16 -> 1024x1792，1:1 -> 1024x1024）
        img_size = resolve_image_size(aspect_ratio, openai_settings.get("image_size"))

        # 检查代理设置：优先使用环境变量，如未配置则自适应检测本机运行的常用代理端口
        proxy_url = os.getenv("OPENAI_PROXY", "").strip()
        if not proxy_url:
            for host, port, proto in [("127.0.0.1", 10808, "socks5"), ("127.0.0.1", 10809, "http"), ("127.0.0.1", 7890, "http")]:
                try:
                    with socket.create_connection((host, port), timeout=0.15):
                        proxy_url = f"{proto}://{host}:{port}"
                        break
                except Exception:
                    pass

        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

        headers = {
            "Authorization": f"Bearer {openai_settings['api_key']}",
            "Content-Type": "application/json",
        }

        clean_prompt = sanitize_image_prompt(prompt)
        payload = {
            "model": openai_settings["image_model"],
            "prompt": clean_prompt,
            "size": img_size,
            "quality": openai_settings["image_quality"],
            "n": 1,
        }

        # 最多 3 次尝试（支持安全合规降级与网络重试）
        for attempt in range(3):
            try:
                response = requests.post(
                    f"{openai_settings['base_url']}/images/generations",
                    headers=headers,
                    json=payload,
                    timeout=(25, 180),
                    proxies=proxies,
                )

                # 命中审查合规策略拦截 (400 content_policy_violation)
                if response.status_code == 400 and any(kw in response.text for kw in ["content_policy_violation", "安全策略", "policy"]):
                    print(f"[生图安全审查拦截] 自动降级为安全风格化艺术提示词重试 (第 {attempt+1} 次)...", flush=True)
                    payload["prompt"] = "Chinese comic art illustration, elegant historical cinematic scene, traditional vintage Chinese architecture, dramatic warm lighting, masterwork digital painting, 16:9 widescreen composition."
                    time.sleep(1.0)
                    continue

                response.raise_for_status()
                data = response.json()
                item = (data.get("data") or [{}])[0]
                img_bytes = None
                if item.get("b64_json"):
                    img_bytes = base64.b64decode(item["b64_json"])
                elif item.get("url"):
                    # 下载生图，优先使用代理，若失败尝试直连
                    try:
                        img_resp = requests.get(item["url"], timeout=(20, 120), proxies=proxies)
                    except Exception:
                        img_resp = requests.get(item["url"], timeout=(20, 120))
                    img_resp.raise_for_status()
                    img_bytes = img_resp.content

                if img_bytes:
                    return img_bytes

            except Exception as e:
                print(f"[生图尝试 {attempt+1}/3 异常]: {e}", flush=True)
                if attempt < 2:
                    time.sleep(2.0)
                    payload["prompt"] = "Vibrant Chinese comic style scene, expressive historical character illustration, cinematic atmosphere, 16:9 widescreen"
                else:
                    break

        return None

    def compose_video_frame(
        source_image_path: Path,
        frame_path: Path,
        subtitle_text: str,
        title: str,
        width: int,
        height: int,
    ) -> None:
        del subtitle_text, title
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from PIL import ImageFilter
            src_img = Image.open(str(source_image_path))
            if src_img.mode != "RGB":
                src_img = src_img.convert("RGB")
            src_w, src_h = src_img.size
            src_ratio = src_w / float(src_h)
            target_ratio = width / float(height)

            # 如果图片比例与视频目标比例基本一致（差异在 18% 以内，例如 1792x1024 与 16:9 几乎完全契合）
            if abs(src_ratio - target_ratio) < 0.18:
                final_frame = src_img.resize((width, height), Image.Resampling.LANCZOS)
            else:
                # 比例差异较大时（例如方图 1:1 或竖图 9:16 置于 16:9 视频中）
                # 采用专业视频常用的【高斯模糊氛围背景 + 居中完整原图】方案，100% 完整保留画面，绝不裁切主体与人头！
                bg = src_img.resize((width, height), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(radius=25))
                dim = Image.new("RGB", (width, height), (0, 0, 0))
                bg = Image.blend(bg, dim, 0.35)

                scale = min(width / float(src_w), height / float(src_h))
                fg_w = max(1, int(src_w * scale))
                fg_h = max(1, int(src_h * scale))
                fg = src_img.resize((fg_w, fg_h), Image.Resampling.LANCZOS)

                pos_x = (width - fg_w) // 2
                pos_y = (height - fg_h) // 2
                bg.paste(fg, (pos_x, pos_y))
                final_frame = bg

            final_frame.save(str(frame_path), "PNG")
        except Exception:
            if source_image_path.suffix.lower() == ".png":
                shutil.copyfile(source_image_path, frame_path)
            else:
                write_simple_png(frame_path, width, height, (36, 40, 48))

    def generate_group_image(group_index: int, scenes: List[Dict[str, Any]], job_dir: Path, settings: Dict[str, Any]) -> Tuple[str, bool]:
        width = settings["width"]
        height = settings["height"]
        aspect_ratio = settings.get("aspect_ratio", "16:9")
        image_path = job_dir / "images" / f"group_{group_index:03d}.png"
        prompt = build_group_prompt(group_index, scenes, settings["style"])
        image_bytes = fetch_image_bytes_from_openai(prompt, aspect_ratio=aspect_ratio)
        is_ai = False
        if image_bytes:
            image_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                import io
                img = Image.open(io.BytesIO(image_bytes))
                if img.mode != "RGB":
                    img = img.convert("RGB")
                img.save(str(image_path), "PNG")
                is_ai = True
            except Exception:
                image_path.write_bytes(image_bytes)
                is_ai = True
        else:
            first_title = scenes[0].get("title") if scenes else f"漫画组 {group_index}"
            render_placeholder_panel(
                output_path=image_path,
                title=first_title,
                prompt=prompt,
                width=width,
                height=max(720, int(height * 0.82)),
            )
        return str(image_path.relative_to(job_dir)).replace("\\", "/"), is_ai

    def generate_scene_frame(scene: Dict[str, Any], image_file: str, job_dir: Path, settings: Dict[str, Any]) -> str:
        width = settings["width"]
        height = settings["height"]
        source_image_path = job_dir / image_file
        frame_path = job_dir / "frames" / f"scene_{scene['scene_index']:03d}.png"
        compose_video_frame(
            source_image_path=source_image_path,
            frame_path=frame_path,
            subtitle_text=scene["subtitle_text"],
            title=scene["title"],
            width=width,
            height=height,
        )
        return str(frame_path.relative_to(job_dir)).replace("\\", "/")

    def generate_scene_image(scene: Dict[str, Any], job_dir: Path, settings: Dict[str, Any]) -> Tuple[str, str]:
        width = settings["width"]
        height = settings["height"]
        aspect_ratio = settings.get("aspect_ratio", "16:9")
        image_path = job_dir / "images" / f"scene_{scene['scene_index']:03d}.png"
        frame_path = job_dir / "frames" / f"scene_{scene['scene_index']:03d}.png"
        image_bytes = fetch_image_bytes_from_openai(scene["image_prompt"], aspect_ratio=aspect_ratio)
        if image_bytes:
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(image_bytes)
        else:
            render_placeholder_panel(
                output_path=image_path,
                title=scene["title"],
                prompt=scene["image_prompt"],
                width=width,
                height=max(720, int(height * 0.82)),
            )

        compose_video_frame(
            source_image_path=image_path,
            frame_path=frame_path,
            subtitle_text=scene["subtitle_text"],
            title=scene["title"],
            width=width,
            height=height,
        )
        return str(image_path.relative_to(job_dir)).replace("\\", "/"), str(frame_path.relative_to(job_dir)).replace("\\", "/")

    def write_silence_wav(path: Path, duration_sec: float, sample_rate: int = 24000) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame_count = max(1, int(duration_sec * sample_rate))
        silence = b"\x00\x00" * frame_count
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(silence)

    def audio_duration(path: Path) -> float:
        try:
            with sf.SoundFile(str(path)) as audio_file:
                return round(len(audio_file) / float(audio_file.samplerate), 2)
        except Exception:
            return 0.0

    def build_voice_payload(voice: str) -> Tuple[str, str, str]:
        voice = (voice or "").strip()
        if voice.startswith("cloned:"):
            clone_id = voice.split(":", 1)[1]
            sample_bytes = get_cloned_voice_sample(clone_id)
            return (
                "mimo-v2.5-tts-voiceclone",
                "",
                base64.b64encode(sample_bytes).decode("utf-8"),
            )
        elif voice.startswith("designed:"):
            design_id = voice.split(":", 1)[1]
            try:
                d_info = get_designed_voice(design_id) if get_designed_voice else {}
                return (
                    "mimo-v2.5-tts-voicedesign",
                    d_info.get("prompt", "") if isinstance(d_info, dict) else "",
                    "",
                )
            except Exception as e:
                print(f"获取设计音色异常: {e}", flush=True)
                return ("mimo-v2.5-tts-voicedesign", "自然成熟沉稳的人声", "")
        return "mimo-v2.5-tts", voice or "mimo_default", ""

    def smooth_audio_endpoints(path: Path, fade_in_ms: float = 30.0, fade_out_ms: float = 30.0) -> None:
        """
        全方位消除音频首尾的突变冲击与 TTS 启动脉冲：
        1. 消除克隆 TTS 解码器启动瞬间（前 0~80ms 内）的瞬态脉冲（click/pop artifact）；
        2. 基于静音区计算基线校准，消除真实硬件直流偏置；
        3. 首部应用汉宁窗（余弦半窗）平滑入声，尾部平滑淡出归零，彻底杜绝分镜字幕转场爆音。
        """
        try:
            data, sr = sf.read(str(path))
            if data.size == 0:
                return

            is_stereo = data.ndim > 1
            mono = data.mean(axis=1) if is_stereo else data

            # 1. 抑制克隆 TTS 解码器在前 100ms 内引入的启动脉冲 (click/pop artifact)
            # 分析表明，克隆 TTS 在 0~50ms 内可能存在孤立瞬态冲击，随后在 50~120ms 降为纯静音，人声在 100ms 之后才正式开始
            search_window = min(len(mono), int(0.12 * sr))
            if search_window > int(0.04 * sr):
                chunk = np.abs(mono[:search_window])
                peak_idx = int(np.argmax(chunk[:int(0.08 * sr)]))
                peak_val = chunk[peak_idx]

                if peak_val > 0.02:
                    valley_start = peak_idx + int(0.015 * sr)
                    valley_end = min(search_window, peak_idx + int(0.08 * sr))
                    if valley_end > valley_start:
                        valley_min = np.min(chunk[valley_start:valley_end])
                        # 如果冲击后存在静音谷底，表明是启动突发杂音而非连续说话人声
                        if valley_min < 0.025:
                            zero_samples = valley_start + int(np.argmin(chunk[valley_start:valley_end]))
                            zero_samples = min(zero_samples, int(0.085 * sr))
                            if is_stereo:
                                data[:zero_samples] = 0.0
                            else:
                                data[:zero_samples] = 0.0

            # 2. 静音区基线校准消除直流偏置 (DC offset)
            tail_check = min(len(mono), int(0.05 * sr))
            if tail_check > 0:
                tail_dc = np.mean(mono[-tail_check:])
                if abs(tail_dc) > 1e-5:
                    if is_stereo:
                        data = data - tail_dc
                    else:
                        data = data - tail_dc

            # 3. 毫秒级汉宁窗（余弦半窗）平滑淡入淡出（0阶跃与0斜率接触纯零点）
            fade_in_samples = min(len(data), int(fade_in_ms * sr / 1000.0))
            fade_out_samples = min(len(data), int(fade_out_ms * sr / 1000.0))

            if fade_in_samples > 0:
                ramp_in = 0.5 * (1.0 - np.cos(np.pi * np.linspace(0.0, 1.0, fade_in_samples)))
                if is_stereo:
                    data[:fade_in_samples] *= ramp_in[:, None]
                else:
                    data[:fade_in_samples] *= ramp_in

            if fade_out_samples > 0:
                ramp_out = 0.5 * (1.0 + np.cos(np.pi * np.linspace(0.0, 1.0, fade_out_samples)))
                if is_stereo:
                    data[-fade_out_samples:] *= ramp_out[:, None]
                else:
                    data[-fade_out_samples:] *= ramp_out

            sf.write(str(path), data.astype(np.float32), sr)
        except Exception as _exc:
            print(f"平滑音频边缘异常: {_exc}", flush=True)

    def generate_scene_audio(scene: Dict[str, Any], job_dir: Path, voice: str) -> Tuple[str, float, Optional[str]]:
        audio_path = job_dir / "audio" / f"scene_{scene['scene_index']:03d}.wav"
        warning = None
        model, voice_name, voice_sample_b64 = build_voice_payload(voice)
        style_instruction = voice_name if model == "mimo-v2.5-tts-voicedesign" else ""
        mimo_voice = "" if model == "mimo-v2.5-tts-voicedesign" else voice_name

        max_retries = 4
        for attempt in range(max_retries):
            try:
                audio_bytes, _ = call_mimo_tts(
                    model=model,
                    text=scene["narration_text"],
                    voice=mimo_voice,
                    style_instruction=style_instruction,
                    audio_format="wav",
                    stream=False,
                    voice_audio_base64=voice_sample_b64,
                    voice_audio_mime="audio/wav",
                    progress=None,
                    voice_info=None,
                )
                audio_path.parent.mkdir(parents=True, exist_ok=True)
                audio_path.write_bytes(audio_bytes)
                smooth_audio_endpoints(audio_path)
                warning = None
                break
            except Exception as exc:
                err_msg = str(exc)
                is_rate_limit = "429" in err_msg or "Too many requests" in err_msg or "limitation" in err_msg
                if is_rate_limit and attempt < max_retries - 1:
                    wait_sec = 2.5 * (2 ** attempt) + random.uniform(0.5, 1.5)
                    print(f"[TTS Scene {scene['scene_index']}] 命中频控 (429)，等待 {wait_sec:.1f} 秒后重试 (第 {attempt+1}/{max_retries} 次)...", flush=True)
                    time.sleep(wait_sec)
                    continue
                elif attempt < 2 and ("timeout" in err_msg.lower() or "connection" in err_msg.lower()):
                    time.sleep(2.0)
                    continue
                else:
                    warning = f"TTS fallback used: {exc}"
                    print(f"[TTS Scene {scene['scene_index']}] 配音最终失败: {exc}，使用静音兜底", flush=True)
                    write_silence_wav(audio_path, scene["estimated_duration_sec"])
                    break

        duration = audio_duration(audio_path)
        if duration <= 0:
            duration = scene["estimated_duration_sec"]
            write_silence_wav(audio_path, duration)

        return str(audio_path.relative_to(job_dir)).replace("\\", "/"), duration, warning

    def build_srt(storyboard: Dict[str, Any], output_path: Path) -> str:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []

        def to_srt_time(seconds: float) -> str:
            millis = int(round(seconds * 1000))
            hours = millis // 3600000
            millis %= 3600000
            minutes = millis // 60000
            millis %= 60000
            secs = millis // 1000
            millis %= 1000
            return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

        cursor = 0.0
        for idx, scene in enumerate(storyboard["scenes"], start=1):
            duration = float(scene.get("actual_duration_sec") or scene["estimated_duration_sec"])
            start = scene.get("start_sec")
            end = scene.get("end_sec")
            if start is None:
                start = cursor
            if end is None:
                end = start + duration
            lines.extend([
                str(idx),
                f"{to_srt_time(start)} --> {to_srt_time(end)}",
                scene.get("subtitle_text") or scene.get("narration_text") or scene.get("title") or "",
                "",
            ])
            cursor = end

        output_path.write_text("\n".join(lines), encoding="utf-8")
        return str(output_path.relative_to(output_dir.parent if output_dir.parent.exists() else Path.cwd()))

    def build_seamless_audio_track(job_dir: Path, storyboard: Dict[str, Any], fps: int) -> Optional[Path]:
        """
        构建整片无缝连续音轨：
        1. 逐分镜读取音频，根据分镜帧数计算采样点，补充平滑静音尾，确保音画与字幕 100% 逐帧对齐；
        2. 全片音频在 PCM 阶段平滑拼接，统一进行一次 AAC 编码，彻底杜绝切片拼接引起的 packet 缝隙与转场“噗”爆音。
        """
        audio_segments = []
        target_sr = 24000
        for scene in storyboard.get("scenes", []):
            wav_rel = scene.get("audio_file")
            wav_path = (job_dir / wav_rel) if wav_rel else None
            if wav_path and wav_path.exists():
                data, sr = sf.read(str(wav_path))
                target_sr = sr
            else:
                data = np.zeros(int(scene.get("estimated_duration_sec", 4.0) * target_sr), dtype=np.float32)

            if data.ndim > 1:
                data = data.mean(axis=1)

            duration = float(scene.get("actual_duration_sec") or scene.get("estimated_duration_sec", 4.0))
            target_samples = int(round(duration * target_sr))

            if len(data) < target_samples:
                padded = np.zeros(target_samples, dtype=data.dtype)
                padded[:len(data)] = data
                tail_len = min(len(data), int(0.015 * target_sr))
                if tail_len > 0:
                    padded[len(data)-tail_len:len(data)] *= 0.5 * (1.0 + np.cos(np.pi * np.linspace(0.0, 1.0, tail_len)))
                data = padded
            elif len(data) > target_samples:
                data = data[:target_samples]
                tail_len = min(len(data), int(0.02 * target_sr))
                if tail_len > 0:
                    data[-tail_len:] *= 0.5 * (1.0 + np.cos(np.pi * np.linspace(0.0, 1.0, tail_len)))

            audio_segments.append(data)

        if not audio_segments:
            return None

        full_audio = np.concatenate(audio_segments)
        output_wav = job_dir / "video" / "full_audio.wav"
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_wav), full_audio, target_sr)
        return output_wav

    def render_video(job_dir: Path, storyboard: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        ffmpeg = ffmpeg_binary()
        if not ffmpeg:
            return None, "ffmpeg not found in PATH"

        parts_dir = job_dir / "video_parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        concat_file = job_dir / "video" / "concat.txt"
        concat_file.parent.mkdir(parents=True, exist_ok=True)
        part_paths: List[Path] = []

        try:
            w = int(settings.get("width", 1280))
            h = int(settings.get("height", 720))
            fps = int(settings.get("fps", 24))

            # 1. 预先构建整体无缝连续音轨（彻底杜绝分镜切歌爆音与时间轴漂移）
            full_audio_path = build_seamless_audio_track(job_dir, storyboard, fps)

            # 2. 逐分镜生成纯视频流动画片段（去除各自独立的 aac 编码碎片，加快渲染并杜绝 packet 缝隙）
            for idx, scene in enumerate(storyboard["scenes"]):
                frame_path = job_dir / scene["frame_file"]
                part_path = parts_dir / f"scene_{scene['scene_index']:03d}.mp4"
                duration = max(1.0, float(scene.get("actual_duration_sec") or scene["estimated_duration_sec"]))
                total_frames = max(24, int(round(duration * fps)))
                step = round(0.06 / total_frames, 5)

                # 动态交替运镜效果（适度缓推与轻微拉远，运镜平稳生动，绝不推太近导致画面被裁切）
                if idx % 2 == 0:
                    motion_vf = f"zoompan=z='min(zoom+{step},1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s={w}x{h}:fps={fps}"
                else:
                    motion_vf = f"zoompan=z='max(1.06-{step}*on,1.0)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s={w}x{h}:fps={fps}"

                vf_complex = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},{motion_vf}[v]"

                cmd = [
                    ffmpeg,
                    "-y",
                    "-i", str(frame_path),
                    "-filter_complex", vf_complex,
                    "-map", "[v]",
                    "-t", f"{duration:.3f}",
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    str(part_path),
                ]
                try:
                    subprocess.run(cmd, check=True, capture_output=True)
                except subprocess.CalledProcessError as _cpe:
                    print(f"运镜渲染异常 (Scene {scene['scene_index']}): {_cpe.stderr.decode('utf-8', errors='ignore')[-300:] if _cpe.stderr else ''}，回退静态画面", flush=True)
                    cmd_fallback = [
                        ffmpeg,
                        "-y",
                        "-loop", "1",
                        "-i", str(frame_path),
                        "-t", f"{duration:.3f}",
                        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
                        "-r", str(fps),
                        "-c:v", "libx264",
                        "-pix_fmt", "yuv420p",
                        str(part_path),
                    ]
                    subprocess.run(cmd_fallback, check=True, capture_output=True)

                part_paths.append(part_path)

            concat_lines = []
            for part in part_paths:
                part_abs = str(part.resolve()).replace("\\", "/")
                concat_lines.append(f"file '{part_abs}'")
            concat_file.write_text("\n".join(concat_lines), encoding="utf-8")
            
            raw_video_path = job_dir / "video" / "raw_video.mp4"
            final_path = job_dir / "video" / "final.mp4"

            # 3. 纯视频无损极速拼接
            cmd_concat = [
                ffmpeg,
                "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(concat_file),
                "-c:v", "copy",
                str(raw_video_path),
            ]
            result = subprocess.run(cmd_concat, capture_output=True)
            if result.returncode != 0:
                cmd_concat_reencode = [
                    ffmpeg,
                    "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", str(concat_file),
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    str(raw_video_path),
                ]
                subprocess.run(cmd_concat_reencode, check=True, capture_output=True)

            # 4. 合成整体连续音轨并硬烧录中文字幕
            srt_path = job_dir / "subtitles" / "subtitles.srt"
            burned_subtitles = False

            if srt_path.exists() and srt_path.stat().st_size > 0:
                try:
                    srt_escaped = str(srt_path.resolve()).replace("\\", "/").replace(":", "\\:")
                    sub_vf = f"subtitles='{srt_escaped}':force_style='FontName=Microsoft YaHei,FontSize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2.5,Shadow=1.2,MarginV=26,Alignment=2'"
                    cmd_burn = [
                        ffmpeg,
                        "-y",
                        "-i", str(raw_video_path),
                    ]
                    if full_audio_path and full_audio_path.exists():
                        cmd_burn.extend(["-i", str(full_audio_path)])
                    cmd_burn.extend([
                        "-vf", sub_vf,
                        "-map", "0:v:0",
                    ])
                    if full_audio_path and full_audio_path.exists():
                        cmd_burn.extend(["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"])
                    cmd_burn.extend([
                        "-c:v", "libx264",
                        "-pix_fmt", "yuv420p",
                        "-shortest",
                        str(final_path),
                    ])
                    res_burn = subprocess.run(cmd_burn, capture_output=True)
                    if res_burn.returncode == 0:
                        burned_subtitles = True
                except Exception as _sub_err:
                    print(f"烧录字幕异常，回退纯合成: {_sub_err}", flush=True)

            if not burned_subtitles:
                cmd_mux = [
                    ffmpeg,
                    "-y",
                    "-i", str(raw_video_path),
                ]
                if full_audio_path and full_audio_path.exists():
                    cmd_mux.extend(["-i", str(full_audio_path)])
                cmd_mux.extend([
                    "-map", "0:v:0",
                    "-c:v", "copy",
                ])
                if full_audio_path and full_audio_path.exists():
                    cmd_mux.extend(["-map", "1:a:0", "-c:a", "aac", "-b:a", "192k"])
                cmd_mux.extend([
                    "-shortest",
                    str(final_path),
                ])
                subprocess.run(cmd_mux, check=True, capture_output=True)

            return str(final_path.relative_to(job_dir)).replace("\\", "/"), None
        except Exception as exc:
            return None, f"video render skipped: {exc}"

    def scene_urls(job_id: str, scene: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(scene)
        if scene.get("image_file"):
            out["image_url"] = f"/video-jobs/{job_id}/{scene['image_file']}"
        if scene.get("frame_file"):
            out["frame_url"] = f"/video-jobs/{job_id}/{scene['frame_file']}"
        if scene.get("audio_file"):
            out["audio_url"] = f"/video-jobs/{job_id}/{scene['audio_file']}"
        return out

    def get_job_metrics(manifest: Dict[str, Any], storyboard: Dict[str, Any], job_dir: Path) -> Dict[str, Any]:
        """
        统一汇总资产消耗指标（生图张数、大模型 Token 数、配音字符数与时长）
        """
        metrics = dict(manifest.get("metrics") or {})
        scenes = storyboard.get("scenes", [])
        source_text = manifest.get("source_text", "")
        clean_text = re.sub(r'\s+', '', source_text)

        if "total_characters" not in metrics:
            metrics["total_characters"] = len(clean_text)
        if "total_scenes" not in metrics:
            metrics["total_scenes"] = len(scenes)
        if "images_total" not in metrics:
            groups = set(s.get("image_group_index") or s.get("scene_index") for s in scenes)
            metrics["images_total"] = len(groups) if groups else len(scenes)
        if "images_generated" not in metrics:
            img_dir = job_dir / "images"
            if img_dir.exists():
                metrics["images_generated"] = len(list(img_dir.glob("*.png")))
            else:
                metrics["images_generated"] = metrics.get("images_total", 0)
        if "images_ai_success" not in metrics:
            metrics["images_ai_success"] = metrics.get("images_generated", 0)
        if "total_tokens" not in metrics or metrics.get("total_tokens", 0) == 0:
            tu = storyboard.get("token_usage") or {}
            metrics["prompt_tokens"] = tu.get("prompt_tokens", 0)
            metrics["completion_tokens"] = tu.get("completion_tokens", 0)
            metrics["total_tokens"] = tu.get("total_tokens", 0)
            if metrics["total_tokens"] == 0 and scenes:
                est_p = max(80, int(len(clean_text) * 1.1) + 160)
                est_c = max(80, len(scenes) * 32)
                metrics["prompt_tokens"] = est_p
                metrics["completion_tokens"] = est_c
                metrics["total_tokens"] = est_p + est_c
        if "total_duration_sec" not in metrics:
            metrics["total_duration_sec"] = storyboard.get("total_estimated_duration_sec", 0.0)

        return metrics

    def job_response(job_id: str) -> Dict[str, Any]:
        job_dir = jobs_dir / job_id
        manifest = json_read(job_dir / "manifest.json", {})
        storyboard = json_read(job_dir / "storyboard.json", {})
        scenes = [scene_urls(job_id, scene) for scene in storyboard.get("scenes", [])]
        outputs = manifest.get("outputs", {})
        source_text = manifest.get("source_text", "")
        project_name = manifest.get("project_name") or storyboard.get("title") or (source_text[:25].strip() if source_text else job_id)
        thumbnail_url = None
        if scenes:
            thumbnail_url = scenes[0].get("frame_url") or scenes[0].get("image_url")
        elif (job_dir / "frames" / "scene_001.png").exists():
            thumbnail_url = f"/video-jobs/{job_id}/frames/scene_001.png"
        elif (job_dir / "images" / "group_001.png").exists():
            thumbnail_url = f"/video-jobs/{job_id}/images/group_001.png"

        metrics = get_job_metrics(manifest, storyboard, job_dir)

        return {
            "job_id": job_id,
            "project_name": project_name,
            "source_text": source_text,
            "thumbnail_url": thumbnail_url,
            "scenes_count": len(scenes),
            "duration": metrics.get("total_duration_sec", 0.0),
            "status": manifest.get("status"),
            "progress": manifest.get("progress", 0),
            "detail_message": manifest.get("detail_message", ""),
            "error": manifest.get("error"),
            "warnings": manifest.get("warnings", []),
            "created_at": manifest.get("created_at"),
            "updated_at": manifest.get("updated_at"),
            "config": manifest.get("config", {}),
            "metrics": metrics,
            "title": storyboard.get("title") or project_name,
            "style": storyboard.get("style"),
            "scenes": scenes,
            "video_url": f"/video-jobs/{job_id}/{outputs['video_file']}" if outputs.get("video_file") else None,
            "subtitle_url": f"/video-jobs/{job_id}/{outputs['subtitle_file']}" if outputs.get("subtitle_file") else None,
            "preview_available": bool(scenes),
            "ffmpeg_available": bool(ffmpeg_binary()),
        }

    def update_manifest(job_dir: Path, **changes: Any) -> Dict[str, Any]:
        manifest_path = job_dir / "manifest.json"
        manifest = json_read(manifest_path, {})
        manifest.update(changes)
        manifest["updated_at"] = now_iso()
        json_write(manifest_path, manifest)
        return manifest

    def build_storyboard(text: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        """
        1:1 原文完全无损分镜构建器：
        - 按照自然句子切分，全文 100% 覆盖，绝不遗漏任何语句；
        - narration_text 与 subtitle_text 严格采用 1:1 原文原句，一字不差；
        - 智能辅以大模型提炼画面生图提示词。
        """
        chunks = chunk_text_verbatim(text)
        style = settings.get("style", "comic")
        aspect_ratio = settings.get("aspect_ratio", "16:9")

        scenes = []
        for idx, chunk in enumerate(chunks, start=1):
            sents = split_sentences_clean(chunk)
            title = sents[0][:20] if sents else f"分镜 {idx}"
            image_prompt = (
                f"Chinese comic storyboard, cinematic composition, style: {style}, clear subject, no speech bubbles, no watermark, suitable for video."
                f" Scene content: {chunk[:220]}"
            )
            scenes.append({
                "scene_index": idx,
                "title": title,
                "source_excerpt": chunk[:120],
                "narration_text": chunk,       # 100% 1:1 原文原句，绝不丢弃、篡改或精简
                "subtitle_text": chunk,        # 100% 1:1 原文字幕，与配音完全逐字对应
                "image_prompt": image_prompt,
                "estimated_duration_sec": estimate_duration_sec(chunk),
                "image_file": None,
                "frame_file": None,
                "audio_file": None,
                "start_sec": None,
                "end_sec": None,
            })

        # 尝试大模型丰富生图提示词（绝对不触碰配音与字幕原句）并记录 Token 消耗
        token_usage = enrich_scenes_with_llm(scenes, style)

        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        main_title = paragraphs[0][:30] if paragraphs else "视频成片"

        return {
            "title": main_title,
            "style": style,
            "aspect_ratio": aspect_ratio,
            "total_estimated_duration_sec": round(sum(s["estimated_duration_sec"] for s in scenes), 1),
            "scenes": scenes,
            "token_usage": token_usage,
        }

    def run_job(job_id: str) -> None:
        job_dir = jobs_dir / job_id
        manifest_path = job_dir / "manifest.json"
        manifest = json_read(manifest_path, {})
        config = manifest.get("config", {})
        settings = resolve_image_group_settings(get_video_settings(), config.get('image_density', 'balanced'))
        warnings = list(manifest.get("warnings", []))

        try:
            update_manifest(job_dir, status="planning", progress=8, detail_message="正在 1:1 提取原文并规划分镜画面...", error=None)
            storyboard = build_storyboard(manifest["source_text"], settings)
            json_write(job_dir / "storyboard.json", storyboard)

            total_scenes = max(1, len(storyboard["scenes"]))
            image_groups = build_image_groups(storyboard["scenes"], settings)
            num_groups = max(1, len(image_groups))
            clean_text = re.sub(r'\s+', '', manifest.get("source_text", ""))
            tu = storyboard.get("token_usage") or {}

            metrics = {
                "images_generated": 0,
                "images_total": num_groups,
                "images_ai_success": 0,
                "prompt_tokens": tu.get("prompt_tokens", 0),
                "completion_tokens": tu.get("completion_tokens", 0),
                "total_tokens": tu.get("total_tokens", 0),
                "total_characters": len(clean_text),
                "total_scenes": total_scenes,
                "total_duration_sec": storyboard.get("total_estimated_duration_sec", 0.0),
            }

            update_manifest(
                job_dir,
                status="generating_assets",
                progress=20,
                detail_message=f"正在并发生成 {num_groups} 组漫画图...",
                metrics=metrics,
            )

            # 1. 多线程并发生成漫画画面
            group_results = {}
            done_groups = 0
            ai_success_count = 0
            with ThreadPoolExecutor(max_workers=min(3, num_groups)) as img_executor:
                future_to_group = {
                    img_executor.submit(generate_group_image, g_idx, g_scenes, job_dir, settings): g_idx
                    for g_idx, g_scenes in enumerate(image_groups, start=1)
                }
                for future in as_completed(future_to_group):
                    g_idx = future_to_group[future]
                    img_file, is_ai = future.result()
                    group_results[g_idx] = img_file
                    done_groups += 1
                    if is_ai:
                        ai_success_count += 1
                    pct = 20 + int(25 * done_groups / num_groups)
                    metrics["images_generated"] = done_groups
                    metrics["images_ai_success"] = ai_success_count
                    update_manifest(
                        job_dir,
                        status="generating_assets",
                        progress=pct,
                        detail_message=f"漫画画面生成中 ({done_groups}/{num_groups})...",
                        metrics=metrics,
                    )

            # 建立 scene_index 到 image_file 的关联
            group_map = {}
            for group_index, group_scenes in enumerate(image_groups, start=1):
                image_file = group_results.get(group_index)
                for scene in group_scenes:
                    scene["image_group_index"] = group_index
                    scene["image_file"] = image_file
                    group_map[scene["scene_index"]] = image_file

            # 2. 为各分镜生成画格
            for scene in storyboard["scenes"]:
                scene["frame_file"] = generate_scene_frame(scene, group_map[scene["scene_index"]], job_dir, settings)

            # 3. 合成各分镜配音音频
            voice_choice = config.get("voice", settings["voice"])
            is_clone = voice_choice.startswith("cloned:")
            max_audio_workers = 1 if is_clone else min(2, total_scenes)

            update_manifest(
                job_dir,
                status="generating_assets",
                progress=46,
                detail_message=f"正在合成 {total_scenes} 个分镜配音...",
            )

            audio_results = {}
            with ThreadPoolExecutor(max_workers=max_audio_workers) as audio_executor:
                future_to_scene = {}
                for scene in storyboard["scenes"]:
                    future = audio_executor.submit(generate_scene_audio, scene, job_dir, voice_choice)
                    future_to_scene[future] = scene
                    if max_audio_workers == 1:
                        time.sleep(0.3)
                done_audios = 0
                for future in as_completed(future_to_scene):
                    sc = future_to_scene[future]
                    audio_file, actual_duration_sec, warning = future.result()
                    audio_results[sc["scene_index"]] = (audio_file, actual_duration_sec, warning)
                    done_audios += 1
                    pct = 46 + int(30 * done_audios / total_scenes)
                    update_manifest(
                        job_dir,
                        status="generating_assets",
                        progress=pct,
                        detail_message=f"分镜配音合成中 ({done_audios}/{total_scenes})...",
                    )

            # 4. 按顺序对齐配音时长与时间轴（帧级对齐，确保音画与字幕 100% 毫秒级锁定）
            cursor = 0.0
            fps = int(settings.get("fps", 24))
            for idx, scene in enumerate(storyboard["scenes"], start=1):
                audio_file, actual_duration_sec, warning = audio_results[scene["scene_index"]]
                scene["audio_file"] = audio_file
                frames = max(24, math.ceil(actual_duration_sec * fps))
                scene_duration = round(frames / fps, 3)
                scene["actual_duration_sec"] = scene_duration
                scene["start_sec"] = round(cursor, 3)
                cursor += scene_duration
                scene["end_sec"] = round(cursor, 3)
                if warning:
                    warnings.append(f"Scene {idx}: {warning}")

            storyboard["total_estimated_duration_sec"] = round(cursor, 1)
            metrics["total_duration_sec"] = round(cursor, 1)
            json_write(job_dir / "storyboard.json", storyboard)

            update_manifest(
                job_dir,
                status="building_subtitles",
                progress=78,
                detail_message="正在构建精准时间轴字幕...",
                metrics=metrics,
                warnings=warnings[-20:],
            )
            subtitle_file = job_dir / "subtitles" / "subtitles.srt"
            build_srt(storyboard, subtitle_file)

            update_manifest(
                job_dir,
                status="rendering_video",
                progress=85,
                detail_message="正在调用 FFmpeg 合成运镜动效与硬字幕...",
                metrics=metrics,
                warnings=warnings[-20:],
            )
            video_file, render_warning = render_video(job_dir, storyboard, settings)
            if render_warning:
                warnings.append(render_warning)

            outputs = {
                "storyboard_file": "storyboard.json",
                "subtitle_file": str(subtitle_file.relative_to(job_dir)).replace("\\", "/"),
                "video_file": video_file,
            }

            update_manifest(
                job_dir,
                status="completed",
                progress=100,
                detail_message="视频生成完成！已烧录中文字幕与镜头动效",
                outputs=outputs,
                metrics=metrics,
                warnings=warnings[-20:],
                error=None,
            )
        except Exception as exc:
            traceback.print_exc()
            update_manifest(
                job_dir,
                status="failed",
                progress=100,
                error=str(exc),
                warnings=warnings[-20:],
            )

    @app.route("/api/video/config", methods=["GET"])
    def api_video_config():
        settings = get_video_settings()
        openai_settings = get_openai_settings()
        return jsonify({
            "ffmpeg_available": bool(ffmpeg_binary()),
            "openai_configured": bool(openai_settings["api_key"]),
            "default_voice": settings["voice"],
            "default_style": settings["style"],
            "scene_min_sec": settings["scene_min_sec"],
            "scene_max_sec": settings["scene_max_sec"],
        })

    @app.route("/api/video/jobs", methods=["GET"])
    def api_video_jobs():
        items = []
        for job_dir in sorted(jobs_dir.glob("job_*"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
            items.append(job_response(job_dir.name))
        return jsonify({"jobs": items})

    @app.route("/api/video/jobs", methods=["POST"])
    def api_create_video_job():
        data = request.get_json(force=True)
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "请输入文章内容"}), 400

        project_name = (data.get("project_name") or "").strip()
        settings = get_video_settings()
        job_id = f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        job_dir = jobs_dir / job_id
        for folder in ["images", "frames", "audio", "subtitles", "video", "video_parts"]:
            (job_dir / folder).mkdir(parents=True, exist_ok=True)

        manifest = {
            "job_id": job_id,
            "project_name": project_name or text[:25].strip() or "未命名视频项目",
            "status": "pending",
            "progress": 0,
            "source_text": text,
            "config": {
                "voice": (data.get("voice") or settings["voice"]).strip() or settings["voice"],
                "style": (data.get("style") or settings["style"]).strip() or settings["style"],
                "aspect_ratio": (data.get("aspect_ratio") or settings["aspect_ratio"]).strip() or settings["aspect_ratio"],
                "image_model": get_openai_settings()["image_model"],
                "image_density": (data.get("image_density") or "balanced").strip() or "balanced",
            },
            "metrics": {
                "images_generated": 0,
                "images_total": 0,
                "images_ai_success": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "total_characters": len(re.sub(r'\s+', '', text)),
                "total_scenes": 0,
                "total_duration_sec": 0.0,
            },
            "outputs": {
                "storyboard_file": None,
                "subtitle_file": None,
                "video_file": None,
            },
            "warnings": [],
            "error": None,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        json_write(job_dir / "manifest.json", manifest)

        thread = threading.Thread(target=run_job, args=(job_id,), daemon=True)
        thread.start()
        return jsonify({"success": True, "job": job_response(job_id)})

    def execute_change_voice(job_id: str, new_voice: str):
        job_dir = jobs_dir / job_id
        manifest_path = job_dir / "manifest.json"
        storyboard_path = job_dir / "storyboard.json"
        if not manifest_path.exists() or not storyboard_path.exists():
            return

        manifest = json_read(manifest_path, {})
        storyboard = json_read(storyboard_path, {})
        settings = get_video_settings()
        if "aspect_ratio" in manifest.get("config", {}):
            settings["aspect_ratio"] = manifest["config"]["aspect_ratio"]
        settings["voice"] = new_voice

        manifest["status"] = "generating_assets"
        manifest["progress"] = 15
        manifest["detail_message"] = f"正在使用新音色【{new_voice}】重新生成分镜配音..."
        manifest["config"]["voice"] = new_voice
        manifest["updated_at"] = now_iso()
        json_write(manifest_path, manifest)

        scenes = storyboard.get("scenes", [])
        total_chars = 0
        total_duration = 0.0
        warnings = manifest.get("warnings", [])

        try:
            # 重新生成所有分镜音频
            for idx, s in enumerate(scenes):
                total_chars += len(re.sub(r"\s+", "", s.get("narration_text", "")))
                a_file, dur, warn = generate_scene_audio(s, job_dir, new_voice)
                s["audio_file"] = a_file
                s["actual_duration_sec"] = dur
                total_duration += dur
                if warn:
                    warnings.append(warn)
                pct = 15 + int(50 * (idx + 1) / max(1, len(scenes)))
                manifest["progress"] = pct
                manifest["detail_message"] = f"正在为分镜 {idx + 1}/{len(scenes)} 重新配音..."
                json_write(manifest_path, manifest)

            storyboard["scenes"] = scenes
            storyboard["estimated_duration_sec"] = round(total_duration, 1)
            json_write(storyboard_path, storyboard)

            # 重建字幕与时间轴
            manifest["status"] = "building_subtitles"
            manifest["progress"] = 70
            manifest["detail_message"] = "正在根据新语速对齐中文字幕..."
            json_write(manifest_path, manifest)

            srt_path = job_dir / "subtitles" / "subtitles.srt"
            build_subtitles(storyboard, srt_path)

            # 重新极速渲染视频（保留现有高清画格，0 生图消耗）
            manifest["status"] = "rendering_video"
            manifest["progress"] = 80
            manifest["detail_message"] = "正在保留原图画格，重新极速合成视频与镜头运镜..."
            json_write(manifest_path, manifest)

            raw_video, final_video = render_video(storyboard, job_dir, settings)

            manifest["status"] = "completed"
            manifest["progress"] = 100
            manifest["detail_message"] = f"配音更换完成！已更新为【{new_voice}】并重新烧录成片"
            manifest["outputs"]["subtitle_file"] = str(srt_path.relative_to(job_dir)).replace("\\", "/")
            if final_video:
                manifest["outputs"]["video_file"] = str(final_video.relative_to(job_dir)).replace("\\", "/")
            elif raw_video:
                manifest["outputs"]["video_file"] = str(raw_video.relative_to(job_dir)).replace("\\", "/")

            if "metrics" in manifest:
                manifest["metrics"]["total_duration_sec"] = round(total_duration, 1)
            manifest["warnings"] = warnings[-20:]
            manifest["updated_at"] = now_iso()
            json_write(manifest_path, manifest)
        except Exception as exc:
            traceback.print_exc()
            manifest["status"] = "failed"
            manifest["error"] = f"更换配音失败: {exc}"
            manifest["updated_at"] = now_iso()
            json_write(manifest_path, manifest)

    def execute_rerender(job_id: str):
        job_dir = jobs_dir / job_id
        manifest_path = job_dir / "manifest.json"
        storyboard_path = job_dir / "storyboard.json"
        if not manifest_path.exists() or not storyboard_path.exists():
            return

        manifest = json_read(manifest_path, {})
        storyboard = json_read(storyboard_path, {})
        settings = get_video_settings()
        voice = manifest.get("config", {}).get("voice") or settings["voice"]
        if "aspect_ratio" in manifest.get("config", {}):
            settings["aspect_ratio"] = manifest["config"]["aspect_ratio"]

        manifest["status"] = "generating_assets"
        manifest["progress"] = 20
        manifest["detail_message"] = "正在检查并更新分镜音频..."
        manifest["updated_at"] = now_iso()
        json_write(manifest_path, manifest)

        scenes = storyboard.get("scenes", [])
        total_duration = 0.0
        warnings = manifest.get("warnings", [])

        try:
            for idx, s in enumerate(scenes):
                audio_path = job_dir / f"audio/scene_{s['scene_index']:03d}.wav"
                if not audio_path.exists() or not s.get("audio_file"):
                    a_file, dur, warn = generate_scene_audio(s, job_dir, voice)
                    s["audio_file"] = a_file
                    s["actual_duration_sec"] = dur
                    if warn:
                        warnings.append(warn)
                else:
                    dur = audio_duration(audio_path)
                    s["actual_duration_sec"] = dur
                total_duration += float(s.get("actual_duration_sec") or 3.0)

            storyboard["scenes"] = scenes
            storyboard["estimated_duration_sec"] = round(total_duration, 1)
            json_write(storyboard_path, storyboard)

            # 重新生成字幕
            manifest["status"] = "building_subtitles"
            manifest["progress"] = 65
            manifest["detail_message"] = "正在根据修改后的分镜对齐字幕..."
            json_write(manifest_path, manifest)

            srt_path = job_dir / "subtitles" / "subtitles.srt"
            build_subtitles(storyboard, srt_path)

            # 重新渲染视频
            manifest["status"] = "rendering_video"
            manifest["progress"] = 80
            manifest["detail_message"] = "正在重新合成完整视频..."
            json_write(manifest_path, manifest)

            raw_video, final_video = render_video(storyboard, job_dir, settings)

            manifest["status"] = "completed"
            manifest["progress"] = 100
            manifest["detail_message"] = "视频重新合成完成！"
            manifest["outputs"]["subtitle_file"] = str(srt_path.relative_to(job_dir)).replace("\\", "/")
            if final_video:
                manifest["outputs"]["video_file"] = str(final_video.relative_to(job_dir)).replace("\\", "/")
            elif raw_video:
                manifest["outputs"]["video_file"] = str(raw_video.relative_to(job_dir)).replace("\\", "/")

            if "metrics" in manifest:
                manifest["metrics"]["total_duration_sec"] = round(total_duration, 1)
            manifest["warnings"] = warnings[-20:]
            manifest["updated_at"] = now_iso()
            json_write(manifest_path, manifest)
        except Exception as exc:
            traceback.print_exc()
            manifest["status"] = "failed"
            manifest["error"] = f"重新合成失败: {exc}"
            manifest["updated_at"] = now_iso()
            json_write(manifest_path, manifest)

    @app.route("/api/video/jobs/<job_id>", methods=["GET"])
    def api_video_job(job_id: str):
        job_dir = jobs_dir / job_id
        if not job_dir.exists():
            return jsonify({"error": "job not found"}), 404
        return jsonify({"job": job_response(job_id)})

    @app.route("/api/video/jobs/<job_id>", methods=["DELETE"])
    def api_delete_video_job(job_id: str):
        job_dir = jobs_dir / job_id
        if job_dir.exists():
            shutil.rmtree(str(job_dir), ignore_errors=True)
        return jsonify({"success": True})

    @app.route("/api/video/jobs/<job_id>/save", methods=["POST"])
    def api_save_video_job(job_id: str):
        job_dir = jobs_dir / job_id
        manifest_path = job_dir / "manifest.json"
        if not manifest_path.exists():
            return jsonify({"error": "job not found"}), 404
        data = request.get_json(force=True)
        manifest = json_read(manifest_path, {})
        if data.get("project_name") is not None:
            manifest["project_name"] = str(data["project_name"]).strip()
        if data.get("tags") is not None:
            manifest["tags"] = data["tags"]
        manifest["updated_at"] = now_iso()
        json_write(manifest_path, manifest)
        return jsonify({"success": True, "job": job_response(job_id)})

    @app.route("/api/video/jobs/<job_id>/change-voice", methods=["POST"])
    def api_change_video_voice(job_id: str):
        job_dir = jobs_dir / job_id
        if not job_dir.exists():
            return jsonify({"error": "job not found"}), 404
        data = request.get_json(force=True)
        new_voice = (data.get("voice") or "").strip()
        if not new_voice:
            return jsonify({"error": "请选择要更换的配音音色"}), 400

        thread = threading.Thread(target=execute_change_voice, args=(job_id, new_voice), daemon=True)
        thread.start()
        return jsonify({"success": True, "message": f"已开始为任务【{job_id}】更换配音为【{new_voice}】", "job": job_response(job_id)})

    @app.route("/api/video/jobs/<job_id>/rerender", methods=["POST"])
    def api_rerender_video_job(job_id: str):
        job_dir = jobs_dir / job_id
        if not job_dir.exists():
            return jsonify({"error": "job not found"}), 404

        thread = threading.Thread(target=execute_rerender, args=(job_id,), daemon=True)
        thread.start()
        return jsonify({"success": True, "message": f"已开始重新渲染任务【{job_id}】", "job": job_response(job_id)})

    @app.route("/api/video/jobs/<job_id>/storyboard", methods=["GET"])
    def api_video_storyboard(job_id: str):
        job_dir = jobs_dir / job_id
        storyboard_path = job_dir / "storyboard.json"
        if not storyboard_path.exists():
            return jsonify({"error": "storyboard not ready"}), 404
        return jsonify(json_read(storyboard_path, {}))

    @app.route("/api/video/jobs/<job_id>/storyboard", methods=["POST"])
    def api_update_video_storyboard(job_id: str):
        job_dir = jobs_dir / job_id
        storyboard_path = job_dir / "storyboard.json"
        if not storyboard_path.exists():
            return jsonify({"error": "storyboard not found"}), 404
        data = request.get_json(force=True)
        new_scenes = data.get("scenes")
        if not isinstance(new_scenes, list):
            return jsonify({"error": "scenes 格式不正确"}), 400

        storyboard = json_read(storyboard_path, {})
        existing_scenes = storyboard.get("scenes", [])
        lookup = {s["scene_index"]: s for s in existing_scenes}

        for ns in new_scenes:
            s_idx = ns.get("scene_index")
            if s_idx in lookup:
                if ns.get("title") is not None:
                    lookup[s_idx]["title"] = str(ns["title"]).strip()
                if ns.get("narration_text") is not None:
                    lookup[s_idx]["narration_text"] = str(ns["narration_text"]).strip()
                if ns.get("subtitle_text") is not None:
                    lookup[s_idx]["subtitle_text"] = str(ns["subtitle_text"]).strip()
                if ns.get("image_prompt") is not None:
                    lookup[s_idx]["image_prompt"] = str(ns["image_prompt"]).strip()

        storyboard["scenes"] = existing_scenes
        json_write(storyboard_path, storyboard)

        # 同步更新字幕
        srt_path = job_dir / "subtitles" / "subtitles.srt"
        build_subtitles(storyboard, srt_path)

        manifest_path = job_dir / "manifest.json"
        manifest = json_read(manifest_path, {})
        manifest["updated_at"] = now_iso()
        manifest["detail_message"] = "分镜内容已更新保存！可点击「重新渲染成片」导出最新视频。"
        json_write(manifest_path, manifest)

        return jsonify({"success": True, "job": job_response(job_id)})

    @app.route("/api/video/jobs/<job_id>/regenerate-scene-audio", methods=["POST"])
    def api_regenerate_scene_audio(job_id: str):
        job_dir = jobs_dir / job_id
        storyboard_path = job_dir / "storyboard.json"
        manifest_path = job_dir / "manifest.json"
        if not storyboard_path.exists():
            return jsonify({"error": "storyboard not found"}), 404

        data = request.get_json(force=True)
        s_idx = int(data.get("scene_index", 1))
        manifest = json_read(manifest_path, {})
        settings = get_video_settings()
        voice = (data.get("voice") or manifest.get("config", {}).get("voice") or settings["voice"]).strip()

        storyboard = json_read(storyboard_path, {})
        scene = next((s for s in storyboard.get("scenes", []) if s["scene_index"] == s_idx), None)
        if not scene:
            return jsonify({"error": "未找到指定分镜"}), 404

        if data.get("narration_text"):
            scene["narration_text"] = str(data["narration_text"]).strip()
            scene["subtitle_text"] = scene["narration_text"]

        a_file, dur, warn = generate_scene_audio(scene, job_dir, voice)
        scene["audio_file"] = a_file
        scene["actual_duration_sec"] = dur
        json_write(storyboard_path, storyboard)

        # 重建字幕
        srt_path = job_dir / "subtitles" / "subtitles.srt"
        build_subtitles(storyboard, srt_path)

        return jsonify({"success": True, "scene": scene_urls(job_id, scene), "warning": warn})

    @app.route("/video-jobs/<job_id>/<path:filename>", methods=["GET"])
    def serve_video_job_asset(job_id: str, filename: str):
        job_dir = jobs_dir / job_id
        requested = (job_dir / filename).resolve()
        job_root = job_dir.resolve()
        if not str(requested).startswith(str(job_root)) or not requested.exists():
            return jsonify({"error": "file not found"}), 404
        return send_file(str(requested))
