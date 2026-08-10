# NeuroForge v3 — 全新 UI/UX 设计方案

> **设计师备忘录**: 本方案基于 Awwwards、Dribbble、Product Hunt、GitHub 等平台 2025-2026 获奖/高赞 UI 设计趋势研究。参考了 Linear、Vercel v0.dev、Cursor IDE、shadcn/ui、Lovable 等顶级开发者工具的界面设计理念。

---

## 1. 业界设计调研摘要

### 1.1 获奖/好评平台与吸收要点

| 平台/产品 | 获得荣誉 | 核心借鉴 |
|-----------|---------|---------|
| **Linear.app** | Product Hunt #1, 开发者社区公认最美项目管理工具 | 极简暗色主题、Command+K 命令面板、流畅键盘导航 |
| **Vercel v0.dev** | Product Hunt 年度产品, shadcn/ui 生态核心 | Bento Grid 布局、shadcn/ui 组件、Tailwind 设计令牌 |
| **Cursor IDE** | Nvidia/Jensen Huang 公开推荐, YC 80%+ 采用率 | Agent 模式 UI、Mission Control 窗口管理、内联 Diff |
| **Lovable** | Product Hunt #1, $6.6B 估值 | Visual Edits 面板、一键部署、设计+代码双视图 |
| **Oryzo AI** (by Lusion) | Awwwards Site of the Day 2026 | 暗色科幻美学、Three.js 3D 场景、流光粒子系统 |
| **Spotify Wrapped** (by Active Theory) | Awwwards Site of the Day 2026 | 数据故事化可视化、渐变色彩叙事、滚动触发动画 |

### 1.2 2026 核心设计趋势

1. **Bento Grid 布局** — 非对称卡片网格，取代传统三段式布局，信息密度与可扫读性兼得
2. **Glassmorphism 2.0 / Liquid Glass** — 基于 `backdrop-filter` 的毛玻璃效果，Apple Vision Pro/ iOS 风格
3. **Dark Mode 2.0** — 自适应暗色模式 (时间/环境光感应)，暗色从 "可选项" 升级为 "默认"
4. **90% Neutral + 10% Accent** — 大面积中性色基底 + 单一高饱和强调色 (Linear/Vercel/Raycast 已验证)
5. **Micro-interactions** — 功能性微动效取代纯装饰动画：确认、引导、错误反馈
6. **Human Texture** — AI 生成视觉泛滥下的 "人性化纹理"：噪点叠加、微妙的非对称、手工感图标
7. **Variable Fonts + Fluid Typography** — 单字体文件覆盖全字重，CSS `clamp()` 流式缩放

---

## 2. 设计理念

### 2.1 设计原则

```
┌─────────────────────────────────────────────────┐
│  "Command Center, not Chat Window"               │
│  指挥中心，而非聊天窗口                              │
│                                                   │
│  NeuroForge 是一款专业的多智能体协同开发工具。       │
│  它的界面应该像飞行驾驶舱 —— 信息密度高、             │
│  操作路径短、状态一目了然 —— 而不是一个聊天机器人。    │
└─────────────────────────────────────────────────┘
```

**六条核心原则**:

1. **Glanceable (一眼可读)** — KPI 指标、Agent 状态、Pipeline 进度应在 200ms 内被理解
2. **Keyboard-first (键盘优先)** — 所有高频操作支持 `Cmd/Ctrl+K` 命令面板，减少鼠标依赖
3. **Progressive disclosure (渐进信息披露)** — 默认展示关键信息，详情按需展开
4. **Consistent feedback (一致反馈)** — 每个操作都有即时、可预期的视觉反馈
5. **Dark-first (暗色优先)** — 面向长时开发会话，暗色减少眼部疲劳
6. **Performant (高性能)** — CSS-only 动画优先，WebGL 按需降级

### 2.2 情绪板 (Mood Board)

