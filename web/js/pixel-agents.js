/**
 * NeuroForge Pixel Agents v6
 * 根治：白色+Bloom=灰色、位置重叠挡文字、动画幅度太小
 * 方案：Among Us 风格 — 大面积鲜艳色主体 + 深色面罩 + 粗轮廓
 */

import * as THREE from 'three';
import { scene, camera } from './three-scene.js';

const SPRITE = 128;

function rr(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

/* ================================================================
   动画数据：每帧 [身体X,身体Y, 头X,头Y, 左臂角度,右臂角度, 左腿X,右腿X, 眨眼, 特殊]
   幅度 ±15~30px，确保屏幕上看得到动作
   ================================================================ */
const ANIM = {
  analyst: {
    idle: [
      [0,0, 0,0, 0,0, 0,0, 0,0],
      [-3,-2, -3,2, -10,5, -2,2, 0,0],
      [-6,-3, -6,3, -15,8, -3,3, 0,0],
      [-3,-2, -3,2, -10,5, -2,2, 0,0],
      [0,0, 0,0, 0,0, 0,0, 1,0],
      [3,-2, 3,2, 5,-10, 2,2, 0,0],
      [6,-3, 6,3, 8,-15, 3,3, 0,0],
      [3,-2, 3,2, 5,-10, 2,2, 0,0],
    ],
    working: [
      [0,-3, 0,-3, -20,10, 0,0, 0,0],
      [-2,-5, -2,-5, -25,15, -1,1, 0,0],
      [-4,-6, -4,-6, -30,20, -2,2, 0,0],
      [-2,-5, -2,-5, -25,15, -1,1, 0,0],
      [0,-3, 0,-3, -20,10, 0,0, 0,0],
      [2,-5, 2,-5, -15,25, 1,1, 0,0],
      [4,-6, 4,-6, -10,30, 2,2, 0,0],
      [2,-5, 2,-5, -15,25, 1,1, 0,0],
    ],
    done:  [[0,-8, 0,-8, -30,-30, 0,0, 0,1]],
    error: [[0,0, 0,0, 15,15, 0,0, 1,0]],
  },
  coder: {
    idle: [
      [0,0, 0,0, 0,0, 0,0, 0,0],
      [0,-2, 0,-4, -5,-5, 5,-5, 0,0],
      [0,-4, 0,-6, -8,-8, 8,-8, 0,0],
      [0,-5, 0,-7, -10,-10, 10,-10, 0,0],
      [0,-5, 1,-7, -8,-12, 8,-10, 0,0],
      [0,-5, -1,-7, -10,-10, 10,-12, 0,0],
      [0,-3, 0,-5, -5,-8, 5,-8, 0,0],
      [0,0, 0,0, 0,0, 0,0, 0,0],
    ],
    working: [
      [0,-5, 0,-7, -15,-20, 15,-20, 0,0],
      [0,-6, 0,-8, -20,-25, 20,-25, 0,0],
      [0,-7, 0,-9, -25,-30, 25,-30, 0,0],
      [0,-6, 0,-8, -20,-25, 20,-25, 0,0],
      [0,-5, 0,-7, -15,-20, 15,-20, 0,0],
      [0,-6, 1,-8, -20,-25, 20,-25, 0,0],
      [0,-7, -1,-9, -25,-30, 25,-30, 0,0],
      [0,-6, 0,-8, -20,-25, 20,-25, 0,0],
    ],
    done:  [[0,-8, 0,-8, -30,-30, 0,0, 0,1]],
    error: [[0,0, 0,0, 20,20, 0,0, 1,0]],
  },
  reviewer: {
    idle: [
      [0,0, 0,0, 0,0, 0,0, 0,0],
      [-4,-2, -4,2, -12,0, 0,0, 0,0],
      [-7,-3, -7,3, -18,0, -2,2, 0,0],
      [-4,-2, -4,2, -12,0, 0,0, 0,0],
      [0,0, 0,0, 0,0, 0,0, 1,0],
      [4,-2, 4,2, 0,12, 2,2, 0,0],
      [7,-3, 7,3, 0,18, 3,3, 0,0],
      [4,-2, 4,2, 0,12, 2,2, 0,0],
    ],
    working: [
      [0,-2, 0,-2, -20,0, 0,0, 0,0],
      [-3,-3, -3,-3, -25,0, -1,1, 0,0],
      [-6,-4, -6,-4, -30,0, -2,2, 0,0],
      [-3,-3, -3,-3, -25,0, -1,1, 0,0],
      [0,-2, 0,-2, -20,0, 0,0, 0,0],
      [3,-3, 3,-3, 0,25, 1,1, 0,0],
      [6,-4, 6,-4, 0,30, 2,2, 0,0],
      [3,-3, 3,-3, 0,25, 1,1, 0,0],
    ],
    done:  [[0,-8, 0,-8, -30,-30, 0,0, 0,1]],
    error: [[0,0, 0,0, 15,15, 0,0, 1,0]],
  },
  tester: {
    idle: [
      [0,0, 0,0, 0,0, 0,0, 0,0],
      [0,3, 0,3, -8,8, -2,2, 0,0],
      [0,8, 0,8, -12,12, -3,3, 0,0],
      [0,-8, 0,-8, -15,-20, -2,-2, 0,0],
      [0,-20, 0,-20, -18,-25, -3,-3, 0,0],
      [0,-8, 0,-8, -12,-15, -2,-2, 0,0],
      [0,5, 0,5, -8,10, -2,2, 0,0],
      [0,1, 0,1, -4,4, -1,1, 0,0],
    ],
    working: [
      [0,0, 0,0, 0,0, 0,0, 0,0],
      [0,5, 0,5, -10,10, -3,3, 0,0],
      [0,12, 0,12, -15,15, -4,4, 0,0],
      [0,-12, 0,-12, -20,-25, -3,-3, 0,0],
      [0,-28, 0,-28, -25,-30, -5,-5, 0,0],
      [0,-12, 0,-12, -15,-20, -3,-3, 0,0],
      [0,8, 0,8, -10,12, -3,3, 0,0],
      [0,2, 0,2, -5,5, -2,2, 0,0],
    ],
    done:  [[0,-8, 0,-8, -30,-30, 0,0, 0,1]],
    error: [[0,0, 0,0, 20,20, 0,0, 1,0]],
  },
};

/* ================================================================
   角色定义：新位置 — 四角分布，不挡文字
   ================================================================ */
const AGENTS = [
  {
    id: 'analyst', name: 'Analyst',
    color: '#3B82F6', accent: '#60A5FA', glow: '#1E40AF',
    basePos: [-5.5, 1.0, 2.5], scale: 2.2,
    bobPhase: 0, bobSpeed: 1.2, animSpeed: 0.12,
  },
  {
    id: 'coder', name: 'Coder',
    color: '#D946EF', accent: '#F0ABFC', glow: '#86198F',
    basePos: [5.5, 1.0, 2.5], scale: 2.2,
    bobPhase: 1.5, bobSpeed: 1.5, animSpeed: 0.08,
  },
  {
    id: 'reviewer', name: 'Reviewer',
    color: '#F59E0B', accent: '#FCD34D', glow: '#92400E',
    basePos: [-5.5, -3.5, 2.5], scale: 2.2,
    bobPhase: 3.0, bobSpeed: 1.0, animSpeed: 0.10,
  },
  {
    id: 'tester', name: 'Tester',
    color: '#06B6D4', accent: '#67E8F9', glow: '#155E75',
    basePos: [5.5, -3.5, 2.5], scale: 2.2,
    bobPhase: 4.5, bobSpeed: 2.0, animSpeed: 0.10,
  },
];

/* ================================================================
   PixelAgent 类 — 独立 CanvasTexture，逐帧重绘
   ================================================================ */
class PixelAgent {
  constructor(def) {
    this.id = def.id;
    this.name = def.name;
    this.color = def.color;      // 主题色（身体主色）
    this.accent = def.accent;    // 高光色
    this.glow = def.glow;        // 发光色（深色版）
    this.basePos = [...def.basePos];
    this.scale = def.scale;
    this.bobPhase = def.bobPhase;
    this.bobSpeed = def.bobSpeed;
    this.animSpeed = def.animSpeed;
    this.animData = ANIM[def.id];

    this.state = 'idle';
    this.frameIdx = 0;
    this.animT = 0;
    this.px = 0; this.py = 0;    // 视差偏移

    // Canvas
    this.canvas = document.createElement('canvas');
    this.canvas.width = this.canvas.height = SPRITE;
    this.ctx = this.canvas.getContext('2d');
    this.ctx.imageSmoothingEnabled = false;

    // Texture
    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.magFilter = THREE.NearestFilter;
    this.texture.minFilter = THREE.NearestFilter;

    // Mesh (PlaneGeometry 1:1 正方形，根治扁平)
    const geo = new THREE.PlaneGeometry(1.5, 1.5);
    const mat = new THREE.MeshBasicMaterial({
      map: this.texture,
      transparent: true,
      alphaTest: 0.1,
      depthWrite: false,
      side: THREE.DoubleSide,
    });
    this.mesh = new THREE.Mesh(geo, mat);
    this.mesh.renderOrder = 100;
    this.mesh.position.set(...this.basePos);
    this.mesh.scale.set(this.scale, this.scale, 1);
    scene.add(this.mesh);

    // 外发光 mesh（更大、更明显）
    const gg = new THREE.PlaneGeometry(2.2, 2.2);
    const gm = new THREE.MeshBasicMaterial({
      color: this.accent,
      transparent: true,
      opacity: 0.15,
      depthWrite: false,
      side: THREE.DoubleSide,
      blending: THREE.AdditiveBlending,
    });
    this.glowMesh = new THREE.Mesh(gg, gm);
    this.glowMesh.position.z = -0.02;
    this.mesh.add(this.glowMesh);

    // 强点光源
    this.light = new THREE.PointLight(this.accent, 3.0, 8.0);
    this.light.position.set(this.basePos[0], this.basePos[1] + 0.5, this.basePos[2]);
    scene.add(this.light);

    this.draw();
  }

  /* ---------- 绘制 ---------- */
  draw() {
    const ctx = this.ctx;
    const f = this.animData[this.state][this.frameIdx];
    const [bx,by, hx,hy, la,ra, llx,rlx, blink, special] = f;

    ctx.clearRect(0, 0, SPRITE, SPRITE);

    const C = this.color;
    const D = this.glow;   // 深色轮廓
    const cx = 64, cy = 64;

    // --- 脚下平台 ---
    ctx.fillStyle = this.accent + '20';
    ctx.beginPath(); ctx.ellipse(cx, 118, 40, 10, 0, 0, Math.PI*2); ctx.fill();
    ctx.fillStyle = this.accent + '40';
    ctx.beginPath(); ctx.ellipse(cx, 118, 24, 6, 0, 0, Math.PI*2); ctx.fill();

    // --- 背包（深色）---
    ctx.fillStyle = '#1a1a2e';
    rr(ctx, cx-14+bx, cy-28+by, 28, 30, 5); ctx.fill();
    ctx.fillStyle = C;
    ctx.fillRect(cx-5+bx, cy-32+by, 10, 5);

    // --- 腿（深色关节+主题色腿）---
    this._leg(ctx, cx-10+llx, cy+18, C, D);
    this._leg(ctx, cx+10+rlx, cy+18, C, D);

    // --- 身体（大面积主题色！不是白色）---
    // 主体 bean shape
    ctx.fillStyle = C;
    rr(ctx, cx-18+bx, cy-12+by, 36, 34, 10); ctx.fill();
    // 深色描边
    ctx.strokeStyle = D; ctx.lineWidth = 2;
    rr(ctx, cx-18+bx, cy-12+by, 36, 34, 10); ctx.stroke();
    // 高光条
    ctx.fillStyle = this.accent;
    ctx.fillRect(cx-18+bx, cy-12+by, 36, 4);
    ctx.fillRect(cx-18+bx, cy+14+by, 36, 3);
    // 胸前徽章（白色小方块）
    ctx.fillStyle = '#fff';
    ctx.fillRect(cx-4+bx, cy+2+by, 8, 6);
    ctx.fillStyle = C;
    ctx.fillRect(cx-3+bx, cy+3+by, 6, 4);

    // --- 手臂（主题色+角度摆动）---
    this._arm(ctx, cx-22+bx, cy-2+by, la, C, D);
    this._arm(ctx, cx+22+bx, cy-2+by, ra, C, D);

    // --- 头部（深色头盔框+深色面罩）---
    ctx.fillStyle = '#e2e8f0';          // 浅灰头盔外框
    ctx.beginPath(); ctx.arc(cx+hx, cy-22+hy, 24, 0, Math.PI*2); ctx.fill();
    ctx.strokeStyle = D; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(cx+hx, cy-22+hy, 24, 0, Math.PI*2); ctx.stroke();

    ctx.fillStyle = '#0f0f1a';          // 深色面罩（占头部80%）
    ctx.beginPath(); ctx.arc(cx+hx, cy-20+hy, 20, 0, Math.PI*2); ctx.fill();

    ctx.fillStyle = 'rgba(255,255,255,0.08)'; // 面罩微反光
    ctx.beginPath(); ctx.arc(cx+hx-6, cy-26+hy, 10, 0, Math.PI*2); ctx.fill();

    // --- 眼睛（发光！）---
    if (blink) {
      ctx.fillStyle = '#475569';
      ctx.fillRect(cx+hx-9, cy-20+hy, 7, 2);
      ctx.fillRect(cx+hx+2, cy-20+hy, 7, 2);
    } else {
      ctx.fillStyle = '#fff';
      ctx.fillRect(cx+hx-9, cy-22+hy, 7, 5);
      ctx.fillRect(cx+hx+2, cy-22+hy, 7, 5);
      // 瞳孔 — 角色专属色
      ctx.fillStyle = this.accent;
      ctx.fillRect(cx+hx-7, cy-21+hy, 4, 3);
      ctx.fillRect(cx+hx+4, cy-21+hy, 4, 3);
      // 高光
      ctx.fillStyle = '#fff';
      ctx.fillRect(cx+hx-6, cy-21+hy, 2, 2);
      ctx.fillRect(cx+hx+5, cy-21+hy, 2, 2);
    }

    // --- 角色专属道具 ---
    this._prop(ctx, cx+bx, cy+by, la, ra, special);

    this.texture.needsUpdate = true;
  }

  _leg(ctx, x, y, C, D) {
    ctx.fillStyle = '#1a1a2e';
    rr(ctx, x-4, y, 8, 16, 3); ctx.fill();
    ctx.strokeStyle = D; ctx.lineWidth = 1;
    rr(ctx, x-4, y, 8, 16, 3); ctx.stroke();
    ctx.fillStyle = C;
    ctx.fillRect(x-3, y+2, 6, 10);
  }

  _arm(ctx, x, y, angle, C, D) {
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle * Math.PI / 180);
    ctx.fillStyle = '#1a1a2e';
    rr(ctx, -4, 0, 8, 18, 3); ctx.fill();
    ctx.strokeStyle = D; ctx.lineWidth = 1;
    rr(ctx, -4, 0, 8, 18, 3); ctx.stroke();
    ctx.fillStyle = C;
    ctx.fillRect(-3, 2, 6, 12);
    ctx.restore();
  }

  _prop(ctx, bx, by, la, ra, special) {
    const c = 64;
    if (this.id === 'analyst') {
      // 数据板 — 左臂末端
      const ax = c-26+bx, ay = c+8+by;
      ctx.fillStyle = '#1a1a2e';
      rr(ctx, ax-12, ay-12, 24, 20, 4); ctx.fill();
      ctx.strokeStyle = this.accent; ctx.lineWidth = 1;
      rr(ctx, ax-12, ay-12, 24, 20, 4); ctx.stroke();
      ctx.fillStyle = '#0f0';
      ctx.fillRect(ax-8, ay-8, 5, 4);
      ctx.fillRect(ax-8, ay-2, 5, 4);
      ctx.fillRect(ax+2, ay-5, 8, 3);
    } else if (this.id === 'coder') {
      // 键盘 — 身体前方
      const kx = c+bx, ky = c+16+by;
      ctx.fillStyle = '#1a1a2e';
      rr(ctx, kx-16, ky, 32, 14, 3); ctx.fill();
      ctx.strokeStyle = this.accent; ctx.lineWidth = 1;
      rr(ctx, kx-16, ky, 32, 14, 3); ctx.stroke();
      ctx.fillStyle = '#475569';
      for (let i=0;i<3;i++) for (let j=0;j<6;j++)
        ctx.fillRect(kx-14+j*5, ky+2+i*3, 4, 2);
    } else if (this.id === 'reviewer') {
      // 放大镜 — 左臂末端
      const rx = c-26+bx, ry = c+8+by;
      ctx.fillStyle = '#888';
      ctx.beginPath(); ctx.arc(rx, ry, 10, 0, Math.PI*2); ctx.fill();
      ctx.fillStyle = 'rgba(200,220,255,0.4)';
      ctx.beginPath(); ctx.arc(rx, ry, 8, 0, Math.PI*2); ctx.fill();
      ctx.fillStyle = '#1a1a2e';
      ctx.fillRect(rx-2, ry+10, 4, 14);
      ctx.fillStyle = this.accent;
      ctx.fillRect(rx-1, ry+10, 2, 14);
    }
    // tester: 无道具（双手自由跳跃）
  }

  /* ---------- 更新 ---------- */
  update(dt, elapsed, parallaxX, parallaxY) {
    this.animT += dt;
    if (this.animT >= this.animSpeed) {
      this.animT = 0;
      const frames = this.animData[this.state];
      this.frameIdx = (this.frameIdx + 1) % frames.length;
      this.draw();
    }

    const bob = Math.sin(elapsed * this.bobSpeed + this.bobPhase) * 0.15;
    this.mesh.position.set(
      this.basePos[0] + parallaxX * 0.5 + this.px,
      this.basePos[1] + bob + parallaxY * 0.3 + this.py,
      this.basePos[2]
    );
    this.mesh.lookAt(camera.position);

    if (this.light) {
      this.light.position.copy(this.mesh.position);
      this.light.position.y += 0.5;
    }
  }

  setState(s) {
    if (this.state === s) return;
    this.state = s;
    this.frameIdx = 0;
    this.animT = 0;
    this.draw();
  }

  setParallax(x, y) {
    this.px = x; this.py = y;
  }

  dispose() {
    this.texture.dispose();
    this.mesh.geometry.dispose();
    this.mesh.material.dispose();
    this.glowMesh.geometry.dispose();
    this.glowMesh.material.dispose();
    scene.remove(this.mesh);
    if (this.light) scene.remove(this.light);
  }
}

/* ================================================================
   系统管理
   ================================================================ */
class PixelAgentSystem {
  constructor() {
    this.agents = [];
    this.initialized = false;
  }

  init() {
    if (this.initialized) return;
    for (const def of AGENTS) {
      this.agents.push(new PixelAgent(def));
    }
    this.initialized = true;
    console.log(`[PixelAgents] v6: ${this.agents.length} agents initialized`);
  }

  update(dt, elapsed) {
    for (const a of this.agents) a.update(dt, elapsed, 0, 0);
  }

  setParallax(x, y) {
    for (const a of this.agents) a.setParallax(x, y);
  }

  setAgentState(id, state) {
    const a = this.agents.find(a => a.id === id);
    if (a) a.setState(state);
  }

  dispose() {
    for (const a of this.agents) a.dispose();
    this.agents = [];
    this.initialized = false;
  }
}

export const pixelAgents = new PixelAgentSystem();
export function initPixelAgents() { pixelAgents.init(); }
export function updatePixelAgents(dt, elapsed) { pixelAgents.update(dt, elapsed); }
export function setPixelAgentParallax(x, y) { pixelAgents.setParallax(x, y); }
export function setPixelAgentState(id, state) { pixelAgents.setAgentState(id, state); }
