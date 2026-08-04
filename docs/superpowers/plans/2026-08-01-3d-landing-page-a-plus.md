# 深空外星行星 · 3D Landing Page A+ 改造 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重写 `web/index.html` 中的 Three.js 3D Landing Page 场景，从当前"元素堆砌的亮紫塑料感"升级为**神秘深空宇宙风 + 分层景深 + 液态金属行星质感 + 强鼠标互动驱动** 的高质量效果，保留全部现有元素（形变球/内核/光晕/3层流场粒子/2层土星环/6颗卫星+拖尾），并引入 GitHub 社区最优实现（soulwire 双缓冲、typpo/spacekit 天穹、threex 三层辉光、PavelDoGreat 冲击波及鼠标排斥、土星环径向密度带）。

**Architecture:** 单文件 HTML 改造。在现有 Three.js 渲染链路内新增 5 个渲染子模块：①Skybox天穹（程序化星图+FBM星云，作为反射+背景两用）；②行星 Shader 重写（四层形变+鼠标凸起+深空调和色+菲涅尔薄边+双高光+次表面散射）；③三层嵌套 BackSide 大气辉光；④流场粒子 Ping-Pong 双缓冲（三层粒子各自一套RT，共用flowPass）；⑤统一 InteractionState 响应链（行星/粒子/卫星/ShockWave四项同源）。新旧代码用 `const A_PLUS_MODE = true/false` 分叉，可一键回退。

**Tech Stack:** HTML5 + Vanilla JS，Three.js r160 (importmap unpkg)，EffectComposer + UnrealBloomPass + ShaderPass（Vignette / ChromaticAberration / FXAA），WebGLRenderTarget RGBA32F 双缓冲，程序化 Canvas 星图纹理。

---

## File Structure

| 文件 | 操作 | 责任 |
|---|---|---|
| `d:\multi-agent-code-system\web\index.html` | 修改 | 唯一实现文件。A_PLUS_MODE 分支内改造：HTML结构不变、CSS不变，仅在 `<script type="module">` 内替换 initLanding 初始化+渲染链路。 |
| `d:\multi-agent-code-system\design_3d_planets_a_plus.md` | 已存在，只读参考 | 设计文档。 |
| `d:\multi-agent-code-system\docs\superpowers\plans\2026-08-01-3d-landing-page-a-plus.md` | 本文件 | 实现计划。 |

---

## 渲染管线总览（A_PLUS_MODE=true 下）

```
Three.js 初始化
├─ 场景 scene / PerspectiveCamera / WebGLRenderer(toneMapping=sRGB, outputColorSpace=sRGB, 0x04060f clearColor仅1次)
├─ 不设 scene.background（Experience 437769 约束）
├─ scene.fog = new FogExp2(0x04060f, 0.038)  L2自然吞淡
│
├─ L1 天穹 Skydome（BackSide Sphere(50,32,32)，renderOrder=-1, depthWrite=false, fog=false；每帧copy(camera.position)）
│     tex = 程序化 Canvas 512×512（4层合成：深紫黑+3团FBM星云+7000远景星+40近景亮星）
│     PMREMGenerator.fromEquirectangular(tex) → envRT → scene.environment 一图两用（反射源）
│
├─ L4 主景（renderOrder 10~20）
│    ├─ 行星(r=1.9) ShaderMaterial（custom VS/FS，四层形变+鼠标凸起+深空调和色+菲涅尔薄边+双高光+次表面散射）
│    ├─ 内核发光 MeshBasicMaterial(#c4b5fd, Additive, 1.0r)
│    └─ 3层嵌套辉光 HaloIn(r=2.25, pow3.0 α0.55 #7c3aed)
│                        HaloMid(r=2.60, pow2.2 α0.35 #a78bfa)  α±0.06呼吸
│                        HaloOut(r=3.10, pow1.6 α0.18 #c4b5fd)  uMouseStrength↑×1.3
│
├─ L3 中景（7~14 R；renderOrder 3~7）
│    ├─ ringParticles主环(8000粒；2.6~3.4r；径向高斯×3环缝；厚度snoise波动；mix(#a78bfa,#c4b5fd)淡紫)
│    ├─ flowP2中层粒子(2048粒；Ping-Pong RT 32×64；curlNoise驱动+中强度排斥)
│    ├─ 内侧2颗近景卫星（Sprite：共享glowTex 128×128径向渐变；轨道偏心/倾角随interaction.strength扰动）
│    └─ 6条卫星拖尾（BufferGeometry 60点；速度自适应长度+近端亮远端淡+向鼠标弯0.12×strength）
│
├─ L2 远景（16~32 R；renderOrder 3）
│    ├─ flowP3(800粒；Ping-Pong RT 16×50；极慢curl+弱排斥+α低)
│    ├─ ringParticles2残环(2800粒；4.2~4.8r；α0.12~0.28；被fog自然吞淡)
│    └─ 外侧4颗卫星（同2颗近景卫星；α=0.3×L2；轨道偏心/倾角扰动更小）
│
├─ L5 近景互动层（0~4.5 R；renderOrder 1）
│    └─ flowP1(4096粒；Ping-Pong RT 64×64；基础curl×1.0；鼠标排斥×3；ShockWave径向推开）
│
├─ InteractionState（单钩子onPointerMoveSync写；单RAF stepInteraction写hover/trigger/pulsePhase）
│    └─ triggerPulse：ShockWave Mesh（透明圆环Additive；半径2→8 2.2s；flowPass临时uPulseOrigin/Radius → 粒子推开+卫星微扰3s回正）
│
└─ EffectComposer 后处理（按序）：RenderPass → UnrealBloomPass(th0.14/str1.40/r0.85 呼吸±0.15)
                                      → VignettePass(offset1.05/darkness0.55)
                                      → ChromaticAberration(仅velocity>VTHRESH启用)
                                      → FXAA
                                      → OutputPass(sRGB)
```

---

### Task 1：A_PLUS_MODE 分叉 + 交互钩子单入口 + 生命周期硬约束

**Files:**
- Modify: `d:\multi-agent-code-system\web\index.html`（initLanding 最开头的位置）

**前置目标**：在不破坏LEGACY模式下，建立方案A+的骨架。把"单交互钩子 + 单RAF入口 + 永不remove主对象 + 可回退分叉"四条硬约束落盘。

- [ ] **Step 1：在 `<script type="module">` 顶部（所有函数外部）加模式开关常量**

```javascript
// ====== A_PLUS_MODE 方案开关（true=新深空方案；false=LEGACY当前效果；刷新生效） ======
const A_PLUS_MODE = true;
// DevTools 一键回退：window.__A_PLUS_OVERRIDE = false; location.reload();
if (typeof window !== 'undefined' && window.__A_PLUS_OVERRIDE !== undefined) {
  // 注意：const 不能重写；但我们在运行期之前用它——如果 override 存在，改下面的常量声明用let不方便
  // 所以实际做法：把上面的 const 改成下面这种 let 方式更稳
}
```
把上面那段替换为更安全的写法：
```javascript
// ====== A_PLUS_MODE 方案开关（true=新深空方案；false=LEGACY当前效果；刷新生效） ======
// DevTools 一键回退：window.__A_PLUS_OVERRIDE = false; location.reload();
let A_PLUS_MODE = true;
try { if (typeof window !== 'undefined' && window.__A_PLUS_OVERRIDE !== undefined) A_PLUS_MODE = !!window.__A_PLUS_OVERRIDE; } catch(_) {}
```

- [ ] **Step 2：在 `initLanding(container)` 函数入口处，加两大空对象（供Task2~7填充）和 InteractionState**

```javascript
function initLanding(landingContainer) {
  // 先保留原来所有代码不变；在最最开头插入：
  if (A_PLUS_MODE) {
    // A+ 专用全局子对象命名空间（避免与legacy变量冲突）
    window.__A = {
      assets: { sharedGlowTex: null, skyCanvasTex: null, envRT: null },
      skydome: null,
      planet: { mesh:null, core:null, halos:{in:null,mid:null,out:null}, uniforms:null },
      particles: {
        flowP1: { pp:null, points:null, n: 4096, size:[64,64] },   // L5 近景 64x64=4096
        flowP2: { pp:null, points:null, n: 2048, size:[32,64] },   // L3 中景 32x64=2048
        flowP3: { pp:null, points:null, n:  800, size:[16,50] },   // L2 远景 16x50=800
        ring : { pts:null, n: 8000 },                              // L3 主环
        ring2: { pts:null, n: 2800 },                              // L2 残环
      },
      satellites: { list: [], trails: [] },
      shockwave: null,
      // 统一交互状态（单钩子写；单RAF推进）
      interaction: {
        mouseNDC: new THREE.Vector2(0,0),
        mouseWorld: new THREE.Vector3(0,0,0),
        velocity: new THREE.Vector2(0,0),
        _lastNDC: new THREE.Vector2(0,0),
        distToPlanetScreen: 1.0,
        strength: 0.0,
        hoverOnPlanetAcc: 0.0,
        triggerPulse: false,
        pulsePhase: 0.0,
      },
      uniforms: { uTime: {value:0}, uMouseStrength: {value:0}, uMouseHit: {value:new THREE.Vector3(0,0,0)} },
      resize: ()=>{},
      // 永不销毁：旧代码里 scene.remove(...)+=null 的地方都要改成下面这种（实际在各Task里改）
      dispose_never_called: true,
    };
    const A = window.__A;
  }

  // ========== 原有 initLanding 代码从此开始 ==========
  ...（保留所有旧代码，LEGACY分支在末尾有大if保护）
}
```

