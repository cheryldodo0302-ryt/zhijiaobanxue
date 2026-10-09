<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

type Vec3 = [number, number, number]
type SceneApi = {
  destroy(): void
}

const canvasRef = ref<HTMLCanvasElement | null>(null)
const errorMessage = ref('')
let sceneApi: SceneApi | null = null

function createScene(canvas: HTMLCanvasElement, reducedMotion: boolean): SceneApi {
  const context = canvas.getContext('webgl', {
    antialias: true,
    alpha: true,
    depth: true,
    premultipliedAlpha: false,
  }) as WebGLRenderingContext | null

  if (!context) {
    errorMessage.value = '当前设备无法启动 WebGL，请开启浏览器硬件加速。'
    return { destroy() {} }
  }

  const gl = context
  const vertexSource = `
attribute vec3 aPosition; attribute vec3 aNormal; attribute vec3 aColor; attribute vec2 aWind;
uniform mat4 uVP; uniform float uTime; uniform float uWind; uniform float uPoints;
varying vec3 vColor; varying float vLight; varying float vDepth;
void main(){
  vec3 p=aPosition;
  p.x+=sin(uTime*.72+aWind.y)*aWind.x*.010*uWind;
  p.z+=cos(uTime*.52+aWind.y)*aWind.x*.004*uWind;
  vec4 clip=uVP*vec4(p,1.); gl_Position=clip; gl_PointSize=uPoints; vDepth=clip.w;
  vec3 normal=normalize(aNormal);
  vLight=.57+.43*max(0.,dot(normal,normalize(vec3(-.65,1.,.65)))); vColor=aColor;
}`
  const fragmentSource = `
precision mediump float; varying vec3 vColor; varying float vLight; varying float vDepth; uniform float uIsPoint;
void main(){
  if(uIsPoint>.5){vec2 p=gl_PointCoord*2.-1.;if(dot(p,p)>1.)discard;}
  vec3 color=vColor*vLight; float fog=clamp((vDepth-18.)/26.,0.,.30);
  gl_FragColor=vec4(mix(color,vec3(.067,.082,.089),fog),1.);
}`

  function compileShader(type: number, source: string) {
    const shader = gl.createShader(type)
    if (!shader) throw new Error('无法创建 WebGL 着色器')
    gl.shaderSource(shader, source)
    gl.compileShader(shader)
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      throw new Error(gl.getShaderInfoLog(shader) || 'WebGL 着色器编译失败')
    }
    return shader
  }

  let program: WebGLProgram | null = null
  try {
    program = gl.createProgram()
    if (!program) throw new Error('无法创建 WebGL 程序')
    gl.attachShader(program, compileShader(gl.VERTEX_SHADER, vertexSource))
    gl.attachShader(program, compileShader(gl.FRAGMENT_SHADER, fragmentSource))
    gl.linkProgram(program)
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(program) || 'WebGL 程序链接失败')
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'WebGL 初始化失败'
    return { destroy() {} }
  }

  gl.useProgram(program)
  gl.enable(gl.DEPTH_TEST)
  gl.depthFunc(gl.LEQUAL)
  gl.disable(gl.CULL_FACE)
  gl.enable(gl.BLEND)
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA)
  gl.clearColor(0, 0, 0, 0)

  const loc = {
    position: gl.getAttribLocation(program, 'aPosition'),
    normal: gl.getAttribLocation(program, 'aNormal'),
    color: gl.getAttribLocation(program, 'aColor'),
    wind: gl.getAttribLocation(program, 'aWind'),
    vp: gl.getUniformLocation(program, 'uVP'),
    time: gl.getUniformLocation(program, 'uTime'),
    windAmount: gl.getUniformLocation(program, 'uWind'),
    pointSize: gl.getUniformLocation(program, 'uPoints'),
    isPoint: gl.getUniformLocation(program, 'uIsPoint'),
  }

  let seed = 290921
  const random = () => {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0
    return seed / 4294967296
  }
  const color = (hex: number): Vec3 => [((hex >> 16) & 255) / 255, ((hex >> 8) & 255) / 255, (hex & 255) / 255]
  const shade = (value: Vec3, factor = 1): Vec3 => value.map((item) => Math.min(1, item * factor)) as Vec3
  const positions: number[] = []
  const normals: number[] = []
  const colors: number[] = []
  const winds: number[] = []

  function triangle(a: Vec3, b: Vec3, c: Vec3, value: Vec3, windY = 0, phase = 0) {
    const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]]
    const v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]]
    let nx = u[1] * v[2] - u[2] * v[1]
    let ny = u[2] * v[0] - u[0] * v[2]
    let nz = u[0] * v[1] - u[1] * v[0]
    const length = Math.hypot(nx, ny, nz) || 1
    nx /= length
    ny /= length
    nz /= length
    for (const point of [a, b, c]) {
      positions.push(...point)
      normals.push(nx, ny, nz)
      colors.push(...value)
      winds.push(windY, phase)
    }
  }

  function quad(a: Vec3, b: Vec3, c: Vec3, d: Vec3, value: Vec3, wind = 0, phase = 0) {
    triangle(a, b, c, value, wind, phase)
    triangle(a, c, d, value, wind, phase)
  }

  function cylinder(a: Vec3, b: Vec3, r0: number, r1: number, value: Vec3, segments = 7) {
    const direction = [b[0] - a[0], b[1] - a[1], b[2] - a[2]]
    const length = Math.hypot(...direction) || 1
    const axis = direction.map((item) => item / length)
    const temp = Math.abs(axis[1]) > .87 ? [1, 0, 0] : [0, 1, 0]
    let basisU = [axis[1] * temp[2] - axis[2] * temp[1], axis[2] * temp[0] - axis[0] * temp[2], axis[0] * temp[1] - axis[1] * temp[0]]
    const basisLength = Math.hypot(...basisU)
    basisU = basisU.map((item) => item / basisLength)
    const basisV = [axis[1] * basisU[2] - axis[2] * basisU[1], axis[2] * basisU[0] - axis[0] * basisU[2], axis[0] * basisU[1] - axis[1] * basisU[0]]
    for (let index = 0; index < segments; index += 1) {
      const start = index / segments * Math.PI * 2
      const end = (index + 1) / segments * Math.PI * 2
      const offset = (radius: number, angle: number): number[] => basisU.map((item, axisIndex) => radius * (item * Math.cos(angle) + basisV[axisIndex] * Math.sin(angle)))
      const p = offset(r0, start); const P = offset(r0, end); const q = offset(r1, start); const Q = offset(r1, end)
      const aa = a.map((item, axisIndex) => item + p[axisIndex]) as Vec3
      const ab = a.map((item, axisIndex) => item + P[axisIndex]) as Vec3
      const ba = b.map((item, axisIndex) => item + q[axisIndex]) as Vec3
      const bb = b.map((item, axisIndex) => item + Q[axisIndex]) as Vec3
      quad(aa, ab, bb, ba, shade(value, .82 + .2 * Math.cos(start)))
    }
  }

  function leaf(center: Vec3, tangent: number, width: number, length: number, value: Vec3, phase: number, height: number) {
    const [x, y, z] = center
    const dx = Math.cos(tangent); const dz = Math.sin(tangent)
    const sx = -dz; const sz = dx
    const base: Vec3 = [x - dx * length * .47, y - length * .07, z - dz * length * .47]
    const tip: Vec3 = [x + dx * length * .53, y + length * .1, z + dz * length * .53]
    const left: Vec3 = [x + sx * width, y + width * .22, z + sz * width]
    const right: Vec3 = [x - sx * width, y - width * .06, z - sz * width]
    const middle: Vec3 = [x, y + width * .16, z]
    triangle(base, left, middle, shade(value, 1.09), height, phase)
    triangle(left, tip, middle, shade(value, 1.03), height, phase)
    triangle(base, middle, right, shade(value, .84), height, phase)
    triangle(middle, tip, right, shade(value, .94), height, phase)
  }

  function ellipsoid(cx: number, cy: number, cz: number, rx: number, ry: number, rz: number, value: Vec3, segments = 8, rings = 5) {
    for (let ring = 0; ring < rings; ring += 1) {
      const v0 = -Math.PI / 2 + ring / rings * Math.PI
      const v1 = -Math.PI / 2 + (ring + 1) / rings * Math.PI
      for (let segment = 0; segment < segments; segment += 1) {
        const a = segment / segments * Math.PI * 2; const b = (segment + 1) / segments * Math.PI * 2
        const point = (vertical: number, horizontal: number): Vec3 => [cx + rx * Math.cos(vertical) * Math.cos(horizontal), cy + ry * Math.sin(vertical), cz + rz * Math.cos(vertical) * Math.sin(horizontal)]
        quad(point(v0, a), point(v0, b), point(v1, b), point(v1, a), shade(value, .78 + .25 * Math.sin(a + 1)))
      }
    }
  }

  for (let index = 0; index < 760; index += 1) {
    const x = (random() - .5) * 13; const z = (random() - .5) * 9.2
    const palette = [0x78442a, 0xa1532e, 0xbe763d, 0x6e5736, 0x52603d]
    leaf([x, .094, z], random() * Math.PI * 2, .023, .085, color(palette[index % palette.length]), 0, 0)
  }

  const greens = [0x254631, 0x2e5337, 0x406d43, 0x597347, 0x315a3c]
  for (let index = 0; index < 103; index += 1) {
    const x = -5.9 + random() * 5.6; const z = 1.25 + random() * 3.25
    const y = .19 + random() * .32; const radius = .25 + random() * .37; const value = color(greens[index % greens.length])
    ellipsoid(x, y, z, radius * 1.4, radius * .9, radius, value, 7, 4)
    for (let branch = 0; branch < 6; branch += 1) {
      const angle = random() * 6.28
      leaf([x + Math.cos(angle) * radius * .65, y + radius * .45, z + Math.sin(angle) * radius * .65], angle, .055, .18, shade(value, 1.12), 0, 0)
    }
  }

  const layout: Array<[number, number, number, number]> = [
    [-5.1, -3.85, 5.8, 0xd6a155], [-4.25, -2.95, 7.6, 0xe0ae5c], [-3.25, -3.7, 6.2, 0xd49142], [-2.25, -2.8, 8.1, 0xd88f42], [-1.25, -3.85, 6.0, 0xd17a37], [-.15, -2.75, 7.4, 0xc66b33],
    [.95, -3.85, 7.9, 0xc45f2f], [1.95, -2.85, 6.3, 0xb95230], [2.95, -3.7, 7.25, 0xbd5630], [4.0, -2.75, 6.5, 0xd16c36], [5.0, -3.55, 5.85, 0xcc6a34],
    [-4.75, -.95, 5.4, 0x718052], [-3.55, .25, 6.2, 0xa99749], [-2.25, -.75, 5.55, 0xcf813a], [-1.0, .45, 6.55, 0xcb7535], [.45, -.7, 5.35, 0xbc5e2e], [1.85, .38, 6.15, 0xb7502e], [3.1, -.6, 5.45, 0xc45b32], [4.35, .28, 6.0, 0xd07535],
    [-3.0, 1.55, 5.0, 0xb68b40], [-1.4, 1.8, 6.3, 0xcb7437], [.3, 1.55, 5.1, 0xc15d30], [1.95, 1.85, 6.05, 0xb95630], [3.45, 1.55, 4.85, 0xcf6e32],
  ]
  const bark = color(0x493b32); const white = color(0xe5e1d4)
  layout.forEach(([x, z, height, hex], treeIndex) => {
    const trunkTop = height * .82; const phase = treeIndex * .57 + 1
    cylinder([x, .09, z], [x, trunkTop, z], .145, .07, bark, 8)
    cylinder([x, .09, z], [x, .76, z], .157, .136, white, 8)
    const baseColor = color(hex)
    for (let level = 0; level < 14; level += 1) {
      const fraction = level / 13; const y = height * (.34 + .62 * fraction)
      const spread = (1 - fraction * .72) * (1.08 + (treeIndex % 4) * .11); const branches = level < 10 ? 8 : 6
      for (let branch = 0; branch < branches; branch += 1) {
        const angle = branch / branches * Math.PI * 2 + level * .31 + phase; const length = spread * (.72 + random() * .26)
        const start: Vec3 = [x, y, z]; const tip: Vec3 = [x + Math.cos(angle) * length, y + length * (.07 + random() * .07), z + Math.sin(angle) * length]
        cylinder(start, tip, .024, .009, shade(bark, .92), 5)
        const leafCount = 14 + Math.floor(random() * 5)
        for (let index = 2; index < leafCount; index += 1) {
          const fractionAlongBranch = index / (leafCount - 1); const side = index % 2 ? 1 : -1
          const px = x + Math.cos(angle) * length * fractionAlongBranch + Math.cos(angle + Math.PI / 2) * side * .055
          const pz = z + Math.sin(angle) * length * fractionAlongBranch + Math.sin(angle + Math.PI / 2) * side * .055
          const py = y + (tip[1] - y) * fractionAlongBranch + (random() - .5) * .09
          leaf([px, py, pz], angle + side * (.5 + random() * .24), .052 + random() * .018, .21 + random() * .08, shade(baseColor, .82 + random() * .34), phase, py * .65)
        }
      }
    }
    for (let index = 0; index < 72; index += 1) {
      const angle = random() * 6.28; const radius = random() * .46; const py = height * (.91 + random() * .12)
      leaf([x + Math.cos(angle) * radius, py, z + Math.sin(angle) * radius], angle, .055, .25, shade(baseColor, .87 + random() * .24), phase, py * .65)
    }
  })

  function makeBuffer(data: number[], size: number, attribute: number) {
    const buffer = gl.createBuffer()
    if (!buffer) throw new Error('无法创建 WebGL 缓冲区')
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW)
    gl.enableVertexAttribArray(attribute)
    gl.vertexAttribPointer(attribute, size, gl.FLOAT, false, 0, 0)
    return buffer
  }

  const count = positions.length / 3
  const buffers = [makeBuffer(positions, 3, loc.position), makeBuffer(normals, 3, loc.normal), makeBuffer(colors, 3, loc.color), makeBuffer(winds, 2, loc.wind)]
  const setBuffers = (items: WebGLBuffer[]) => {
    const attributes: Array<[number, number]> = [[loc.position, 3], [loc.normal, 3], [loc.color, 3], [loc.wind, 2]]
    items.forEach((buffer, index) => {
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
      gl.enableVertexAttribArray(attributes[index][0])
      gl.vertexAttribPointer(attributes[index][0], attributes[index][1], gl.FLOAT, false, 0, 0)
    })
  }

  const falling = Array.from({ length: 92 }, () => ({ x: (random() - .5) * 11, y: .4 + random() * 7.6, z: (random() - .5) * 8.4, phase: random() * 6.28, speed: .25 + random() * .4 }))
  const particleBuffers = [gl.createBuffer(), gl.createBuffer(), gl.createBuffer(), gl.createBuffer()]
  if (particleBuffers.some((buffer) => !buffer)) throw new Error('无法创建落叶缓冲区')
  const particleCount = falling.length
  const particleBufferList = particleBuffers as WebGLBuffer[]

  function updateParticles(delta: number, time: number) {
    const particlePositions: number[] = []; const particleNormals: number[] = []; const particleColors: number[] = []; const particleWinds: number[] = []
    falling.forEach((item, index) => {
      item.y -= delta * item.speed; item.x += delta * Math.sin(time + item.phase) * .06
      if (item.y < .12) { item.y = 6.5 + random() * 2; item.x = (random() - .5) * 11 }
      particlePositions.push(item.x, item.y, item.z); particleNormals.push(0, 1, 0); particleColors.push(...color(index % 3 ? 0xd98847 : 0xeab366)); particleWinds.push(0, 0)
    })
    ;[particlePositions, particleNormals, particleColors, particleWinds].forEach((data, index) => {
      gl.bindBuffer(gl.ARRAY_BUFFER, particleBufferList[index])
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.DYNAMIC_DRAW)
    })
  }

  function perspective(fovy: number, aspect: number, near: number, far: number) {
    const f = 1 / Math.tan(fovy / 2); const nf = 1 / (near - far)
    return new Float32Array([f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0])
  }

  function lookAt(eye: Vec3, target: Vec3) {
    let z = [eye[0] - target[0], eye[1] - target[1], eye[2] - target[2]]; let length = Math.hypot(...z); z = z.map((item) => item / length)
    let x = [z[2], 0, -z[0]]; length = Math.hypot(...x); x = x.map((item) => item / length)
    const y = [z[1] * x[2] - z[2] * x[1], z[2] * x[0] - z[0] * x[2], z[0] * x[1] - z[1] * x[0]]
    return new Float32Array([x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -(x[0] * eye[0] + x[1] * eye[1] + x[2] * eye[2]), -(y[0] * eye[0] + y[1] * eye[1] + y[2] * eye[2]), -(z[0] * eye[0] + z[1] * eye[1] + z[2] * eye[2]), 1])
  }

  function multiply(a: Float32Array, b: Float32Array) {
    const output = new Float32Array(16)
    for (let column = 0; column < 4; column += 1) for (let row = 0; row < 4; row += 1) for (let index = 0; index < 4; index += 1) output[column * 4 + row] += a[index * 4 + row] * b[column * 4 + index]
    return output
  }

  const state = { angle: 0, pitch: -.34, distance: 16.8, wind: true, leaves: true }
  const rotationAmplitude = 75 * Math.PI / 180
  const rotationSpeed = reducedMotion ? .04 : .12
  let animationFrame = 0; let disposed = false; let previous = 0; const startedAt = performance.now()

  function resize() {
    const ratio = Math.min(window.devicePixelRatio || 1, 2); const width = Math.max(1, canvas.clientWidth); const height = Math.max(1, canvas.clientHeight)
    canvas.width = Math.floor(width * ratio); canvas.height = Math.floor(height * ratio); gl.viewport(0, 0, canvas.width, canvas.height)
  }
  resize()
  window.addEventListener('resize', resize)

  function render(now: number) {
    if (disposed) return
    const delta = Math.min((now - (previous || now)) / 1000, .06); previous = now; const time = (now - startedAt) / 1000
    state.angle = Math.sin(time * rotationSpeed) * rotationAmplitude
    const target: Vec3 = [0, 4.45, 0]; const eye: Vec3 = [Math.sin(state.angle) * Math.cos(state.pitch) * state.distance, target[1] + Math.sin(state.pitch) * state.distance, Math.cos(state.angle) * Math.cos(state.pitch) * state.distance]
    const viewProjection = multiply(perspective(39 * Math.PI / 180, canvas.width / canvas.height, .1, 90), lookAt(eye, target))
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT); gl.uniformMatrix4fv(loc.vp, false, viewProjection); gl.uniform1f(loc.time, time); gl.uniform1f(loc.windAmount, state.wind ? 1 : 0); gl.uniform1f(loc.pointSize, 3.5); gl.uniform1f(loc.isPoint, 0)
    setBuffers(buffers); gl.drawArrays(gl.TRIANGLES, 0, count)
    if (state.leaves) { updateParticles(delta, time); setBuffers(particleBufferList); gl.uniform1f(loc.pointSize, Math.min(6, 3.6 * (window.devicePixelRatio || 1))); gl.uniform1f(loc.isPoint, 1); gl.drawArrays(gl.POINTS, 0, particleCount) }
    animationFrame = requestAnimationFrame(render)
  }
  animationFrame = requestAnimationFrame(render)

  return {
    destroy() {
      disposed = true; cancelAnimationFrame(animationFrame); window.removeEventListener('resize', resize)
      ;[...buffers, ...particleBufferList].forEach((buffer) => gl.deleteBuffer(buffer)); gl.deleteProgram(program)
    },
  }
}

onMounted(() => {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (canvasRef.value) sceneApi = createScene(canvasRef.value, reducedMotion)
})

onBeforeUnmount(() => {
  sceneApi?.destroy()
  sceneApi = null
})
</script>

<template>
  <section class="forest-side" aria-label="秋日水杉林三维场景">
    <div class="forest-word forest-word-back" aria-hidden="true"><p>让课程</p><p class="line-b">知识成为</p><p class="line-c">可靠依据</p></div>
    <canvas ref="canvasRef" aria-label="可拖动旋转的秋日水杉林三维模型" />
    <div class="forest-word forest-word-front" aria-hidden="true"><p>让课程</p><p class="line-b">知识成为</p><p class="line-c">可靠依据</p></div>
    <div v-if="errorMessage" class="forest-error" role="status">{{ errorMessage }}</div>
  </section>
</template>
