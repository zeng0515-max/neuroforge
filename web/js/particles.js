/* ============================================================
   NeuroForge · 粒子系统
   流场粒子、星环粒子、GPU Ping-Pong 双缓冲粒子
   ============================================================ */

import * as THREE from 'three';
import {
  GLSL_NOISE, FLOW_PARTICLE_VS, FLOW_PARTICLE_FS, RING_PARTICLE_VS,
  FS_POS_UPDATE, FS_VEL_COPY, makeSharedGlowTex
} from './shaders.js';
import { scene, renderer, A_PLUS_MODE } from './three-scene.js';

// ============ Flow Particles (LEGACY) ============
export function makeFlowParticles(count, rMin, rMax, colors, sizeRange) {
  const g = new THREE.BufferGeometry();
  const pos = new Float32Array(count * 3);
  const seed = new Float32Array(count);
  const col = new Float32Array(count * 3);
  const sz = new Float32Array(count);
  const speed = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    const r = rMin + Math.random() * (rMax - rMin);
    pos[i*3]   = r * Math.sin(phi) * Math.cos(theta);
    pos[i*3+1] = r * Math.sin(phi) * Math.sin(theta);
    pos[i*3+2] = r * Math.cos(phi);
    seed[i] = Math.random() * 100;
    speed[i] = 0.5 + Math.random() * 1.5;
    const c = colors[Math.floor(Math.random() * colors.length)];
    col[i*3] = c.r; col[i*3+1] = c.g; col[i*3+2] = c.b;
    sz[i] = sizeRange[0] + Math.random() * (sizeRange[1] - sizeRange[0]);
  }
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('aSeed', new THREE.BufferAttribute(seed, 1));
  g.setAttribute('aColor', new THREE.BufferAttribute(col, 3));
  g.setAttribute('aSize', new THREE.BufferAttribute(sz, 1));
  g.setAttribute('aSpeed', new THREE.BufferAttribute(speed, 1));
  return g;
}

export function makeFlowMaterial(flowScale) {
  return new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uPixelRatio: { value: Math.min(window.devicePixelRatio, 2) },
      uMouse: { value: new THREE.Vector2(0, 0) },
      uFlowScale: { value: flowScale }
    },
    vertexShader: FLOW_PARTICLE_VS,
    fragmentShader: FLOW_PARTICLE_FS,
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });
}

// LEGACY flow particle instances
export let flowP1, flowP2, flowP3;
export let matFlow1, matFlow2, matFlow3;
export let geoFlow1, geoFlow2, geoFlow3;

export function initLegacyFlowParticles() {
  if (A_PLUS_MODE) return;
  const pal1 = [new THREE.Color(0xFBBF24), new THREE.Color(0x22D3EE), new THREE.Color(0xD946EF), new THREE.Color(0x67E8F9)];
  const pal2 = [new THREE.Color(0x22D3EE), new THREE.Color(0xE879F9), new THREE.Color(0x67E8F9), new THREE.Color(0xD946EF)];
  const pal3 = [new THREE.Color(0x22D3EE), new THREE.Color(0xE879F9), new THREE.Color(0xFCD34D)];

  geoFlow1 = makeFlowParticles(4000, 2.8, 4.5, pal1, [2.0, 6.0]);
  geoFlow2 = makeFlowParticles(2000, 4.8, 7.5, pal2, [3.0, 9.0]);
  geoFlow3 = makeFlowParticles(800, 8.0, 12.0, pal3, [4.0, 14.0]);
  matFlow1 = makeFlowMaterial(0.4);
  matFlow2 = makeFlowMaterial(0.6);
  matFlow3 = makeFlowMaterial(0.8);
  flowP1 = new THREE.Points(geoFlow1, matFlow1);
  flowP2 = new THREE.Points(geoFlow2, matFlow2);
  flowP3 = new THREE.Points(geoFlow3, matFlow3);
  scene.add(flowP1);
  scene.add(flowP2);
  scene.add(flowP3);
}

// ============ Ring Particles (LEGACY) ============
export function makeRingParticles(count, rInner, rOuter, colors, sizeRange) {
  const g = new THREE.BufferGeometry();
  const pos = new Float32Array(count * 3);
  const col = new Float32Array(count * 3);
  const sz = new Float32Array(count);
  const ang = new Float32Array(count);
  const rad = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const a = Math.random() * Math.PI * 2;
    const r = rInner + Math.pow(Math.random(), 0.5) * (rOuter - rInner);
    ang[i] = a; rad[i] = r;
    pos[i*3] = r * Math.cos(a);
    pos[i*3+1] = (Math.random() - 0.5) * 0.15;
    pos[i*3+2] = r * Math.sin(a);
    const c = colors[Math.floor(Math.random() * colors.length)];
    col[i*3] = c.r; col[i*3+1] = c.g; col[i*3+2] = c.b;
    sz[i] = sizeRange[0] + Math.random() * (sizeRange[1] - sizeRange[0]);
  }
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('aColor', new THREE.BufferAttribute(col, 3));
  g.setAttribute('aSize', new THREE.BufferAttribute(sz, 1));
  g.setAttribute('aAngle', new THREE.BufferAttribute(ang, 1));
  g.setAttribute('aRadius', new THREE.BufferAttribute(rad, 1));
  return g;
}

