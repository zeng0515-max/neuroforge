/* ============================================================
   NeuroForge · Three.js 场景核心
   Scene, Camera, Renderer, Lighting, Post-Processing
   ============================================================ */

import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { FXAAShader } from 'three/addons/shaders/FXAAShader.js';
import { GRAIN_SHADER, VIGNETTE_SHADER, CA_SHADER } from './shaders.js';

// ============ A+ Mode Flag ============
export let A_PLUS_MODE = true;
try { if (typeof window !== 'undefined' && window.__A_PLUS_OVERRIDE !== undefined) A_PLUS_MODE = !!window.__A_PLUS_OVERRIDE; } catch(_) {}

// ============ Container ============
const container = document.getElementById('three-container');

// ============ Scene ============
export const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x0a0a14, 0.035);
if (A_PLUS_MODE) scene.fog.density = 0.045;

// ============ Camera ============
export const camera = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.1, 200);
camera.position.set(-0.2, 0.9, 7.8);

// ============ Renderer ============
export const renderer = new THREE.WebGLRenderer({
  antialias: true, alpha: true, powerPreference: 'high-performance',
  preserveDrawingBuffer: true, premultipliedAlpha: false
});
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.8));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setClearColor(0x0a0a14, 1, true);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.92;
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.domElement.style.display = 'block';
renderer.domElement.style.width = '100%';
renderer.domElement.style.height = '100%';

// ============ Clock ============
export const clock = new THREE.Clock();

// ============ Mouse ============
export const mouse = { x: 0, y: 0, tx: 0, ty: 0 };

// ============ Init container ============
export function initContainer() {
  if (!container) {
    console.warn('Three.js container not found');
    return false;
  }
  container.innerHTML = '';
  container.appendChild(renderer.domElement);
  return true;
}

// ============ A+ Namespace ============
export function initAPlusNamespace() {
  if (!A_PLUS_MODE) return;
  window.__A = {
    assets: { sharedGlowTex: null, skyCanvasTex: null, envRT: null },
    skydome: null,
    planet: { mesh:null, core:null, halos:{in:null,mid:null,out:null}, uniforms:null },
    particles: {
      flowP1: { pp:null, points:null, n: 4096, size:[64,64] },
      flowP2: { pp:null, points:null, n: 2048, size:[32,64] },
      flowP3: { pp:null, points:null, n:  800, size:[16,50] },
      ring : { pts:null, n: 8000, _spin: 0.03 },
      ring2: { pts:null, n: 2800, _spin: -0.018 },
    },
    satellites: { list: [], trails: [] },
    shockwave: null,
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
    _planetUpdate: null,
    _CA: null,
    resize: ()=>{},
  };
}

// ============ Environment Map (LEGACY) ============
export let envTex = null;

export function initLegacyEnvironment() {
  if (A_PLUS_MODE) return;
  const pmremGenerator = new THREE.PMREMGenerator(renderer);
  const envScene = new THREE.Scene();
  const envGeom = new THREE.SphereGeometry(50, 32, 32);
  const envCanvas = document.createElement('canvas');
  envCanvas.width = 512; envCanvas.height = 512;
  const ectx = envCanvas.getContext('2d');
  const grad = ectx.createLinearGradient(0, 0, 0, 512);
  grad.addColorStop(0, '#040818');
  grad.addColorStop(0.35, '#081430');
  grad.addColorStop(0.6, '#06122a');
  grad.addColorStop(0.85, '#0a1a2e');
  grad.addColorStop(1, '#020510');
  ectx.fillStyle = grad;
  ectx.fillRect(0, 0, 512, 512);
  const hueArr = [0x22d3ee, 0x67e8f9, 0xe879f9, 0xf0abfc, 0xfbbf24];
  for (let i = 0; i < 22; i++) {
    const r = 30 + Math.random() * 80;
    const cx = Math.random() * 512, cy = Math.random() * 512;
    const g = ectx.createRadialGradient(cx, cy, 0, cx, cy, r);
    const h = hueArr[Math.floor(Math.random() * hueArr.length)];
    const rr = (h >> 16) & 255, gg = (h >> 8) & 255, bb = h & 255;
    g.addColorStop(0, `rgba(${rr},${gg},${bb},0.9)`);
    g.addColorStop(1, 'rgba(0,0,0,0)');
    ectx.fillStyle = g;
    ectx.fillRect(0, 0, 512, 512);
  }
  envTex = new THREE.CanvasTexture(envCanvas);
  envTex.mapping = THREE.EquirectangularReflectionMapping;
  const envMesh = new THREE.Mesh(envGeom, new THREE.MeshBasicMaterial({ map: envTex, side: THREE.BackSide }));
  envScene.add(envMesh);
  const envRT = pmremGenerator.fromScene(envScene, 0.04);
  scene.environment = envRT.texture;
  envGeom.dispose();
}

