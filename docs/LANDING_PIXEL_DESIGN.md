# NeuroForge Landing Page — 3D 像素风格升级设计方案

> **设计宣言**: 把当前「太空行星」升级为「像素 Agent 太空站」—— 保留 A+ 行星的史诗感，加入四个会注视你的像素 Agent，让整个页面从"看风景"变成"进入指挥中心"。

---

## 1. 设计参考来源

### 1.1 Awwwards 2026 获奖趋势（数据支撑）

| 指标 | 数据 |
|------|------|
| 2026 H1 SOTD 中沉浸式 3D 占比 | **61%**（2024年仅23%） |
| 3D 作品创意平均分 | **8.7/10** vs 扁平布局 6.4/10 |
| 获奖公式 | 滚轮驱动叙事 + 相机路径 + 粒子效果 + **60fps** |
| 关键洞察 | 评判标准已从"布局设计"转向"空间思维 + 实时图形工程" |

> **评委原话**: "创造力的标准已从排版精度转向空间思维和实时 3D 交互" — Awwwards 2026 评审

### 1.2 具体参考案例

| 作品 | 平台 | 核心借鉴点 |
|------|------|-----------|
| **Bruno Simon 2025 WebGPU Portfolio** | Awwwards SOTY | 零 DOM 纯 3D 交互、相机自由探索、双击聚焦 |
| **Pixel 3D Homepage** (GitHub) | GitHub 2.1k stars | Three.js 体素世界 + 可交互角色 + 像素风格 UI |
| **VoxelVerse** (GitHub) | 游戏化作品集 | 像素艺术 + Three.js + 角色移动交互 |
| **Cartier Watches & Wonders 2026** | Awwwards SOTD | 空间分区叙事、场景间平滑过渡 |
| **REPLACED (2026 游戏)** | Steam 83% 好评 | 像素精灵 + 3D 光照 + 现代后处理 = Retro-Futurism |

### 1.3 为什么 Pixel + 3D 是 2026 的正确方向

Awwwards 2026 评审特别指出：**"成功的 3D 网站不是堆砌特效，而是围绕一个统一的视觉概念做深度"**。像素 + 3D 的组合恰好满足：
- **差异化**: 61% 的 3D 获奖作品是写实/抽象风格，像素风格极其罕见
- **品牌契合**: NeuroForge 是"代码生成系统"，像素 = 数字原生，天然契合
- **性能友好**: 像素精灵（Sprite）比 3D 模型性能开销低一个数量级
- **情感连接**: 像素角色比抽象粒子更容易让用户产生" companionship "感

---

## 2. 当前页面分析

### 2.1 现有资产（截图 + 代码）

```
当前 Landing Page:
┌──────────────────────────────────────────────────┐
│  // AUTONOMOUS INTELLIGENCE ENGINE               │
│  MULTI ⇄ AGENT ⇝ CODE ↯ SYSTEM                   │
│                                                    │
│  NeuroForge                                        │
│  Multi-Agent Code Forge — 自主多智能体代码生成系统 │
│                                                    │
│  [点击任意位置进入系统]                            │
│                                                    │
│         ╭────────────────────╮                   │
│        ╱   蓝色行星 + 轨道环  ╲                  │
│       │    + 粒子 + Bloom      │                  │
│        ╲   + 卫星轨迹         ╱                  │
│         ╰────────────────────╯                   │
└──────────────────────────────────────────────────┘
```

**已有（保留并增强）**:
- Three.js 3D 场景（行星 PBR + SSS、GPU Ping-Pong 粒子、God Rays、Bloom）
- 轨道环系统（双环反向旋转）
- 6 颗卫星（椭圆轨道 + 拖尾 + 鼠标交互偏移）
- 冲击波系统（鼠标悬停行星触发）
- 量子概率场 + 神经网络拓扑可视化
- 后处理链（Bloom + 色差 + 胶片颗粒 + 暗角）
- 相机微摆 + 鼠标响应（已有基础视差）

**缺失（需要新增）**:
- 像素 Agent 角色
- Agent 注视鼠标系统
- 深度分层视差
- 像素风格 UI 覆盖层
- 戏剧性进入动画

### 2.2 现有鼠标交互（代码分析）

