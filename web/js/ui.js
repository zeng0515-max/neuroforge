/* ============================================================
   NeuroForge · UI 交互
   Landing Page 切换、符号循环、磁吸 CTA、Curtain Reveal、
   消息渲染、Agent 状态、任务提交/轮询、模型/技能管理
   ============================================================ */

/* ============================================================
   Landing Page 切换逻辑（已迁移至 index.html 内联脚本，这里仅做兼容）
   ============================================================ */
const landing = document.getElementById('landing');
const app = document.querySelector('.app');

// 使用内联脚本暴露的函数（如果存在），否则兜底
const enterApp = window.__enterApp || function() {
  if (landing && !landing.classList.contains('hidden')) {
    landing.classList.add('hidden');
    if (app) {
      setTimeout(() => { app.classList.add('visible'); }, 400);
    }
  }
};

// 如果内联脚本的绑定失败，这里重新绑定
if (landing && !landing.__bound) {
  landing.addEventListener('click', enterApp);
  landing.__bound = true;
}
// 兜底：如果还没绑定，键盘也能进
if (landing) {
  document.addEventListener('keydown', function() {
    if (!landing.classList.contains('hidden')) enterApp();
  }, { once: true });
}

/* ============================================================
   V5 Group A: Symbol Cycle + Magnetic CTA + Curtain Reveal
   ============================================================ */
// --- T12 符号循环（每 2.2s 切到下一个符号，相位错开） ---
(function initSymbolCycle() {
  const syms = document.querySelectorAll('#landing-eyebrow-2 .sym');
  if (!syms.length) return;
  let tick = 0;
  function step() {
    syms.forEach((el, i) => {
      const pool = (el.dataset.syms || '⇄,⇝,↯').split(',');
      const offset = (i * 2 + 1) % pool.length;
      const idx = ((tick + offset) % pool.length + pool.length) % pool.length;
      const oldText = el.textContent;
      const newText = pool[idx];
      if (oldText !== newText) {
        el.classList.add('cycle');
        setTimeout(() => {
          el.textContent = newText;
          requestAnimationFrame(() => {
            setTimeout(() => el.classList.remove('cycle'), 16);
          });
        }, 110);
      }
    });
    tick++;
  }
  step();
  setInterval(step, 2200);
})();

// --- T7 / Group A 预载：Magnetic CTA 位移（半径 90px，最多 16px 偏移） ---
(function initMagneticCTA() {
  const btn = document.getElementById('landing-hint');
  if (!btn) return;
  let targetX = 0, targetY = 0, curX = 0, curY = 0;
  const R = 90, MAX = 16;
  function onMove(e) {
    const rect = btn.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const dx = e.clientX - cx, dy = e.clientY - cy;
    const d = Math.hypot(dx, dy);
    if (d < R) {
      const k = 1 - d / R;
      targetX = dx / d * k * MAX;
      targetY = dy / d * k * MAX;
    } else {
      targetX = 0; targetY = 0;
    }
  }
  window.addEventListener('mousemove', onMove, { passive: true });
  function raf() {
    curX += (targetX - curX) * 0.22;
    curY += (targetY - curY) * 0.22;
    if (Math.abs(curX) < 0.08 && Math.abs(targetX) < 0.01) curX = 0;
    if (Math.abs(curY) < 0.08 && Math.abs(targetY) < 0.01) curY = 0;
    btn.style.transform = `translate3d(${curX.toFixed(2)}px, ${curY.toFixed(2)}px, 0)`;
    requestAnimationFrame(raf);
  }
  requestAnimationFrame(raf);
})();

// --- T10 CSS Curtain reveal：已由 index.html 内联脚本处理（1.5s 强制移除 + 首次交互触发 + 3.5s 兜底），此处不再重复 ---
// 暴露一个手动移除钩子供外部使用
window.__revealCurtain = function() {
  const c = document.getElementById('load-curtain');
  if (c) c.classList.add('reveal');
};

/* ============================================================
   全局状态
   ============================================================ */
const els = {
  input: document.getElementById('input'),
  sendBtn: document.getElementById('send-btn'),
  newBtn: document.getElementById('new-btn'),
  clearBtn: document.getElementById('clear-btn'),
  history: document.getElementById('history'),
  histCount: document.getElementById('hist-count'),
  messages: document.getElementById('messages'),
  heroEmpty: document.getElementById('hero-empty'),
  terminal: document.getElementById('terminal'),
  phaseTag: document.getElementById('phase-tag'),
  mTokens: document.getElementById('m-tokens'),
  mRetry: document.getElementById('m-retry'),
  statTotal: document.getElementById('stat-total'),
  statPass: document.getElementById('stat-pass'),
  statFail: document.getElementById('stat-fail'),
  statusText: document.getElementById('status-text'),
};
let currentTaskId = null;
let pollingTimer = null;
let heroInited = false;

/* ============================================================
   工具：终端日志
   ============================================================ */