// ============ A+ Skybox ============
export function initAPlusSkybox() {
  if (!A_PLUS_MODE) return;
  const A = window.__A;

  function makeSkyCanvasTexture() {
    const W=512, H=512;
    const cv = document.createElement('canvas'); cv.width=W; cv.height=H;
    const ctx = cv.getContext('2d');
    const g0 = ctx.createLinearGradient(0,0,H,H);
    g0.addColorStop(0, '#0a0a14');
    g0.addColorStop(0.5, '#080812');
    g0.addColorStop(1, '#06060e');
    ctx.fillStyle = g0; ctx.fillRect(0,0,W,H);
    const nebulas = [
      {x:0.18*W, y:0.35*H, rx:0.28*W, ry:0.20*H, c1:'rgba(124,139,255,0.10)', c2:'rgba(124,139,255,0)'},
      {x:0.75*W, y:0.60*H, rx:0.30*W, ry:0.22*H, c1:'rgba(74,85,104,0.08)',  c2:'rgba(74,85,104,0)'},
      {x:0.60*W, y:0.20*H, rx:0.22*W, ry:0.16*H, c1:'rgba(163,230,53,0.05)',  c2:'rgba(163,230,53,0)'},
      {x:0.30*W, y:0.75*H, rx:0.18*W, ry:0.14*H, c1:'rgba(251,113,133,0.05)',   c2:'rgba(251,113,133,0)'},
    ];
    for (const n of nebulas) {
      const rg = ctx.createRadialGradient(n.x,n.y,0, n.x,n.y, Math.max(n.rx,n.ry));
      rg.addColorStop(0, n.c1); rg.addColorStop(1, n.c2);
      ctx.fillStyle = rg;
      ctx.beginPath();
      ctx.ellipse(n.x,n.y,n.rx,n.ry, 0, 0, Math.PI*2); ctx.fill();
    }
    function gaussNear(cx,cy,sigma) {
      let u1=0,u2=0; while(u1===0) u1=Math.random(); u2=Math.random();
      const z = Math.sqrt(-2*Math.log(u1))*Math.cos(2*Math.PI*u2);
      return { x: cx + z*sigma, y: cy + z*sigma };
    }
    for (let i=0; i<7000; i++) {
      let x,y;
      if (i < 6300) {
        const pick = nebulas[Math.floor(Math.random()*4)];
        const p = gaussNear(pick.x, pick.y, pick.rx*0.6);
        x=p.x; y=p.y;
      } else {
        x = Math.random()*W; y = Math.random()*H;
      }
      if (x<0) x=0; if (x>=W) x=W-1; if (y<0) y=0; if (y>=H) y=H-1;
      const lv = Math.random();
      let sz=0.5, alpha=0.06;
      if (lv>0.66) { sz=1.0; alpha=0.12; }
      if (lv>0.92) { sz=1.8; alpha=0.30; }
      let starC;
      const temp = Math.random();
      if (temp < 0.70)      starC = `rgba(200,230,255,${alpha})`;
      else if (temp < 0.85) starC = `rgba(0,229,255,${alpha})`;
      else if (temp < 0.95) starC = `rgba(255,184,0,${alpha})`;
      else                  starC = `rgba(255,62,165,${alpha})`;
      ctx.fillStyle = starC;
      ctx.fillRect(x|0, y|0, sz|0, Math.max(1,sz|0));
    }
    for (let i=0; i<40; i++) {
      const x = Math.random()*W, y = Math.random()*H;
      const sz = 2.5 + Math.random()*1.5;
      const hotPalette = [
        'rgba(124,139,255,0.55)',
        'rgba(163,230,53,0.35)',
        'rgba(251,113,133,0.35)',
        'rgba(200,210,240,0.55)',
      ];
      const c = hotPalette[(Math.random()*hotPalette.length)|0];
      ctx.fillStyle = c.replace(/[\d.]+\)/, '0.04)');
      ctx.beginPath(); ctx.arc(x,y,sz+2,0,Math.PI*2); ctx.fill();
      ctx.fillStyle = c;
      ctx.beginPath(); ctx.arc(x,y,sz*0.55,0,Math.PI*2); ctx.fill();
    }
    const tex = new THREE.CanvasTexture(cv);
    tex.mapping = THREE.EquirectangularReflectionMapping;
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.needsUpdate = true;
    return { tex, canvas:cv };
  }

  const { tex: skyTex } = makeSkyCanvasTexture();
  A.assets.skyCanvasTex = skyTex;

  const domeGeom = new THREE.SphereGeometry(50, 32, 32);
  const domeMat = new THREE.MeshBasicMaterial({ map: skyTex, side: THREE.BackSide, depthWrite:false, fog:false });
  const dome = new THREE.Mesh(domeGeom, domeMat);
  dome.renderOrder = -1;
  scene.add(dome);
  A.skydome = dome;

  const pmrem = new THREE.PMREMGenerator(renderer);
  const envRT = pmrem.fromEquirectangular(skyTex);
  A.assets.envRT = envRT;
  scene.environment = envRT.texture;
}