当前 `main.js` 已有基础鼠标响应：
```javascript
// 鼠标归一化坐标 [-1, 1]
mouse.tx = (e.clientX / W) * 2 - 1;
mouse.ty = (e.clientY / H) * 2 - 1;

// 平滑插值
mouse.x += (mouse.tx - mouse.x) * 0.05;
mouse.y += (mouse.ty - mouse.y) * 0.05;

// 应用到：行星自转、粒子 uniform、光照位置、相机微摆
camera.position.x += (mouse.x * 0.90 + swingX - camera.position.x) * 0.036;
camera.position.y += (mouse.y * -0.72 + 0.4 + swingY - camera.position.y) * 0.036;
```

**结论**: 已有很好的基础！只需扩展为**深度分层视差 + Agent 注视**。

---

## 3. 设计愿景：像素 Agent 太空站

### 3.1 场景描述

用户打开页面后看到的不是"一个带有 3D 背景的网页"，而是**透过一扇舷窗看到的太空指挥中心**:

```
                        ★ 星空背景 (z=-30, 视差 0.2x)
              ╭────────────────────────────────────╮
             ╱   蓝色能量行星 (z=-10, 视差 0.4x)    ╲
            │    + 脉动核心 + 双轨道环               │
            │                                      │
            │   👤 Analyst      👤 Coder           │  ← 像素 Agent
            │   (左前)          (右前)             │   (z=0, 视差 0.7x)
            │                                      │
            │        [对话气泡]                     │
            │                                      │
            │   👤 Reviewer     👤 Tester          │
            │   (左后)          (右后)             │
            │                                      │
             ╲   + 粒子流 + 数据光束              ╱
              ╰────────────────────────────────────╯

    ┌─────────────────────────────────────────────────┐
    │  NEUROFORGE · MULTI-AGENT CODE FORGE            │  ← UI 覆盖层
    │  [点击任意位置进入系统]                          │
    └─────────────────────────────────────────────────┘
```

### 3.2 核心设计原则

1. **行星是舞台，Agent 是演员** — 不要破坏现有行星的视觉主导地位
2. **像素是纹理，3D 是空间** — Agent 是 2D 像素精灵，但存在于 3D 空间中
3. **鼠标是主角的注意力** — Agent 会"看"向鼠标，让用户感到被关注
4. **克制胜于堆砌** — Awwwards 2026 评审明确批评"饱和的通用粒子背景"，我们要避免

---

## 4. 像素视觉语言

### 4.1 像素 Agent 角色设计

四个 Agent 都是 **32×32 像素** 的精灵图（Sprite Sheet），使用 **Nearest Neighbor** 缩放保持像素感。

#### Analyst（需求分析）— 蓝色 `#3B82F6`

```
   ██  ██      ← 眼镜/护目镜
  ████████
   ▓▓  ▓▓      ← ^_^ 笑脸
  ████████
   ██  ██      ← 手持数据板
  ▓▓▓▓▓▓▓▓     ← 蓝色实验服
```

- **配色**: 主色 `#3B82F6`，辅色 `#1E3A8A`，高光 `#60A5FA`
- **道具**: 手持发光数据板（会随工作脉动）
- **性格**: 冷静、分析型、偶尔推眼镜
- **动画帧**: Idle(2帧循环) → Working(4帧: 打字/分析) → Done(1帧: 竖拇指) → Error(1帧: 挠头)

#### Coder（代码生成）— 品红 `#D946EF`

```
   ▓▓▓▓▓▓      ← 连帽衫帽子
  █  ▓▓  █
   >  ()       ← >() 专注表情
  ████████
  ▓░▓░▓░▓░     ← 键盘发光纹路
```

- **配色**: 主色 `#D946EF`，辅色 `#701A75`，高光 `#E879F9`
- **道具**: 悬浮的代码片段粒子（从角色手中飘出）
- **性格**: 专注、极客、打字飞快
- **动画帧**: Idle(2帧: 呼吸) → Working(4帧: 疯狂打字) → Done(1帧: 转椅子庆祝) → Error(1帧: 砸键盘)

#### Reviewer（智能审查）— 珊瑚橙 `#F6C445`

```
   ◉    ◉      ← 机械义眼
  ████████
   =  =        ← =_= 严肃表情
  ████████
   🔍          ← 手持放大镜
  ▓▓▓▓▓▓▓▓     ← 橙色制服
```

