/* ============================================================
   NeuroForge · Shaders
   所有 GLSL Shader 代码、噪声函数、纹理生成工具
   ============================================================ */

import * as THREE from 'three';

// ============ 共享 GLSL 噪声函数（流体力学核心） ============
export const GLSL_NOISE = `
  vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
  vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
  vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
  vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
  float snoise(vec3 v){
    const vec2 C=vec2(1.0/6.0,1.0/3.0);
    const vec4 D=vec4(0.0,0.5,1.0,2.0);
    vec3 i=floor(v+dot(v,C.yyy));
    vec3 x0=v-i+dot(i,C.xxx);
    vec3 g=step(x0.yzx,x0.xyz);
    vec3 l=1.0-g;
    vec3 i1=min(g.xyz,l.zxy);
    vec3 i2=max(g.xyz,l.zxy);
    vec3 x1=x0-i1+C.xxx;
    vec3 x2=x0-i2+C.yyy;
    vec3 x3=x0-D.yyy;
    i=mod289(i);
    vec4 p=permute(permute(permute(
      i.z+vec4(0.0,i1.z,i2.z,1.0))
      +i.y+vec4(0.0,i1.y,i2.y,1.0))
      +i.x+vec4(0.0,i1.x,i2.x,1.0));
    float n_=0.142857142857;
    vec3 ns=n_*D.wyz-D.xzx;
    vec4 j=p-49.0*floor(p*ns.z*ns.z);
    vec4 x_=floor(j*ns.z);
    vec4 y_=floor(j-7.0*x_);
    vec4 x=x_*ns.x+ns.yyyy;
    vec4 y=y_*ns.x+ns.yyyy;
    vec4 h=1.0-abs(x)-abs(y);
    vec4 b0=vec4(x.xy,y.xy);
    vec4 b1=vec4(x.zw,y.zw);
    vec4 s0=floor(b0)*2.0+1.0;
    vec4 s1=floor(b1)*2.0+1.0;
    vec4 sh=-step(h,vec4(0.0));
    vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;
    vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
    vec3 p0=vec3(a0.xy,h.x);
    vec3 p1=vec3(a0.zw,h.y);
    vec3 p2=vec3(a1.xy,h.z);
    vec3 p3=vec3(a1.zw,h.w);
    vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
    p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
    vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);
    m=m*m;
    return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
  }
  // Curl Noise：无散度流体涡旋场
  vec3 curlNoise(vec3 p){
    float e=0.1;
    vec3 dx=vec3(e,0.0,0.0);
    vec3 dy=vec3(0.0,e,0.0);
    vec3 dz=vec3(0.0,0.0,e);
    float p_x0=snoise(p-dx); float p_x1=snoise(p+dx);
    float p_y0=snoise(p-dy); float p_y1=snoise(p+dy);
    float p_z0=snoise(p-dz); float p_z1=snoise(p+dz);
    float x=p_y1-p_y0;
    float y=p_z1-p_z0;
    float z=p_x1-p_x0;
    return vec3(x,y,z);
  }
`;

// ============ fbm GLSL（用于 A+ 行星 Fragment Shader） ============
export const fbmGLSL = `float fbm(vec3 p){float a=0.5;float r=0.0;for(int i=0;i<4;i++){r+=a*snoise(p);p*=2.02;a*=0.5;}return r;}`;

// ============ LEGACY 球体 Vertex Shader ============
export const SPHERE_VS = `
  uniform float uTime;
  uniform float uNoiseAmp;
  uniform float uNoiseFreq;
  uniform vec2 uMouse;
  varying vec3 vNormal;
  varying vec3 vViewDir;
  varying vec3 vWorldPos;
  varying float vDisplacement;
  varying vec3 vOriginalPos;
  varying vec3 vReflect;

  ${GLSL_NOISE}

  void main(){
    vec3 pos = position;
    vOriginalPos = pos;

    float n1 = snoise(pos * uNoiseFreq + vec3(uTime*0.25, uTime*0.18, uTime*0.22));
    float n2 = snoise(pos * uNoiseFreq * 2.3 + vec3(-uTime*0.35, uTime*0.3, -uTime*0.28));
    float n3 = snoise(pos * uNoiseFreq * 4.8 + vec3(uTime*0.5, -uTime*0.4, uTime*0.45));
    vec3 curl = curlNoise(pos * uNoiseFreq * 0.8 + vec3(uTime*0.15));

    float mouseInfluence = length(uMouse) * 0.4;
    float mouseDisp = snoise(pos * 3.0 + uTime * 0.5) * mouseInfluence;

    float displacement = (n1 * 0.55 + n2 * 0.3 + n3 * 0.1 + curl.x * 0.25 + mouseDisp * 0.1) * uNoiseAmp;
    vDisplacement = displacement;
    pos += normal * displacement;

    vec4 worldPos = modelMatrix * vec4(pos, 1.0);
    vWorldPos = worldPos.xyz;
    vNormal = normalize(normalMatrix * normal);
    vViewDir = normalize(cameraPosition - worldPos.xyz);
    vReflect = reflect(-vViewDir, vNormal);
    gl_Position = projectionMatrix * viewMatrix * worldPos;
  }
`;

