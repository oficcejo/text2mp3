/**
 * 小米 MiMo TTS - 前端逻辑（支持实时进度）
 */

let currentEventSource = null;
let timerInterval = null;
let startTime = null;
let currentVideoJobId = null;
let currentVideoPollTimer = null;
let currentDesignAudioBase64 = null;
let currentVoicePreviewAudio = null;
let currentVoicePreviewId = null;

// 文生动画专属状态变量
let currentAnimJobId = null;
let currentAnimPollTimer = null;
let currentAnimStartTime = null;
let currentAnimTimerInterval = null;
let currentAnimStoryboard = null;

// ============================================================
// 初始化
// ============================================================
document.addEventListener('DOMContentLoaded', function() {
    checkApiStatus();
    setupCharCounters();
    loadHistory();
    loadClonedVoicesList();
    loadDesignedVoices();
    updateAllVoiceDropdowns();
    loadProjectCount();
    loadAnimProjectCount();
    loadVideoConfig();
    setupTabSwitching();
    setupModelSelector();
    setupFileUpload();
    setupProjectNameInput();
    setupAnimThemeSelector();
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
    updateCounter('videoTextInput', 'videoCharCount', 'videoTokenEst');
    updateCounter('animTextInput', 'animCharCount', null);
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
            const targetTab = document.getElementById('tab-' + tab);
            if (targetTab) targetTab.classList.add('active');

            if (tab === 'voiceclone') {
                loadClonedVoicesList();
            } else if (tab === 'video') {
                loadVideoConfig();
                updateAllVoiceDropdowns();
                loadProjectCount();
            } else if (tab === 'animation') {
                updateAllVoiceDropdowns();
                loadAnimProjectCount();
                setupAnimThemeSelector();
            } else if (tab === 'voicedesign') {
                loadDesignedVoices();
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

function populateVoiceSelect(selectEl, voicesData, clonedVoices, designedVoices, currentValue) {
    if (!selectEl) return;
    const previousVal = currentValue !== undefined ? currentValue : selectEl.value;
    selectEl.innerHTML = '';

    // 1. 中文官方预置
    const zhGroup = document.createElement('optgroup');
    zhGroup.label = '官方预置音色 (中文)';
    // 2. 英文官方预置
    const enGroup = document.createElement('optgroup');
    enGroup.label = '官方预置音色 (英文)';

    for (const [id, info] of Object.entries(voicesData)) {
        const opt = document.createElement('option');
        opt.value = id;
        const name = typeof info === 'object' ? (info.name || id) : info;
        const desc = typeof info === 'object' && info.desc ? ` - ${info.desc}` : '';
        opt.textContent = `${name}${desc}`;

        const lang = typeof info === 'object' ? (info.lang || '') : '';
        if (lang === '英文' || ['Mia', 'Chloe', 'Milo', 'Dean'].includes(id)) {
            enGroup.appendChild(opt);
        } else {
            zhGroup.appendChild(opt);
        }
    }

    if (zhGroup.children.length > 0) selectEl.appendChild(zhGroup);
    if (enGroup.children.length > 0) selectEl.appendChild(enGroup);

    // 3. 我的设计音色 (Voice Design)
    if (designedVoices && designedVoices.length > 0) {
        const desGroup = document.createElement('optgroup');
        desGroup.label = '我的设计音色 (Voice Design)';
        for (const dv of designedVoices) {
            const opt = document.createElement('option');
            opt.value = 'designed:' + dv.id;
            const promptPreview = dv.prompt ? (dv.prompt.length > 20 ? dv.prompt.slice(0, 20) + '...' : dv.prompt) : '';
            opt.textContent = `✨ ${dv.name}${promptPreview ? ` (${promptPreview})` : ''}`;
            desGroup.appendChild(opt);
        }
        selectEl.appendChild(desGroup);
    }

    // 4. 我的克隆音色 (Voice Clone)
    if (clonedVoices && clonedVoices.length > 0) {
        const cloneGroup = document.createElement('optgroup');
        cloneGroup.label = '我的克隆音色 (Voice Clone)';
        for (const cv of clonedVoices) {
            const opt = document.createElement('option');
            opt.value = 'cloned:' + cv.id;
            opt.textContent = `🧬 ${cv.name}` + (cv.original_filename ? ` (${cv.original_filename})` : '');
            cloneGroup.appendChild(opt);
        }
        selectEl.appendChild(cloneGroup);
    }

    if (previousVal && Array.from(selectEl.options).some(o => o.value === previousVal)) {
        selectEl.value = previousVal;
    } else {
        selectEl.value = 'mimo_default';
    }
}

async function updateAllVoiceDropdowns() {
    try {
        const [voicesRes, clonedRes, designedRes] = await Promise.all([
            fetch('/api/voices?model=mimo-v2.5-tts').then(r => r.json()).catch(() => ({ voices: {} })),
            fetch('/api/cloned-voices').then(r => r.json()).catch(() => ({ voices: [] })),
            fetch('/api/designed-voices').then(r => r.json()).catch(() => ({ voices: [] })),
        ]);

        const voicesData = voicesRes.voices || {};
        const clonedVoices = clonedRes.voices || [];
        const designedVoices = designedRes.voices || [];

        const synthSelect = document.getElementById('voiceSelect');
        if (synthSelect) {
            populateVoiceSelect(synthSelect, voicesData, clonedVoices, designedVoices);
        }

        const videoSelect = document.getElementById('videoVoiceSelect');
        if (videoSelect) {
            populateVoiceSelect(videoSelect, voicesData, clonedVoices, designedVoices);
        }

        const changeVoiceSelect = document.getElementById('videoChangeVoiceSelect');
        if (changeVoiceSelect) {
            populateVoiceSelect(changeVoiceSelect, voicesData, clonedVoices, designedVoices);
        }

        const animVoiceSelect = document.getElementById('animVoiceSelect');
        if (animVoiceSelect) {
            populateVoiceSelect(animVoiceSelect, voicesData, clonedVoices, designedVoices);
        }

        const animChangeVoiceSelect = document.getElementById('animChangeVoiceSelect');
        if (animChangeVoiceSelect) {
            populateVoiceSelect(animChangeVoiceSelect, voicesData, clonedVoices, designedVoices);
        }
    } catch (e) {
        console.error('更新音色下拉菜单失败:', e);
    }
}

async function updateVoiceList(model) {
    if (model === 'mimo-v2.5-tts') {
        return updateAllVoiceDropdowns();
    }
    try {
        const resp = await fetch(`/api/voices?model=${model}`);
        const data = await resp.json();
        const select = document.getElementById('voiceSelect');
        if (!select) return;
        select.innerHTML = '';
        const voices = data.voices || {};

        for (const [id, val] of Object.entries(voices)) {
            const opt = document.createElement('option');
            opt.value = id;
            opt.textContent = typeof val === 'object' ? (val.name || id) : val;
            select.appendChild(opt);
        }
    } catch(e) {
        console.error('获取音色列表失败:', e);
    }
}

async function updateVideoVoiceList() {
    return updateAllVoiceDropdowns();
}

// ============================================================
// 进度面板通用逻辑
// ============================================================

function getProgressEl(suffix, prefix = '') {
    const p = prefix ? prefix : '';
    if (!p) {
        return document.getElementById('progress' + suffix) ||
               document.getElementById('Progress' + suffix) ||
               document.getElementById(suffix.toLowerCase());
    }
    return document.getElementById(p + 'Progress' + suffix) ||
           document.getElementById(p + 'progress' + suffix);
}

function getProgressOverlayEl(prefix = '') {
    if (!prefix) {
        return document.getElementById('progressOverlay') || document.getElementById('ProgressOverlay');
    }
    return document.getElementById(prefix + 'ProgressOverlay') || document.getElementById(prefix + 'progressOverlay');
}

function getInlineProgressCard(prefix = '') {
    if (!prefix) {
        return document.getElementById('inlineProgressCard');
    }
    return document.getElementById(prefix + 'InlineProgressCard');
}

function startTimer(prefix = '') {
    startTime = Date.now();
    const timerOverlay = getProgressEl('Timer', prefix);
    const inlineTimerId = prefix ? `${prefix}InlineProgressTimer` : 'inlineProgressTimer';
    const timerInline = document.getElementById(inlineTimerId);

    if (timerInterval) clearInterval(timerInterval);
    timerInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        const m = Math.floor(elapsed / 60);
        const s = elapsed % 60;
        const timeStr = m > 0 ? `${m}m${s}s` : `${s}s`;
        if (timerOverlay) timerOverlay.textContent = timeStr;
        if (timerInline) timerInline.textContent = timeStr;
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
    const progress = data.progress || 0;
    const message = data.message || '';

    // 1. 更新模态浮层
    const fill = getProgressEl('BarFill', prefix);
    const percent = getProgressEl('Percent', prefix);
    const msg = getProgressEl('Message', prefix);

    if (fill) fill.style.width = progress + '%';
    if (percent) percent.textContent = progress + '%';
    if (msg) {
        msg.textContent = message;
        msg.className = 'progress-message' + (stage === 'error' ? ' error' : (stage === 'complete' ? ' success' : ''));
    }

    // 2. 更新内联进度条
    const inlineCard = getInlineProgressCard(prefix);
    if (inlineCard) {
        inlineCard.classList.remove('hidden');
        const inlineFill = document.getElementById(prefix ? `${prefix}InlineProgressBarFill` : 'inlineProgressBarFill');
        const inlinePercent = document.getElementById(prefix ? `${prefix}InlineProgressPercent` : 'inlineProgressPercent');
        const inlineMsg = document.getElementById(prefix ? `${prefix}InlineProgressMessage` : 'inlineProgressMessage');
        if (inlineFill) inlineFill.style.width = progress + '%';
        if (inlinePercent) inlinePercent.textContent = progress + '%';
        if (inlineMsg) inlineMsg.textContent = message;
    }

    // 3. 更新按钮文字提示
    const btnId = prefix === 'clone' ? 'cloneBtn' : 'synthesizeBtn';
    const actionBtn = document.getElementById(btnId);
    if (actionBtn && stage !== 'complete' && stage !== 'error') {
        actionBtn.textContent = `${prefix === 'clone' ? '克隆中' : '合成中'} ${progress}%...`;
    }

    // 4. 更新阶段指示器
    const allStages = ['preparing', 'connecting', 'generating', 'processing', 'complete'];
    const currentIdx = allStages.indexOf(stage);
    const overlay = getProgressOverlayEl(prefix);

    if (overlay) {
        overlay.querySelectorAll('.stage-item').forEach(item => {
            const s = item.dataset.stage;
            const sIdx = allStages.indexOf(s);
            item.classList.remove('active', 'done', 'error');

            if (stage === 'error') {
                item.classList.add('error');
            } else if (sIdx < currentIdx) {
                item.classList.add('done');
            } else if (sIdx === currentIdx) {
                item.classList.add('active');
                if (stage === 'complete') {
                    item.classList.remove('active');
                    item.classList.add('done');
                }
            }
        });

        overlay.querySelectorAll('.stage-connector').forEach((conn, i) => {
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
    }

    if (stage === 'error') {
        stopTimer();
        if (actionBtn) {
            actionBtn.disabled = false;
            actionBtn.innerHTML = prefix === 'clone'
                ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> 开始克隆 & 合成`
                : `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> 生成语音`;
        }
        showToast(message || '合成失败', 'error');
        setTimeout(() => {
            hideProgress(prefix);
        }, 5000);
    }

    if (stage === 'complete') {
        stopTimer();
        currentEventSource = null;

        if (actionBtn) {
            actionBtn.disabled = false;
            actionBtn.innerHTML = prefix === 'clone'
                ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> 开始克隆 & 合成`
                : `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg> 生成语音`;
        }

        setTimeout(() => {
            hideProgress(prefix);

            if (data.file_url) {
                const playerId = prefix === 'clone' ? 'cloneAudioPlayer' : 'audioPlayer';
                const downloadId = prefix === 'clone' ? 'cloneDownloadBtn' : 'downloadBtn';
                const resultId = prefix === 'clone' ? 'cloneResult' : 'synthesizeResult';

                const player = document.getElementById(playerId);
                const download = document.getElementById(downloadId);
                if (player) {
                    player.src = data.file_url;
                    player.load();
                    player.play().catch(() => {});
                }
                if (download) {
                    download.href = data.file_url;
                    download.download = data.file_url.split('/').pop();
                }

                const resCard = document.getElementById(resultId);
                if (resCard) {
                    resCard.classList.remove('hidden');
                    resCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }

                loadHistory();
                loadClonedVoicesList();
                showToast('合成成功！', 'success');
            }
        }, 600);
    }
}

function showProgress(prefix = '') {
    const overlay = getProgressOverlayEl(prefix);
    if (overlay) {
        overlay.classList.remove('hidden');

        // 重置浮层 UI
        const fill = getProgressEl('BarFill', prefix);
        const percent = getProgressEl('Percent', prefix);
        const msg = getProgressEl('Message', prefix);
        const timer = getProgressEl('Timer', prefix);
        if (fill) fill.style.width = '0%';
        if (percent) percent.textContent = '0%';
        if (msg) { msg.textContent = '正在准备...'; msg.className = 'progress-message'; }
        if (timer) timer.textContent = '0s';

        // 重置阶段状态
        overlay.querySelectorAll('.stage-item').forEach(item => {
            item.classList.remove('active', 'done', 'error');
        });
        overlay.querySelectorAll('.stage-connector').forEach(conn => {
            conn.classList.remove('active', 'done');
        });
    }

    // 重置内联卡片 UI
    const inlineCard = getInlineProgressCard(prefix);
    if (inlineCard) {
        inlineCard.classList.remove('hidden');
        const inlineFill = document.getElementById(prefix ? `${prefix}InlineProgressBarFill` : 'inlineProgressBarFill');
        const inlinePercent = document.getElementById(prefix ? `${prefix}InlineProgressPercent` : 'inlineProgressPercent');
        const inlineMsg = document.getElementById(prefix ? `${prefix}InlineProgressMessage` : 'inlineProgressMessage');
        const inlineTimer = document.getElementById(prefix ? `${prefix}InlineProgressTimer` : 'inlineProgressTimer');
        if (inlineFill) inlineFill.style.width = '0%';
        if (inlinePercent) inlinePercent.textContent = '0%';
        if (inlineMsg) inlineMsg.textContent = '正在准备...';
        if (inlineTimer) inlineTimer.textContent = '0s';
    }

    startTimer(prefix);
}

function hideProgress(prefix = '') {
    const overlay = getProgressOverlayEl(prefix);
    if (overlay) overlay.classList.add('hidden');

    const inlineCard = getInlineProgressCard(prefix);
    if (inlineCard) inlineCard.classList.add('hidden');

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
        payload.voice = '';
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
// 文生视频
// ============================================================

async function loadVideoConfig() {
    const openaiStatus = document.getElementById('videoOpenAIStatus');
    const ffmpegStatus = document.getElementById('videoFfmpegStatus');
    if (!openaiStatus || !ffmpegStatus) return;

    try {
        const resp = await fetch('/api/video/config');
        const data = await resp.json();

        openaiStatus.textContent = data.openai_configured ? '图像接口：已配置' : '图像接口：未配置';
        openaiStatus.className = 'status-chip ' + (data.openai_configured ? 'success' : 'warn');

        ffmpegStatus.textContent = data.ffmpeg_available ? 'FFmpeg：已就绪（可生成运镜动效与硬字幕 MP4）' : 'FFmpeg：未检测到，将只保留分镜产物';
        ffmpegStatus.className = 'status-chip ' + (data.ffmpeg_available ? 'success' : 'warn');

        // 加载最新任务状态
        loadLatestVideoJob();
    } catch (e) {
        openaiStatus.textContent = '图像接口：检查失败';
        ffmpegStatus.textContent = 'FFmpeg：检查失败';
        openaiStatus.className = 'status-chip warn';
        ffmpegStatus.className = 'status-chip warn';
    }
}

async function loadLatestVideoJob() {
    try {
        const resp = await fetch('/api/video/jobs');
        const data = await resp.json();
        if (data.jobs && data.jobs.length > 0) {
            const latestJob = data.jobs[0];
            currentVideoJobId = latestJob.job_id;
            document.getElementById('videoJobCard').classList.remove('hidden');
            document.getElementById('storyboardCard').classList.remove('hidden');
            document.getElementById('videoPlayerCard').classList.remove('hidden');
            renderVideoJob(latestJob);
            if (['pending', 'planning', 'generating_assets', 'building_subtitles', 'rendering_video'].includes(latestJob.status)) {
                startVideoPolling(latestJob.job_id);
            }
        }
    } catch (e) {
        console.warn('加载最近视频任务失败:', e);
    }
}

async function createVideoJob() {
    const text = document.getElementById('videoTextInput').value.trim();
    const voice = document.getElementById('videoVoiceSelect').value;
    const style = document.getElementById('videoStyleSelect').value;
    const imageDensity = document.getElementById('videoImageDensitySelect').value;
    const btn = document.getElementById('videoGenerateBtn');

    if (!text) {
        showToast('请输入文章内容', 'error');
        return;
    }

    btn.disabled = true;
    btn.innerHTML = '正在创建任务...';

    try {
        const resp = await fetch('/api/video/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, voice, style, aspect_ratio: '16:9', image_density: imageDensity }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.error || '创建任务失败');
        }

        currentVideoJobId = data.job.job_id;
        document.getElementById('videoJobCard').classList.remove('hidden');
        document.getElementById('storyboardCard').classList.remove('hidden');
        document.getElementById('videoPlayerCard').classList.remove('hidden');
        renderVideoJob(data.job);
        startVideoPolling(currentVideoJobId);
        showToast('视频任务已创建', 'success');
    } catch (e) {
        showToast(e.message || '创建任务失败', 'error');
        btn.disabled = false;
        btn.innerHTML = `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            开始生成视频
        `;
    }
}

function startVideoPolling(jobId) {
    stopVideoPolling();
    // 立即拉取一次
    fetch(`/api/video/jobs/${jobId}`)
        .then(r => r.json())
        .then(data => { if (data.job) renderVideoJob(data.job); })
        .catch(() => {});

    currentVideoPollTimer = setInterval(async () => {
        try {
            const resp = await fetch(`/api/video/jobs/${jobId}`);
            const data = await resp.json();
            if (!resp.ok) {
                throw new Error(data.error || '读取任务失败');
            }
            renderVideoJob(data.job);
            if (['completed', 'failed', 'cancelled'].includes(data.job.status)) {
                stopVideoPolling();
            }
        } catch (e) {
            stopVideoPolling();
            showToast('任务轮询失败', 'error');
        }
    }, 2000);
}

function stopVideoPolling() {
    if (currentVideoPollTimer) {
        clearInterval(currentVideoPollTimer);
        currentVideoPollTimer = null;
    }
}

function renderVideoJob(job) {
    const fill = document.getElementById('videoJobProgressFill');
    const statusText = document.getElementById('videoJobStatusText');
    const progressText = document.getElementById('videoJobProgressText');
    const warnings = document.getElementById('videoWarnings');
    const resultActions = document.getElementById('videoResultActions');
    const downloadBtn = document.getElementById('videoDownloadBtn');
    const subtitleBtn = document.getElementById('videoSubtitleBtn');
    const videoPlayer = document.getElementById('videoPlayer');
    const previewHint = document.getElementById('videoPreviewHint');
    const storyboardGrid = document.getElementById('storyboardGrid');
    const genBtn = document.getElementById('videoGenerateBtn');

    fill.style.width = (job.progress || 0) + '%';
    const displayStatus = job.detail_message || mapVideoJobStatus(job.status, job.error);
    statusText.textContent = displayStatus;
    progressText.textContent = (job.progress || 0) + '%';

    // 渲染资产消耗指标看板 (生图张数、大模型 Token 数、配音字数与时长)
    const metricsPanel = document.getElementById('videoMetricsPanel');
    if (metricsPanel) {
        const m = job.metrics;
        if (m) {
            metricsPanel.classList.remove('hidden');
            const imgVal = document.getElementById('metricImagesVal');
            const imgSub = document.getElementById('metricImagesSub');
            const tokVal = document.getElementById('metricTokensVal');
            const tokSub = document.getElementById('metricTokensSub');
            const audVal = document.getElementById('metricAudioVal');
            const audSub = document.getElementById('metricAudioSub');

            if (imgVal) imgVal.textContent = `${m.images_generated || 0} 张`;
            if (imgSub) {
                const totalG = m.images_total || (job.scenes ? Math.ceil(job.scenes.length / 6) : 0);
                const aiSuccess = m.images_ai_success != null ? m.images_ai_success : (m.images_generated || 0);
                imgSub.textContent = `共 ${totalG} 组画面 · AI实生 ${aiSuccess} 张`;
            }

            if (tokVal) tokVal.textContent = `${(m.total_tokens || 0).toLocaleString()} Tokens`;
            if (tokSub) tokSub.textContent = `输入 ${(m.prompt_tokens || 0).toLocaleString()} / 输出 ${(m.completion_tokens || 0).toLocaleString()}`;

            if (audVal) audVal.textContent = `${(m.total_characters || 0).toLocaleString()} 字`;
            if (audSub) {
                const dur = Math.round(m.total_duration_sec || 0);
                audSub.textContent = `共 ${m.total_scenes || (job.scenes ? job.scenes.length : 0)} 个分镜 · 约 ${dur} 秒成片`;
            }
        } else {
            metricsPanel.classList.add('hidden');
        }
    }

    // 动态同步主操作按钮状态
    const isOngoing = ['pending', 'planning', 'generating_assets', 'building_subtitles', 'rendering_video'].includes(job.status);
    if (genBtn) {
        if (isOngoing) {
            genBtn.disabled = true;
            genBtn.innerHTML = `<span style="display:inline-block;width:14px;height:14px;border:2px solid #fff;border-top-color:transparent;border-radius:50%;animation:spin 1s linear infinite;margin-right:6px;vertical-align:middle"></span> ${displayStatus} (${job.progress || 0}%)`;
        } else {
            genBtn.disabled = false;
            genBtn.innerHTML = `
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                开始生成视频
            `;
        }
    }

    if (job.warnings && job.warnings.length) {
        warnings.classList.remove('hidden');
        warnings.innerHTML = job.warnings.map(w => `<div class="video-warning-item">${escapeHtml(w)}</div>`).join('');
    } else {
        warnings.classList.add('hidden');
        warnings.innerHTML = '';
    }

    if (job.video_url || job.subtitle_url) {
        resultActions.classList.remove('hidden');
    } else {
        resultActions.classList.add('hidden');
    }

    if (job.video_url) {
        downloadBtn.classList.remove('hidden');
        downloadBtn.href = job.video_url;
        if (videoPlayer.getAttribute('src') !== job.video_url) {
            videoPlayer.src = job.video_url;
            videoPlayer.load();
        }
        previewHint.classList.add('hidden');
    } else {
        downloadBtn.classList.add('hidden');
        videoPlayer.removeAttribute('src');
        videoPlayer.load();
        if (job.preview_available) {
            previewHint.classList.remove('hidden');
        }
    }

    if (job.subtitle_url) {
        subtitleBtn.classList.remove('hidden');
        subtitleBtn.href = job.subtitle_url;
    } else {
        subtitleBtn.classList.add('hidden');
    }

    // 同步项目名称到工具栏输入框
    const nameInput = document.getElementById('videoProjectNameInput');
    if (nameInput && (!nameInput.dataset.focused || nameInput.dataset.focused === 'false')) {
        nameInput.value = job.project_name || (job.scenes && job.scenes[0] ? job.scenes[0].title : '') || job.job_id;
    }

    // 同步当前更换配音音色下拉框
    const changeSelect = document.getElementById('videoChangeVoiceSelect');
    if (changeSelect && job.config && job.config.voice) {
        changeSelect.value = job.config.voice;
    }

    const scenes = job.scenes || [];
    const groups = [];
    for (const scene of scenes) {
        const groupKey = scene.image_group_index || scene.scene_index;
        let group = groups.find(item => item.key === groupKey);
        if (!group) {
            group = {
                key: groupKey,
                image_url: scene.image_url || scene.frame_url,
                scenes: [],
            };
            groups.push(group);
        }
        if (!group.image_url && (scene.image_url || scene.frame_url)) {
            group.image_url = scene.image_url || scene.frame_url;
        }
        group.scenes.push(scene);
    }

    storyboardGrid.innerHTML = groups.map(group => `
        <div class="storyboard-group-card">
            <div class="storyboard-group-image">
                ${group.image_url ? `<img src="${group.image_url}" alt="漫画组 ${group.key}">` : ''}
                <div class="storyboard-group-badge">画面组 ${group.key}</div>
            </div>
            <div class="storyboard-group-body">
                ${group.scenes.map(scene => `
                    <div class="storyboard-scene-item" data-scene-index="${scene.scene_index}">
                        <div class="storyboard-scene-head">
                            <span class="storyboard-scene-title">分镜 ${scene.scene_index}：${escapeHtml(scene.title || '')}</span>
                            <span class="storyboard-scene-meta">${scene.actual_duration_sec ? `${scene.actual_duration_sec}s` : ''}</span>
                        </div>
                        <div style="margin-top:6px;">
                            <textarea class="form-textarea storyboard-scene-text-edit" rows="2" style="font-size:13px;padding:6px 8px;resize:vertical;" placeholder="分镜台词 / 字幕内容">${escapeHtml(scene.subtitle_text || scene.narration_text || '')}</textarea>
                        </div>
                    </div>
                `).join('')}
            </div>
        </div>
    `).join('');

    if (job.status === 'completed') {
        showToast(job.video_url ? '视频生成完成' : '分镜产物已生成', 'success');
    }
    if (job.status === 'failed' && job.error) {
        showToast(job.error, 'error');
    }
}

function mapVideoJobStatus(status, error) {
    const labels = {
        pending: '等待任务启动',
        planning: '正在生成分镜',
        generating_assets: '正在生成画面和配音',
        building_subtitles: '正在生成字幕',
        rendering_video: '正在合成视频',
        completed: '处理完成',
        failed: '处理失败',
        cancelled: '已取消',
    };
    if (status === 'failed' && error) return `处理失败：${error}`;
    return labels[status] || status || '处理中';
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
// 克隆音色管理与试听
// ============================================================
let currentPreviewAudio = null;
let currentPreviewVoiceId = null;

function togglePreviewVoice(voiceId, sampleUrl) {
    const url = sampleUrl || `/api/cloned-voices/${voiceId}/sample`;

    // 如果当前正在播放同一个，暂停它
    if (currentPreviewVoiceId === voiceId && currentPreviewAudio && !currentPreviewAudio.paused) {
        currentPreviewAudio.pause();
        updatePreviewButtonState(voiceId, false);
        currentPreviewVoiceId = null;
        return;
    }

    // 暂停之前正在播放的
    if (currentPreviewAudio) {
        currentPreviewAudio.pause();
        if (currentPreviewVoiceId) {
            updatePreviewButtonState(currentPreviewVoiceId, false);
        }
    }

    if (!currentPreviewAudio) {
        currentPreviewAudio = new Audio();
        currentPreviewAudio.onended = () => {
            if (currentPreviewVoiceId) {
                updatePreviewButtonState(currentPreviewVoiceId, false);
            }
            currentPreviewVoiceId = null;
        };
        currentPreviewAudio.onerror = () => {
            showToast('试听音频加载失败', 'error');
            if (currentPreviewVoiceId) {
                updatePreviewButtonState(currentPreviewVoiceId, false);
            }
            currentPreviewVoiceId = null;
        };
    }

    currentPreviewVoiceId = voiceId;
    currentPreviewAudio.src = url;
    currentPreviewAudio.load();
    updatePreviewButtonState(voiceId, true);
    currentPreviewAudio.play().catch(e => {
        console.warn('播放失败:', e);
        updatePreviewButtonState(voiceId, false);
        currentPreviewVoiceId = null;
        showToast('浏览器阻止了自动播放，请再点击一次', 'error');
    });
}

function updatePreviewButtonState(voiceId, isPlaying) {
    const item = document.querySelector(`.cloned-voice-item[data-id="${voiceId}"]`);
    if (!item) return;

    if (isPlaying) {
        item.classList.add('playing');
    } else {
        item.classList.remove('playing');
    }

    const roundBtn = item.querySelector('.btn-preview-voice-round');
    if (roundBtn) {
        roundBtn.innerHTML = isPlaying
            ? `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`
            : `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>`;
        roundBtn.title = isPlaying ? '暂停试听' : '点击试听';
    }

    const textBtn = item.querySelector('.btn-preview-voice-text');
    if (textBtn) {
        textBtn.innerHTML = isPlaying
            ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg><span>暂停</span>`
            : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg><span>试听</span>`;
        textBtn.title = isPlaying ? '暂停试听' : '试听样本';
    }
}

function useClonedVoiceInSynthesize(voiceId) {
    // 切换到【语音合成】Tab
    const synthNav = document.querySelector('.nav-item[data-tab="synthesize"]');
    if (synthNav) synthNav.click();

    // 模型选为 mimo-v2.5-tts
    const ttsModelOpt = document.querySelector('.model-option[data-model="mimo-v2.5-tts"]');
    if (ttsModelOpt) ttsModelOpt.click();

    // 下拉框选中该克隆音色
    const voiceSelect = document.getElementById('voiceSelect');
    if (voiceSelect) {
        voiceSelect.value = 'cloned:' + voiceId;
    }
    showToast('已切换至语音合成并应用该音色', 'info');
}

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
            list.querySelectorAll('.cloned-voice-item').forEach(el => el.remove());
            updateVideoVoiceList();
            return;
        }

        if (empty) empty.style.display = 'none';

        // 渲染列表
        list.querySelectorAll('.cloned-voice-item').forEach(el => el.remove());
        for (const cv of voices) {
            const item = document.createElement('div');
            item.className = 'cloned-voice-item' + (currentPreviewVoiceId === cv.id ? ' playing' : '');
            item.dataset.id = cv.id;
            const sampleUrl = cv.sample_url || `/api/cloned-voices/${cv.id}/sample`;
            const isPlaying = currentPreviewVoiceId === cv.id;

            item.innerHTML = `
                <div class="voice-info-left" onclick="togglePreviewVoice('${cv.id}', '${sampleUrl}')">
                    <button type="button" class="btn-preview-voice-round" title="${isPlaying ? '暂停试听' : '点击试听'}" onclick="event.stopPropagation(); togglePreviewVoice('${cv.id}', '${sampleUrl}')">
                        ${isPlaying
                            ? `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`
                            : `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>`}
                    </button>
                    <div>
                        <div class="voice-name">${escapeHtml(cv.name)}</div>
                        <div class="voice-meta">
                            ${cv.original_filename ? escapeHtml(cv.original_filename) + ' · ' : ''}
                            ${cv.duration_secs ? cv.duration_secs + 's' : ''}
                            · ${formatTime(cv.created_at)}
                        </div>
                    </div>
                </div>
                <div class="voice-actions">
                    <button type="button" class="btn btn-sm btn-outline btn-preview-voice-text" title="${isPlaying ? '暂停试听' : '试听样本'}" onclick="event.stopPropagation(); togglePreviewVoice('${cv.id}', '${sampleUrl}')">
                        ${isPlaying
                            ? `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg><span>暂停</span>`
                            : `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg><span>试听</span>`}
                    </button>
                    <button type="button" class="btn btn-sm btn-ghost" title="在语音合成中使用" onclick="event.stopPropagation(); useClonedVoiceInSynthesize('${cv.id}')">
                        使用
                    </button>
                    <button type="button" class="btn-icon" title="删除" onclick="event.stopPropagation(); deleteClonedVoice('${cv.id}')">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                </div>
            `;
            list.appendChild(item);
        }
        updateVideoVoiceList();
    } catch(e) {
        console.error('加载克隆音色失败:', e);
    }
}

async function deleteClonedVoice(voiceId) {
    if (!confirm('确定要删除这个克隆音色吗？')) return;
    if (currentPreviewVoiceId === voiceId && currentPreviewAudio) {
        currentPreviewAudio.pause();
        currentPreviewVoiceId = null;
    }
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

// ============================================================
// 音色设计 (Voice Design) 模块
// ============================================================

function addDesignPromptTag(tag) {
    const promptInput = document.getElementById('designVoicePrompt');
    if (!promptInput) return;
    const current = promptInput.value.trim();
    if (!current) {
        promptInput.value = tag + '，';
    } else if (current.endsWith('，') || current.endsWith(',') || current.endsWith('。')) {
        promptInput.value = current + tag + '，';
    } else {
        promptInput.value = current + '，' + tag + '，';
    }
    promptInput.focus();
}

async function previewVoiceDesign() {
    const prompt = document.getElementById('designVoicePrompt').value.trim();
    const text = document.getElementById('designVoiceText').value.trim();
    const btn = document.getElementById('designPreviewBtn');
    const saveBtn = document.getElementById('designSaveBtn');
    const playerBox = document.getElementById('designPlayerBox');
    const audioPlayer = document.getElementById('designAudioPlayer');

    if (!prompt) {
        showToast('请先输入音色描述 Prompt', 'warn');
        return;
    }
    if (!text) {
        showToast('请输入试听测试文本', 'warn');
        return;
    }

    const origHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span style="display:inline-block;width:14px;height:14px;border:2px solid #fff;border-top-color:transparent;border-radius:50%;animation:spin 1s linear infinite;margin-right:6px;vertical-align:middle"></span> 正在生成专属试听...`;

    try {
        const resp = await fetch('/api/tts/voicedesign/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt, text })
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.error || '试听生成失败');
        }

        currentDesignAudioBase64 = data.audio_base64;
        audioPlayer.src = 'data:audio/wav;base64,' + data.audio_base64;
        playerBox.classList.remove('hidden');
        audioPlayer.play().catch(() => {});

        saveBtn.disabled = false;
        showToast('音色试听生成完成，满意可点击「保存到我的音色库」', 'success');
    } catch (e) {
        showToast(e.message || '试听生成失败', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = origHtml;
    }
}

async function saveCurrentDesignedVoice() {
    let name = document.getElementById('designVoiceName').value.trim();
    const prompt = document.getElementById('designVoicePrompt').value.trim();
    const saveBtn = document.getElementById('designSaveBtn');

    if (!prompt) {
        showToast('请先输入音色描述 Prompt', 'warn');
        return;
    }
    if (!name) {
        name = prompt.slice(0, 10);
        document.getElementById('designVoiceName').value = name;
    }

    saveBtn.disabled = true;
    const origHtml = saveBtn.innerHTML;
    saveBtn.innerHTML = '正在保存...';

    try {
        const resp = await fetch('/api/designed-voices', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name,
                prompt,
                sample_base64: currentDesignAudioBase64
            })
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.error || '保存音色失败');
        }

        showToast(`专属音色「${name}」已保存到音色库！`, 'success');
        await loadDesignedVoices();
        await updateAllVoiceDropdowns();
    } catch (e) {
        showToast(e.message || '保存失败', 'error');
    } finally {
        saveBtn.disabled = false;
        saveBtn.innerHTML = origHtml;
    }
}

async function loadDesignedVoices() {
    const grid = document.getElementById('designedVoicesGrid');
    if (!grid) return;

    try {
        const resp = await fetch('/api/designed-voices');
        const data = await resp.json();
        const voices = data.voices || [];

        if (voices.length === 0) {
            grid.innerHTML = '<p style="color:var(--gray-500);font-size:13px;grid-column:1/-1;">暂无保存的设计音色，在上方设计并保存后可在此查看并随时在配音或视频中使用。</p>';
            return;
        }

        grid.innerHTML = voices.map(v => {
            const isPlaying = currentVoicePreviewId === ('des_' + v.id);
            const sampleUrl = v.sample_url || `/api/designed-voices/${v.id}/sample`;
            return `
                <div class="designed-voice-card ${isPlaying ? 'playing' : ''}">
                    <div class="designed-voice-header">
                        <span class="designed-voice-title">${escapeHtml(v.name)}</span>
                        <div style="display:flex;gap:4px;">
                            <button type="button" class="btn-icon" title="${isPlaying ? '暂停' : '试听样本'}" onclick="togglePreviewDesignedVoice('${v.id}', '${sampleUrl}')">
                                ${isPlaying
                                    ? `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`
                                    : `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>`}
                            </button>
                            <button type="button" class="btn-icon" title="删除" onclick="deleteDesignedVoice('${v.id}')">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                            </button>
                        </div>
                    </div>
                    <div class="designed-voice-prompt" title="${escapeHtml(v.prompt)}">${escapeHtml(v.prompt)}</div>
                    <div class="designed-voice-actions">
                        <span style="font-size:11px;color:var(--gray-400);">${formatTime(v.created_at)}</span>
                        <div style="display:flex;gap:6px;">
                            <button type="button" class="btn btn-sm btn-ghost" onclick="useDesignedVoiceInVideo('${v.id}')">用于视频</button>
                            <button type="button" class="btn btn-sm btn-ghost" onclick="useDesignedVoiceInSynth('${v.id}')">用于配音</button>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    } catch (e) {
        console.error('加载设计音色库失败:', e);
    }
}

function togglePreviewDesignedVoice(id, url) {
    const key = 'des_' + id;
    if (currentVoicePreviewId === key && currentVoicePreviewAudio) {
        currentVoicePreviewAudio.pause();
        currentVoicePreviewAudio = null;
        currentVoicePreviewId = null;
        loadDesignedVoices();
        return;
    }

    if (currentVoicePreviewAudio) {
        currentVoicePreviewAudio.pause();
    }

    currentVoicePreviewAudio = new Audio(url);
    currentVoicePreviewId = key;
    currentVoicePreviewAudio.play().catch(() => {});
    currentVoicePreviewAudio.onended = () => {
        currentVoicePreviewId = null;
        currentVoicePreviewAudio = null;
        loadDesignedVoices();
    };
    loadDesignedVoices();
}

async function deleteDesignedVoice(voiceId) {
    if (!confirm('确定要删除这个自设计音色吗？')) return;
    try {
        const resp = await fetch(`/api/designed-voices/${voiceId}`, { method: 'DELETE' });
        if (resp.ok) {
            showToast('音色已删除', 'success');
            await loadDesignedVoices();
            await updateAllVoiceDropdowns();
        } else {
            showToast('删除失败', 'error');
        }
    } catch (e) {
        showToast('删除失败', 'error');
    }
}

function useDesignedVoiceInVideo(voiceId) {
    const videoNav = document.querySelector('.nav-item[data-tab="video"]');
    if (videoNav) videoNav.click();
    const select = document.getElementById('videoVoiceSelect');
    if (select) {
        select.value = 'designed:' + voiceId;
    }
    const changeSelect = document.getElementById('videoChangeVoiceSelect');
    if (changeSelect) {
        changeSelect.value = 'designed:' + voiceId;
    }
    showToast('已选中该设计音色作为视频配音', 'info');
}

function useDesignedVoiceInSynth(voiceId) {
    const synthNav = document.querySelector('.nav-item[data-tab="synthesize"]');
    if (synthNav) synthNav.click();
    const ttsModelOpt = document.querySelector('.model-option[data-model="mimo-v2.5-tts"]');
    if (ttsModelOpt) ttsModelOpt.click();
    const voiceSelect = document.getElementById('voiceSelect');
    if (voiceSelect) {
        voiceSelect.value = 'designed:' + voiceId;
    }
    showToast('已切换至语音合成并应用该设计音色', 'info');
}

// ============================================================
// 文生视频项目管理 & 一键更换配音
// ============================================================

function setupProjectNameInput() {
    const input = document.getElementById('videoProjectNameInput');
    if (input) {
        input.addEventListener('focus', () => { input.dataset.focused = 'true'; });
        input.addEventListener('blur', () => { input.dataset.focused = 'false'; });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                saveCurrentVideoProject();
            }
        });
    }

    // 模态弹窗点击外部与 ESC 键关闭
    window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeProjectListModal();
    });
    const modal = document.getElementById('projectListModal');
    if (modal) {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) closeProjectListModal();
        });
    }
}

