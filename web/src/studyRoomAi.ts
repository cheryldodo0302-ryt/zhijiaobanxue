import { FaceLandmarker, FilesetResolver, HandLandmarker, PoseLandmarker } from '@mediapipe/tasks-vision'

export type StudyAiResult = {
  face_ok: boolean
  head_ok: boolean
  head_score: number
  eye_closed: boolean
  person_ok: boolean
  hand_count: number
  hand_confidence: number
  hand_near_face: boolean
  yaw: number
  pitch: number
  roll_deg: number
  score: number
  calibrating: boolean
  camera_available: boolean
}

type Point = { x: number; y: number; z?: number }

const YAW_LIMIT = 0.25
const PITCH_DOWN_LIMIT = 0.30
const PITCH_UP_LIMIT = 0.20
const ROLL_LIMIT_DEG = 20
const YAW_SOFT = 0.40
const PITCH_DOWN_SOFT = 0.45
const PITCH_UP_SOFT = 0.35
const ROLL_SOFT_DEG = 30
const BLINK_THRESHOLD = 0.5
const EAR_CLOSED = 0.18
const HAND_NEAR_FACE_DIST = 1.2

const clamp01 = (value: number) => Math.max(0, Math.min(1, Number(value) || 0))

function distance(a: Point, b: Point) {
  return Math.hypot(a.x - b.x, a.y - b.y)
}

function headPose(face: Point[]) {
  const nose = face[1]
  const leftEye = face[33]
  const rightEye = face[263]
  const eyeDist = Math.max(0.001, distance(leftEye, rightEye))
  return {
    yaw: (nose.x - (leftEye.x + rightEye.x) / 2) / eyeDist,
    pitch: (nose.y - (leftEye.y + rightEye.y) / 2) / eyeDist,
    roll_deg: Math.atan2(rightEye.y - leftEye.y, rightEye.x - leftEye.x) * 180 / Math.PI,
    eyeDist,
  }
}

class Calibrator {
  private readonly duration = 3000
  private readonly minSamples = 15
  private readonly timeout = 15000
  private born = performance.now()
  private started: number | null = null
  private samples: Array<[number, number, number]> = []
  done = false

  reset() {
    this.born = performance.now()
    this.started = null
    this.samples = []
    this.done = false
  }

  add(yaw: number, pitch: number, roll: number, faceOk: boolean, now = performance.now()) {
    if (this.done) return null
    if (now - this.born >= this.timeout) {
      this.done = true
      return 'timeout' as const
    }
    if (!faceOk) {
      this.samples = []
      this.started = null
      return null
    }
    if (this.started === null) this.started = now
    this.samples.push([yaw, pitch, roll])
    if (now - this.started >= this.duration && this.samples.length >= this.minSamples) {
      const median = (index: number) => {
        const values = this.samples.map(sample => sample[index]).sort((a, b) => a - b)
        return values[Math.floor(values.length / 2)]
      }
      this.done = true
      return { yaw: median(0), pitch: median(1), roll: median(2) }
    }
    return null
  }
}

export class BrowserStudyAnalyzer {
  private face: FaceLandmarker | null = null
  private hand: HandLandmarker | null = null
  private pose: PoseLandmarker | null = null
  private calibration = { yaw: 0, pitch: 0, roll: 0 }
  private calibrator = new Calibrator()
  private running = false
  private lastInference = 0
  private frameHandle: number | undefined
  private loading: Promise<void> | null = null

  get isCalibrating() { return !this.calibrator.done }