export const SPHERE_FS = `
  uniform float uTime;
  uniform vec3 uColorA;
  uniform vec3 uColorB;
  uniform vec3 uColorC;
  uniform vec3 uColorD;
  uniform float uFresnel;
  uniform float uFresnelPow;
  varying vec3 vNormal;
  varying vec3 vViewDir;
  varying vec3 vWorldPos;
  varying float vDisplacement;
  varying vec3 vOriginalPos;
  varying vec3 vReflect;

  void main(){
    vec3 N = normalize(vNormal);
    vec3 V = normalize(vViewDir);
    float NdotV = max(dot(N, V), 0.0);

    float fres = pow(1.0 - NdotV, uFresnelPow) * uFresnel;
    vec3 envColor = mix(uColorC, uColorB, 0.5 + 0.5 * sin(vReflect.x * 2.0 + uTime * 0.3));
    envColor = mix(envColor, uColorD, 0.3 * (1.0 - NdotV));

    float dispNorm = vDisplacement * 2.5 + 0.5;
    float t = uTime * 0.2;
    vec3 c1 = mix(uColorA, uColorB, 0.5 + 0.5 * sin(t + vOriginalPos.y * 0.5));
    vec3 c2 = mix(uColorC, uColorD, 0.5 + 0.5 * cos(t * 1.3 + vOriginalPos.x * 0.5));
    vec3 base = mix(c1, c2, dispNorm);

    vec3 lightDir1 = normalize(vec3(4.0, 4.0, 4.0) - vWorldPos);
    vec3 lightDir2 = normalize(vec3(-5.0, -3.0, 3.0) - vWorldPos);
    float diff1 = max(dot(N, lightDir1), 0.0);
    float diff2 = max(dot(N, lightDir2), 0.0);
    vec3 diffuse = base * (0.3 + diff1 * 0.5 + diff2 * 0.3);

    vec3 H1 = normalize(V + lightDir1);
    float spec1 = pow(max(dot(N, H1), 0.0), 80.0);
    vec3 H2 = normalize(V + lightDir2);
    float spec2 = pow(max(dot(N, H2), 0.0), 48.0);

    float inner = pow(abs(vDisplacement) * 2.5, 2.0);
    vec3 innerGlow = uColorD * inner * 0.5;
    vec3 metalReflect = envColor * (0.4 + fres * 0.8);

    vec3 color = diffuse * 0.6;
    color += metalReflect;
    color += uColorC * spec1 * 2.5;
    color += uColorB * spec2 * 1.5;
    color += uColorB * fres * 1.8;
    color += innerGlow;
    color += base * 0.15;

    color = pow(color, vec3(0.92));
    color *= 1.3;
    gl_FragColor = vec4(color, 1.0);
  }
`;