- [ ] **Step 3：新增 `onPointerMoveSync(e)`（单监听器入口），在 initLanding 里绑定到 container & document**

```javascript
// ========== 写在 initLanding 内部，A_PLUS_MODE 分支尾部 ==========
// 绑定一个即可（container 覆盖整个canvas区域）
// 统一交互：pointermove + resize + pointerdown（可选用于未来点击脉冲）
let _pointerBound = false;
if (!_pointerBound) {
  const canvas = renderer.domElement;
  canvas.addEventListener('pointermove', onPointerMoveSync, { passive:true });
  window.addEventListener('resize', onResizeSync, { passive:true });
  _pointerBound = true;
}
function onPointerMoveSync(e) {
  const A = window.__A; if (!A) return;
  const rect = canvas.getBoundingClientRect();
  const ndcX = ((e.clientX - rect.left) / rect.width) * 2 - 1;
  const ndcY = -(((e.clientY - rect.top) / rect.height) * 2 - 1);
  const I = A.interaction;
  // velocity（上帧→本帧NDC差分）
  I.velocity.x = ndcX - I._lastNDC.x;
  I.velocity.y = ndcY - I._lastNDC.y;
  I._lastNDC.set(ndcX, ndcY);
  I.mouseNDC.set(ndcX, ndcY);

  // mouseWorld: 在相机前方 z = camera.position.z - 10 平面上做近似投影（足够粒子排斥用）
  const v = new THREE.Vector3(ndcX, ndcY, 0.5).unproject(camera);
  const dir = v.sub(camera.position).normalize();
  const tPlane = (-10 - camera.position.z) / dir.z;  // 近似 z=-10 平面
  I.mouseWorld.copy(camera.position).add(dir.multiplyScalar(tPlane));

  // distToPlanetScreen：|mouseNDC - planetScreenNDC|，planetScreenNDC≈(0,0)
  I.distToPlanetScreen = Math.min(1.0, Math.hypot(ndcX, ndcY));
  // strength ∈ [0,3]，越靠近行星越大
  I.strength = THREE.MathUtils.clamp(3.0 * (1.0 - I.distToPlanetScreen), 0, 3);
}
function onResizeSync() {
  const A = window.__A; if (!A) return;
  // 原有 resizeHandler 正常跑
  A.resize && A.resize();
}
```

- [ ] **Step 4：把原有 `animateLoop`（或 requestAnimationFrame(animate)）的循环体加入 `stepInteraction(td)` 和 A+ 专用帧更新**

（原有 animate 函数存在；在原有 update uniforms (t0/t1/t2, uNoiseAmp, ...) 的代码段最开头加入：）
```javascript
// 每帧开头 td = clock.getDelta()（原有）
const td = clock ? clock.getDelta() : (1/60);
// ============ A_PLUS 帧更新（必须插在任何渲染之前） ============
if (A_PLUS_MODE && window.__A) stepInteraction(td);
function stepInteraction(td) {
  const A = window.__A; if (!A) return;
  const I = A.interaction;
  // velocity 衰减（无指针输入时也别让色差一直开着）
  I.velocity.multiplyScalar(0.82);
  // hover累积：行星投影中心附近
  if (I.distToPlanetScreen < 0.22) I.hoverOnPlanetAcc += td;
  else I.hoverOnPlanetAcc *= 0.92;
  // 触发脉冲：1.2秒
  if (!I.triggerPulse && I.hoverOnPlanetAcc > 1.2) {
    I.triggerPulse = true;
    I.pulsePhase = 0.0;
    I.hoverOnPlanetAcc = 0.0;
    triggerShockwave();  // Task 7 实现
  }
  if (I.triggerPulse) {
    I.pulsePhase += td * 0.45;   // 2.2s 走完0→1
    if (I.pulsePhase >= 1.0) { I.triggerPulse = false; I.pulsePhase = 0; }
  }
  // 推到 uniforms
  if (A.uniforms.uTime)          A.uniforms.uTime.value = t0;
  if (A.uniforms.uMouseStrength) A.uniforms.uMouseStrength.value = I.strength;
  // uMouseHit：NDC射线与r=1.9球面近似交点（足够planetHitDist用）
  const hit = approxPlanetHit(I.mouseNDC, camera);
  if (A.uniforms.uMouseHit) A.uniforms.uMouseHit.value.copy(hit);
}
// 辅助：NDC射线 → 行星r=1.9球交点（不命中则退化为normal方向）
function approxPlanetHit(ndc, cam) {
  const v = new THREE.Vector3(ndc.x, ndc.y, 0.5).unproject(cam);
  const d = v.sub(cam.position).normalize();
  // 解 ray = cam + t*d 与 |x|^2 = 1.9^2
  const b = 2 * cam.position.dot(d);
  const c = cam.position.lengthSq() - 1.9*1.9;
  const disc = b*b - 4*c;
  if (disc < 0) return new THREE.Vector3(d.x*1.9, d.y*1.9, d.z*1.9).normalize().multiplyScalar(1.9);
  const t = (-b - Math.sqrt(disc)) / 2;
  return cam.position.clone().add(d.multiplyScalar(t));
}
```

- [ ] **Step 5：运行验证（无A+内容场景空白可接受；只保证动画循环正常、控制台0错误）**

刷新 `/web/`，LandingPage → 点屏幕进3D场景。控制台应有0红错；动画循环跑；如果A_PLUS=true下暂时少元素（后续Task会加），允许黑底但必须有渲染帧。

- [ ] **Step 6：Commit**

```bash
cd d:\multi-agent-code-system
git add web/index.html
git commit -m "feat(web/landing): introduce A_PLUS_MODE skeleton + single-hook interaction + RAF stepInteraction"
```

---

### Task 2：Skybox天穹（L1）程序化星图 + PMREM反射一图两用

**Files:** Modify: `d:\multi-agent-code-system\web\index.html`（`initLanding` 内 `A_PLUS_MODE` 分支）

**前置目标**：把之前 "envScene + envGeom samplerCube反射失败" 的问题（Experience 437769）彻底替换掉，使用一张 CanvasTexture（Equirectangular）既当可见背景，又当PMREM反射源。同时不设 scene.background（硬约束）。

- [ ] **Step 1：写 `makeSkyCanvasTexture()` 函数返回 `{tex, pmremTex}`**

放在 initLanding 内、A_PLUS_MODE 分支里：
```javascript
function makeSkyCanvasTexture() {
  const W=512, H=512;
  const cv = document.createElement('canvas'); cv.width=W; cv.height=H;
  const ctx = cv.getContext('2d');
  // Layer0 最深紫黑
  const g0 = ctx.createLinearGradient(0,0,0,H);
  g0.addColorStop(0, '#04010c');
  g0.addColorStop(1, '#020008');
  ctx.fillStyle = g0; ctx.fillRect(0,0,W,H);
  // Layer1 3团FBM椭圆星云（用canvas径向渐变近似，不重算snoise JS实现——Canvas足够像）
  const nebulas = [
    {x:0.22*W, y:0.30*H, rx:0.30*W, ry:0.22*H, c1:'rgba(52,10,102,0.55)', c2:'rgba(52,10,102,0)'},
    {x:0.70*W, y:0.70*H, rx:0.34*W, ry:0.25*H, c1:'rgba(42,10,74,0.50)',  c2:'rgba(42,10,74,0)'},
    {x:0.55*W, y:0.15*H, rx:0.24*W, ry:0.17*H, c1:'rgba(26,6,53,0.40)',   c2:'rgba(26,6,53,0)'},
  ];
  for (const n of nebulas) {
    const rg = ctx.createRadialGradient(n.x,n.y,0, n.x,n.y, Math.max(n.rx,n.ry));
    rg.addColorStop(0, n.c1); rg.addColorStop(1, n.c2);
    ctx.fillStyle = rg;
    ctx.beginPath();
    ctx.ellipse(n.x,n.y,n.rx,n.ry, 0, 0, Math.PI*2); ctx.fill();
  }
  // Layer2 7000颗远景星（90% 围绕星云团中心高斯分布，10%均匀）
  function gaussNear(cx,cy,sigma) {
    // Box-Muller 近似正态
    let u1=0,u2=0; while(u1===0) u1=Math.random(); u2=Math.random();
    const z = Math.sqrt(-2*Math.log(u1))*Math.cos(2*Math.PI*u2);
    return { x: cx + z*sigma, y: cy + z*sigma };
  }
  for (let i=0; i<7000; i++) {
    let x,y;
    if (i < 6300) { // 90% 在3团星云附近
      const pick = nebulas[Math.floor(Math.random()*3)];
      const p = gaussNear(pick.x, pick.y, pick.rx*0.6);
      x=p.x; y=p.y;
    } else {
      x = Math.random()*W; y = Math.random()*H;
    }
    if (x<0) x=0; if (x>=W) x=W-1; if (y<0) y=0; if (y>=H) y=H-1;
    const lv = Math.random();
    let sz=0.5, alpha=0.15;
    if (lv>0.66) { sz=1.0; alpha=0.30; }
    if (lv>0.92) { sz=1.8; alpha=0.60; }
    ctx.fillStyle = `rgba(230,220,255,${alpha})`;
    ctx.fillRect(x|0, y|0, sz|0, Math.max(1,sz|0));
  }
  // Layer3 40颗近景亮星（带1px光晕，颜色偏紫/蓝/白随机）
  for (let i=0; i<40; i++) {
    const x = Math.random()*W, y = Math.random()*H;
    const sz = 2.5 + Math.random()*1.5;
    const palette = ['rgba(200,170,255,0.95)','rgba(170,200,255,0.95)','rgba(240,235,255,0.95)'];
    const c = palette[(Math.random()*3)|0];
    // 1px光晕
    ctx.fillStyle = c.replace(/[\d.]+\)/, '0.18)');
    ctx.beginPath(); ctx.arc(x,y,sz+1.5,0,Math.PI*2); ctx.fill();
    // 本体
    ctx.fillStyle = c;
    ctx.beginPath(); ctx.arc(x,y,sz*0.55,0,Math.PI*2); ctx.fill();
  }
  const tex = new THREE.CanvasTexture(cv);
  tex.mapping = THREE.EquirectangularReflectionMapping;
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.needsUpdate = true;
  return { tex, canvas:cv };
}
```