  async load() {
    if (this.loading) return this.loading
    this.loading = (async () => {
      const vision = await FilesetResolver.forVisionTasks('/mediapipe/wasm')
      const base = (modelAssetPath: string) => ({ modelAssetPath })
      this.face = await FaceLandmarker.createFromOptions(vision, {
        baseOptions: base('/models/face_landmarker.task'), runningMode: 'VIDEO', numFaces: 1,
        minFaceDetectionConfidence: 0.5, minFacePresenceConfidence: 0.5, minTrackingConfidence: 0.5,
        outputFaceBlendshapes: true,
      })
      this.hand = await HandLandmarker.createFromOptions(vision, {
        baseOptions: base('/models/hand_landmarker.task'), runningMode: 'VIDEO', numHands: 2,
        minHandDetectionConfidence: 0.5, minHandPresenceConfidence: 0.5, minTrackingConfidence: 0.5,
      })
      this.pose = await PoseLandmarker.createFromOptions(vision, {
        baseOptions: base('/models/pose_landmarker_lite.task'), runningMode: 'VIDEO', numPoses: 1,
        minPoseDetectionConfidence: 0.5, minPosePresenceConfidence: 0.5, minTrackingConfidence: 0.5,
      })
    })()
    try { await this.loading } catch (error) { this.loading = null; throw error }
  }

  reset() {
    this.calibration = { yaw: 0, pitch: 0, roll: 0 }
    this.calibrator.reset()
    this.lastInference = 0
  }

  private headOk(yaw: number, pitch: number, roll: number) {
    const dyaw = yaw - this.calibration.yaw
    const dpitch = pitch - this.calibration.pitch
    const droll = roll - this.calibration.roll
    return Math.abs(dyaw) < YAW_LIMIT && dpitch < PITCH_DOWN_LIMIT && dpitch > -PITCH_UP_LIMIT && Math.abs(droll) < ROLL_LIMIT_DEG
  }

  private headScore(yaw: number, pitch: number, roll: number) {
    const dyaw = yaw - this.calibration.yaw
    const dpitch = pitch - this.calibration.pitch
    const droll = roll - this.calibration.roll
    return Math.min(clamp01(1 - Math.abs(dyaw) / YAW_SOFT), clamp01(1 - dpitch / PITCH_DOWN_SOFT), clamp01(1 + dpitch / PITCH_UP_SOFT), clamp01(1 - Math.abs(droll) / ROLL_SOFT_DEG))
  }

  private eyeEar(face: Point[], ids: number[]) {
    const p = ids.map(index => face[index])
    const c = Math.max(0.000001, distance(p[0], p[3]))
    return (distance(p[1], p[5]) + distance(p[2], p[4])) / (2 * c)
  }

  private eyesClosed(faceResult: any, face: Point[]) {
    const categories = faceResult?.faceBlendshapes?.[0]?.categories || []
    const left = categories.find((item: any) => item.categoryName === 'eyeBlinkLeft')?.score
    const right = categories.find((item: any) => item.categoryName === 'eyeBlinkRight')?.score
    if (typeof left === 'number' && typeof right === 'number') return left > BLINK_THRESHOLD && right > BLINK_THRESHOLD
    return (this.eyeEar(face, [362, 385, 387, 263, 373, 380]) + this.eyeEar(face, [33, 160, 158, 133, 153, 144])) / 2 < EAR_CLOSED
  }

  private handNearFace(face: Point[] | undefined, hands: Point[][], eyeDist: number) {
    if (!face || !hands.length) return false
    const nose = face[1]
    return hands.some(hand => {
      if (!hand.length) return false
      const center = hand.reduce((sum, point) => ({ x: sum.x + point.x, y: sum.y + point.y }), { x: 0, y: 0 })
      center.x /= hand.length
      center.y /= hand.length
      return distance(center, nose) / Math.max(0.001, eyeDist) < HAND_NEAR_FACE_DIST
    })
  }

  private handConfidence(handResult: any) {
    const handedness = handResult?.handednesses || handResult?.handedness || []
    const scores = handedness.map((items: any[]) => Math.max(...items.map(item => Number(item.score) || 0))).filter((value: number) => Number.isFinite(value))
    return scores.length ? Math.max(...scores) : 0
  }

