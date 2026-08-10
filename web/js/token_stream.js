const TokenStream = (function () {
  let containerEl = null;
  let contentEl = null;
  let statsEl = null;
  let cursorEl = null;

  let tokens = [];
  let totalTokens = 0;
  let tokenStartTime = null;
  let isStreaming = false;
  let autoScroll = true;
  let onTokenCb = null;

  function init(containerId, options = {}) {
    containerEl = document.getElementById(containerId);
    if (!containerEl) return;

    contentEl = containerEl.querySelector('.token-stream-content');
    statsEl = containerEl.querySelector('.token-stats');
    cursorEl = null;

    tokens = [];
    totalTokens = 0;
    isStreaming = false;
    autoScroll = true;
    onTokenCb = options.onToken || null;

    updateStats();
  }

  function toggle() {
    if (containerEl) {
      containerEl.classList.toggle('collapsed');
      const indicator = containerEl.querySelector('#tokenStreamToggle');
      if (indicator) {
        indicator.textContent = containerEl.classList.contains('collapsed') ? '▲' : '▼';
      }
    }
  }

  function addToken(token) {
    if (!token || !contentEl) return;

    tokens.push(token);
    totalTokens++;

    if (onTokenCb) onTokenCb(token, totalTokens);

    const span = document.createElement('span');
    span.className = 'token-word';
    span.textContent = token;

    if (cursorEl && cursorEl.parentNode) {
      cursorEl.parentNode.insertBefore(span, cursorEl);
    } else {
      contentEl.appendChild(span);
      cursorEl = document.createElement('span');
      cursorEl.className = 'cursor-blink';
      contentEl.appendChild(cursorEl);
    }

    if (autoScroll) {
      contentEl.scrollTop = contentEl.scrollHeight;
    }

    updateStats();
  }

  function addBatch(tokenString) {
    if (!tokenString) return;
    const words = tokenString.split(/(\s+)/);
    words.forEach(w => {
      if (w.trim()) {
        addToken(w);
      } else {
        addToken(' ');
      }
    });
  }

  function start() {
    isStreaming = true;
    tokenStartTime = Date.now();
    totalTokens = 0;
    tokens = [];
    if (contentEl) {
      contentEl.innerHTML = '';
      cursorEl = document.createElement('span');
      cursorEl.className = 'cursor-blink';
      contentEl.appendChild(cursorEl);
    }
    updateStats();
  }

  function stop() {
    isStreaming = false;
    if (cursorEl) {
      cursorEl.remove();
      cursorEl = null;
    }
  }

  function clear() {
    stop();
    tokens = [];
    totalTokens = 0;
    if (contentEl) {
      contentEl.innerHTML = '';
    }
    updateStats();
  }

  function setAutoScroll(val) {
    autoScroll = val;
  }

  function getStats() {
    const elapsed = tokenStartTime ? (Date.now() - tokenStartTime) / 1000 : 0;
    const tps = elapsed > 0 ? Math.round(totalTokens / elapsed) : 0;
    return {
      total: totalTokens,
      elapsed: elapsed.toFixed(1),
      tps,
      isStreaming,
    };
  }

  function updateStats() {
    if (!statsEl) return;
    const s = getStats();
    statsEl.innerHTML = `
      <span>Tokens: <span class="ts-val">${s.total.toLocaleString()}</span></span>
      <span>速率: <span class="ts-val">${s.tps}</span> tok/s</span>
      <span>耗时: <span class="ts-val">${s.elapsed}s</span></span>
      <span>状态: <span class="ts-val" style="color:${s.isStreaming ? 'var(--success)' : 'var(--text2)'}">${s.isStreaming ? 'STREAMING' : 'IDLE'}</span></span>
    `;
  }

  function getTokens() {
    return tokens.slice();
  }

  return { init, addToken, addBatch, start, stop, clear, toggle, getStats, getTokens, setAutoScroll };
})();