// ============ A+ 行星 Vertex Shader ============
export const APLUS_PLANET_VS = `
  uniform float uTime;
  uniform float uNoiseAmp;
  uniform float uMouseStrength;
  uniform vec3  uMouseHit;
  uniform mat4  uModelMat;
  varying vec3  vWorldPos;
  varying vec3  vNormalW;
  varying vec3  vOriginalPos;
  varying float vDisplacement;

  float planetHitDist(vec3 p, vec3 hit) {
    float c = dot(normalize(p), normalize(hit));
    return acos(clamp(c, -0.999, 0.999));
  }
  void main() {
    vOriginalPos = position;
    vec3 p = position;
    float t = uTime;
    float n1 = snoise(p * 1.0 + t*vec3(0.25,0.18,0.22)) * 0.55;
    float n2 = snoise(p * 2.3 - t*vec3(0.35,0.30,0.28)) * 0.30;
    float n3 = snoise(p * 4.8 + t*vec3(0.50,0.40,0.45)) * 0.10;
    float n4 = curlNoise(p * 0.8 + t*0.15).x * 0.25;
    float dispBase = n1+n2+n3+n4;
    vec3 hitLocal = normalize(mat3(inverse(uModelMat)) * uMouseHit) * 1.9;
    float ang = planetHitDist(p, hitLocal);
    float mouseK = smoothstep(1.4, 0.0, ang) * uMouseStrength * 0.55;
    float disp = dispBase * uNoiseAmp + mouseK;
    vDisplacement = disp;
    vec3 displaced = p + normal * disp;
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

export const APLUS_PLANET_FS = `
  precision highp float;
  varying vec3 vWorldPos;
  varying vec3 vNormalW;
  varying vec3 vOriginalPos;
  varying float vDisplacement;
  uniform vec3 uCamPos;
  uniform float uTime;

  const vec3 C_DEEP  = vec3(0.016,0.032,0.068);
  const vec3 C_MID   = vec3(0.035,0.092,0.170);
  const vec3 C_LIT   = vec3(0.00,0.72,0.88);
  const vec3 C_SSS   = vec3(0.75,0.22,0.62);
  const vec3 C_PEAK  = vec3(1.00,0.76,0.15);
  const vec3 C_RIM   = vec3(0.00,0.54,0.70);
  const vec3 C_SPEC  = vec3(1.00,0.82,0.25);

  float D_GGX(float NoH, float a){
    float a2 = a*a;
    float f = (NoH*a2 - NoH)*NoH + 1.0;
    return a2 / (3.14159265 * f*f + 1e-5);
  }
  vec3 F_Schlick(float VoH, vec3 F0){
    return F0 + (1.0 - F0) * pow(clamp(1.0 - VoH, 0.0, 1.0), 5.0);
  }

  const mat3 GRADING_MAT = mat3(
    1.04, -0.03,  0.02,
   -0.02,  1.03,  0.04,
   -0.02,  0.04,  1.07
  );

  float sdBox(vec2 p, vec2 b){ vec2 d = abs(p)-b; return length(max(d,0.0)) + min(max(d.x,d.y),0.0); }
  float nfLogoMask(vec3 p, vec3 camDir){
    vec3 N = normalize(p);
    vec3 right = normalize(cross(vec3(0.0,1.0,0.001), camDir));
    vec3 up    = cross(camDir, right);
    vec2 uv = vec2(dot(N, right), dot(N, up));
    float rRegion = 0.32;
    if (length(uv) > rRegion) return 0.0;
    float mask = 0.0;
    float barN_L = 1.0 - smoothstep(0.012, 0.022, abs(uv.x + 0.13));
    float barN_R = 1.0 - smoothstep(0.012, 0.022, abs(uv.x - 0.05));
    float barN_D = 1.0 - smoothstep(0.012, 0.024, abs((uv.y-0.04) - 0.90*(uv.x+0.13)));
    float barN_Y = (1.0 - smoothstep(0.14, 0.16, abs(uv.y+0.04)));
    mask += 0.55 * max(max(barN_L*barN_Y, barN_R*barN_Y), barN_D * (1.0-smoothstep(0.16,0.20,abs(uv.y+0.04))));
    float barF_V = 1.0 - smoothstep(0.012, 0.022, abs(uv.x - 0.10));
    float barF_H1 = 1.0 - smoothstep(0.010, 0.018, abs(uv.y - 0.08));
    float barF_H2 = 1.0 - smoothstep(0.010, 0.018, abs(uv.y + 0.00));
    float barF_X1 = (1.0 - smoothstep(0.06, 0.11, abs(uv.x - 0.13)));
    float barF_X2 = (1.0 - smoothstep(0.04, 0.09, abs(uv.x - 0.12)));
    mask += 0.55 * max(barF_V * (1.0-smoothstep(0.15,0.18,abs(uv.y+0.04))),
                 max(barF_H1*barF_X1, barF_H2*barF_X2));
    vec2 p2 = uv - vec2(0.19, -0.02);
    float psiL = 1.0 - smoothstep(0.012, 0.022, abs(uv.x + 0.18));
    float psiR = 1.0 - smoothstep(0.012, 0.022, abs(uv.x - 0.20));
    float psiY1 = (1.0 - smoothstep(0.06, 0.10, abs(uv.y - 0.06)));
    float psiT = 1.0 - smoothstep(0.010, 0.020, abs(uv.y + 0.10));
    float psiTX = 1.0 - smoothstep(0.01, 0.05, abs(uv.x - 0.01));
    mask += 0.50 * (max(psiL*psiY1, psiR*psiY1) + psiT * psiTX * 0.6);
    mask *= smoothstep(rRegion, rRegion*0.70, length(uv));
    return clamp(mask, 0.0, 1.0);
  }

  void main() {
    vec3 N = normalize(vNormalW);
    vec3 N_orig = normalize(vOriginalPos);
    float nn0_L = snoise(vOriginalPos * 3.2 + uTime*0.35);
    float nnx_L = snoise(vOriginalPos * 3.2 + vec3(0.012,0,0) + uTime*0.35);
    float nny_L = snoise(vOriginalPos * 3.2 + vec3(0,0.012,0) + uTime*0.35);
    float nnz_L = snoise(vOriginalPos * 3.2 + vec3(0,0,0.012) + uTime*0.35);
    vec3 perPixN_L = vec3(nnx_L - nn0_L, nny_L - nn0_L, nnz_L - nn0_L) * 42.0;
    float nn0_H = snoise(vOriginalPos * 11.5 + uTime*0.90);
    float nnx_H = snoise(vOriginalPos * 11.5 + vec3(0.008,0,0) + uTime*0.90);
    float nny_H = snoise(vOriginalPos * 11.5 + vec3(0,0.008,0) + uTime*0.90);
    float nnz_H = snoise(vOriginalPos * 11.5 + vec3(0,0,0.008) + uTime*0.90);
    vec3 perPixN_H = vec3(nnx_H - nn0_H, nny_H - nn0_H, nnz_H - nn0_H) * 42.0;
    N = normalize(N + perPixN_L * 0.30 + perPixN_H * 0.22);

    vec3 V = normalize(uCamPos - vWorldPos);
    float NdotV = max(dot(N, V), 0.0);
    float depth = length(vOriginalPos) / 1.9;
    vec3 camDir = normalize(uCamPos);

    vec3 baseCol = mix(C_DEEP, C_MID, smoothstep(0.3, 1.0, depth));
    baseCol = mix(baseCol, C_LIT, smoothstep(0.5, 1.0, 1.0-depth+0.3));
    float dispK = smoothstep(-0.15, 0.35, vDisplacement);
    baseCol *= mix(0.80, 1.20, dispK);

    vec3 L_key = normalize(vec3(4.0, 3.5, 5.0) - vWorldPos);
    vec3 L_fil = normalize(vec3(-4.0, -2.0, 4.0) - vWorldPos);
    vec3 L_rim = normalize(vec3(-2.0, 2.0, -5.0) - vWorldPos);

    float NdotL_key = max(dot(N, L_key), 0.0);
    float NdotL_fil = max(dot(N, L_fil), 0.0);
    float NdotL_rim = max(dot(N, L_rim), 0.0);

    vec3 diffuse = baseCol * (0.065 + NdotL_key*0.42 + NdotL_fil*0.09);
    diffuse += C_SSS * NdotL_rim * 0.022;

    vec3 H_key = normalize(L_key + V);
    float NoH_key = max(dot(N, H_key), 0.0);
    float VoH_key = max(dot(V, H_key), 0.0);
    float D_key = D_GGX(NoH_key, 0.32);
    vec3  F_key = F_Schlick(VoH_key, C_SPEC * 0.45);
    float specKey = D_key * 0.15;
    float specBoost_K = 1.0 + smoothstep(0.05, 0.30, vDisplacement) * 0.35;

    vec3 H_fil = normalize(L_fil + V);
    float NoH_fil = max(dot(N, H_fil), 0.0);
    float VoH_fil = max(dot(V, H_fil), 0.0);
    float D_fil = D_GGX(NoH_fil, 0.50);
    vec3  F_fil = F_Schlick(VoH_fil, C_LIT * 0.22);
    float specFil = D_fil * 0.15 * 0.22;

    float microScratch = fbm(vWorldPos * 220.0 + uTime*0.24) * 2.0 - 1.0;
    float scratchK   = smoothstep(0.02, 0.28, vDisplacement) * clamp(microScratch*0.65 + 0.35, 0.0, 1.0);
    vec3 specTint    = mix(C_SPEC, vec3(1.0), scratchK);

    vec3  F0_liquid = vec3(0.16, 0.14, 0.20);
    vec3  fresSchl  = F_Schlick(NdotV, F0_liquid);
    float fresRimW  = clamp(fresSchl.r + fresSchl.g + fresSchl.b, 0.0, 1.0);
    float fresRim   = fresRimW * 0.26;
    float fresInner = pow(1.0 - NdotV, 7.5) * 0.13;

    float dispBoost = smoothstep(0.08, 0.30, vDisplacement);
    vec3 peakTint = C_PEAK * dispBoost * 0.24;

    float sss = exp(-length(vOriginalPos)*0.75) *
                (0.30 + 0.28*sin(uTime*0.60 + fbm(vOriginalPos*2.0 + vec3(3.1,1.7,0.3))*0.8));
    vec3 sssCol = C_SSS * sss * 0.075;

    vec3 R = reflect(-V, N);
    float e1 = fbm(R*1.5 + uTime*0.08);
    float e2 = fbm(R*3.0);
    vec3 envCol = mix(C_DEEP, C_MID, 0.4+0.6*e1);
    envCol = mix(envCol, C_LIT, smoothstep(0.6, 0.95, e2));

    float NdotCam = dot(normalize(vOriginalPos), normalize(uCamPos));
    float logoK = smoothstep(0.50, 0.72, NdotCam) * (0.55 + 0.45*clamp(1.0 - vDisplacement*2.5, 0.0, 1.0));
    vec3 logoCol = C_PEAK * nfLogoMask(vOriginalPos, normalize(uCamPos)) * logoK * 0.11;

    float backAng = -dot(N, L_key);
    float fCont = fbm(vOriginalPos*6.0 + vec3(1.2, 0.3, 2.1));
    float backLine = smoothstep(0.26, 0.08, backAng) * smoothstep(0.72, 0.48, abs(fCont));
    vec3 backContourCol = C_RIM * backLine * 0.055;

    vec3 color = diffuse;
    color += specTint * F_key * specKey * 0.42 * specBoost_K;
    color += C_LIT   * F_fil * specFil * 0.18;
    color += C_RIM   * fresRim * 0.065;
    color += C_LIT   * fresInner * 0.04;
    color += envCol  * fresRim * 0.04;
    color += peakTint * 0.38;
    color += sssCol;
    color += logoCol;
    color += backContourCol;
    color += baseCol * 0.012;

    color = GRADING_MAT * color;
    color = max(color, vec3(0.03));
    const float a = 2.51;
    const float b = 0.03;
    const float c = 2.43;
    const float d = 0.59;
    const float e = 0.14;
    color = clamp((color * (a * color + b)) / (color * (c * color + d) + e), 0.0, 1.0);
    color = (color - 0.5) * 1.04 + 0.5;
    color = clamp(color, 0.0, 1.0);
    gl_FragColor = vec4(color, 1.0);
  }
