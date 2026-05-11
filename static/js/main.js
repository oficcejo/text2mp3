/**
 * 小米 MiMo TTS - 前端逻辑（支持实时进度）
 */

let currentEventSource = null;
let timerInterval = null;
let startTime = null;

// ============================================================
// 初始化
// ============================================================
document.addEventListener('DOMContentLoaded', function() {
    checkApiStatus();
    setupCharCounters();
    loadHistory();
    loadClonedVoicesList();
    setupTabSwitching();
    setupModelSelector();
    setupFileUpload();
});

// ============================================================
// API 状态检查
// ============================================================
async function checkApiStatus() {
    const dot = document.getElementById('statusDot');
    const text = document.getElementById('statusText');

    try {
        const resp = await fetch('/api/config');
        const data = await resp.json();

        if (data.has_api_key) {
            dot.className = 'status-dot configured';
            text.textContent = 'API 已配置';
        } else {
            dot.className = 'status-dot unconfigured';
            text.textContent = '未配置 API 密钥';
        }
    } catch(e) {
        dot.className = 'status-dot unconfigured';
        text.textContent = '连接失败';
    }
}

// ============================================================
// 字数统计
// ============================================================
function setupCharCounters() {
    const SYNTH_MAX_CHARS = 4500;
    const CLONE_MAX_CHARS = 3000;

    function updateCounter(textareaId, countId, tokenId) {
        const ta = document.getElementById(textareaId);
        const countEl = document.getElementById(countId);
        const tokenEl = tokenId ? document.getElementById(tokenId) : null;
        if (!ta || !countEl) return;

        const update = function() {
            const len = ta.value.length;
            countEl.textContent = len + ' 字';

            if (tokenEl && len > 0) {
                const cjkChars = (ta.value.match(/[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]/g) || []).length;
                const otherChars = len - cjkChars;
                const estTokens = Math.ceil(cjkChars * 1.2 + otherChars * 0.3);
                const limit = (textareaId === 'cloneTextInput') ? CLONE_MAX_CHARS : SYNTH_MAX_CHARS;
                tokenEl.textContent = '\u2248' + estTokens + ' tokens';
                if (len > limit) {
                    const chunks = Math.ceil(len / limit);
                    tokenEl.textContent += ' \u00b7 \u81ea\u52a8\u5206' + chunks + '\u6bb5\u5408\u6210';
                    tokenEl.style.color = 'var(--color-warning, #f59e0b)';
                } else {
                    tokenEl.style.color = 'var(--text-muted)';
                }
            }
        };
        ta.addEventListener('input', update);
        update();
    }

    updateCounter('textInput', 'charCount', null);
    updateCounter('cloneTextInput', 'cloneCharCount', 'cloneTokenEst');
}

// ============================================================
// Tab 切换
// ============================================================
function setupTabSwitching() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', function(e) {
            e.preventDefault();
            const tab = this.dataset.tab;

            document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
            this.classList.add('active');

            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            document.getElementById('tab-' + tab).classList.add('active');

            if (tab === 'voiceclone') {
                loadClonedVoicesList();
            }
        });
    });
}

// ============================================================
// 模型选择器
// ============================================================
function setupModelSelector() {
    document.querySelectorAll('.model-option').forEach(btn => {
        btn.addEventListener('click', function() {
            document.querySelectorAll('.model-option').forEach(b => b.classList.remove('active'));
            this.classList.add('active');

            const model = this.dataset.model;
            const voiceGroup = document.getElementById('voiceGroup');
            const voiceDesignGroup = document.getElementById('voiceDesignGroup');
            const singingMode = document.getElementById('singingMode');

            if (model === 'mimo-v2.5-tts' || model === 'mimo-v2-tts') {
                voiceGroup.classList.remove('hidden');
                voiceDesignGroup.classList.add('hidden');
                updateVoiceList(model);
            } else if (model === 'mimo-v2.5-tts-voicedesign') {
                voiceGroup.classList.add('hidden');
                voiceDesignGroup.classList.remove('hidden');
            } else if (model === 'mimo-v2.5-tts-voiceclone') {
                voiceGroup.classList.add('hidden');
                voiceDesignGroup.classList.add('hidden');
            }

            if (model === 'mimo-v2.5-tts' || model === 'mimo-v2-tts') {
                singingMode.closest('.form-group').classList.remove('hidden');
            } else {
                singingMode.closest('.form-group').classList.add('hidden');
                singingMode.checked = false;
            }
        });
    });
}

