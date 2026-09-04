"""
小米 MiMo TTS 文字转语音 Web 应用程序
基于 MiMo-V2.5-TTS 系列 API
"""

import os
import base64
import json
import uuid
import time
import threading
import queue
import traceback
from datetime import datetime
from pathlib import Path

import requests
import numpy as np
import soundfile as sf
from flask import (
    Flask, render_template, request, jsonify,
    send_file, session, Response, stream_with_context
)
from dotenv import load_dotenv
from video_mvp import register_video_mvp_routes
from animation_mvp import register_animation_mvp_routes

load_dotenv()

# 自动探测并注入 FFmpeg 路径到 PATH 与 pydub
ffmpeg_bin = os.getenv("FFMPEG_BIN", "").strip()
ffmpeg_candidate_dirs = []
if ffmpeg_bin and Path(ffmpeg_bin).exists():
    ffmpeg_candidate_dirs.append(str(Path(ffmpeg_bin).parent))
for p in [r"D:\ffmpeg\bin", r"C:\ffmpeg\bin", r"D:\web\videotool"]:
    if (Path(p) / "ffmpeg.exe").exists() and p not in ffmpeg_candidate_dirs:
        ffmpeg_candidate_dirs.append(p)

try:
    import imageio_ffmpeg
    _img_ff = imageio_ffmpeg.get_ffmpeg_exe()
    if _img_ff and Path(_img_ff).exists():
        _pdir = str(Path(_img_ff).parent)
        if _pdir not in ffmpeg_candidate_dirs:
            ffmpeg_candidate_dirs.append(_pdir)
except Exception:
    pass

for fdir in ffmpeg_candidate_dirs:
    if fdir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = fdir + os.pathsep + os.environ.get("PATH", "")

try:
    import shutil
    from pydub import AudioSegment
    _ff_path = shutil.which("ffmpeg")
    if _ff_path:
        AudioSegment.converter = _ff_path
    _ffprobe_path = shutil.which("ffprobe")
    if _ffprobe_path:
        AudioSegment.ffprobe = _ffprobe_path
except Exception:
    pass

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ============================================================
# 配置
# ============================================================
MIMO_API_BASE = "https://api.xiaomimimo.com/v1"
MIMO_API_KEY = os.getenv("MIMO_API_KEY", "")
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)
HISTORY_FILE = Path("history.json")
CLONED_VOICES_DIR = Path("cloned_voices")
CLONED_VOICES_DIR.mkdir(exist_ok=True)
CLONED_VOICES_INDEX = CLONED_VOICES_DIR / "index.json"

DESIGNED_VOICES_DIR = Path("designed_voices")
DESIGNED_VOICES_DIR.mkdir(exist_ok=True)
DESIGNED_VOICES_INDEX = DESIGNED_VOICES_DIR / "index.json"

# 内置音色列表（v2.5-tts，来自小米 MiMo TTS 官方全集文档）
BUILTIN_VOICES = {
    "mimo_default": {
        "name": "MiMo-默认",
        "gender": "女",
        "lang": "中文",
        "desc": "智能自适应 (国内默认冰糖/海外Mia)",
    },
    "冰糖": {
        "name": "冰糖",
        "gender": "女",
        "lang": "中文",
        "desc": "甜美清澈 · 适合小说散文、艺术朗读、生活Vlog",
    },
    "茉莉": {
        "name": "茉莉",
        "gender": "女",
        "lang": "中文",
        "desc": "温柔典雅 · 适合有声书、情感电台、散文旁白",
    },
    "苏打": {
        "name": "苏打",
        "gender": "男",
        "lang": "中文",
        "desc": "阳光活力 · 适合科技科普、新闻播报、短视频叙事",
    },
    "白桦": {
        "name": "白桦",
        "gender": "男",
        "lang": "中文",
        "desc": "沉稳磁性 · 适合历史传记、财经解说、庄重纪录片",
    },
    "Mia": {
        "name": "Mia",
        "gender": "女",
        "lang": "英文",
        "desc": "自然温暖 · 适合故事叙事、日常会话",
    },
    "Chloe": {
        "name": "Chloe",
        "gender": "女",
        "lang": "英文",
        "desc": "轻快活泼 · 适合快节奏解说、现代对话",
    },
    "Milo": {
        "name": "Milo",
        "gender": "男",
        "lang": "英文",
        "desc": "沉稳专业 · 适合商业演示、说明文",
    },
    "Dean": {
        "name": "Dean",
        "gender": "男",
        "lang": "英文",
        "desc": "磁性低沉 · 适合影视解说、深夜广播",
    },
}

# V2 兼容音色
V2_VOICES = {
    "mimo_default": "MiMo 默认音色",
    "default_zh": "中文女声",
    "default_en": "英文女声",
}