`;

// ============ A+ Halo Shaders ============
export const APLUS_HALO_VS = `varying vec3 vN; varying vec3 vP; void main(){ vN = normalize(normalMatrix*normal); vec4 mv = modelViewMatrix*vec4(position,1.0); vP = -mv.xyz; gl_Position = projectionMatrix*mv; }`;

export const APLUS_HALO_FS = `
  precision highp float;
  varying vec3 vN; varying vec3 vP;
  uniform vec3  uColor;
  uniform float uAlpha;
  uniform float uFresPow;
  uniform float uPulseK;
  void main() {
    vec3 v = normalize(vP);
    float NdotV = abs(dot(normalize(vN), v));
    float f = pow(1.0 - NdotV, uFresPow);
    float a = f * uAlpha * uPulseK;
    gl_FragColor = vec4(uColor * f, a);
  }
`;

// ============ LEGACY Halo Shader ============
export const LEGACY_HALO_VS = `
  varying vec3 vNormal; varying vec3 vViewDir;
  void main(){
    vNormal = normalize(normalMatrix * normal);
    vec4 wp = modelMatrix * vec4(position, 1.0);
    vViewDir = normalize(cameraPosition - wp.xyz);
    gl_Position = projectionMatrix * viewMatrix * wp;
  }
`;

export const LEGACY_HALO_FS = `
  varying vec3 vNormal; varying vec3 vViewDir; uniform float uTime;
  void main(){
    float f = pow(1.0 - max(dot(vNormal, vViewDir), 0.0), 3.0);
    vec3 c = mix(vec3(0.4, 0.2, 1.0), vec3(0.1, 0.8, 1.0), 0.5 + 0.5 * sin(uTime));
    gl_FragColor = vec4(c * f * 1.5, f * 0.6);
  }
