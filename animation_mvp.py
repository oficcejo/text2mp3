import base64
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
import uuid
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import soundfile as sf
from flask import jsonify, request, send_file

from animation_engine import render_animation_video, THEMES, DEFAULT_FFMPEG


def register_animation_mvp_routes(app, deps: Dict[str, Any]):
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
            "timeout": int(os.getenv("OPENAI_TEXT_TIMEOUT", "180")),
        }

    def split_sentences_clean(text: str) -> List[str]:
        pattern = r'[^。！？!?；\n]+(?:[。！？!?；][”"’\']?|$)'
        items = re.findall(pattern, text)
        return [s.strip() for s in items if s.strip()]

    def estimate_duration_sec(text: str) -> float:
        estimated = max(4.0, len(text.strip()) / 4.6)
        return round(estimated, 1)

    def chunk_text_verbatim(raw_text: str, min_chars: int = 40, max_chars: int = 80) -> List[str]:
        """
        1:1 原文连续覆盖切分，保证不漏句、不删减。
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

        if start_arr != -1 and end_arr != -1 and (start_obj == -1 or start_arr < start_obj):
            try:
                return json.loads(text[start_arr:end_arr + 1])
            except Exception:
                pass

        if start_obj != -1 and end_obj != -1:
            try:
                return json.loads(text[start_obj:end_obj + 1])
            except Exception:
                pass

        return None

    def build_voice_payload(voice: str) -> Tuple[str, str, str]:
        voice = (voice or "").strip()
        if voice.startswith("cloned:"):
            clone_id = voice.split(":", 1)[1]
            try:
                sample_b64, clone_info = get_cloned_voice_sample(clone_id)
                return "mimo-v2.5-tts-voiceclone", clone_info.get("name", "克隆音色"), sample_b64
            except Exception as e:
                print(f"获取克隆音色样本异常: {e}", flush=True)
                return "mimo-v2.5-tts", "mimo_default", ""
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
        消除音频首尾的突变冲击与 TTS 启动脉冲，确保分镜转场无爆音。
        """
        try:
            data, sr = sf.read(str(path))
            if data.size == 0:
                return

            is_stereo = data.ndim > 1
            mono = data.mean(axis=1) if is_stereo else data

            # 1. 抑制启动脉冲
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
                        if valley_min < 0.025:
                            zero_samples = valley_start + int(np.argmin(chunk[valley_start:valley_end]))
                            zero_samples = min(zero_samples, int(0.085 * sr))
                            data[:zero_samples] = 0.0

            # 2. 直流偏置消除
            tail_check = min(len(mono), int(0.05 * sr))
            if tail_check > 0:
                tail_dc = np.mean(mono[-tail_check:])
                if abs(tail_dc) > 1e-5:
                    data = data - tail_dc

            # 3. 毫秒级汉宁窗平滑淡入淡出
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

    def write_silence_wav(path: Path, duration_sec: float) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        sr = 24000
        samples = int(max(0.5, duration_sec) * sr)
        silence = np.zeros(samples, dtype=np.float32)
        sf.write(str(path), silence, sr)

    def audio_duration(path: Path) -> float:
        try:
            info = sf.info(str(path))
            return float(info.duration)
        except Exception:
            return 0.0

    def generate_scene_audio(scene: Dict[str, Any], job_dir: Path, voice: str) -> Tuple[str, float, Optional[str]]:
        audio_path = job_dir / "audio" / f"scene_{scene['scene_index']:03d}.wav"
        warning = None
        model, voice_name, voice_sample_b64 = build_voice_payload(voice)
        style_instruction = voice_name if model == "mimo-v2.5-tts-voicedesign" else ""
        mimo_voice = "" if model == "mimo-v2.5-tts-voicedesign" else voice_name

        max_retries = 3
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
                if "429" in err_msg or "Too many requests" in err_msg:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                elif attempt < max_retries - 1:
                    time.sleep(1.5)
                    continue
                else:
                    warning = f"TTS fallback used: {exc}"
                    print(f"[Anim TTS Scene {scene['scene_index']}] 失败: {exc}，使用静音兜底", flush=True)
                    write_silence_wav(audio_path, scene.get("estimated_duration_sec", 6.0))
                    break

        duration = audio_duration(audio_path)
        if duration <= 0:
            duration = scene.get("estimated_duration_sec", 6.0)
            write_silence_wav(audio_path, duration)

        return str(audio_path.relative_to(job_dir)).replace("\\", "/"), duration, warning

    def build_subtitles(storyboard: Dict[str, Any], srt_path: Path) -> None:
        srt_path.parent.mkdir(parents=True, exist_ok=True)
        vtt_path = srt_path.with_suffix(".vtt")

        def to_srt_time(seconds: float) -> str:
            millis = int(round(seconds * 1000))
            hours = millis // 3600000
            millis %= 3600000
            minutes = millis // 60000
            millis %= 60000
            secs = millis // 1000
            milli = millis % 1000
            return f"{hours:02d}:{minutes:02d}:{secs:02d},{milli:03d}"

        def to_vtt_time(seconds: float) -> str:
            return to_srt_time(seconds).replace(",", ".")

        srt_lines = []
        vtt_lines = ["WEBVTT\n"]

        for idx, sc in enumerate(storyboard.get("scenes", []), 1):
            text = sc.get("narration_text", "").strip()
            if not text:
                continue
            st = sc.get("start_time", 0.0)
            et = sc.get("end_time", st + 4.0)

            srt_lines.append(str(idx))
            srt_lines.append(f"{to_srt_time(st)} --> {to_srt_time(et)}")
            srt_lines.append(text)
            srt_lines.append("")

            vtt_lines.append(str(idx))
            vtt_lines.append(f"{to_vtt_time(st)} --> {to_vtt_time(et)}")
            vtt_lines.append(text)
            vtt_lines.append("")

        srt_path.write_text("\n".join(srt_lines), encoding="utf-8")
        vtt_path.write_text("\n".join(vtt_lines), encoding="utf-8")

    def concatenate_audio_files(audio_files: List[Path], output_full_mp3: Path) -> float:
        """
        将所有分镜 wav 合并压制为单个高质量 MP3 音频流
        """
        output_full_mp3.parent.mkdir(parents=True, exist_ok=True)
        list_file = output_full_mp3.parent / "audio_list.txt"
        lines = [f"file '{p.resolve().as_posix()}'" for p in audio_files if p.exists()]
        list_file.write_text("\n".join(lines), encoding="utf-8")

        ffmpeg_bin = DEFAULT_FFMPEG or "ffmpeg"
        cmd = [
            ffmpeg_bin, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file.resolve()),
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            str(output_full_mp3.resolve())
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            # 回退到 wav 合并后转写
            data_list = []
            sr_target = 24000
            for p in audio_files:
                if p.exists():
                    d, s = sf.read(str(p))
                    data_list.append(d)
                    sr_target = s
            if data_list:
                full_data = np.concatenate(data_list)
                sf.write(str(output_full_mp3.with_suffix(".wav")), full_data, sr_target)
                return float(len(full_data) / sr_target)
        return audio_duration(output_full_mp3)

    # ============================================================
    # 启发式兜底分镜规划器 (智能识别语义与关键词匹配版式)
    # ============================================================
    def heuristic_plan_storyboard(raw_text: str) -> List[Dict[str, Any]]:
        chunks = chunk_text_verbatim(raw_text, min_chars=40, max_chars=85)
        if not chunks:
            chunks = [raw_text.strip() or "欢迎使用文生动画！基于纯矢量技术打造。"]

        # 可选版式轮换池
        layout_cycle = ["cards_grid", "comparison", "data_chart", "steps_flow", "metrics_grid", "code_terminal", "quote_focus"]
        scenes = []

        total_chunks = len(chunks)
        for idx, chunk in enumerate(chunks, 1):
            est_dur = estimate_duration_sec(chunk)
            
            # 关键词语义匹配类型
            chosen_type = None
            if idx == total_chunks and total_chunks >= 3:
                chosen_type = "call_to_action"
            elif any(k in chunk for k in ["对比", "痛点", "传统", "相比", "缺点", "繁琐", "vs", "优势"]):
                chosen_type = "comparison"
            elif any(k in chunk for k in ["走势", "行情", "K线", "指标", "曲线", "波动", "趋势", "数据"]):
                chosen_type = "data_chart"
            elif any(k in chunk for k in ["步骤", "第一步", "向导", "流程", "操作", "首先", "然后", "安装"]):
                chosen_type = "steps_flow"
            elif any(k in chunk for k in ["指标", "数字", "性能", "降低", "提升", "内存", "延迟", "统计", "毫秒", "%"]):
                chosen_type = "metrics_grid"
            elif any(k in chunk for k in ["代码", "终端", "命令", "脚本", "运行", "exe", "python", "git", "bash"]):
                chosen_type = "code_terminal"
            elif any(k in chunk for k in ["核心", "理念", "愿景", "关键", "观点", "名言", "总结"]):
                chosen_type = "quote_focus"
            else:
                chosen_type = layout_cycle[(idx - 1) % len(layout_cycle)]

            # 提炼简短主标题与标签
            first_sentence = split_sentences_clean(chunk)
            stitle = first_sentence[0][:20] if first_sentence else f"分镜场景 {idx}"
            if len(stitle) < 4:
                stitle = f"核心特性解构 · 0{idx}"
                
            tag_map = {
                "cards_grid": "🌟 核心亮点",
                "comparison": "⚖️ 深度对比",
                "data_chart": "📊 数据洞察",
                "steps_flow": "🚀 实操指南",
                "metrics_grid": "📈 性能指标",
                "code_terminal": "💻 极客终端",
                "quote_focus": "💡 观点聚焦",
                "call_to_action": "🎯 行动号召"
            }
            stag = tag_map.get(chosen_type, "🌟 动态场景")

            # 构建符合该类型的 content 默认结构
            content = {}
            if chosen_type == "cards_grid":
                content = {
                    "cards": [
                        {"title": "纯代码矢量渲染", "desc": "0 图像 Token 消耗，即开即用，文字超清", "badge": "免费极速"},
                        {"title": "MiMo 官方配音", "desc": "全套官方音色、克隆音色与音色设计深度联动", "badge": "毫秒级"},
                        {"title": "8 种动态版式", "desc": "数据图表、终端代码、步骤流全自动编排", "badge": "多版式"},
                        {"title": "一键换配音", "desc": "成片直接切换音色重混流，零等待", "badge": "超便捷"}
                    ]
                }
            elif chosen_type == "comparison":
                content = {
                    "left_title": "❌ 传统视频生成方案 (痛点)",
                    "left_items": ["生图排队等待长，API 费用昂贵", "文字模糊易变形乱码", "无法精确微调分镜和文字", "重新生成整片成本极高"],
                    "right_title": "✅ 本系统文生动画 (优势)",
                    "right_items": ["0 图像 Token，纯代码毫秒级压制", "字字锐利如矢量，排版毫厘精准", "支持可视化在线微调与重渲染", "一键秒换配音音色，混流即出片"]
                }
            elif chosen_type == "data_chart":
                content = {
                    "chart_title": "⚡ 动态趋势与量化指标实时监控流",
                    "indicators": ["自适应均线跟踪", "动态波动通道", "语义结构置信度 98%"],
                    "status_text": "🤖 AI 引擎诊断流：结构研判完成 ➔ 最优参数确定"
                }
            elif chosen_type == "steps_flow":
                content = {
                    "steps": [
                        {"step": "Step 1", "title": "文案输入", "desc": "粘贴任意长文或教程脚本，系统自动解析语义"},
                        {"step": "Step 2", "title": "分镜规划", "desc": "大模型智能拆解分镜，自动匹配 8 大高质感模板"},
                        {"step": "Step 3", "title": "MiMo 配音", "desc": "并发合成官方与克隆音色，边缘平滑抗爆音"},
                        {"step": "Step 4", "title": "成片导出", "desc": "FFmpeg rawvideo 管道流式压制，1080P 秒级出片"}
                    ]
                }
            elif chosen_type == "metrics_grid":
                content = {
                    "metrics": [
                        {"label": "生图 Token 消耗", "value": "0", "unit": "Tokens", "trend": "100% 免费"},
                        {"label": "1080P 渲染耗时", "value": "< 30s", "unit": "秒", "trend": "提升 15x"},
                        {"label": "文字排版清晰度", "value": "100%", "unit": "矢量保真", "trend": "无畸变"},
                        {"label": "MiMo 配音延迟", "value": "毫秒级", "unit": "实时对齐", "trend": "零爆音"}
                    ]
                }
            elif chosen_type == "code_terminal":
                content = {
                    "title": "bash - text2mp3 dynamic pipeline",
                    "lines": [
                        "$ python animation_mvp.py --model mimo-v2.5-tts --theme cyber_dark",
                        "[INFO] Initializing dynamic vector renderer (1920x1080 @ 30 FPS)...",
                        "[INFO] Synthesizing MiMo TTS voiceover for scenes...",
                        "[SUCCESS] Zero image token cost! Video pipe stream connected to FFmpeg stdin.",
                        "[RENDER] 1080P animation generated successfully: output/animation.mp4"
                    ]
                }
            elif chosen_type == "quote_focus":
                content = {
                    "quote": chunk[:75],
                    "author": "—— 核心观点聚焦",
                    "highlights": ["零生图开销", "极速出片", "超清矢量", "自由编排"]
                }
            elif chosen_type == "call_to_action":
                content = {
                    "title": "🚀 立即开启你的高质感动画创作之旅",
                    "desc": "开源免费 · 0 图像 Token · 纯代码压制 · MiMo 官方高保真配音",
                    "github_url": "https://github.com/oficcejo/text2mp3"
                }

            scenes.append({
                "scene_index": idx,
                "title": stitle,
                "subtitle": chunk[:40],
                "tag": stag,
                "type": chosen_type,
                "content": content,
                "narration_text": chunk,
                "estimated_duration_sec": est_dur
            })

        return scenes

    # ============================================================
    # LLM 分镜动画规划器 (智能大模型解析)
    # ============================================================
    def plan_storyboard_with_llm(raw_text: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        heuristic_scenes = heuristic_plan_storyboard(raw_text)
        openai_settings = get_openai_settings()
        token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        if not openai_settings["api_key"]:
            return heuristic_scenes, token_usage

        chunks = [s["narration_text"] for s in heuristic_scenes]
        system_msg = (
            "你是一名专业的技术发布会与科普视频视觉动效导演。请为用户的文案脚本规划矢量动画分镜。\n"
            "【严格合规与格式要求】：\n"
            "1. 严禁修改或缩减配音解说词！各分镜的 narration_text 必须与输入分块 100% 严格一致；\n"
            "2. 可用的视觉类型 (type) 仅限：cards_grid (特性卡片矩阵), comparison (左右红绿痛点优势对比), "
            "data_chart (数据图表与走势), steps_flow (步骤向导 1->2->3), metrics_grid (核心指标仪表盘), "
            "code_terminal (代码终端窗口), quote_focus (金句观点聚焦), call_to_action (尾声行动呼吁，仅限 GitHub 与开源口号，严禁包含 B 站信息)；\n"
            "3. 为每个分镜生成简短主标题 title (10~18 字)、副标题 subtitle (15~28 字)、胶囊标签 tag (4~8 字) 以及匹配该类型的 content 对象；\n"
            "4. content 字段规范（注意所有标签和序号必须是字符串）：\n"
            "   - steps_flow: 必须包含 steps 列表，每个元素形如 {\"step\": \"Step 1\", \"title\": \"...\", \"desc\": \"...\"}（step 必须为字符串）；\n"
            "   - comparison: 包含 left_title, left_items (字符串数组), right_title, right_items (字符串数组)；\n"
            "   - cards_grid: 包含 cards 列表，每个元素形如 {\"badge\": \"01\", \"title\": \"...\", \"desc\": \"...\"}；\n"
            "   - metrics_grid: 包含 metrics 列表，每个元素形如 {\"label\": \"...\", \"value\": \"...\", \"unit\": \"...\", \"trend\": \"...\"}；\n"
            "   - quote_focus: 包含 quote, author, highlights (字符串数组)；\n"
            "   - call_to_action: 包含 title, desc, github_url (\"https://github.com/oficcejo/text2mp3\"), footer (\"💬 欢迎在评论区交流讨论与提需求，欢迎去 GitHub 点个 Star 支持！\")；\n"
            "5. 输出格式必须为纯 JSON 数组，包含对象的字段：scene_index, title, subtitle, tag, type, content, narration_text。"
        )

        user_input_items = [{"scene_index": i, "narration_text": chunk} for i, chunk in enumerate(chunks, 1)]
        user_msg = f"请为以下分镜列表规划最佳动效视觉版式与结构化 content：\n{json.dumps(user_input_items, ensure_ascii=False)}"

        payload = {
            "model": openai_settings["text_model"],
            "temperature": 0.3,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
        }

        try:
            import requests
            response = requests.post(
                f"{openai_settings['base_url']}/chat/completions",
                headers={"Authorization": f"Bearer {openai_settings['api_key']}", "Content-Type": "application/json"},
                json=payload,
                timeout=(15, min(90, openai_settings["timeout"])),
            )
            if response.status_code == 200:
                data = response.json()
                usage = data.get("usage") or {}
                token_usage["prompt_tokens"] = int(usage.get("prompt_tokens") or 0)
                token_usage["completion_tokens"] = int(usage.get("completion_tokens") or 0)
                token_usage["total_tokens"] = int(usage.get("total_tokens") or (token_usage["prompt_tokens"] + token_usage["completion_tokens"]))

                reply = data["choices"][0]["message"]["content"]
                if isinstance(reply, list):
                    reply = "".join(part.get("text", "") for part in reply if isinstance(part, dict))

                raw_arr = extract_first_json_block(str(reply))
                if isinstance(raw_arr, list) and len(raw_arr) > 0:
                    llm_map = {item.get("scene_index"): item for item in raw_arr if isinstance(item, dict)}
                    for sc in heuristic_scenes:
                        s_idx = sc["scene_index"]
                        if s_idx in llm_map:
                            item = llm_map[s_idx]
                            if item.get("title"):
                                sc["title"] = str(item["title"]).strip()
                            if item.get("subtitle"):
                                sc["subtitle"] = str(item["subtitle"]).strip()
                            if item.get("tag"):
                                sc["tag"] = str(item["tag"]).strip()
                            if item.get("type") in ["cards_grid", "comparison", "data_chart", "steps_flow", "metrics_grid", "code_terminal", "quote_focus", "call_to_action"]:
                                sc["type"] = item["type"]
                            if isinstance(item.get("content"), dict) and item["content"]:
                                sc["content"] = item["content"]
        except Exception as e:
            print(f"[Anim LLM Planner] 调用异常，回退启发式规划: {e}", flush=True)

        return heuristic_scenes, token_usage

    # ============================================================
    # 异步作业执行主线程
    # ============================================================
    def run_animation_job(job_id: str):
        job_dir = jobs_dir / job_id
        manifest_path = job_dir / "manifest.json"
        storyboard_path = job_dir / "storyboard.json"

        manifest = json_read(manifest_path, {})
        config = manifest.get("config", {})
        voice = config.get("voice", "mimo_default")
        theme_name = config.get("theme", "cyber_dark")
        fps = int(config.get("fps", 30))
        res_str = config.get("resolution", "1920x1080")
        try:
            w_str, h_str = res_str.split("x")
            width, height = int(w_str), int(h_str)
        except Exception:
            width, height = 1920, 1080

        raw_text = manifest.get("raw_text", "").strip()

        try:
            # 阶段 1: 智能分镜规划
            manifest["status"] = "planning"
            manifest["stage"] = "planning"
            manifest["progress"] = 0.1
            manifest["detail_message"] = "正在通过大语言模型将文章结构化规划为动态矢量分镜..."
            manifest["updated_at"] = now_iso()
            json_write(manifest_path, manifest)

            scenes, token_usage = plan_storyboard_with_llm(raw_text)
            manifest["token_usage"] = {
                "text_tokens_est": token_usage["total_tokens"],
                "image_tokens": 0,
                "cost_note": "纯代码矢量渲染，0 图像 Token 消耗"
            }
            manifest["progress"] = 0.25
            manifest["detail_message"] = f"已规划完成 {len(scenes)} 个视觉分镜！正在并发调用 MiMo TTS 合成各分镜配音..."
            manifest["updated_at"] = now_iso()
            json_write(manifest_path, manifest)

            # 阶段 2: MiMo 并发语音合成与时间轴计算
            manifest["status"] = "synthesizing_audio"
            manifest["stage"] = "synthesizing_audio"
            json_write(manifest_path, manifest)

            audio_futures = {}
            with ThreadPoolExecutor(max_workers=3) as pool:
                for sc in scenes:
                    fut = pool.submit(generate_scene_audio, sc, job_dir, voice)
                    audio_futures[fut] = sc

                for fut in as_completed(audio_futures):
                    sc = audio_futures[fut]
                    a_rel, dur, warn = fut.result()
                    sc["audio_file"] = a_rel
                    sc["duration"] = dur

            # 严格按分镜序号排序并计算精准起止毫秒时间轴
            scenes.sort(key=lambda x: x["scene_index"])
            curr_time = 0.0
            audio_paths = []
            for sc in scenes:
                dur = max(0.5, sc.get("duration", 4.0))
                sc["start_time"] = round(curr_time, 3)
                curr_time += dur
                sc["end_time"] = round(curr_time, 3)
                sc["duration"] = round(dur, 3)
                audio_paths.append(job_dir / sc["audio_file"])

            total_duration = round(curr_time, 3)

            # 合并全片音频与生成字幕
            full_audio_mp3 = job_dir / "audio" / "full_voice.mp3"
            concatenate_audio_files(audio_paths, full_audio_mp3)

            storyboard = {
                "voice": voice,
                "theme": theme_name,
                "total_duration": total_duration,
                "scenes": scenes
            }
            json_write(storyboard_path, storyboard)

            srt_path = job_dir / "subtitles" / "subtitles.srt"
            build_subtitles(storyboard, srt_path)

            manifest["progress"] = 0.5
            manifest["duration_sec"] = total_duration
            manifest["detail_message"] = f"配音与字幕完成 (总长: {total_duration:.1f}s)！启动 FFmpeg 管道逐帧渲染 1080P 矢量动画..."
            manifest["updated_at"] = now_iso()
            json_write(manifest_path, manifest)

            # 阶段 3: FFmpeg 流式管道视频渲染
            manifest["status"] = "rendering_video"
            manifest["stage"] = "rendering_video"
            json_write(manifest_path, manifest)

            output_video_path = job_dir / "animation.mp4"

            def on_render_progress(pct: float, cur_frame: int, total_f: int, fps_act: float):
                prog = 0.5 + pct * 0.48
                manifest["progress"] = round(prog, 3)
                manifest["detail_message"] = f"正在流式压制动画视频: {pct*100:4.1f}% ({cur_frame}/{total_f}帧, {fps_act:.1f} FPS)"
                manifest["updated_at"] = now_iso()
                json_write(manifest_path, manifest)

            render_animation_video(
                storyboard=storyboard,
                audio_file=str(full_audio_mp3.resolve()),
                output_file=str(output_video_path.resolve()),
                theme_name=theme_name,
                width=width,
                height=height,
                fps=fps,
                progress_callback=on_render_progress
            )

            # 阶段 4: 完成
            manifest["status"] = "completed"
            manifest["stage"] = "completed"
            manifest["progress"] = 1.0
            manifest["video_file"] = "animation.mp4"
            manifest["audio_file"] = "audio/full_voice.mp3"
            manifest["srt_file"] = "subtitles/subtitles.srt"
            manifest["vtt_file"] = "subtitles/subtitles.vtt"
            manifest["detail_message"] = "🎉 文生动画成片生成成功！支持在线播放、下载或一键更换配音。"
            manifest["completed_at"] = now_iso()
            manifest["updated_at"] = now_iso()
            json_write(manifest_path, manifest)

        except Exception as e:
            traceback.print_exc()
            manifest["status"] = "failed"
            manifest["stage"] = "failed"
            manifest["error"] = str(e)
            manifest["detail_message"] = f"生成失败: {e}"
            manifest["updated_at"] = now_iso()
            json_write(manifest_path, manifest)

    # ============================================================
    # 响应辅助
    # ============================================================
    def get_job_response(job_id: str) -> Dict[str, Any]:
        job_dir = jobs_dir / job_id
        manifest_path = job_dir / "manifest.json"
        storyboard_path = job_dir / "storyboard.json"

        manifest = json_read(manifest_path, {})
        storyboard = json_read(storyboard_path, {})

        video_url = f"/animation-jobs/{job_id}/animation.mp4" if (job_dir / "animation.mp4").exists() else None
        audio_url = f"/animation-jobs/{job_id}/audio/full_voice.mp3" if (job_dir / "audio/full_voice.mp3").exists() else None
        srt_url = f"/animation-jobs/{job_id}/subtitles/subtitles.srt" if (job_dir / "subtitles/subtitles.srt").exists() else None

        scenes_with_urls = []
        for sc in storyboard.get("scenes", []):
            sc_copy = dict(sc)
            if sc.get("audio_file"):
                sc_copy["audio_url"] = f"/animation-jobs/{job_id}/{sc['audio_file']}"
            scenes_with_urls.append(sc_copy)

        return {
            "job_id": job_id,
            "project_name": manifest.get("project_name", "未命名动画"),
            "status": manifest.get("status", "unknown"),
            "stage": manifest.get("stage", ""),
            "progress": manifest.get("progress", 0.0),
            "detail_message": manifest.get("detail_message", ""),
            "duration_sec": manifest.get("duration_sec", 0.0),
            "created_at": manifest.get("created_at", ""),
            "updated_at": manifest.get("updated_at", ""),
            "video_url": video_url,
            "audio_url": audio_url,
            "srt_url": srt_url,
            "token_usage": manifest.get("token_usage", {"image_tokens": 0, "text_tokens_est": 0}),
            "config": manifest.get("config", {}),
            "storyboard": {
                "voice": storyboard.get("voice", ""),
                "theme": storyboard.get("theme", "cyber_dark"),
                "total_duration": storyboard.get("total_duration", 0.0),
                "scenes": scenes_with_urls
            },
            "error": manifest.get("error")
        }

    # ============================================================
    # REST API 路由
    # ============================================================

    @app.route("/api/animation/themes", methods=["GET"])
    def api_get_themes():
        themes_list = []
        for k, v in THEMES.items():
            themes_list.append({
                "id": k,
                "name": v["name"],
                "primary": f"rgb{v['primary']}",
                "bg": f"rgb{v['bg_dark']}",
                "accent": f"rgb{v['accent']}"
            })
        return jsonify({"themes": themes_list})

    @app.route("/api/animation/jobs", methods=["GET"])
    def api_list_animation_jobs():
        results = []
        for p in sorted(jobs_dir.glob("anim_*"), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_dir():
                manifest = json_read(p / "manifest.json", None)
                if manifest:
                    results.append({
                        "job_id": p.name,
                        "project_name": manifest.get("project_name", "未命名动画"),
                        "status": manifest.get("status", "unknown"),
                        "created_at": manifest.get("created_at", ""),
                        "duration_sec": manifest.get("duration_sec", 0.0),
                        "has_video": (p / "animation.mp4").exists(),
                        "voice": manifest.get("config", {}).get("voice", ""),
                        "theme": manifest.get("config", {}).get("theme", "cyber_dark")
                    })
        return jsonify({"jobs": results})

    @app.route("/api/animation/jobs", methods=["POST"])
    def api_create_animation_job():
        data = request.get_json(force=True)
        raw_text = (data.get("text") or "").strip()
        if not raw_text:
            return jsonify({"error": "文章内容不能为空"}), 400

        project_name = (data.get("project_name") or "").strip()
        if not project_name:
            first_line = split_sentences_clean(raw_text)
            project_name = first_line[0][:18] if first_line else "文生动画工程"

        voice = (data.get("voice") or "mimo_default").strip()
        theme = (data.get("theme") or "cyber_dark").strip()
        fps = int(data.get("fps") or 30)
        resolution = (data.get("resolution") or "1920x1080").strip()

        job_id = f"anim_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        job_dir = jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "job_id": job_id,
            "project_name": project_name,
            "status": "queued",
            "stage": "queued",
            "progress": 0.0,
            "detail_message": "作业已提交排队...",
            "raw_text": raw_text,
            "config": {
                "voice": voice,
                "theme": theme,
                "fps": fps,
                "resolution": resolution
            },
            "created_at": now_iso(),
            "updated_at": now_iso()
        }
        json_write(job_dir / "manifest.json", manifest)

        # 启动后台线程异步执行
        t = threading.Thread(target=run_animation_job, args=(job_id,), daemon=True)
        t.start()

        return jsonify({"success": True, "job_id": job_id, "job": get_job_response(job_id)})

    @app.route("/api/animation/jobs/<job_id>", methods=["GET"])
    def api_get_animation_job(job_id: str):
        job_dir = jobs_dir / job_id
        if not job_dir.exists():
            return jsonify({"error": "Job not found"}), 404
        return jsonify({"success": True, "job": get_job_response(job_id)})

    @app.route("/api/animation/jobs/<job_id>/save", methods=["POST"])
    def api_save_animation_project(job_id: str):
        job_dir = jobs_dir / job_id
        manifest_path = job_dir / "manifest.json"
        if not manifest_path.exists():
            return jsonify({"error": "Job not found"}), 404

        data = request.get_json(force=True)
        manifest = json_read(manifest_path, {})
        if data.get("project_name"):
            manifest["project_name"] = str(data["project_name"]).strip()
        manifest["updated_at"] = now_iso()
        json_write(manifest_path, manifest)

        return jsonify({"success": True, "project_name": manifest["project_name"]})

    @app.route("/api/animation/jobs/<job_id>/change-voice", methods=["POST"])
    def api_change_animation_voice(job_id: str):
        """
        一键更换配音：并发重配音，自动校准毫秒时间轴并快速重新压制成片
        """
        job_dir = jobs_dir / job_id
        manifest_path = job_dir / "manifest.json"
        storyboard_path = job_dir / "storyboard.json"
        if not storyboard_path.exists():
            return jsonify({"error": "Storyboard not found"}), 404

        data = request.get_json(force=True)
        new_voice = (data.get("voice") or "").strip()
        if not new_voice:
            return jsonify({"error": "音色未指定"}), 400

        manifest = json_read(manifest_path, {})
        manifest["config"]["voice"] = new_voice
        manifest["status"] = "synthesizing_audio"
        manifest["progress"] = 0.2
        manifest["detail_message"] = f"正在为各分镜重新生成配音 (音色: {new_voice})..."
        manifest["updated_at"] = now_iso()
        json_write(manifest_path, manifest)

        def run_voice_change():
            try:
                storyboard = json_read(storyboard_path, {})
                scenes = storyboard.get("scenes", [])
                storyboard["voice"] = new_voice

                # 并发生成新配音
                with ThreadPoolExecutor(max_workers=3) as pool:
                    futures = {pool.submit(generate_scene_audio, sc, job_dir, new_voice): sc for sc in scenes}
                    for fut in as_completed(futures):
                        sc = futures[fut]
                        a_rel, dur, _ = fut.result()
                        sc["audio_file"] = a_rel
                        sc["duration"] = dur

                # 重新计算时间轴
                scenes.sort(key=lambda x: x["scene_index"])
                curr_t = 0.0
                audio_paths = []
                for sc in scenes:
                    dur = max(0.5, sc.get("duration", 4.0))
                    sc["start_time"] = round(curr_t, 3)
                    curr_t += dur
                    sc["end_time"] = round(curr_t, 3)
                    sc["duration"] = round(dur, 3)
                    audio_paths.append(job_dir / sc["audio_file"])

                total_dur = round(curr_t, 3)
                storyboard["total_duration"] = total_dur
                json_write(storyboard_path, storyboard)

                # 合并音频与字幕
                full_mp3 = job_dir / "audio" / "full_voice.mp3"
                concatenate_audio_files(audio_paths, full_mp3)
                build_subtitles(storyboard, job_dir / "subtitles" / "subtitles.srt")

                # 快速压制视频
                manifest["status"] = "rendering_video"
                manifest["progress"] = 0.5
                manifest["duration_sec"] = total_dur
                manifest["detail_message"] = "音频已更新，正在重新渲染视频..."
                json_write(manifest_path, manifest)

                cfg = manifest.get("config", {})
                theme_name = cfg.get("theme", "cyber_dark")
                fps = int(cfg.get("fps", 30))
                res_str = cfg.get("resolution", "1920x1080")
                w, h = 1920, 1080
                try:
                    w, h = map(int, res_str.split("x"))
                except Exception:
                    pass

                def progress_cb(pct, c_f, t_f, fps_act):
                    manifest["progress"] = round(0.5 + pct * 0.48, 3)
                    manifest["detail_message"] = f"正在重新渲染视频: {pct*100:4.1f}% ({c_f}/{t_f}帧)"
                    json_write(manifest_path, manifest)

                render_animation_video(
                    storyboard=storyboard,
                    audio_file=str(full_mp3.resolve()),
                    output_file=str((job_dir / "animation.mp4").resolve()),
                    theme_name=theme_name,
                    width=w,
                    height=h,
                    fps=fps,
                    progress_callback=progress_cb
                )

                manifest["status"] = "completed"
                manifest["progress"] = 1.0
                manifest["detail_message"] = f"配音更换成功！已切换为 {new_voice} 并重新完成音画对齐渲染。"
                manifest["updated_at"] = now_iso()
                json_write(manifest_path, manifest)
            except Exception as e:
                manifest["status"] = "failed"
                manifest["error"] = str(e)
                manifest["detail_message"] = f"换配音失败: {e}"
                json_write(manifest_path, manifest)

        t = threading.Thread(target=run_voice_change, daemon=True)
        t.start()

        return jsonify({"success": True, "message": "换配音任务已提交"})

    @app.route("/api/animation/jobs/<job_id>/update-scenes", methods=["POST"])
    def api_update_animation_scenes(job_id: str):
        """
        在线保存用户修改的分镜数据 (标题, 副标题, 标签, 视觉类型, 解说词)
        """
        job_dir = jobs_dir / job_id
        storyboard_path = job_dir / "storyboard.json"
        manifest_path = job_dir / "manifest.json"
        if not storyboard_path.exists():
            return jsonify({"error": "Storyboard not found"}), 404

        data = request.get_json(force=True)
        new_scenes = data.get("scenes", [])
        if not isinstance(new_scenes, list):
            return jsonify({"error": "scenes 必须是数组"}), 400

        storyboard = json_read(storyboard_path, {})
        existing_scenes = storyboard.get("scenes", [])
        lookup = {s["scene_index"]: s for s in existing_scenes}

        for ns in new_scenes:
            s_idx = ns.get("scene_index")
            if s_idx in lookup:
                cur = lookup[s_idx]
                if ns.get("title") is not None:
                    cur["title"] = str(ns["title"]).strip()
                if ns.get("subtitle") is not None:
                    cur["subtitle"] = str(ns["subtitle"]).strip()
                if ns.get("tag") is not None:
                    cur["tag"] = str(ns["tag"]).strip()
                if ns.get("type") is not None and ns["type"] in THEMES:
                    cur["type"] = ns["type"]
                if isinstance(ns.get("content"), dict):
                    cur["content"] = ns["content"]
                if ns.get("narration_text") is not None:
                    cur["narration_text"] = str(ns["narration_text"]).strip()

        storyboard["scenes"] = existing_scenes
        json_write(storyboard_path, storyboard)

        # 同步更新字幕
        build_subtitles(storyboard, job_dir / "subtitles" / "subtitles.srt")

        manifest = json_read(manifest_path, {})
        manifest["updated_at"] = now_iso()
        manifest["detail_message"] = "分镜内容已更新！可点击「重新渲染成片」导出最新视频。"
        json_write(manifest_path, manifest)

        return jsonify({"success": True, "job": get_job_response(job_id)})

    @app.route("/api/animation/jobs/<job_id>/rerender", methods=["POST"])
    def api_rerender_animation(job_id: str):
        """
        重新渲染分镜视频
        """
        job_dir = jobs_dir / job_id
        manifest_path = job_dir / "manifest.json"
        storyboard_path = job_dir / "storyboard.json"
        if not storyboard_path.exists():
            return jsonify({"error": "Storyboard not found"}), 404

        data = request.get_json(silent=True) or {}
        manifest = json_read(manifest_path, {})
        if data.get("theme"):
            manifest["config"]["theme"] = data["theme"]
        if data.get("fps"):
            manifest["config"]["fps"] = int(data["fps"])

        manifest["status"] = "synthesizing_audio"
        manifest["progress"] = 0.15
        manifest["detail_message"] = "重新合成配音与渲染中..."
        json_write(manifest_path, manifest)

        def do_rerender():
            try:
                storyboard = json_read(storyboard_path, {})
                scenes = storyboard.get("scenes", [])
                voice = manifest.get("config", {}).get("voice", "mimo_default")

                with ThreadPoolExecutor(max_workers=3) as pool:
                    futures = {pool.submit(generate_scene_audio, sc, job_dir, voice): sc for sc in scenes}
                    for fut in as_completed(futures):
                        sc = futures[fut]
                        a_rel, dur, _ = fut.result()
                        sc["audio_file"] = a_rel
                        sc["duration"] = dur

                scenes.sort(key=lambda x: x["scene_index"])
                curr_t = 0.0
                audio_paths = []
                for sc in scenes:
                    dur = max(0.5, sc.get("duration", 4.0))
                    sc["start_time"] = round(curr_t, 3)
                    curr_t += dur
                    sc["end_time"] = round(curr_t, 3)
                    sc["duration"] = round(dur, 3)
                    audio_paths.append(job_dir / sc["audio_file"])

                total_dur = round(curr_t, 3)
                storyboard["total_duration"] = total_dur
                json_write(storyboard_path, storyboard)

                full_mp3 = job_dir / "audio" / "full_voice.mp3"
                concatenate_audio_files(audio_paths, full_mp3)
                build_subtitles(storyboard, job_dir / "subtitles" / "subtitles.srt")

                manifest["status"] = "rendering_video"
                manifest["progress"] = 0.5
                manifest["duration_sec"] = total_dur
                json_write(manifest_path, manifest)

                cfg = manifest.get("config", {})
                theme_name = cfg.get("theme", "cyber_dark")
                fps = int(cfg.get("fps", 30))
                w, h = 1920, 1080
                try:
                    w, h = map(int, cfg.get("resolution", "1920x1080").split("x"))
                except Exception:
                    pass

                def progress_cb(pct, c_f, t_f, fps_act):
                    manifest["progress"] = round(0.5 + pct * 0.48, 3)
                    manifest["detail_message"] = f"正在压制视频: {pct*100:4.1f}% ({c_f}/{t_f}帧)"
                    json_write(manifest_path, manifest)

                render_animation_video(
                    storyboard=storyboard,
                    audio_file=str(full_mp3.resolve()),
                    output_file=str((job_dir / "animation.mp4").resolve()),
                    theme_name=theme_name,
                    width=w,
                    height=h,
                    fps=fps,
                    progress_callback=progress_cb
                )

                manifest["status"] = "completed"
                manifest["progress"] = 1.0
                manifest["detail_message"] = "🎉 重新渲染完成！"
                manifest["updated_at"] = now_iso()
                json_write(manifest_path, manifest)
            except Exception as e:
                manifest["status"] = "failed"
                manifest["error"] = str(e)
                manifest["detail_message"] = f"渲染失败: {e}"
                json_write(manifest_path, manifest)

        t = threading.Thread(target=do_rerender, daemon=True)
        t.start()

        return jsonify({"success": True, "message": "重新渲染任务已启动"})

    @app.route("/api/animation/jobs/<job_id>", methods=["DELETE"])
    def api_delete_animation_job(job_id: str):
        job_dir = jobs_dir / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"success": True})

    @app.route("/animation-jobs/<job_id>/<path:filename>", methods=["GET"])
    def serve_animation_job_asset(job_id: str, filename: str):
        job_dir = jobs_dir / job_id
        requested = (job_dir / filename).resolve()
        job_root = job_dir.resolve()
        if not str(requested).startswith(str(job_root)) or not requested.exists():
            return jsonify({"error": "file not found"}), 404
        return send_file(str(requested))