- **配色**: 主色 `#F6C445`，辅色 `#92400E`，高光 `#FCD34D`
- **道具**: 放大镜会发出扫描光束（Shader 效果）
- **性格**: 严谨、挑剔、放大镜会放大缩小
- **动画帧**: Idle(2帧: 巡视) → Working(4帧: 扫描/放大) → Done(1帧: 点头) → Error(1帧: 放大镜掉地)

#### Tester（测试生成）— 青色 `#06B6D4`

```
   █    █      ← 护目镜
  ▓▓▓▓▓▓▓▓
   o  o        ← o_o 好奇表情
   ω           ← 小嘴巴
  ████████
  ▓░▓░▓░▓░     ← 测试工装
```

- **配色**: 主色 `#06B6D4`，辅色 `#164E63`，高光 `#67E8F9`
- **道具**: 手中的"bug jar"（玻璃罐里装着虫子图标）
- **性格**: 好奇、活泼、喜欢抓 bug
- **动画帧**: Idle(2帧: 晃腿) → Working(4帧: 放测试机器人) → Done(1帧: 高举通过的测试报告) → Error(1帧: Bug 从罐子里飞出来)

### 4.2 像素风格 UI 覆盖层

当前 Landing Page 的文字覆盖层需要与像素风格融合，但不能完全变成"游戏 UI"——要保持专业感。

#### 字体系统

| 层级 | 当前字体 | 新字体 | 说明 |
|------|---------|--------|------|
| 标题 "NeuroForge" | Geist 500 | **Geist 500 + text-shadow 像素化** | 保持现代感，但加像素边缘 |
| Eyebrow "// AUTONOMOUS..." | JetBrains Mono | **JetBrains Mono + CRT 扫描线** | 代码感 + 复古终端 |
| 副标题 | Geist 400 | **Geist 400** | 不变，保持可读性 |
| 按钮 "点击任意位置进入系统" | Geist 400 | **等宽字体 + 打字机效果** | 模拟终端输入 |

#### 像素化文字效果（CSS）

```css
/* 标题：轻微像素边缘 */
#landing-title {
  font-family: 'Geist', sans-serif;
  font-weight: 500;
  font-size: 72px;
  color: #fff;
  /* 多层 text-shadow 模拟像素发光 */
  text-shadow:
    0 0 10px rgba(6, 182, 212, 0.5),
    0 0 20px rgba(6, 182, 212, 0.3),
    0 0 40px rgba(6, 182, 212, 0.1),
    2px 2px 0px rgba(0, 0, 0, 0.3);  /* 像素偏移阴影 */
  letter-spacing: -0.02em;
}

/* Eyebrow：CRT 终端效果 */
#landing-eyebrow {
  font-family: 'JetBrains Mono', monospace;
  font-size: 11px;
  color: #06B6D4;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  /* 扫描线纹理 */
  background: linear-gradient(
    transparent 50%,
    rgba(6, 182, 212, 0.03) 50%
  );
  background-size: 100% 4px;
}

/* 按钮：像素边框 */
#landing-hint {
  display: inline-block;
  padding: 12px 24px;
  border: 2px solid rgba(6, 182, 212, 0.5);
  /* 像素化边框：使用 box-shadow 模拟 */
  box-shadow:
    inset 0 0 0 1px rgba(6, 182, 212, 0.2),
    0 0 15px rgba(6, 182, 212, 0.15);
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.7);
  letter-spacing: 0.05em;
  /* 闪烁光标 */
  position: relative;
}
#landing-hint::after {
  content: '_';
  animation: blink 1s step-end infinite;
}
```

### 4.3 配色保留与微调

当前配色已经很好（深蓝 + 青色 + 品红），只需加入像素风格的点缀色：

```
现有 → 新增点缀
─────────────────────────────────────
Background: #09090B  →  不变
Primary:    #06B6D4  →  不变 (青色)
Accent:     #D946EF  →  不变 (品红)
Success:    #10B981  →  不变
Warning:    #F6C445  →  不变
Error:      #EF4444  →  不变

新增:
Pixel Blue:   #3B82F6  (Analyst)
Pixel Cyan:   #06B6D4  (Tester)
Pixel Magenta:#D946EF  (Coder)
Pixel Amber:  #F6C445  (Reviewer)

像素精灵发光色（Bloom）:
每个 Agent 有一个对应的发光色，在 Bloom 后处理中单独识别
```