async function updateVoiceList(model) {
    try {
        const resp = await fetch(`/api/voices?model=${model}`);
        const data = await resp.json();
        const select = document.getElementById('voiceSelect');
        select.innerHTML = '';
        const voices = data.voices || {};

        // 加载已克隆音色
        let clonedVoices = [];
        try {
            const cvResp = await fetch('/api/cloned-voices');
            const cvData = await cvResp.json();
            clonedVoices = cvData.voices || [];
        } catch(e) {}

        if (Object.keys(voices).length === 0) {
            if (clonedVoices.length === 0) {
                select.innerHTML = '<option value="">该模型无内置音色</option>';
                return;
            }
        }

        // 已克隆音色分组
        if (clonedVoices.length > 0) {
            const cloneGroup = document.createElement('optgroup');
            cloneGroup.label = '已克隆音色';
            for (const cv of clonedVoices) {
                const opt = document.createElement('option');
                opt.value = 'cloned:' + cv.id;
                opt.textContent = cv.name + (cv.original_filename ? ' (' + cv.original_filename + ')' : '');
                cloneGroup.appendChild(opt);
            }
            select.appendChild(cloneGroup);
        }

        for (const [id, name] of Object.entries(voices)) {
            const opt = document.createElement('option');
            opt.value = id;
            opt.textContent = name;
            select.appendChild(opt);
        }
    } catch(e) {
        console.error('获取音色列表失败:', e);
    }
}

// ============================================================
// 进度面板通用逻辑
// ============================================================

function startTimer(elementId) {
    startTime = Date.now();
    const el = document.getElementById(elementId);
    timerInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        const m = Math.floor(elapsed / 60);
        const s = elapsed % 60;
        el.textContent = m > 0 ? `${m}m${s}s` : `${s}s`;
    }, 200);
}

function stopTimer() {
    if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
    }
}

function updateProgressUI(event, prefix = '') {
    const data = JSON.parse(event.data);
    const stage = data.stage;
    const progress = data.progress;
    const message = data.message;

    // 更新进度条
    const fill = document.getElementById(prefix + 'ProgressBarFill');
    const percent = document.getElementById(prefix + 'ProgressPercent');
    const msg = document.getElementById(prefix + 'ProgressMessage');

    if (fill) fill.style.width = progress + '%';
    if (percent) percent.textContent = progress + '%';

    if (msg) {
        msg.textContent = message;
        msg.className = 'progress-message';
    }

    // 更新阶段指示器
    const allStages = ['preparing', 'connecting', 'generating', 'processing', 'complete'];
    const stageOrder = ['preparing', 'connecting', 'generating', 'processing', 'complete'];
    const currentIdx = stageOrder.indexOf(stage);

    document.querySelectorAll(`#${prefix}ProgressOverlay .stage-item`).forEach(item => {
        const s = item.dataset.stage;
        const sIdx = stageOrder.indexOf(s);

        item.classList.remove('active', 'done', 'error');

        if (stage === 'error') {
            item.classList.add('error');
        } else if (sIdx < currentIdx) {
            item.classList.add('done');
        } else if (sIdx === currentIdx) {
            item.classList.add('active');
            // 完成后不要脉冲动画
            if (stage === 'complete') {
                item.classList.remove('active');
                item.classList.add('done');
            }
        }
    });

    // 更新连接线
    document.querySelectorAll(`#${prefix}ProgressOverlay .stage-connector`).forEach((conn, i) => {
        conn.classList.remove('active', 'done');
        if (stage === 'error') return;
        if (i < currentIdx) {
            conn.classList.add('done');
        } else if (i === currentIdx && stage !== 'complete') {
            conn.classList.add('active');
        } else if (i < currentIdx || (stage === 'complete' && i < allStages.length - 1)) {
            conn.classList.add('done');
        }
    });

    if (stage === 'error') {
        if (msg) msg.className = 'progress-message error';
        stopTimer();
        setTimeout(function() {
            hideProgress(prefix);
        }, 5000);
    }

    if (stage === 'complete') {
        if (msg) msg.className = 'progress-message success';
        stopTimer();
        currentEventSource = null;

        // 延迟关闭进度面板
        setTimeout(() => {
            hideProgress(prefix);

            // 显示结果
            if (data.file_url) {
                const playerId = prefix === 'clone' ? 'cloneAudioPlayer' : 'audioPlayer';
                const downloadId = prefix === 'clone' ? 'cloneDownloadBtn' : 'downloadBtn';
                const resultId = prefix === 'clone' ? 'cloneResult' : 'synthesizeResult';

                const player = document.getElementById(playerId);
                const download = document.getElementById(downloadId);
                player.src = data.file_url;
                player.load();
                player.play().catch(() => {});
                download.href = data.file_url;
                download.download = data.file_url.split('/').pop();

                document.getElementById(resultId).classList.remove('hidden');
                document.getElementById(resultId).scrollIntoView({ behavior: 'smooth', block: 'nearest' });

                loadHistory();
                loadClonedVoicesList();
                showToast('合成成功！', 'success');
            }
        }, 600);
    }
}