function log(text, type = 'info') {
  const line = document.createElement('div');
  line.className = 'term-line ' + type;
  const t = new Date().toLocaleTimeString('en-GB');
  const prefix = { ok: 'OK', err: 'ERR', warn: 'WRN', info: 'INF' }[type] || 'LOG';
  line.innerHTML = `<span class="t">[${t}]</span><span class="prefix">${prefix}</span>${text}`;
  els.terminal.appendChild(line);
  els.terminal.scrollTop = els.terminal.scrollHeight;
}

/* ============================================================
   工具：Agent 节点状态控制
   ============================================================ */
const AGENT_KEYS = [
  { id: 'requirement',  short: 'req', label: '需求分析' },
  { id: 'code_gen',     short: 'gen', label: '代码生成' },
  { id: 'review',       short: 'rev', label: '智能审查' },
  { id: 'test_gen',     short: 'tes', label: '测试生成' },
];
function setAgent(short, state) {
  const row = document.querySelector(`.agent-row[data-ag="${short}"]`);
  const tag = row ? row.querySelector('[data-as]') : null;
  const map = {
    idle: { cls: 'idle', text: 'IDLE' },
    run:  { cls: 'run',  text: 'RUN'  },
    ok:   { cls: 'ok',   text: 'DONE' },
    err:  { cls: 'err',  text: 'FAIL' },
  }[state];
  if (tag) { tag.className = 'a-state ' + map.cls; tag.textContent = map.text; }

  const info = AGENT_KEYS.find(k => k.short === short);
  if (info) {
    const node = document.querySelector(`.pipe-node[data-agent="${info.id}"]`);
    if (node) {
      node.classList.remove('pending','active','done','error');
      node.classList.add({ run: 'active', ok: 'done', err: 'error', idle: 'pending' }[state]);
    }
  }
  const shortIndex = AGENT_KEYS.findIndex(k => k.short === short);
  if (shortIndex > 0 && state === 'run') {
    const conn = document.getElementById('pc-' + shortIndex);
    if (conn) conn.classList.add('flow');
  }
}
function clearAgents() {
  AGENT_KEYS.forEach(k => setAgent(k.short, 'idle'));
  for (let i = 1; i <= 3; i++) {
    const c = document.getElementById('pc-' + i);
    if (c) c.classList.remove('flow');
  }
  document.querySelectorAll('.pipe-node').forEach(n => {
    n.classList.remove('active','done','error'); n.classList.add('pending');
  });
}
function markAllDone() { AGENT_KEYS.forEach(k => setAgent(k.short, 'ok')); }

/* ============================================================
   消息渲染
   ============================================================ */
