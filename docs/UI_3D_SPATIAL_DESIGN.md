# NeuroForge v3 — 3D 空间沉浸式界面设计方案

> **设计宣言**: "Not a website. Not a dashboard. A spatial command center."
> 不做一个网页，不做一个面板。做一个 3D 空间指挥中心。

---

## 1. 为什么必须是 3D？

### 1.1 数据说话

Awwwards 2026 H1 数据：
- **61%** 的 Site of the Day 获奖网站是沉浸式 3D（2024 年仅 23%）
- 3D 网站创意平均分 **8.7/10**，扁平布局 **6.4/10**
- 评委明确表态："创造力的标准已从排版精度转向空间思维和实时 3D 交互"

### 1.2 你的项目天然适合 3D

NeuroForge 已经有一个非常棒的 Three.js 3D 背景系统（A+ 行星着色器、粒子流场、God Rays）。问题在于：**背景是 3D 的，但 UI 还是 2D DOM 叠在上面**。

新版方案核心思路：**把 UI 面板本身放进 3D 空间**，让整个界面成为一个可以旋转、缩放、探索的虚拟空间。

### 1.3 参考作品

| 作品 | 核心技法 | 借鉴要点 |
|------|---------|---------|
| **Bruno Simon 2025 WebGPU Portfolio** | 所有 UI 元素都是 3D 物体，零 HTML 界面 | 相机自由旋转，双击聚焦某个物体 |
| **Cartier Watches & Wonders 2026** | 每件产品一个 3D 房间，滚动切换 | 空间分区叙事，场景间平滑过渡 |
| **Apple Vision Pro visionOS 26** | Glass 材质、空间小组件、深度感 | 毛玻璃面板浮在空间中，可固定位置 |
| **NeuroPulse (GitHub)** | 赛博 AI 面板 + Three.js + 霓虹效果 | HUD 全息风格，数据可视化在 3D 表面 |
| **Jarvis (钢铁侠风格)** | 全息投影 + 手势交互 + 3D 地球 | 科幻指挥中心氛围，浮空操作面板 |
| **3D Virtual Workspace** | 全息屏幕 + 浮动书籍 + AI 脑缸 | 开发者空间主题，元素可交互 |
| **Oryzo AI** (Awwwards Site of Day) | 实时 3D 渲染 + 粒子 + 材质 | 产品展示用重量感 3D 物体 |

---

## 2. 核心概念：3D 空间指挥中心

### 2.1 场景描述

用户进入后看到的不是网页，而是一个**虚拟的科幻指挥中心**：

```
                          ┌─────────────────────┐
                          │   Cmd+K 命令面板     │  ← 浮在最上层
                          └─────────────────────┘
                                  ↑
        ┌──────────────────────────────────────────────┐
        │          Pipeline 节点环 (头顶悬浮)             │
        │   [需求]─→[生成]─→[评审]─→[测试]─→[交付]      │  ← 第二层 (高)
        │          Agent 状态 + KPI 卡片                │
        └──────────────────────────────────────────────┘
                                  │
    ┌──────────┐         ┌──────────────┐         ┌──────────┐
    │ 左侧任务栏 │  ←──→  │  正面主控台   │  ←──→  │ 右侧监控台 │  ← 第三层 (中)
    │ 历史/技能  │         │ 对话流+输入   │         │ 产物/评审  │
    └──────────┘         └──────────────┘         └──────────┘
                                  │
                    ┌─────────────────────────┐
                    │   状态栏 + 粒子反馈带     │  ← 第四层 (低)
                    └─────────────────────────┘

  背景层: 深空星云 + 流场粒子 + 能量核心行星 (当前已有的 A+ 3D 场景)
```

### 2.2 相机的四种模式

借鉴 Bruno Simon 的 3D 导航系统 + Cartier 的场景切换：