export let ringParticles, ringParticles2;
export let matRing, matRing2;
export let geoRing, geoRing2;

export function initLegacyRingParticles() {
  if (A_PLUS_MODE) return;
  const ringPal = [new THREE.Color(0x22d3ee), new THREE.Color(0xe879f9), new THREE.Color(0xfbbf24)];

  geoRing = makeRingParticles(8000, 2.6, 3.4, ringPal, [1.5, 4.5]);
  matRing = new THREE.ShaderMaterial({
    uniforms: { uTime: { value: 0 }, uPixelRatio: { value: Math.min(window.devicePixelRatio, 2) } },
    vertexShader: RING_PARTICLE_VS, fragmentShader: FLOW_PARTICLE_FS,
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });
  ringParticles = new THREE.Points(geoRing, matRing);
  ringParticles.rotation.x = Math.PI / 3.5;
  ringParticles.rotation.z = Math.PI / 6;
  scene.add(ringParticles);

  geoRing2 = makeRingParticles(4000, 4.2, 4.8, [new THREE.Color(0x67e8f9), new THREE.Color(0xe879f9)], [1.2, 3.5]);
  matRing2 = new THREE.ShaderMaterial({
    uniforms: { uTime: { value: 0 }, uPixelRatio: { value: Math.min(window.devicePixelRatio, 2) } },
    vertexShader: RING_PARTICLE_VS, fragmentShader: FLOW_PARTICLE_FS,
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
  });
  ringParticles2 = new THREE.Points(geoRing2, matRing2);
  ringParticles2.rotation.x = -Math.PI / 4;
  ringParticles2.rotation.y = Math.PI / 3;
  scene.add(ringParticles2);
}

// ============ A+ Ring Particles ============
export function makeRingParticlesPlus(rIn, rOut, N, isOuter) {
  const positions = new Float32Array(N*3);
  const colors    = new Float32Array(N*3);
  const sizes     = new Float32Array(N);
  const mid   = (rIn+rOut)/2;
  const sigma = (rOut-rIn)/6;
  const dipPos   = [rIn + (rOut-rIn)*0.25, rIn + (rOut-rIn)*0.52, rIn + (rOut-rIn)*0.78];
  const dipDepth = [0.55, 0.45, 0.35];
  const C1 = isOuter ? new THREE.Color('#FBBF24').multiplyScalar(0.70) : new THREE.Color('#22D3EE');
  const C2 = isOuter ? new THREE.Color('#D946EF').multiplyScalar(0.60) : new THREE.Color('#E879F9');
  function pseudoNoise3(x,y,z){ return (Math.sin(x*12.9898+y*78.233+z*37.719)*43758.5453) % 1; }
  let i = 0;
  let guards = 0;
  while (i < N && guards < N*50) {
    guards++;
    const r = rIn + Math.random()*(rOut - rIn);
    const gauss = Math.exp(-Math.pow((r-mid)/sigma, 2));
    let dipsM = 1.0;
    for (let k=0; k<3; k++) dipsM *= 1.0 - dipDepth[k] * Math.exp(-Math.pow((r-dipPos[k])/((rOut-rIn)*0.02), 2));
    const p = gauss * dipsM;
    if (Math.random() > p) continue;
    const theta = Math.random()*Math.PI*2;
    const ph = (r - rIn) / (rOut - rIn);
    const hK = (1 - Math.abs(ph-0.5)*2);
    const thickness = (rOut - rIn) * (isOuter ? 0.04 : 0.06);
    const hZ = (pseudoNoise3(r*5.0, theta*1.3, 0) * 0.15 + 0.05) * thickness * (0.8 + 0.6*hK);
    positions[i*3  ] = Math.cos(theta) * r;
    positions[i*3+1] = hZ;
    positions[i*3+2] = Math.sin(theta) * r;
    const cmix = 0.5 + 0.5*pseudoNoise3(r*3.0, theta*0.7, 1);
    const c  = C1.clone().lerp(C2, cmix);
    colors[i*3  ] = c.r; colors[i*3+1] = c.g; colors[i*3+2] = c.b;
    sizes[i] = (isOuter ? 0.014 : 0.020) * (1.0 + 1.5 * (1 - (r-rIn)/(rOut-rIn)));
    i++;
  }
  const geom = new THREE.BufferGeometry();
  geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geom.setAttribute('color',    new THREE.BufferAttribute(colors, 3));
  geom.setAttribute('aSize',    new THREE.BufferAttribute(sizes, 1));
  const A = window.__A;
  if (!A.assets.sharedGlowTex) A.assets.sharedGlowTex = makeSharedGlowTex();
  const mat = new THREE.PointsMaterial({
    size: 1.0, sizeAttenuation: true, transparent: true,
    map: A.assets.sharedGlowTex, vertexColors: true,
    blending: isOuter ? THREE.AdditiveBlending : THREE.NormalBlending,
    depthWrite: false, opacity: isOuter ? 0.08 : 0.22,
  });
  mat.onBeforeCompile = (shader) => {
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', `attribute float aSize;\n#include <common>`)
      .replace('gl_PointSize = size;', 'gl_PointSize = size * aSize * (300.0 / -mvPosition.z);');
  };
  const pts = new THREE.Points(geom, mat);
  pts.renderOrder = isOuter ? 3 : 5;
  return pts;
}