async function saveCurrentVideoProject() {
    if (!currentVideoJobId) {
        showToast('当前没有进行中的视频工程', 'warn');
        return;
    }
    const nameInput = document.getElementById('videoProjectNameInput');
    const projectName = nameInput ? nameInput.value.trim() : '';
    if (!projectName) {
        showToast('请输入工程名称', 'warn');
        return;
    }

    try {
        const resp = await fetch(`/api/video/jobs/${currentVideoJobId}/save`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_name: projectName })
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) throw new Error(data.error || '保存项目失败');
        showToast(`工程名称已更新为「${projectName}」`, 'success');
        await loadProjectCount();
    } catch (e) {
        showToast(e.message || '保存失败', 'error');
    }
}

async function loadProjectCount() {
    const badge = document.getElementById('videoProjectCount');
    if (!badge) return;
    try {
        const resp = await fetch('/api/video/jobs');
        const data = await resp.json();
        const jobs = data.jobs || [];
        badge.textContent = jobs.length;
    } catch (e) {}
}

async function openProjectListModal() {
    const modal = document.getElementById('projectListModal');
    const body = document.getElementById('projectModalListBody');
    if (!modal || !body) return;

    modal.classList.add('show');
    modal.classList.add('active');
    body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--gray-500);"><span style="display:inline-block;width:16px;height:16px;border:2px solid currentColor;border-top-color:transparent;border-radius:50%;animation:spin 1s linear infinite;margin-right:8px;vertical-align:middle"></span> 正在读取视频工程...</div>';

    try {
        const resp = await fetch('/api/video/jobs');
        const data = await resp.json();
        const jobs = data.jobs || [];

        if (jobs.length === 0) {
            body.innerHTML = '<div style="text-align:center;padding:40px;color:var(--gray-500);">暂无历史视频工程，在上方输入文章并生成视频后将自动保存到此处。</div>';
            return;
        }

        body.innerHTML = `
            <div class="project-list-container">
                ${jobs.map(job => {
                    const isCurrent = job.job_id === currentVideoJobId;
                    const name = job.project_name || (job.scenes && job.scenes[0] ? job.scenes[0].title : '') || job.job_id;
                    const thumb = job.thumbnail_url || (job.scenes && job.scenes[0] ? (job.scenes[0].image_url || job.scenes[0].frame_url) : '');
                    const statusText = mapVideoJobStatus(job.status);
                    const dur = job.duration ? `${Math.round(job.duration)}秒` : (job.scenes_count ? `${job.scenes_count}分镜` : '');

                    return `
                        <div class="project-item ${isCurrent ? 'active-job' : ''}">
                            <div class="project-thumb">
                                ${thumb ? `<img src="${thumb}" alt="封面">` : `<span style="font-size:24px;">🎬</span>`}
                            </div>
                            <div class="project-meta">
                                <div class="project-meta-title" title="${escapeHtml(name)}">
                                    ${escapeHtml(name)}
                                    ${isCurrent ? '<span style="font-size:11px;background:#dbeafe;color:#1d4ed8;padding:1px 6px;border-radius:4px;margin-left:6px;">当前工程</span>' : ''}
                                </div>
                                <div class="project-meta-sub">
                                    <span>状态: <strong>${statusText}</strong></span>
                                    ${dur ? `<span>时长: ${dur}</span>` : ''}
                                    <span>时间: ${formatTime(job.created_at)}</span>
                                </div>
                            </div>
                            <div class="project-actions">
                                <button class="btn btn-primary btn-sm" onclick="loadVideoJob('${job.job_id}')">
                                    ${isCurrent ? '刷新查看' : '打开工程'}
                                </button>
                                <button class="btn btn-outline btn-sm" style="color:var(--danger,#ef4444);border-color:#fca5a5;" title="删除工程" onclick="deleteVideoProject('${job.job_id}')">
                                    删除
                                </button>
                            </div>
                        </div>
                    `;
                }).join('')}
            </div>
        `;
    } catch (e) {
        body.innerHTML = `<div style="text-align:center;padding:30px;color:var(--danger-color,#ef4444);">加载项目列表失败: ${escapeHtml(e.message)}</div>`;
    }
}

