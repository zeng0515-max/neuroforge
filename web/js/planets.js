/* ============================================================
   NeuroForge · 行星系统
   LEGACY 球体、A+ 行星、卫星、冲击环、量子/神经/几何可视化层
   ============================================================ */

import * as THREE from 'three';
import {
  GLSL_NOISE, APLUS_PLANET_VS, APLUS_PLANET_FS, APLUS_HALO_VS, APLUS_HALO_FS,
  SPHERE_VS, SPHERE_FS, LEGACY_HALO_VS, LEGACY_HALO_FS,
  GOD_RAY_VS, GOD_RAY_FS, SHOCKWAVE_VS, SHOCKWAVE_FS,
  QUANTUM_VS, QUANTUM_FS, QUANTUM_SHELL_VS, QUANTUM_SHELL_FS,
  Q_NOISE, makeSharedGlowTex, makeGlowTexture
} from './shaders.js';
import { scene, camera, renderer, clock, mouse, A_PLUS_MODE } from './three-scene.js';

// ============ LEGACY 球体导出（供 animate 循环使用） ============
export let sphereGeom, sphereUniforms, sphereMat, sphere;
export let coreGeom, coreMat, core;
export let haloGeom, haloMat, halo;

// ============ LEGACY 轨道天体导出 ============
export let orbitBodies = [];

// ============ 初始化 LEGACY 球体 ============
export function initLegacySphere() {
  if (A_PLUS_MODE) return;
  sphereGeom = new THREE.IcosahedronGeometry(1.9, 12);

  sphereUniforms = {
    uTime:       { value: 0 },
    uNoiseAmp:   { value: 0.42 },
    uNoiseFreq:  { value: 1.0 },
    uColorA:     { value: new THREE.Color(0x00e5ff) },
    uColorB:     { value: new THREE.Color(0xD946EF) },
    uColorC:     { value: new THREE.Color(0xffb800) },
    uColorD:     { value: new THREE.Color(0xE879F9) },
    uFresnel:    { value: 3.2 },
    uFresnelPow: { value: 2.6 },
    uMouse:      { value: new THREE.Vector2(0, 0) },
  };

  sphereMat = new THREE.ShaderMaterial({
    uniforms: sphereUniforms,
    vertexShader: SPHERE_VS,
    fragmentShader: SPHERE_FS,
  });

  sphere = new THREE.Mesh(sphereGeom, sphereMat);
  scene.add(sphere);

  // 内核发光
  coreGeom = new THREE.SphereGeometry(0.4, 64, 48);
  coreMat = new THREE.MeshBasicMaterial({ color: 0xFBBF24, transparent: true, opacity: 0.55, blending: THREE.AdditiveBlending });
  core = new THREE.Mesh(coreGeom, coreMat);
  scene.add(core);

  // 内核外层光晕
  haloGeom = new THREE.SphereGeometry(0.65, 32, 32);
  haloMat = new THREE.ShaderMaterial({
    transparent: true, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.BackSide,
    uniforms: { uTime: { value: 0 } },
    vertexShader: LEGACY_HALO_VS,
    fragmentShader: LEGACY_HALO_FS,
  });
  halo = new THREE.Mesh(haloGeom, haloMat);
  scene.add(halo);
}