function showProgress(prefix = '') {
    const overlay = document.getElementById(prefix + 'ProgressOverlay');
    if (overlay) {
        overlay.classList.remove('hidden');

        // 重置 UI
        const fill = document.getElementById(prefix + 'ProgressBarFill');
        const percent = document.getElementById(prefix + 'ProgressPercent');
        const msg = document.getElementById(prefix + 'ProgressMessage');
        const timer = document.getElementById(prefix + 'ProgressTimer');
        if (fill) fill.style.width = '0%';
        if (percent) percent.textContent = '0%';
        if (msg) { msg.textContent = '正在准备...'; msg.className = 'progress-message'; }
        if (timer) timer.textContent = '0s';

        // 重置阶段状态
        document.querySelectorAll(`#${prefix}ProgressOverlay .stage-item`).forEach(item => {
            item.classList.remove('active', 'done', 'error');
        });
        document.querySelectorAll(`#${prefix}ProgressOverlay .stage-connector`).forEach(conn => {
            conn.classList.remove('active', 'done');
        });

        startTimer(prefix + 'ProgressTimer');
    }
}

function hideProgress(prefix = '') {
    const overlay = document.getElementById(prefix + 'ProgressOverlay');
    if (overlay) overlay.classList.add('hidden');
    stopTimer();
}