// ============ Lighting ============
export const l1 = new THREE.PointLight(0xFBBF24, 26, 35, 1.8);
export const l2 = new THREE.PointLight(0x22D3EE, 6, 35, 1.6);
export const l3 = new THREE.PointLight(0xE879F9, 22, 35, 1.6);
export const keyLight = new THREE.DirectionalLight(0x8899bb, 0.12);

export function initLighting() {
  scene.add(new THREE.AmbientLight(0x0a0a1a, 0.30));
  l1.position.set(5, 3, 5); scene.add(l1);
  l2.position.set(-4, -3, 4); scene.add(l2);
  l3.position.set(0, 4, -6); scene.add(l3);
  keyLight.position.set(6, 4, 8); scene.add(keyLight);
}

// ============ Post-Processing ============
export let composer = null;
export let bloom = null;
export let _bloomPass = null;
export let aaPass = null;
export let outputPass = null;

export function initPostProcessing() {
  composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));

  if (!A_PLUS_MODE) {
    bloom = new UnrealBloomPass(new THREE.Vector2(window.innerWidth, window.innerHeight), 1.4, 0.85, 0.1);
    bloom.threshold = 0.30;
    bloom.strength = 0.14;
    bloom.radius = 0.95;
    composer.addPass(bloom);

    aaPass = new ShaderPass(FXAAShader);
    aaPass.uniforms['resolution'].value.set(1 / (window.innerWidth * renderer.getPixelRatio()), 1 / (window.innerHeight * renderer.getPixelRatio()));
    composer.addPass(aaPass);

    outputPass = new OutputPass();
    composer.addPass(outputPass);
  }

  if (A_PLUS_MODE) {
    _bloomPass = new UnrealBloomPass(new THREE.Vector2(window.innerWidth, window.innerHeight), 0.16, 0.95, 0.93);
    composer.addPass(_bloomPass);

    const grainPass = new ShaderPass(GRAIN_SHADER);
    composer.addPass(grainPass);
    window.__A._grain = grainPass;

    const vPass = new ShaderPass(VIGNETTE_SHADER);
    composer.addPass(vPass);

    const caPass = new ShaderPass(CA_SHADER);
    composer.addPass(caPass);
    window.__A._CA = caPass;

    aaPass = new ShaderPass(FXAAShader);
    aaPass.uniforms['resolution'].value.set(1 / (window.innerWidth * renderer.getPixelRatio()), 1 / (window.innerHeight * renderer.getPixelRatio()));
    composer.addPass(aaPass);

    outputPass = new OutputPass();
    composer.addPass(outputPass);
  }
}