---

## 5. 空间布局：Agent 在 3D 场景中的位置

### 5.1 坐标设计

以行星中心为原点 (0, 0, 0)，相机位于 z=7.8 处：

```
俯视图 (y 轴向上):

                    z=-10
                      │
              ┌───────┴───────┐
              │    行星核心     │
              │   (0, 0, -10) │
              └───────┬───────┘
                      │
     ┌────────────────┼────────────────┐
     │                │                │
  Analyst          Coder          Reviewer
(-3, 0, -2)     (3, 0, -2)     (-3, 0, 2)
     │                │                │
     └────────────────┼────────────────┘
                      │
                   Tester
                  (3, 0, 2)
                      │
                    z=+2

相机: (0, 0.4, 7.8) 看向 (0, 0.6, 0)
```

### 5.2 布局逻辑

| Agent | 位置 | 深度(z) | 角色定位 |
|-------|------|---------|---------|
| Analyst | 左前 | -2 | 面对用户，最容易被看到 |
| Coder | 右前 | -2 | 面对用户，与 Analyst 对称 |
| Reviewer | 左后 | +2 | 在行星左侧，略微被遮挡增加层次 |
| Tester | 右后 | +2 | 在行星右侧，略微被遮挡增加层次 |

**设计意图**:
- 前排两个 Agent（Analyst + Coder）是"主要接待员"，离用户最近，最活跃
- 后排两个 Agent（Reviewer + Tester）是"背景工作人员"，增加场景丰富度
- 所有 Agent 都朝向相机（Billboard），确保像素精灵始终正面可见

---

## 6. 鼠标跟随视差系统（详细设计）

### 6.1 三层视差架构

```
Layer        | Z Depth  | Parallax Factor | Elements
─────────────┼──────────┼─────────────────┼────────────────────────
Background   | -30      | 0.2x            | 星空、星云、远卫星
Midground    | -10 ~ 0  | 0.5x            | 行星、轨道环、前排 Agent
Foreground   | +2 ~ +5  | 0.9x            | 后排 Agent、UI 覆盖层
```

### 6.2 核心代码设计

```javascript
// ============================================
// Mouse Parallax System — 鼠标视差跟随
// ============================================

class MouseParallaxSystem {
  constructor(camera, scene) {
    this.camera = camera;
    this.scene = scene;

    // 鼠标目标值（归一化 [-1, 1]）
    this.target = { x: 0, y: 0 };
    // 当前插值（平滑跟随）
    this.current = { x: 0, y: 0 };

    // 视差配置
    this.config = {
      // 相机倾斜幅度（弧度）
      cameraTiltX: 0.10,  // 垂直：±0.10 rad ≈ ±5.7°
      cameraTiltY: 0.15,  // 水平：±0.15 rad ≈ ±8.6°

      // 平滑系数（越小越慢，越有惯性）
      lerpFactor: 0.05,

      // 各层视差系数
      layers: {
        background: 0.20,  // 远景星空
        midground:  0.50,  // 行星 + 前排 Agent
        foreground: 0.90,  // 后排 Agent
      }
    };

    this._bindEvents();
  }

  _bindEvents() {
    window.addEventListener('mousemove', (e) => {
      this.target.x = (e.clientX / window.innerWidth) * 2 - 1;
      this.target.y = (e.clientY / window.innerHeight) * 2 - 1;
    }, { passive: true });
  }

  update() {
    // 平滑插值到目标
    this.current.x += (this.target.x - this.current.x) * this.config.lerpFactor;
    this.current.y += (this.target.y - this.current.y) * this.config.lerpFactor;

    const { x, y } = this.current;
    const cfg = this.config;

    // 1. 相机微倾斜（围绕 lookAt 点旋转）
    // 注意：这里只做微小偏移，不覆盖现有的相机动画系统
    this.cameraOffset = {
      x: x * cfg.cameraTiltY * 2,  // 水平偏移
      y: y * cfg.cameraTiltX * 2,  // 垂直偏移
    };

    // 2. 应用到各层物体
    this._applyToLayer('background', cfg.layers.background);
    this._applyToLayer('midground', cfg.layers.midground);
    this._applyToLayer('foreground', cfg.layers.foreground);
  }

  _applyToLayer(layerName, factor) {
    const objects = this.scene.userData.parallaxLayers?.[layerName];
    if (!objects) return;

    for (const obj of objects) {
      if (!obj.userData.basePosition) {
        obj.userData.basePosition = obj.position.clone();
      }

      const base = obj.userData.basePosition;
      obj.position.x = base.x + this.current.x * factor * 3;
      obj.position.y = base.y - this.current.y * factor * 2;
      // Z 轴也有轻微偏移，增加立体感
      obj.position.z = base.z + Math.abs(this.current.x) * factor * 0.5;
    }
  }

  // 获取当前相机偏移（供主渲染循环使用）
  getCameraOffset() {
    return this.cameraOffset;
  }
}
```