  private score(faceOk: boolean, headOk: boolean, handCount: number, rawStatus: string, personOk: boolean, headScore: number, handConfidence: number, handNearFace: boolean) {
    if (!personOk || rawStatus === '离开') return 0
    let value = faceOk ? 35 : 0
    if (faceOk) value += 45 * clamp01(headScore)
    if (handCount > 0 && !handNearFace) value += 10 * clamp01(handConfidence)
    else if (faceOk && headOk && !handNearFace) value += 8
    if (handNearFace) value *= 0.70
    if (rawStatus === '注意分心') value *= 0.45
    else if (rawStatus === '疑似睡觉') value *= 0.20
    else if (rawStatus === '检测中') value *= 0.80
    return Math.max(0, Math.min(100, value))
  }

  analyze(video: HTMLVideoElement, timestamp: number): StudyAiResult {
    if (!this.face || !this.hand) throw new Error('AI 模型尚未加载')
    const faceResult: any = this.face.detectForVideo(video, timestamp)
    const handResult: any = this.hand.detectForVideo(video, timestamp)
    const poseResult: any = this.pose?.detectForVideo(video, timestamp)
    const face = (faceResult.faceLandmarks?.[0] || undefined) as Point[] | undefined
    const hands = (handResult.handLandmarks || []) as Point[][]
    const posePresent = Boolean(poseResult?.landmarks?.length || poseResult?.poseLandmarks?.length)
    const faceOk = Boolean(face)
    const pose = face ? headPose(face) : { yaw: 0, pitch: 0, roll_deg: 0, eyeDist: 0.001 }
    const headOk = face ? this.headOk(pose.yaw, pose.pitch, pose.roll_deg) : false
    const headScore = face ? this.headScore(pose.yaw, pose.pitch, pose.roll_deg) : 0
    const eyeClosed = face ? this.eyesClosed(faceResult, face) : false
    const nearFace = this.handNearFace(face, hands, pose.eyeDist)
    const personOk = faceOk || hands.length > 0 || posePresent
    const rawStatus = !faceOk ? (personOk ? '注意分心' : '离开') : eyeClosed ? '疑似睡觉' : nearFace || !headOk ? '注意分心' : '正在学习'
    const confidence = this.handConfidence(handResult)
    const result: StudyAiResult = {
      face_ok: faceOk, head_ok: headOk, head_score: headScore, eye_closed: eyeClosed, person_ok: personOk,
      hand_count: Math.min(2, hands.length), hand_confidence: confidence, hand_near_face: nearFace,
      yaw: pose.yaw, pitch: pose.pitch, roll_deg: pose.roll_deg,
      score: this.score(faceOk, headOk, hands.length, rawStatus, personOk, headScore, confidence, nearFace),
      calibrating: !this.calibrator.done, camera_available: true,
    }
    if (!this.calibrator.done) {
      const calibrationResult = this.calibrator.add(pose.yaw, pose.pitch, pose.roll_deg, faceOk)
      if (calibrationResult === 'timeout') this.calibration = { yaw: 0, pitch: 0, roll: 0 }
      else if (calibrationResult) this.calibration = calibrationResult
      result.calibrating = !this.calibrator.done
    }
    return result
  }

  start(video: HTMLVideoElement, onResult: (result: StudyAiResult) => void, onError: (error: unknown) => void) {
    this.stop()
    this.reset()
    this.running = true
    const tick = (now: number) => {
      if (!this.running) return
      if (now - this.lastInference >= 200 && video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
        this.lastInference = now
        try { onResult(this.analyze(video, Math.max(1, Math.round(now)))) } catch (error) { onError(error) }
      }
      this.frameHandle = window.requestAnimationFrame(tick)
    }
    this.frameHandle = window.requestAnimationFrame(tick)
  }

  stop() {
    this.running = false
    if (this.frameHandle !== undefined) window.cancelAnimationFrame(this.frameHandle)
    this.frameHandle = undefined
  }

  close() {
    this.stop()
    this.face?.close()
    this.hand?.close()
    this.pose?.close()
    this.face = null
    this.hand = null
    this.pose = null
  }
}