function append(elem) {
  if (els.heroEmpty) {
    els.heroEmpty.remove();
    els.heroEmpty = null;
  }
  els.messages.appendChild(elem);
  els.messages.scrollTop = els.messages.scrollHeight;
}
function renderUserMsg(text) {
  const d = document.createElement('div');
  d.className = 'msg';
  d.innerHTML = `
    <div class="msg-avatar u">U</div>
    <div class="msg-body">
      <div class="msg-role">YOU · ${new Date().toLocaleTimeString('en-GB')}</div>
      <div class="msg-content"></div>
    </div>`;
  d.querySelector('.msg-content').textContent = text;
  return d;
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}
function renderAssistantMsg(data) {
  const d = document.createElement('div');
  d.className = 'msg';
  const task = data.task || {};
  const phase = task.phase || 'unknown';
  const status = task.status || '';
  const files = (data.files || []);
  const review = data.review_report || null;
  const test = data.test_report || null;
  const req = data.requirement || null;

  let fs = '';
  if (files.length) {
    fs = `<div class="res-card"><h4>FILES 产物</h4><ul>` +
      files.map(f => {
        const lang = f.path.endsWith('.py') ? 'python' : '';
        return `<li>
          <b>${escapeHtml(f.path)}</b>
          <span style="color:var(--text-faint); margin-left:6px;">(${(f.line_count||0).toLocaleString()} 行)</span>
          <pre class="language-${lang}"><code>${escapeHtml((f.content||'').slice(0,1500))}</code></pre>
        </li>`;
      }).join('') + `</ul></div>`;
  }
  let rv = '';
  if (review) {
    const issues = review.issues || [];
    const sevColor = { critical:'var(--danger)', high:'var(--warn)', medium:'var(--pulse)', low:'var(--text-dim)' };
    const issuesHtml = issues.slice(0, 12).map(i =>
      `<li><b style="color:${sevColor[i.severity]||'var(--text)'}">[${(i.severity||'').toUpperCase()}]</b> ` +
      `<code>${escapeHtml(i.file_path||'')}:${i.line_start||0}</code> ${escapeHtml(i.message||'')}` +
      (i.suggestion ? ` <span style="color:var(--accent)">→ ${escapeHtml(i.suggestion)}</span>` : '') +
      `</li>`
    ).join('');
    rv = `<div class="res-card"><h4>REVIEW 评审</h4>
      <div>结论：<b style="color:${review.verdict==='approved'?'var(--success)':'var(--danger)'}">${review.verdict||''}</b>  ·  问题：${issues.length} 个</div>
      <ul>${issuesHtml || '<li style=\"color:var(--text-faint)\">无</li>'}</ul>
    </div>`;
  }
  let ts = '';
  if (test) {
    const tc = test.case_results || [];
    const tcHtml = tc.map(c => `<li>
      <b style="color:${c.status==='pass'?'var(--success)':'var(--danger)'}">[${(c.status||'').toUpperCase()}]</b>
      ${escapeHtml(c.case_name||'')}
      ${c.duration_ms?`<span style="color:var(--text-faint)">(${c.duration_ms}ms)</span>`:''}
      ${c.error_msg?`<pre style="margin:4px 0 0;">${escapeHtml(c.error_msg)}</pre>`:''}
    </li>`).join('');
    ts = `<div class="res-card"><h4>TEST 测试执行</h4>
      <div>通过 <b style="color:var(--success)">${test.passed||0}</b> / 失败 <b style="color:var(--danger)">${test.failed||0}</b> / 共 ${test.total||0}</div>
      <ul>${tcHtml || '<li style=\"color:var(--text-faint)\">无测试结果</li>'}</ul>
    </div>`;
  }
  const requirementBlock = req ? `<div class="res-card"><h4>REQUIREMENT 需求规格</h4>
    <div><b>${escapeHtml(req.title||'')}</b></div>
    <div style="margin-top:6px; color:var(--text-dim); font-size:11.5px;">${escapeHtml(req.goal||'')}</div>
  </div>` : '';

  const taskId = task.task_id || '';
  const actionBtns = (taskId && files.length) ? `
    <div class="res-card" style="border-color: var(--accent);">
      <h4 style="color: var(--accent);">ACTIONS 操作</h4>
      <div class="action-row">
        <button onclick="executeCode('${taskId}')" style="background:var(--grad-main); color:#fff; border:none; padding:8px 16px; border-radius:8px; cursor:pointer; font-size:12px; font-weight:600;">
          ▶ 执行验证（0 Token）
        </button>
        <button onclick="showModifyBox('${taskId}')" style="background:transparent; color:var(--accent); border:1px solid var(--accent); padding:8px 16px; border-radius:8px; cursor:pointer; font-size:12px; font-weight:600;">
          ✎ 对话修改
        </button>
      </div>
      <div id="modify-box-${taskId}" class="modify-box">
        <textarea id="modify-input-${taskId}" placeholder="输入修改指令，如：把第3行改成类型注解 / 增加参数校验 / 函数名改为 calc"></textarea>
        <div class="btn-row">
          <button onclick="submitModify('${taskId}')" style="background:var(--grad-main); color:#fff; border:none; padding:6px 14px; border-radius:6px; cursor:pointer; font-size:11px;">提交修改</button>
          <button onclick="hideModifyBox('${taskId}')" style="background:transparent; color:var(--text-dim); border:1px solid var(--border); padding:6px 14px; border-radius:6px; cursor:pointer; font-size:11px;">取消</button>
        </div>
      </div>
      <div id="exec-result-${taskId}" class="exec-result"></div>
    </div>` : '';

  d.innerHTML = `
    <div class="msg-avatar a">AI</div>
    <div class="msg-body">
      <div class="msg-role">NEUROFORGE · ${phase.toUpperCase()} · ${status.toUpperCase()}</div>
      <div class="msg-content">
        <h3>任务 ${escapeHtml(task.task_id||'')} 结果</h3>
        <div class="result-grid">
          ${requirementBlock}
          ${rv}
          ${ts}
          ${fs}
          ${actionBtns}
        </div>
      </div>
    </div>`;
  return d;
}

/* ============================================================
   代码执行验证 & 对话修改
   ============================================================ */
async function executeCode(taskId) {
  const box = document.getElementById(`exec-result-${taskId}`);
  if (box) box.innerHTML = `<div style="color:var(--accent); font-size:12px;">⏳ 正在执行验证...（纯本地执行，不消耗 Token）</div>`;
  try {
    const r = await fetch(`/api/v1/tasks/${taskId}/execute`, { method: 'POST' });
    const data = await r.json();
    let html = `<div style="font-size:12px; margin-top:6px;">`;
    html += `<div style="font-weight:600; color:${data.success ? 'var(--success)' : 'var(--danger)'};">
      ${data.success ? '✓' : '✗'} ${escapeHtml(data.summary || '')}
    </div>`;
    if (data.results && data.results.length) {
      html += `<ul style="margin-top:4px; font-size:11px;">`;
      data.results.forEach(r => {
        const ok = r.syntax_ok && r.import_ok;
        const color = ok ? 'var(--success)' : 'var(--danger)';
        html += `<li style="color:${color};">
          <b>${escapeHtml(r.path)}</b>
          语法:${r.syntax_ok ? '✓' : '✗(' + escapeHtml(r.syntax_error) + ')'}
          导入:${r.import_ok ? '✓' : '✗'}
          ${r.run_ok !== undefined && r.path.match(/^(main|app|run|__main__)\.py$/) ? '运行:' + (r.run_ok ? '✓' : '✗') : ''}
          <span style="color:var(--text-faint);">(${r.duration_ms}ms)</span>
        </li>`;
        if (!r.import_ok && r.import_error) {
          html += `<li style="color:var(--danger); font-size:10px; padding-left:16px;">${escapeHtml(r.import_error.slice(0,200))}</li>`;
        }
        if (r.run_stderr && !r.run_ok) {
          html += `<li style="color:var(--danger); font-size:10px; padding-left:16px;">${escapeHtml(r.run_stderr.slice(0,200))}</li>`;
        }
      });
      html += `</ul>`;
    }
    html += `</div>`;
    if (box) box.innerHTML = html;
    log(`代码验证完成: ${data.success ? '通过' : '失败'}`, data.success ? 'ok' : 'err');
  } catch (e) {
    if (box) box.innerHTML = `<div style="color:var(--danger); font-size:12px;">验证失败: ${escapeHtml(e.message)}</div>`;
    log('代码验证失败: ' + e.message, 'err');
  }
}