// ============ 初始化 A+ 行星 ============
export function initAPlusPlanet() {
  if (!A_PLUS_MODE) return;
  const A = window.__A;

  const planetGeo = new THREE.IcosahedronGeometry(1.9, 5);
  const planetUniforms = {
    uTime:          { value: 0 },
    uNoiseAmp:      { value: 0.42 },
    uMouseStrength: { value: 0 },
    uMouseHit:      { value: new THREE.Vector3(0,0,0) },
    uModelMat:      { value: new THREE.Matrix4() },
    uCamPos:        { value: new THREE.Vector3() },
  };
  const planetMat = new THREE.ShaderMaterial({
    uniforms: planetUniforms,
    vertexShader: GLSL_NOISE + APLUS_PLANET_VS,
    fragmentShader: GLSL_NOISE + `float fbm(vec3 p){float a=0.5;float r=0.0;for(int i=0;i<4;i++){r+=a*snoise(p);p*=2.02;a*=0.5;}return r;}` + APLUS_PLANET_FS,
    depthWrite: true,
    depthTest: true,
  });
  const planetMesh = new THREE.Mesh(planetGeo, planetMat);
  planetMesh.position.set(-1.2, 0.6, 0);
  planetMesh.renderOrder = 20;
  scene.add(planetMesh);

  // Inner core glow
  const planetCore = new THREE.Mesh(
    new THREE.IcosahedronGeometry(1.0, 4),
    new THREE.MeshBasicMaterial({ color:0xF6C445, transparent:true, opacity:0.05, blending:THREE.AdditiveBlending, depthWrite:false })
  );
  planetCore.position.copy(planetMesh.position);
  planetCore.renderOrder = 15;
  scene.add(planetCore);
  A.planet.core = planetCore;

  // 3 nested halos
  const haloConfigs = [
    { key:'in',  r:2.185, pow:1.6, alpha:0.032, c:new THREE.Color(0x22D3EE), pulse:'breath',renderOrder:10 },
    { key:'mid', r:2.812, pow:2.6, alpha:0.018, c:new THREE.Color(0xD946EF), pulse:'none',  renderOrder:3  },
    { key:'out', r:3.705, pow:4.5, alpha:0.007, c:new THREE.Color(0xF6C445), pulse:'mouse', renderOrder:1  },
  ];
  for (const h of haloConfigs) {
    const m = new THREE.ShaderMaterial({
      uniforms: { uColor:{value:h.c}, uAlpha:{value:h.alpha}, uFresPow:{value:h.pow}, uPulseK:{value:1.0} },
      vertexShader: APLUS_HALO_VS, fragmentShader: APLUS_HALO_FS,
      side: THREE.BackSide, transparent: true, blending: THREE.AdditiveBlending, depthWrite:false,
    });
    const haloMesh = new THREE.Mesh(new THREE.SphereGeometry(h.r, 64, 40), m);
    haloMesh.position.copy(planetMesh.position);
    haloMesh.renderOrder = h.renderOrder;
    scene.add(haloMesh);
    haloMesh.userData.pulseMode = h.pulse;
    A.planet.halos[h.key] = haloMesh;
  }

  A.planet.mesh = planetMesh;
  A.planet.uniforms = planetUniforms;

  // God Rays
  const grUniforms = {
    uTime:     { value: 0 },
    uStrength: { value: 1.0 }
  };
  const grMat = new THREE.ShaderMaterial({
    uniforms: grUniforms,
    vertexShader: GOD_RAY_VS,
    fragmentShader: GOD_RAY_FS,
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    depthTest: true,
  });
  const grGeo = new THREE.PlaneGeometry(32, 19, 1, 1);
  const grMesh = new THREE.Mesh(grGeo, grMat);
  grMesh.position.set(0, 0, -20);
  grMesh.renderOrder = 4;
  scene.add(grMesh);
  A.godRays = { mesh: grMesh, uniforms: grUniforms };

  // Per-frame update function
  A._planetUpdate = function() {
    const tt = clock.elapsedTime;
    planetUniforms.uTime.value = tt;
    planetUniforms.uNoiseAmp.value = 0.25 + 0.30 * (A.interaction.strength / 3);
    planetUniforms.uCamPos.value.copy(camera.position);
    planetMesh.updateMatrixWorld();
    planetUniforms.uModelMat.value.copy(planetMesh.matrixWorld);
    planetMesh.rotation.y = tt * 0.15 + mouse.x * 0.4;
    planetMesh.rotation.x = Math.sin(tt * 0.3) * 0.12 + mouse.y * 0.3;
    if (A.godRays) {
      A.godRays.uniforms.uTime.value = tt;
      A.godRays.uniforms.uStrength.value = 1.0 + A.interaction.strength * 0.28;
    }
    planetCore.scale.setScalar(0.85 + Math.sin(tt * 2.2) * 0.06);
    planetCore.material.opacity = 0.10 + Math.sin(tt * 2.2) * 0.04;
    if (A.planet.halos.in && A.planet.halos.in.userData.pulseMode==='breath')
      A.planet.halos.in.material.uniforms.uPulseK.value = 1.0 + 0.06*Math.sin(tt*0.50);
    if (A.planet.halos.out && A.planet.halos.out.userData.pulseMode==='mouse')
      A.planet.halos.out.material.uniforms.uPulseK.value = 1.0 + 0.1*A.interaction.strength;
  };
}

