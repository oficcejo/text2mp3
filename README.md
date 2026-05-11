# MiMo TTS / 文生视频工具

一个基于 Flask 的本地 Web 工具，当前支持两类能力：

- MiMo TTS 文本转语音
- 文章转漫画视频 MVP

项目适合本地使用、内网部署，或者作为你自己的 TTS / 文生视频工作台继续扩展。

## 功能说明

### 1. 语音合成

支持以下模型：

- `mimo-v2.5-tts`
- `mimo-v2.5-tts-voicedesign`
- `mimo-v2.5-tts-voiceclone`
- `mimo-v2-tts`

支持能力：

- 内置音色选择
- VoiceDesign 文字定义音色
- VoiceClone 音频样本克隆音色
- 风格指令
- 唱歌模式
- `mp3 / wav` 输出
- 长文本自动分段合成
- 合成历史记录

### 2. 克隆音色管理

支持上传 `mp3 / wav` 样本做克隆音色，并保存到本地。

支持：

- 克隆音色保存
- 在语音合成中复用克隆音色
- 在文生视频中复用克隆音色
- 删除已保存克隆音色

### 3. 文生视频 MVP

输入文章后，系统会：

1. 拆分分镜
2. 生成漫画图
3. 生成配音
4. 生成字幕
5. 合成视频

当前实现是“静态漫画视频”路线，不是动画视频。

#### 当前文生视频策略

- 分镜时长默认更细：
  - `SCENE_TARGET_SECONDS_MIN=12`
  - `SCENE_TARGET_SECONDS_MAX=18`
- 漫画图按“漫画组”复用：
  - 默认每 `2-3 分钟` 内容共用一张漫画图
  - 每组至少 `10` 个分镜
  - 每组生成一张“多格漫画”
  - 组内各 scene 分别保留自己的字幕、配音和时长

这套策略的目标是节约生图成本，而不是每个 scene 都单独生图。

#### 当前兜底行为

为了保证流程能跑通，当前代码带有兜底逻辑：

- OpenAI 兼容生图接口未配置时：
  - 生成占位漫画图
- MiMo API 不可用时：
  - 生成静音 WAV，保持字幕和视频链路可继续执行
- 系统未检测到 `ffmpeg` 时：
  - 保留分镜图、音频、字幕，不输出最终 `mp4`

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
│   └── index.html
├── static/
│   ├── css/style.css
│   └── js/main.js
├── output/
├── cloned_voices/
├── jobs/
└── history.json
```

### 关键目录说明

- `output/`
  - TTS 输出音频
- `cloned_voices/`
  - 克隆音色样本与索引
- `jobs/`
  - 文生视频任务目录
- `history.json`
  - 语音合成历史

### 文生视频任务目录

```text
jobs/job_xxx/
├── manifest.json
├── storyboard.json
├── images/
├── frames/
├── audio/
├── subtitles/
├── video/
└── video_parts/
```

## 环境变量

参考 `.env.example`。

### MiMo TTS

```env
MIMO_API_KEY=
```

### OpenAI 兼容文本 / 生图接口

可对接官方 OpenAI，也可对接第三方中转。

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
IMAGE_GROUP_SECONDS_MIN=120
IMAGE_GROUP_SECONDS_MAX=180
IMAGE_GROUP_MIN_SCENES=10
FFMPEG_BIN=
```

## 部署说明

## 一、本地开发部署

### 1. Python 环境

建议：

- Python `3.11`
- Windows / Linux 都可

安装依赖：

```bash
pip install -r requirements.txt
```

### 2. 配置 `.env`

复制：

```bash
cp .env.example .env
```

然后填写至少以下内容：

- `MIMO_API_KEY`
- `OPENAI_API_KEY`（如果要真实生图）
- `OPENAI_BASE_URL`（如果你使用第三方中转）

### 3. 安装 ffmpeg

文生视频最终输出 `mp4` 依赖 `ffmpeg`。

如果系统里没有 `ffmpeg`：

- 页面仍可创建视频任务
- 仍可生成分镜、字幕、音频
- 但不会产出最终视频文件

Windows 下建议：

- 把 `ffmpeg.exe` 加入 PATH
- 或者在 `.env` 中设置：

```env
FFMPEG_BIN=C:\path\to\ffmpeg.exe
```

### 4. 启动服务

开发启动：

```bash
python app.py
```

默认地址：

```text
http://127.0.0.1:5000
```

如果你在当前项目里用的是虚拟环境：

```powershell
.\venv\Scripts\python.exe app.py
```

如果你发现后台调试启动不稳定，可以直接用：

```powershell
.\venv\Scripts\python.exe -c "from app import app; app.run(host='127.0.0.1', port=5000, debug=False)"
```

## 二、生产部署建议

当前项目是 Flask 单体应用，推荐这样部署：

- Gunicorn / Waitress 作为 WSGI 服务
- Nginx / Caddy 做反向代理
- `ffmpeg` 作为系统依赖安装

如果要长期对外提供文生视频能力，建议至少补这些内容：

- 任务队列
- 鉴权
- 失败重试
- 持久化数据库
- 对 `jobs/` 做清理策略

## 三、Docker 部署说明

项目当前已经带了：

- `Dockerfile`
- `docker-compose.yml`

但要注意：

1. 现有 Docker 配置更偏向原始 TTS 功能
2. 如果你要完整使用文生视频：
   - 需要容器里有 `ffmpeg`
   - 需要确保 OpenAI / MiMo 网络可访问

### 构建

```bash
docker build -t text2mp3 .
```

### 运行

```bash
docker run --rm -p 5000:5000 --env-file .env text2mp3
```

### Compose

```bash
docker compose up --build
```

### Docker 注意事项

当前 README 必须说明这件事：

- 如果镜像里没有 `ffmpeg`
- 文生视频最终 MP4 输出就不会完整可用

所以如果你准备在 Docker 里跑文生视频，建议自行补 `ffmpeg` 到镜像。

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

## 文生视频预览说明

当前“分镜预览”已经不是逐 scene 平铺，而是按“漫画组”展示：

- 每组只展示一张组图
- 组内列出多个分镜条目
- 更符合“同一张多格漫画复用 2-3 分钟内容”的策略

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

## 已知限制

- 当前视频链路仍是 MVP
- 漫画图是静态图，不是动画
- 角色一致性主要依赖 prompt，未做角色锁定系统
- Docker 默认配置还不算完整的视频生产环境
- 当前项目仍偏单体结构，后续最好把 `video_mvp.py` 再拆分

## 常见问题

### 1. 页面能创建视频任务，但没有 mp4

先检查：

- 是否安装了 `ffmpeg`
- `FFMPEG_BIN` 是否正确

### 2. 视频有分镜和字幕，但没有真实配音

说明 MiMo API 没有通，系统走了静音兜底。

检查：

- `MIMO_API_KEY`
- 网络是否可访问 `https://api.xiaomimimo.com`

### 3. 视频有占位图，没有真实漫画图

说明 OpenAI 兼容生图接口未配置或不可访问。

检查：

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL`
- `OPENAI_IMAGE_MODEL`

### 4. 文生视频里看不到克隆音色

刷新页面后重试。  
当前代码已经支持在文生视频页复用克隆音色。

## 广告 / 资源推荐

如果你需要低价稳定的主流 AI 大模型 API，可查看：

**低价稳定无套路主流 AI 大模型，国内直连 API**

- minimax2.7 百万 token `0.2 元`
- gpt5.5 百万 token `2 元`
- gpt-image-2 生图 `1 张 0.1 元`

链接：

https://llm-token.cn/r/INVE8242B72

