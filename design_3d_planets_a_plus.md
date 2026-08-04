# NeuroForge · 3D Landing Page 改造设计文档（方案A+：深空外星行星·社区最佳实践版）

> 变更对象：`d:\multi-agent-code-system\web\index.html`
> 交付状态：**设计方案阶段**，等待用户批准后进入实现。
> 当前运行：服务端口 8001 → `/web/` 验证。

---

## 1. 用户需求与确认事项（设计输入）

| 需求项 | 用户确认 |
|---|---|
| 不满四项全改 | 质感 + 层次感 + 动态节奏 + 色彩氛围 |
| 审美方向 | 神秘深空宇宙风（偏黑深紫星云） |
| 元素取舍 | 保留全部现有元素，靠分层让主次分明 |
| 交互驱动 | 强互动驱动（行星/粒子/卫星/脉冲响应鼠标） |

---

## 2. 整体架构总览

### 2.1 四大核心约束（避免重蹈过往 Experience 437769 / 504002 / 1099566 覆辙）

1. **天空盒渲染单线**：使用 BackSide 大球 + 程序化Canvas纹理作为唯一可见天空，**不设 scene.background**，renderer.clearColor 仅初始化为兜底。
2. **粒子永不销毁主对象**：三层流场/环/卫星/拖尾 全用透明度+visible过渡，永远不 `scene.remove()`+置 `null`，避免"触发一次永久消失"类bug。
3. **单一 RAF + 单一交互输入**：只保留一个 animate 循环；交互只通过 `interaction` 对象统一入口写，不注册多份监听器互相打架。
4. **模式开关可回退**：`window.__A_PLUS_MODE = true | false`（true=新方案，false=当前LEGACY），一键刷新切换，确保改造不崩。

### 2.2 五层宇宙景深穿透体系（Z轴严格分层）

| 层级 | 范围 Z/R | 组成元素 | α 区间 | 节奏倍率 | 视觉角色 |
|---|---|---|---|---|---|
| **L1 天穹** | 40~80 (R球) | Skydome程序化星图星云（BackSide大球） + 7000+40颗远近景星 | 0.4~0.7 | 0.05 | 宇宙包裹感 |
| **L2 远景星云余辉** | 16~32 | flowP3(800粒) + ringParticles2(2800粒) + 6颗卫星中外侧4颗卫星（远距卫星） | 0.12~0.28 | 0.12 | 外晕、大气尘埃 |
| **L3 中景主环** | 7~14 | ringParticles主环(8000粒，带3条环缝) + flowP2(2000粒) + 内侧2颗近景卫星 + 卫星拖尾 | 0.35~0.72 | 0.45 | 结构骨架 |
| **L4 主景行星** | 2.0~6.5 | 液态金属球(r=1.9) + 内核发光 + 三层嵌套大气辉光(r=2.25/2.60/3.10) | 1.0 + 加色叠层 | 1.00 | 绝对视觉焦点 |
| **L5 近景互动层** | 0.0~4.5 | flowP1(4000粒，满感curl) + 拖尾近端 + ShockWave叠加 | 0.65~0.92 | 1.80 | 宇宙接触面 |

---

## 3. 视觉核心：行星 + 三层辉光 + Skybox（板块二）

### 3.1 Skybox（L1 · 借鉴 typpo/spacekit NASA TYCHO）

程序化 Canvas 512×512 生成四层合成：

```
Layer 0: 最深墨黑  rgb(4,1,12)  → #04010c
Layer 1: 3团FBM椭圆星云，色 #340a66 / #2a0a4a / #1a0635 混合
Layer 2: 7000颗远景星（0.15/0.3/0.6 三档亮度），90% 高斯分布在星云团周围
Layer 3: 40颗近景亮星（2.5~4px + 1px光晕，色偏紫/蓝/白随机）
```

生成后的 CanvasTexture **一图两用**：
1. 作为 Skydome Mesh.map → 直接可见背景（BackSide Sphere(50, 32, 32)）
2. 喂 PMREMGenerator → 得到 envRT.texture → scene.environment（行星Shader用它的程序化反射，避免samplerCube采样失败问题）

Skydome 渲染约束（Experience 437769成功经验）：
- `renderOrder = -1`（永远先画）
- `skydomeMat.depthWrite = false`、`skydomeMat.fog = false`
- 每帧 `skydome.position.copy(camera.position)`（永远包着相机，不随视差移动）

### 3.2 深空液态金属行星 Shader（L4 · 借鉴 threex.geometricglow + PavelDoGreat）