// ============================================================
// 语音合成（通过 SSE 进度端点）
// ============================================================
function synthesize() {
    if (currentEventSource) {
        showToast('已有任务正在执行', 'error');
        return;
    }

    const text = document.getElementById('textInput').value.trim();
    if (!text) {
        showToast('请输入要合成的文本', 'error');
        return;
    }

    const activeModel = document.querySelector('.model-option.active');
    const model = activeModel ? activeModel.dataset.model : 'mimo-v2.5-tts';
    const voice = document.getElementById('voiceSelect').value;
    const styleInstruction = document.getElementById('styleInput').value.trim();
    const audioFormat = document.getElementById('formatSelect').value;
    const singingMode = document.getElementById('singingMode').checked;

    const payload = {
        model: model,
        text: text,
        voice: voice,
        style_instruction: styleInstruction,
        audio_format: audioFormat,
        stream: false,
        singing: singingMode,
    };

    // VoiceDesign 特殊处理
    if (model === 'mimo-v2.5-tts-voicedesign') {
        const voiceDesign = document.getElementById('voiceDesignInput').value.trim();
        if (!voiceDesign) {
            showToast('VoiceDesign 模型需要填写音色描述', 'error');
            return;
        }
        payload.voice_design_text = voiceDesign;
    }

    // 显示进度面板
    showProgress('');

    document.getElementById('synthesizeBtn').disabled = true;
    document.getElementById('synthesizeBtn').textContent = '合成中...';

    // 创建 EventSource 连接
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/tts/progress', true);
    xhr.setRequestHeader('Content-Type', 'application/json');
    xhr.responseType = 'text';

    let lastIndex = 0;

    xhr.onprogress = function() {
        const newData = xhr.responseText.substring(lastIndex);
        lastIndex = xhr.responseText.length;

        // 解析 SSE 事件
        const lines = newData.split('\n');
        for (const line of lines) {
            if (line.startsWith('data: ')) {
                try {
                    const eventData = JSON.parse(line.substring(6));
                    updateProgressUI({ data: line.substring(6) }, '');
                } catch(e) {
                    console.error('SSE parse error:', e, 'raw:', line.substring(6, 200));
                    hideProgress('');
                    showToast('数据解析失败，请重试', 'error');
                }
            }
        }
    };

    xhr.onloadend = function() {
        document.getElementById('synthesizeBtn').disabled = false;
        document.getElementById('synthesizeBtn').innerHTML = `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            生成语音
        `;

        if (xhr.status !== 200) {
            try {
                const errData = JSON.parse(xhr.responseText);
                hideProgress('');
                showToast(errData.error || '请求失败', 'error');
            } catch {
                hideProgress('');
                showToast('请求失败，请检查网络', 'error');
            }
        }
        currentEventSource = null;
    };

    xhr.onerror = function() {
        hideProgress('');
        document.getElementById('synthesizeBtn').disabled = false;
        document.getElementById('synthesizeBtn').innerHTML = `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            生成语音
        `;
        showToast('网络连接失败', 'error');
        currentEventSource = null;
    };

    xhr.send(JSON.stringify(payload));
    currentEventSource = xhr;
}

function cancelSynthesis() {
    if (currentEventSource) {
        currentEventSource.abort();
        currentEventSource = null;
    }
    hideProgress('');
    document.getElementById('synthesizeBtn').disabled = false;
    document.getElementById('synthesizeBtn').innerHTML = `
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
        生成语音
    `;
    showToast('已取消', 'info');
}

// ============================================================
// 声音克隆（通过 SSE 进度端点）
// ============================================================
function setupFileUpload() {
    const uploadArea = document.getElementById('uploadArea');
    const fileInput = document.getElementById('audioFileInput');

    uploadArea.addEventListener('click', () => fileInput.click());

    uploadArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        uploadArea.classList.add('dragover');
    });

    uploadArea.addEventListener('dragleave', () => {
        uploadArea.classList.remove('dragover');
    });

    uploadArea.addEventListener('drop', (e) => {
        e.preventDefault();
        uploadArea.classList.remove('dragover');
        if (e.dataTransfer.files.length) {
            handleFileSelect(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length) {
            handleFileSelect(fileInput.files[0]);
        }
    });
}

function handleFileSelect(file) {
    const maxSize = 10 * 1024 * 1024;
    if (file.size > maxSize) {
        showToast('文件过大，请选择 10MB 以下的音频文件', 'error');
        return;
    }

    // 检查格式
    const validTypes = ['.mp3', '.wav'];
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!validTypes.includes(ext)) {
        showToast('仅支持 MP3 和 WAV 格式', 'error');
        return;
    }

    document.getElementById('fileInfo').classList.remove('hidden');
    document.getElementById('fileName').textContent = file.name;
    document.getElementById('uploadArea').classList.add('hidden');

    // 保留文件引用
    const dt = new DataTransfer();
    dt.items.add(file);
    document.getElementById('audioFileInput').files = dt.files;
}

function clearFile() {
    document.getElementById('fileInfo').classList.add('hidden');
    document.getElementById('uploadArea').classList.remove('hidden');
    document.getElementById('audioFileInput').value = '';
}