export function initAPlusRingParticles() {
  if (!A_PLUS_MODE) return;
  const A = window.__A;
  if (!A.assets.sharedGlowTex) A.assets.sharedGlowTex = makeSharedGlowTex();
  A.particles.ring.pts  = makeRingParticlesPlus(2.6, 3.4, 3000, false);
  A.particles.ring2.pts = makeRingParticlesPlus(4.2, 4.8, 1000, true);
  A.particles.ring.pts.rotation.x = Math.PI / 3.5;
  A.particles.ring.pts.rotation.z = Math.PI / 6;
  scene.add(A.particles.ring.pts);
  scene.add(A.particles.ring2.pts);
}

// ============ GPU Ping-Pong Flow Particles (A+) ============
export function makeFlowPP(renderer, W, H, opts) {
  const { rIn, rOut, curlScale=0.8, baseSpeed=0.08, damping=0.9, repelK=0.5, layerAlpha=0.8 } = opts;
  const N = W*H;

  function makeRT() {
    return new THREE.WebGLRenderTarget(W, H, {
      type: THREE.HalfFloatType,
      format: THREE.RGBAFormat,
      magFilter: THREE.LinearFilter, minFilter: THREE.LinearFilter,
      depthBuffer:false, stencilBuffer:false,
    });
  }
  let posA = makeRT(), posB = makeRT();
  let velA = makeRT(), velB = makeRT();

  const posData = new Float32Array(N*4);
  const velData = new Float32Array(N*4);
  for (let i=0;i<N;i++){
    const r = rIn + Math.pow(Math.random(), 0.6)*(rOut-rIn);
    const u = Math.random()*Math.PI*2;
    const v = Math.acos(2*Math.random()-1);
    posData[i*4  ] = r*Math.sin(v)*Math.cos(u);
    posData[i*4+1] = r*Math.sin(v)*Math.sin(u);
    posData[i*4+2] = r*Math.cos(v);
    posData[i*4+3] = Math.random();
    velData[i*4  ] = velData[i*4+1] = velData[i*4+2] = 0.0;
    velData[i*4+3] = 1.0;
  }
  const posTex = new THREE.DataTexture(posData, W,H, THREE.RGBAFormat, THREE.FloatType);
  const velTex = new THREE.DataTexture(velData, W,H, THREE.RGBAFormat, THREE.FloatType);
  posTex.needsUpdate = true; velTex.needsUpdate = true;

  function blit(srcTex, dstRT) {
    const s = new THREE.Scene();
    const c = new THREE.OrthographicCamera(-1,1,1,-1,0,1);
    const q = new THREE.Mesh(new THREE.PlaneGeometry(2,2), new THREE.ShaderMaterial({
      uniforms: { tSrc: { value: srcTex } },
      vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=vec4(position.xy,0.0,1.0); }`,
      fragmentShader: `varying vec2 vUv; uniform sampler2D tSrc; void main(){ gl_FragColor = texture2D(tSrc, vUv); }`,
      depthTest:false, depthWrite:false,
    }));
    s.add(q);
    renderer.setRenderTarget(dstRT);
    renderer.clear();
    renderer.render(s, c);
    renderer.setRenderTarget(null);
  }
  blit(posTex, posA);
  blit(velTex, velA);

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

  const simPosPass = buildSimPass(`precision highp float;\n` + GLSL_NOISE + FS_POS_UPDATE);
  const simVelPass = buildSimPass(`precision highp float;\n` + FS_VEL_COPY);

  const pointsGeom = new THREE.BufferGeometry();
  const idxUV = new Float32Array(N*2);
  for (let i=0;i<N;i++){ idxUV[i*2]=(i%W)/W; idxUV[i*2+1]=(i/W)/H; }
  pointsGeom.setAttribute('aUV', new THREE.BufferAttribute(idxUV, 2));

  const A = window.__A;
  if (!A.assets.sharedGlowTex) A.assets.sharedGlowTex = makeSharedGlowTex();

  const pointsMat = new THREE.ShaderMaterial({
    uniforms: {
      tPos:{ value: posA.texture },
      uTex:{ value: A.assets.sharedGlowTex },
      uPx:{ value: renderer.getPixelRatio() },
      uAlpha:{ value: layerAlpha },
      uSizeBase:{ value: baseSpeed>0.1?0.08:0.055 },
    },
    vertexShader: `
      attribute vec2 aUV;
      uniform sampler2D tPos; uniform float uSizeBase; uniform float uPx;
      void main(){
        vec4 P = texture2D(tPos, aUV);
        vec4 mv = modelViewMatrix * vec4(P.xyz, 1.0);
        gl_Position = projectionMatrix * mv;
        gl_PointSize = uSizeBase * (300.0 / max(0.01,-mv.z)) * uPx;
      }
    `,
    fragmentShader: `
      precision highp float;
      uniform sampler2D uTex; uniform float uAlpha;
      void main(){
        vec4 c = texture2D(uTex, gl_PointCoord);
        if (c.a < 0.02) discard;
        vec3 tint = vec3(0.0, 0.90, 0.95);
        gl_FragColor = vec4(c.rgb*tint, c.a * uAlpha);
      }
    `,
    transparent: true, depthWrite:false, blending: THREE.AdditiveBlending,
  });
  const points = new THREE.Points(pointsGeom, pointsMat);

  return {
    step(dt, uniformsIn) {
      const uP = simPosPass.mat.uniforms;
      uP.tPos.value = posA.texture;
      uP.tVel.value = velA.texture;
      uP.uDT.value = dt;
      uP.uTime.value = uniformsIn.uTime;
      uP.uMouseWorld.value.copy(uniformsIn.uMouseWorld);
      uP.uMouseStrength.value = uniformsIn.uMouseStrength;
      uP.uPulseOrigin.value.copy(uniformsIn.uPulseOrigin);
      uP.uPulseRadius.value = uniformsIn.uPulseRadius;
      renderer.setRenderTarget(posB); renderer.clear(); renderer.render(simPosPass.scene, simPosPass.cam);

      const uV = simVelPass.mat.uniforms;
      uV.tPos.value = posB.texture; uV.tVel.value = velA.texture;
      uV.uDT.value = dt;
      uV.uDamping.value = simPosPass.mat.uniforms.uDamping.value;
      uV.uRIn.value = rIn; uV.uROut.value = rOut;
      renderer.setRenderTarget(velB); renderer.clear(); renderer.render(simVelPass.scene, simVelPass.cam);

      renderer.setRenderTarget(null);
      let tmp = posA; posA = posB; posB = tmp;
      tmp = velA; velA = velB; velB = tmp;
      pointsMat.uniforms.tPos.value = posA.texture;
    },
    points,
  };
}

export function initAPlusFlowPP() {
  if (!A_PLUS_MODE) return;
  const A = window.__A;
  if (!A.assets.sharedGlowTex) A.assets.sharedGlowTex = makeSharedGlowTex();
  A.particles.flowP1.pp = makeFlowPP(renderer, 32, 32, { rIn:1.8, rOut:4.2, curlScale:1.1, baseSpeed:0.12, damping:0.88, repelK:1.8, layerAlpha:0.32 });
  A.particles.flowP2.pp = makeFlowPP(renderer, 16, 32, { rIn:4.0, rOut:9.0, curlScale:0.9, baseSpeed:0.09, damping:0.92, repelK:0.7, layerAlpha:0.18 });
  A.particles.flowP3.pp = makeFlowPP(renderer, 8, 24, { rIn:16.0, rOut:28.0, curlScale:0.7, baseSpeed:0.05, damping:0.95, repelK:0.2, layerAlpha:0.08 });
  A.particles.flowP1.points = A.particles.flowP1.pp.points; A.particles.flowP1.points.renderOrder = 1;
  A.particles.flowP2.points = A.particles.flowP2.pp.points; A.particles.flowP2.points.renderOrder = 4;
  A.particles.flowP3.points = A.particles.flowP3.pp.points; A.particles.flowP3.points.renderOrder = 2;
  scene.add(A.particles.flowP1.points);
  scene.add(A.particles.flowP2.points);
  scene.add(A.particles.flowP3.points);
}

// ============ Full init ============
export function initParticles() {
  initLegacyFlowParticles();
  initLegacyRingParticles();
  initAPlusRingParticles();
  initAPlusFlowPP();
}