### 6.3 Agent 注视鼠标（Gaze Tracking）

```javascript
// ============================================
// Agent Gaze System — Agent 注视鼠标
// ============================================

class AgentGazeSystem {
  constructor(camera) {
    this.camera = camera;
    this.agents = [];
    this.mouseNDC = new THREE.Vector2();

    window.addEventListener('mousemove', (e) => {
      this.mouseNDC.x = (e.clientX / window.innerWidth) * 2 - 1;
      this.mouseNDC.y = -(e.clientY / window.innerHeight) * 2 + 1;
    }, { passive: true });
  }

  addAgent(spriteMesh, personality = 'calm') {
    this.agents.push({
      mesh: spriteMesh,
      personality,
      // 每个 Agent 有独立的注视参数
      gazeIntensity: this._getGazeIntensity(personality),
      blinkTimer: Math.random() * 3,
      isBlinking: false,
    });
  }

  _getGazeIntensity(personality) {
    // 不同性格的注视灵敏度
    const map = {
      calm: 0.06,      // Analyst: 冷静，不怎么动
      focused: 0.04,   // Coder: 专注时降低
      strict: 0.08,    // Reviewer: 严厉，盯得紧
      curious: 0.10,   // Tester: 好奇，东张西望
    };
    return map[personality] || 0.06;
  }

  update(deltaTime) {
    for (const agent of this.agents) {
      this._updateAgentGaze(agent, deltaTime);
      this._updateAgentBlink(agent, deltaTime);
    }
  }

  _updateAgentGaze(agent, dt) {
    const { mesh, gazeIntensity } = agent;

    // 将 Agent 的世界坐标投影到屏幕空间
    const agentScreenPos = mesh.position.clone().project(this.camera);

    // 计算从 Agent 到鼠标的方向
    const dirX = this.mouseNDC.x - agentScreenPos.x;
    const dirY = this.mouseNDC.y - agentScreenPos.y;

    // 目标旋转（Billboard 基础旋转 + 注视偏移）
    // 注意：Billboard 已经让精灵面朝相机，这里只做微小倾斜
    const targetRotY = dirX * gazeIntensity;  // 水平偏头
    const targetRotX = dirY * gazeIntensity * 0.6;  // 垂直仰头（幅度小一点）

    // 平滑旋转（每个 Agent 有自己的惯性）
    mesh.rotation.y += (targetRotY - mesh.rotation.y) * 0.04;
    mesh.rotation.x += (targetRotX - mesh.rotation.x) * 0.04;

    // 轻微的位置偏移（模拟身体前倾看）
    if (!mesh.userData.basePos) {
      mesh.userData.basePos = mesh.position.clone();
    }
    const base = mesh.userData.basePos;
    mesh.position.x = base.x + dirX * gazeIntensity * 0.3;
    mesh.position.y = base.y + dirY * gazeIntensity * 0.2;
  }

  _updateAgentBlink(agent, dt) {
    agent.blinkTimer -= dt;

    if (agent.blinkTimer <= 0) {
      // 触发眨眼（切换 sprite 帧到闭眼帧）
      agent.isBlinking = true;
      agent.blinkTimer = 0.15;  // 闭眼持续时间

      // 切换到闭眼材质帧
      this._setSpriteFrame(agent.mesh, 'blink');
    } else if (agent.isBlinking && agent.blinkTimer <= 0) {
      // 睁眼
      agent.isBlinking = false;
      agent.blinkTimer = 2 + Math.random() * 4;  // 下次眨眼间隔 2-6 秒
      this._setSpriteFrame(agent.mesh, 'idle');
    }
  }

  _setSpriteFrame(mesh, frameName) {
    // 切换 sprite sheet 的 UV 偏移
    const map = mesh.material.map;
    if (!map || !mesh.userData.frameUVs) return;

    const uv = mesh.userData.frameUVs[frameName];
    if (uv) {
      map.offset.set(uv.x, uv.y);
    }
  }
}
```