function showModifyBox(taskId) {
  const box = document.getElementById(`modify-box-${taskId}`);
  if (box) { box.style.display = 'block'; box.querySelector('textarea').focus(); }
}
function hideModifyBox(taskId) {
  const box = document.getElementById(`modify-box-${taskId}`);
  if (box) { box.style.display = 'none'; const ta = box.querySelector('textarea'); if(ta) ta.value=''; }
}

async function submitModify(taskId) {
  const input = document.getElementById(`modify-input-${taskId}`);
  const instruction = (input?.value || '').trim();
  if (!instruction) return;

  const box = document.getElementById(`exec-result-${taskId}`);
  if (box) box.innerHTML = `<div style="color:var(--accent); font-size:12px;">⏳ 正在修改代码...（消耗少量 Token）</div>`;
  hideModifyBox(taskId);

  try {
    const r = await fetch(`/api/v1/tasks/${taskId}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instruction, auto_verify: true }),
    });
    const data = await r.json();

    let html = `<div style="font-size:12px; margin-top:6px;">`;
    html += `<div style="font-weight:600; color:${data.success ? 'var(--success)' : 'var(--danger)'};">
      ${data.success ? '✓' : '✗'} ${escapeHtml(data.summary || data.error || '')}
    </div>`;

    if (data.files && data.files.length) {
      html += `<ul style="margin-top:4px; font-size:11px;">`;
      data.files.forEach(f => {
        const color = f.modified ? 'var(--accent)' : 'var(--text-faint)';
        html += `<li style="color:${color};">
          <b>${escapeHtml(f.path)}</b> ${f.modified ? '（已修改）' : '（未变）'}
        </li>`;
      });
      html += `</ul>`;
    }

    if (data.verify_result) {
      const vr = data.verify_result;
      const vColor = vr.success ? 'var(--success)' : 'var(--danger)';
      html += `<div style="margin-top:6px; color:${vColor}; font-size:11px;">
        验证: ${vr.success ? '✓ 通过' : '✗ ' + escapeHtml(vr.summary || '失败')}
      </div>`;
    }

    html += `</div>`;
    if (box) box.innerHTML = html;

    log(`代码修改完成: ${data.success ? '成功' : '失败'}`, data.success ? 'ok' : 'err');

    if (data.success) {
      setTimeout(() => viewTask(taskId), 500);
    }
  } catch (e) {
    if (box) box.innerHTML = `<div style="color:var(--danger); font-size:12px;">修改失败: ${escapeHtml(e.message)}</div>`;
    log('代码修改失败: ' + e.message, 'err');
  }
}

/* ============================================================
   监控数据更新
   ============================================================ */
function updateMetrics(data) {
  const task = data.task || {};
  els.phaseTag.textContent = (task.phase || 'IDLE') + ' · ' + (task.status || '');
  els.phaseTag.textContent = els.phaseTag.textContent.toUpperCase();
  els.mTokens.textContent = (task.total_tokens || 0).toLocaleString();
  els.mRetry.textContent = data.retry_counter || 0;
}

/* ============================================================
   任务历史
   ============================================================ */
const PHASE_LABEL = {
  pending: 'Pending', requirement: 'Requirement', code_gen: 'CodeGen',
  review: 'Review', test_gen: 'TestGen', done: 'Done', failed: 'Failed',
};
function fmtTime(s) {
  if (!s) return '';
  const d = new Date(s);
  if (isNaN(d)) return '';
  const now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return Math.floor(diff) + 's 前';
  if (diff < 3600) return Math.floor(diff/60) + 'm 前';
  if (diff < 86400) return Math.floor(diff/3600) + 'h 前';
  return d.toLocaleDateString();
}
async function loadHistory(forceRefresh = false) {
  try {
    const url = forceRefresh ? `/api/v1/tasks?_t=${Date.now()}` : '/api/v1/tasks';
    const r = await fetch(url, { cache: 'no-cache' });
    const data = await r.json();
    const items = data.items || [];
    els.histCount.textContent = items.length;
    els.history.innerHTML = '';
    if (!items.length) {
      els.history.innerHTML = `<div class="empty-hist">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M12 8v4l3 3 M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0z"/></svg>
        <div>暂无历史任务</div><div style="margin-top:4px;">在下方输入框创建第一个任务</div>
      </div>`;
      els.statTotal.textContent = 0; els.statPass.textContent = 0; els.statFail.textContent = 0;
      return;
    }
    let pass = 0, fail = 0;
    items.forEach(it => {
      if (it.status === 'done') pass++;
      else if (it.status === 'failed') fail++;
      const li = document.createElement('div');
      li.className = `hist-item phase-${it.phase} status-${it.status}`;
      if (it.task_id === currentTaskId) li.classList.add('active');
      const cls = it.status === 'done' ? 'done' : it.status === 'failed' ? 'fail' : '';
      const req = (it.user_request || '').replace(/^[\s\/]+/g, '').slice(0, 42) || '未命名需求';
      li.innerHTML = `
        <div class="hist-title" title="${escapeHtml(it.user_request||'')}">${escapeHtml(req)}</div>
        <div class="hist-meta">
          <span class="phase-tag ${cls}">${PHASE_LABEL[it.phase] || it.phase}</span>
          <span>${fmtTime(it.created_at)}</span>
        </div>`;
      li.onclick = () => viewTask(it.task_id);
      els.history.appendChild(li);
    });
    els.statTotal.textContent = items.length;
    els.statPass.textContent  = pass;
    els.statFail.textContent  = fail;
  } catch (e) {
    log('加载历史失败: ' + e.message, 'err');
  }
}

async function viewTask(taskId) {
  currentTaskId = taskId;
  try {
    const r = await fetch(`/api/v1/tasks/${taskId}`);
    const data = await r.json();
    if (els.heroEmpty) { els.heroEmpty.remove(); els.heroEmpty = null; }
    els.messages.innerHTML = '';
    append(renderAssistantMsg(data));
    updateMetrics(data);
    const phaseMap = { requirement: 'req', code_gen: 'gen', review: 'rev', test_gen: 'tes' };
    const phase = (data.task||{}).phase;
    const status = (data.task||{}).status;
    if (status === 'done' || status === 'failed') {
      markAllDone();
      if (status === 'failed') AGENT_KEYS.forEach(k => setAgent(k.short, (AGENT_KEYS.find(x=>x.id===phase)||{}).short === k.short ? 'err' : 'ok'));
    } else if (phaseMap[phase]) {
      setAgent(phaseMap[phase], 'run');
    }
    log(`加载任务 ${taskId}`, 'ok');
  } catch (e) {
    log('加载任务失败: ' + e.message, 'err');
  }
  loadHistory();
}

/* ============================================================
   提交 / 轮询
   ============================================================ */
async function submit() {
  const text = els.input.value.trim();
  if (!text) return;
  els.sendBtn.disabled = true;
  els.input.value = '';
  append(renderUserMsg(text));
  clearAgents();
  els.phaseTag.textContent = 'SUBMITTING';
  els.mTokens.textContent = '0';
  els.mRetry.textContent = '0';
  log(`提交任务: ${text.slice(0, 60)}...`);

  try {
    const modelSelect = document.getElementById('model-select');
    const currentModel = modelSelect ? modelSelect.value : null;
    const skillSelect = document.getElementById('skill-select');
    const skillId = skillSelect ? skillSelect.value : '';
    const body = { user_request: text };
    if (currentModel) body.model = currentModel;
    if (skillId) body.skill_id = skillId;
    const r = await fetch('/api/v1/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    currentTaskId = data.task_id;
    log(`任务已创建: ${currentTaskId}`, 'ok');
    startSSE(currentTaskId);
    loadHistory();
  } catch (e) {
    log('提交失败: ' + e.message, 'err');
    els.sendBtn.disabled = false;
  }
}
/* Bug D6：SSE 优先，浏览器不支持或连接断开时回退到轮询 */
function startSSE(taskId) {
  if (!window.EventSource) {
    log('浏览器不支持 SSE，回退到轮询', 'warn');
    startPolling(taskId);
    return;
  }
  const phaseMap = { requirement: 'req', code_gen: 'gen', review: 'rev', test_gen: 'tes' };
  const es = new EventSource(`/api/v1/tasks/${taskId}/stream`);
  es.addEventListener('phase', (e) => {
    // data 是阶段字符串，如 code_gen / review / done / failed
    const phase = e.data;
    els.phaseTag.textContent = (phase || '') + ' · ' + (phase === 'failed' ? 'FAILED' : 'RUNNING');
    els.phaseTag.textContent = els.phaseTag.textContent.toUpperCase();
    const short = phaseMap[phase];
    if (short) {
      AGENT_KEYS.forEach(k => {
        const idx = AGENT_KEYS.findIndex(x => x.short === k.short);
        const curIdx = AGENT_KEYS.findIndex(x => x.short === short);
        if (k.short === short) setAgent(k.short, 'run');
        else if (idx < curIdx) setAgent(k.short, 'ok');
        else setAgent(k.short, 'idle');
      });
    }
  });
  es.addEventListener('stream_event', (e) => {
    try {
      const data = JSON.parse(e.data);
      const phase = data.phase || '';
      const progress = data.progress;
      const message = data.message || '';
      const status = progress === -1 ? 'ERR' : progress === 100 ? 'DONE' : 'RUN';
      // 更新终端日志
      log(`[${phase.toUpperCase()}] ${message}`, progress === -1 ? 'err' : progress === 100 ? 'ok' : 'info');
      // 更新阶段标签
      els.phaseTag.textContent = `${phase.toUpperCase()} · ${status}`;
      // 更新 Agent 状态显示
      const phaseMap = { requirement: 'req', code_gen: 'gen', review: 'rev', test_gen: 'tes' };
      const short = phaseMap[phase];
      if (short) {
        if (progress === -1) {
          setAgent(short, 'err');
        } else if (progress === 100) {
          setAgent(short, 'ok');
        } else {
          setAgent(short, 'run');
        }
      }
    } catch (err) {
      log('stream_event 解析失败: ' + err.message, 'err');
    }
  });
  es.addEventListener('done', (e) => {
    try {
      const data = JSON.parse(e.data);
      const task = data.task || {};
      const status = task.status || '';
      if (status === 'done') markAllDone();
      else {
        const last = phaseMap[task.phase] || 'tes';
        AGENT_KEYS.forEach(k => setAgent(k.short, k.short === last ? 'err' : 'ok'));
      }
      append(renderAssistantMsg(data));
      updateMetrics(data);
      els.sendBtn.disabled = false;
      log(`任务完成: ${status}`, status === 'done' ? 'ok' : 'err');
    } catch (err) {
      log('SSE done 解析失败: ' + err.message, 'err');
    }
    es.close();
    loadHistory();
  });
  es.addEventListener('error', () => {
    // readyState === CLOSED 表示连接已彻底关闭，回退到轮询；否则浏览器会自动重连
    if (es.readyState === EventSource.CLOSED) {
      log('SSE 连接关闭，回退到轮询', 'warn');
      startPolling(taskId);
    }
  });
  log('已启用 SSE 实时推送', 'info');
}
function startPolling(taskId) {
  // Bug D6：接受可选 taskId，SSE 回退时复用；未传则沿用全局 currentTaskId
  if (taskId) currentTaskId = taskId;
  if (pollingTimer) clearInterval(pollingTimer);
  const tick = async () => {
    if (!currentTaskId) return;
    try {
      const r = await fetch(`/api/v1/tasks/${currentTaskId}`);
      const data = await r.json();
      const task = data.task || {};
      const phase = task.phase;
      const status = task.status;
      els.phaseTag.textContent = (phase || '') + ' · ' + (status || '');
      els.phaseTag.textContent = els.phaseTag.textContent.toUpperCase();
      els.mTokens.textContent = (task.total_tokens || 0).toLocaleString();
      els.mRetry.textContent = data.retry_counter || 0;
      const phaseMap = { requirement: 'req', code_gen: 'gen', review: 'rev', test_gen: 'tes' };
      const short = phaseMap[phase];
      if (short) {
        AGENT_KEYS.forEach(k => {
          const idx = AGENT_KEYS.findIndex(x => x.short === k.short);
          const curIdx = AGENT_KEYS.findIndex(x => x.short === short);
          if (k.short === short) setAgent(k.short, 'run');
          else if (idx < curIdx) setAgent(k.short, 'ok');
          else setAgent(k.short, 'idle');
        });
      }
      if (status === 'done' || status === 'failed') {
        clearInterval(pollingTimer); pollingTimer = null;
        if (status === 'done') markAllDone();
        else {
          const last = short ? short : 'tes';
          AGENT_KEYS.forEach(k => setAgent(k.short, k.short === last ? 'err' : 'ok'));
        }
        log(`任务完成: ${status}`, status === 'done' ? 'ok' : 'err');
        append(renderAssistantMsg(data));
        els.sendBtn.disabled = false;
        loadHistory();
      }
    } catch (e) {
      log('轮询失败: ' + e.message, 'err');
    }
  };
  tick();
  pollingTimer = setInterval(tick, 3000);
}

function newTask() {
  currentTaskId = null;
  if (pollingTimer) { clearInterval(pollingTimer); pollingTimer = null; }
  els.messages.innerHTML = '';
  const hero = document.createElement('div');
  hero.className = 'hero-empty';
  hero.id = 'hero-empty';
  hero.innerHTML = `
    <div class="hero-title">NEUROFORGE</div>
    <div class="hero-sub">需求分析 · 代码生成 · AST + LLM 双层校验 · 测试生成</div>
    <div class="hero-cap">
      <span><span class="pulse-dot"></span>五智能体协同工作流</span>
      <span><span class="pulse-dot" style="background:var(--accent); box-shadow:0 0 8px var(--accent);"></span>实时可视化监控</span>
      <span><span class="pulse-dot" style="background:var(--success); box-shadow:0 0 8px var(--success);"></span>反馈驱动自修复</span>
    </div>
    <div class="examples">
      ${EXAMPLES_HTML}
    </div>`;
  els.messages.appendChild(hero);
  els.heroEmpty = hero;
  bindExamples();
  clearAgents();
  els.phaseTag.textContent = 'IDLE';
  els.mTokens.textContent = '0';
  els.mRetry.textContent = '0';
  els.terminal.innerHTML = '';
  log('新建会话', 'info');
}

const EXAMPLES_HTML = `
  <button class="ex-card" type="button" data-q="实现一个快速排序算法，包含性能优化与边界校验">
    <div class="ex-tag">SORT · 01</div>
    <div class="ex-title">快速排序算法</div>
    <div class="ex-desc">Hoare分区 + 三数取中 + 输入校验</div>
  </button>
  <button class="ex-card" type="button" data-q="实现一个JWT身份验证装饰器，支持过期校验和权限检查">
    <div class="ex-tag">AUTH · 02</div>
    <div class="ex-title">JWT 鉴权装饰器</div>
    <div class="ex-desc">HS256 签名 + 过期 + 权限角色</div>
  </button>
  <button class="ex-card" type="button" data-q="实现一个异步任务队列，支持优先级、重试和死信队列">
    <div class="ex-tag">QUEUE · 03</div>
    <div class="ex-title">优先级任务队列</div>
    <div class="ex-desc">重试策略 + 死信 + 异步消费</div>
  </button>
  <button class="ex-card" type="button" data-q="实现一个线程安全的数据库连接池，含最小最大连接数管理">
    <div class="ex-tag">POOL · 04</div>
    <div class="ex-title">数据库连接池</div>
    <div class="ex-desc">线程安全 + 保活 + 连接复用</div>
  </button>
  <button class="ex-card" type="button" data-q="实现一个LRU缓存，支持过期时间与线程安全">
    <div class="ex-tag">CACHE · 05</div>
    <div class="ex-title">LRU 缓存</div>
    <div class="ex-desc">双向链表 + 哈希 + TTL</div>
  </button>
  <button class="ex-card" type="button" data-q="实现一个令牌桶限流算法，支持分布式原子操作">
    <div class="ex-tag">RATE · 06</div>
    <div class="ex-title">令牌桶限流器</div>
    <div class="ex-desc">原子补令牌 + 可配置速率</div>
  </button>
  <button class="ex-card" type="button" data-q="实现一个线程安全的哈希表，支持动态扩容和渐进式rehash">
    <div class="ex-tag">HASH · 07</div>
    <div class="ex-title">可扩缩哈希表</div>
    <div class="ex-desc">渐进式 rehash + 线程安全</div>
  </button>
  <button class="ex-card" type="button" data-q="实现一个WebSocket服务器，支持房间、广播和心跳保活">
    <div class="ex-tag">WS · 08</div>
    <div class="ex-title">WebSocket 服务端</div>
    <div class="ex-desc">房间广播 + 心跳 + 连接管理</div>
  </button>`;

function bindExamples() {
  document.querySelectorAll('.ex-card').forEach(b => {
    b.onclick = () => { els.input.value = b.dataset.q; submit(); };
  });
}
bindExamples();

/* ============================================================
   事件绑定
   ============================================================ */
els.sendBtn.onclick = submit;
els.newBtn.onclick = newTask;
els.clearBtn.onclick = async () => {
  if (!confirm('确定清空所有任务历史？此操作不可撤销。')) return;
  try {
    const r = await fetch('/api/v1/tasks', { method: 'DELETE' });
    const data = await r.json();
    log(`已清空 ${data.deleted} 条历史`, 'ok');
    currentTaskId = null;
    if (pollingTimer) { clearInterval(pollingTimer); pollingTimer = null; }
    els.messages.innerHTML = '';
    const hero = document.createElement('div');
    hero.className = 'hero-empty';
    hero.id = 'hero-empty';
    hero.innerHTML = `
      <div class="hero-title">NEUROFORGE</div>
      <div class="hero-sub">需求分析 · 代码生成 · AST + LLM 双层校验 · 测试生成</div>
      <div class="hero-cap">
        <span><span class="pulse-dot"></span>五智能体协同工作流</span>
        <span><span class="pulse-dot" style="background:var(--accent); box-shadow:0 0 8px var(--accent);"></span>实时可视化监控</span>
        <span><span class="pulse-dot" style="background:var(--success); box-shadow:0 0 8px var(--success);"></span>反馈驱动自修复</span>
      </div>
      <div class="examples">${EXAMPLES_HTML}</div>`;
    els.messages.appendChild(hero);
    els.heroEmpty = hero;
    bindExamples();
    clearAgents();
    els.phaseTag.textContent = 'IDLE';
    els.mTokens.textContent = '0';
    els.mRetry.textContent = '0';
    await loadHistory(true);
    setTimeout(() => loadHistory(true), 500);
  } catch (e) {
    log('清空失败: ' + e.message, 'err');
  }
};
els.input.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
});

/* ============================================================
   模型选择
   ============================================================ */
async function loadModels() {
  try {
    const r = await fetch('/api/v1/models');
    const data = await r.json();
    const sel = document.getElementById('model-select');
    sel.innerHTML = '';
    (data.available || []).forEach(m => {
      const opt = document.createElement('option');
      opt.value = m; opt.textContent = m;
      if (m === data.current_main) opt.selected = true;
      sel.appendChild(opt);
    });
    if (!data.available.includes(data.current_main)) {
      const opt = document.createElement('option');
      opt.value = data.current_main; opt.textContent = data.current_main;
      opt.selected = true; sel.appendChild(opt);
    }
    els.statusText.textContent = 'ONLINE · ' + (data.current_main || '');
    log(`模型列表已加载（当前: ${data.current_main}）`, 'ok');
  } catch (e) {
    log('加载模型失败: ' + e.message, 'err');
  }
}

async function loadSkills() {
  try {
    const r = await fetch('/api/v1/tasks/skills/list');
    const data = await r.json();
    const sel = document.getElementById('skill-select');
    if (!sel) return;
    sel.innerHTML = '<option value="">自动匹配</option>';
    (data.skills || []).forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = `${s.name} (${s.id})`;
      sel.appendChild(opt);
    });
    log(`技能库已加载（${data.skills.length} 个技能）`, 'ok');
  } catch (e) {
    log('加载技能失败: ' + e.message, 'err');
  }
}
document.getElementById('model-save').onclick = async () => {
  const sel = document.getElementById('model-select');
  const target = sel.value;
  if (!target) return;
  try {
    const r = await fetch('/api/v1/models/switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: target, for_code: false }),
    });
    const data = await r.json();
    if (data.status === 'ok') {
      log(`✓ 已切换到模型: ${data.model}`, 'ok');
      els.statusText.textContent = 'ONLINE · ' + data.model;
      if (data.api_key_fallback && data.api_key_url) {
        const provider = data.provider || '该厂商';
        log(`[ERR] ${provider} 专用 API Key 未配置，当前用主 Key 兜底，跨厂商可能失败`, 'err');
        showApiKeyHint(data.model, provider, data.api_key_url);
      } else if (data.provider) {
        log(`✓ ${data.provider} 专用 Key 已配置`, 'ok');
      }
    } else {
      log('切换失败: ' + (data.message || 'unknown'), 'err');
    }
  } catch (e) {
    log('切换失败: ' + e.message, 'err');
  }
};

/* ---- API Key 获取提示卡片 ---- */
function showApiKeyHint(model, provider, url) {
  const old = document.getElementById('api-key-hint');
  if (old) old.remove();

  const hint = document.createElement('div');
  hint.id = 'api-key-hint';
  hint.style.cssText = `
    position: fixed; bottom: 24px; right: 24px; z-index: 9999;
    background: linear-gradient(135deg, rgba(20,10,40,0.96), rgba(40,15,60,0.96));
    border: 1px solid rgba(244,63,94,0.5); border-radius: 12px;
    padding: 16px 20px; max-width: 380px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.6), 0 0 24px rgba(244,63,94,0.2);
    backdrop-filter: blur(12px); color: #E8E8F5; font-size: 13px;
    animation: slideIn 0.3s ease;
  `;
  hint.innerHTML = `
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
      <span style="font-size:18px;color:#F43F5E;">◈</span>
      <span style="font-weight:600;color:#F43F5E;">需要 ${provider} API Key</span>
    </div>
    <div style="line-height:1.6;margin-bottom:12px;color:#B5B5D5;">
      模型 <b style="color:#E879F9;">${model}</b> 需要 ${provider} 的专用 API Key。<br>
      当前使用主 Key 兜底，跨厂商调用可能失败。
    </div>
    <div style="display:flex;gap:8px;">
      <a href="${url}" target="_blank" rel="noopener"
         style="flex:1;text-align:center;padding:8px 16px;border-radius:8px;
                background:linear-gradient(135deg,#D946EF,#F43F5E);color:#fff;
                text-decoration:none;font-weight:600;font-size:12px;">
        前往 ${provider} 获取 Key →
      </a>
      <button id="api-key-hint-close"
         style="padding:8px 16px;border-radius:8px;border:1px solid rgba(255,255,255,0.2);
                background:transparent;color:#B5B5D5;cursor:pointer;font-size:12px;">
        稍后
      </button>
    </div>
    <div style="margin-top:10px;font-size:11px;color:var(--text-faint);">
      获取后在 .env 中配置对应环境变量，重启服务生效
    </div>
  `;
  document.body.appendChild(hint);
  document.getElementById('api-key-hint-close').onclick = () => hint.remove();
  setTimeout(() => { if (hint.parentNode) hint.remove(); }, 30000);
}

/* ============================================================
   初始化
   ============================================================ */
loadHistory();
loadModels();
loadSkills();
fetch('/api/v1/health').then(r => r.json()).then(d => {
  if (d.llm) els.statusText.textContent = 'ONLINE · ' + d.llm;
}).catch(() => log('健康检查失败', 'err'));
log('系统就绪 · NeuroForge Liquid Orb UI v0.2.0', 'ok');

// 实时时钟
setInterval(() => {
  document.getElementById('now-tm').textContent = new Date().toLocaleTimeString('en-GB');
}, 1000);
document.getElementById('now-tm').textContent = new Date().toLocaleTimeString('en-GB');

// 输入自动高度
els.input.addEventListener('input', () => {
  els.input.style.height = 'auto';
  els.input.style.height = Math.min(els.input.scrollHeight, 180) + 'px';
});

// 暴露全局函数供 onclick 内联使用
window.executeCode = executeCode;
window.showModifyBox = showModifyBox;
window.hideModifyBox = hideModifyBox;
window.submitModify = submitModify;