| 模式 | 触发方式 | 效果 |
|------|---------|------|
| **待机注视 (Idle Gaze)** | 无操作，仅移动鼠标 | 场景微微跟随鼠标做视差偏移，Agent "注视"鼠标 |
| **自由探索** | 鼠标拖拽旋转 / 滚轮缩放 | 任意角度观察整个空间 |
| **预设视角** | 键盘 1/2/3 或侧边栏按钮 | 快速跳到正面/头顶/右侧固定视角 |
| **面板聚焦** | 双击某个面板 | 相机平滑拉近该面板，其他元素半透明 |

### 2.3 鼠标视差跟随系统 (Mouse Parallax)

**这是让场景"活过来"的灵魂机制。** 用户的鼠标移动不再是死板的指针位移——它变成了 Agent 们感知到的"存在感"，整个 3D 空间会微微倾斜、人物会偏头看你、前景后景以不同速率偏移。

#### 2.3.1 核心公式

```javascript
// 鼠标在屏幕上的归一化坐标，范围 [-1, 1]
const mouseNDC = {
  x: (mouse.clientX / window.innerWidth) * 2 - 1,   // -1(左) → +1(右)
  y: (mouse.clientY / window.innerHeight) * 2 - 1,  // -1(上) → +1(下)
};

// 平滑插值到目标值（缓动跟随，不是瞬间跳变）
const lerp = (a, b, t) => a + (b - a) * t;

// 每帧更新
target.orbitY += mouseNDC.x * 0.15;   // 水平旋转范围：±0.15 弧度 ≈ ±8.6°
target.orbitX += mouseNDC.y * 0.10;   // 垂直旋转范围：±0.10 弧度 ≈ ±5.7°
current.orbitY = lerp(current.orbitY, target.orbitY, 0.05);
current.orbitX = lerp(current.orbitX, target.orbitX, 0.05);
```

**设计要点：**
- 幅度很小（±8° 以内），不能喧宾夺主影响功能操作
- 使用 `lerp` 缓动（0.05 系数），有"惯性"感，不跟手
- 垂直灵敏度低于水平（人类对水平偏移更不敏感）

#### 2.3.2 深度分层视差

不同 z 深度的物体，位移系数不同——离相机越近，看起来位移越大：

```
Depth Layer    |  z 范围  |  位移系数  |  元素
───────────────┼──────────┼───────────┼──────────────
 远景 (Far)     |  -20     |  0.3x     |  星空、星云、行星
 中景 (Mid)     |  -5~0    |  0.6x     |  像素 Agent 角色
 近景 (Near)    |  +5      |  1.0x     |  Glass UI 面板
```

```javascript
// 每个物体的实际偏移 = 基础相机偏移 × 深度系数
function applyParallax(object3D, depthLayer) {
  const factor = PARALLAX_FACTORS[depthLayer]; // 0.3 / 0.6 / 1.0
  object3D.position.x = baseX + cameraOffsetX * factor;
  object3D.position.y = baseY + cameraOffsetY * factor;
}
```

#### 2.3.3 Agent 注视鼠标 (Gaze Tracking)

像素角色不只是随视差平移，还会"看向"鼠标方向，仿佛感知到你的存在：

```javascript
// 对每个 Agent（Billboard Sprite）
function updateAgentGaze(agent, mouseNDC) {
  // 计算 Agent 到鼠标在屏幕空间的方向
  const agentScreenPos = agent.position.clone().project(camera);
  const gazeDir = new THREE.Vector2(
    mouseNDC.x - agentScreenPos.x,
    mouseNDC.y - agentScreenPos.y
  ).normalize();

  // 目标旋转：Billboard 朝向 → 朝相机 + 朝鼠标微倾
  const targetRotation = agent.baseRotation.clone();
  targetRotation.y += gazeDir.x * 0.08;  // 水平偏头
  targetRotation.x += gazeDir.y * 0.05;  // 垂直仰头/低头

  // 平滑旋转
  agent.rotation.y = lerp(agent.rotation.y, targetRotation.y, 0.03);
  agent.rotation.x = lerp(agent.rotation.x, targetRotation.x, 0.03);
}
```