// ============ 初始化 LEGACY 轨道天体 ============
export function initLegacyOrbitBodies() {
  if (A_PLUS_MODE) return;
  const glowTex = makeGlowTexture();

  const orbitConfigs = [
    { r: 3.0, tilt: 0.15, speed: 0.45, size: 0.18, color: 0x22d3ee, phase: 0 },
    { r: 3.6, tilt: -0.35, speed: 0.32, size: 0.14, color: 0xe879f9, phase: 1.5 },
    { r: 4.2, tilt: 0.6, speed: 0.25, size: 0.22, color: 0x22d3ee, phase: 3.0 },
    { r: 5.0, tilt: -0.2, speed: 0.18, size: 0.12, color: 0xf0abfc, phase: 4.5 },
    { r: 5.8, tilt: 0.45, speed: 0.14, size: 0.16, color: 0xfbbf24, phase: 2.0 },
    { r: 6.6, tilt: -0.55, speed: 0.11, size: 0.20, color: 0x67e8f9, phase: 5.5 },
  ];

  orbitConfigs.forEach(cfg => {
    const g = new THREE.IcosahedronGeometry(cfg.size, 3);
    const m = new THREE.MeshStandardMaterial({
      color: cfg.color, emissive: cfg.color, emissiveIntensity: 1.2,
      metalness: 0.4, roughness: 0.3,
    });
    const mesh = new THREE.Mesh(g, m);
    scene.add(mesh);
    const haloMat = new THREE.SpriteMaterial({
      map: glowTex, color: cfg.color, transparent: true,
      blending: THREE.AdditiveBlending, depthWrite: false, opacity: 0.8,
    });
    const halo = new THREE.Sprite(haloMat);
    halo.scale.setScalar(cfg.size * 6);
    scene.add(halo);
    const trailPoints = [];
    for (let i = 0; i < 40; i++) trailPoints.push(new THREE.Vector3());
    const trailGeom = new THREE.BufferGeometry().setFromPoints(trailPoints);
    const trailMat = new THREE.LineBasicMaterial({
      color: cfg.color, transparent: true, opacity: 0.4, blending: THREE.AdditiveBlending,
    });
    const trail = new THREE.Line(trailGeom, trailMat);
    scene.add(trail);
    orbitBodies.push({ mesh, halo, haloMat, trail, trailGeom, trailPoints, cfg });
  });
}

