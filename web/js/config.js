/* ============================================================
   NeuroForge · LLM 配置弹窗
   页面内配置 API 大模型（Base URL / API Key / 模型），保存即热生效。
   未配置 API 时自动弹出引导。
   ============================================================ */

(function () {
  const PROVIDER_MAP = {
    DASHSCOPE_API_KEY: {
      name: '阿里云 DashScope',
      base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
      key_url: 'https://bailian.console.aliyun.com/?apiKey=1#/api-key',
      models: ['qwen-max', 'qwen-plus', 'qwen-turbo', 'qwen-coder-plus', 'qwen3-max', 'qwen3-coder-plus'],
    },
    DEEPSEEK_API_KEY: {
      name: 'DeepSeek',
      base_url: 'https://api.deepseek.com/v1',
      key_url: 'https://platform.deepseek.com/api_keys',
      models: ['deepseek-chat', 'deepseek-reasoner', 'deepseek-v4-flash', 'deepseek-v4-pro'],
    },
    ARK_API_KEY: {
      name: '火山方舟（豆包）',
      base_url: 'https://ark.cn-beijing.volces.com/api/v3',
      key_url: 'https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey',
      models: ['doubao-seed-1-6-flash-250828', 'doubao-seed-1-6-250615'],
    },
    OPENAI_API_KEY: {
      name: 'OpenAI',
      base_url: 'https://api.openai.com/v1',
      key_url: 'https://platform.openai.com/api-keys',
      models: ['gpt-4o', 'gpt-4o-mini', 'gpt-4.1', 'gpt-4.1-mini', 'o4-mini'],
    },
    GEMINI_API_KEY: {
      name: 'Google Gemini',
      base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/',
      key_url: 'https://aistudio.google.com/app/apikey',
      models: ['gemini-2.0-flash', 'gemini-2.5-flash', 'gemini-2.5-pro'],
    },
  };

  const modal = document.getElementById('config-modal');
  const btn = document.getElementById('config-btn');
  const statusEl = document.getElementById('config-status');
  const statusText = document.getElementById('config-status-text');
  const providerSel = document.getElementById('cfg-provider');
  const providerLinkWrap = document.getElementById('cfg-provider-link');
  const providerUrl = document.getElementById('cfg-provider-url');
  const baseUrlInput = document.getElementById('cfg-base-url');
  const apiKeyInput = document.getElementById('cfg-api-key');
  const modelInput = document.getElementById('cfg-model');
  const codeModelInput = document.getElementById('cfg-code-model');
  const saveBtn = document.getElementById('cfg-save');

  let lastStatus = null;
  let lastProviders = [];

  function openModal() {
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    refreshStatus();
  }

  function closeModal() {
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
  }

  // 关闭：点击遮罩 / 关闭按钮 / Esc
  document.querySelectorAll('[data-close="config"]').forEach(el => {
    el.addEventListener('click', closeModal);
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal.classList.contains('open')) closeModal();
  });

  btn.addEventListener('click', openModal);

  // 填充厂商下拉
  function populateProviders(providers) {
    const sel = providerSel;
    sel.innerHTML = '<option value="">自定义（手动填写）</option>';
    (providers || []).forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.env_key;
      opt.textContent = st_to_padded(p.name);
      sel.appendChild(opt);
    });
  }

  function st_to_padded(name) {
    return name;
  }

  // 选择厂商自动填充 Base URL / 模型 / Key 链接
  providerSel.addEventListener('change', () => {
    const envKey = providerSel.value;
    const info = lastProviders.find((p) => p.env_key === envKey) || PROVIDER_MAP[envKey];
    if (!info) {
      providerLinkWrap.style.display = 'none';
      return;
    }
    if (info.base_url && baseUrlInput.value !== info.base_url) baseUrlInput.value = info.base_url;
    if (!modelInput.value || info.models.includes(modelInput.value)) {
      modelInput.value = info.models[0] || '';
    }
    providerUrl.href = info.key_url;
    providerLinkWrap.style.display = 'block';
  });

  // 读取当前配置状态
  async function refreshStatus() {
    try {
      const r = await fetch('/api/v1/config/llm', { cache: 'no-cache' });
      const data = await r.json();
      lastStatus = data;
      lastProviders = data.providers || [];
      populateProviders(data.providers || []);

      statusEl.classList.toggle('ok', data.configured);
      if (data.quota_exhausted) {
        statusEl.classList.add('quota');
        statusText.textContent = `⚠ 额度已用尽 · ${data.model}（请更换 Key）`;
      } else {
        statusEl.classList.remove('quota');
        statusText.textContent = data.configured
          ? `已配置 · ${data.model}（${data.dry_run ? 'DryRun 待验证' : '生效中'}）`
          : '未配置 API Key，当前为 DryRun 模式（不会真正调用模型）';
      }

      if (data.base_url) baseUrlInput.value = data.base_url;
      if (data.model) modelInput.value = data.model;
      if (data.code_model && data.code_model !== data.model) codeModelInput.value = data.code_model;
      apiKeyInput.value = '';
      apiKeyInput.placeholder = data.configured ? '已配置（留空保持不变）' : 'sk-...';
    } catch (e) {
      statusText.textContent = '读取配置失败：' + e.message;
    }
  }

  // 保存
  saveBtn.addEventListener('click', async () => {
    const base_url = baseUrlInput.value.trim();
    const api_key = apiKeyInput.value.trim();
    const model = modelInput.value.trim();
    if (!base_url || !model) {
      statusText.textContent = '请填写 Base URL 与主模型';
      return;
    }
    if (!api_key) {
      statusText.textContent = '请填写 API Key';
      return;
    }
    saveBtn.disabled = true;
    saveBtn.textContent = '保存中…';
    try {
      const body = {
        base_url,
        api_key,
        model,
        code_model: codeModelInput.value.trim() || null,
        provider_env_key: providerSel.value || null,
      };
      const r = await fetch('/api/v1/config/llm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) {
        statusText.textContent = '保存失败：' + (data.detail || data.message || '未知错误');
        return;
      }
      statusEl.classList.toggle('ok', !data.dry_run);
      statusEl.classList.remove('quota');
      statusText.textContent = data.message;

      // 隐藏配额警告横幅
      const banner = document.getElementById('quota-warning');
      if (banner) banner.hidden = true;

      // 同步顶部状态与模型列表
      const statusChip = document.getElementById('status-text');
      if (statusChip) statusChip.textContent = 'ONLINE · ' + (data.model || '');
      if (typeof window.loadModels === 'function') window.loadModels();

      // 保存成功且非 DryRun，稍后自动关闭
      setTimeout(() => {
        if (!data.dry_run) closeModal();
      }, 1200);
    } catch (e) {
      statusText.textContent = '保存失败：' + e.message;
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = '保存并生效';
    }
  });

  // 未配置时自动弹出引导 + 顶部按钮高亮
  async function checkAndPrompt() {
    try {
      const r = await fetch('/api/v1/config/llm', { cache: 'no-cache' });
      const data = await r.json();
      if (!data.configured) {
        btn.classList.add('needs-config');
        setTimeout(openModal, 1200);
      }
    } catch (e) { /* 忽略静默失败 */ }
  }

  // 暴露给 ui.js 使用
  window.configModal = { open: openModal, close: closeModal, refresh: refreshStatus };

  // 启动：延迟检测，避免打断首屏
  setTimeout(checkAndPrompt, 2500);
})();