function cloneVoice() {
    if (currentEventSource) {
        showToast('已有任务正在执行', 'error');
        return;
    }

    const fileInput = document.getElementById('audioFileInput');
    const text = document.getElementById('cloneTextInput').value.trim();
    const style = document.getElementById('cloneStyleInput').value.trim();
    const format = document.getElementById('cloneFormatSelect').value;
    const voiceName = document.getElementById('cloneNameInput').value.trim();

    if (!fileInput.files.length) {
        showToast('请上传音频样本文件', 'error');
        return;
    }

    if (!text) {
        showToast('请输入要合成的文本', 'error');
        return;
    }

    // 读取音频文件为 Base64
    const reader = new FileReader();
    reader.onload = function(e) {
        const base64 = e.target.result.split(',')[1];
        const mimeType = fileInput.files[0].type || 'audio/mpeg';

        showProgress('clone');
        document.getElementById('cloneBtn').disabled = true;
        document.getElementById('cloneBtn').textContent = '克隆中...';

        const payload = {
            model: 'mimo-v2.5-tts-voiceclone',
            text: text,
            voice_name: voiceName,
            voice_audio_base64: base64,
            voice_audio_mime: mimeType,
            original_filename: fileInput.files[0].name,
            style_instruction: style,
            audio_format: format,
            stream: false,
            singing: false,
        };

        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/tts/progress', true);
        xhr.setRequestHeader('Content-Type', 'application/json');
        xhr.responseType = 'text';

        let lastIndex = 0;

        xhr.onprogress = function() {
            const newData = xhr.responseText.substring(lastIndex);
            lastIndex = xhr.responseText.length;

            const lines = newData.split('\n');
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        updateProgressUI({ data: line.substring(6) }, 'clone');
                    } catch(e) {
                        console.error('Clone SSE parse error:', e, 'raw:', line.substring(6, 200));
                        hideProgress('clone');
                        showToast('数据解析失败，请重试', 'error');
                    }
                }
            }
        };

        xhr.onloadend = function() {
            document.getElementById('cloneBtn').disabled = false;
            document.getElementById('cloneBtn').innerHTML = `
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                开始克隆 & 合成
            `;

            if (xhr.status !== 200) {
                try {
                    const errData = JSON.parse(xhr.responseText);
                    hideProgress('clone');
                    showToast(errData.error || '请求失败', 'error');
                } catch {
                    hideProgress('clone');
                    showToast('请求失败，请检查网络', 'error');
                }
            }
            currentEventSource = null;
        };

        xhr.onerror = function() {
            hideProgress('clone');
            document.getElementById('cloneBtn').disabled = false;
            document.getElementById('cloneBtn').innerHTML = `
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                开始克隆 & 合成
            `;
            showToast('网络连接失败', 'error');
            currentEventSource = null;
        };

        xhr.send(JSON.stringify(payload));
        currentEventSource = xhr;
    };

    reader.onerror = function() {
        showToast('读取音频文件失败', 'error');
    };

    reader.readAsDataURL(fileInput.files[0]);
}

function cancelCloneSynthesis() {
    if (currentEventSource) {
        currentEventSource.abort();
        currentEventSource = null;
    }
    hideProgress('clone');
    document.getElementById('cloneBtn').disabled = false;
    document.getElementById('cloneBtn').innerHTML = `
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
        开始克隆 & 合成
    `;
    showToast('已取消', 'info');
}

// ============================================================
// 历史记录
// ============================================================
async function loadHistory() {
    try {
        const resp = await fetch('/api/history');
        const data = await resp.json();
        const history = data.history || [];
        const container = document.getElementById('historyList');
        const emptyState = document.getElementById('emptyHistory');

        if (history.length === 0) {
            container.innerHTML = '';
            container.appendChild(emptyState);
            emptyState.classList.remove('hidden');
            return;
        }

        emptyState.classList.add('hidden');
        container.innerHTML = history.map(item => {
            return `
                <div class="history-item">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#64748b" stroke-width="2">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                        <polyline points="14 2 14 8 20 8"/>
                    </svg>
                    <div class="history-info">
                        <div class="history-text">${escapeHtml(item.text)}</div>
                        <div class="history-meta">
                            ${escapeHtml(item.model)}
                            ${item.voice ? '· ' + escapeHtml(item.voice) : ''}
                            ${item.style ? '· ' + escapeHtml(item.style) : ''}
                            · ${formatTime(item.created_at)}
                        </div>
                    </div>
                    <div style="display:flex;gap:4px">
                        <a href="${item.file_url}" class="btn-icon" title="播放" onclick="playHistory(event, '${item.file_url}')">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                        </a>
                        <a href="${item.file_url}" class="btn-icon" title="下载" download>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                        </a>
                        <button class="btn-icon" title="删除" onclick="deleteHistory('${item.id}')">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                        </button>
                    </div>
                </div>
            `;
        }).join('');

    } catch(e) {
        console.error('加载历史失败:', e);
    }
}