// ============ Interaction Helpers ============
export function approxPlanetHit(ndc, cam) {
  const ray = new THREE.Raycaster();
  ray.setFromCamera(ndc, cam);
  const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 10);
  const hit = new THREE.Vector3();
  if (ray.ray.intersectPlane(plane, hit)) return hit;
  return new THREE.Vector3(0, 0, -10);
}

export function stepInteraction(td) {
  const A = window.__A;
  if (!A) return;
  const it = A.interaction;
  it.velocity.multiplyScalar(0.82);
  if (it.distToPlanetScreen < 0.22) {
    it.hoverOnPlanetAcc += td;
  } else {
    it.hoverOnPlanetAcc *= 0.92;
  }
  if (it.hoverOnPlanetAcc > 1.2 && !it.triggerPulse) {
    it.triggerPulse = true;
    it.pulsePhase = 0;
    it.hoverOnPlanetAcc = 0;
    if (typeof triggerShockwave === 'function') triggerShockwave();
  }
  if (it.triggerPulse) {
    it.pulsePhase += td * 0.45;
    if (it.pulsePhase >= 1.0) {
      it.triggerPulse = false;
      it.pulsePhase = 0;
    }
  }
  A.uniforms.uTime.value = clock.elapsedTime;
  A.uniforms.uMouseStrength.value = it.strength;
  A.uniforms.uMouseHit.value.copy(approxPlanetHit(it.mouseNDC, camera));
}

// ============ Pointer Sync ============
export function onPointerMoveSync(e) {
  if (!window.__A) return;
  const A = window.__A;
  const it = A.interaction;
  const rect = renderer.domElement.getBoundingClientRect();
  const ndcX = ((e.clientX - rect.left) / rect.width) * 2 - 1;
  const ndcY = -(((e.clientY - rect.top) / rect.height) * 2 - 1);
  it.velocity.x = ndcX - it._lastNDC.x;
  it.velocity.y = ndcY - it._lastNDC.y;
  it._lastNDC.set(ndcX, ndcY);
  it.mouseNDC.set(ndcX, ndcY);
  it.mouseWorld.copy(approxPlanetHit(it.mouseNDC, camera));
  const dist = Math.hypot(ndcX, ndcY);
  it.distToPlanetScreen = Math.min(dist, 1.0);
  it.strength = Math.max(0, Math.min(3.0 * (1.0 - it.distToPlanetScreen), 3.0));
}

// ============ Resize Handler ============
export function setupResize() {
  window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
    composer.setSize(window.innerWidth, window.innerHeight);
  });
}

// ============ Visibility ============
export let visible = true;
export function setupVisibility() {
  document.addEventListener('visibilitychange', () => { visible = !document.hidden; });
}

// ============ Full init ============
export function initScene() {
  if (!initContainer()) return false;
  initAPlusNamespace();
  initLegacyEnvironment();
  initAPlusSkybox();
  initLighting();
  initPostProcessing();
  setupResize();
  setupVisibility();
  return true;
}