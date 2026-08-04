/* ============================================================
   NeuroForge · 主入口
   初始化所有模块、动画循环、清理
   ============================================================ */

import * as THREE from 'three';
import {
  scene, camera, renderer, clock, mouse, composer, bloom,
  A_PLUS_MODE, _bloomPass, aaPass, outputPass, visible,
  l1, l2, l3, keyLight, initScene, onPointerMoveSync
} from './three-scene.js';
import { initParticles,
  flowP1, flowP2, flowP3, matFlow1, matFlow2, matFlow3,
  geoFlow1, geoFlow2, geoFlow3, ringParticles, ringParticles2,
  matRing, matRing2, geoRing, geoRing2
} from './particles.js';
import { initPlanets,
  sphereGeom, sphereUniforms, sphereMat, sphere,
  coreGeom, coreMat, core, haloGeom, haloMat, halo,
  orbitBodies
} from './planets.js';
import './ui.js';

// ============ 初始化场景 ============
if (!initScene()) {
  console.warn('Three.js container not found');
}

// ============ 初始化所有子系统 ============
initParticles();
initPlanets();

// ============ 鼠标视差 ============
window.addEventListener('mousemove', (e) => {
  mouse.tx = (e.clientX / window.innerWidth) * 2 - 1;
  mouse.ty = (e.clientY / window.innerHeight) * 2 - 1;
}, { passive: true });

// ============ A+ 单点交互：pointer 同步 ============
if (A_PLUS_MODE) {
  renderer.domElement.addEventListener('pointermove', onPointerMoveSync, { passive: true });
}

// ============ 动画循环 ============
let rafId = null;
let _frameCount = 0;

