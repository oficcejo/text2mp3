# MiMo TTS Web

一个基于 Flask 的小米 MiMo TTS Web 工具，提供文本转语音、音色设计、音色克隆、历史记录和克隆音色管理能力。

项目当前是单文件后端 `app.py` + 原生前端页面，适合本地自用、内网部署或作为 MiMo TTS 的轻量封装。

## 功能概览

- 文本转语音
  - 支持 `mimo-v2.5-tts`
  - 支持 `mimo-v2-tts`
- 音色设计
  - 支持 `mimo-v2.5-tts-voicedesign`
  - 通过文字描述生成音色
- 音色克隆
  - 支持上传 `MP3/WAV` 样本
  - 自动保存克隆音色，后续可复用
- 长文本处理
  - 普通 TTS 默认按 `4500` 字符分段
  - 音色克隆模式默认按 `3000` 字符分段
  - 分段结果自动拼接，段间插入 `0.3s` 静音
- 历史记录
  - 默认保留最近 `50` 条
  - 支持删除历史及对应音频文件
- 实时进度
  - 通过 SSE 风格流式响应回传准备、连接、生成、处理、完成等阶段
- 诊断接口
  - 检查 API Key、DNS、TCP 连接和模型接口可达性

## 技术栈

- 后端：Flask
- 前端：HTML + CSS + 原生 JavaScript
- HTTP：requests
- 音频处理：numpy、soundfile、pydub
- 配置：python-dotenv
- 部署：Gunicorn + Docker / Docker Compose

## 目录结构

```text
.
├── app.py                  # Flask 服务入口与全部后端逻辑
├── requirements.txt        # Python 依赖
├── Dockerfile              # 容器镜像构建
├── docker-compose.yml      # 容器编排
├── templates/
│   └── index.html          # 页面模板
├── static/
│   ├── css/style.css       # 样式
│   └── js/main.js          # 前端交互逻辑
├── output/                 # 生成音频输出目录
├── cloned_voices/          # 克隆音色样本及索引
└── history.json            # 历史记录
```

## 运行前准备

### 1. 申请 MiMo API Key

在小米 MiMo 平台创建 API Key，然后写入 `.env`：

```env
MIMO_API_KEY=你的API密钥
```

可直接复制 `.env.example` 为 `.env` 后再修改。

### 2. Python 依赖

推荐 Python `3.11`。

安装依赖：

```bash
pip install -r requirements.txt
```

### 3. 系统音频依赖

项目使用 `pydub` 处理音频：

- 上传 `MP3` 样本时，通常需要系统可用的 `ffmpeg`
- 导出 `MP3` 时，也通常依赖 `ffmpeg`
- `soundfile` 运行时需要 `libsndfile`

如果你只使用 `WAV` 输入和输出，外部依赖会少一些；如果要稳定支持 `MP3`，建议明确安装 `ffmpeg`。

## 本地启动

```bash
python app.py
```

默认地址：

```text
http://127.0.0.1:5000
```

开发模式下，服务会：

- 自动创建 `output/`
- 自动创建 `cloned_voices/`
- 读取 `.env` 中的 `MIMO_API_KEY`

## Docker 启动

### 方式一：直接构建运行

```bash
docker build -t text2mp3 .
docker run --rm -p 5000:5000 --env-file .env text2mp3
```

### 方式二：Docker Compose

```bash
docker compose up --build
```

`docker-compose.yml` 已挂载以下目录和文件用于持久化：

- `./output -> /app/output`
- `./cloned_voices -> /app/cloned_voices`
- `./history.json -> /app/history.json`

### Docker 注意事项

当前 `Dockerfile` 安装了 `libsndfile1`，但没有安装 `ffmpeg`。这意味着：

- `WAV` 路径通常更稳妥
- `MP3` 上传或 `MP3` 导出在某些环境里可能不可用

如果容器内需要完整 `MP3` 能力，建议补充安装 `ffmpeg`。

## 页面功能说明

### 语音合成

支持选择以下模型：

- `mimo-v2.5-tts`
- `mimo-v2.5-tts-voicedesign`
- `mimo-v2.5-tts-voiceclone`
- `mimo-v2-tts`

支持的主要参数：

- 文本内容
- 音色
- 风格指令
- 输出格式：`mp3` / `wav`
- 唱歌模式：仅普通 TTS 模型可用

其中：

- `VoiceDesign` 模式要求填写音色描述
- `VoiceClone` 模式可直接上传样本生成，也可复用已保存的克隆音色

### 音色克隆

上传音频样本后，后端会先做预处理：

- 最长截取 `10s`
- 统一转为 `16kHz`、单声道、16-bit PCM WAV

随后：

- 调用 `mimo-v2.5-tts-voiceclone`
- 保存样本到 `cloned_voices/<voice_id>/sample.wav`
- 保存元信息到 `cloned_voices/<voice_id>/info.json`
- 更新 `cloned_voices/index.json`

### 历史记录

历史记录保存在 `history.json`，每条记录包含：

- 任务 ID
- 模型名
- 文本摘要
- 音色
- 风格摘要
- 输出文件地址
- 输出格式
- 创建时间

删除历史时，会同时尝试删除对应的 `mp3/wav` 文件。

## 数据说明

### `output/`

保存生成后的最终音频文件，文件名示例：

- `tts_xxxxxxxx.mp3`
- `tts_xxxxxxxx.wav`
- `clone_xxxxxxxx.mp3`

### `cloned_voices/`

每个克隆音色单独一个目录，例如：

```text
cloned_voices/
└── cv_ab12cd34/
    ├── info.json
    └── sample.wav
```

### `history.json`

历史记录文件，最多保留最近 `50` 条。

## API 概览

### 页面与文件

- `GET /`
  - 首页
- `GET /output/<filename>`
  - 下载或播放生成音频

### 合成相关

- `POST /api/tts/progress`
  - 带进度的合成接口
  - 前端主要使用这个接口
- `POST /api/tts`
  - 不带进度的兼容接口
- `POST /api/tts/voiceclone`
  - 表单上传版音色克隆接口

### 配置与查询

- `GET /api/config`
  - 返回模型配置与 API Key 是否已配置
- `GET /api/voices?model=...`
  - 获取模型内置音色
- `GET /api/cloned-voices`
  - 获取已保存克隆音色列表
- `GET /api/history`
  - 获取历史记录
- `GET /api/diagnose`
  - 检查网络和 MiMo API 连通性

### 删除接口

- `DELETE /api/cloned-voices/<voice_id>`
- `DELETE /api/history/<history_id>`

## 已知实现特征

- 后端核心逻辑集中在 `app.py`，目前没有拆模块
- 进度接口本质上是 HTTP 流式输出，前端用 `XMLHttpRequest` 持续读取
- 长文本时会逐段调用 MiMo，再在本地拼接
- 历史和克隆音色信息都保存在本地文件中，不依赖数据库

## 适合后续优化的点

- 将 `app.py` 拆分为路由、服务、存储模块
- 为 Docker 镜像补齐 `ffmpeg`
- 为接口补测试
- 增加鉴权与访问控制
- 增加任务队列，避免长任务阻塞
- 为历史记录与音色管理引入数据库

## 快速检查清单

启动后如果不能正常工作，优先检查：

1. `.env` 是否已配置 `MIMO_API_KEY`
2. 服务器是否能访问 `https://api.xiaomimimo.com`
3. 是否安装了 `ffmpeg`，尤其是使用 `MP3` 时
4. `output/`、`cloned_voices/` 是否有写权限
5. 可直接访问 `GET /api/diagnose` 查看诊断结果