**深空调色板（uniform C_DEEP / C_MID / C_HIGH / C_SSS / C_PEAK）**
```
C_DEEP = #0a0218  最深紫黑
C_MID  = #2a0a4a  中暗紫
C_HIGH = #b488ff  薄边高光淡紫
C_SSS  = #e9d5ff  内散射近白紫
C_PEAK = #6a2fb8  形变峰位透出
```

**Vertex Shader 四层形变 + 鼠标命中凸起**
```
n1(low 0.55)   = snoise(pos*1.0 + t*0.22) × 0.55  低频大呼吸
n2(mid 0.30)   = snoise(pos*2.3 - t*0.31) × 0.30  中频涡旋
n3(high 0.10)  = snoise(pos*4.8 + t*0.45) × 0.10  高频微涟漪
n4(curl 0.25)  = curlNoise(pos*0.8 + t*0.15).x × 0.25  curl切变

mouseDisp = smoothstep(0.9, 0.0, planetHitDist) × uMouseStrength × 0.55
           （planetHitDist = mouseNDC射线与球面交点 到 本顶点的球面角距）

displacement = (n1+n2+n3+n4) × uNoiseAmp + mouseDisp
uNoiseAmp = 0.42 + 0.53 × (uMouseStrength/3)
```

**Fragment Shader：渐变基底+峰位色+程序化环境反射+双高光+菲涅尔内贴边+次表面散射**
```
coreGrad   = mix(C_DEEP, C_MID, 0.5+0.5*vOriginalPos.y/1.9)
peakTint   = C_PEAK × smoothstep(0.10, 0.32, vDisplacement)
envCol     = mix(C_DEEP, C_MID,  fbm(vReflect*1.5+t*0.1))
           ; mix(envCol, C_HIGH, smoothstep(0.55,0.95, fbm(vReflect*3.7)))
spec1      = C_HIGH × pow(max(dot(N,H1),0), 80)  × 2.5
spec2      = C_MID  × pow(max(dot(N,H2),0), 180) × 1.5
fresEdge   = pow(1-NdotV, 2.6) × 3.2 × C_HIGH
fresInner  = pow(1-NdotV, 6.0) × 1.8 × C_HIGH
sss        = C_SSS × exp(-len(vOriginalPos)*0.9) × (0.5+0.5×sin(t*0.8)) × 0.55

最终色 = coreGrad*0.55 + peakTint*0.9 + envCol*(0.35+fresEdge*0.7)
       + spec1 + spec2 + fresEdge + fresInner*1.6 + sss
```

### 3.3 三层嵌套大气辉光（threex.geometricglow BackSide × 3）

| 层 | 半径 | Fresnel 幂 | α | 颜色 | 备注 |
|---|---|---|---|---|---|
| HaloIn | 2.25 | 3.0 | 0.55 | #7c3aed | 贴边、亮、薄、Bloom可抓 |
| HaloMid | 2.60 | 2.2 | 0.35 | #a78bfa | 中扩散，α±0.06呼吸脉动 |
| HaloOut | 3.10 | 1.6 | 0.18 | #c4b5fd | 最外极淡，uMouseStrength↑ α×1.3 |

- BackSide + AdditiveBlending + depthWrite=false

---

## 4. 粒子系统与互动链（板块三）

### 4.1 流场粒子 Ping-Pong 双缓冲（soulwire/WebGL-GPU-Particles）

三层各一套（L5 P1=64×64=4096, L3 P2=32×64=2048, L2 P3=16×50=800）
- 纹理对：`RT_pos_A/B(RGBA32F: xyz=pos, w=seed)` + `RT_vel_A/B(RGBA32F: xyz=vel, w=life)`
- **共用同一 flowPass GLSL**，只 uniforms 不同（半径区间、基础流速、curlScale、排斥强度）
- **关键修复**：粒子不再每帧回到原位，`newPos = oldPos + curl*dt + mouseRepel*dt + lifeBoundReset`
- 渲染Pass：Points Shader 读刚写完的 pos 纹理 → gl_PointSize 层间不同 → AdditiveBlending

### 4.2 土星环粒子带升级