# 模型定义
MODELS = {
    "mimo-v2.5-tts": {
        "label": "MiMo-V2.5-TTS",
        "desc": "内置高品质音色，支持唱歌模式、细粒度风格控制",
        "supports_voices": True,
        "supports_voiceclone": False,
        "supports_voicedesign": False,
        "supports_singing": True,
        "voices": BUILTIN_VOICES,
    },
    "mimo-v2.5-tts-voicedesign": {
        "label": "MiMo-V2.5-TTS-VoiceDesign",
        "desc": "通过文字描述自定义音色，无需预设或音频样本",
        "supports_voices": False,
        "supports_voiceclone": False,
        "supports_voicedesign": True,
        "supports_singing": False,
        "voices": {},
    },
    "mimo-v2.5-tts-voiceclone": {
        "label": "MiMo-V2.5-TTS-VoiceClone",
        "desc": "基于音频样本高保真复制任意音色",
        "supports_voices": False,
        "supports_voiceclone": True,
        "supports_voicedesign": False,
        "supports_singing": False,
        "voices": {},
    },
    "mimo-v2-tts": {
        "label": "MiMo-V2-TTS",
        "desc": "V2 版本 TTS，兼容旧版接口",
        "supports_voices": True,
        "supports_voiceclone": False,
        "supports_voicedesign": False,
        "supports_singing": True,
        "voices": V2_VOICES,
    },
}


# ============================================================
# 历史记录
# ============================================================
def load_history():
    if HISTORY_FILE.exists():
        try:
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            return data[:50]
        except Exception:
            return []
    return []


def save_history(entry):
    history = load_history()
    history.insert(0, entry)
    if len(history) > 50:
        history = history[:50]
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ============================================================
# 克隆音色存储
# ============================================================
def load_cloned_voices():
    """加载已克隆音色列表"""
    if CLONED_VOICES_INDEX.exists():
        try:
            data = json.loads(CLONED_VOICES_INDEX.read_text(encoding="utf-8"))
            return data
        except Exception:
            return []
    return []