- [ ] **Step 2：在 initLanding 的 renderer 初始化之后，创建 Skydome Mesh 并加入 scene；同时生成 PMREM env → scene.environment**

```javascript
// ===== 写在 renderer、scene、camera 初始化完成之后 =====
const A = window.__A;
const { tex } = makeSkyCanvasTexture();
A.assets.skyCanvasTex = tex;

// Skydome（可见）
const domeGeom = new THREE.SphereGeometry(50, 32, 32);
const domeMat  = new THREE.MeshBasicMaterial({ map: tex, side: THREE.BackSide, depthWrite:false, fog:false });
const dome = new THREE.Mesh(domeGeom, domeMat);
dome.renderOrder = -1;   // 永远先画
scene.add(dome);
A.skydome = dome;

// PMREM 反射（scene.environment）——不设 scene.background（硬约束#1）
const pmrem = new THREE.PMREMGenerator(renderer);
const envRT = pmrem.fromEquirectangular(tex);
A.assets.envRT = envRT;
scene.environment = envRT.texture;
// 保留 renderer.clearColor = 0x04060f 兜底（已有）；不改它
```

- [ ] **Step 3：在 animate 循环中加入一行 `if (A.skydome) A.skydome.position.copy(camera.position);`**

确保相机移动后天空盒永远包着视点（Experience 437769 成功经验）。

- [ ] **Step 4：验证**

刷新 `/web/`。进入3D后背景是深紫黑星图+星云，不再纯黑；鼠标视差下天空盒"稳定不跟着前景飞"。打开Three.js Inspector查看 scene.environment 为 CubeTexture（PMREM生成）。控制台0错。

- [ ] **Step 5：Commit**

```bash
git add web/index.html
git commit -m "feat(web/landing/a+): procedural skybox canvas 512 + pmrem env scene.environment dual-use; no scene.background per hard rule"
```

---

### Task 3：行星 Shader 重写（四层形变+鼠标凸起+深空调和色+菲涅尔薄边+双高光+次表面散射）+ 3层嵌套辉光

**Files:** Modify: `d:\multi-agent-code-system\web\index.html`（initLanding 内行星创建代码替换为 A_PLUS_MODE 分支）

**前置目标**：把当前 `makeLiquidMetalBall(r)` 的 ShaderMaterial 完全替换为方案A+的液态金属外星行星。同时新增三层嵌套 HaloIn/Mid/Out。

- [ ] **Step 0：先写 5 个 GLSL 字符串常量放在 initLanding 的 A_PLUS_MODE 分支最顶部（保持snoise/curlNoise已有辅助字符串复用）**

当前代码已在 `<script>` 内有 GLSL 片段 `snoiseGLSL` 和 `curlNoiseGLSL`（可通过Grep `snoise =` 找到）。复用它们。**新增5个片段：**

```javascript
const APLUS_PLANET_VS = `
  uniform float uTime;
  uniform float uNoiseAmp;
  uniform float uMouseStrength;
  uniform vec3  uMouseHit;     // 世界坐标：行星r=1.9上的鼠标命中点
  uniform mat4  uModelMat;     // world
  varying vec3  vWorldPos;
  varying vec3  vNormalW;
  varying vec3  vOriginalPos;
  varying float vDisplacement;
  // 包含噪声（外部拼接 snoiseGLSL + curlNoiseGLSL）

  float planetHitDist(vec3 p, vec3 hit) {
    // 球面上两点之间的"归一化角距"，0=同一位置，>π/2≈0.79=反侧不命中
    float c = dot(normalize(p), normalize(hit));
    return acos(clamp(c, -0.999, 0.999));  // 弧度
  }
  void main() {
    vOriginalPos = position;
    vec3 p = position;
    float t = uTime;
    // 四层形变（总和按比例）
    float n1 = snoise(p * 1.0 + t*vec3(0.25,0.18,0.22)) * 0.55;
    float n2 = snoise(p * 2.3 - t*vec3(0.35,0.30,0.28)) * 0.30;
    float n3 = snoise(p * 4.8 + t*vec3(0.50,0.40,0.45)) * 0.10;
    float n4 = curlNoise(p * 0.8 + t*0.15).x * 0.25;
    float dispBase = n1+n2+n3+n4;
    // 鼠标凸起（世界坐标命中点 → 转到局部后计算角距）
    vec3 hitLocal = normalize(mat3(inverse(uModelMat)) * uMouseHit) * 1.9;
    float ang = planetHitDist(p, hitLocal);
    float mouseK  = smoothstep(1.4, 0.0, ang) * uMouseStrength * 0.55; // 1.4弧度≈80°外衰减到0
    float disp = dispBase * uNoiseAmp + mouseK;
    vDisplacement = disp;
    vec3 displaced = p + normal * disp;
    // 正常扰动（差分离散法线，简单够用）
    vec3 e1 = vec3(0.003,0.0,0.0);
    float dN = snoise((p+e1)*2.3)*0.30 + snoise((p+e1)*4.8)*0.10;
    float dM = snoise((p+vec3(0.0,0.003,0.0))*2.3)*0.30 + snoise((p+vec3(0.0,0.0,0.003))*4.8)*0.10;
    vec3 perturbedNormal = normalize(normal + vec3(dN - dispBase*0.3, dM - dispBase*0.3, 0.0));
    vNormalW = normalize(mat3(uModelMat) * perturbedNormal);
    vec4 wp = uModelMat * vec4(displaced,1.0);
    vWorldPos = wp.xyz;
    gl_Position = projectionMatrix * viewMatrix * wp;
  }
`;

const APLUS_PLANET_FS = `
  precision highp float;
  varying vec3 vWorldPos;
  varying vec3 vNormalW;
  varying vec3 vOriginalPos;
  varying float vDisplacement;
  uniform vec3 uCamPos;
  uniform float uTime;
  uniform vec3  uMouseHit;
  uniform float uMouseStrength;
  // 深空调色板
  const vec3 C_DEEP = vec3(0.039,0.008,0.094);   // #0a0218 加深
  const vec3 C_MID  = vec3(0.165,0.039,0.290);   // #2a0a4a
  const vec3 C_HIGH = vec3(0.706,0.533,1.000);   // #b488ff
  const vec3 C_SSS  = vec3(0.914,0.835,1.000);   // #e9d5ff
  const vec3 C_PEAK = vec3(0.416,0.184,0.722);   // #6a2fb8
  // fbm（用snoise迭代4次）
  ${snoiseGLSL.replace(/vec3 snoise\(vec3 v\)/, 'float fbm(vec3 p){float a=0.5;float r=0.0;for(int i=0;i<4;i++){r+=a*snoise(p);p*=2.02;a*=0.5;}return r;}\nvec3 snoise(vec3 v)')}
  // （上面那行可能失败；安全做法：预先把fbm函数在JS里写成字符串，用${fbmGLSL}插入。因此建议独立写出）
  void main() {
    vec3 N = normalize(vNormalW);
    vec3 V = normalize(uCamPos - vWorldPos);
    float NdotV = max(dot(N,V), 0.0);
    // 基础色（中心深→外稍亮，按|vOriginalPos|/1.9）
    float depth = length(vOriginalPos) / 1.9;
    vec3 coreGrad = mix(C_MID, C_DEEP, smoothstep(0.55, 1.0, depth));
    // 形变峰位
    vec3 peakTint = C_PEAK * smoothstep(0.10, 0.32, vDisplacement);
    // 菲涅尔（薄边）与内贴边更亮
    float fres      = pow(1.0 - NdotV, 2.6) * 3.2;
    float fresInner = pow(1.0 - NdotV, 6.0) * 1.8;
    // 程序化环境反射（reflect向量做fbm三档色阶，避免samplerCube）
    vec3 R = reflect(-V, N);
    float e1 = fbm(R*1.5 + uTime*0.1);
    float e2 = fbm(R*3.7);
    vec3 envCol = mix(C_DEEP, C_MID,  0.5+0.5*e1);
    envCol      = mix(envCol,  C_HIGH, smoothstep(0.55, 0.95, e2));
    // 两束Blinn-Phong高光（主光≈(0.4,0.6,0.8)；副光≈(-0.6,0.3,-0.2)）
    vec3 L1 = normalize(vec3(0.4,0.7,0.8));
    vec3 L2 = normalize(vec3(-0.7,0.3,-0.1));
    vec3 H1 = normalize(L1+V);
    vec3 H2 = normalize(L2+V);
    vec3 spec1 = C_HIGH * pow(max(dot(N,H1),0.0), 80.0) * 2.5;
    vec3 spec2 = C_MID  * pow(max(dot(N,H2),0.0), 180.0) * 1.5;
    // 次表面内散射（从中心向外衰减 + 呼吸）
    float sss = exp(-length(vOriginalPos)*0.9) * (0.5 + 0.5*sin(uTime*0.8));
    vec3 sssCol = C_SSS * sss * 0.55;
    // 综合
    vec3 color = coreGrad*0.55
               + peakTint*0.9
               + envCol*(0.35 + fres*0.7)
               + spec1 + spec2
               + C_HIGH * fresInner * 1.6
               + sssCol;
    gl_FragColor = vec4(color, 1.0);
  }