function closeProjectListModal() {
    const modal = document.getElementById('projectListModal');
    if (modal) {
        modal.classList.remove('show');
        modal.classList.remove('active');
    }
}

async function loadVideoJob(jobId) {
    try {
        const resp = await fetch(`/api/video/jobs/${jobId}`);
        const data = await resp.json();
        if (!resp.ok || !data.job) throw new Error(data.error || '工程不存在');

        currentVideoJobId = jobId;
        document.getElementById('videoJobCard').classList.remove('hidden');
        document.getElementById('storyboardCard').classList.remove('hidden');
        document.getElementById('videoPlayerCard').classList.remove('hidden');
        renderVideoJob(data.job);

        closeProjectListModal();
        showToast(`已加载工程: ${data.job.project_name || jobId}`, 'info');

        if (['pending', 'planning', 'generating_assets', 'building_subtitles', 'rendering_video'].includes(data.job.status)) {
            startVideoPolling(jobId);
        }
    } catch (e) {
        showToast(e.message || '加载工程失败', 'error');
    }
}

async function deleteVideoProject(jobId) {
    if (!confirm('确定要删除此视频工程吗？所有分镜、图片和成片文件将被永久移除。')) return;

    try {
        const resp = await fetch(`/api/video/jobs/${jobId}`, { method: 'DELETE' });
        if (!resp.ok) throw new Error('删除失败');

        showToast('工程已成功删除', 'success');
        if (currentVideoJobId === jobId) {
            currentVideoJobId = null;
            document.getElementById('videoJobCard').classList.add('hidden');
            document.getElementById('storyboardCard').classList.add('hidden');
            document.getElementById('videoPlayerCard').classList.add('hidden');
        }
        await loadProjectCount();
        openProjectListModal();
    } catch (e) {
        showToast(e.message || '删除失败', 'error');
    }
}