### 6.4 交互行为矩阵

| 用户行为 | Agent 反应 |
|---------|-----------|
| 鼠标缓慢移动 | 所有 Agent 平滑转头跟随 |
| 鼠标快速划过 | Agent 快速偏头 + 短暂"惊讶"帧 |
| 鼠标悬停某 Agent | 该 Agent 向前微移(z+0.3) + 发光增强 + 显示名字标签 |
| 鼠标移出 | 平滑回到原位 |
| 长时间无鼠标移动 | Agent 进入"待机巡逻"：缓慢左右转头 |
| 页面加载完成 | Agent 从透明渐入 + 依次"登场"动画 |

---

## 7. 戏剧性进入动画

### 7.1 时间线设计

```
时间轴 (页面加载后):

0.0s  ─┬─ 黑色屏幕
       │
0.5s   ├─  curtain 文字: "NEUROFORGE · INITIALIZING" 打字机效果
       │   "正在建立量子场渲染管线…" 逐字显示
       │
2.0s   ├─  星空从中心点爆开出现（粒子从中心向四周扩散）
       │
2.5s   ├─  行星核心从点光源渐显，表面纹理加载
       │
3.0s   ├─  轨道环从透明渐显，开始旋转
       │
3.5s   ├─  Analyst 从左侧滑入 (+ 像素化渐入效果)
       │
3.8s   ├─  Coder 从右侧滑入
       │
4.1s   ├─  Reviewer 从左后方滑入
       │
4.4s   ├─  Tester 从右后方滑入
       │
4.8s   ├─  所有 Agent 一起面向用户（Billboard 对齐）
       │   + 名字标签依次弹出
       │
5.0s   ├─  UI 文字层淡入：
       │   "// AUTONOMOUS INTELLIGENCE ENGINE"
       │   "MULTI ⇄ AGENT ⇝ CODE ↯ SYSTEM"
       │
5.3s   ├─  "NeuroForge" 大标题字母逐个从乱码解码为正确文字
       │   (Matrix 风格字符滚动效果)
       │
5.8s   ├─  副标题 + "点击任意位置进入系统" 淡入
       │
6.0s   └─  进入动画完成，开始 Idle 循环
```

### 7.2 关键动画效果

**Agent 登场**: 每个 Agent 入场时使用 **像素化渐入**（Pixelate In）：
- 从 1×1 像素块开始
- 逐步增加到 32×32 清晰像素
- 同时从透明到不透明
- 配合轻微的"弹跳"（overshoot）落地感

```javascript
// 像素化渐入
function pixelateIn(sprite, duration = 800) {
  const startTime = performance.now();

  function update(now) {
    const elapsed = now - startTime;
    const progress = Math.min(elapsed / duration, 1);

    // 使用 easeOutBack 弹跳曲线
    const ease = easeOutBack(progress);

    // 像素化程度：从高(32)到低(1)
    const pixelation = Math.floor(32 * (1 - ease)) + 1;
    sprite.material.map.magFilter = THREE.NearestFilter;
    sprite.scale.setScalar(ease);
    sprite.material.opacity = ease;

    if (progress < 1) requestAnimationFrame(update);
  }

  requestAnimationFrame(update);
}
```

---

## 8. 技术实现要点

### 8.1 Sprite Sheet 系统

每个 Agent 使用一张 **Sprite Sheet**（精灵图集），包含所有动画帧：

```
Sprite Sheet (128×128, 4×4 网格):
┌────────┬────────┬────────┬────────┐
│ Idle_1 │ Idle_2 │ Work_1 │ Work_2 │
├────────┼────────┼────────┼────────┤
│ Work_3 │ Work_4 │ Done   │ Error  │
├────────┼────────┼────────┼────────┤
│ Blink  │ Wave   │ Jump   │ (预留) │
├────────┼────────┼────────┼────────┤
│ (预留) │ (预留) │ (预留) │ (预留) │
└────────┴────────┴────────┴────────┘
每帧: 32×32 像素
```