`;

// ============ God Rays Shaders ============
export const GOD_RAY_VS = `
  varying vec2 vUv;
  void main(){
    vUv = uv;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * mv;
  }
`;

export const GOD_RAY_FS = `
  precision highp float;
  varying vec2 vUv;
  uniform float uTime;
  uniform float uStrength;

  float fbmG(vec3 p){
    float a=0.5, r=0.0;
    float n1 = sin(p.x*1.2 + p.y*0.8)*0.5 + sin(p.x*2.5 - p.z*1.1)*0.25 + sin(p.y*1.7 + p.z*1.3)*0.25;
    float n2 = sin(p.x*2.7 - p.y*1.9 + 1.7)*0.4 + sin(p.z*2.3 + p.x*1.4)*0.3 + sin(p.y*3.1 - 0.9)*0.3;
    float n3 = sin(p.x*5.3 + p.y*4.1 - 2.3)*0.3 + sin(p.z*5.7 + 0.7)*0.35;
    return clamp(0.5 + 0.5*(n1 + n2*0.6 + n3*0.35), 0.0, 1.0);
  }

  float expImpulseG(float x, float k){ float h = k*x; return h*exp(1.0-h); }

  void main(){
    vec2 uv = vUv;
    vec2 axis = normalize(vec2(0.900, -0.435));
    vec2 toP = uv - vec2(0.5) - vec2(0.382 - 0.5, 0.382 - 0.5);
    float distFromPlanet = length(toP + vec2(-0.118, -0.118));
    float projAxis = dot(normalize(toP + vec2(1e-4)), axis);
    float axisK = pow(clamp(projAxis, 0.0, 1.0), 2.8);
    float radialK = expImpulseG(clamp(distFromPlanet * 1.55, 0.0, 1.4), 2.6);

    vec3 np = vec3(uv * 3.2 + axis * (uTime * 0.60), uTime * 0.25);
    float tex = fbmG(np);

    vec3 C_GOLD = vec3(1.00, 0.78, 0.28);
    vec3 C_MAG  = vec3(0.86, 0.28, 0.94);
    vec3 col = mix(C_GOLD, C_MAG, 0.12 + axisK * 0.18);

    float energy = axisK * radialK * (0.55 + 0.45*tex) * (0.78 + 0.22 * sin(uTime*0.80));
    float alpha = clamp(energy * 0.022 * uStrength, 0.0, 0.022);
    col *= energy;
    gl_FragColor = vec4(col, alpha);
  }