```
视觉感受: 精密 · 冷静 · 科技 · 可信赖 · 有生命力
色彩氛围: 深空暗色 + 青色脉冲 + 品红能量 + 金色焦点
空间感受: 呼吸感 · 层叠深度 · 毛玻璃透叠
动效语言: 精准克制 · 功能导向 · 200ms 瞬时反馈
```

---

## 3. 页面布局架构

### 3.1 新增 "Workbench" 工作台模式

取代原有的两个独立入口 (`index.html` 3D Landing + `dashboard.html`)，统一为一个 **Workbench 工作台**:

```
┌──────────────────────────────────────────────────────────┐
│  Top Bar (48px)  Logo | Pipeline 节点条 | 模型选择 | Cmd+K │
├────────┬───────────────────────────────────┬──────────────┤
│        │                                   │              │
│  Side- │  Bento Grid 主画布                  │  Context     │
│  bar   │  ┌─────────┐ ┌──────┐ ┌───────┐  │  Panel       │
│        │  │Pipeline │ │ KPI  │ │Agent  │  │  ┌────────┐  │
│  240px │  │ 可视化   │ │ 卡片 │ │ 状态  │  │  │产物文件│  │
│        │  └─────────┘ └──────┘ └───────┘  │  └────────┘  │
│ 任务   │                                   │  ┌────────┐  │
│ 历史   │  ┌──────────────┐ ┌────────────┐  │  │评审结果│  │
│ 技能   │  │ 任务输入区     │ │ 实时日志流   │  │  └────────┘  │
│ 快捷   │  └──────────────┘ └────────────┘  │  ┌────────┐  │
│ 入口   │                                   │  │测试报告│  │
│        │  ┌──────────────────────────────┐  │  └────────┘  │
│        │  │  对话流 / 消息时间线            │  │              │
│        │  │  (可折叠、可搜索、可导出)         │  │  300px      │
│        │  └──────────────────────────────┘  │              │
├────────┴───────────────────────────────────┴──────────────┤
│  Status Bar (28px)  模型: GPT-4o | Tokens: 1.2k | 连接状态  │
└──────────────────────────────────────────────────────────┘
```

### 3.2 响应式策略

| 断点 | 宽度 | 布局变化 |
|------|------|---------|
| Desktop | >= 1440px | 完整三栏: 侧边栏(240) + 主画布 + 上下文面板(300) |
| Laptop | 1024-1439px | 双栏: 侧边栏(200) + 主画布, 上下文面板变为浮层 |
| Tablet | 768-1023px | 单栏: 侧边栏收起, 上下文面板通过 Tab 切换 |
| Mobile | < 768px | 纯对话模式, 简化 Pipeline 为步骤条 |

---

## 4. 设计令牌 (Design Tokens)

### 4.1 色彩系统

```css
:root {
  /* ========== 中性色阶 (Zinc-based) ========== */
  --background: #09090B;        /* 最深背景 */
  --foreground: #FAFAFA;        /* 主要文字 */

  --card: #18181B;              /* 卡片背景 */
  --card-foreground: #FAFAFA;

  --popover: #18181B;           /* 浮层背景 */
  --popover-foreground: #FAFAFA;

  --primary: #FAFAFA;           /* 主要按钮 */
  --primary-foreground: #09090B;

  --secondary: #27272A;         /* 次要按钮 */
  --secondary-foreground: #FAFAFA;

  --muted: #27272A;             /* 弱化表面 */
  --muted-foreground: #A1A1AA;

  --border: #27272A;            /* 边框 */
  --input: #27272A;             /* 输入框 */
  --ring: #06B6D4;              /* 聚焦环 */

  /* ========== "Cosmic Anomaly" 强调色 ========== */
  --accent-cyan: #06B6D4;       /* 青色 - 结构/连接 */
  --accent-magenta: #D946EF;    /* 品红 - 能量/活跃 */
  --accent-gold: #F6C445;       /* 金色 - 焦点/警告 */

  /* ========== 语义色 ========== */
  --success: #4ADE80;
  --danger: #F87171;
  --warning: #FBBF24;
  --info: #38BDF8;

  /* ========== 圆角 ========== */
  --radius-sm: 6px;
  --radius-md: 8px;
  --radius-lg: 12px;
  --radius-xl: 16px;
  --radius-2xl: 24px;

  /* ========== 阴影 (暗色模式) ========== */
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.4);
  --shadow-md: 0 4px 6px rgba(0,0,0,0.4);
  --shadow-lg: 0 10px 15px rgba(0,0,0,0.5);
}
```

