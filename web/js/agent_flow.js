const AgentFlow = (function () {
  const NODES = [
    { id: 'requirement', label: '需求分析', icon: 'R', x: 140, y: 60 },
    { id: 'code_gen', label: '代码生成', icon: 'C', x: 140, y: 150 },
    { id: 'review', label: '代码评审', icon: 'V', x: 140, y: 240 },
    { id: 'test_gen', label: '测试生成', icon: 'T', x: 140, y: 330 },
    { id: 'done', label: '交付', icon: 'D', x: 140, y: 420 },
  ];

  let nodeStates = {};
  let svgEl = null;
  let onNodeClickCb = null;

  function init(containerId, options = {}) {
    onNodeClickCb = options.onNodeClick || null;
    svgEl = document.getElementById(containerId);
    if (!svgEl) return;

    nodeStates = {};
    NODES.forEach(n => { nodeStates[n.id] = 'pending'; });

    render();
  }

  function render() {
    if (!svgEl) return;
    const svgNS = 'http://www.w3.org/2000/svg';
    svgEl.innerHTML = '';

    const defs = document.createElementNS(svgNS, 'defs');
    const filter = document.createElementNS(svgNS, 'filter');
    filter.setAttribute('id', 'glow');
    filter.setAttribute('x', '-50%');
    filter.setAttribute('y', '-50%');
    filter.setAttribute('width', '200%');
    filter.setAttribute('height', '200%');
    const blur = document.createElementNS(svgNS, 'feGaussianBlur');
    blur.setAttribute('stdDeviation', '3');
    blur.setAttribute('result', 'coloredBlur');
    const merge = document.createElementNS(svgNS, 'feMerge');
    const mn1 = document.createElementNS(svgNS, 'feMergeNode');
    mn1.setAttribute('in', 'coloredBlur');
    const mn2 = document.createElementNS(svgNS, 'feMergeNode');
    mn2.setAttribute('in', 'SourceGraphic');
    merge.appendChild(mn1);
    merge.appendChild(mn2);
    filter.appendChild(blur);
    filter.appendChild(merge);
    defs.appendChild(filter);
    svgEl.appendChild(defs);

    for (let i = 0; i < NODES.length - 1; i++) {
      const a = NODES[i];
      const b = NODES[i + 1];
      const line = document.createElementNS(svgNS, 'line');
      line.setAttribute('x1', a.x + 40);
      line.setAttribute('y1', a.y);
      line.setAttribute('x2', b.x + 40);
      line.setAttribute('y2', b.y);
      line.setAttribute('class', 'flow-edge');
      const stateA = nodeStates[a.id];
      const stateB = nodeStates[b.id];
      if (stateA === 'completed' || stateA === 'failed') {
        line.classList.add('completed');
      } else if (stateA === 'running') {
        line.classList.add('active');
      }
      svgEl.appendChild(line);

      const arrow = document.createElementNS(svgNS, 'polygon');
      const ax = b.x + 40;
      const ay = b.y;
      arrow.setAttribute('points', `${ax - 5},${ay - 4} ${ax},${ay} ${ax - 5},${ay + 4}`);
      arrow.setAttribute('fill', stateA === 'running' ? 'var(--accent)' : 'var(--border)');
      svgEl.appendChild(arrow);
    }

    NODES.forEach(node => {
      const g = document.createElementNS(svgNS, 'g');
      g.setAttribute('class', `flow-node ${nodeStates[node.id] || 'pending'}`);
      g.setAttribute('transform', `translate(${node.x}, ${node.y})`);
      g.dataset.nodeId = node.id;

      const rect = document.createElementNS(svgNS, 'rect');
      rect.setAttribute('width', '80');
      rect.setAttribute('height', '44');
      rect.setAttribute('rx', '8');
      rect.setAttribute('ry', '8');
      g.appendChild(rect);

      const circle = document.createElementNS(svgNS, 'circle');
      circle.setAttribute('cx', '16');
      circle.setAttribute('cy', '22');
      circle.setAttribute('r', '11');
      const stateColors = {
        pending: 'var(--text2)',
        running: 'var(--accent)',
        completed: 'var(--success)',
        failed: 'var(--danger)',
        hitl: 'var(--warning)',
      };
      circle.setAttribute('fill', stateColors[nodeStates[node.id] || 'pending']);
      g.appendChild(circle);

      const iconText = document.createElementNS(svgNS, 'text');
      iconText.setAttribute('x', '16');
      iconText.setAttribute('y', '26');
      iconText.setAttribute('text-anchor', 'middle');
      iconText.setAttribute('font-size', '11');
      iconText.setAttribute('font-weight', '700');
      iconText.setAttribute('fill', '#fff');
      iconText.textContent = node.icon;
      g.appendChild(iconText);

      const labelText = document.createElementNS(svgNS, 'text');
      labelText.setAttribute('x', '38');
      labelText.setAttribute('y', '20');
      labelText.setAttribute('font-size', '11');
      labelText.setAttribute('font-weight', '600');
      labelText.textContent = node.label;
      g.appendChild(labelText);

      const statusText = document.createElementNS(svgNS, 'text');
      statusText.setAttribute('x', '38');
      statusText.setAttribute('y', '36');
      statusText.setAttribute('font-size', '9');
      statusText.setAttribute('fill', 'var(--text2)');
      statusText.textContent = (nodeStates[node.id] || 'pending').toUpperCase();
      g.appendChild(statusText);

      g.addEventListener('click', () => {
        if (onNodeClickCb) onNodeClickCb(node.id, nodeStates[node.id]);
      });

      svgEl.appendChild(g);
    });
  }

  function setNodeState(nodeId, state) {
    if (nodeStates[nodeId] !== state) {
      nodeStates[nodeId] = state;
      render();
    }
  }

  function reset() {
    NODES.forEach(n => { nodeStates[n.id] = 'pending'; });
    render();
  }

  function setAllStates(states) {
    Object.assign(nodeStates, states);
    render();
  }

  function getNodes() {
    return NODES.map(n => ({ ...n, state: nodeStates[n.id] }));
  }

  return { init, setNodeState, reset, setAllStates, getNodes };
})();