`;

// ============ Flow Particle Shaders ============
export const FLOW_PARTICLE_VS = `
  attribute float aSeed;
  attribute vec3 aColor;
  attribute float aSize;
  attribute float aSpeed;
  uniform float uTime;
  uniform float uPixelRatio;
  uniform vec2 uMouse;
  uniform float uFlowScale;
  varying vec3 vColor;
  varying float vAlpha;
  varying float vDepth;

  ${GLSL_NOISE}

  float expImpulseV( float x, float k ){ float h = k*x; return h*exp(1.0-h); }

  void main(){
    vColor = aColor;
    float t = uTime * 0.15 * aSpeed;
    vec3 p = position;
    vec3 flow = curlNoise(p * 0.35 + vec3(t, t*0.7, t*1.1));
    float r = length(p);
    vec3 toCenter = -normalize(p) * 0.02 * (r - 4.0);
    float orbitAngle = t * 0.3 + aSeed * 6.28;
    vec3 orbitOffset = vec3(
      sin(orbitAngle) * 0.15,
      cos(orbitAngle * 0.7) * 0.1,
      sin(orbitAngle * 1.3) * 0.12
    );
    vec3 newPos = p + flow * uFlowScale + toCenter + orbitOffset;
    vec2 mouseInfluence = uMouse * 0.3;
    newPos.xz += mouseInfluence * (1.0 / (r + 0.5));
    vAlpha = smoothstep(15.0, 3.0, r) * (0.6 + 0.4 * sin(uTime * 2.0 + aSeed * 10.0));
    vec4 mvPosition = modelViewMatrix * vec4(newPos, 1.0);
    vDepth = -mvPosition.z;
    float age = fract(uTime * 0.22 + aSeed * 37.0);
    float lifeK = expImpulseV(age + 0.10, 3.2);
    gl_PointSize = aSize * uPixelRatio * (200.0 / -mvPosition.z) * lifeK
                 * (1.0 + 0.28 * sin(uTime * 3.0 + aSeed));
    gl_Position = projectionMatrix * mvPosition;
  }
`;

export const FLOW_PARTICLE_FS = `
  varying vec3 vColor;
  varying float vAlpha;
  varying float vDepth;
  float expImpulse( float x, float k ){ float h = k*x; return h*exp(1.0-h); }
  void main(){
    vec2 center = gl_PointCoord - 0.5;
    float dist = length(center);
    if (dist > 0.5) discard;
    float edge = smoothstep(0.5, 0.18, dist);
    float core = expImpulse(dist * 2.0, 4.2);
    float depthFactor = smoothstep(15.0, 3.5, vDepth);
    float alpha = edge * (0.28 + core * 0.72) * depthFactor;
    vec3 color = vColor * (0.22 + core * 2.0) * (0.45 + depthFactor * 0.55);
    float finalAlpha = min(alpha * vAlpha * 0.36, 0.38);
    gl_FragColor = vec4(color, finalAlpha);
  }
`;

// ============ Ring Particle Shaders ============
export const RING_PARTICLE_VS = `
  attribute vec3 aColor;
  attribute float aSize;
  attribute float aAngle;
  attribute float aRadius;
  uniform float uTime;
  uniform float uPixelRatio;
  varying vec3 vColor;
  varying float vAlpha;
  void main(){
    vColor = aColor;
    float omega = 0.4 / pow(aRadius, 1.2);
    float a = aAngle + uTime * omega;
    vec3 p = vec3(aRadius * cos(a), position.y, aRadius * sin(a));
    vAlpha = 0.6 + 0.4 * sin(uTime * 1.5 + aAngle * 3.0);
    vec4 mvPosition = modelViewMatrix * vec4(p, 1.0);
    gl_PointSize = aSize * uPixelRatio * (300.0 / -mvPosition.z);
    gl_Position = projectionMatrix * mvPosition;
  }
`;

// ============ Ping-Pong Simulation Shaders ============
export const FS_POS_UPDATE = `
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
    vec3 c = curlNoise(p * uCurl + uTime*0.18 + seed);
    v += c * uBaseSpeed;
    vec3 dm = p - uMouseWorld;
    float d = length(dm) + 1e-5;
    float k = smoothstep(uMouseRepelR, 0.0, d) * uRepelK * uMouseStrength;
    v += normalize(dm) * k * 1.5;
    if (uPulseRadius > 0.0) {
      vec3 dp = p - uPulseOrigin;
      float rr = length(dp);
      float onRing = smoothstep(uPulseRadius+uPulseThick, uPulseRadius, rr)
                   * smoothstep(uPulseRadius-uPulseThick, uPulseRadius, rr);
      v += normalize(dp + 1e-5) * onRing * 1.6;
    }
    v *= uDamping;
    life -= uDT*0.08;
    p += v * uDT;
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