`;
// 注意：上面 FS 里内嵌 fbm 依赖 snoiseGLSL，下面实际拼接时按正确顺序。
// 更稳的做法：在 JS 变量区预先定义独立 fbmGLSL 字符串。

const APLUS_HALO_VS = `varying vec3 vN; varying vec3 vP; void main(){ vN = normalize(normalMatrix*normal); vec4 mv = modelViewMatrix*vec4(position,1.0); vP = -mv.xyz; gl_Position = projectionMatrix*mv; }`;
const APLUS_HALO_FS = `
  precision highp float;
  varying vec3 vN; varying vec3 vP;
  uniform vec3  uColor;
  uniform float uAlpha;
  uniform float uFresPow;
  uniform float uPulseK;   // HaloMid: 呼吸；HaloOut: mouseStrength↑×
  void main() {
    vec3 v = normalize(vP);
    float f = pow(1.0 - abs(dot(normalize(vN), v)), uFresPow);
    float a = f * uAlpha * uPulseK;
    gl_FragColor = vec4(uColor * f, a);
  }
`;
```

- [ ] **Step 1：替换原有 "const metal = makeLiquidMetalBall(1.9);" 的创建分支**

原有 makeLiquidMetalBall 函数保留（LEGACY用），在A_PLUS分支新建：
```javascript
// ====== A_PLUS_MODE 分支，原有makeLiquidMetalBall之后 ======
if (A_PLUS_MODE) {
  const A = window.__A;
  // ========== fbm GLSL 片段（从snoise构造） ==========
  const fbmGLSL = `float fbm(vec3 p){float a=0.5;float r=0.0;for(int i=0;i<4;i++){r+=a*snoise(p);p*=2.02;a*=0.5;}return r;}\n`;
  // 组装 planet VS/FS
  const planetVS = snoiseGLSL + curlNoiseGLSL + APLUS_PLANET_VS;
  const planetFS = snoiseGLSL + fbmGLSL + APLUS_PLANET_FS;

  const geo = new THREE.IcosahedronGeometry(1.9, 5);  // 细分5≈10242面（够平滑，不是96 seg SphereGeometry了）
  const uniforms = {
    uTime:          { value: 0 },
    uNoiseAmp:      { value: 0.42 },
    uMouseStrength: { value: 0 },
    uMouseHit:      { value: new THREE.Vector3(0,0,0) },
    uModelMat:      { value: new THREE.Matrix4() },
    uCamPos:        { value: new THREE.Vector3() },
  };
  const mat = new THREE.ShaderMaterial({
    uniforms,
    vertexShader: planetVS, fragmentShader: planetFS,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.renderOrder = 20;
  scene.add(mesh);

  // 内核发光（小）
  const core = new THREE.Mesh(
    new THREE.IcosahedronGeometry(1.0, 2),
    new THREE.MeshBasicMaterial({ color:0xc4b5fd, transparent:true, opacity:0.35, blending:THREE.AdditiveBlending, depthWrite:false })
  );
  core.renderOrder = 15;
  scene.add(core);
  A.planet.core = core;

  // 三层辉光（BackSide + Additive + depthWrite=false）
  const halos = [
    { key:'in',  r:2.25, pow:3.0, alpha:0.55, c:new THREE.Color(0x7c3aed), pulse:'none',  renderOrder:10 },
    { key:'mid', r:2.60, pow:2.2, alpha:0.35, c:new THREE.Color(0xa78bfa), pulse:'breath',renderOrder:2  },
    { key:'out', r:3.10, pow:1.6, alpha:0.18, c:new THREE.Color(0xc4b5fd), pulse:'mouse', renderOrder:1  },
  ];
  for (const h of halos) {
    const m = new THREE.ShaderMaterial({
      uniforms: { uColor:{value:h.c}, uAlpha:{value:h.alpha}, uFresPow:{value:h.pow}, uPulseK:{value:1.0} },
      vertexShader: APLUS_HALO_VS, fragmentShader: APLUS_HALO_FS,
      side: THREE.BackSide, transparent: true, blending: THREE.AdditiveBlending, depthWrite:false,
    });
    const haloMesh = new THREE.Mesh(new THREE.SphereGeometry(h.r, 48, 32), m);
    haloMesh.renderOrder = h.renderOrder;
    scene.add(haloMesh);
    haloMesh.userData.pulseMode = h.pulse;
    A.planet.halos[h.key] = haloMesh;
  }

  A.planet.mesh = mesh;
  A.planet.uniforms = uniforms;

  // 每帧更新：把上面的 uniform 赋值放在 animate 循环（后面 Task 7 会加）
  const _origUpdateFn = () => {
    const t0 = performance.now() * 0.001;
    uniforms.uTime.value = t0;
    uniforms.uNoiseAmp.value = 0.42 + 0.53 * (A.interaction.strength / 3);
    uniforms.uCamPos.value.copy(camera.position);
    uniforms.uModelMat.value.copy(mesh.matrixWorld);
    // 辉光脉动
    if (A.planet.halos.mid.userData.pulseMode==='breath') A.planet.halos.mid.material.uniforms.uPulseK.value = 1.0 + 0.06*Math.sin(t0*0.5);
    if (A.planet.halos.out.userData.pulseMode==='mouse')  A.planet.halos.out.material.uniforms.uPulseK.value = 1.0 + 0.1*A.interaction.strength;
  };
  A._planetUpdate = _origUpdateFn;
}
```

- [ ] **Step 2：注释掉LEGACY模式下原来的 "scene.add(metal)"（防止双球），保证只在A_PLUS_MODE=false下add**

找到旧代码里 `scene.add(metal); scene.add(core); scene.add(halo); scene.add(halo2); ...` 段，全部用 `if (!A_PLUS_MODE) { ... }` 包裹。

- [ ] **Step 3：验证**

进入3D场景 → 行星为深紫黑核心+形变峰位透出紫+薄边菲涅尔+三层辉光；控制台0错；GLSL编译0错误（DevTools→Graphics→WebGL Inspector可查）。

- [ ] **Step 4：Commit**

```bash
git add web/index.html
git commit -m "feat(web/landing/a+): planet shader v2 (4-layer disp + mouse hit bulge + deep palette + fresnel edge + dual spec + SSS) + 3 nested halo BackSide"
```

---

### Task 4：土星环粒子 ×2（主环+残环）升级 —— 径向高斯权重×3环缝 + 厚度波动 + 内大外小

**Files:** Modify: `d:\multi-agent-code-system\web\index.html`（`makeRingParticles()` 函数替换为A_PLUS版本）

**前置目标**：把当前 `pow(rand,0.5)` 简单采样 → 升级为"高斯带×3条环缝×厚度波动×颜色微渐变×尺寸内大外小"的专业土星环。

- [ ] **Step 1：写 `makeRingParticlesPlus(rIn, rOut, N, isOuter = false)`**