async function changeCurrentVideoVoice() {
    if (!currentVideoJobId) {
        showToast('当前没有视频工程', 'warn');
        return;
    }
    const select = document.getElementById('videoChangeVoiceSelect');
    const newVoice = select ? select.value : '';
    if (!newVoice) {
        showToast('请选择要更换的配音音色', 'warn');
        return;
    }

    const btn = document.getElementById('videoChangeVoiceBtn');
    const origHtml = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span style="display:inline-block;width:12px;height:12px;border:2px solid #fff;border-top-color:transparent;border-radius:50%;animation:spin 1s linear infinite;margin-right:4px;vertical-align:middle"></span> 更换配音中...`;

    try {
        const resp = await fetch(`/api/video/jobs/${currentVideoJobId}/change-voice`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ voice: newVoice })
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.error || '更换配音失败');
        }

        showToast('已启动重新配音与合成（0 生图消耗，保留现有所有画面）', 'success');
        startVideoPolling(currentVideoJobId);
    } catch (e) {
        showToast(e.message || '更换配音失败', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = origHtml;
    }
}

async function saveStoryboardEdits() {
    if (!currentVideoJobId) {
        showToast('当前没有视频工程', 'warn');
        return false;
    }
    const sceneItems = document.querySelectorAll('.storyboard-scene-item[data-scene-index]');
    if (!sceneItems.length) {
        showToast('未找到可编辑的分镜', 'warn');
        return false;
    }

    const scenes = [];
    sceneItems.forEach(item => {
        const idx = parseInt(item.dataset.sceneIndex, 10);
        const textarea = item.querySelector('.storyboard-scene-text-edit');
        const text = textarea ? textarea.value.trim() : '';
        scenes.push({
            scene_index: idx,
            subtitle_text: text,
            narration_text: text
        });
    });

    try {
        const resp = await fetch(`/api/video/jobs/${currentVideoJobId}/storyboard`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scenes })
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) throw new Error(data.error || '保存分镜失败');
        showToast('分镜台词已保存', 'success');
        return true;
    } catch (e) {
        showToast(e.message || '保存分镜台词失败', 'error');
        return false;
    }
}

async function rerenderVideoFromStoryboard() {
    if (!currentVideoJobId) {
        showToast('当前没有视频工程', 'warn');
        return;
    }
    const saved = await saveStoryboardEdits();
    if (!saved) return;

    try {
        const resp = await fetch(`/api/video/jobs/${currentVideoJobId}/rerender`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) throw new Error(data.error || '重新渲染失败');
        showToast('已开始重新合成视频...', 'success');
        startVideoPolling(currentVideoJobId);
    } catch (e) {
        showToast(e.message || '重新渲染失败', 'error');
    }
}

// ============================================================
// 文生动画 (Text-to-Animation) 前端逻辑
// ============================================================

function setupAnimThemeSelector() {
    const cards = document.querySelectorAll('#animThemeSelector .theme-card');
    cards.forEach(c => {
        c.addEventListener('click', function() {
            cards.forEach(item => item.classList.remove('active'));
            this.classList.add('active');
        });
    });
}

function getSelectedAnimTheme() {
    const active = document.querySelector('#animThemeSelector .theme-card.active');
    return active ? active.dataset.theme : 'cyber_dark';
}

async function loadAnimProjectCount() {
    try {
        const resp = await fetch('/api/animation/jobs');
        const data = await resp.json();
        const countEl = document.getElementById('animProjectCount');
        if (countEl && data.jobs) {
            countEl.textContent = data.jobs.length;
        }
    } catch (e) {
        console.warn('获取动画工程数量失败:', e);
    }
}

async function startAnimationJob() {
    const text = document.getElementById('animTextInput').value.trim();
    if (!text) {
        showToast('请输入文章或脚本内容', 'warn');
        return;
    }

    const projectName = document.getElementById('animProjectNameInput').value.trim();
    const voice = document.getElementById('animVoiceSelect').value || 'mimo_default';
    const theme = getSelectedAnimTheme();
    const resolution = document.getElementById('animResolutionSelect').value || '1920x1080';
    const fps = parseInt(document.getElementById('animFpsSelect').value || '30', 10);

    const btn = document.getElementById('animStartBtn');
    btn.disabled = true;
    btn.innerHTML = '正在提交并规划分镜...';

    // 重置并显示进度卡片
    document.getElementById('animProgressCard').classList.remove('hidden');
    document.getElementById('animProgressBarFill').style.width = '5%';
    document.getElementById('animProgressPercent').textContent = '5%';
    document.getElementById('animProgressMessage').textContent = '正在通过大模型进行智能分镜规划...';

    // 启动计时器
    currentAnimStartTime = Date.now();
    if (currentAnimTimerInterval) clearInterval(currentAnimTimerInterval);
    const timerEl = document.getElementById('animProgressTimer');
    currentAnimTimerInterval = setInterval(() => {
        const sec = Math.floor((Date.now() - currentAnimStartTime) / 1000);
        if (timerEl) timerEl.textContent = sec + 's';
    }, 1000);

    try {
        const resp = await fetch('/api/animation/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                text,
                project_name: projectName,
                voice,
                theme,
                resolution,
                fps
            })
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            throw new Error(data.error || '创建动画任务失败');
        }

        currentAnimJobId = data.job_id;
        showToast('文生动画任务已启动！正在并发渲染...', 'success');
        startAnimPolling(currentAnimJobId);
    } catch (e) {
        showToast(e.message || '启动失败', 'error');
        btn.disabled = false;
        btn.innerHTML = `
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            🚀 开始生成文生动画 (0 Token 极速压制)
        `;
        if (currentAnimTimerInterval) clearInterval(currentAnimTimerInterval);
    }
}

function startAnimPolling(jobId) {
    if (currentAnimPollTimer) clearInterval(currentAnimPollTimer);
    currentAnimJobId = jobId;

    // 立即拉取一次
    fetch(`/api/animation/jobs/${jobId}`)
        .then(r => r.json())
        .then(data => { if (data.job) renderAnimJob(data.job); })
        .catch(() => {});

    currentAnimPollTimer = setInterval(async () => {
        try {
            const resp = await fetch(`/api/animation/jobs/${jobId}`);
            const data = await resp.json();
            if (!resp.ok || !data.job) return;

            renderAnimJob(data.job);

            if (data.job.status === 'completed' || data.job.status === 'failed') {
                clearInterval(currentAnimPollTimer);
                currentAnimPollTimer = null;
                if (currentAnimTimerInterval) {
                    clearInterval(currentAnimTimerInterval);
                    currentAnimTimerInterval = null;
                }
            }
        } catch (e) {
            console.warn('轮询动画任务异常:', e);
        }
    }, 1500);
}

function renderAnimJob(job) {
    const fill = document.getElementById('animProgressBarFill');
    const pct = document.getElementById('animProgressPercent');
    const msg = document.getElementById('animProgressMessage');

    const progressPct = Math.min(100, Math.round((job.progress || 0) * 100));
    if (fill) fill.style.width = progressPct + '%';
    if (pct) pct.textContent = progressPct + '%';
    if (msg && job.detail_message) msg.textContent = job.detail_message;

    // 阶段指示器高亮
    const stages = ['planning', 'synthesizing_audio', 'rendering_video', 'completed'];
    const curIdx = stages.indexOf(job.stage || job.status);
    document.querySelectorAll('#animProgressCard .stage-item').forEach(el => {
        const s = el.dataset.stage;
        const sIdx = stages.indexOf(s);
        if (sIdx <= curIdx || curIdx === 3) {
            el.classList.add('active');
        } else {
            el.classList.remove('active');
        }
    });

    if (job.status === 'completed') {
        // 展示播放卡片与分镜卡片
        document.getElementById('animPlayerCard').classList.remove('hidden');
        document.getElementById('animStoryboardCard').classList.remove('hidden');

        const player = document.getElementById('animVideoPlayer');
        if (player && job.video_url) {
            if (!player.src.endsWith(job.video_url)) {
                player.src = job.video_url;
            }
        }

        const dlVideo = document.getElementById('animDownloadVideoBtn');
        if (dlVideo && job.video_url) dlVideo.href = job.video_url;

        const dlSrt = document.getElementById('animDownloadSrtBtn');
        if (dlSrt && job.srt_url) dlSrt.href = job.srt_url;

        const durBadge = document.getElementById('animDurationBadge');
        if (durBadge) durBadge.textContent = (job.duration_sec || 0).toFixed(1) + 's';

        const nameInput = document.getElementById('animProjectNameInput');
        if (nameInput && job.project_name) nameInput.value = job.project_name;

        // 填充一键换音的初始值
        const changeSelect = document.getElementById('animChangeVoiceSelect');
        if (changeSelect && job.config && job.config.voice) {
            changeSelect.value = job.config.voice;
        }

        if (job.storyboard) {
            renderAnimStoryboardGrid(job.storyboard);
        }

        const btn = document.getElementById('animStartBtn');
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                🚀 开始生成文生动画 (0 Token 极速压制)
            `;
        }
        loadAnimProjectCount();
    } else if (job.status === 'failed') {
        showToast(`动画生成失败: ${job.error || job.detail_message}`, 'error');
        const btn = document.getElementById('animStartBtn');
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                🚀 开始生成文生动画 (0 Token 极速压制)
            `;
        }
    }
}

function renderAnimStoryboardGrid(storyboard) {
    currentAnimStoryboard = storyboard;
    const grid = document.getElementById('animStoryboardGrid');
    if (!grid) return;

    grid.innerHTML = '';
    const scenes = storyboard.scenes || [];
    if (scenes.length === 0) {
        grid.innerHTML = '<div style="color:var(--gray-500);grid-column:1/-1;text-align:center;padding:20px;">暂无分镜剧本数据</div>';
        return;
    }

    const typeNames = {
        cards_grid: '亮点卡片',
        comparison: '红绿对比',
        data_chart: '走势图表',
        steps_flow: '步骤向导',
        metrics_grid: '指标仪表',
        code_terminal: '代码终端',
        quote_focus: '观点聚焦',
        call_to_action: '行动呼吁'
    };

    scenes.forEach(sc => {
        const card = document.createElement('div');
        card.className = 'anim-scene-card';

        const typeLabel = typeNames[sc.type] || sc.type;
        const tagText = sc.tag || `分镜 0${sc.scene_index}`;

        card.innerHTML = `
            <div class="anim-scene-card-header">
                <span class="anim-scene-index-badge">#${sc.scene_index}</span>
                <span class="anim-scene-type-tag ${sc.type}">${typeLabel} · ${tagText}</span>
            </div>
            <div class="anim-scene-title">${escapeHtml(sc.title || '')}</div>
            ${sc.subtitle ? `<div class="anim-scene-subtitle">${escapeHtml(sc.subtitle)}</div>` : ''}
            <div class="anim-scene-text">${escapeHtml(sc.narration_text || '')}</div>
            <div class="anim-scene-footer">
                <span>⏱️ 时长: ${(sc.duration || 0).toFixed(1)}s</span>
                ${sc.audio_url ? `
                    <button class="btn btn-ghost btn-sm" onclick="playSceneAudio('${sc.audio_url}', this)" style="padding:2px 8px;font-size:12px;">
                        ▶️ 试听原声
                    </button>
                ` : ''}
            </div>
        `;
        grid.appendChild(card);
    });
}

let activeSceneAudio = null;
function playSceneAudio(url, btn) {
    if (activeSceneAudio) {
        activeSceneAudio.pause();
        activeSceneAudio = null;
    }
    const audio = new Audio(url);
    activeSceneAudio = audio;
    if (btn) btn.textContent = '⏸️ 正在播放...';
    audio.play();
    audio.onended = () => {
        if (btn) btn.textContent = '▶️ 试听原声';
        activeSceneAudio = null;
    };
    audio.onerror = () => {
        if (btn) btn.textContent = '▶️ 试听原声';
        showToast('音频播放失败', 'error');
    };
}

async function changeCurrentAnimVoice() {
    if (!currentAnimJobId) {
        showToast('当前没有进行中的动画工程', 'warn');
        return;
    }
    const voice = document.getElementById('animChangeVoiceSelect').value;
    if (!voice) {
        showToast('请选择新音色', 'warn');
        return;
    }

    const btn = document.getElementById('animChangeVoiceBtn');
    btn.disabled = true;
    btn.innerHTML = '正在重混流配音...';

    // 重新展开进度卡片
    document.getElementById('animProgressCard').classList.remove('hidden');
    document.getElementById('animProgressBarFill').style.width = '20%';
    document.getElementById('animProgressPercent').textContent = '20%';
    document.getElementById('animProgressMessage').textContent = `正在为各分镜重新生成配音并重新渲染...`;

    try {
        const resp = await fetch(`/api/animation/jobs/${currentAnimJobId}/change-voice`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ voice })
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) throw new Error(data.error || '更换配音失败');

        showToast('已提交一键换音任务！正在极速混流...', 'success');
        startAnimPolling(currentAnimJobId);
    } catch (e) {
        showToast(e.message || '换配音失败', 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = `
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
            更换配音并混流
        `;
    }
}

async function saveCurrentAnimProject() {
    if (!currentAnimJobId) {
        showToast('尚未创建动画项目', 'warn');
        return;
    }
    const projectName = document.getElementById('animProjectNameInput').value.trim();
    if (!projectName) {
        showToast('项目名称不能为空', 'warn');
        return;
    }

    try {
        const resp = await fetch(`/api/animation/jobs/${currentAnimJobId}/save`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_name: projectName })
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) throw new Error(data.error || '保存失败');
        showToast('动画项目名称已保存', 'success');
        loadAnimProjectCount();
    } catch (e) {
        showToast(e.message || '保存失败', 'error');
    }
}

// ------------------------------------------------------------
// 动画工程列表模态框
// ------------------------------------------------------------
async function openAnimProjectsModal() {
    const modal = document.getElementById('animProjectListModal');
    const body = document.getElementById('animProjectModalListBody');
    if (!modal || !body) return;

    modal.classList.add('active');
    body.innerHTML = '<div style="text-align:center;padding:30px;color:var(--gray-500);">正在读取本地动画工程...</div>';

    try {
        const resp = await fetch('/api/animation/jobs');
        const data = await resp.json();
        const jobs = data.jobs || [];

        if (jobs.length === 0) {
            body.innerHTML = `
                <div class="empty-state">
                    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="1.5"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>
                    <p>暂无本地动画工程</p>
                    <span style="font-size:12px;color:var(--gray-400);">在文生动画页面点击“开始生成”即可自动创建工程</span>
                </div>
            `;
            return;
        }

        body.innerHTML = '';
        jobs.forEach(p => {
            const item = document.createElement('div');
            item.className = 'project-item';

            const createdTime = p.created_at ? new Date(p.created_at).toLocaleString() : '未知时间';
            const statusBadge = p.status === 'completed'
                ? '<span style="color:#10b981;font-weight:600;">已就绪</span>'
                : (p.status === 'failed' ? '<span style="color:#ef4444;">失败</span>' : '<span style="color:#3b82f6;">渲染中...</span>');

            item.innerHTML = `
                <div class="project-thumb" style="background:#0f172a;display:flex;align-items:center;justify-content:center;color:#38bdf8;">
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>
                </div>
                <div class="project-meta">
                    <div class="project-meta-title">${escapeHtml(p.project_name)}</div>
                    <div class="project-meta-sub">
                        <span>🕒 ${createdTime}</span>
                        <span>⏱️ ${(p.duration_sec || 0).toFixed(1)}s</span>
                        <span>🎨 主题: ${p.theme}</span>
                        <span>状态: ${statusBadge}</span>
                    </div>
                </div>
                <div class="project-actions">
                    <button class="btn btn-outline btn-sm" onclick="loadAnimJobById('${p.job_id}')">打开工程</button>
                    <button class="btn btn-ghost btn-sm" style="color:var(--red-500);" onclick="deleteAnimJob('${p.job_id}', event)">删除</button>
                </div>
            `;
            body.appendChild(item);
        });
    } catch (e) {
        body.innerHTML = `<div style="color:var(--red-500);text-align:center;padding:20px;">加载工程失败: ${e.message}</div>`;
    }
}

function closeAnimProjectsModal() {
    const modal = document.getElementById('animProjectListModal');
    if (modal) modal.classList.remove('active');
}

async function loadAnimJobById(jobId) {
    closeAnimProjectsModal();
    currentAnimJobId = jobId;

    showToast('正在载入动画工程...', 'info');
    try {
        const resp = await fetch(`/api/animation/jobs/${jobId}`);
        const data = await resp.json();
        if (!resp.ok || !data.job) throw new Error(data.error || '读取失败');

        renderAnimJob(data.job);
        if (data.job.status !== 'completed' && data.job.status !== 'failed') {
            startAnimPolling(jobId);
        }
        showToast(`已加载工程: ${data.job.project_name}`, 'success');
    } catch (e) {
        showToast(e.message || '加载工程失败', 'error');
    }
}

async function deleteAnimJob(jobId, event) {
    if (event) event.stopPropagation();
    if (!confirm('确定删除此动画工程及其全部渲染资产吗？此操作不可恢复。')) return;

    try {
        const resp = await fetch(`/api/animation/jobs/${jobId}`, { method: 'DELETE' });
        const data = await resp.json();
        if (!resp.ok || !data.success) throw new Error(data.error || '删除失败');
        showToast('动画工程已删除', 'success');
        openAnimProjectsModal();
        loadAnimProjectCount();
    } catch (e) {
        showToast(e.message || '删除失败', 'error');
    }
}

// ------------------------------------------------------------
// 分镜微调模态框与重新渲染
// ------------------------------------------------------------
function openAnimEditAllModal() {
    if (!currentAnimStoryboard || !currentAnimStoryboard.scenes) {
        showToast('当前无分镜数据可编辑', 'warn');
        return;
    }
    const modal = document.getElementById('animEditAllModal');
    const container = document.getElementById('animEditScenesContainer');
    if (!modal || !container) return;

    container.innerHTML = '';
    const scenes = currentAnimStoryboard.scenes;

    const typeOptions = [
        { id: 'cards_grid', name: '亮点卡片矩阵 (cards_grid)' },
        { id: 'comparison', 'name': '红绿痛点优势对比 (comparison)' },
        { id: 'data_chart', 'name': '动态行情与走势折线 (data_chart)' },
        { id: 'steps_flow', 'name': '向导操作步骤流 (steps_flow)' },
        { id: 'metrics_grid', 'name': '核心指标大数字仪表盘 (metrics_grid)' },
        { id: 'code_terminal', 'name': '极客终端打字机 (code_terminal)' },
        { id: 'quote_focus', 'name': '震撼名言金句聚焦 (quote_focus)' },
        { id: 'call_to_action', 'name': '尾声行动号召与开源 (call_to_action)' }
    ];

    scenes.forEach(sc => {
        const card = document.createElement('div');
        card.className = 'anim-edit-item-card';
        card.dataset.sceneIndex = sc.scene_index;

        let optionsHtml = '';
        typeOptions.forEach(opt => {
            const sel = (sc.type === opt.id) ? 'selected' : '';
            optionsHtml += `<option value="${opt.id}" ${sel}>${opt.name}</option>`;
        });

        card.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <strong style="color:var(--blue-600);">分镜 #${sc.scene_index}</strong>
                <div style="display:flex;align-items:center;gap:8px;">
                    <label style="font-size:12px;color:var(--gray-600);">版式类型:</label>
                    <select class="form-select scene-type-input" style="height:32px;font-size:12px;padding:2px 8px;">
                        ${optionsHtml}
                    </select>
                </div>
            </div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
                <div>
                    <label style="font-size:12px;color:var(--gray-600);">主标题:</label>
                    <input type="text" class="form-input scene-title-input" value="${escapeHtml(sc.title || '')}" style="height:32px;font-size:13px;">
                </div>
                <div>
                    <label style="font-size:12px;color:var(--gray-600);">胶囊标签:</label>
                    <input type="text" class="form-input scene-tag-input" value="${escapeHtml(sc.tag || '')}" style="height:32px;font-size:13px;">
                </div>
            </div>
            <div>
                <label style="font-size:12px;color:var(--gray-600);">配音与字幕台词 (精确发音):</label>
                <textarea class="form-textarea scene-text-input" rows="3" style="font-size:13px;">${escapeHtml(sc.narration_text || '')}</textarea>
            </div>
        `;
        container.appendChild(card);
    });

    modal.classList.add('active');
}