export const FS_VEL_COPY = `
  varying vec2 vUv;
  uniform sampler2D tPos, tVel;
  uniform float uDT, uDamping;
  uniform float uRIn, uROut;
  void main() {
    vec4 P = texture2D(tPos, vUv);
    vec4 V = texture2D(tVel, vUv);
    vec3 v = V.xyz * uDamping;
    float life = V.w;
    float r = length(P.xyz);
    if (r > uROut || r < uRIn) { v = vec3(0.0); life = 1.0; }
    gl_FragColor = vec4(v, life);
  }
`;

// ============ Shockwave Shader ============
export const SHOCKWAVE_VS = `varying vec3 vP; varying vec2 vUv; void main(){ vUv=uv; vP=position; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }`;

export const SHOCKWAVE_FS = `varying vec2 vUv; varying vec3 vP; uniform vec3 uColor; uniform float uWidth, uT, uK;
  void main(){
    float d = abs(length(vP.xy) - mix(0.975, 1.025, uT));
    float ring = smoothstep(uWidth, 0.0, d);
    float a = ring * uK;
    if (a<0.01) discard;
    gl_FragColor = vec4(uColor, a);
  }`;

// ============ Quantum Noise (inline snoise for self-contained shader) ============
export const Q_NOISE = `
  vec3 mod289(vec3 x){ return x - floor(x * (1.0/289.0)) * 289.0; }
  vec4 mod289(vec4 x){ return x - floor(x * (1.0/289.0)) * 289.0; }
  vec4 permute(vec4 x){ return mod289(((x*34.0)+1.0)*x); }
  vec4 taylorInvSqrt(vec4 r){ return 1.79284291400159 - 0.85373472095314 * r; }
  float snoise(vec3 v){
    const vec2 C = vec2(1.0/6.0, 1.0/3.0);
    const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
    vec3 i = floor(v + dot(v, C.yyy));
    vec3 x0 = v - i + dot(i, C.xxx);
    vec3 g = step(x0.yzx, x0.xyz);
    vec3 l = 1.0 - g;
    vec3 i1 = min(g.xyz, l.zxy);
    vec3 i2 = max(g.xyz, l.zxy);
    vec3 x1 = x0 - i1 + C.xxx;
    vec3 x2 = x0 - i2 + C.yyy;
    vec3 x3 = x0 - D.yyy;
    i = mod289(i);
    vec4 p = permute( permute( permute(
               i.z + vec4(0.0, i1.z, i2.z, 1.0))
             + i.y + vec4(0.0, i1.y, i2.y, 1.0))
             + i.x + vec4(0.0, i1.x, i2.x, 1.0));
    float n_ = 0.142857142857;
    vec3 ns = n_ * D.wyz - D.xzx;
    vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
    vec4 x_ = floor(j * ns.z);
    vec4 y_ = floor(j - 7.0 * x_);
    vec4 x = x_ *ns.x + ns.yyyy;
    vec4 y = y_ *ns.x + ns.yyyy;
    vec4 h = 1.0 - abs(x) - abs(y);
    vec4 b0 = vec4(x.xy, y.xy);
    vec4 b1 = vec4(x.zw, y.zw);
    vec4 s0 = floor(b0)*2.0 + 1.0;
    vec4 s1 = floor(b1)*2.0 + 1.0;
    vec4 sh = -step(h, vec4(0.0));
    vec4 a0 = b0.xzyw + s0.xzyw*sh.xxyy;
    vec4 a1 = b1.xzyw + s1.xzyw*sh.zzww;
    vec3 p0 = vec3(a0.xy, h.x);
    vec3 p1 = vec3(a0.zw, h.y);
    vec3 p2 = vec3(a1.xy, h.z);
    vec3 p3 = vec3(a1.zw, h.w);
    vec4 norm = taylorInvSqrt(vec4(dot(p0,p0), dot(p1,p1), dot(p2,p2), dot(p3,p3)));
    p0 *= norm.x; p1 *= norm.y; p2 *= norm.z; p3 *= norm.w;
    vec4 m = max(0.6 - vec4(dot(x0,x0), dot(x1,x1), dot(x2,x2), dot(x3,x3)), 0.0);
    m = m * m;
    return 42.0 * dot(m*m, vec4(dot(p0,x0), dot(p1,x1), dot(p2,x2), dot(p3,x3)));
  }`;

// ============ Quantum Field Shaders ============
export const QUANTUM_VS = `
  varying vec3 vNormalW;
  varying vec3 vPosW;
  void main(){
    vec4 wp = modelMatrix * vec4(position, 1.0);
    vPosW = wp.xyz;
    vNormalW = normalize(mat3(modelMatrix) * normal);
    gl_Position = projectionMatrix * viewMatrix * wp;
  }`;