#### 2.3.4 行为细节

| 场景 | 行为 |
|------|------|
| **鼠标悬停在某个 Agent 上方** | 该 Agent 微微靠近（z 前移），眨眼频率降低 |
| **鼠标快速划过** | 所有 Agent 头部齐刷刷追随，产生"围观"感 |
| **Agent 正在工作** | 注视灵敏度降低 50%，专注模式 |
| **Agent 空闲** | 注视灵敏度提升，偶尔有小动作（眨眼、摆头） |
| **进入面板聚焦模式** | 视差跟随暂停，相机锁定目标面板 |

---

## 3. 3D 材质与视觉语言

### 3.1 全息 Glass 面板 (核心视觉特征)

每个 UI 面板在 3D 空间中是一个**半透明玻璃板**，类似 Apple Vision Pro 的 Glass Material：

```
面板结构:
┌──────────────────────────────────┐
│  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  │  ← Cyan 发光边框 (shader)
│  ┌────────────────────────────┐  │
│  │  半透明 Glass 表面           │  │  ← 磨砂玻璃透过看到星空
│  │  backdrop 实时模糊背景       │  │
│  │                            │  │
│  │  HUD 文字 (品红微光)         │  │  ← text-shadow glow
│  │  数据可视化                 │  │
│  │                            │  │
│  └────────────────────────────┘  │
└──────────────────────────────────┘
      ↕ 粒子数据束连接到其他面板
```

**材质实现**:
- `MeshPhysicalMaterial` + `transmission: 0.6` + `roughness: 0.15`
- 边缘发光：自定义 Shader，fresnel 边缘高亮
- 表面内容通过 CanvasTexture 动态渲染到 3D 平面上
- 面板间用粒子束/贝塞尔曲线连接表示数据流动

### 3.2 辉光系统 (Bloom + Glow)

```javascript
// 后处理链
renderer → UnrealBloomPass (threshold: 0.4, strength: 0.8, radius: 0.5)
        → FilmPass (grain: 0.15)        // 胶片颗粒 = 人性化纹理
        → ChromaticAberration (0.002)    // 微色差 = 全息感
        → Vignette (暗角)
```

**分层 Bloom**:
- 面板边框: 青色辉光 `#06B6D4`
- 活跃状态: 品红脉冲 `#D946EF`
- 关键指标: 金色高亮 `#F6C445`
- 数据连接线: 低强度白色辉光

### 3.3 现有 3D 资产的复用与升级

NeuroForge 现有的 A+ 3D 场景**全部保留并增强**：

| 现有资产 | 新角色 |
|---------|-------|
| 行星 (PBR + SSS) | 空间中心能量核心，Agent 活动时表面脉动 |
| 流场粒子 (GPU Ping-Pong) | 环绕面板飞行，数据流动可视化 |
| God Rays | 从能量核心射出，指向活跃面板 |
| 神经网络拓扑 | 面板间动态连线，Agent 协作可视化 |
| 冲击波 | 任务完成/评审通过时的庆祝反馈 |
| 轨道卫星 | 每个卫星代表一个活跃任务 |
| 量子概率场 | Pipeline 阶段的不确定性可视化 |

---

## 4. 四大 3D 面板详解

### 4.1 正面主控台 (Front Console)

位置: 用户正前方，最常用面板

```
┌─────────────────────────────────────────┐
│  NEUROFORGE COMMAND                     │
│  ┌─────────────────────────────────────┐│
│  │                                     ││
│  │  [用户消息]  [AI 消息 + 结构化卡片]   ││  ← 对话流
│  │                                     ││
│  │  ...可滚动...                        ││
│  └─────────────────────────────────────┘│
│  ┌─────────────────────────────────────┐│
│  │ > 描述你的需求...          [+] [发送] ││  ← 输入区
│  │  Skills: [python] [react] [api]      ││
│  └─────────────────────────────────────┘│
└─────────────────────────────────────────┘
```