function closeAnimEditAllModal() {
    const modal = document.getElementById('animEditAllModal');
    if (modal) modal.classList.remove('active');
}

async function saveAnimScenesEdits() {
    if (!currentAnimJobId) {
        showToast('当前没有动画工程', 'warn');
        return;
    }

    const cards = document.querySelectorAll('#animEditScenesContainer .anim-edit-item-card');
    const scenes = [];

    cards.forEach(c => {
        const sIdx = parseInt(c.dataset.sceneIndex, 10);
        const type = c.querySelector('.scene-type-input').value;
        const title = c.querySelector('.scene-title-input').value.trim();
        const tag = c.querySelector('.scene-tag-input').value.trim();
        const text = c.querySelector('.scene-text-input').value.trim();

        scenes.push({
            scene_index: sIdx,
            type,
            title,
            tag,
            narration_text: text
        });
    });

    closeAnimEditAllModal();
    showToast('正在更新分镜配置并启动重新渲染...', 'info');

    try {
        const resp = await fetch(`/api/animation/jobs/${currentAnimJobId}/update-scenes`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ scenes })
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) throw new Error(data.error || '更新失败');

        // 重新调用重新渲染接口
        await rerenderAnimFromStoryboard();
    } catch (e) {
        showToast(e.message || '分镜保存失败', 'error');
    }
}