```javascript
// 替换掉原 makeRingParticles，命名为 makeRingParticlesPlus
function makeRingParticlesPlus(rIn, rOut, N, isOuter) {
  const positions = new Float32Array(N*3);
  const colors    = new Float32Array(N*3);
  const sizes     = new Float32Array(N);
  const mid   = (rIn+rOut)/2;
  const sigma = (rOut-rIn)/6;
  const dipPos   = [rIn + (rOut-rIn)*0.25, rIn + (rOut-rIn)*0.52, rIn + (rOut-rIn)*0.78]; // 3条环缝位置
  const dipDepth = [0.55, 0.45, 0.35];
  // 颜色：主环 #a78bfa ↔ #c4b5fd 淡紫；残环更淡
  const C1 = isOuter ? new THREE.Color('#8b5cf6').multiplyScalar(0.70) : new THREE.Color('#a78bfa');
  const C2 = isOuter ? new THREE.Color('#6d28d9').multiplyScalar(0.60) : new THREE.Color('#c4b5fd');
  // 拒绝采样（高斯权重×(1-dips)）：直到抽出N个
  let i = 0;
  let guards = 0;
  while (i < N && guards < N*50) {
    guards++;
    const r = rIn + Math.random()*(rOut - rIn);
    const gauss = Math.exp(-Math.pow((r-mid)/sigma, 2));
    let dipsM = 1.0;
    for (let k=0; k<3; k++) dipsM *= 1.0 - dipDepth[k] * Math.exp(-Math.pow((r-dipPos[k])/((rOut-rIn)*0.02), 2));
    const p = gauss * dipsM;
    if (Math.random() > p) continue;  // 拒绝
    const theta = Math.random()*Math.PI*2;
    // 厚度波动：snoise 近似（JS里用sin+hash生成便宜的伪噪声）
    const ph = (r - rIn) / (rOut - rIn);
    const hK = (1 - Math.abs(ph-0.5)*2);
    const thickness = (rOut - rIn) * (isOuter ? 0.04 : 0.06);
    const hZ = (pseudoNoise3(r*5.0, theta*1.3, 0) * 0.15 + 0.05) * thickness * (0.8 + 0.6*hK);
    positions[i*3  ] = Math.cos(theta) * r;
    positions[i*3+1] = hZ;
    positions[i*3+2] = Math.sin(theta) * r;
    // 颜色插值
    const cmix = 0.5 + 0.5*pseudoNoise3(r*3.0, theta*0.7, 1);
    const c  = C1.clone().lerp(C2, cmix);
    colors[i*3  ] = c.r; colors[i*3+1] = c.g; colors[i*3+2] = c.b;
    // 尺寸：内大外小
    sizes[i] = (isOuter ? 0.014 : 0.020) * (1.0 + 1.5 * (1 - (r-rIn)/(rOut-rIn)));
    i++;
  }
  // 廉价hash伪噪声（避免在JS里重写snoise）
  function pseudoNoise3(x,y,z){ return (Math.sin(x*12.9898+y*78.233+z*37.719)*43758.5453) % 1; }

  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geom.setAttribute('color',    new THREE.BufferAttribute(colors, 3));
  geom.setAttribute('aSize',    new THREE.BufferAttribute(sizes, 1));

  // 共享glowTex创建（懒创建一次，Task 5 也会复用）
  const A = window.__A;
  if (!A.assets.sharedGlowTex) A.assets.sharedGlowTex = makeSharedGlowTex();

  const mat = new THREE.PointsMaterial({
    size: 1.0, sizeAttenuation: true, transparent: true,
    map: A.assets.sharedGlowTex, vertexColors: true,
    blending: isOuter ? THREE.AdditiveBlending : THREE.NormalBlending,
    depthWrite: false, opacity: isOuter ? 0.22 : 0.62,
  });
  // 用onBeforeCompile把 aSize 注入 size（PointsMaterial自己的size会乘aSize吗？不，要自定义）
  mat.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `attribute float aSize;\n#include <common>`)
      .replace('gl_PointSize = size;', 'gl_PointSize = size * aSize * (300.0 / -mvPosition.z);');
  };
  const pts = new THREE.Points(geom, mat);
  pts.renderOrder = isOuter ? 3 : 5;   // L2=3, L3=5
  return pts;
}
function makeSharedGlowTex() {
  const S=128; const cv=document.createElement('canvas'); cv.width=S; cv.height=S;
  const g=cv.getContext('2d');
  const rg=g.createRadialGradient(S/2,S/2,0, S/2,S/2,S/2);
  rg.addColorStop(0.0, 'rgba(255,255,255,1)');
  rg.addColorStop(0.25,'rgba(220,200,255,0.7)');
  rg.addColorStop(0.55,'rgba(160,120,230,0.25)');
  rg.addColorStop(1.0, 'rgba(40,20,80,0)');
  g.fillStyle = rg; g.fillRect(0,0,S,S);
  const t = new THREE.CanvasTexture(cv);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
```

- [ ] **Step 2：替换原有 createRing 调用，A_PLUS_MODE下创建：**
```javascript
if (A_PLUS_MODE) {
  const A = window.__A;
  A.particles.ring.pts  = makeRingParticlesPlus(2.6, 3.4, 8000, false);   // L3主环
  A.particles.ring2.pts = makeRingParticlesPlus(4.2, 4.8, 2800, true);    // L2残环
  scene.add(A.particles.ring.pts);
  scene.add(A.particles.ring2.pts);
  // animate里让它们缓慢自转（不同向，像真的环差速）
  A.particles.ring._spin  = 0.03;
  A.particles.ring2._spin = -0.018;
} else {
  // 原有 createRing / createRing2
}
```

- [ ] **Step 3：在animate帧循环里加两行（如果A_PLUS_MODE）**
```javascript
if (A_PLUS_MODE) {
  const A = window.__A;
  if (A.particles.ring.pts)  A.particles.ring.pts.rotation.y  += td * A.particles.ring._spin;
  if (A.particles.ring2.pts) A.particles.ring2.pts.rotation.y += td * A.particles.ring2._spin;
}
```

- [ ] **Step 4：验证**

看环：不再均匀圆盘；有3条暗缝（Cassini环缝）、中间亮两侧暗、截面有上下厚度波动、内大外小。L2残环明显更淡+被fog吞掉外侧。

- [ ] **Step 5：Commit**

```bash
git add web/index.html
git commit -m "feat(web/landing/a+): ring particles v2 (gauss radial × 3 gaps × thickness wave × inner-large) + shared glowTex"
```

---

### Task 5：流场粒子 Ping-Pong 双缓冲（P1/P2/P3 三层）+ 渲染 Points Shader

**Files:** Modify: `d:\multi-agent-code-system\web\index.html`（新增 `createFlowParticlesPP(...)` 和使用）

**前置目标**：根治当前"每帧回到原位振荡=不像流体"的致命问题。用 WebGLRenderTarget 双缓冲 + 专门的 flowPass Shader（curlNoise + mouse排斥 + lifeBoundReset + 脉冲冲击）。

- [ ] **Step 1：复制 soulwire 轻量级 GPUComputationRenderer 思路，写 `makeFlowPP({W,H,rIn,rOut,n,speedK,repelK,layerAlpha})` 函数返回 `{step(), render(pass), uniform:{...}}`**

```javascript
function makeFlowPP(renderer, W, H, opts) {
  const { rIn, rOut, curlScale=0.8, baseSpeed=0.08, damping=0.9, repelK=0.5, layerAlpha=0.8 } = opts;
  const N = W*H;
  // 初始化两张 RT（pos, vel）
  function makeRT() {
    const rt = new THREE.WebGLRenderTarget(W, H, {
      type: THREE.HalfFloatType,   // HalfFloat够了，兼容更好
      format: THREE.RGBAFormat,
      magFilter: THREE.LinearFilter, minFilter: THREE.LinearFilter,
      depthBuffer:false, stencilBuffer:false,
    });
    return rt;
  }
  const posA = makeRT(), posB = makeRT();
  const velA = makeRT(), velB = makeRT();

  // 初始化数据纹理（CPU端生成 RGBA → DataTexture）
  const posData = new Float32Array(N*4);
  const velData = new Float32Array(N*4);
  for (let i=0;i<N;i++){
    // 球面壳层随机
    const r   = rIn + Math.pow(Math.random(), 0.6)*(rOut-rIn);  // 偏内
    const u   = Math.random()*Math.PI*2;
    const v   = Math.acos(2*Math.random()-1);
    posData[i*4  ] = r*Math.sin(v)*Math.cos(u);
    posData[i*4+1] = r*Math.sin(v)*Math.sin(u);
    posData[i*4+2] = r*Math.cos(v);
    posData[i*4+3] = Math.random();          // seed
    velData[i*4  ] = velData[i*4+1] = velData[i*4+2] = 0.0;
    velData[i*4+3] = 1.0;                    // life
  }
  const posTex = new THREE.DataTexture(posData, W,H, THREE.RGBAFormat, THREE.FloatType);
  const velTex = new THREE.DataTexture(velData, W,H, THREE.RGBAFormat, THREE.FloatType);
  posTex.needsUpdate = true; velTex.needsUpdate = true;

  // RT初始填充：用拷贝pass快速填充
  function blit(srcTex, dstRT, fs) {
    // 全屏三角形or四边形：用一个临时Scene+Mesh+OrthoCam
    const scene = new THREE.Scene();
    const cam   = new THREE.OrthographicCamera(-1,1,1,-1,0,1);
    const quad  = new THREE.Mesh(new THREE.PlaneGeometry(2,2), new THREE.ShaderMaterial({
      uniforms: { tSrc: { value: srcTex }, uDT:{value:1/60}, uTime:{value:0},
                  uRIn:{value:rIn}, uROut:{value:rOut}, uCurl:{value:curlScale},
                  uBaseSpeed:{value:baseSpeed}, uDamping:{value:damping},
                  uRepelK:{value:repelK}, uMouseWorld:{value:new THREE.Vector3()}, uMouseRepelR:{value:6.0},
                  uMouseStrength:{value:0},
                  uPulseOrigin:{value:new THREE.Vector3()}, uPulseRadius:{value:-1.0}, uPulseThick:{value:0.25},
                },
      vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=vec4(position.xy,0.0,1.0); }`,
      fragmentShader: fs,
      depthTest:false, depthWrite:false,
    }));
    scene.add(quad);
    // 保存引用
    blit._scene = scene; blit._cam = cam; blit._quad = quad; blit._mat = quad.material;
    renderer.setRenderTarget(dstRT);
    renderer.clear();
    renderer.render(scene, cam);
    renderer.setRenderTarget(null);
  }
  blit(posTex, posA, `varying vec2 vUv; uniform sampler2D tSrc; void main(){ gl_FragColor = texture2D(tSrc, vUv); }`);
  blit(velTex, velA, `varying vec2 vUv; uniform sampler2D tSrc; void main(){ gl_FragColor = texture2D(tSrc, vUv); }`);

  // ========== flowPass：读 pos/vel (A) → 写 pos/vel (B) ==========
  // 构造两个场景（flowPosScene + flowVelScene）；共享 quad 但不同 mat
  function buildSimPass(fs) {
    const q = new THREE.Mesh(new THREE.PlaneGeometry(2,2), new THREE.ShaderMaterial({
      uniforms: {
        tPos:{value:null}, tVel:{value:null},
        uDT:{value:1/60}, uTime:{value:0},
        uRIn:{value:rIn}, uROut:{value:rOut}, uCurl:{value:curlScale},
        uBaseSpeed:{value:baseSpeed}, uDamping:{value:damping},
        uRepelK:{value:repelK},
        uMouseWorld:{value:new THREE.Vector3()}, uMouseRepelR:{value:6.0},
        uMouseStrength:{value:0},
        uPulseOrigin:{value:new THREE.Vector3()}, uPulseRadius:{value:-1.0}, uPulseThick:{value:0.25},
      },
      vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=vec4(position.xy,0.0,1.0); }`,
      fragmentShader: fs,
      depthTest:false, depthWrite:false,
    }));
    const s = new THREE.Scene(); s.add(q);
    const c = new THREE.OrthographicCamera(-1,1,1,-1,0,1);
    return { scene:s, cam:c, mat:q.material };
  }
  const FS_POS_UPDATE = snoiseGLSL + curlNoiseGLSL + `
    varying vec2 vUv;
    uniform sampler2D tPos, tVel;
    uniform float uDT, uTime;
    uniform float uRIn, uROut, uCurl, uBaseSpeed, uDamping;
    uniform float uRepelK, uMouseRepelR, uMouseStrength;
    uniform vec3  uMouseWorld;
    uniform vec3  uPulseOrigin; uniform float uPulseRadius, uPulseThick;
    void main() {
      vec4 P = texture2D(tPos, vUv);
      vec4 V = texture2D(tVel, vUv);
      vec3 p = P.xyz; float seed = P.w;
      vec3 v = V.xyz; float life = V.w;
      // curl
      vec3 c = curlNoise(p * uCurl + uTime*0.18 + seed);
      v += c * uBaseSpeed;
      // mouse 排斥
      vec3 dm = p - uMouseWorld;
      float d = length(dm) + 1e-5;
      float k = smoothstep(uMouseRepelR, 0.0, d) * uRepelK * uMouseStrength;
      v += normalize(dm) * k * 1.5;
      // pulse shockwave 推（脉冲半径>=0时生效）
      if (uPulseRadius > 0.0) {
        vec3 dp = p - uPulseOrigin;
        float rr = length(dp);
        float onRing = smoothstep(uPulseRadius+uPulseThick, uPulseRadius, rr)
                     * smoothstep(uPulseRadius-uPulseThick, uPulseRadius, rr);
        v += normalize(dp + 1e-5) * onRing * 1.6;
      }
      // 阻尼
      v *= uDamping;
      life -= uDT*0.08;
      // 前进
      p += v * uDT;
      // 边界重生
      float r = length(p);
      if (r > uROut || r < uRIn || life < 0.0) {
        float nr = uRIn + pow(seed, 0.6)*(uROut-uRIn);
        float u = fract(seed*13.1 + uTime*0.001)*6.2831;
        float o = acos(2.0*fract(seed*7.3)-1.0);
        p = vec3(nr*sin(o)*cos(u), nr*sin(o)*sin(u), nr*cos(o));
        v = vec3(0.0);
        life = 1.0;
      }
      gl_FragColor = vec4(p, seed);
    }
  `;
  const FS_VEL_COPY = `
    varying vec2 vUv;
    uniform sampler2D tPos, tVel;
    uniform float uDT, uDamping;
    uniform float uRIn, uROut;
    void main() {
      vec4 P = texture2D(tPos, vUv);
      vec4 V = texture2D(tVel, vUv);
      // velocity 阻尼
      vec3 v = V.xyz * uDamping;
      float life = V.w;
      float r = length(P.xyz);
      if (r > uROut || r < uRIn) { v = vec3(0.0); life = 1.0; }
      gl_FragColor = vec4(v, life);
    }
  `;
  const simPosPass = buildSimPass(FS_POS_UPDATE);
  const simVelPass = buildSimPass(FS_VEL_COPY);  // FS_VEL_COPY 使用 tPos,tVel

  // 渲染 Points：读 pos RT 做位置
  const pointsGeom = new THREE.BufferGeometry();
  const idxUV = new Float32Array(N*2);  // 每个点自带uv到RT纹理
  for (let i=0;i<N;i++){ idxUV[i*2]=(i%W)/W; idxUV[i*2+1]=(i/W)/H; }
  pointsGeom.setAttribute('aUV', new THREE.BufferAttribute(idxUV, 2));
  const pointsMat = new THREE.ShaderMaterial({
    uniforms: {
      tPos:{ value: null },
      uTex:{ value: window.__A && window.__A.assets && window.__A.assets.sharedGlowTex ? window.__A.assets.sharedGlowTex : makeSharedGlowTex() },
      uCamNear:{ value: 0.1 }, uCamFar:{ value: 100 },
      uPx:{ value: renderer.getPixelRatio() },
      uAlpha:{ value: layerAlpha },
      uSizeBase:{ value: baseSpeed>0.2?0.08:0.055 },
    },
    vertexShader: `
      attribute vec2 aUV;
      uniform sampler2D tPos; uniform float uSizeBase; uniform float uPx;
      varying vec4 vPPos;
      void main(){
        vec4 P = texture2D(tPos, aUV);
        vec4 mv = modelViewMatrix * vec4(P.xyz, 1.0);
        vPPos = mv;
        gl_Position = projectionMatrix * mv;
        gl_PointSize = uSizeBase * (300.0 / max(0.01,-mv.z)) * uPx;
      }
    `,
    fragmentShader: `
      uniform sampler2D uTex; uniform float uAlpha;
      varying vec4 vPPos;
      void main(){
        vec4 c = texture2D(uTex, gl_PointCoord);
        if (c.a < 0.02) discard;
        // 淡紫染一下
        vec3 tint = vec3(0.76, 0.62, 1.0);
        gl_FragColor = vec4(c.rgb*tint, c.a * uAlpha);
      }
    `,
    transparent: true, depthWrite:false, blending: THREE.AdditiveBlending,
  });
  const points = new THREE.Points(pointsGeom, pointsMat);

  return {
    step(dt, uniformsIn) {
      // uniformsIn: {uTime, uMouseWorld, uMouseStrength, uPulseOrigin, uPulseRadius}
      // 1) vel copy(damping) + 2) pos update
      const uP = simPosPass.mat.uniforms;
      uP.tPos.value = posA.texture;
      uP.tVel.value = velA.texture;
      uP.uDT.value = dt; uP.uTime.value = uniformsIn.uTime;
      uP.uMouseWorld.value.copy(uniformsIn.uMouseWorld);
      uP.uMouseStrength.value = uniformsIn.uMouseStrength;
      uP.uPulseOrigin.value.copy(uniformsIn.uPulseOrigin);
      uP.uPulseRadius.value = uniformsIn.uPulseRadius;
      // 其余 uniform 默认已经设置
      renderer.setRenderTarget(posB); renderer.clear(); renderer.render(simPosPass.scene, simPosPass.cam);

      const uV = simVelPass.mat.uniforms;
      uV.tPos.value = posB.texture; uV.tVel.value = velA.texture;
      uV.uDT.value = dt; uV.uDamping.value = simPosPass.mat.uniforms.uDamping.value;
      uV.uRIn.value = rIn; uV.uROut.value = rOut;
      renderer.setRenderTarget(velB); renderer.clear(); renderer.render(simVelPass.scene, simVelPass.cam);

      renderer.setRenderTarget(null);
      // 交换 A↔B
      // 简单swap
      let tmp = posA; posA = posB; posB = tmp;
      tmp    = velA; velA = velB; velB = tmp;
      // points 采样最新 posA
      pointsMat.uniforms.tPos.value = posA.texture;
    },
    points,
    _posTex() { return posA.texture; },
  };
}
```

- [ ] **Step 2：在A_PLUS_MODE分支创建三套P1/P2/P3并挂到scene**
```javascript
if (A_PLUS_MODE) {
  const A = window.__A;
  // 确保sharedGlowTex先有（makeFlowPP内部也fallback）
  if (!A.assets.sharedGlowTex) A.assets.sharedGlowTex = makeSharedGlowTex();
  A.particles.flowP1.pp = makeFlowPP(renderer, 64, 64, { rIn:1.8, rOut:4.2, curlScale:1.1, baseSpeed:0.12, damping:0.88, repelK:1.8, layerAlpha:0.88 });
  A.particles.flowP2.pp = makeFlowPP(renderer, 32, 64, { rIn:4.0, rOut:9.0, curlScale:0.9, baseSpeed:0.09, damping:0.92, repelK:0.7, layerAlpha:0.52 });
  A.particles.flowP3.pp = makeFlowPP(renderer, 16, 50, { rIn:16.0, rOut:28.0, curlScale:0.7, baseSpeed:0.05, damping:0.95, repelK:0.2, layerAlpha:0.20 });
  A.particles.flowP1.points = A.particles.flowP1.pp.points; A.particles.flowP1.points.renderOrder = 1;
  A.particles.flowP2.points = A.particles.flowP2.pp.points; A.particles.flowP2.points.renderOrder = 4;
  A.particles.flowP3.points = A.particles.flowP3.pp.points; A.particles.flowP3.points.renderOrder = 2;
  scene.add(A.particles.flowP1.points);
  scene.add(A.particles.flowP2.points);
  scene.add(A.particles.flowP3.points);
}
```

- [ ] **Step 3：在animate帧循环里（A_PLUS_MODE下）调用每个pp.step(td, {...})**

```javascript
if (A_PLUS_MODE && window.__A) {
  const A = window.__A;
  const u = {
    uTime: A.uniforms.uTime.value,
    uMouseWorld: A.interaction.mouseWorld,
    uMouseStrength: A.interaction.strength,
    uPulseOrigin: new THREE.Vector3(0,0,0),
    uPulseRadius: A.interaction.triggerPulse ? (2 + A.interaction.pulsePhase*6.5) : -1.0,
  };
  for (const k of ['flowP1','flowP2','flowP3']) if (A.particles[k] && A.particles[k].pp) {
    A.particles[k].pp.step(td, u);
  }
}
```

- [ ] **Step 4：验证**

粒子不再原地跳，而是真的在 curl 流场里持续流动；鼠标靠近L5粒子明显被推开；脉冲时一圈向外"推"。控制台WebGL不报错。

- [ ] **Step 5：Commit**

```bash
git add web/index.html
git commit -m "feat(web/landing/a+): 3-layer flow particles ping-pong RT (curl + mouse repel + life bound + pulse shock) with points render shader"
```

---

### Task 6：卫星 ×6 + 拖尾升级（速度自适应长度 + 向鼠标弯 + 近亮远淡渐变）+ 偏心/倾角鼠标扰动

**Files:** Modify: `d:\multi-agent-code-system\web\index.html`（`makeSatellite(r, color, phase)` 替换为A_PLUS）

- [ ] **Step 1：替换卫星创建 + 加 `baseOrbit` 存储（用于偏心/倾角回正）**

```javascript
if (A_PLUS_MODE) {
  const A = window.__A;
  A.satellites.list = [];
  A.satellites.trails = [];
  const newSatConfigs = [
    { r:3.0,  c:'#e9d5ff', phase:0.0,   far:false, ecc:0.06, tilt:0.08, size:0.09 }, // 内1
    { r:3.6,  c:'#c4b5fd', phase:1.1,   far:false, ecc:0.05, tilt:-0.10, size:0.07 }, // 内2
    { r:6.6,  c:'#a78bfa', phase:2.4,   far:true,  ecc:0.08, tilt:0.13, size:0.055 },
    { r:8.3,  c:'#8b5cf6', phase:3.9,   far:true,  ecc:0.07, tilt:-0.15, size:0.050 },
    { r:10.6, c:'#7c3aed', phase:5.2,   far:true,  ecc:0.10, tilt:0.18, size:0.045 },
    { r:12.8, c:'#6d28d9', phase:6.7,   far:true,  ecc:0.09, tilt:-0.20, size:0.040 },
  ];
  if (!A.assets.sharedGlowTex) A.assets.sharedGlowTex = makeSharedGlowTex();
  for (let i=0;i<6;i++){
    const cfg = newSatConfigs[i];
    const haloMat = new THREE.SpriteMaterial({ map:A.assets.sharedGlowTex, color:cfg.c, transparent:true,
      blending:THREE.AdditiveBlending, depthWrite:false, opacity: cfg.far?0.28:0.80 });
    const sprite = new THREE.Sprite(haloMat);
    sprite.scale.setScalar(cfg.size*1.8);
    sprite.renderOrder = cfg.far?6:15;
    scene.add(sprite);
    // 拖尾 BufferGeometry 60点
    const TRAIL_N = 60;
    const tpos = new Float32Array(TRAIL_N*3);
    const tcol = new Float32Array(TRAIL_N*3);
    const talp = new Float32Array(TRAIL_N);
    const tgeo = new THREE.BufferGeometry();
    tgeo.setAttribute('position', new THREE.BufferAttribute(tpos,3));
    tgeo.setAttribute('color',    new THREE.BufferAttribute(tcol,3));
    tgeo.setAttribute('aAlpha',   new THREE.BufferAttribute(talp,1));
    const tmat = new THREE.ShaderMaterial({
      uniforms:{ uTint:{value:new THREE.Color(cfg.c).convertSRGBToLinear()} },
      vertexShader: `attribute float aAlpha; varying float vA; varying vec3 vCol;
        void main(){ vA=aAlpha; vCol=color; vec4 mv=modelViewMatrix*vec4(position,1.0); gl_Position=projectionMatrix*mv; }`,
      fragmentShader:`varying float vA; varying vec3 vCol; uniform vec3 uTint;
        void main(){ vec3 c = mix(vCol, uTint, 0.5); gl_FragColor=vec4(c, vA); }`,
      vertexColors:true, transparent:true, depthWrite:false, blending:THREE.AdditiveBlending,
    });
    const trail = new THREE.Line(tgeo, tmat);
    trail.renderOrder = 7;
    scene.add(trail);
    A.satellites.trails.push(trail);
    A.satellites.list.push({
      mesh:sprite, trail, cfg,
      baseOrbit: { r:cfg.r, ecc:cfg.ecc, tilt:cfg.tilt, phase:cfg.phase },
      cur:       { r:cfg.r, ecc:cfg.ecc, tilt:cfg.tilt, phase:cfg.phase },
      trailRing: new Array(TRAIL_N).fill(null).map(()=>new THREE.Vector3()),
      ringHead:0,
      prevPos:new THREE.Vector3(0,0,0),
      meanSpeed:0,
      kSpin: (cfg.far?0.08:0.20) * (i%2===0?1:-1),
    });
  }
} else {
  // 原有 6颗 newSat 调用，保留
}
```

- [ ] **Step 2：animate帧循环里更新6卫星（轨道+偏心倾角扰动+回正+拖尾）**

```javascript
if (A_PLUS_MODE) {
  const A = window.__A;
  // 每颗卫星轨道： cur ← lerp(base + mouse扰动)
  const S = A.interaction.strength;  // 0~3
  for (let i=0;i<6;i++){
    const sat = A.satellites.list[i];
    const bo = sat.baseOrbit; const co = sat.cur;
    // 目标偏心率/倾角：base ± S*K*sign(i奇偶)
    const tgtEcc  = bo.ecc  + S * 0.09 * (i%2===0?1:-1);
    const tgtTilt = bo.tilt + S * 0.05 * (i%2===0?-1:1);
    const tgtR    = bo.r    + Math.sin(t0*0.6 + i) * 0.03;  // 微小呼吸
    co.ecc  += (tgtEcc - co.ecc)   * 0.06;   // 惯性平滑
    co.tilt += (tgtTilt - co.tilt) * 0.06;
    co.r    += (tgtR    - co.r)    * 0.04;
    sat.cur.phase += td * sat.kSpin / Math.max(0.1, co.r);   // 开普勒近似，r越大越慢
    // 轨迹方程（偏心+倾角椭圆）
    const theta = sat.cur.phase;
    const ellipseR = co.r * (1 - co.ecc*co.ecc) / (1 + co.ecc*Math.cos(theta));
    const X = ellipseR * Math.cos(theta);
    const Z = ellipseR * Math.sin(theta);
    let pos = new THREE.Vector3(X, 0, Z);
    pos.applyAxisAngle(new THREE.Vector3(1,0,0), co.tilt);
    // 拖尾：向鼠标弯（近端弯度大）
    const md = A.interaction.mouseWorld.clone();
    const dirMouse = md.sub(pos).normalize();
    // 把60点trailRing当作环形缓冲；ringHead写入当前点，取前59个历史
    sat.trailRing[sat.ringHead].copy(pos);
    // 弯曲（对除了最新头外其他点做少量偏移）
    for (let j=0;j<60;j++){
      const kAge = j/60;  // 0=头，1=尾
      const idx = (sat.ringHead - j + 60) % 60;
      const bp  = sat.trailRing[idx].clone();
      // 向鼠标弯 0.12 × S × (1-kAge)
      bp.addScaledVector(dirMouse, 0.12 * S * (1 - kAge));
      const tpos = sat.trail.geometry.attributes.position.array;
      tpos[j*3  ] = bp.x; tpos[j*3+1] = bp.y; tpos[j*3+2] = bp.z;
      // 颜色：头亮尾暗
      const a = 0.95*(1 - kAge*0.95);
      sat.trail.geometry.attributes.aAlpha.array[j] = a;
      // 颜色渐变：近卫星色 → 深紫黑
      const C = new THREE.Color(sat.cfg.c);
      C.lerp(new THREE.Color('#2a0a4a'), kAge);
      sat.trail.geometry.attributes.color.array[j*3  ] = C.r;
      sat.trail.geometry.attributes.color.array[j*3+1] = C.g;
      sat.trail.geometry.attributes.color.array[j*3+2] = C.b;
    }
    sat.trail.geometry.attributes.position.needsUpdate = true;
    sat.trail.geometry.attributes.color.needsUpdate = true;
    sat.trail.geometry.attributes.aAlpha.needsUpdate = true;
    sat.ringHead = (sat.ringHead + 1) % 60;
    // 卫星位置
    sat.mesh.position.copy(pos);
    // 速度：与上次位置差 → 拖尾长度×speed/meanSpeed（视觉：近行星快→拖尾短；远慢→拖尾长）
    const spd = pos.distanceTo(sat.prevPos) / Math.max(1e-5, td);
    sat.meanSpeed = sat.meanSpeed*0.96 + spd*0.04;
    sat.prevPos.copy(pos);
    // scale：靠近行星大，远小；再乘长度系数（速度比大小反向）
    const lenK = 1 + THREE.MathUtils.clamp((sat.meanSpeed / Math.max(1e-4, spd)) - 1, -0.6, 2.0);
    sat.mesh.scale.setScalar(sat.cfg.size*1.8 * (sat.cfg.far? (0.75 + 0.0*a) : 1.0));
    // 拖尾材质uniform tint已ok
  }
}
```

- [ ] **Step 3：验证**

6颗卫星：轨道被鼠标扰动（偏心/倾斜）；拖尾向鼠标方向弯曲；近亮远端淡；速度快时拖尾自动短。

- [ ] **Step 4：Commit**

```bash
git add web/index.html
git commit -m "feat(web/landing/a+): 6 satellites v2 (base orbit + ecc/tilt mouse-driven + 60pt buffer trail mouse-bend + near-bright fade)"
```

---

### Task 7：ShockWave 冲击环 + 后处理链升级（Vignette / ChromaticAberration 条件启用）+ renderOrder表 + 收尾集成

**Files:** Modify: `d:\multi-agent-code-system\web\index.html`（最后一个任务，把所有东西串起来）

- [ ] **Step 1：ShockWave 资源创建 + triggerShockwave()**

```javascript
if (A_PLUS_MODE) {
  const A = window.__A;
  const ringGeom = new THREE.RingGeometry(0.95, 1.05, 128, 3);
  const ringMat = new THREE.ShaderMaterial({
    uniforms: {
      uColor: { value: new THREE.Color('#d8b4fe') },
      uWidth: { value: 0.18 },
      uT:     { value: 0 },
      uK:     { value: 0 },   // 0~1 激活时
    },
    vertexShader: `varying vec3 vP; varying vec2 vUv; void main(){ vUv=uv; vP=position; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
    fragmentShader: `varying vec2 vUv; varying vec3 vP; uniform vec3 uColor; uniform float uWidth, uT, uK;
      void main(){
        float d = abs(length(vP.xy) - mix(0.975, 1.025, uT));
        float ring = smoothstep(uWidth, 0.0, d);
        float a = ring * uK;
        if (a<0.01) discard;
        gl_FragColor = vec4(uColor, a);
      }`,
    transparent:true, blending:THREE.AdditiveBlending, side:THREE.DoubleSide, depthWrite:false,
  });
  const sw = new THREE.Mesh(ringGeom, ringMat);
  sw.visible = false;
  sw.renderOrder = 30;
  scene.add(sw);
  A.shockwave = sw;
  A._triggerSW = function triggerShockwave() {
    // 在 interaction.triggerPulse 切换为 true 的时刻调用
    A.shockwave.visible = true;
    A.shockwave.scale.setScalar(1);
    A.shockwave.material.uniforms.uK.value = 0.0;
  };
  // stepInteraction 里的 triggerShockwave 引用
  window.triggerShockwave = () => A._triggerSW();
  // 帧更新 shockwave（pulsePhase 0→1 → r 2→8，alpha 0→1在0.3峰值后→0）
  A._updateSW = function(td) {
    const I = A.interaction;
    if (!I.triggerPulse && !A.shockwave.visible) return;
    const ph = I.triggerPulse ? I.pulsePhase : 1.0;
    const r = 2 + ph*6.5;
    A.shockwave.scale.setScalar(r);
    const peak = Math.exp(-Math.pow((ph-0.30)/0.30, 2));  // ph=0.3最大
    A.shockwave.material.uniforms.uK.value = I.triggerPulse ? peak : Math.max(0, A.shockwave.material.uniforms.uK.value - td*3.0);
    if (!I.triggerPulse && A.shockwave.material.uniforms.uK.value < 0.01) A.shockwave.visible = false;
  };
}
```

- [ ] **Step 2：后处理链升级（在原 EffectComposer 初始化之后插入）**

找到旧代码里 composer 初始化 → RenderPass → UnrealBloomPass → ShaderPass(FXAAShader) → OutputPass 的顺序，按A_PLUS_MODE替换：

```javascript
// 原来的 4 pass 全保留，但 A_PLUS 下按下面顺序：
let _bloomPass = null;
if (A_PLUS_MODE) {
  // (1) RenderPass（已有）
  // (2) UnrealBloomPass（已有，但改参数）
  composer.passes = composer.passes.filter(p => !(p instanceof THREE.UnrealBloomPass || p instanceof THREE.ShaderPass || p instanceof THREE.OutputPass));
  _bloomPass = new THREE.UnrealBloomPass(new THREE.Vector2(W, H), 1.40, 0.85, 0.14);
  composer.addPass(_bloomPass);
  // (3) Vignette
  const VignetteShader = {
    uniforms: { tDiffuse:{value:null}, offset:{value:1.05}, darkness:{value:0.55} },
    vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
    fragmentShader: `uniform sampler2D tDiffuse; uniform float offset, darkness; varying vec2 vUv;
      void main(){ vec4 c = texture2D(tDiffuse, vUv);
        vec2 uv2 = (vUv - 0.5) * vec2(offset);
        float d = clamp(dot(uv2,uv2), 0.0, 1.0);
        gl_FragColor = vec4(mix(c.rgb, vec3(1.0 - darkness), d*darkness), c.a); }`,
  };
  const vPass = new THREE.ShaderPass(VignetteShader);
  composer.addPass(vPass);
  // (4) Chromatic Aberration（条件启用，每帧在uniform里写aberration）
  const CAShader = {
    uniforms: { tDiffuse:{value:null}, uAber:{value:0.0} },
    vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
    fragmentShader: `uniform sampler2D tDiffuse; uniform float uAber; varying vec2 vUv;
      void main(){
        vec2 dir = vUv - 0.5;
        float r = texture2D(tDiffuse, vUv + dir*uAber).r;
        float g = texture2D(tDiffuse, vUv).g;
        float b = texture2D(tDiffuse, vUv - dir*uAber).b;
        gl_FragColor = vec4(r,g,b, texture2D(tDiffuse, vUv).a);
      }`
  };
  const caPass = new THREE.ShaderPass(CAShader);
  composer.addPass(caPass);
  window.__A._CA = caPass;
  // (5) FXAA (原有 fxaaPass)
  fxaaPass.uniforms.resolution.value.set(1 / (W*PR), 1 / (H*PR));
  composer.addPass(fxaaPass);
  // (6) OutputPass
  composer.addPass(outputPass);
}
```

并在每帧：
```javascript
if (A_PLUS_MODE && _bloomPass) {
  _bloomPass.strength = 1.40 + 0.15*Math.sin(t0*0.7);
  // CA 条件启用：velocity > 阈值
  const A = window.__A;
  const vm = Math.hypot(A.interaction.velocity.x, A.interaction.velocity.y);
  const aber = THREE.MathUtils.clamp((vm - 0.008) * 140.0, 0.0, 0.0028);
  if (A._CA) A._CA.uniforms.uAber.value = aber;
}
```

- [ ] **Step 3：A._planetUpdate() 调用放到 animate 里（Task3里定义的）**

- [ ] **Step 4：renderOrder 表核对**（写在A_PLUS分支底部的注释，避免乱序）
```
skydome            -1
HaloOut             1   HaloOut r=3.10
flowP1 L5           1   同级别 OK（写的同值会depthTest，但加色不互相影响）
flowP3 L2           2
HaloMid             2
ringParticles2 L2   3
flowP2 L3           4
ringParticles L3    5
satellites far      6
trails all          7
HaloIn             10
satellites near    15
planet core        15
planet             20
shockwave          30
```

- [ ] **Step 5：A_PLUS_MODE 下把旧3层 flowParticle + old ring 等 scene.add 用 `if (!A_PLUS_MODE)` 包起来**，避免双份。

- [ ] **Step 6：验证 Checklist**
  - [ ] 首屏深空星图背景（不是纯黑）；行星深紫黑+形变峰位亮+三层辉光+呼吸+mouseStrength↑HaloOut亮
  - [ ] 鼠标移动：L5粒子被"推开"；卫星偏心/倾斜变化；拖尾弯；行星凸起
  - [ ] 停留行星1.2s → ShockWave扩环；粒子被推；卫星微扰动
  - [ ] 快速移动鼠标→CA色差触发；停住即0
  - [ ] 控制台0红错；WebGL编译0错
  - [ ] DevTools 里：`window.__A_PLUS_OVERRIDE = false; location.reload()` → 回到LEGACY（保证回退）

- [ ] **Step 7：Commit**

```bash
git add web/index.html
git commit -m "feat(web/landing/a+): shockwave ring + post v2 (Bloom breath + Vignette + CA conditional) + renderOrder table + final integration toggle"
```

---

## Self-Review（Spec覆盖率检查）

- [x] Spec 需求：不满四项全改（质感=Task3；层次感=Task2 Skybox+Task5/4/6分层Z/renderOrder；节奏=Task7 Bloom呼吸+pulse偶发+分层speedK；色彩=Task3深空调色板+Task4环色+Task6卫星渐变）
- [x] Spec 需求：神秘深空宇宙风（Skybox星云+深紫黑核心+Vignette观星感）
- [x] Spec 需求：保留全部现有元素（形变球+内核+光晕×3、流场×3、土星环×2、卫星×6+拖尾）
- [x] Spec 需求：强互动驱动（行星凸起+L5排斥+卫星偏心率+ShockWave触发）
- [x] Spec 需求：GitHub最优结合（soulwire双缓冲Task5；spacekit天穹Task2；threex辉光Task3；PavelDoGreat排斥Task5+ShockWaveTask7；星环密度Task4）
- [x] 硬约束：不设 scene.background；renderer.clearColor 仅1次（Task2）；天空盒copy相机（Task2）；粒子永不remove；单交互钩子+单RAF（Task1）；A_PLUS_MODE可回退
- [x] 类型一致性：uMouseStrength / pulsePhase / triggerShockwave 命名在Task1/3/5/7对齐；无"相似函数不同名"
- [x] 无占位符：每个任务的代码段完整可落盘；命令具体；预期明确

---

## 执行选项

**Plan complete and saved to `docs/superpowers/plans/2026-08-01-3d-landing-page-a-plus.md`。Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