**3D 特性**:
- Glass 半透明，透过可以看到背后的星空
- AI 回复到达时：面板边缘脉冲青色光
- 输入时：面板底部流动粒子光带

### 4.2 头顶悬浮面板 (Overhead Panel)

位置: 正面上方，抬头可见

```
┌──────────────────────────────────────────┐
│  ┌─ Pipeline ────────────────────────┐  │
│  │ [需求]─→[生成]─→[评审]─→[测试]─→[交付]│  │
│  │  ●done   ●run    ○wait   ○wait   ○  │  │
│  └────────────────────────────────────┘  │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐  │
│  │Token │ │ 耗时  │ │ 通过  │ │Agent │  │  ← Bento KPI 卡片
│  │ 1.2k │ │ 34s  │ │ 94%  │ │ 3/4  │  │
│  └──────┘ └──────┘ └──────┘ └──────┘  │
└──────────────────────────────────────────┘
```

**3D 特性**:
- 略微向后倾斜 15°，模拟抬头看仪表盘的感觉
- Pipeline 节点是 3D 小球体，运行时发光 + 旋转
- KPI 卡片是浮在面板上方的独立小玻璃片

### 4.3 右侧监控台 (Right Monitor)

位置: 右侧面，可独立旋转朝向用户

```
┌──────────────────────┐
│  [产物] [评审] [测试] │  ← Tab 切换
│                      │
│  ┌─ 生成文件 ──────┐ │
│  │ pool.py    156L │ │  ← 文件树 + 代码预览
│  │ pool_test  89L  │ │
│  │ __init__.py 12L │ │
│  └─────────────────┘ │
│  ┌─ 评审结果 ──────┐ │
│  │ ● crit: 2       │ │
│  │ ● high: 1       │ │  ← 严重性色块
│  │ ○ med: 3        │ │
│  └─────────────────┘ │
│  ┌─ 测试报告 ──────┐ │
│  │ ████████░░ 87%  │ │  ← 覆盖率进度条
│  │ 12/14 passed    │ │
│  └─────────────────┘ │
└──────────────────────┘
```

**3D 特性**:
- 可以拖拽旋转面板角度（拉近看代码）
- 文件列表悬浮在玻璃面上方 5px，有微投影

### 4.4 左侧任务栏 (Left Panel)

位置: 左侧，竖向窄条

```
┌──────────────┐
│ 历史任务       │
│ ┌──────────┐ │
│ │ 连接池    │ │  ← 带状态圆点的任务卡片
│ │ ● done   │ │
│ ├──────────┤ │
│ │ 排序算法   │ │
│ │ ● done   │ │
│ ├──────────┤ │
│ │ API网关   │ │
│ │ ◉ run    │ │  ← 活跃中
│ ├──────────┤ │
│ │ 认证系统   │ │
│ │ ○ queue  │ │  ← 队列中
│ └──────────┘ │
│              │
│ 快捷技能       │
│ [python-dev]  │
│ [react-comp]  │
│ [api-design]  │
└──────────────┘
```

**3D 特性**:
- 略微向左倾斜 10°
- 活跃任务有粒子从卡片飘向 Pipeline 头顶面板

---

## 5. 核心交互设计

### 5.1 鼠标/触控板 Gesture

```
移动鼠标      → 场景视差跟随 + Agent 注视鼠标（Idle Gaze 模式）
拖拽空白区域  → 旋转整个空间的观察角度（覆盖 Idle Gaze）
滚轮          → 拉近/拉远（缩放）
双击面板      → 相机飞向该面板，其他面板缩小 + 半透明以聚焦
右键拖拽      → 平移相机位置
中键点击      → 重置到默认视角 1
```

### 5.2 键盘快捷键