async function rerenderAnimFromStoryboard() {
    if (!currentAnimJobId) {
        showToast('当前没有动画工程', 'warn');
        return;
    }

    // 展开进度卡片
    document.getElementById('animProgressCard').classList.remove('hidden');
    document.getElementById('animProgressBarFill').style.width = '15%';
    document.getElementById('animProgressPercent').textContent = '15%';
    document.getElementById('animProgressMessage').textContent = '正在根据最新剧本重新合成音频与流式渲染...';

    try {
        const resp = await fetch(`/api/animation/jobs/${currentAnimJobId}/rerender`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) throw new Error(data.error || '重新渲染失败');

        showToast('重新渲染任务已提交！', 'success');
        startAnimPolling(currentAnimJobId);
    } catch (e) {
        showToast(e.message || '重新渲染失败', 'error');
    }
}

function auditionAnimVoice() {
    const voice = document.getElementById('animVoiceSelect').value;
    if (!voice) {
        showToast('请选择要试听的音色', 'warn');
        return;
    }
    auditionVoice(voice);
}

function copyAnimVideoLink() {
    const player = document.getElementById('animVideoPlayer');
    if (!player || !player.src) {
        showToast('成片链接尚未就绪', 'warn');
        return;
    }
    navigator.clipboard.writeText(player.src).then(() => {
        showToast('视频成片链接已复制到剪贴板！', 'success');
    }).catch(() => {
        showToast(player.src, 'info');
    });
}