// ============ 初始化 A+ 卫星 ============
export function initAPlusSatellites() {
  if (!A_PLUS_MODE) return;
  const A = window.__A;
  A.satellites.list = [];
  A.satellites.trails = [];
  const newSatConfigs = [
    { r:3.0,  c:'#67e8f9', phase:0.0,   far:false, ecc:0.06, tilt:0.08, size:0.09 },
    { r:3.6,  c:'#67e8f9', phase:1.1,   far:false, ecc:0.05, tilt:-0.10, size:0.07 },
    { r:6.6,  c:'#e879f9', phase:2.4,   far:true,  ecc:0.08, tilt:0.13, size:0.055 },
    { r:8.3,  c:'#22d3ee', phase:3.9,   far:true,  ecc:0.07, tilt:-0.15, size:0.050 },
    { r:10.6, c:'#d946ef', phase:5.2,   far:true,  ecc:0.10, tilt:0.18, size:0.045 },
    { r:12.8, c:'#fbbf24', phase:6.7,   far:true,  ecc:0.09, tilt:-0.20, size:0.040 },
  ];
  if (!A.assets.sharedGlowTex) A.assets.sharedGlowTex = makeSharedGlowTex();
  for (let i=0;i<6;i++){
    const cfg = newSatConfigs[i];
    const haloMat = new THREE.SpriteMaterial({ map:A.assets.sharedGlowTex, color:cfg.c, transparent:true,
      blending:THREE.AdditiveBlending, depthWrite:false, opacity: cfg.far?0.10:0.45 });
    const sprite = new THREE.Sprite(haloMat);
    sprite.scale.setScalar(cfg.size*1.8);
    sprite.renderOrder = cfg.far?6:15;
    scene.add(sprite);
    const TRAIL_N = 24;
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
}

// ============ 初始化 A+ ShockWave ============
export function initAPlusShockwave() {
  if (!A_PLUS_MODE) return;
  const A = window.__A;
  const ringGeom = new THREE.RingGeometry(0.95, 1.05, 128, 3);
  const ringMat = new THREE.ShaderMaterial({
    uniforms: {
      uColor: { value: new THREE.Color(0x00e5ff) },
      uWidth: { value: 0.18 },
      uT:     { value: 0 },
      uK:     { value: 0 },
    },
    vertexShader: SHOCKWAVE_VS,
    fragmentShader: SHOCKWAVE_FS,
    transparent:true, blending:THREE.AdditiveBlending, side:THREE.DoubleSide, depthWrite:false,
  });
  const sw = new THREE.Mesh(ringGeom, ringMat);
  sw.visible = false;
  sw.renderOrder = 30;
  scene.add(sw);
  A.shockwave = sw;
  A._triggerSW = function triggerShockwave() {
    A.shockwave.visible = true;
    A.shockwave.scale.setScalar(1);
    A.shockwave.material.uniforms.uK.value = 0.0;
  };
  window.triggerShockwave = () => A._triggerSW();
  A._updateSW = function(td) {
    const I = A.interaction;
    if (!I.triggerPulse && !A.shockwave.visible) return;
    const ph = I.triggerPulse ? I.pulsePhase : 1.0;
    const r = 2 + ph*6.5;
    A.shockwave.scale.setScalar(r);
    const peak = Math.exp(-Math.pow((ph-0.30)/0.30, 2));
    A.shockwave.material.uniforms.uK.value = I.triggerPulse ? peak : Math.max(0, A.shockwave.material.uniforms.uK.value - td*3.0);
    if (!I.triggerPulse && A.shockwave.material.uniforms.uK.value < 0.01) A.shockwave.visible = false;
  };
}

// ============ 初始化 A+ 量子/神经/几何可视化层 ============
export function initAPlusScienceLayers() {
  if (!A_PLUS_MODE) return;
  const A = window.__A;

  // ---- 1. 量子概率场 ----
  const qGeo = new THREE.IcosahedronGeometry(2.35, 5);
  const qMat = new THREE.ShaderMaterial({
    uniforms: {
      uTime:{value:0}, uMouse:{value:0},
      uColorA:{value:new THREE.Color(0x22D3EE)},
      uColorB:{value:new THREE.Color(0xD946EF)},
      uColorC:{value:new THREE.Color(0xFBBF24)},
    },
    vertexShader: QUANTUM_VS,
    fragmentShader: Q_NOISE + QUANTUM_FS,
    transparent: true, blending: THREE.AdditiveBlending,
    depthWrite: false, side: THREE.FrontSide,
  });
  const qField = new THREE.Mesh(qGeo, qMat);
  qField.renderOrder = 8;
  scene.add(qField);
  A.quantum = { field: qField, mat: qMat };

  // ---- 2. 神经网络拓扑 ----
  const NN_NODES = [5, 4, 3];
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  const totalNodes = NN_NODES.reduce((a,b)=>a+b,0);
  const nnPositions = [];
  for (let i=0; i<totalNodes; i++) {
    const y = 1 - (i / (totalNodes-1)) * 2;
    const r = Math.sqrt(1 - y*y);
    const phi = goldenAngle * i;
    nnPositions.push(new THREE.Vector3(
      Math.cos(phi)*r*2.5, y*2.5, Math.sin(phi)*r*2.5
    ));
  }
  const nodeGeo = new THREE.BufferGeometry().setFromPoints(nnPositions);
  const nodeMat = new THREE.PointsMaterial({
    color: 0x22D3EE, size: 0.07, transparent: true, opacity: 0.6,
    blending: THREE.AdditiveBlending, depthWrite: false
  });
  const nodePoints = new THREE.Points(nodeGeo, nodeMat);
  nodePoints.renderOrder = 11;
  scene.add(nodePoints);
  A.neuralNodes = nodePoints;

  const nnEdges = [];
  let offset = 0;
  for (let l=0; l<NN_NODES.length-1; l++) {
    const sA = offset, eA = offset+NN_NODES[l];
    const sB = eA, eB = eA+NN_NODES[l+1];
    for (let i=sA; i<eA; i++) {
      for (let j=sB; j<eB; j++) {
        if (Math.random() < 0.35) {
          nnEdges.push(nnPositions[i].clone(), nnPositions[j].clone());
        }
      }
    }
    offset = eA;
  }
  const edgeGeo = new THREE.BufferGeometry().setFromPoints(nnEdges);
  const edgeMat = new THREE.LineBasicMaterial({
    color: 0xD946EF, transparent: true, opacity: 0.14,
    blending: THREE.AdditiveBlending, depthWrite: false
  });
  const edgeLines = new THREE.LineSegments(edgeGeo, edgeMat);
  edgeLines.renderOrder = 10;
  scene.add(edgeLines);
  A.neuralEdges = edgeLines;

  // ---- 3. 能量轨道环 ----
  A.quantum.rings = [];
  const ringConfigs = [
    { axis: 'x', r: 2.9, c: 0x22D3EE },
    { axis: 'y', r: 3.35, c: 0xD946EF },
    { axis: 'z', r: 3.80, c: 0xFBBF24 },
  ];
  for (const rc of ringConfigs) {
    const rg = new THREE.TorusGeometry(rc.r, 0.003, 4, 64);
    const rm = new THREE.MeshBasicMaterial({
      color: rc.c, transparent: true, opacity: 0.15,
      blending: THREE.AdditiveBlending, depthWrite: false
    });
    const rmesh = new THREE.Mesh(rg, rm);
    if (rc.axis === 'x') rmesh.rotation.y = Math.PI/2;
    if (rc.axis === 'y') rmesh.rotation.x = Math.PI/2;
    rmesh.renderOrder = 9;
    scene.add(rmesh);
    A.quantum.rings.push({ mesh: rmesh, axis: rc.axis, baseR: rc.r });
  }

  // ---- 4. 能级跃迁壳 ----
  A.quantum.shells = [];
  for (let i=0; i<4; i++) {
    const radius = 1.55 + i * 0.42;
    const sg = new THREE.SphereGeometry(radius, 64, 40);
    const sm = new THREE.ShaderMaterial({
      uniforms: { uTime:{value:0}, uLevel:{value:i} },
      vertexShader: QUANTUM_SHELL_VS,
      fragmentShader: QUANTUM_SHELL_FS,
      transparent: true, blending: THREE.AdditiveBlending,
      depthWrite: false, side: THREE.BackSide,
    });
    const shell = new THREE.Mesh(sg, sm);
    shell.renderOrder = 7;
    scene.add(shell);
    A.quantum.shells.push({ mesh: shell, mat: sm });
  }
}

// ============ 完整初始化 ============
export function initPlanets() {
  initLegacySphere();
  initAPlusPlanet();
  initLegacyOrbitBodies();
  initAPlusSatellites();
  initAPlusShockwave();
  initAPlusScienceLayers();
}