### 4.2 排版系统

```
─────────────────────────────────────────────
  Display   36px / 700  Geist          品牌/大标题
  H1        24px / 600  Geist          页面标题
  H2        18px / 600  Geist          区块标题
  H3        15px / 500  Geist          卡片标题
  Body      14px / 400  Geist          正文
  Caption   12px / 400  Geist          辅助信息
  Mono      13px / 400  JetBrains Mono 代码/数据
  Mono-S    11px / 400  JetBrains Mono 终端日志
─────────────────────────────────────────────
```

### 4.3 间距系统

```
--space-1: 4px    (微调间距)
--space-2: 8px    (组件内间距)
--space-3: 12px   (紧密元素间距)
--space-4: 16px   (标准元素间距)
--space-5: 20px   (区块内间距)
--space-6: 24px   (区块间距)
--space-8: 32px   (大区块间距)
--space-10: 40px  (页级间距)
```

---

## 5. 核心组件设计

### 5.1 Pipeline 节点条 (Top Bar 核心)

```
┌─────────────────────────────────────────────────────────┐
│  [需求分析]──→[代码生成]──→[代码评审]──→[测试生成]──→[交付] │
│    ● idle     ○ pending   ○ pending    ○ pending   ○     │
└─────────────────────────────────────────────────────────┘
```

**状态语义**:
- `idle` — 灰色圆点，等待触发
- `active` — 青色脉冲动画 (借鉴 Linear 的 issue 状态)
- `running` — 品红流光从左到右扫描
- `completed` — 绿色实心 + checkmark
- `failed` — 红色实心 + X，可点击查看错误

### 5.2 Bento Grid 主画布卡片

```
┌──────────────────────┐  ┌──────────┐  ┌──────────────┐
│  Pipeline 执行状态    │  │  Tokens  │  │  Agent 状态   │
│                      │  │          │  │              │
│  [████████░░] 78%    │  │  1,247   │  │ req ● running│
│                      │  │  / 4k    │  │ gen ○ pending │
│  正在: 代码生成       │  │          │  │ rev ○ pending │
│  预计: 12s           │  └──────────┘  │ tes ○ pending │
└──────────────────────┘                └──────────────┘

┌──────────────────────┐  ┌──────────────────────────┐
│  快速操作              │  │  质量指标                  │
│                      │  │                          │
│  [新任务] [清空]      │  │  通过率  覆盖率  评分      │
│  [技能] [配置]        │  │  94%    87%    A-        │
│                      │  │  ██████  █████  ███░     │
└──────────────────────┘  └──────────────────────────┘
```

### 5.3 上下文面板 (Context Panel)

右侧面板不再是固定的信息展示，而是**上下文感知**的动态面板:
- 任务执行中 → 显示实时产物 (文件树 + 代码预览)
- 评审完成 → 显示评审报告卡片 (按严重性着色)
- 测试完成 → 显示测试结果矩阵 (通过/失败/覆盖率)
- 空闲状态 → 显示历史任务时间线

### 5.4 Command Palette (Cmd+K)

借鉴 Linear / Cursor 的核心交互模式:

```
┌─────────────────────────────────────┐
│  > 新任务                           │
│  ─────────────────────────────────  │
│  Actions                            │
│    > New Task          Ctrl+N       │
│    > Switch Model      Ctrl+M       │
│    > Clear History     Ctrl+Shift+D │
│  ─────────────────────────────────  │
│  Recent Tasks                       │
│    > 实现快速排序算法    2 min ago   │
│    > 设计用户认证系统    1 hour ago  │
│    > 优化数据库查询      3 hours ago │
│  ─────────────────────────────────  │
│  Skills                             │
│    > python-dev                      │
│    > react-component                 │
│    > api-design                      │
└─────────────────────────────────────┘
```