function fillSampleAnimText() {
    const sample = `欢迎体验全新的开源 AI 音视频创作神器！

这是一款完全免费、开箱即用的音视频生产工具。无论你是自媒体博主、教程讲师还是开发者，无需昂贵的显卡，也不用购买付费 API，一键即可生成专业级高保真视听内容。

系统深度集成了小米官方 MiMo TTS 全套高保真音色，不仅支持数十种预置音色，还内置独创的音色设计与声音克隆，只需一句话描述或几秒音频，专属人声即刻诞生。

以往做视频，生图排队漫长、Token 费用昂贵、画面文字模糊畸变。而全新的文生动画功能，采用纯代码矢量流式压制，实现真正的 0 图像 Token 消耗，几十秒极速输出 1080P 超高清动画，字字锐利如矢量，毫厘毕现！

上手更是极其简单：只需把你的文案脚本粘贴进来，大模型将自动拆解分镜，智能编排 8 大高质感科技动效，并并发合成配音。成片还支持一键极速换音，想换什么音色就换什么音色！

全套项目代码已在 GitHub 完全开源免费，欢迎大家前往 Star 收藏支持！你在使用中有什么好点子或新需求？非常欢迎在评论区交流讨论！`;

    const textarea = document.getElementById('animTextInput');
    if (textarea) {
        textarea.value = sample;
        updateCounter('animTextInput', 'animCharCount', null);
        showToast('已填入测试宣传文案（突出免费、易用与评论区交流）', 'info');
    }
}