**Three.js 实现**:
```javascript
const textureLoader = new THREE.TextureLoader();
const spriteSheet = textureLoader.load('assets/agents/coder-sheet.png');
spriteSheet.magFilter = THREE.NearestFilter;  // 关键：保持像素感
spriteSheet.minFilter = THREE.NearestFilter;
spriteSheet.wrapS = THREE.ClampToEdgeWrapping;
spriteSheet.wrapT = THREE.ClampToEdgeWrapping;

// 每帧 UV 计算
const frameSize = 1 / 4;  // 4×4 网格
const frameUVs = {
  idle_1: { x: 0 * frameSize, y: 3 * frameSize },
  idle_2: { x: 1 * frameSize, y: 3 * frameSize },
  // ...
};

const material = new THREE.SpriteMaterial({
  map: spriteSheet,
  transparent: true,
  opacity: 0.9,
});

const sprite = new THREE.Sprite(material);
sprite.scale.set(1.5, 1.5, 1);  // 世界空间大小
```

### 8.2 Billboard + 深度排序

Agent 是 2D Sprite，但在 3D 空间中：
- 始终面向相机（Billboard，Three.js Sprite 自动实现）
- 根据 z 深度正确排序（确保近处 Agent 遮挡远处）
- 半透明混合使用 `AdditiveBlending` 增加发光感

### 8.3 Bloom 后处理集成

现有 Bloom 系统需要识别 Agent 发光色：

```javascript
// 在 Bloom 后处理中，Agent 使用高亮颜色
// 通过设置材质 emissive 或直接使用发光色
// UnrealBloomPass threshold 设为 0.4 时，这些颜色会辉光

const AGENT_GLOW_COLORS = {
  analyst:  new THREE.Color('#3B82F6'),
  coder:    new THREE.Color('#D946EF'),
  reviewer: new THREE.Color('#F6C445'),
  tester:   new THREE.Color('#06B6D4'),
};
```

### 8.4 性能预算

| 指标 | 预算 | 当前状态 |
|------|------|---------|
| Draw Calls | < 50 | ~30（已有） |
| 像素精灵 | 4 个 | 新增 |
| Sprite Sheet 总大小 | < 256KB | 4×128×128×4 = 256KB |
| 额外 JS 逻辑 | < 5KB gzip | 预计 3KB |
| 帧率影响 | 0fps | 几乎无（Sprite 极轻量） |

---

## 9. 实现优先级

```
Phase 1: 基础像素 Agent (3 天)
  ├── 创建 4 个 Agent 的 32×32 像素精灵图（各 8 帧）
  ├── 实现 SpriteSheet 加载和 UV 切换系统
  ├── 在 Three.js 场景中添加 4 个 Sprite
  ├── 实现 Billboard 面向相机
  └── 基础 Idle 动画循环

Phase 2: 鼠标视差 + 注视 (2 天)
  ├── 深度分层视差系统
  ├── Agent 注视鼠标（Gaze Tracking）
  ├── 眨眼系统
  └── 鼠标悬停交互（前移 + 高亮 + 名字标签）

Phase 3: 进入动画 + UI 融合 (2 天)
  ├── Agent 像素化渐入动画
  ├── 标题 Matrix 解码效果
  ├── UI 覆盖层像素风格改造
  └── 打字机效果 + 扫描线

Phase 4: 打磨 (可选, 2 天)
  ├── Agent 对话气泡（随机台词）
  ├── 工作状态下 Agent 动画切换
  ├── 粒子数据束（Agent → 行星）
  └── 性能优化 + 移动端降级
```

**总计: 7-9 天出一个可演示的 Landing Page**

---

## 10. 与完整 3D 指挥中心的衔接

这个 Landing Page 的像素 Agent 系统**直接复用**到后续的 3D 空间指挥中心：

```
Landing Page (现在):
  └── 4 个像素 Agent 在行星周围
      └── 注视鼠标 + 视差跟随

Dashboard (下一步):
  └── 相同的 4 个像素 Agent
      └── 出现在 3D 空间的不同位置
      └── 相同的行为系统（注视、眨眼、状态动画）
      └── 新增：与 UI 面板的数据流连接
```

**复用率: ~80%**

---

## 11. 一句话总结

> **在当前 A+ 行星场景中加入 4 个会注视你的 32×32 像素 Agent，让整个 Landing Page 从"看风景"变成"进入一个有生命的人工智能指挥中心"。**