### 5.5 消息气泡 (重新设计)

```
┌──────────────────────────────────────────────┐
│ U 用户                         14:32         │
│ ┌────────────────────────────────────────┐   │
│ │ 实现一个线程安全的连接池，支持动态扩容     │   │
│ └────────────────────────────────────────┘   │
│                                              │
│ AI NeuroForge  ▸ 需求分析  ▸ 代码生成  14:33 │
│ ┌────────────────────────────────────────┐   │
│ │  ┌─ 需求规格 ──────────────────────┐   │   │
│ │  │ 目标: 实现线程安全连接池         │   │   │
│ │  │ 约束: Python 3.11+, asyncio    │   │   │
│ │  └────────────────────────────────┘   │   │
│ │  ┌─ 生成文件 ──────────────────────┐   │   │
│ │  │ pool.py        156 lines        │   │   │
│ │  │ pool_test.py   89 lines         │   │   │
│ │  └────────────────────────────────┘   │   │
│ │  ┌─ 评审结果 ──────────────────────┐   │   │
│ │  │ ● 2 critical  ● 1 high         │   │   │
│ │  │ ○ 3 medium    ○ 5 low          │   │   │
│ │  └────────────────────────────────┘   │   │
│ │  [查看代码] [运行测试] [对话修改]        │   │
│ └────────────────────────────────────────┘   │
└──────────────────────────────────────────────┘
```

---

## 6. 交互设计

### 6.1 微交互动效规范

| 交互 | 动画 | 时长 | 缓动 |
|------|------|------|------|
| 按钮 hover | scale(1.02) + 颜色过渡 | 150ms | ease-out |
| 卡片 hover | translateY(-2px) + shadow | 200ms | ease-out |
| 折叠/展开 | max-height + opacity | 250ms | ease-in-out |
| 路由切换 | fade + slide(8px) | 200ms | ease-out |
| 加载骨架屏 | shimmer 扫描 | 1.5s loop | linear |
| 通知弹出 | slide-in from right | 300ms | spring |
| Pipeline 节点激活 | scale(1.05) + 脉冲光环 | 300ms | ease-out |
| 数据更新 | 数字跳动 (count-up) | 400ms | ease-out |

### 6.2 键盘快捷键

```
Ctrl/Cmd + K      打开命令面板
Ctrl/Cmd + N      新建任务
Ctrl/Cmd + Enter  提交当前输入
Ctrl/Cmd + /      切换侧边栏
Ctrl/Cmd + \      切换上下文面板
Ctrl/Cmd + 1-5    切换到对应 Pipeline 节点详情
Escape            关闭面板/浮层
```

### 6.3 空状态与加载状态

```
空状态:
┌──────────────────────────────────┐
│                                  │
│         ○ (图标)                  │
│     还没有任务                    │
│     选择一个示例开始体验           │
│                                  │
│  [排序算法] [认证系统] [连接池]    │
│  [消息队列] [缓存层] [API网关]    │
│                                  │
└──────────────────────────────────┘

加载状态 (Skeleton):
┌──────────────────────┐
│ ████████████░░░░░░░  │  ← shimmer 扫描
│ ████████░░░░░░░░░░░  │
│ ██████████████░░░░░  │
└──────────────────────┘
```

---

## 7. 3D 背景系统 (保留与升级)

### 7.1 当前优势保留

NeuroForge 现有的 Three.js 3D 背景系统是该项目的**核心差异化视觉资产**。Awwwards 获奖网站普遍使用 WebGL 增强视觉冲击力。