```
1/2/3        → 切换预设视角（正面/头顶/右侧）
R            → 重置视角
Tab          → 循环聚焦面板
Space        → 暂停/恢复 3D 动画
Esc          → 退出聚焦，回到全局视角
Cmd/Ctrl+K   → 打开命令面板（3D 空间中弹出）
```

### 5.3 面板交互

- **Hover**: 边缘辉光增强 + 轻微上浮（translateZ +5px）
- **Click**: 面板激活，边框变品红
- **Double Click**: 相机聚焦该面板
- **Pin 固定**: 拖拽面板到空间任意位置并固定
- **Resize**: 四角拖拽调整面板大小（最小 300px, 最大全屏）

### 5.4 数据流动可视化

借鉴 NeuroPulse 和钢铁侠 Jarvis：
- 任务执行时：能量核心行星发射**粒子束**到对应 Agent 面板
- Pipeline 阶段切换：**流光脉冲**沿节点连线传递
- 数据更新：**数字跳动动画** (count-up effect)
- 错误发生：**红色冲击波**从错误位置扩散

---

## 6. 技术架构

### 6.1 核心技术栈

```
┌─────────────────────────────────────┐
│              HTML Overlay           │  ← 仅用于 Cmd+K / Tooltip / 通知
├─────────────────────────────────────┤
│           Three.js Scene             │  ← 核心渲染层
│  ┌──────────┐  ┌──────────────────┐ │
│  │ 3D 背景   │  │ 3D UI 面板系统    │ │
│  │ (已有)    │  │ (新增)           │ │
│  │ · 行星    │  │ · GlassPanel     │ │
│  │ · 粒子    │  │ · HologramText   │ │
│  │ · 星云    │  │ · NeonGlow       │ │
│  │ · 光晕    │  │ · DataBeam       │ │
│  └──────────┘  └──────────────────┘ │
├─────────────────────────────────────┤
│      React State Management         │  ← 数据层
│      Zustand store → CanvasTexture  │
├─────────────────────────────────────┤
│         API Layer (SSE/WS)          │  ← 实时通信
└─────────────────────────────────────┘
```

### 6.2 CanvasTexture 方案 (关键)

UI 面板的内容通过 Canvas 2D 渲染到 Three.js 平面贴图上：

```javascript
// 每个面板 = PlaneGeometry + CanvasTexture
const canvas = document.createElement('canvas');
canvas.width = 1024;
canvas.height = 768;
const ctx = canvas.getContext('2d');

// React 管理数据 → 绘制到 Canvas
function renderPanel(data) {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  // 绘制 Glass 背景
  ctx.fillStyle = 'rgba(9, 9, 11, 0.75)';
  // 绘制文字/卡片/图表...
  // ...
  texture.needsUpdate = true;
}

const material = new THREE.MeshPhysicalMaterial({
  map: texture,
  roughness: 0.15,
  metalness: 0.05,
  clearcoat: 0.1,
  transparent: true,
  opacity: 0.9,
});

const panel = new THREE.Mesh(geometry, material);
```

**为什么是 Canvas2D 而不是 DOM-over-3D？**
- Canvas 的内容天然随 3D 空间旋转/缩放（DOM 需要 CSS 3D transform 同步，复杂且易出 bug）
- 性能更好，不需要 GPU 合成 DOM 层
- 全息效果（glow/bloom）可以统一后处理
- 参考：Bruno Simon 2025 的所有 UI 都在 3D 空间内

### 6.3 React 集成 (React Three Fiber 可选)

```
Phase 1 (快速启动):
  Vanilla Three.js + Zustand store → Canvas2D UI
  = 在现有代码基础上，给面板加 3D 定位和材质

Phase 2 (组件化):
  React Three Fiber + @react-three/drei
  = 声明式 3D 组件，更容易维护
```

---

## 7. 性能策略

### 7.1 分级渲染