function animate() {
  rafId = requestAnimationFrame(animate);
  _frameCount++;
  if (_frameCount === 3) console.log('Three.js animate running, scene children:', scene.children.length);

  const td = clock.getDelta();
  const t = clock.getElapsedTime();

  // A+ 交互步进
  if (A_PLUS_MODE && window.__A) {
    const A = window.__A;
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
      if (typeof window.triggerShockwave === 'function') window.triggerShockwave();
    }
    if (it.triggerPulse) {
      it.pulsePhase += td * 0.45;
      if (it.pulsePhase >= 1.0) {
        it.triggerPulse = false;
        it.pulsePhase = 0;
      }
    }
    A.uniforms.uTime.value = t;
    A.uniforms.uMouseStrength.value = it.strength;
    A.uniforms.uMouseHit.value.copy(approxPlanetHit(it.mouseNDC, camera));
  }

  if (A_PLUS_MODE && window.__A && window.__A.skydome) window.__A.skydome.position.copy(camera.position);

  // 平滑鼠标
  mouse.x += (mouse.tx - mouse.x) * 0.05;
  mouse.y += (mouse.ty - mouse.y) * 0.05;

  // 更新 shader uniform
  if (!A_PLUS_MODE && sphereUniforms) {
    sphereUniforms.uTime.value = t;
    sphereUniforms.uMouse.value.set(mouse.x, mouse.y);
    if (coreMat) coreMat.opacity = 0.7 + Math.sin(t * 2.2) * 0.25;
    if (core) core.scale.setScalar(0.9 + Math.sin(t * 2.2) * 0.15);
    if (haloMat) haloMat.uniforms.uTime.value = t;
    if (halo) halo.scale.setScalar(1.0 + Math.sin(t * 1.5) * 0.1);
  }

  if (A_PLUS_MODE && window.__A && window.__A._planetUpdate) {
    window.__A._planetUpdate();
  }

  if (!A_PLUS_MODE) {
    if (matFlow1) { matFlow1.uniforms.uTime.value = t; matFlow1.uniforms.uMouse.value.set(mouse.x, mouse.y); }
    if (matFlow2) { matFlow2.uniforms.uTime.value = t; matFlow2.uniforms.uMouse.value.set(mouse.x, mouse.y); }
    if (matFlow3) { matFlow3.uniforms.uTime.value = t; matFlow3.uniforms.uMouse.value.set(mouse.x, mouse.y); }
  }

  if (!A_PLUS_MODE) {
    if (matRing) matRing.uniforms.uTime.value = t;
    if (matRing2) matRing2.uniforms.uTime.value = t;
  }

  // 球体自转
  if (!A_PLUS_MODE && sphere) {
    sphere.rotation.y = t * 0.15 + mouse.x * 0.4;
    sphere.rotation.x = Math.sin(t * 0.3) * 0.12 + mouse.y * 0.3;
  }

  // 星环倾斜
  if (!A_PLUS_MODE) {
    if (ringParticles) ringParticles.rotation.z += 0.0005;
    if (ringParticles2) ringParticles2.rotation.z -= 0.0003;
  }
  if (A_PLUS_MODE && window.__A) {
    const A = window.__A;
    if (A.particles.ring.pts)  A.particles.ring.pts.rotation.y  += td * A.particles.ring._spin;
    if (A.particles.ring2.pts) A.particles.ring2.pts.rotation.y += td * A.particles.ring2._spin;
  }

  // A+ 模式：3 层 Ping-Pong 流场粒子步进
  if (A_PLUS_MODE && window.__A) {
    const A = window.__A;
    const u = {
      uTime: A.uniforms.uTime.value,
      uMouseWorld: A.interaction.mouseWorld,
      uMouseStrength: A.interaction.strength,
      uPulseOrigin: new THREE.Vector3(0,0,0),
      uPulseRadius: A.interaction.triggerPulse ? (2 + A.interaction.pulsePhase*6.5) : -1.0,
    };
    for (const k of ['flowP1','flowP2','flowP3']) {
      if (A.particles[k] && A.particles[k].pp) A.particles[k].pp.step(td, u);
    }
  }

  // LEGACY 轨道天体
  if (!A_PLUS_MODE) {
    orbitBodies.forEach((o, idx) => {
      const c = o.cfg;
      const a = t * c.speed + c.phase;
      const x = c.r * Math.cos(a);
      const z = c.r * Math.sin(a);
      const y = Math.sin(a) * c.r * c.tilt;
      o.mesh.position.set(x, y, z);
      o.mesh.rotation.y = t * 0.5;
      o.mesh.rotation.x = t * 0.3;
      o.halo.position.set(x, y, z);
      o.trailPoints.shift();
      o.trailPoints.push(new THREE.Vector3(x, y, z));
      o.trailGeom.setFromPoints(o.trailPoints);
      o.mesh.material.emissiveIntensity = 1.2 + Math.sin(t * 2 + idx) * 0.5;
      o.haloMat.opacity = 0.6 + Math.sin(t * 1.5 + idx) * 0.3;
    });
  }

  // A+ 卫星
  if (A_PLUS_MODE) {
    const A = window.__A;
    const S = A.interaction.strength;
    for (let i=0;i<6;i++){
      const sat = A.satellites.list[i];
      const bo = sat.baseOrbit; const co = sat.cur;
      const tgtEcc  = bo.ecc  + S * 0.09 * (i%2===0?1:-1);
      const tgtTilt = bo.tilt + S * 0.05 * (i%2===0?-1:1);
      const tgtR    = bo.r    + Math.sin(t*0.6 + i) * 0.03;
      co.ecc  += (tgtEcc - co.ecc)   * 0.06;
      co.tilt += (tgtTilt - co.tilt) * 0.06;
      co.r    += (tgtR    - co.r)    * 0.04;
      sat.cur.phase += td * sat.kSpin / Math.max(0.1, co.r);
      const theta = sat.cur.phase;
      const ellipseR = co.r * (1 - co.ecc*co.ecc) / (1 + co.ecc*Math.cos(theta));
      const X = ellipseR * Math.cos(theta);
      const Z = ellipseR * Math.sin(theta);
      let pos = new THREE.Vector3(X, 0, Z);
      pos.applyAxisAngle(new THREE.Vector3(1,0,0), co.tilt);
      const md = A.interaction.mouseWorld.clone();
      const dirMouse = md.sub(pos).normalize();
      sat.trailRing[sat.ringHead].copy(pos);
      const TN = sat.trailRing.length;
      const tposArr = sat.trail.geometry.attributes.position.array;
      const talpArr = sat.trail.geometry.attributes.aAlpha.array;
      const tcolArr = sat.trail.geometry.attributes.color.array;
      const baseCol = sat._baseCol || (sat._baseCol = new THREE.Color(sat.cfg.c));
      const fadeCol = sat._fadeCol || (sat._fadeCol = new THREE.Color('#2a0a4a'));
      const tmpCol = sat._tmpCol || (sat._tmpCol = new THREE.Color());
      const tmpBp = sat._tmpBp || (sat._tmpBp = new THREE.Vector3());
      for (let j=0;j<TN;j++){
        const kAge = j/TN;
        const idx = (sat.ringHead - j + TN) % TN;
        tmpBp.copy(sat.trailRing[idx]);
        tmpBp.addScaledVector(dirMouse, 0.12 * S * (1 - kAge));
        tposArr[j*3  ] = tmpBp.x; tposArr[j*3+1] = tmpBp.y; tposArr[j*3+2] = tmpBp.z;
        talpArr[j] = 0.95*(1 - kAge*0.95);
        tmpCol.copy(baseCol).lerp(fadeCol, kAge);
        tcolArr[j*3  ] = tmpCol.r;
        tcolArr[j*3+1] = tmpCol.g;
        tcolArr[j*3+2] = tmpCol.b;
      }
      sat.trail.geometry.attributes.position.needsUpdate = true;
      sat.trail.geometry.attributes.color.needsUpdate = true;
      sat.trail.geometry.attributes.aAlpha.needsUpdate = true;
      sat.ringHead = (sat.ringHead + 1) % sat.trailRing.length;
      sat.mesh.position.copy(pos);
      const spd = pos.distanceTo(sat.prevPos) / Math.max(1e-5, td);
      sat.meanSpeed = sat.meanSpeed*0.96 + spd*0.04;
      sat.prevPos.copy(pos);
      sat.mesh.scale.setScalar(sat.cfg.size*1.8 * (sat.cfg.far? 0.75 : 1.0));
    }
  }

  // A+ 冲击环更新 + 科学可视化层
  if (A_PLUS_MODE && window.__A && window.__A._updateSW) window.__A._updateSW(td);
  if (A_PLUS_MODE && window.__A && window.__A.quantum) {
    const Q = window.__A.quantum;
    Q.mat.uniforms.uTime.value = t;
    Q.mat.uniforms.uMouse.value = window.__A.interaction.strength;
    Q.field.rotation.y = t * 0.08;
    Q.field.rotation.x = t * 0.04;
    const qs = 1 + Math.sin(t*0.4)*0.015 + window.__A.interaction.strength*0.02;
    Q.field.scale.setScalar(qs);
    for (let i=0;i<Q.rings.length;i++){
      const r = Q.rings[i];
      r.mesh.rotation.z = t * (0.12 + i*0.04);
      r.mesh.rotation.x += td * 0.08 * (i%2?1:-1);
    }
    if (Q.shells) {
      for (const sh of Q.shells) {
        sh.mat.uniforms.uTime.value = t;
        sh.mesh.rotation.y = t * 0.1;
        sh.mesh.rotation.x = t * 0.07;
      }
    }
  }
  if (A_PLUS_MODE && window.__A && window.__A.neuralNodes) {
    window.__A.neuralNodes.rotation.y = t * 0.06;
    window.__A.neuralNodes.rotation.x = t * 0.03;
    const ns = 1 + Math.sin(t*0.5)*0.04;
    window.__A.neuralNodes.scale.setScalar(ns);
    if (window.__A.neuralEdges) {
      window.__A.neuralEdges.rotation.copy(window.__A.neuralNodes.rotation);
      window.__A.neuralEdges.scale.setScalar(ns);
      const act = 0.10 + 0.10*Math.sin(t*1.3);
      window.__A.neuralEdges.material.opacity = act + window.__A.interaction.strength*0.08;
    }
  }
  if (A_PLUS_MODE && _bloomPass) {
    _bloomPass.strength = 0.34 + 0.025*Math.sin(t*0.7);
    const A = window.__A;
    const vm = Math.hypot(A.interaction.velocity.x, A.interaction.velocity.y);
    const aber = THREE.MathUtils.clamp((vm - 0.008) * 140.0, 0.0, 0.0018);
    if (A._CA) A._CA.uniforms.uAber.value = aber;
    if (A._grain) A._grain.uniforms.uTime.value = t;
  }

  // 光照跟随鼠标
  l1.position.x = 5 + Math.sin(t * 0.8) * 2 + mouse.x * 2;
  l1.position.y = 4 + Math.cos(t * 0.6) * 2 + mouse.y * 2;
  l2.position.x = -5 + Math.cos(t * 0.7) * 2;
  l2.position.y = -3 + Math.sin(t * 0.9) * 2;

  // Bloom 强度呼吸
  if (!A_PLUS_MODE && bloom) {
    bloom.strength = 0.11 + Math.sin(t * 0.5) * 0.03;
  }

  // 相机微摆
  const baseLookX = -1.2, baseLookY = 0.6, baseLookZ = 0.0;
  const camPeriod = 12.0;
  const swingAmp = 0.11;
  const pushAmp  = 1.0;
  const camPhase = t * 2.0 * Math.PI / camPeriod;
  const swingX = Math.sin(camPhase * 0.7) * swingAmp * 0.95;
  const swingY = Math.cos(camPhase * 1.15 + 0.3) * swingAmp * 0.70;
  const pushZ  = Math.sin(camPhase * 1.6 + 1.7) * pushAmp;
  const tgtX = mouse.x * 0.90 + swingX;
  const tgtY = mouse.y * -0.72 + 0.4 + swingY;
  const tgtZ = 7.8 + pushZ;
  camera.position.x += (tgtX - camera.position.x) * 0.036;
  camera.position.y += (tgtY - camera.position.y) * 0.036;
  camera.position.z += (tgtZ - camera.position.z) * 0.022;
  camera.lookAt(baseLookX + swingX * 0.18, baseLookY + swingY * 0.22, baseLookZ);

  composer.render();
}

