const Dashboard = (function () {
  const API_BASE = '/api/v1';
  const WS_BASE = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}${API_BASE}`;

  const state = {
    currentTaskId: null,
    ws: null,
    wsConnected: false,
    autoScroll: true,
    activeTab: 'logs',
    tasks: [],
    currentNode: null,
    events: [],
    generatedCode: '',
    reviewResult: '',
    metrics: {},
    hitlTaskId: null,
    stageMetrics: {},
    auditLog: [],
    isReplaying: false,
    replayEvents: [],
    replayIndex: -1,
    replayTimer: null,
    paletteOpen: false,
    paletteSelected: 0,
    systemStatus: 'idle', // idle | running | done | failed
  };

  const NODES = [
    { id: 'requirement', label: '需求分析', color: '#06B6D4', icon: 'R', y: 0.12 },
    { id: 'code_gen', label: '代码生成', color: '#D946EF', icon: 'C', y: 0.30 },
    { id: 'review', label: '代码评审', color: '#F6C445', icon: 'V', y: 0.48 },
    { id: 'test_gen', label: '测试生成', color: '#06B6D4', icon: 'T', y: 0.66 },
    { id: 'done', label: '交付', color: '#F6C445', icon: 'D', y: 0.84 },
  ];

  const EXAMPLE_PROMPTS = {
    lru: '用 Python 实现一个 LRU Cache，支持 get/put 操作，O(1) 复杂度',
    api: '用 FastAPI 实现一个用户注册 / 登录 / JWT 认证的 REST API',
    scraper: '用 Python 写一个异步新闻标题爬虫，支持限速与错误重试',
  };

  let particles = [];
  let activeNodeIdx = -1;
  let animFrame;
  let metricsInterval = null;

  /* ============================================================
     初始化
     ============================================================ */
  function init() {
    initCanvas();
    initAgentFlow();
    initTokenStream();
    initAuditPanel();
    initReplayControls();
    initMobileToggle();
    initThemeToggle();
    initCommandPalette();
    initKeyboardShortcuts();
    loadExistingTasks();
    startMetricsPolling();
    updateFlowStatusDisplay();
    updateEmptyState();
    updateStatusChips();
  }

  /* ============================================================
     Canvas 粒子与节点可视化
     ============================================================ */
  function initCanvas() {
    const canvas = document.getElementById('graphCanvas');
    if (!canvas) return;
    canvas.width = canvas.parentElement.clientWidth;
    canvas.height = canvas.parentElement.clientHeight;
    window.addEventListener('resize', () => {
      canvas.width = canvas.parentElement.clientWidth;
      canvas.height = canvas.parentElement.clientHeight;
    });
    for (let i = 0; i < 50; i++) {
      particles.push({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        r: Math.random() * 1.5 + 0.5,
        vx: (Math.random() - 0.5) * 0.3,
        vy: (Math.random() - 0.5) * 0.3,
      });
    }
    animate();
  }

  function animate() {
    const canvas = document.getElementById('graphCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const cx = canvas.width / 2;

    particles.forEach(p => {
      p.x += p.vx; p.y += p.vy;
      if (p.x < 0 || p.x > canvas.width) p.vx *= -1;
      if (p.y < 0 || p.y > canvas.height) p.vy *= -1;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(6, 182, 212, 0.15)';
      ctx.fill();
    });

    for (let i = 0; i < NODES.length - 1; i++) {
      const a = NODES[i], b = NODES[i + 1];
      const y1 = a.y * canvas.height, y2 = b.y * canvas.height;
      const active = i === activeNodeIdx;
      if (active) {
        for (let j = 0; j < 3; j++) {
          const t = ((Date.now() / 800 + j * 0.33) % 1);
          const y = y1 + (y2 - y1) * t;
          ctx.beginPath();
          ctx.arc(cx, y, 3, 0, Math.PI * 2);
          ctx.fillStyle = a.color;
          ctx.fill();
        }
      }
      ctx.beginPath();
      ctx.moveTo(cx, y1 + 28);
      ctx.lineTo(cx, y2 - 28);
      ctx.strokeStyle = active ? `${a.color}44` : 'rgba(255,255,255,0.08)';
      ctx.lineWidth = active ? 2 : 1;
      ctx.setLineDash(active ? [] : [4, 4]);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    NODES.forEach((n, idx) => {
      const x = cx, y = n.y * canvas.height;
      const isActive = idx === activeNodeIdx;
      const isDone = idx < activeNodeIdx;
      const r = isActive ? 30 : 26;
      if (isActive) {
        ctx.beginPath();
        ctx.arc(x, y, r + 8, 0, Math.PI * 2);
        ctx.fillStyle = `${n.color}15`;
        ctx.fill();
      }
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = isActive ? n.color : isDone ? `${n.color}66` : '#141420';
      ctx.strokeStyle = isActive ? n.color : isDone ? `${n.color}aa` : 'rgba(255,255,255,0.1)';
      ctx.lineWidth = isActive ? 2 : 1;
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = isActive ? '#fff' : isDone ? n.color : 'rgba(180,180,200,0.42)';
      ctx.font = "12px 'Geist', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(n.icon, x, y);
      ctx.fillStyle = isActive ? n.color : isDone ? `${n.color}aa` : 'rgba(180,180,200,0.42)';
      ctx.font = `${isActive ? '12px' : '11px'} 'Geist', system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`;
      ctx.fillText(n.label, x, y + r + 16);
    });

    animFrame = requestAnimationFrame(animate);
  }

  /* ============================================================
     Agent Flow / Token Stream / Audit / Replay 初始化
     ============================================================ */
  function initAgentFlow() {
    AgentFlow.init('agentFlowSvg', {
      onNodeClick: (nodeId, state) => {
        const node = NODES.find(n => n.id === nodeId);
        if (node) {
          addAuditLog('info', nodeId, `节点 ${node.label} 被点击`, { nodeId, state });
        }
      }
    });
  }

  function initTokenStream() {
    TokenStream.init('tokenStreamPanel', {
      onToken: (token, total) => {
        if (total % 50 === 0) {
          addAuditLog('info', 'token_stream', `已接收 ${total} tokens`);
        }
      }
    });
  }

  function initAuditPanel() {
    addAuditLog('info', 'system', 'Dashboard 已初始化');
    document.querySelectorAll('.audit-filter-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.audit-filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        filterAuditLogs(btn.dataset.filter);
      });
    });
  }

  function initReplayControls() {
    const playBtn = document.getElementById('replayPlay');
    const pauseBtn = document.getElementById('replayPause');
    const stopBtn = document.getElementById('replayStop');
    const slider = document.getElementById('replaySlider');

    if (playBtn) playBtn.addEventListener('click', startReplay);
    if (pauseBtn) pauseBtn.addEventListener('click', pauseReplay);
    if (stopBtn) stopBtn.addEventListener('click', stopReplay);
    if (slider) slider.addEventListener('input', (e) => seekReplay(parseInt(e.target.value)));

    updateReplayUI();
  }

  function initMobileToggle() {
    const toggle = document.getElementById('mobileToggle');
    const overlay = document.getElementById('sidebarOverlay');
    const sidebar = document.querySelector('.sidebar');
    if (toggle) {
      toggle.addEventListener('click', () => {
        sidebar.classList.toggle('open');
        overlay.classList.toggle('active');
      });
    }
    if (overlay) {
      overlay.addEventListener('click', () => {
        sidebar.classList.remove('open');
        overlay.classList.remove('active');
      });
    }
  }

  function initThemeToggle() {
    const toggle = document.getElementById('themeToggle');
    if (!toggle) return;

    const saved = localStorage.getItem('dashboard-theme');
    if (saved === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
      updateThemeIcon('light');
    }

    toggle.addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-theme');
      const next = current === 'light' ? 'dark' : 'light';
      if (next === 'light') {
        document.documentElement.setAttribute('data-theme', 'light');
      } else {
        document.documentElement.removeAttribute('data-theme');
      }
      localStorage.setItem('dashboard-theme', next);
      updateThemeIcon(next);
    });
  }

  function updateThemeIcon(theme) {
    const toggle = document.getElementById('themeToggle');
    if (toggle) {
      toggle.innerHTML = theme === 'light'
        ? '🌙 <span>暗色</span>'
        : '☀️ <span>亮色</span>';
    }
  }

  /* ============================================================
     状态芯片与空状态
     ============================================================ */
  function updateStatusChips() {
    const systemChip = document.getElementById('systemStatusChip');
    const wsChip = document.getElementById('wsStatusChip');

    if (systemChip) {
      const dot = systemChip.querySelector('.status-dot');
      const text = systemChip.querySelector('.status-text');
      systemChip.className = 'status-chip';
      dot.className = 'status-dot';

      if (state.systemStatus === 'running') {
        systemChip.classList.add('active');
        dot.classList.add('running');
        text.textContent = '执行中';
      } else if (state.systemStatus === 'done') {
        systemChip.classList.add('online');
        dot.classList.add('done');
        text.textContent = '已完成';
      } else if (state.systemStatus === 'failed') {
        systemChip.classList.add('error');
        dot.classList.add('failed');
        text.textContent = '执行失败';
      } else {
        dot.classList.add('pending');
        text.textContent = '系统待机';
      }
    }

    if (wsChip) {
      const dot = wsChip.querySelector('.status-dot');
      const text = wsChip.querySelector('.status-text');
      wsChip.className = 'status-chip';
      dot.className = 'status-dot';

      if (state.wsConnected) {
        wsChip.classList.add('online');
        dot.classList.add('done');
        text.textContent = 'WS 在线';
      } else {
        dot.classList.add('pending');
        text.textContent = 'WS 未连接';
      }
    }
  }

  function setSystemStatus(status) {
    state.systemStatus = status;
    updateStatusChips();
  }

  function updateEmptyState() {
    const panel = document.getElementById('graphPanel');
    const overlay = document.getElementById('noTaskOverlay');
    if (!panel) return;

    const isEmpty = !state.currentTaskId;
    panel.classList.toggle('is-empty', isEmpty);
    if (overlay) overlay.style.display = isEmpty ? 'flex' : 'none';
  }

  /* ============================================================
     审计日志
     ============================================================ */
  function addAuditLog(level, node, message, data) {
    const entry = {
      id: Date.now() + Math.random(),
      time: new Date(),
      level,
      node,
      message,
      data: data || null,
    };
    state.auditLog.push(entry);
    if (state.auditLog.length > 500) {
      state.auditLog.shift();
    }
    renderAuditLog(entry);
  }

  function renderAuditLog(entry) {
    const list = document.getElementById('auditLogList');
    if (!list) return;

    const item = document.createElement('div');
    item.className = 'audit-log-item';
    item.innerHTML = `
      <span class="audit-time">${entry.time.toLocaleTimeString('en-GB')}</span>
      <span class="audit-level ${entry.level}">${entry.level.toUpperCase()}</span>
      <span class="audit-msg">
        <span class="audit-node">[${entry.node}]</span> ${entry.message}
      </span>
    `;
    list.insertBefore(item, list.firstChild);

    while (list.children.length > 200) {
      list.removeChild(list.lastChild);
    }
  }

  function filterAuditLogs(level) {
    const list = document.getElementById('auditLogList');
    if (!list) return;
    const items = list.querySelectorAll('.audit-log-item');
    items.forEach(item => {
      const levelBadge = item.querySelector('.audit-level');
      if (!level || level === 'all') {
        item.style.display = '';
      } else {
        item.style.display = levelBadge && levelBadge.textContent.toLowerCase().includes(level) ? '' : 'none';
      }
    });
  }

  /* ============================================================
     WebSocket
     ============================================================ */
  function connectWS(taskId) {
    disconnectWS();
    const url = `${WS_BASE}/ws/pipeline/${taskId}`;
    state.ws = new WebSocket(url);

    state.ws.onopen = () => {
      state.wsConnected = true;
      updateWSStatus('connected');
      updateStatusChips();
      state.ws.send(JSON.stringify({ type: 'subscribe' }));
      addAuditLog('success', 'websocket', `已连接到任务 ${taskId}`);
    };

    state.ws.onclose = () => {
      state.wsConnected = false;
      updateWSStatus('disconnected');
      updateStatusChips();
      addAuditLog('warning', 'websocket', 'WebSocket 连接已关闭');
    };

    state.ws.onerror = () => {
      updateWSStatus('error');
      updateStatusChips();
      addAuditLog('error', 'websocket', 'WebSocket 连接错误');
    };

    state.ws.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      if (msg.type === 'ping') return;
      handleEvent(msg);
    };
  }

  function disconnectWS() {
    if (state.ws) {
      state.ws.close();
      state.ws = null;
    }
  }

  function updateWSStatus(status) {
    const el = document.getElementById('wsStatusLegacy');
    if (!el) return;
    const map = {
      connected: '<span class="status-dot running"></span>已连接',
      disconnected: '<span class="status-dot pending"></span>未连接',
      error: '<span class="status-dot failed"></span>连接错误',
    };
    el.innerHTML = map[status] || map.disconnected;
  }

  /* ============================================================
     事件处理
     ============================================================ */
  function handleEvent(msg) {
    if (msg.type !== 'event') return;
    state.events.push(msg);
    state.replayEvents.push(msg);

    const stage = msg.stage;
    const data = msg.data || {};

    const nodeStatusMap = {
      node_running: 'running',
      node_completed: 'completed',
      node_failed: 'failed',
      hitl_waiting: 'hitl',
      pipeline_done: 'completed',
      pipeline_failed: 'failed',
    };

    const stageIdMap = {
      node_running: data.node_name,
      node_completed: data.node_name,
      node_failed: data.node_name,
      hitl_waiting: 'review',
      pipeline_done: 'done',
      pipeline_failed: 'done',
    };

    if (stage === 'node_running') {
      const nodeId = mapNodeNameToId(data.node_name);
      if (nodeId) {
        AgentFlow.setNodeState(nodeId, 'running');
        const idx = NODES.findIndex(n => n.id === nodeId);
        if (idx >= 0) activeNodeIdx = idx;
        updateStageMetric(nodeId, 'running');
      }
      if (data.token_stream && data.token_stream.length) {
        TokenStream.addBatch(data.token_stream);
      }
      setSystemStatus('running');
    }

    if (stage === 'node_completed') {
      const nodeId = mapNodeNameToId(data.node_name);
      if (nodeId) {
        AgentFlow.setNodeState(nodeId, 'completed');
        updateStageMetric(nodeId, 'completed');
      }
    }

    if (stage === 'node_failed') {
      const nodeId = mapNodeNameToId(data.node_name);
      if (nodeId) {
        AgentFlow.setNodeState(nodeId, 'failed');
        updateStageMetric(nodeId, 'failed');
      }
    }

    if (stage === 'pipeline_done') {
      AgentFlow.setNodeState('done', 'completed');
      activeNodeIdx = NODES.length - 1;
      TokenStream.stop();
      addAuditLog('success', 'pipeline', 'Pipeline 执行完成');
      updateTaskStatus(msg.task_id, 'done');
      setSystemStatus('done');
      document.getElementById('noTaskOverlay').style.display = 'none';
    }

    if (stage === 'pipeline_failed') {
      AgentFlow.setNodeState('done', 'failed');
      TokenStream.stop();
      addAuditLog('error', 'pipeline', 'Pipeline 执行失败');
      updateTaskStatus(msg.task_id, 'failed');
      setSystemStatus('failed');
      document.getElementById('noTaskOverlay').style.display = 'none';
    }

    if (stage === 'hitl_waiting') {
      state.hitlTaskId = msg.task_id;
      AgentFlow.setNodeState('review', 'hitl');
      document.getElementById('approvalBar').classList.add('visible');
      document.getElementById('approvalMsg').textContent =
        `人工审批: ${data.reason || '生成的代码需要审查确认'}`;
      addAuditLog('warning', 'hitl', '等待人工审批');
    }

    if (stage === 'hitl_resolved') {
      document.getElementById('approvalBar').classList.remove('visible');
      addAuditLog('info', 'hitl', '审批已解决');
    }

    if (data.generated_code) {
      state.generatedCode = data.generated_code;
    }
    if (data.review_summary || data.verdict) {
      state.reviewResult = JSON.stringify({
        verdict: data.verdict,
        summary: data.review_summary,
        score: data.score,
      }, null, 2);
    }
    if (data.test_code) {
      state.generatedCode += '\n\n# --- Generated Tests ---\n' + data.test_code;
    }

    refreshPanel();
    updateFlowStatusDisplay();
    updateStatusChips();
  }

  function updateFlowStatusDisplay() {
    const el = document.getElementById('flowStatus');
    if (!el) return;
    if (activeNodeIdx >= 0 && activeNodeIdx < NODES.length) {
      el.textContent = `${NODES[activeNodeIdx].label} · ${activeNodeIdx + 1}/${NODES.length}`;
    } else {
      el.textContent = 'IDLE';
    }
    const fmStage = document.getElementById('fmStage');
    const fmProgress = document.getElementById('fmProgress');
    const fmElapsed = document.getElementById('fmElapsed');
    if (fmStage) fmStage.textContent = activeNodeIdx >= 0 ? NODES[activeNodeIdx].icon : '-';
    if (fmProgress) fmProgress.textContent = activeNodeIdx >= 0 ? `${Math.round((activeNodeIdx + 1) / NODES.length * 100)}%` : '-';
    if (fmElapsed) {
      const runningNode = NODES[activeNodeIdx];
      if (runningNode && state.stageMetrics[runningNode.id]) {
        const m = state.stageMetrics[runningNode.id];
        const startT = m.startTime;
        if (startT) {
          fmElapsed.textContent = `${((Date.now() - startT) / 1000).toFixed(1)}s`;
        } else {
          fmElapsed.textContent = '-';
        }
      } else {
        fmElapsed.textContent = '-';
      }
    }
  }

  function mapNodeNameToId(name) {
    if (!name) return null;
    const lower = name.toLowerCase();
    for (const n of NODES) {
      if (lower.includes(n.id) || lower.includes(n.label)) return n.id;
    }
    return null;
  }

  function updateStageMetric(nodeId, status) {
    const existing = state.stageMetrics[nodeId] || {};
    state.stageMetrics[nodeId] = {
      ...existing,
      status,
      updatedAt: Date.now(),
    };
    if (status === 'running' && !existing.startTime) {
      state.stageMetrics[nodeId].startTime = Date.now();
    }
    if ((status === 'completed' || status === 'failed') && existing.startTime) {
      state.stageMetrics[nodeId].duration = Date.now() - existing.startTime;
    }
    renderStageMetrics();
  }

  function renderStageMetrics() {
    const container = document.getElementById('stageMetrics');
    if (!container) return;
    container.innerHTML = '';
    NODES.forEach(n => {
      const m = state.stageMetrics[n.id] || { status: 'pending' };
      const div = document.createElement('div');
      div.className = `stage-metric ${m.status || 'pending'}`;
      const duration = m.duration ? `${(m.duration / 1000).toFixed(1)}s` : (m.status === 'running' ? '...' : '-');
      div.innerHTML = `
        <div class="sm-stage">${n.label}</div>
        <div class="sm-val">${m.status ? m.status.toUpperCase() : 'PENDING'}</div>
        <div class="sm-dur">耗时: ${duration}</div>
      `;
      container.appendChild(div);
    });
  }

  /* ============================================================
     任务提交与切换
     ============================================================ */
  async function submitTask() {
    const input = document.getElementById('promptInput');
    const text = input.value.trim();
    if (!text) return;

    const btn = document.getElementById('submitBtn');
    btn.disabled = true;
    btn.textContent = '提交中...';

    try {
      const resp = await fetch(`${API_BASE}/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_request: text, skill_id: 'general' }),
      });
      const result = await resp.json();
      if (!resp.ok) throw new Error(result.detail || '请求失败');

      const taskId = result.task_id || result.id;
      state.currentTaskId = taskId;
      state.events = [];
      state.replayEvents = [];
      state.generatedCode = '';
      state.reviewResult = '';
      state.metrics = {};
      state.stageMetrics = {};
      activeNodeIdx = 0;

      document.getElementById('taskIdDisplay').textContent = `Task: ${taskId}`;
      document.getElementById('headerTitle').textContent = 'Pipeline 执行中...';
      document.getElementById('noTaskOverlay').style.display = 'none';

      addTaskToList(taskId, text);

      connectWS(taskId);

      TokenStream.start();
      AgentFlow.reset();
      NODES.forEach(n => updateStageMetric(n.id, 'pending'));

      input.value = '';
      switchTab('logs');
      updateEmptyState();
      setSystemStatus('running');
      addAuditLog('info', 'task', `任务已提交: ${text.substring(0, 50)}`);
    } catch (err) {
      addAuditLog('error', 'task', '提交失败: ' + err.message);
      alert('提交失败: ' + err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = '运行 Pipeline';
    }
  }

  function runExample(key) {
    const prompt = EXAMPLE_PROMPTS[key];
    if (!prompt) return;
    const input = document.getElementById('promptInput');
    if (input) input.value = prompt;
    submitTask();
  }

  function addTaskToList(taskId, prompt) {
    const list = document.getElementById('taskList');
    if (list.querySelector('.task-empty-hint') && list.children.length === 1) {
      list.innerHTML = '';
    }
    const el = document.createElement('div');
    el.className = 'task-item active';
    el.dataset.taskId = taskId;
    el.innerHTML = `
      <div class="title">${prompt.substring(0, 40)}${prompt.length > 40 ? '...' : ''}</div>
      <div class="meta"><span class="status-dot running"></span>执行中</div>
    `;
    el.onclick = () => switchTask(taskId);
    list.prepend(el);
  }

  function switchTask(taskId) {
    document.querySelectorAll('.task-item').forEach(e => e.classList.remove('active'));
    const el = document.querySelector(`[data-task-id="${taskId}"]`);
    if (el) el.classList.add('active');
    state.currentTaskId = taskId;
    document.getElementById('taskIdDisplay').textContent = `Task: ${taskId}`;
    document.getElementById('noTaskOverlay').style.display = 'none';
    connectWS(taskId);
    updateEmptyState();
    addAuditLog('info', 'task', `切换到任务 ${taskId}`);
  }

  async function approveHITL(approved) {
    if (!state.hitlTaskId) return;
    try {
      const resp = await fetch(`${API_BASE}/tasks/${state.hitlTaskId}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved, reason: approved ? '人工批准' : '人工拒绝' }),
      });
      const result = await resp.json();
      document.getElementById('approvalBar').classList.remove('visible');
      state.hitlTaskId = null;
      addAuditLog(approved ? 'success' : 'warning', 'hitl', approved ? '审批已批准' : '审批已拒绝');
      if (!resp.ok) alert('审批失败: ' + (result.detail || ''));
    } catch (err) {
      addAuditLog('error', 'hitl', '审批失败: ' + err.message);
      alert('审批失败: ' + err.message);
    }
  }

  /* ============================================================
     标签页
     ============================================================ */
  function switchTab(tab) {
    state.activeTab = tab;
    document.querySelectorAll('.panel-tab').forEach((t, i) => {
      t.classList.remove('active');
      t.setAttribute('aria-selected', 'false');
    });
    const tabs = document.querySelectorAll('.panel-tab');
    const idx = { logs: 0, code: 1, metrics: 2, review: 3, audit: 4 }[tab];
    if (idx !== undefined && idx < tabs.length) {
      tabs[idx].classList.add('active');
      tabs[idx].setAttribute('aria-selected', 'true');
    }
    refreshPanel();
  }

  function refreshPanel() {
    const container = document.getElementById('panelContent');
    if (!container) return;

    if (state.activeTab === 'logs') {
      if (state.events.length === 0) {
        container.innerHTML = '<div class="panel-empty">等待事件...</div>';
        return;
      }
      container.innerHTML = state.events.slice().reverse().map((e) => {
        const cls = {
          node_running: 'node-start',
          node_completed: 'node-done',
          node_failed: 'node-error',
          hitl_waiting: 'hitl',
        }[e.stage] || '';
        const icon = {
          node_running: '▶',
          node_completed: '✓',
          node_failed: '✗',
          hitl_waiting: '⏸',
          pipeline_done: '✓',
          pipeline_failed: '✗',
        }[e.stage] || '·';
        const label = e.data?.node_name || e.stage || 'event';
        return `<div class="log-entry ${cls}">
          <span class="log-time">${icon}</span> ${label}
          ${e.data?.error ? '<div style="color:var(--danger);margin-top:4px">' + e.data.error + '</div>' : ''}
          ${e.data?.verdict ? '<div style="color:var(--gold);margin-top:4px">评审: ' + e.data.verdict + '</div>' : ''}
        </div>`;
      }).join('');
      if (state.autoScroll) {
        container.scrollTop = container.scrollHeight;
      }
    }

    if (state.activeTab === 'code') {
      if (!state.generatedCode) {
        container.innerHTML = '<div class="panel-empty">代码将在生成阶段显示</div>';
      } else {
        container.innerHTML = `<div class="code-output">${escapeHtml(state.generatedCode)}</div>`;
      }
    }

    if (state.activeTab === 'metrics') {
      const completedCount = Object.values(state.stageMetrics).filter(m => m.status === 'completed').length;
      const failedCount = Object.values(state.stageMetrics).filter(m => m.status === 'failed').length;
      const runningCount = Object.values(state.stageMetrics).filter(m => m.status === 'running').length;
      container.innerHTML = `
        <div class="metric-grid">
          <div class="metric-card"><div class="label">执行阶段</div><div class="value" style="color:var(--cyan)">${activeNodeIdx + 1}/${NODES.length}</div></div>
          <div class="metric-card"><div class="label">事件数</div><div class="value" style="color:var(--magenta-200)">${state.events.length}</div></div>
          <div class="metric-card"><div class="label">已完成</div><div class="value" style="color:var(--success)">${completedCount}</div></div>
          <div class="metric-card"><div class="label">失败</div><div class="value" style="color:var(--danger)">${failedCount}</div></div>
          <div class="metric-card"><div class="label">执行中</div><div class="value" style="color:var(--gold)">${runningCount}</div></div>
          <div class="metric-card"><div class="label">WS 连接</div><div class="value" style="font-size:13px;color:${state.wsConnected ? 'var(--cyan)' : 'var(--text-faint)'}">${state.wsConnected ? '在线' : '离线'}</div></div>
        </div>
        <div id="stageMetrics" style="margin-top:16px;"></div>
      `;
      renderStageMetrics();
    }

    if (state.activeTab === 'review') {
      if (!state.reviewResult) {
        container.innerHTML = '<div class="panel-empty">评审结果将在评审阶段显示</div>';
      } else {
        container.innerHTML = `<div class="code-output">${escapeHtml(state.reviewResult)}</div>`;
      }
    }

    if (state.activeTab === 'audit') {
      const logs = state.auditLog.slice(-50).reverse();
      container.innerHTML = `
        <div style="margin-bottom:12px;display:flex;gap:6px;">
          <button class="btn small" onclick="window.__dashboard.clearAudit()" aria-label="清空审计日志">清空</button>
          <button class="btn small" onclick="window.__dashboard.exportAudit()" aria-label="导出审计日志">导出</button>
        </div>
        <div class="audit-log-list">
          ${logs.map(l => `
            <div class="audit-log-item">
              <span class="audit-time">${l.time.toLocaleTimeString('en-GB')}</span>
              <span class="audit-level ${l.level}">${l.level.toUpperCase()}</span>
              <span class="audit-msg"><span class="audit-node">[${l.node}]</span> ${escapeHtml(l.message)}</span>
            </div>
          `).join('') || '<div class="panel-empty">暂无审计日志</div>'}
        </div>
      `;
    }
  }

  function escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
  }

  function toggleAutoScroll() {
    state.autoScroll = !state.autoScroll;
    document.getElementById('autoScrollBtn').textContent =
      `Auto Scroll: ${state.autoScroll ? 'ON' : 'OFF'}`;
  }

  function updateTaskStatus(taskId, status) {
    const items = document.querySelectorAll('.task-item');
    items.forEach(el => {
      if (el.dataset.taskId === taskId) {
        const dot = el.querySelector('.status-dot');
        dot.className = `status-dot ${status}`;
        const meta = el.querySelector('.meta');
        if (meta) meta.textContent += ` | ${status === 'done' ? '已完成' : '失败'}`;
      }
    });
  }

  async function loadExistingTasks() {
    try {
      const resp = await fetch(`${API_BASE}/tasks?limit=10`);
      if (resp.ok) {
        const data = await resp.json();
        const tasks = data.tasks || data || [];
        if (tasks.length > 0) {
          const list = document.getElementById('taskList');
          list.innerHTML = '';
          tasks.forEach(t => {
            const el = document.createElement('div');
            el.className = 'task-item';
            el.dataset.taskId = t.id || t.task_id;
            const status = t.status || 'pending';
            el.innerHTML = `
              <div class="title">${(t.user_request || t.title || 'Unknown').substring(0, 40)}</div>
              <div class="meta"><span class="status-dot ${status}"></span>${status}</div>
            `;
            el.onclick = () => switchTask(t.id || t.task_id);
            list.appendChild(el);
          });
        }
      }
    } catch (e) {
      addAuditLog('warning', 'init', '无法加载历史任务 (离线模式)');
    }
  }

  function startMetricsPolling() {
    if (metricsInterval) clearInterval(metricsInterval);
    metricsInterval = setInterval(() => {
      if (state.currentTaskId) {
        fetchMetrics();
      }
    }, 5000);
  }

  async function fetchMetrics() {
    if (!state.currentTaskId) return;
    try {
      const resp = await fetch(`${API_BASE}/tasks/${state.currentTaskId}/metrics`);
      if (resp.ok) {
        const data = await resp.json();
        state.metrics = data;
        renderStageMetrics();
      }
    } catch (e) {
      // silently fail
    }
  }

  /* ============================================================
     回放
     ============================================================ */
  function startReplay() {
    if (state.replayEvents.length === 0) return;
    if (state.isReplaying) return;

    state.isReplaying = true;
    state.replayIndex = -1;
    const slider = document.getElementById('replaySlider');
    if (slider) slider.max = state.replayEvents.length - 1;

    addAuditLog('info', 'replay', '开始事件回放');

    state.replayTimer = setInterval(() => {
      state.replayIndex++;
      if (state.replayIndex >= state.replayEvents.length) {
        pauseReplay();
        return;
      }
      const slider = document.getElementById('replaySlider');
      if (slider) slider.value = state.replayIndex;
      replayApplyEvent(state.replayEvents[state.replayIndex]);
      updateReplayUI();
    }, 300);
  }

  function pauseReplay() {
    state.isReplaying = false;
    if (state.replayTimer) {
      clearInterval(state.replayTimer);
      state.replayTimer = null;
    }
    updateReplayUI();
  }

  function stopReplay() {
    pauseReplay();
    state.replayIndex = -1;
    const slider = document.getElementById('replaySlider');
    if (slider) slider.value = 0;
    AgentFlow.reset();
    activeNodeIdx = -1;
    updateReplayUI();
    addAuditLog('info', 'replay', '回放已停止');
  }

  function seekReplay(index) {
    pauseReplay();
    state.replayIndex = index;
    AgentFlow.reset();
    for (let i = 0; i <= index && i < state.replayEvents.length; i++) {
      replayApplyEvent(state.replayEvents[i]);
    }
    updateReplayUI();
  }

  function replayApplyEvent(msg) {
    if (msg.type !== 'event') return;
    const stage = msg.stage;
    const data = msg.data || {};

    const nodeId = mapNodeNameToId(data.node_name);
    if (stage === 'node_running' && nodeId) {
      AgentFlow.setNodeState(nodeId, 'running');
      const idx = NODES.findIndex(n => n.id === nodeId);
      if (idx >= 0) activeNodeIdx = idx;
    } else if (stage === 'node_completed' && nodeId) {
      AgentFlow.setNodeState(nodeId, 'completed');
    } else if (stage === 'node_failed' && nodeId) {
      AgentFlow.setNodeState(nodeId, 'failed');
    } else if (stage === 'pipeline_done') {
      AgentFlow.setNodeState('done', 'completed');
    } else if (stage === 'pipeline_failed') {
      AgentFlow.setNodeState('done', 'failed');
    } else if (stage === 'hitl_waiting') {
      AgentFlow.setNodeState('review', 'hitl');
    }
  }

  function updateReplayUI() {
    const playBtn = document.getElementById('replayPlay');
    const pauseBtn = document.getElementById('replayPause');
    const info = document.getElementById('replayInfo');
    if (!info) return;

    if (state.isReplaying) {
      info.innerHTML = '<span class="replay-status">回放中</span>';
      if (playBtn) playBtn.disabled = true;
      if (pauseBtn) pauseBtn.disabled = false;
    } else if (state.replayIndex >= 0) {
      info.innerHTML = `<span class="replay-status">暂停</span> ${state.replayIndex + 1}/${state.replayEvents.length}`;
      if (playBtn) playBtn.disabled = false;
      if (pauseBtn) pauseBtn.disabled = true;
    } else {
      info.innerHTML = `就绪 ${state.replayEvents.length} 事件`;
      if (playBtn) playBtn.disabled = state.replayEvents.length === 0;
      if (pauseBtn) pauseBtn.disabled = true;
    }
  }

  /* ============================================================
     审计操作
     ============================================================ */
  function clearAudit() {
    state.auditLog = [];
    const list = document.getElementById('auditLogList');
    if (list) list.innerHTML = '';
    addAuditLog('info', 'audit', '审计日志已清空');
  }

  function exportAudit() {
    const data = JSON.stringify(state.auditLog, null, 2);
    const blob = new Blob([data], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `audit-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
    addAuditLog('success', 'audit', '审计日志已导出');
  }

  /* ============================================================
     Command Palette
     ============================================================ */
  function getCommands() {
    const taskItems = Array.from(document.querySelectorAll('.task-item')).map(el => ({
      id: `task:${el.dataset.taskId}`,
      name: `切换任务: ${el.querySelector('.title')?.textContent || el.dataset.taskId}`,
      desc: '切换到该历史任务',
      icon: '⇄',
      keywords: 'task switch 切换 任务',
      action: () => switchTask(el.dataset.taskId),
    }));

    return [
      {
        id: 'tab:logs',
        name: '查看 Agent 日志',
        desc: '切换到日志标签页',
        icon: '📋',
        keywords: 'logs 日志',
        action: () => switchTab('logs'),
      },
      {
        id: 'tab:code',
        name: '查看生成代码',
        desc: '切换到代码标签页',
        icon: '💻',
        keywords: 'code 代码',
        action: () => switchTab('code'),
      },
      {
        id: 'tab:metrics',
        name: '查看指标',
        desc: '切换到指标标签页',
        icon: '📊',
        keywords: 'metrics 指标',
        action: () => switchTab('metrics'),
      },
      {
        id: 'tab:review',
        name: '查看评审',
        desc: '切换到评审标签页',
        icon: '🔍',
        keywords: 'review 评审',
        action: () => switchTab('review'),
      },
      {
        id: 'tab:audit',
        name: '查看审计',
        desc: '切换到审计标签页',
        icon: '🛡',
        keywords: 'audit 审计',
        action: () => switchTab('audit'),
      },
      {
        id: 'example:lru',
        name: '运行示例: LRU Cache',
        desc: '提交 LRU Cache 实现需求',
        icon: '⚡',
        keywords: 'example lru cache 示例',
        action: () => runExample('lru'),
      },
      {
        id: 'example:api',
        name: '运行示例: REST API',
        desc: '提交 FastAPI 认证接口需求',
        icon: '⚡',
        keywords: 'example api rest 示例',
        action: () => runExample('api'),
      },
      {
        id: 'example:scraper',
        name: '运行示例: Web Scraper',
        desc: '提交异步爬虫需求',
        icon: '⚡',
        keywords: 'example scraper spider 示例 爬虫',
        action: () => runExample('scraper'),
      },
      {
        id: 'autoScroll',
        name: '切换自动滚动',
        desc: `${state.autoScroll ? '关闭' : '开启'} 日志自动滚动`,
        icon: '⇅',
        keywords: 'autoscroll 自动滚动',
        action: () => toggleAutoScroll(),
      },
      {
        id: 'clearAudit',
        name: '清空审计日志',
        desc: '清除当前审计记录',
        icon: '🗑',
        keywords: 'clear audit 清空 审计',
        action: () => { clearAudit(); switchTab('audit'); },
      },
      {
        id: 'exportAudit',
        name: '导出审计日志',
        desc: '下载审计记录为 JSON',
        icon: '⬇',
        keywords: 'export audit 导出 审计',
        action: () => exportAudit(),
      },
      {
        id: 'toggleTheme',
        name: '切换主题',
        desc: '在深色 / 浅色主题间切换',
        icon: '☀',
        keywords: 'theme 主题 切换',
        action: () => document.getElementById('themeToggle')?.click(),
      },
      ...taskItems,
    ];
  }

  function initCommandPalette() {
    const input = document.getElementById('commandPaletteInput');
    const list = document.getElementById('commandPaletteList');

    if (input) {
      input.addEventListener('input', (e) => filterCommands(e.target.value));
      input.addEventListener('keydown', handlePaletteKeydown);
    }
    if (list) {
      list.addEventListener('click', (e) => {
        const item = e.target.closest('.command-palette-item');
        if (item) executeCommand(item.dataset.commandId);
      });
    }

    const backdrop = document.querySelector('.command-palette-backdrop');
    if (backdrop) {
      backdrop.addEventListener('click', closeCommandPalette);
    }
  }

  function toggleCommandPalette() {
    if (state.paletteOpen) {
      closeCommandPalette();
    } else {
      openCommandPalette();
    }
  }

  function openCommandPalette() {
    const palette = document.getElementById('commandPalette');
    const input = document.getElementById('commandPaletteInput');
    if (!palette) return;

    state.paletteOpen = true;
    state.paletteSelected = 0;
    palette.hidden = false;
    filterCommands('');
    if (input) {
      input.value = '';
      setTimeout(() => input.focus(), 10);
    }
  }

  function closeCommandPalette() {
    const palette = document.getElementById('commandPalette');
    if (palette) palette.hidden = true;
    state.paletteOpen = false;
  }

  function filterCommands(query) {
    const list = document.getElementById('commandPaletteList');
    if (!list) return;

    const q = (query || '').toLowerCase().trim();
    const all = getCommands();
    const filtered = q
      ? all.filter(cmd =>
          cmd.name.toLowerCase().includes(q) ||
          cmd.desc.toLowerCase().includes(q) ||
          cmd.keywords.toLowerCase().includes(q)
        )
      : all;

    state.paletteFiltered = filtered;
    state.paletteSelected = 0;

    if (filtered.length === 0) {
      list.innerHTML = '<div class="command-palette-empty">未找到匹配命令</div>';
      return;
    }

    list.innerHTML = filtered.map((cmd, idx) => `
      <div class="command-palette-item ${idx === 0 ? 'active' : ''}" data-command-id="${cmd.id}" data-index="${idx}">
        <div class="cmd-left">
          <span class="cmd-icon">${cmd.icon}</span>
          <div>
            <div class="cmd-name">${escapeHtml(cmd.name)}</div>
            <div class="cmd-desc">${escapeHtml(cmd.desc)}</div>
          </div>
        </div>
        <span class="cmd-key">↵</span>
      </div>
    `).join('');
  }

  function executeCommand(id) {
    const cmd = getCommands().find(c => c.id === id);
    if (!cmd) return;
    try {
      cmd.action();
    } catch (err) {
      addAuditLog('error', 'palette', `命令执行失败: ${err.message}`);
    }
    closeCommandPalette();
  }

  function handlePaletteKeydown(e) {
    if (!state.paletteOpen) return;

    const items = document.querySelectorAll('.command-palette-item');
    const max = items.length - 1;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      state.paletteSelected = Math.min(state.paletteSelected + 1, max);
      updatePaletteSelection(items);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      state.paletteSelected = Math.max(state.paletteSelected - 1, 0);
      updatePaletteSelection(items);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const active = items[state.paletteSelected];
      if (active) executeCommand(active.dataset.commandId);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeCommandPalette();
    }
  }

  function updatePaletteSelection(items) {
    items.forEach((item, idx) => {
      item.classList.toggle('active', idx === state.paletteSelected);
    });
    const active = items[state.paletteSelected];
    if (active) active.scrollIntoView({ block: 'nearest' });
  }

  function initKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
      const isMod = e.ctrlKey || e.metaKey;

      // Ctrl/Cmd + K 打开命令面板
      if (isMod && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        toggleCommandPalette();
        return;
      }

      // Esc 关闭命令面板
      if (e.key === 'Escape' && state.paletteOpen) {
        e.preventDefault();
        closeCommandPalette();
      }
    });
  }

  return {
    init,
    submitTask,
    switchTask,
    approveHITL,
    switchTab,
    toggleAutoScroll,
    clearAudit,
    exportAudit,
    runExample,
    toggleCommandPalette,
    filterCommands,
    executeCommand,
    getState: () => state,
  };
})();

window.__dashboard = Dashboard;