function playHistory(e, url) {
    e.preventDefault();
    const player = document.getElementById('audioPlayer');
    player.src = url;
    player.load();
    player.play().catch(() => {});
    document.getElementById('synthesizeResult').classList.remove('hidden');
    document.getElementById('tab-synthesize').scrollIntoView({ behavior: 'smooth' });
}

async function deleteHistory(id) {
    if (!confirm('确定要删除这条记录和对应的音频文件吗？')) return;

    try {
        const resp = await fetch(`/api/history/${id}`, { method: 'DELETE' });
        if (resp.ok) {
            loadHistory();
            showToast('已删除', 'success');
        }
    } catch(e) {
        showToast('删除失败', 'error');
    }
}

// ============================================================
// 克隆音色管理
// ============================================================
async function loadClonedVoicesList() {
    try {
        const resp = await fetch('/api/cloned-voices');
        const data = await resp.json();
        const voices = data.voices || [];
        const list = document.getElementById('clonedVoicesList');
        const empty = document.getElementById('emptyClonedVoices');
        const count = document.getElementById('clonedVoicesCount');

        if (count) count.textContent = voices.length;

        if (voices.length === 0) {
            if (empty) empty.style.display = '';
            // Remove any existing items
            list.querySelectorAll('.cloned-voice-item').forEach(el => el.remove());
            return;
        }

        if (empty) empty.style.display = 'none';

        // Build list
        list.querySelectorAll('.cloned-voice-item').forEach(el => el.remove());
        for (const cv of voices) {
            const item = document.createElement('div');
            item.className = 'cloned-voice-item';
            item.style.cssText = 'display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-bottom:1px solid var(--border-color);';
            item.innerHTML = `
                <div>
                    <div style="font-weight:500">${escapeHtml(cv.name)}</div>
                    <div style="font-size:12px;color:var(--text-muted)">
                        ${cv.original_filename ? escapeHtml(cv.original_filename) + ' · ' : ''}
                        ${cv.duration_secs ? cv.duration_secs + 's' : ''}
                        · ${formatTime(cv.created_at)}
                    </div>
                </div>
                <button class="btn-icon" title="删除" onclick="deleteClonedVoice('${cv.id}')">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
            `;
            list.appendChild(item);
        }
    } catch(e) {
        console.error('加载克隆音色失败:', e);
    }
}

async function deleteClonedVoice(voiceId) {
    if (!confirm('确定要删除这个克隆音色吗？')) return;
    try {
        const resp = await fetch('/api/cloned-voices/' + voiceId, { method: 'DELETE' });
        if (resp.ok) {
            loadClonedVoicesList();
            // Also refresh voice dropdown in synthesize tab
            const model = document.querySelector('.model-option.active');
            if (model) {
                updateVoiceList(model.dataset.model);
            }
            showToast('克隆音色已删除', 'success');
        } else {
            showToast('删除失败', 'error');
        }
    } catch(e) {
        showToast('删除失败', 'error');
    }
}

function copyResultLink() {
    const link = document.getElementById('downloadBtn').href;
    navigator.clipboard.writeText(window.location.origin + link).then(() => {
        showToast('链接已复制到剪贴板', 'success');
    }).catch(() => {
        showToast('复制失败', 'error');
    });
}

// ============================================================
// Toast 通知
// ============================================================
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        toast.style.transition = 'all .3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ============================================================
// 工具函数
// ============================================================
function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function formatTime(isoStr) {
    try {
        const d = new Date(isoStr);
        const now = new Date();
        const diff = now - d;

        if (diff < 60000) return '刚刚';
        if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`;
        if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`;

        const month = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        const hours = String(d.getHours()).padStart(2, '0');
        const mins = String(d.getMinutes()).padStart(2, '0');
        return `${month}-${day} ${hours}:${mins}`;
    } catch {
        return isoStr;
    }
}