// ============ 辅助函数 ============
function approxPlanetHit(ndc, cam) {
  const ray = new THREE.Raycaster();
  ray.setFromCamera(ndc, cam);
  const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 10);
  const hit = new THREE.Vector3();
  if (ray.ray.intersectPlane(plane, hit)) return hit;
  return new THREE.Vector3(0, 0, -10);
}

// ============ 启动 ============
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
console.log('Three.js init done. reduceMotion:', reduceMotion, 'scene children:', scene.children.length, 'canvas:', renderer.domElement.width + 'x' + renderer.domElement.height);
if (!reduceMotion) {
  animate();
} else {
  composer.render();
  console.log('Rendered one frame (reduceMotion)');
}

// ============ 清理 ============
window._threeCleanup = () => {
  if (rafId) cancelAnimationFrame(rafId);
  renderer.dispose();
  composer.dispose();
  if (sphereGeom) sphereGeom.dispose();
  if (sphereMat) sphereMat.dispose();
  if (coreGeom) coreGeom.dispose();
  if (coreMat) coreMat.dispose();
  if (haloGeom) haloGeom.dispose();
  if (haloMat) haloMat.dispose();
  if (geoFlow1) geoFlow1.dispose();
  if (geoFlow2) geoFlow2.dispose();
  if (geoFlow3) geoFlow3.dispose();
  if (matFlow1) matFlow1.dispose();
  if (matFlow2) matFlow2.dispose();
  if (matFlow3) matFlow3.dispose();
  if (geoRing) geoRing.dispose();
  if (matRing) matRing.dispose();
  if (geoRing2) geoRing2.dispose();
  if (matRing2) matRing2.dispose();
  orbitBodies.forEach(o => {
    o.mesh.geometry.dispose(); o.mesh.material.dispose();
    o.haloMat.dispose(); o.trailGeom.dispose();
  });
  renderer.domElement.remove();
};