```
High (Desktop 独显):
  · Bloom + Chromatic Aberration 后处理
  · 4096 粒子流场
  · PBR 行星着色器
  · Canvas 面板 1024x768 分辨率

Medium (Desktop 集显 / 高性能笔记本):
  · Bloom 仅面板边框
  · 2048 粒子
  · 简化行星着色器
  · Canvas 面板 768x512

Low (移动端):
  · 无后处理
  · 静态星空背景
  · Canvas 面板 512x384
  · 固定视角，不可旋转
```

### 7.2 自动检测

```javascript
// GPU 性能检测
const gl = renderer.getContext();
const gpu = gl.getParameter(gl.RENDERER);
const tier = detectGPUTier(gpu);

// prefers-reduced-motion 检测
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
if (reduceMotion) {
  // 所有动画静态化，仅渲染关键帧
}
```

---

## 8. 分阶段实施计划

```
Phase 1: 3D 面板基础 + 像素 Agent + 鼠标视差 (Week 1-2)
  ├── 实现 GlassPanel 3D 类 (PlaneGeometry + CanvasTexture)
  ├── 正面主控台在 3D 空间中渲染
  ├── 面板辉光边框 Shader
  ├── 创建第一个像素 Agent (Coder) Billboard Sprite + 帧动画
  ├── 鼠标视差跟随系统 (Camera + 深度分层)
  ├── Agent 注视鼠标系统 (Gaze Tracking)
  └── 基本 3D 导航 (OrbitControls)

Phase 2: 多面板 + 空间布局 (Week 3-4)
  ├── 头顶监控面板 + Bento KPI 卡片
  ├── 右侧监控台 + 左侧任务栏
  ├── 面板间粒子连接束
  └── 预设视角 (1/2/3) + 聚焦动画

Phase 3: 数据流 + 交互 (Week 5-6)
  ├── SSE 数据驱动 Canvas 内容更新
  ├── Pipeline 节点 3D 可视化
  ├── Agent 状态在 3D 空间中的反馈
  └── 命令面板 (Cmd+K)

Phase 4: 打磨 (Week 7-8)
  ├── Bloom/Glow 后处理调参
  ├── 数据粒子流动画优化
  ├── 性能分级 + 移动端降级
  ├── 过渡动画 (Camera FlyTo)
  └── 可访问性 fallback
```

---

## 9. 与之前 2D 方案的对比

| 维度 | 2D Workbench 方案 | 3D 空间方案 |
|------|-----------------|------------|
| **视觉冲击力** | 中高 (Bento Grid + 暗色主题) | **极高** (Awwwards 获奖级) |
| **差异化** | 与 Linear/v0 类似 | **独一无二**，业界罕见 |
| **面试加分** | 高 | **超高** (面试官会记住) |
| **开发难度** | 中等 (React + Tailwind) | 较高 (Three.js + Shader) |
| **现有资产复用** | 低 (重写前端) | **高** (保留所有 3D 代码) |
| **可维护性** | 高 (标准组件) | 中等 (3D 逻辑复杂) |
| **移动端体验** | 好 (响应式) | 需精心降级 |

---

## 10. 下一步行动

**推荐路径**: 从 Phase 1 开始，先在当前项目中实现 **一个 3D Glass 面板** 作为 POC，验证技术可行性和视觉效果。如果效果好，继续推进后续 Phase。

具体来说：
1. 在现有 `index.html` 的 Three.js 场景中，添加一个 PlaneGeometry 面板
2. 用 Canvas 2D 画上对话内容
3. 加上边缘辉光 Shader
4. 跑通 `鼠标拖拽旋转面板` 的交互

7 天就能出一个可演示的 Demo。

---

> **一句话总结**: 把 UI 面板从 `position: fixed` 的 DOM 元素变成 Three.js 空间中的全息玻璃板，让 NeuroForge 从一个 "AI 聊天工具" 进化成一个 "AI 指挥中心"。