export const QUANTUM_FS = `
  varying vec3 vNormalW; varying vec3 vPosW;
  uniform vec3 uColorA; uniform vec3 uColorB; uniform vec3 uColorC;
  uniform float uTime; uniform float uMouse;
  void main(){
    vec3 V = normalize(cameraPosition - vPosW);
    float NdotV = dot(normalize(vNormalW), V);
    float fres = pow(1.0 - abs(NdotV), 3.0);
    vec3 P = vPosW * 0.6 + vec3(uTime*0.2, uTime*0.15, -uTime*0.1);
    float n1 = snoise(P*1.2) * 0.5 + 0.5;
    float n2 = snoise(P*2.4 + 5.0) * 0.5 + 0.5;
    float n3 = snoise(P*0.8 + 10.0) * 0.5 + 0.5;
    float pattern = n1*0.5 + n2*0.3 + n3*0.2;
    float bands = sin(pattern * 12.0 + uTime*2.0) * 0.5 + 0.5;
    vec3 col = mix(uColorA, uColorB, smoothstep(0.35, 0.65, pattern));
    col = mix(col, uColorC, smoothstep(0.6, 0.9, bands) * 0.4);
    float mouseBoost = 1.0 + uMouse * 0.6;
    float alpha = fres * 0.30 + pattern * 0.04;
    alpha = min(alpha * mouseBoost, 0.45);
    gl_FragColor = vec4(col * (0.2 + fres*0.8), alpha);
  }`;

// ============ Quantum Shell Shaders ============
export const QUANTUM_SHELL_VS = `
  varying vec3 vN;
  varying vec3 vP;
  void main(){
    vN = normalize(normalMatrix * normal);
    vP = (modelViewMatrix * vec4(position, 1.0)).xyz;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`;

export const QUANTUM_SHELL_FS = `
  uniform float uTime, uLevel;
  varying vec3 vN; varying vec3 vP;
  void main(){
    float intensity = sin(uTime*1.2 + uLevel*1.5 + vP.y*6.0) * 0.5 + 0.5;
    float fres = pow(1.0 - max(abs(normalize(vN).z), 0.0), 2.0);
    float a = fres * 0.12 + intensity * 0.04;
    a = min(a, 0.18);
    gl_FragColor = vec4(0.0, 0.6, 0.9, a);
  }`;

// ============ Post-Processing Shaders ============
export const GRAIN_SHADER = {
  uniforms: { tDiffuse:{value:null}, uTime:{value:0}, uIntensity:{value:0.006} },
  vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
  fragmentShader: `uniform sampler2D tDiffuse; uniform float uTime, uIntensity; varying vec2 vUv;
    float hash21(vec2 p){ p=fract(p*vec2(123.34,456.21)); p+=dot(p,p+45.32); return fract(p.x*p.y); }
    void main(){
      vec4 c = texture2D(tDiffuse, vUv);
      float n0 = hash21(vUv * vec2(1920.0 * 8.0, 1080.0 * 8.0) + fract(uTime)*47.0) - 0.5;
      float n1 = hash21(vUv * vec2(1920.0 * 8.0 + 13.7, 1080.0 * 8.0 - 7.3) + fract(uTime*1.3)*29.0 + 9.0) - 0.5;
      float n = (n0 + n1) * 0.5;
      float lum = dot(c.rgb, vec3(0.2126,0.7152,0.0722));
      float w = 0.22 + 0.78 * smoothstep(0.0, 0.35, lum);
      vec3 oc = c.rgb * (1.0 + n * uIntensity * w * 1.6);
      gl_FragColor = vec4(oc, c.a);
    }`
};

export const VIGNETTE_SHADER = {
  uniforms: { tDiffuse:{value:null}, offset:{value:1.35}, darkness:{value:0.30} },
  vertexShader: `varying vec2 vUv; void main(){ vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }`,
  fragmentShader: `uniform sampler2D tDiffuse; uniform float offset, darkness; varying vec2 vUv;
    void main(){ vec4 c = texture2D(tDiffuse, vUv);
      vec2 uv2 = (vUv - 0.5) * vec2(offset);
      float d = clamp(dot(uv2,uv2), 0.0, 1.0);
      gl_FragColor = vec4(mix(c.rgb, vec3(1.0 - darkness), d*darkness), c.a); }`,
};

export const CA_SHADER = {
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

// ============ 纹理生成工具 ============
export function makeSharedGlowTex() {
  const S=128; const cv=document.createElement('canvas'); cv.width=S; cv.height=S;
  const g=cv.getContext('2d');
  const rg=g.createRadialGradient(S/2,S/2,0, S/2,S/2,S/2);
  rg.addColorStop(0.0, 'rgba(255,255,255,1)');
  rg.addColorStop(0.20,'rgba(0,229,255,0.75)');
  rg.addColorStop(0.50,'rgba(0,180,220,0.25)');
  rg.addColorStop(1.0, 'rgba(0,50,80,0)');
  g.fillStyle = rg; g.fillRect(0,0,S,S);
  const t = new THREE.CanvasTexture(cv);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export function makeGlowTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  g.addColorStop(0, 'rgba(255,255,255,1)');
  g.addColorStop(0.2, 'rgba(255,255,255,0.6)');
  g.addColorStop(0.5, 'rgba(255,255,255,0.15)');
  g.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(c);
}