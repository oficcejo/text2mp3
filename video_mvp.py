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
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import soundfile as sf
from flask import jsonify, request, send_file


def register_video_mvp_routes(app, deps: Dict[str, Any]):
    jobs_dir = Path(os.getenv("JOBS_DIR", "jobs"))
    jobs_dir.mkdir(exist_ok=True)

    output_dir = Path(deps["output_dir"])
    get_cloned_voice_sample = deps["get_cloned_voice_sample"]
    call_mimo_tts = deps["call_mimo_tts"]

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
            "image_group_seconds_min": int(os.getenv("IMAGE_GROUP_SECONDS_MIN", "90")),
            "image_group_seconds_max": int(os.getenv("IMAGE_GROUP_SECONDS_MAX", "150")),
            "image_group_min_scenes": int(os.getenv("IMAGE_GROUP_MIN_SCENES", "6")),
        }

    def resolve_image_group_settings(settings: Dict[str, Any], density: str) -> Dict[str, Any]:
        resolved = dict(settings)
        density = (density or 'balanced').strip().lower()
        if density == 'more':
            resolved['image_group_seconds_min'] = 60
            resolved['image_group_seconds_max'] = 100
            resolved['image_group_min_scenes'] = 4
        elif density == 'fewer':
            resolved['image_group_seconds_min'] = max(settings['image_group_seconds_min'], 120)
            resolved['image_group_seconds_max'] = max(settings['image_group_seconds_max'], 180)
            resolved['image_group_min_scenes'] = max(settings['image_group_min_scenes'], 8)
        else:
            resolved['image_group_seconds_min'] = settings['image_group_seconds_min']
            resolved['image_group_seconds_max'] = settings['image_group_seconds_max']
            resolved['image_group_min_scenes'] = settings['image_group_min_scenes']
        return resolved

    def ffmpeg_binary() -> Optional[str]:
        configured = os.getenv("FFMPEG_BIN", "").strip()
        if configured and Path(configured).exists():
            return configured
        return shutil.which("ffmpeg")

    def split_sentences(text: str) -> List[str]:
        parts = re.split(r"(?<=[。！？!?\.])\s*", text.strip())
        return [part.strip() for part in parts if part.strip()]

    def estimate_duration_sec(text: str) -> float:
        estimated = max(4.0, len(text.strip()) / 4.6)
        return round(estimated, 1)

    def chunk_text_by_duration(text: str, min_sec: int, max_sec: int, max_count: int) -> List[str]:
        text = re.sub(r"\r\n?", "\n", text).strip()
        if not text:
            return []

        target_chars = max(90, int(((min_sec + max_sec) / 2) * 4.6))
        max_chars = max(target_chars + 60, int(max_sec * 5.2))
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
        chunks: List[str] = []
        current = ""

        def flush() -> None:
            nonlocal current
            if current.strip():
                chunks.append(current.strip())
                current = ""

        for para in paragraphs:
            sentences = split_sentences(para) or [para]
            for sentence in sentences:
                candidate = (current + "\n" + sentence).strip() if current else sentence
                if len(candidate) <= max_chars:
                    current = candidate
                    if len(current) >= target_chars:
                        flush()
                else:
                    if current:
                        flush()
                    if len(sentence) <= max_chars:
                        current = sentence
                    else:
                        for i in range(0, len(sentence), max_chars):
                            chunks.append(sentence[i:i + max_chars].strip())
            if len(chunks) >= max_count:
                break

        if len(chunks) < max_count:
            flush()

        if len(chunks) > max_count:
            tail = chunks[max_count - 1:]
            chunks = chunks[:max_count - 1] + ["\n".join(tail)]

        return [chunk for chunk in chunks if chunk]

    def coerce_storyboard(raw_data: Dict[str, Any], text: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        scenes = raw_data.get("scenes") if isinstance(raw_data, dict) else None
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("planner returned no scenes")

        normalized_scenes = []
        for idx, scene in enumerate(scenes, start=1):
            if not isinstance(scene, dict):
                continue
            narration = str(scene.get("narration_text") or scene.get("narration") or "").strip()
            subtitle = str(scene.get("subtitle_text") or narration).strip()
            image_prompt = str(scene.get("image_prompt") or scene.get("visual_prompt") or "").strip()
            if not narration:
                continue
            if subtitle and len(subtitle) < len(narration) * 0.65:
                subtitle = narration
            if not image_prompt:
                image_prompt = narration[:220]
            duration = scene.get("estimated_duration_sec") or estimate_duration_sec(narration)
            try:
                duration = float(duration)
            except Exception:
                duration = estimate_duration_sec(narration)
            normalized_scenes.append({
                "scene_index": idx,
                "title": str(scene.get("title") or f"Scene {idx}").strip(),
                "source_excerpt": narration[:120].strip(),
                "narration_text": narration,
                "subtitle_text": subtitle,
                "image_prompt": image_prompt,
                "estimated_duration_sec": max(4.0, min(duration, settings["scene_max_sec"] * 1.4)),
                "image_file": None,
                "frame_file": None,
                "audio_file": None,
                "start_sec": None,
                "end_sec": None,
            })

        if not normalized_scenes:
            raise ValueError("planner scenes invalid")

        return {
            "title": str(raw_data.get("title") or split_sentences(text)[0][:40] or "????").strip(),
            "style": str(raw_data.get("style") or settings["style"]).strip() or settings["style"],
            "aspect_ratio": settings["aspect_ratio"],
            "total_estimated_duration_sec": round(sum(scene["estimated_duration_sec"] for scene in normalized_scenes), 1),
            "scenes": normalized_scenes,
        }

    def build_storyboard_fallback(text: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        chunks = chunk_text_by_duration(
            text=text,
            min_sec=settings["scene_min_sec"],
            max_sec=settings["scene_max_sec"],
            max_count=settings["scene_max_count"],
        )
        scenes = []
        for idx, chunk in enumerate(chunks, start=1):
            normalized = re.sub(r"[ 	]+", " ", chunk).strip()
            scenes.append({
                "scene_index": idx,
                "title": f"?? {idx}",
                "source_excerpt": normalized[:120],
                "narration_text": chunk,
                "subtitle_text": normalized,
                "image_prompt": (
                    "Chinese comic storyboard, cinematic composition, clear subject, no speech bubbles, no watermark, suitable for video."
                    f" Scene content: {normalized[:220]}"
                ),
                "estimated_duration_sec": estimate_duration_sec(chunk),
                "image_file": None,
                "frame_file": None,
                "audio_file": None,
                "start_sec": None,
                "end_sec": None,
            })

        return {
            "title": split_sentences(text)[0][:40] if split_sentences(text) else "????",
            "style": settings["style"],
            "aspect_ratio": settings["aspect_ratio"],
            "total_estimated_duration_sec": round(sum(scene["estimated_duration_sec"] for scene in scenes), 1),
            "scenes": scenes,
        }

    def planner_prompt_payload(text: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "model": get_openai_settings()["text_model"],
            "temperature": 0.3,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是中文漫画视频分镜导演。"
                        "请把文章拆成适合静态漫画视频的分镜，并且只返回 JSON。"
                        "返回格式必须是一个 JSON 对象，包含 title、style、scenes。"
                        "scenes 是数组，每项包含 title、source_excerpt、narration_text、subtitle_text、image_prompt、estimated_duration_sec。"
                        "image_prompt 要适合中文漫画分镜生图，不要包含对白气泡和水印。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"风格：{settings['style']}\n"
                        f"每个分镜建议时长：{settings['scene_min_sec']}-{settings['scene_max_sec']} 秒\n"
                        f"最多分镜数：{settings['scene_max_count']}\n"
                        "文章如下：\n"
                        f"{text}"
                    ),
                },
            ],
        }

    def extract_first_json_block(text: str) -> Dict[str, Any]:
        text = text.strip()
        if text.startswith("{") and text.endswith("}"):
            return json.loads(text)

        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise ValueError("no json block found")

    def try_plan_storyboard_with_llm(text: str, settings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        openai_settings = get_openai_settings()
        if not openai_settings["api_key"]:
            return None

        payload = planner_prompt_payload(text, settings)
        headers = {
            "Authorization": f"Bearer {openai_settings['api_key']}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.post(
                f"{openai_settings['base_url']}/chat/completions",
                headers=headers,
                json=payload,
                timeout=(15, openai_settings["timeout"]),
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            raw_storyboard = extract_first_json_block(str(content))
            return coerce_storyboard(raw_storyboard, text, settings)
        except Exception:
            return None

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
        del title, prompt
        write_simple_png(output_path, width, height, (232, 235, 240))


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
            if groups and len(current) < max(3, min_scenes // 2):
                groups[-1].extend(current)
            else:
                groups.append(current)
        return groups

    def build_group_prompt(group_index: int, scenes: List[Dict[str, Any]], style: str) -> str:
        prompt_lines = [
            'Chinese comic storyboard page, multi-panel comic layout, suitable for 16:9 video, cinematic composition, clear subject, no speech bubbles, no watermark.',
            f'Overall visual style: {style}.',
            f'This is storyboard group {group_index}. Draw all of the following scenes on one single comic page, with one panel per scene and consistent characters across panels.',
        ]
        for idx, scene in enumerate(scenes, start=1):
            content = scene.get("image_prompt") or scene.get("subtitle_text") or scene.get("title") or "scene"
            prompt_lines.append(f'Panel {idx}: {content}')
        return "\n".join(prompt_lines)

    def fetch_image_bytes_from_openai(prompt: str) -> Optional[bytes]:
        openai_settings = get_openai_settings()
        if not openai_settings["api_key"]:
            return None

        headers = {
            "Authorization": f"Bearer {openai_settings['api_key']}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": openai_settings["image_model"],
            "prompt": prompt,
            "size": openai_settings["image_size"],
            "quality": openai_settings["image_quality"],
            "n": 1,
        }
        try:
            response = requests.post(
                f"{openai_settings['base_url']}/images/generations",
                headers=headers,
                json=payload,
                timeout=(20, 240),
            )
            response.raise_for_status()
            data = response.json()
            item = (data.get("data") or [{}])[0]
            if item.get("b64_json"):
                return base64.b64decode(item["b64_json"])
            if item.get("url"):
                image_response = requests.get(item["url"], timeout=(15, 180))
                image_response.raise_for_status()
                return image_response.content
        except Exception:
            return None
        return None

    def compose_video_frame(
        source_image_path: Path,
        frame_path: Path,
        subtitle_text: str,
        title: str,
        width: int,
        height: int,
    ) -> None:
        del subtitle_text, title, width, height
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        if source_image_path.suffix.lower() == ".png":
            shutil.copyfile(source_image_path, frame_path)
        else:
            write_simple_png(frame_path, 1280, 720, (224, 228, 233))

    def generate_group_image(group_index: int, scenes: List[Dict[str, Any]], job_dir: Path, settings: Dict[str, Any]) -> str:
        width = settings["width"]
        height = settings["height"]
        image_path = job_dir / "images" / f"group_{group_index:03d}.png"
        prompt = build_group_prompt(group_index, scenes, settings["style"])
        image_bytes = fetch_image_bytes_from_openai(prompt)
        if image_bytes:
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(image_bytes)
        else:
            render_placeholder_panel(
                output_path=image_path,
                title=f"漫画组 {group_index}",
                prompt=prompt,
                width=width,
                height=max(720, int(height * 0.82)),
            )
        return str(image_path.relative_to(job_dir)).replace("\\", "/")

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
        image_path = job_dir / "images" / f"scene_{scene['scene_index']:03d}.png"
        frame_path = job_dir / "frames" / f"scene_{scene['scene_index']:03d}.png"
        image_bytes = fetch_image_bytes_from_openai(scene["image_prompt"])
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
        if voice.startswith("cloned:"):
            clone_id = voice.split(":", 1)[1]
            sample_bytes = get_cloned_voice_sample(clone_id)
            return (
                "mimo-v2.5-tts-voiceclone",
                "",
                base64.b64encode(sample_bytes).decode("utf-8"),
            )
        return "mimo-v2.5-tts", voice or "mimo_default", ""

    def generate_scene_audio(scene: Dict[str, Any], job_dir: Path, voice: str) -> Tuple[str, float, Optional[str]]:
        audio_path = job_dir / "audio" / f"scene_{scene['scene_index']:03d}.wav"
        warning = None
        model, voice_name, voice_sample_b64 = build_voice_payload(voice)

        try:
            audio_bytes, _ = call_mimo_tts(
                model=model,
                text=scene["narration_text"],
                voice=voice_name,
                style_instruction="",
                audio_format="wav",
                stream=False,
                voice_audio_base64=voice_sample_b64,
                voice_audio_mime="audio/wav",
                progress=None,
                voice_info=None,
            )
            audio_path.parent.mkdir(parents=True, exist_ok=True)
            audio_path.write_bytes(audio_bytes)
        except Exception as exc:
            warning = f"TTS fallback used: {exc}"
            write_silence_wav(audio_path, scene["estimated_duration_sec"])

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
            for scene in storyboard["scenes"]:
                frame_path = job_dir / scene["frame_file"]
                audio_path = job_dir / scene["audio_file"]
                part_path = parts_dir / f"scene_{scene['scene_index']:03d}.mp4"
                duration = max(1.0, float(scene.get("actual_duration_sec") or scene["estimated_duration_sec"]))
                cmd = [
                    ffmpeg,
                    "-y",
                    "-loop",
                    "1",
                    "-i",
                    str(frame_path),
                    "-i",
                    str(audio_path),
                    "-t",
                    f"{duration:.2f}",
                    "-r",
                    str(settings["fps"]),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(part_path),
                ]
                subprocess.run(cmd, check=True, capture_output=True)
                part_paths.append(part_path)

            concat_lines = []
            for part in part_paths:
                part_abs = str(part.resolve()).replace("\\", "/")
                concat_lines.append(f"file '{part_abs}'")
            concat_file.write_text("\n".join(concat_lines), encoding="utf-8")
            final_path = job_dir / "video" / "final.mp4"

            cmd = [
                ffmpeg,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c",
                "copy",
                str(final_path),
            ]
            result = subprocess.run(cmd, capture_output=True)
            if result.returncode != 0:
                cmd = [
                    ffmpeg,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file),
                    "-c:v",
                    "libx264",
                    "-c:a",
                    "aac",
                    str(final_path),
                ]
                subprocess.run(cmd, check=True, capture_output=True)

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

    def job_response(job_id: str) -> Dict[str, Any]:
        job_dir = jobs_dir / job_id
        manifest = json_read(job_dir / "manifest.json", {})
        storyboard = json_read(job_dir / "storyboard.json", {})
        scenes = [scene_urls(job_id, scene) for scene in storyboard.get("scenes", [])]
        outputs = manifest.get("outputs", {})
        return {
            "job_id": job_id,
            "status": manifest.get("status"),
            "progress": manifest.get("progress", 0),
            "error": manifest.get("error"),
            "warnings": manifest.get("warnings", []),
            "created_at": manifest.get("created_at"),
            "updated_at": manifest.get("updated_at"),
            "config": manifest.get("config", {}),
            "title": storyboard.get("title"),
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
        planned = try_plan_storyboard_with_llm(text, settings)
        if planned:
            return planned
        return build_storyboard_fallback(text, settings)

    def run_job(job_id: str) -> None:
        job_dir = jobs_dir / job_id
        manifest_path = job_dir / "manifest.json"
        manifest = json_read(manifest_path, {})
        config = manifest.get("config", {})
        settings = resolve_image_group_settings(get_video_settings(), config.get('image_density', 'balanced'))
        warnings = list(manifest.get("warnings", []))

        try:
            update_manifest(job_dir, status="planning", progress=8, error=None)
            storyboard = build_storyboard(manifest["source_text"], settings)
            json_write(job_dir / "storyboard.json", storyboard)
            update_manifest(job_dir, status="generating_assets", progress=20)

            total = max(1, len(storyboard["scenes"]))
            image_groups = build_image_groups(storyboard["scenes"], settings)
            group_map = {}
            for group_index, group_scenes in enumerate(image_groups, start=1):
                image_file = generate_group_image(group_index, group_scenes, job_dir, settings)
                for scene in group_scenes:
                    scene["image_group_index"] = group_index
                    scene["image_file"] = image_file
                    group_map[scene["scene_index"]] = image_file

            cursor = 0.0
            for idx, scene in enumerate(storyboard["scenes"], start=1):
                scene["frame_file"] = generate_scene_frame(scene, group_map[scene["scene_index"]], job_dir, settings)

                audio_file, actual_duration_sec, warning = generate_scene_audio(
                    scene=scene,
                    job_dir=job_dir,
                    voice=config.get("voice", settings["voice"]),
                )
                scene["audio_file"] = audio_file
                scene["actual_duration_sec"] = actual_duration_sec
                scene["start_sec"] = round(cursor, 2)
                cursor += actual_duration_sec
                scene["end_sec"] = round(cursor, 2)
                if warning:
                    warnings.append(f"Scene {idx}: {warning}")

                progress = 20 + int(55 * idx / total)
                update_manifest(
                    job_dir,
                    status="generating_assets",
                    progress=progress,
                    warnings=warnings[-20:],
                )
                json_write(job_dir / "storyboard.json", storyboard)

            storyboard["total_estimated_duration_sec"] = round(cursor, 1)
            json_write(job_dir / "storyboard.json", storyboard)

            update_manifest(job_dir, status="building_subtitles", progress=80, warnings=warnings[-20:])
            subtitle_file = job_dir / "subtitles" / "subtitles.srt"
            build_srt(storyboard, subtitle_file)

            update_manifest(job_dir, status="rendering_video", progress=88, warnings=warnings[-20:])
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
                outputs=outputs,
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
        for job_dir in sorted(jobs_dir.glob("job_*"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            items.append(job_response(job_dir.name))
        return jsonify({"jobs": items})

    @app.route("/api/video/jobs", methods=["POST"])
    def api_create_video_job():
        data = request.get_json(force=True)
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"error": "请输入文章内容"}), 400

        settings = get_video_settings()
        job_id = f"job_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        job_dir = jobs_dir / job_id
        for folder in ["images", "frames", "audio", "subtitles", "video", "video_parts"]:
            (job_dir / folder).mkdir(parents=True, exist_ok=True)

        manifest = {
            "job_id": job_id,
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

    @app.route("/api/video/jobs/<job_id>", methods=["GET"])
    def api_video_job(job_id: str):
        job_dir = jobs_dir / job_id
        if not job_dir.exists():
            return jsonify({"error": "job not found"}), 404
        return jsonify({"job": job_response(job_id)})

    @app.route("/api/video/jobs/<job_id>/storyboard", methods=["GET"])
    def api_video_storyboard(job_id: str):
        job_dir = jobs_dir / job_id
        storyboard_path = job_dir / "storyboard.json"
        if not storyboard_path.exists():
            return jsonify({"error": "storyboard not ready"}), 404
        return jsonify(json_read(storyboard_path, {}))

    @app.route("/video-jobs/<job_id>/<path:filename>", methods=["GET"])
    def serve_video_job_asset(job_id: str, filename: str):
        job_dir = jobs_dir / job_id
        requested = (job_dir / filename).resolve()
        job_root = job_dir.resolve()
        if not str(requested).startswith(str(job_root)) or not requested.exists():
            return jsonify({"error": "file not found"}), 404
        return send_file(str(requested))