**保留**:
- A+ 行星着色器系统 (PBR + SSS + God Rays)
- GPU Ping-Pong 粒子流场
- 鼠标视差交互
- 后处理 (Bloom + Grain + Vignette)

**优化方向**:
- 粒子密度根据设备性能自动调整 (低/中/高)
- `prefers-reduced-motion` 时自动降级为静态星空背景
- 3D 场景与 UI 层的视觉融合更自然 (毛玻璃透叠)

---

## 8. 技术选型与迁移路线图

### 8.1 当前 vs 目标

| 维度 | 当前 (v2.x) | 目标 (v3.0) |
|------|------------|------------|
| **框架** | Vanilla JS + ES Modules | React 18/19 + TypeScript |
| **构建** | 零构建，直接静态服务 | Vite (开发) / Next.js (可选 SSR) |
| **样式** | 单文件 CSS + 内联 style | Tailwind CSS + shadcn/ui |
| **组件** | 手动 DOM 操作函数 | React 组件 + Storybook |
| **状态** | 全局变量 + DOM 查询 | Zustand / Jotai |
| **动画** | CSS keyframes | Framer Motion + CSS transitions |
| **3D** | Three.js (保留) | Three.js + React Three Fiber (可选) |
| **路由** | 无 (两个独立 HTML) | React Router (Workbench 统一入口) |
| **实时通信** | SSE + 轮询回退 | SSE 主 + WebSocket + React Query |
| **主题** | 硬编码暗色 | CSS 变量 + 暗/亮双主题 + 自适应 |

### 8.2 分阶段迁移计划

```
Phase 1: Foundation (Week 1-2)
  ├── 搭建 Vite + React + TypeScript 项目骨架
  ├── 引入 Tailwind CSS + shadcn/ui
  ├── 建立设计令牌系统 (CSS 变量)
  ├── 实现暗/亮主题切换
  └── Three.js 场景 React 化 (React Three Fiber)

Phase 2: Core Components (Week 3-4)
  ├── Top Bar + Pipeline 节点条
  ├── Sidebar (任务历史/技能/快捷入口)
  ├── Bento Grid 主画布
  ├── Context Panel (上下文感知面板)
  ├── Command Palette (Cmd+K)
  └── Status Bar

Phase 3: Workbench Integration (Week 5-6)
  ├── 对话流 / 消息时间线
  ├── 任务输入区 (富文本 + 技能选择)
  ├── 实时日志终端
  ├── SSE/WebSocket 数据层
  └── 产物/评审/测试结果组件

Phase 4: Polish (Week 7-8)
  ├── 微交互动效
  ├── 骨架屏/空状态/错误状态
  ├── 键盘快捷键系统
  ├── 响应式适配 (Tablet/Mobile)
  ├── 性能优化 (虚拟列表、代码分割)
  └── Storybook 组件文档
```

---

## 9. 竞品对比参考

| 特性 | NeuroForge v3 | Cursor IDE | v0.dev | Linear |
|------|:---:|:---:|:---:|:---:|
| Bento Grid 布局 | * | - | * | - |
| Command Palette | * | * | - | * |
| Pipeline 可视化 | * | - | - | - |
| 3D 科幻背景 | * | - | - | - |
| shadcn/ui 组件 | * | - | * | - |
| Agent 多智能体流 | * | * | - | - |
| 键盘优先导航 | * | * | - | * |
| 暗/亮双主题 | * | * | * | * |
| 实时 SSE/WS | * | - | - | * |

---

## 10. 交付物清单

- [x] 本设计方案文档
- [x] 布局架构图 (SVG)
- [x] 设计系统令牌定义
- [ ] Figma 高保真设计稿 (建议下一步)
- [ ] React 组件库代码实现
- [ ] Storybook 组件文档
- [ ] 用户测试原型

---

> **下一步行动**: 建议先基于此方案在 Figma 中制作 3-4 个核心页面的高保真原型，经过一轮设计评审后再进入 Phase 1 代码实现。是否需要我直接在代码层面开始 Phase 1 的 React 项目搭建？
