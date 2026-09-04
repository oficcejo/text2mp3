# MiMo TTS / 文生视频工具

<p align="left">
  <a href="https://github.com/oficcejo/text2mp3"><img src="https://img.shields.io/github/stars/oficcejo/text2mp3?style=social" alt="GitHub stars"></a>
  <a href="https://github.com/oficcejo/text2mp3"><img src="https://img.shields.io/github/forks/oficcejo/text2mp3?style=social" alt="GitHub forks"></a>
  <a href="https://space.bilibili.com/1468337275"><img src="https://img.shields.io/badge/Bilibili-关注作者UP-fb7299?logo=bilibili&logoColor=white" alt="Bilibili"></a>
  <a href="https://github.com/oficcejo/text2mp3/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white" alt="Python">
</p>

> 🌟 **欢迎关注与支持作者！**
> 
> 📺 **作者 B站主页**：[https://space.bilibili.com/1468337275](https://space.bilibili.com/1468337275) （获取最新 AI 教程、音视频实战与工具玩法，欢迎关注支持！）  
> 🔗 **GitHub 开源仓库**：[https://github.com/oficcejo/text2mp3](https://github.com/oficcejo/text2mp3) （如果觉得好用，请点击右上角 **Star ⭐️** 鼓励支持！）

一个基于 Flask 的本地 Web 工具，支持：

- 小米 MiMo TTS 官方全套音色（中文、英文）
- 专属音色设计（Voice Design）工作台
- 声音克隆（Voice Clone）与音色库管理
- 文章转漫画视频 MVP（工程持久化、分镜编辑、一键极速换配音）

## 功能说明

### 语音合成

支持模型：

- `mimo-v2.5-tts`
- `mimo-v2.5-tts-voicedesign`
- `mimo-v2.5-tts-voiceclone`
- `mimo-v2-tts`

支持：

- 内置音色选择
- VoiceDesign 音色描述
- VoiceClone 音频样本克隆
- 风格指令
- 唱歌模式
- `mp3 / wav` 输出
- 长文本自动分段
- 历史记录

### 克隆音色管理

支持上传 `mp3 / wav` 音频样本并保存到本地。

可用于：

- 语音合成
- 文生视频配音

### 文生视频 MVP

输入文章后，系统会：

1. 拆分分镜
2. 生成漫画图
3. 生成配音
4. 生成字幕
5. 合成视频

当前实现是“静态漫画视频”。

#### 当前策略

- 分镜时长默认更细：`12-18` 秒
- 漫画图按“漫画组”复用
- 默认每 `2-3` 分钟内容共用一张漫画图
- 每组至少 `10` 个分镜
- 每组生成一张多格漫画
- 组内各 scene 保留各自字幕、配音和时长

#### 兜底行为

- OpenAI 兼容生图接口未配置时，生成占位图
- MiMo API 不可用时，生成静音 WAV
- 系统未检测到 `ffmpeg` 时，不输出最终 `mp4`

## 项目结构

```text
.
├── app.py
├── video_mvp.py
├── requirements.txt
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── templates/
├── static/
├── output/
├── cloned_voices/
├── jobs/
└── history.json
```

## 环境变量

参考 `.env.example`。

### MiMo TTS目前免费，但至少冲上10元吧，获取apikey

```env
MIMO_API_KEY=
```

### OpenAI 兼容文本 / 生图接口

```env
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_TEXT_MODEL=gpt-4.1
OPENAI_IMAGE_MODEL=gpt-image-2
OPENAI_IMAGE_SIZE=1024x1024
OPENAI_IMAGE_QUALITY=medium
OPENAI_TEXT_TIMEOUT=180
```

### 文生视频参数

```env
JOBS_DIR=jobs
VIDEO_DEFAULT_VOICE=mimo_default
SCENE_TARGET_SECONDS_MIN=12
SCENE_TARGET_SECONDS_MAX=18
SCENE_MAX_COUNT=24
SCENE_STYLE=comic
VIDEO_ASPECT_RATIO=16:9
VIDEO_WIDTH=1280
VIDEO_HEIGHT=720
VIDEO_FPS=24
IMAGE_GROUP_SECONDS_MIN=90
IMAGE_GROUP_SECONDS_MAX=150
IMAGE_GROUP_MIN_SCENES=6
FFMPEG_BIN=
```

## 部署说明

### 本地部署

1. 安装依赖

```bash
pip install -r requirements.txt
```

2. 配置 `.env`

至少填写：

- `MIMO_API_KEY`
- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`

3. 启动服务

```bash
python app.py
```

默认地址：

```text
http://127.0.0.1:5000
```

### ffmpeg

文生视频最终输出 `mp4` 依赖 `ffmpeg`。

如果本机没有 `ffmpeg`：

- 仍可创建视频任务
- 仍可生成分镜、字幕、音频
- 但不会输出最终视频文件

### Docker 部署

Docker 镜像已内置：

- `ffmpeg`
- `gunicorn`

构建：

```bash
docker build -t text2mp3 .
```

运行：

```bash
docker run --rm -p 5000:5000 --env-file .env text2mp3
```

Compose：

```bash
docker compose up --build
```

建议挂载：

- `./output:/app/output`
- `./cloned_voices:/app/cloned_voices`
- `./history.json:/app/history.json`
- `./jobs:/app/jobs`

## 页面说明

当前页面包含：

- 语音合成
- 文生视频
- 声音克隆
- 历史记录

### 文生视频页

支持：

- 输入文章
- 选择配音音色
  - 包括内置音色
  - 包括克隆音色
- 选择画面风格
- 查看任务进度
- 查看按“漫画组”展示的预览
- 下载字幕
- 下载最终视频

## 接口概览

### 原有 TTS

- `GET /`
- `POST /api/tts/progress`
- `POST /api/tts`
- `POST /api/tts/voiceclone`
- `GET /api/cloned-voices`
- `DELETE /api/cloned-voices/<voice_id>`
- `GET /api/history`
- `DELETE /api/history/<history_id>`
- `GET /api/voices`
- `GET /api/config`
- `GET /api/diagnose`

### 文生视频

- `GET /api/video/config`
- `GET /api/video/jobs`
- `POST /api/video/jobs`
- `GET /api/video/jobs/<job_id>`
- `GET /api/video/jobs/<job_id>/storyboard`
- `GET /video-jobs/<job_id>/<path:filename>`

## 广告 / 资源推荐

低价稳定无套路主流 AI 大模型，国内直连 API：

- minimax2.7 百万 token 0.2 元
- gpt5.5 百万 token 2 元
- gpt-image-2 生图 1 张 0.1 元

链接：

https://llm-token.cn/r/INVE8242B72