def save_cloned_voice(voice_id: str, info: dict):
    """保存克隆音色记录"""
    voices = load_cloned_voices()
    # 去重：同 ID 则替换
    voices = [v for v in voices if v.get("id") != voice_id]
    voices.insert(0, info)
    CLONED_VOICES_INDEX.write_text(
        json.dumps(voices, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def delete_cloned_voice(voice_id: str):
    """删除克隆音色及其音频文件"""
    voices = load_cloned_voices()
    voices = [v for v in voices if v.get("id") != voice_id]
    CLONED_VOICES_INDEX.write_text(
        json.dumps(voices, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 删除音频文件目录
    voice_dir = CLONED_VOICES_DIR / voice_id
    if voice_dir.exists():
        import shutil
        shutil.rmtree(str(voice_dir), ignore_errors=True)


def get_cloned_voice_sample(voice_id: str) -> bytes:
    """读取克隆音色的音频样本"""
    sample_path = CLONED_VOICES_DIR / voice_id / "sample.wav"
    if sample_path.exists():
        return sample_path.read_bytes()
    raise FileNotFoundError(f"克隆音色 {voice_id} 的样本文件不存在")


def get_cloned_voice_info(voice_id: str) -> dict:
    """获取单个克隆音色信息"""
    info_path = CLONED_VOICES_DIR / voice_id / "info.json"
    if info_path.exists():
        return json.loads(info_path.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"克隆音色 {voice_id} 的信息文件不存在")


# ============================================================
# 音色设计（Voice Design）存储
# ============================================================
def load_designed_voices():
    """加载已保存的设计音色列表"""
    if DESIGNED_VOICES_INDEX.exists():
        try:
            return json.loads(DESIGNED_VOICES_INDEX.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_designed_voice(voice_id: str, info: dict):
    """保存设计音色记录"""
    voices = load_designed_voices()
    voices = [v for v in voices if v.get("id") != voice_id]
    voices.insert(0, info)
    DESIGNED_VOICES_INDEX.write_text(
        json.dumps(voices, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def delete_designed_voice(voice_id: str):
    """删除设计音色及其音频样本"""
    voices = load_designed_voices()
    voices = [v for v in voices if v.get("id") != voice_id]
    DESIGNED_VOICES_INDEX.write_text(
        json.dumps(voices, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    voice_dir = DESIGNED_VOICES_DIR / voice_id
    if voice_dir.exists():
        import shutil
        shutil.rmtree(str(voice_dir), ignore_errors=True)


def get_designed_voice(voice_id: str) -> dict:
    """获取单个设计音色信息"""
    voices = load_designed_voices()
    for v in voices:
        if v.get("id") == voice_id:
            return v
    # 尝试直接读目录
    info_path = DESIGNED_VOICES_DIR / voice_id / "info.json"
    if info_path.exists():
        return json.loads(info_path.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"设计音色 {voice_id} 不存在")


# ============================================================
# 进度状态类
# ============================================================
class ProgressEvent:
    def __init__(self):
        self._queue = queue.Queue()
        self._done = False

    def send(self, stage: str, progress: int, message: str, **extra):
        event = {"stage": stage, "progress": progress, "message": message}
        event.update(extra)
        if not self._done:
            self._queue.put(event)

    def done(self, result: dict = None):
        if result:
            self._queue.put({
                "stage": "complete", "progress": 100,
                "message": "合成完成！", "result": result
            })
        self._done = True
        self._queue.put(None)

    def error(self, err_msg: str):
        self._queue.put({"stage": "error", "progress": 0, "message": err_msg})
        self._done = True
        self._queue.put(None)

    def __iter__(self):
        return self

    def __next__(self):
        item = self._queue.get()
        if item is None:
            raise StopIteration
        return item


# ============================================================
# 文本分块（长文本自动切割，避免超过 API token 限制）
# ============================================================
MAX_CHUNK_CHARS = 4500  # 中文每字符约 1-2 token，4500 字 ≈ 4500-9000 token，安全落在 8192 以内
VOICECLONE_MAX_CHUNK_CHARS = 3000  # voiceclone 模式：音频样本也消耗 token，需更保守
SILENCE_SECONDS = 0.3   # 分块之间的静音间隔


def split_text_for_tts(text: str, max_chars: int = MAX_CHUNK_CHARS):
    """按句子边界将长文本切分为多个块，每块不超过 max_chars"""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    chunks = []
    # 主分隔符（句子级）
    sentence_seps = set("。！？\n")
    # 次分隔符（从句级）
    clause_seps = set("，；：、")

    current = ""
    for ch in text:
        current += ch
        if len(current) >= max_chars:
            # 尝试在最近的句子分隔符处切断
            cut = -1
            for i in range(len(current) - 1, max(len(current) - 500, 0), -1):
                if current[i] in sentence_seps:
                    cut = i + 1
                    break
            # 如果没有句子分隔符，尝试从句分隔符
            if cut < 0:
                for i in range(len(current) - 1, max(len(current) - 300, 0), -1):
                    if current[i] in clause_seps:
                        cut = i + 1
                        break
            # 硬切
            if cut < 0:
                cut = max_chars

            chunks.append(current[:cut])
            current = current[cut:]
    if current.strip():
        chunks.append(current)
    return chunks

# API 调用核心（带进度回调）
# ============================================================
def call_mimo_tts(
    model: str,
    text: str,
    voice: str = "",
    style_instruction: str = "",
    audio_format: str = "wav",
    stream: bool = False,
    voice_audio_base64: str = "",
    voice_audio_mime: str = "audio/mpeg",
    progress: ProgressEvent = None,
    voice_info: dict = None,
):
    def emit(stage, pct, msg, **kw):
        if progress:
            progress.send(stage, pct, msg, **kw)

    if not MIMO_API_KEY:
        raise ValueError("未配置 MIMO_API_KEY，请在 .env 文件中设置")

    emit("preparing", 5, "正在准备请求参数...")
    time.sleep(0.1)

    headers = {
        "api-key": MIMO_API_KEY,
        "Content-Type": "application/json",
    }

    # 支持 designed: 和 cloned: 前缀直接传入
    if voice and isinstance(voice, str) and voice.startswith("designed:"):
        design_id = voice.split(":", 1)[1]
        try:
            d_voice = get_designed_voice(design_id)
            model = "mimo-v2.5-tts-voicedesign"
            if not style_instruction:
                style_instruction = d_voice.get("prompt", "")
        except Exception as e:
            print(f"获取设计音色失败: {e}", flush=True)
    elif voice and isinstance(voice, str) and voice.startswith("cloned:"):
        clone_id = voice.split(":", 1)[1]
        try:
            model = "mimo-v2.5-tts-voiceclone"
            sample_bytes = get_cloned_voice_sample(clone_id)
            voice_audio_base64 = base64.b64encode(sample_bytes).decode("utf-8")
            voice_audio_mime = "audio/wav"
        except Exception as e:
            print(f"获取克隆音色失败: {e}", flush=True)

    # 构建 messages
    messages = []

    # user 消息：风格指令 / 音色描述
    if style_instruction:
        messages.append({"role": "user", "content": style_instruction})
    elif model == "mimo-v2.5-tts-voicedesign":
        messages.append({"role": "user", "content": "请生成一个自然的人声"})
    else:
        messages.append({"role": "user", "content": "请朗读以下内容"})

    messages.append({"role": "assistant", "content": text})

    # 构建 audio 参数
    audio_params = {"format": audio_format}
    if model == "mimo-v2.5-tts-voiceclone" and voice_audio_base64:
        # 预处理：将上传音频转换为标准化 WAV，提高 API 兼容性
        emit("preparing", 8, "正在预处理音频样本...")
        import io as _io
        try:
            audio_bytes = base64.b64decode(voice_audio_base64)
            from pydub import AudioSegment
            audio_seg = AudioSegment.from_file(_io.BytesIO(audio_bytes))
            original_dur = len(audio_seg) / 1000.0
            # 限制最长 10 秒，减少 token 消耗
            MAX_CLONE_DURATION_MS = 10000
            if len(audio_seg) > MAX_CLONE_DURATION_MS:
                audio_seg = audio_seg[:MAX_CLONE_DURATION_MS]
                emit("preparing", 9, f"音频样本过长，已截取前 10 秒（原始 {original_dur:.1f}s）")
                original_dur = len(audio_seg) / 1000.0
            # 统一转为 16kHz mono 16-bit PCM WAV
            audio_seg = audio_seg.set_frame_rate(16000).set_channels(1).set_sample_width(2)
            wav_buf = _io.BytesIO()
            audio_seg.export(wav_buf, format="wav")
            wav_bytes = wav_buf.getvalue()
            voice_audio_base64 = base64.b64encode(wav_bytes).decode("utf-8")
            voice_audio_mime = "audio/wav"
            emit("preparing", 10, f"音频预处理完成 (原始: {len(audio_bytes)/1024:.0f}KB/{original_dur:.1f}s, 转换后: {len(wav_bytes)/1024:.0f}KB)")
        except Exception as _preproc_err:
            emit("preparing", 10, f"音频预处理失败，尝试使用原始格式 ({_preproc_err})")
        # 回传预处理后的音频数据给调用者（用于保存克隆音色）
        if voice_info is not None:
            voice_info["base64"] = voice_audio_base64
        audio_params["voice"] = f"data:{voice_audio_mime};base64,{voice_audio_base64}"
    elif model == "mimo-v2.5-tts-voicedesign":
        pass
    elif voice and not str(voice).startswith(("cloned:", "designed:")):
        audio_params["voice"] = voice

    payload = {
        "model": model,
        "messages": messages,
        "audio": audio_params,
        "stream": stream,
        "max_completion_tokens": 4096,
        "temperature": 0.6,
        "top_p": 0.95,
    }

    emit("connecting", 15, "正在连接小米 MiMo API...")

    max_retries = 2
    last_error = None
    for attempt in range(max_retries + 1):
        if attempt > 0:
            wait = 2 ** attempt
            emit("connecting", 15, f"第 {attempt} 次重试（{wait}s 后）...")
            time.sleep(wait)

        try:
            resp = requests.post(
                f"{MIMO_API_BASE}/chat/completions",
                headers=headers,
                json=payload,
                stream=stream,
                timeout=(15, 300),
            )
            break
        except requests.exceptions.ReadTimeout:
            last_error = "等待 MiMo API 响应超时（300s）"
            if attempt < max_retries:
                continue
        except requests.exceptions.ConnectTimeout:
            raise RuntimeError("连接小米 MiMo API 超时，请检查网络")
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(f"无法连接到小米 MiMo API ({e})")
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"API 请求失败: {e}")

    if last_error:
        raise RuntimeError(f"{last_error}，已重试 {max_retries} 次仍失败，请稍后再试或缩短文本")
    if resp.status_code != 200:
        error_detail = resp.text
        try:
            err_json = resp.json()
            error_detail = json.dumps(err_json, ensure_ascii=False)
        except Exception:
            pass
        # 输出诊断信息到控制台
        print(f"[MiMo API Error] HTTP {resp.status_code}", flush=True)
        print(f"  Model: {model}, Text: {text[:50]}...", flush=True)
        if voice_audio_base64:
            padded = voice_audio_base64 + "=" * (-len(voice_audio_base64) % 4)
            audio_byte_len = len(base64.b64decode(padded))
            print(f"  Voice Audio: {audio_byte_len/1024:.1f} KB, MIME: {voice_audio_mime}", flush=True)
        print(f"  Error: {error_detail[:500]}", flush=True)
        raise RuntimeError(f"API 调用失败 (HTTP {resp.status_code}): {error_detail}")

    emit("generating", 30, "等待语音生成中...")

    if stream:
        collected = np.array([], dtype=np.float32)
        chunk_count = 0
        for line in resp.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8", errors="replace")
            if not line_str.startswith("data: "):
                continue
            data_str = line_str[6:]
            if data_str.strip() == "[DONE]":
                break
            try:
                chunk_data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            choices = chunk_data.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            audio_data = delta.get("audio")
            if audio_data and "data" in audio_data:
                pcm_bytes = base64.b64decode(audio_data["data"])
                np_pcm = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                collected = np.concatenate((collected, np_pcm))
                chunk_count += 1
                pct = min(30 + chunk_count * 2, 80)
                kb = len(pcm_bytes) / 1024
                emit("generating", pct, f"正在接收语音数据... ({kb:.1f} KB)")

        emit("processing", 80, f"已接收 {chunk_count} 个数据包，正在组装音频...")
        import tempfile as tmpmod
        tmp = tmpmod.NamedTemporaryFile(suffix=".wav", delete=False)
        sf.write(tmp.name, collected, samplerate=24000)
        with open(tmp.name, "rb") as f:
            wav_bytes = f.read()
        os.unlink(tmp.name)
        emit("processing", 90, "音频组装完成")
        return wav_bytes, "wav"
    else:
        emit("generating", 40, "正在向模型发送合成请求...")
        result = resp.json()
        emit("generating", 60, "模型返回结果，正在解析音频数据...")
        message = result["choices"][0]["message"]
        audio_data_b64 = message["audio"]["data"]
        audio_bytes = base64.b64decode(audio_data_b64)
        audio_len_kb = len(audio_bytes) / 1024
        emit("processing", 80, f"已获取音频数据 ({audio_len_kb:.1f} KB)")
        return audio_bytes, audio_format


def convert_to_mp3(wav_bytes: bytes) -> bytes:
    """将 WAV 字节转换为 MP3"""
    import io
    import shutil
    from pydub import AudioSegment

    if not getattr(AudioSegment, "converter", None):
        ff = shutil.which("ffmpeg") or (r"D:\ffmpeg\bin\ffmpeg.exe" if Path(r"D:\ffmpeg\bin\ffmpeg.exe").exists() else None)
        if ff:
            AudioSegment.converter = str(ff)

    try:
        audio = AudioSegment.from_wav(io.BytesIO(wav_bytes))
        buf = io.BytesIO()
        audio.export(buf, format="mp3", bitrate="192k")
        return buf.getvalue()
    except Exception as e:
        raise RuntimeError(f"MP3 转换失败（请检查 FFmpeg 是否可用）: {e}")


register_video_mvp_routes(app, {
    "output_dir": OUTPUT_DIR,
    "get_cloned_voice_sample": get_cloned_voice_sample,
    "call_mimo_tts": call_mimo_tts,
    "load_cloned_voices": load_cloned_voices,
    "load_designed_voices": load_designed_voices,
    "get_designed_voice": get_designed_voice,
    "builtin_voices": BUILTIN_VOICES,
})

register_animation_mvp_routes(app, {
    "output_dir": OUTPUT_DIR,
    "get_cloned_voice_sample": get_cloned_voice_sample,
    "call_mimo_tts": call_mimo_tts,
    "load_cloned_voices": load_cloned_voices,
    "load_designed_voices": load_designed_voices,
    "get_designed_voice": get_designed_voice,
    "builtin_voices": BUILTIN_VOICES,
})


# ============================================================
# Flask 路由
# ============================================================

@app.route("/")
def index():
    return render_template("index.html", models=MODELS)


# ---------------------------------------------------------------
# SSE 进度端点（合成 + 克隆 + 设计共用）
# ---------------------------------------------------------------
@app.route("/api/tts/progress", methods=["POST"])
def api_tts_progress():
    data = request.get_json(force=True)
    model = data.get("model", "mimo-v2.5-tts")
    text = data.get("text", "").strip()
    voice = data.get("voice", "")
    style_instruction = data.get("style_instruction", "")
    audio_format = data.get("audio_format", "mp3")
    stream = data.get("stream", False)
    voice_audio_base64 = data.get("voice_audio_base64", "")
    voice_audio_mime = data.get("voice_audio_mime", "audio/mpeg")
    voice_name = data.get("voice_name", "").strip()
    cloned_voice_id = data.get("cloned_voice_id", "").strip()

    if not text:
        def err_gen():
            yield f"data: {json.dumps({'stage': 'error', 'progress': 0, 'message': '请输入要合成的文本'})}\n\n"
        return Response(err_gen(), mimetype="text/event-stream")

    if model not in MODELS and model != "mimo-v2.5-tts-voiceclone":
        def err_gen():
            yield f"data: {json.dumps({'stage': 'error', 'progress': 0, 'message': f'不支持的模型: {model}'})}\n\n"
        return Response(err_gen(), mimetype="text/event-stream")

    # 检查 base64 音频大小
    if voice_audio_base64 and len(voice_audio_base64) > 15 * 1024 * 1024:
        def err_gen():
            yield f"data: {json.dumps({'stage': 'error', 'progress': 0, 'message': '音频样本过大，请使用 10MB 以下的文件'})}\n\n"
        return Response(err_gen(), mimetype="text/event-stream")

    # 如果 voice 以 "cloned:" 开头，从存储加载克隆音色样本
    if voice.startswith("cloned:") and not voice_audio_base64:
        cid = voice.split(":", 1)[1]
        try:
            sample_bytes = get_cloned_voice_sample(cid)
            voice_audio_base64 = base64.b64encode(sample_bytes).decode("utf-8")
            voice_audio_mime = "audio/wav"
            model = "mimo-v2.5-tts-voiceclone"
            voice = ""
            cloned_voice_id = cid
        except FileNotFoundError:
            def err_gen():
                yield f"data: {json.dumps({'stage': 'error', 'progress': 0, 'message': f'克隆音色 {cid} 不存在'})}\n\n"
            return Response(err_gen(), mimetype="text/event-stream")
        except Exception as e:
            def err_gen():
                yield f"data: {json.dumps({'stage': 'error', 'progress': 0, 'message': f'加载克隆音色失败: {e}'})}\n\n"
            return Response(err_gen(), mimetype="text/event-stream")

    # 处理唱歌模式
    singing = data.get("singing", False)
    final_text = text
    if singing and (model == "mimo-v2.5-tts" or model == "mimo-v2-tts"):
        final_text = "(singing)" + text

    final_style = style_instruction

    # VoiceDesign：音色描述合并到风格指令
    voice_design_text = data.get("voice_design_text", "")
    if model == "mimo-v2.5-tts-voicedesign":
        voice = ""
        if not voice_design_text:
            def err_gen():
                yield f"data: {json.dumps({'stage': 'error', 'progress': 0, 'message': 'VoiceDesign 模型需要填写音色描述'})}\n\n"
            return Response(err_gen(), mimetype="text/event-stream")
        final_style = voice_design_text
        if style_instruction:
            final_style += "\n\n" + style_instruction

    def generate():
        prog = ProgressEvent()

        def worker():
            try:
                # 检测是否需要分块
                chunk_limit = VOICECLONE_MAX_CHUNK_CHARS if (voice_audio_base64 or voice.startswith("cloned:")) else MAX_CHUNK_CHARS
                text_chunks = split_text_for_tts(final_text, max_chars=chunk_limit)
                total_chunks = len(text_chunks)

                if total_chunks == 1:
                    # 短文：单次合成
                    voice_info = {}
                    result_bytes, raw_format = call_mimo_tts(
                        model=model,
                        text=final_text,
                        voice=voice,
                        style_instruction=final_style,
                        audio_format="wav",
                        stream=stream,
                        voice_audio_base64=voice_audio_base64,
                        voice_audio_mime=voice_audio_mime,
                        progress=prog,
                        voice_info=voice_info,
                    )
                else:
                    # 长文：分块合成 + 拼接
                    voice_info = {}
                    prog.send("generating", 15, f"文本较长（{len(final_text)}字），自动分为 {total_chunks} 段依次合成...")

                    all_segments = []
                    sr = None

                    for idx, chunk_text in enumerate(text_chunks):
                        base_pct = 15 + int(65 * idx / total_chunks)
                        prog.send("generating", base_pct, f"合成第 {idx+1}/{total_chunks} 段（{len(chunk_text)}字）...")

                        chunk_bytes, _ = call_mimo_tts(
                            model=model,
                            text=chunk_text,
                            voice=voice,
                            style_instruction=final_style if idx == 0 else "",
                            audio_format="wav",
                            stream=False,
                            voice_audio_base64=voice_audio_base64,
                            voice_audio_mime=voice_audio_mime,
                            progress=None,
                            voice_info=(voice_info if idx == 0 else None),
                        )

                        import io as _cio
                        seg, srate = sf.read(_cio.BytesIO(chunk_bytes))
                        all_segments.append(seg)
                        sr = srate

                        if idx < total_chunks - 1:
                            silence = np.zeros(int(sr * SILENCE_SECONDS), dtype=np.float32)
                            all_segments.append(silence)

                    prog.send("processing", 80, f"拼接 {total_chunks} 段音频...")
                    combined = np.concatenate(all_segments)
                    import io as _cio
                    wav_buf = _cio.BytesIO()
                    sf.write(wav_buf, combined, sr, format='WAV')
                    result_bytes = wav_buf.getvalue()
                    raw_format = "wav"

                prog.send("processing", 85, "正在保存音频文件...")
                output_filename = f"tts_{uuid.uuid4().hex[:8]}"
                output_path = OUTPUT_DIR / f"{output_filename}.wav"
                output_path.write_bytes(result_bytes)

                if audio_format == "mp3":
                    prog.send("processing", 90, "正在转换 MP3 格式...")
                    mp3_bytes = convert_to_mp3(result_bytes)
                    mp3_path = OUTPUT_DIR / f"{output_filename}.mp3"
                    mp3_path.write_bytes(mp3_bytes)
                    output_path.unlink(missing_ok=True)
                    file_url = f"/output/{output_filename}.mp3"
                    final_fmt = "mp3"
                else:
                    file_url = f"/output/{output_filename}.wav"
                    final_fmt = "wav"

                # 保存克隆音色样本（用于后续复用）
                if model == "mimo-v2.5-tts-voiceclone" and "base64" in voice_info:
                    clone_id = cloned_voice_id or f"cv_{uuid.uuid4().hex[:8]}"
                    clone_dir = CLONED_VOICES_DIR / clone_id
                    clone_dir.mkdir(exist_ok=True)
                    try:
                        clone_b64 = voice_info["base64"]
                        clone_bytes = base64.b64decode(clone_b64)
                        (clone_dir / "sample.wav").write_bytes(clone_bytes)
                        duration_secs = len(clone_bytes) / 32000  # 16kHz mono 16bit -> 32KB/s
                        info_name = voice_name or (f"克隆音色 {clone_id[-6:]}")
                        info = {
                            "id": clone_id,
                            "name": info_name,
                            "original_filename": data.get("original_filename", ""),
                            "created_at": datetime.now().isoformat(),
                            "duration_secs": round(duration_secs, 1),
                        }
                        (clone_dir / "info.json").write_text(
                            json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                        save_cloned_voice(clone_id, info)
                        prog.send("processing", 86, f"克隆音色「{info_name}」已保存")
                    except Exception as _save_err:
                        prog.send("processing", 86, f"保存克隆音色失败: {_save_err}")

                save_history({
                    "id": output_filename,
                    "model": MODELS.get(model, {}).get("label", model),
                    "text": text[:100] + ("..." if len(text) > 100 else ""),
                    "voice": voice or "",
                    "style": style_instruction[:50] if style_instruction else "",
                    "file_url": file_url,
                    "format": final_fmt,
                    "created_at": datetime.now().isoformat(),
                })

                prog.send("complete", 100, "合成完成！",
                          file_url=file_url, format=final_fmt)
                prog.done()

            except Exception as e:
                traceback.print_exc()
                prog.error(str(e))

        t = threading.Thread(
            target=lambda: app.app_context().push() or worker(),
            daemon=True
        )
        t.start()

        for event in prog:
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------
# 非进度端点（保留兼容）
# ---------------------------------------------------------------
@app.route("/api/tts", methods=["POST"])
def api_tts():
    data = request.get_json(force=True)
    model = data.get("model", "mimo-v2.5-tts")
    text = data.get("text", "").strip()
    voice = data.get("voice", "")
    style_instruction = data.get("style_instruction", "")
    audio_format = data.get("audio_format", "mp3")
    stream = data.get("stream", False)

    if not text:
        return jsonify({"error": "请输入要合成的文本"}), 400
    if model not in MODELS:
        return jsonify({"error": f"不支持的模型: {model}"}), 400

    try:
        audio_bytes, raw_format = call_mimo_tts(
            model=model, text=text, voice=voice,
            style_instruction=style_instruction,
            audio_format="wav" if audio_format == "mp3" else "pcm16",
            stream=stream,
        )

        output_filename = f"tts_{uuid.uuid4().hex[:8]}"

        if audio_format == "mp3":
            mp3_bytes = convert_to_mp3(audio_bytes)
            output_path = OUTPUT_DIR / f"{output_filename}.mp3"
            output_path.write_bytes(mp3_bytes)
            file_url = f"/output/{output_filename}.mp3"
        else:
            output_path = OUTPUT_DIR / f"{output_filename}.wav"
            output_path.write_bytes(audio_bytes)
            file_url = f"/output/{output_filename}.wav"

        save_history({
            "id": output_filename,
            "model": MODELS.get(model, {}).get("label", model),
            "text": text[:100] + ("..." if len(text) > 100 else ""),
            "voice": voice,
            "style": style_instruction[:50] if style_instruction else "",
            "file_url": file_url,
            "format": audio_format,
            "created_at": datetime.now().isoformat(),
        })

        return jsonify({"success": True, "file_url": file_url, "format": audio_format})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tts/voiceclone", methods=["POST"])
def api_tts_voiceclone():
    if "audio_file" not in request.files:
        return jsonify({"error": "请上传音频样本文件"}), 400

    audio_file = request.files["audio_file"]
    text = request.form.get("text", "").strip()
    style_instruction = request.form.get("style_instruction", "")
    audio_format = request.form.get("audio_format", "mp3")
    voice_name = request.form.get("voice_name", "").strip()

    if not text:
        return jsonify({"error": "请输入要合成的文本"}), 400
    if not audio_file.filename:
        return jsonify({"error": "请选择音频文件"}), 400

    audio_bytes = audio_file.read()
    audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")
    filename = audio_file.filename.lower()
    mime_type = "audio/wav" if filename.endswith(".wav") else "audio/mpeg"

    try:
        voice_info = {}
        result_bytes, raw_format = call_mimo_tts(
            model="mimo-v2.5-tts-voiceclone", text=text, voice="",
            style_instruction=style_instruction, audio_format="wav",
            stream=False, voice_audio_base64=audio_base64,
            voice_audio_mime=mime_type,
            voice_info=voice_info,
        )

        output_filename = f"clone_{uuid.uuid4().hex[:8]}"
        if audio_format == "mp3":
            mp3_bytes = convert_to_mp3(result_bytes)
            output_path = OUTPUT_DIR / f"{output_filename}.mp3"
            output_path.write_bytes(mp3_bytes)
            file_url = f"/output/{output_filename}.mp3"
        else:
            output_path = OUTPUT_DIR / f"{output_filename}.wav"
            output_path.write_bytes(result_bytes)
            file_url = f"/output/{output_filename}.wav"

        save_history({
            "id": output_filename, "model": "MiMo-V2.5-TTS-VoiceClone",
            "text": text[:100] + ("..." if len(text) > 100 else ""),
            "voice": f"克隆自: {audio_file.filename}",
            "style": style_instruction[:50] if style_instruction else "",
            "file_url": file_url, "format": audio_format,
            "created_at": datetime.now().isoformat(),
        })

        # 保存克隆音色样本
        clone_id = f"cv_{uuid.uuid4().hex[:8]}"
        clone_dir = CLONED_VOICES_DIR / clone_id
        clone_dir.mkdir(exist_ok=True)
        try:
            clone_b64 = voice_info.get("base64", audio_base64)
            clone_bytes = base64.b64decode(clone_b64)
            (clone_dir / "sample.wav").write_bytes(clone_bytes)
            duration_secs = len(clone_bytes) / 32000
            info_name = voice_name or (os.path.splitext(audio_file.filename)[0] if audio_file.filename else f"克隆音色 {clone_id[-6:]}")
            info = {
                "id": clone_id,
                "name": info_name,
                "original_filename": audio_file.filename,
                "created_at": datetime.now().isoformat(),
                "duration_secs": round(duration_secs, 1),
            }
            (clone_dir / "info.json").write_text(
                json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            save_cloned_voice(clone_id, info)
        except Exception as _save_err:
            print(f"保存克隆音色失败: {_save_err}", flush=True)
        return jsonify({"success": True, "file_url": file_url, "format": audio_format})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------

# 克隆音色管理
# ---------------------------------------------------------------
@app.route("/api/cloned-voices", methods=["GET"])
def api_cloned_voices():
    """返回已克隆音色列表"""
    voices = load_cloned_voices()
    for v in voices:
        v["sample_url"] = f"/api/cloned-voices/{v['id']}/sample"
    return jsonify({"voices": voices})


@app.route("/api/cloned-voices/<voice_id>/sample", methods=["GET"])
def api_cloned_voice_sample(voice_id):
    """获取克隆音色样本音频供试听"""
    voice_dir = CLONED_VOICES_DIR / voice_id
    for ext, mime in [(".wav", "audio/wav"), (".mp3", "audio/mpeg")]:
        sample_path = voice_dir / f"sample{ext}"
        if sample_path.exists():
            return send_file(str(sample_path), mimetype=mime)
    return jsonify({"error": "克隆音色样本文件不存在"}), 404


@app.route("/api/cloned-voices/<voice_id>", methods=["DELETE"])
def api_delete_cloned_voice(voice_id):
    """删除克隆音色"""
    try:
        delete_cloned_voice(voice_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------
# 音色设计（Voice Design）管理
# ---------------------------------------------------------------
@app.route("/api/designed-voices", methods=["GET"])
def api_designed_voices():
    """返回已设计音色列表"""
    voices = load_designed_voices()
    for v in voices:
        v["sample_url"] = f"/api/designed-voices/{v['id']}/sample"
    return jsonify({"voices": voices})


@app.route("/api/designed-voices/<voice_id>/sample", methods=["GET"])
def api_designed_voice_sample(voice_id):
    """获取设计音色样本音频供试听"""
    voice_dir = DESIGNED_VOICES_DIR / voice_id
    for ext, mime in [(".wav", "audio/wav"), (".mp3", "audio/mpeg")]:
        sample_path = voice_dir / f"sample{ext}"
        if sample_path.exists():
            return send_file(str(sample_path), mimetype=mime)
    return jsonify({"error": "设计音色样本文件不存在"}), 404


@app.route("/api/designed-voices", methods=["POST"])
def api_save_designed_voice():
    """保存自设计音色"""
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    prompt = (data.get("prompt") or "").strip()
    tags = data.get("tags") or []
    sample_b64 = data.get("sample_base64")

    if not name:
        return jsonify({"error": "请输入音色名称"}), 400
    if not prompt:
        return jsonify({"error": "请输入音色描述 Prompt"}), 400

    voice_id = f"des_{uuid.uuid4().hex[:8]}"
    voice_dir = DESIGNED_VOICES_DIR / voice_id
    voice_dir.mkdir(parents=True, exist_ok=True)

    if sample_b64:
        try:
            (voice_dir / "sample.wav").write_bytes(base64.b64decode(sample_b64))
        except Exception as e:
            print(f"保存设计音色样本失败: {e}", flush=True)

    info = {
        "id": voice_id,
        "name": name,
        "prompt": prompt,
        "tags": tags,
        "created_at": datetime.now().isoformat(),
    }
    (voice_dir / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    save_designed_voice(voice_id, info)

    info["sample_url"] = f"/api/designed-voices/{voice_id}/sample"
    return jsonify({"success": True, "voice": info})


@app.route("/api/designed-voices/<voice_id>", methods=["DELETE"])
def api_delete_designed_voice(voice_id):
    """删除设计音色"""
    try:
        delete_designed_voice(voice_id)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tts/voicedesign/preview", methods=["POST"])
def api_voicedesign_preview():
    """在线试听设计音色"""
    data = request.get_json(force=True)
    prompt = (data.get("prompt") or "").strip()
    text = (data.get("text") or "").strip() or "你好，这是通过音色设计生成的专属人声试听。"
    if not prompt:
        return jsonify({"error": "请输入音色描述 Prompt"}), 400

    try:
        audio_bytes, _ = call_mimo_tts(
            model="mimo-v2.5-tts-voicedesign",
            text=text,
            style_instruction=prompt,
            audio_format="wav",
            stream=False,
        )
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        return jsonify({
            "success": True,
            "audio_base64": audio_b64,
            "format": "wav",
        })
    except Exception as e:
        return jsonify({"error": f"试听生成失败: {str(e)}"}), 500


# ---------------------------------------------------------------
# 历史记录
# ---------------------------------------------------------------
@app.route("/api/history", methods=["GET"])
def api_history():
    return jsonify({"history": load_history()})


@app.route("/api/history/<history_id>", methods=["DELETE"])
def api_delete_history(history_id):
    history = load_history()
    history = [h for h in history if h.get("id") != history_id]
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for ext in [".mp3", ".wav"]:
        p = OUTPUT_DIR / f"{history_id}{ext}"
        if p.exists():
            p.unlink()
    return jsonify({"success": True})


@app.route("/output/<filename>")
def serve_output(filename):
    filepath = OUTPUT_DIR / filename
    if not filepath.exists():
        return jsonify({"error": "文件不存在"}), 404
    mimetype = "audio/mpeg" if filename.endswith(".mp3") else "audio/wav"
    return send_file(str(filepath), mimetype=mimetype)


@app.route("/api/config", methods=["GET"])
def api_config():
    return jsonify({"models": MODELS, "has_api_key": bool(MIMO_API_KEY)})


@app.route("/api/voices", methods=["GET"])
def api_voices():
    model = request.args.get("model", "mimo-v2.5-tts")
    return jsonify({"voices": MODELS.get(model, {}).get("voices", {})})


# ---------------------------------------------------------------
# 诊断端点
# ---------------------------------------------------------------
@app.route("/api/diagnose", methods=["GET"])
def api_diagnose():
    import socket
    results = {}
    try:
        ip = socket.getaddrinfo("api.xiaomimimo.com", 443)
        results["dns"] = f"DNS OK: {ip[0][4][0]}"
    except Exception as e:
        results["dns"] = f"DNS fail: {e}"
    try:
        s = socket.create_connection(("api.xiaomimimo.com", 443), timeout=5)
        s.close()
        results["tcp"] = "TCP OK"
    except Exception as e:
        results["tcp"] = f"TCP fail: {e}"
    results["api_key_configured"] = bool(MIMO_API_KEY)
    results["api_key_prefix"] = (MIMO_API_KEY[:8] + "...") if MIMO_API_KEY else "not set"
    if MIMO_API_KEY:
        try:
            r = requests.get(
                f"{MIMO_API_BASE}/models",
                headers={"api-key": MIMO_API_KEY},
                timeout=(5, 10),
            )
            results["api_status"] = f"HTTP {r.status_code}"
        except Exception as e:
            results["api_status"] = f"API fail: {e}"
    else:
        results["api_status"] = "skipped (no key)"
    return jsonify(results)


# ============================================================
# 启动
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  小米 MiMo TTS 文字转语音 Web 应用")
    print("=" * 60)
    print(f"  API Base: {MIMO_API_BASE}")
    print(f"  API Key: {'已配置' if MIMO_API_KEY else '未配置（请在 .env 中设置 MIMO_API_KEY）'}")
    print(f"  输出目录: {OUTPUT_DIR.resolve()}")
    print(f"  启动地址: http://127.0.0.1:5000")
    print("=" * 60)
    app.run(host="127.0.0.1", port=5000, debug=True)