**ringParticles L3（r=2.6~3.4, N=8000）**
- 径向密度权重：高斯带(mid=3.0, σ=0.13) × 3条环缝暗隙dip(0.55/0.45/0.35)
- 厚度波动：h = 0.15 × snoise(r*5+phase) + 0.05（上下起伏，不总在平面上）
- 颜色：mix(#a78bfa, #c4b5fd, 0.5+0.5×snoise(r*3+phase2))
- 尺寸：内大外小（靠近中心的粒子更大更亮）

**ringParticles2 L2（r=4.2~4.8, N=2800）**
- α∈[0.12,0.28]，在 fogExp2(0.038) 下被自然吞淡 → 远景残环

### 4.3 卫星拖尾升级（vasturiano/three-globe 轨迹渐变风格）

旧：LineBasicMaterial 40点固定长度 → 新：BufferGeometry 60点
```
长度 = base(2.2) × min(3.0, 1.0 + clamp(speed/meanSpeed, 0, 2))
弯度 = 每个点 p_i 加 mouseDir × (1-i/60) × 0.12 × uMouseStrength
alpha  = 0.95 → 0.05 线性渐变（近端亮远端淡）
color  = 卫星主色 → #2a0a4a 深紫黑
```

### 4.4 统一 InteractionState + 响应链（PavelDoGreat splatting）

单对象 + 单钩子写入：
```js
interaction = {
  mouseNDC, mouseWorld, velocity,
  distToPlanetScreen, strength[0,3],
  hoverOnPlanetAcc, triggerPulse, pulsePhase[0,1]
}
// onPointerMoveSync → 写前五项（单监听器入口）
// stepInteraction(td) 每帧写 hover/trigger/pulsePhase（单RAF入口）
```

**四项同源响应链：**
1. **行星表面**：uNoiseAmp = 0.42 + 0.53×(s/3); uMouseStrength = s; uMouseHit = hitPoint
2. **流场flowPass**：uRepelK = base × (1+s); uFlowBaseSpeed = base × (1+s/3)
3. **卫星轨道**：eccentricity = baseEcc ± s×0.09（卫星号奇偶异号）；倾角同理±s×0.05
4. **Planetary Pulse ShockWave**：hover满1.2s → 发出透明扩张圆环 ShockWave（半径 r=2→8，2.2s）+ flowPass 临时 uPulseOrigin/Radius 把 L5/L3 粒子"推开一波"+卫星轨道微扰动（3s内回正）

---

## 5. 后处理链 + 性能（板块四）

### 5.1 后处理链（按顺序）
```
RenderPass
  → UnrealBloomPass(th=0.14, str=1.40, r=0.85, 低频±0.15呼吸)
  → VignetteShaderPass(offset=1.05, darkness=0.55) [新增]
  → ChromaticAberrationShaderPass(aberration=0~0.0028, 仅velocity>VTHRESH启用) [新增，条件启用]
  → ShaderPass(FXAAShader)
  → OutputPass(ACESFilmic sRGB)
```

### 5.2 性能四大硬策略（Spacekit KeplerParticles + soulwire 共识）
1. **共享glowTex**：所有卫星/环/拖尾/ShockWave共用一张 128×128 径向渐变贴图 → 单texture切换
2. **Ping-Pong RT 自适应**：≤1920→256²，>1920→512²，reduceMotion退化为attribute单帧
3. **fogExp2 密度 0.038**：L2 远景自然吞淡，显深邃还省fillrate
4. **固定 renderOrder 表**：Skydome(-1) → 各层(1,3,5,7,10,15,20,30) 保证透明/加色混合顺序正确

---

## 6. 交付清单 + 验证 checklist

### 6.1 文件变更
- 仅修改：`d:\multi-agent-code-system\web\index.html`（所有 Shader + JS 逻辑 + UI CSS 保留）
- 新增：模式切换常量 `const A_PLUS_MODE = true;`（顶部，false=回退LEGACY）

### 6.2 验收验证
- [ ] 访问 `/web/` → 刷新后 LandingPage 首屏即深空宇宙风（深紫黑核心不再亮紫）
- [ ] 鼠标移动：L5 flowP1 粒子出现明显排斥/加速；行星表面出现鼠标向局部凸起；卫星轨道偏心/倾角微扰；HaloOut α 放大
- [ ] 鼠标停在行星上 1.2s → 行星脉冲向外一圈 ShockWave，穿过粒子/环/卫星都有响应
- [ ] 无控制台错误（WebGLProgram编译0错误；RAF循环无uncaught）
- [ ] 快速移动鼠标 → 微色差ChromaticAberration出现但不刺眼；停住则消失
- [ ] 切换 LEGACY 模式（devtools设`A_PLUS_MODE=false; location.reload()`）→ 回到当前效果，新旧互不污染
