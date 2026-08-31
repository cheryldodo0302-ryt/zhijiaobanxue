# -*- coding: utf-8 -*-
"""
study_judge.py —— AI 自习室统一学习状态判断模块

所有程序（web_app.py / face_ai_final.py / ai_server.py / face_ai_module.py）
都从这里导入判断逻辑，保证全项目只有一份实现。

v2：
1. 低头/仰头（pitch）、歪头（roll），不再只看左右转头
2. 眼睛开合检测，闭眼超时判"疑似睡觉"
3. 人体存在检测（pose 模型优先，画面运动兜底）

v3（第三优先）：
4. 手部信号改语义：手贴脸（玩手机/打电话/托腮）视为分心信号，
   普通手部（打字/写字）只给小幅加分；手部分数按检测置信度加权
5. 置信度/软评分：头部姿态改为连续打分，越接近边界分越低，
   不再是非黑即白
6. 每场学习校准：开始学习时正对摄像头 3 秒，记录个人基准姿态，
   之后按"偏离基准多少"判断，摄像头放侧面也不会误判

包含：
analyze_frame()      单帧 AI 分析
frame_score()        单帧行为评分（0~100）
StudyStateMachine    状态机 + 迟滞
Calibrator           学习开始时的姿态校准
"""

import cv2
import time
import os
import math
import statistics
import mediapipe as mp


# =========================================================
# 0. 项目目录与模型路径
# =========================================================

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

MODEL_DIR = os.path.join(
    BASE_DIR,
    "models"
)

FACE_MODEL = os.path.join(
    MODEL_DIR,
    "face_landmarker.task"
)

HAND_MODEL = os.path.join(
    MODEL_DIR,
    "hand_landmarker.task"
)

# 人体姿态模型（可选，有就加载，没有则用画面运动兜底）
POSE_MODEL_LITE = os.path.join(
    MODEL_DIR,
    "pose_landmarker_lite.task"
)

POSE_MODEL = os.path.join(
    MODEL_DIR,
    "pose_landmarker.task"
)


# =========================================================
# 1. 状态常量
# =========================================================

STATE_STUDYING = "正在学习"
STATE_DISTRACT = "注意分心"
STATE_AWAY = "离开"
STATE_DETECTING = "检测中"
STATE_SLEEP = "疑似睡觉"
STATE_CALIBRATING = "校准中"


# =========================================================
# 2. 模型检查
# =========================================================

for _path, _name in (
    (FACE_MODEL, "人脸模型"),
    (HAND_MODEL, "手部模型"),
):

    if not os.path.exists(_path):

        raise FileNotFoundError(
            "找不到"
            + _name
            + "："
            + _path
        )


# =========================================================
# 3. MediaPipe 基础设置
# =========================================================

BaseOptions = mp.tasks.BaseOptions

VisionRunningMode = (
    mp.tasks.vision.RunningMode
)


# =========================================================
# 4. 加载人脸模型
# =========================================================

FaceLandmarker = (
    mp.tasks.vision.FaceLandmarker
)

FaceLandmarkerOptions = (
    mp.tasks.vision.FaceLandmarkerOptions
)

face_options = FaceLandmarkerOptions(

    base_options=BaseOptions(
        model_asset_path=FACE_MODEL
    ),

    running_mode=(
        VisionRunningMode.VIDEO
    ),

    num_faces=1,

    min_face_detection_confidence=0.5,

    min_face_presence_confidence=0.5,

    min_tracking_confidence=0.5,

    # 输出眼睛 blendshape，用于判断睁眼/闭眼
    output_face_blendshapes=True

)

face_detector = (
    FaceLandmarker.create_from_options(
        face_options
    )
)

print("AI模型加载成功")


# =========================================================
# 5. 加载手部模型
# =========================================================

HandLandmarker = (
    mp.tasks.vision.HandLandmarker
)

HandLandmarkerOptions = (
    mp.tasks.vision.HandLandmarkerOptions
)

hand_options = HandLandmarkerOptions(

    base_options=BaseOptions(
        model_asset_path=HAND_MODEL
    ),

    running_mode=(
        VisionRunningMode.VIDEO
    ),

    num_hands=2,

    min_hand_detection_confidence=0.5,

    min_hand_presence_confidence=0.5,

    min_tracking_confidence=0.5

)

hand_detector = (
    HandLandmarker.create_from_options(
        hand_options
    )
)


# =========================================================
# 6. 加载人体姿态模型（可选）
# =========================================================

PoseLandmarker = (
    mp.tasks.vision.PoseLandmarker
)

PoseLandmarkerOptions = (
    mp.tasks.vision.PoseLandmarkerOptions
)

pose_model_path = None

if os.path.exists(POSE_MODEL_LITE):

    pose_model_path = POSE_MODEL_LITE

elif os.path.exists(POSE_MODEL):

    pose_model_path = POSE_MODEL

pose_detector = None

if pose_model_path is not None:

    pose_options = PoseLandmarkerOptions(

        base_options=BaseOptions(
            model_asset_path=pose_model_path
        ),

        running_mode=(
            VisionRunningMode.VIDEO
        ),

        num_poses=1,

        min_pose_detection_confidence=0.5,

        min_pose_presence_confidence=0.5,

        min_tracking_confidence=0.5

    )

    pose_detector = (
        PoseLandmarker.create_from_options(
            pose_options
        )
    )

    print(
        "[OK] 人体姿态模型加载成功："
    )
    print(
        os.path.basename(pose_model_path)
    )

else:

    print(
        "[提示] 未找到人体姿态模型，"
        "使用画面运动检测作为「人在不在」的兜底"
    )
    print(
        "建议下载 pose_landmarker_lite.task 放入 models 文件夹"
    )


# =========================================================
# 7. 时间戳
# =========================================================

last_timestamp = 0


def get_timestamp():

    global last_timestamp

    now = int(
        time.monotonic() * 1000
    )

    if now <= last_timestamp:

        now = last_timestamp + 1

    last_timestamp = now

    return now


def close_detectors():

    """程序退出时释放模型资源"""

    try:
        face_detector.close()
    except Exception:
        pass

    try:
        hand_detector.close()
    except Exception:
        pass

    if pose_detector is not None:

        try:
            pose_detector.close()
        except Exception:
            pass


# =========================================================
# 8. 头姿与眼睛阈值
# =========================================================

# 硬边界：超过即视为头姿异常（用于状态机）
YAW_LIMIT = 0.25
PITCH_DOWN_LIMIT = 0.30
PITCH_UP_LIMIT = 0.20
ROLL_LIMIT_DEG = 20.0

# 软边界：评分从 1 平滑降到 0 的范围（用于连续打分）
YAW_SOFT = 0.40
PITCH_DOWN_SOFT = 0.45
PITCH_UP_SOFT = 0.35
ROLL_SOFT_DEG = 30.0

# 眼睛
BLINK_THRESHOLD = 0.5
EAR_CLOSED = 0.18

# 画面运动兜底
MOTION_RATIO = 0.02

# 手部
# 手中心到鼻子的距离 / 眼距，小于该值视为"手贴脸"
HAND_NEAR_FACE_DIST = 1.2


# =========================================================
# 9. 姿态校准
#
# 摄像头位置不同、坐姿不同，正对屏幕时的 yaw/pitch 基准也不同。
# 校准后按"偏离个人基准"判断，而不是用写死的 0。
# =========================================================

CALIBRATION = {
    "yaw": 0.0,
    "pitch": 0.0,
    "roll": 0.0
}


def set_calibration(
    yaw=0.0,
    pitch=0.0,
    roll=0.0
):

    """设置个人姿态基准"""

    CALIBRATION["yaw"] = float(yaw)
    CALIBRATION["pitch"] = float(pitch)
    CALIBRATION["roll"] = float(roll)


def clear_calibration():

    """清除基准"""

    set_calibration(0.0, 0.0, 0.0)


class Calibrator:

    """
    校准器：开始学习时收集 3 秒正脸样本，
    用中位数作为个人基准。

    add() 返回值：
        (yaw, pitch, roll)  校准成功
        "timeout"           超时未完成（没一直正对摄像头）
        None                还在采样中
    """

    def __init__(
        self,
        duration=3.0,
        min_samples=15,
        timeout=15.0
    ):

        self.duration = duration
        self.min_samples = min_samples
        self.timeout = timeout
        self.born = time.time()
        self.samples = []
        self.start = None
        self.done = False

    def add(
        self,
        yaw,
        pitch,
        roll_deg,
        face_ok,
        now=None
    ):

        if self.done:

            return None

        if now is None:

            now = time.time()

        if now - self.born >= self.timeout:

            self.done = True

            return "timeout"

        # 校准期间人脸必须一直在，
        # 丢了脸就重新计时
        if not face_ok:

            self.samples = []

            self.start = None

            return None

        if self.start is None:

            self.start = now

        self.samples.append(
            (yaw, pitch, roll_deg)
        )

        if (
            now - self.start
            >= self.duration
            and
            len(self.samples)
            >= self.min_samples
        ):

            yaw_off = statistics.median(
                sorted(
                    s[0]
                    for s in self.samples
                )
            )

            pitch_off = statistics.median(
                sorted(
                    s[1]
                    for s in self.samples
                )
            )

            roll_off = statistics.median(
                sorted(
                    s[2]
                    for s in self.samples
                )
            )

            set_calibration(
                yaw_off,
                pitch_off,
                roll_off
            )

            self.done = True

            return (
                yaw_off,
                pitch_off,
                roll_off
            )

        return None

    def reset(self):

        self.samples = []
        self.start = None
        self.done = False
        self.born = time.time()


# =========================================================
# 10. 头部姿态计算（纯函数，便于测试）
# =========================================================

def _clamp01(value):

    return max(
        0.0,
        min(
            1.0,
            float(value)
        )
    )


def _head_pose(face):

    """
    输入：MediaPipe 468 点人脸关键点

    输出（原始值，未做个人校准）：
        yaw       左右转头（鼻子相对双眼中心 / 眼距）
        pitch     低头/仰头（鼻子相对双眼中心纵向 / 眼距），正=低头
        roll_deg  歪头角度（双眼连线与水平线的夹角）
    """

    nose = face[1]

    left_eye = face[33]

    right_eye = face[263]

    eye_dist = math.hypot(
        right_eye.x - left_eye.x,
        right_eye.y - left_eye.y
    )

    if eye_dist < 0.001:

        eye_dist = 0.001

    eye_center_x = (
        left_eye.x
        +
        right_eye.x
    ) / 2

    eye_center_y = (
        left_eye.y
        +
        right_eye.y
    ) / 2

    yaw = (
        nose.x
        -
        eye_center_x
    ) / eye_dist

    pitch = (
        nose.y
        -
        eye_center_y
    ) / eye_dist

    roll_deg = math.degrees(
        math.atan2(
            right_eye.y - left_eye.y,
            right_eye.x - left_eye.x
        )
    )

    return {
        "yaw": yaw,
        "pitch": pitch,
        "roll_deg": roll_deg,
        "eye_dist": eye_dist
    }


def _head_pose_ok(
    yaw,
    pitch,
    roll_deg
):

    """按个人校准后的偏移判断头姿是否正常（硬边界）"""

    dyaw = (
        yaw
        -
        CALIBRATION["yaw"]
    )

    dpitch = (
        pitch
        -
        CALIBRATION["pitch"]
    )

    droll = (
        roll_deg
        -
        CALIBRATION["roll"]
    )

    return (
        abs(dyaw) < YAW_LIMIT
        and
        dpitch < PITCH_DOWN_LIMIT
        and
        dpitch > -PITCH_UP_LIMIT
        and
        abs(droll) < ROLL_LIMIT_DEG
    )


def _head_pose_score(
    yaw,
    pitch,
    roll_deg
):

    """
    头姿连续评分 0~1：
    越靠近硬边界分数越低，越界降到 0。
    用于单帧评分，代替"对/错"二值。
    """

    dyaw = (
        yaw
        -
        CALIBRATION["yaw"]
    )

    dpitch = (
        pitch
        -
        CALIBRATION["pitch"]
    )

    droll = (
        roll_deg
        -
        CALIBRATION["roll"]
    )

    yaw_score = _clamp01(
        1.0
        -
        abs(dyaw)
        /
        YAW_SOFT
    )

    down_score = _clamp01(
        1.0
        -
        dpitch
        /
        PITCH_DOWN_SOFT
    )

    up_score = _clamp01(
        1.0
        +
        dpitch
        /
        PITCH_UP_SOFT
    )

    roll_score = _clamp01(
        1.0
        -
        abs(droll)
        /
        ROLL_SOFT_DEG
    )

    return min(
        yaw_score,
        down_score,
        up_score,
        roll_score
    )


# =========================================================
# 11. 眼睛开合计算
# =========================================================

# FaceMesh 中左眼（画面右侧）6 点
LEFT_EYE_POINTS = [362, 385, 387, 263, 373, 380]

# FaceMesh 中右眼（画面左侧）6 点
RIGHT_EYE_POINTS = [33, 160, 158, 133, 153, 144]


def _eye_ear(face, point_ids):

    """单眼 EAR：眼开合度，睁眼约 0.25~0.35，闭眼约 0.1"""

    p = [
        face[i]
        for i in point_ids
    ]

    a = math.hypot(
        p[1].x - p[5].x,
        p[1].y - p[5].y
    )

    b = math.hypot(
        p[2].x - p[4].x,
        p[2].y - p[4].y
    )

    c = math.hypot(
        p[0].x - p[3].x,
        p[0].y - p[3].y
    )

    if c < 1e-6:

        return 1.0

    return (
        a
        +
        b
    ) / (
        2.0
        *
        c
    )


def _eyes_closed_blendshapes(face_result):

    """
    优先用 blendshape 判断闭眼。
    返回 None 表示无法判断（走 EAR 兜底）。
    """

    try:

        blendshapes = (
            face_result.face_blendshapes
        )

        if not blendshapes:

            return None

        for bs in blendshapes:

            left = 0.0

            right = 0.0

            for cat in bs.categories:

                if (
                    cat.category_name
                    == "eyeBlinkLeft"
                ):

                    left = cat.score

                elif (
                    cat.category_name
                    == "eyeBlinkRight"
                ):

                    right = cat.score

            if left > 0 or right > 0:

                return (
                    left > BLINK_THRESHOLD
                    and
                    right > BLINK_THRESHOLD
                )

        return None

    except Exception:

        return None


def _eyes_closed(face_result, face):

    """判断双眼是否闭合（blendshape 优先，EAR 兜底）"""

    closed = (
        _eyes_closed_blendshapes(
            face_result
        )
    )

    if closed is not None:

        return closed

    left_ear = _eye_ear(
        face,
        LEFT_EYE_POINTS
    )

    right_ear = _eye_ear(
        face,
        RIGHT_EYE_POINTS
    )

    return (
        (
            left_ear
            +
            right_ear
        )
        /
        2.0
        <
        EAR_CLOSED
    )


# =========================================================
# 12. 手部信号
# =========================================================

def _hand_center(hand):

    """手部 21 点的中心位置"""

    if not hand:

        return None

    x = sum(
        p.x
        for p in hand
    ) / len(hand)

    y = sum(
        p.y
        for p in hand
    ) / len(hand)

    return (x, y)


def _hands_near_face(
    face,
    hands,
    eye_dist
):

    """
    判断是否有手贴在脸附近（玩手机/打电话/托腮/吃东西）。
    距离用"手中心到鼻子的距离 / 眼距"归一化。
    """

    if face is None or not hands:

        return False

    nose = face[1]

    if eye_dist < 0.001:

        eye_dist = 0.001

    for hand in hands:

        center = _hand_center(hand)

        if center is None:

            continue

        distance = math.hypot(
            center[0] - nose.x,
            center[1] - nose.y
        )

        if (
            distance
            /
            eye_dist
            <
            HAND_NEAR_FACE_DIST
        ):

            return True

    return False


def _hand_confidence(hand_result):

    """手部检测置信度（0~1），取所有手里最高的"""

    try:

        scores = []

        for handedness in (
            hand_result.handedness
        ):

            if handedness:

                scores.append(
                    max(
                        c.score
                        for c in handedness
                    )
                )

        if not scores:

            return 0.0

        return max(scores)

    except Exception:

        return 0.0


# =========================================================
# 13. 人体存在检测
# =========================================================

# 画面运动兜底用的背景减法器
bg_subtractor = (
    cv2.createBackgroundSubtractorMOG2(
        history=200,
        varThreshold=36,
        detectShadows=False
    )
)


def _person_present(
    frame,
    face_ok,
    hand_count,
    mp_image,
    timestamp_ms
):

    """
    判断座位上是否有人：
    1. 有脸/有手 → 肯定在
    2. 有 pose 模型 → 用人体姿态判断
    3. 都没有 → 用画面运动做兜底
    """

    if face_ok or hand_count > 0:

        return True

    if pose_detector is not None:

        try:

            pose_result = (
                pose_detector.detect_for_video(
                    mp_image,
                    timestamp_ms
                )
            )

            return bool(
                pose_result.pose_landmarks
            )

        except Exception:

            return False

    # 画面运动兜底
    try:

        small = cv2.resize(
            frame,
            (160, 120)
        )

        gray = cv2.cvtColor(
            small,
            cv2.COLOR_BGR2GRAY
        )

        fg = bg_subtractor.apply(
            gray
        )

        ratio = (
            cv2.countNonZero(fg)
            /
            (
                160
                *
                120
            )
        )

        return ratio > MOTION_RATIO

    except Exception:

        return False


# =========================================================
# 14. 单帧分析
# =========================================================

def analyze_frame(
    frame,
    timestamp_ms=None
):

    """
    输入：
        frame
        OpenCV BGR图像

    输出：
        face_ok          是否检测到人脸
        head_ok          头姿是否正常（校准后，硬边界）
        head_score       头姿连续评分 0~1（校准后，软边界）
        yaw/pitch/roll   原始头姿（未校准，供校准器使用）
        eye_closed       双眼是否闭合
        person_ok        座位上是否有人
        hand_count       检测到手部数量
        hand_confidence  手部检测置信度（0~1）
        hand_near_face   是否有手贴在脸附近
        raw_status       单帧原始状态
        score            单帧行为评分 0~100
    """

    if timestamp_ms is None:

        timestamp_ms = get_timestamp()

    # =================================================
    # BGR -> RGB
    # =================================================

    rgb = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2RGB
    )

    mp_image = mp.Image(
        image_format=(
            mp.ImageFormat.SRGB
        ),
        data=rgb
    )

    # =================================================
    # 人脸检测
    # =================================================

    face_result = (
        face_detector.detect_for_video(
            mp_image,
            timestamp_ms
        )
    )

    # =================================================
    # 手部检测
    # =================================================

    hand_result = (
        hand_detector.detect_for_video(
            mp_image,
            timestamp_ms
        )
    )

    hand_count = len(
        hand_result.hand_landmarks
    )

    hand_confidence = (
        _hand_confidence(
            hand_result
        )
    )

    # =================================================
    # 人脸、头姿、眼睛
    # =================================================

    face_ok = False

    head_ok = False

    head_score = 0.0

    yaw = 0.0

    pitch = 0.0

    roll_deg = 0.0

    eye_dist = 0.001

    eye_closed = False

    face = None

    if face_result.face_landmarks:

        face_ok = True

        face = (
            face_result.face_landmarks[0]
        )

        pose = _head_pose(face)

        yaw = pose["yaw"]

        pitch = pose["pitch"]

        roll_deg = pose["roll_deg"]

        eye_dist = pose["eye_dist"]

        head_ok = _head_pose_ok(
            yaw,
            pitch,
            roll_deg
        )

        head_score = _head_pose_score(
            yaw,
            pitch,
            roll_deg
        )

        eye_closed = _eyes_closed(
            face_result,
            face
        )

    # =================================================
    # 手贴脸判断
    # =================================================

    hand_near_face = (
        _hands_near_face(
            face,
            hand_result.hand_landmarks,
            eye_dist
        )
    )

    # =================================================
    # 人体存在检测
    # =================================================

    person_ok = _person_present(
        frame,
        face_ok,
        hand_count,
        mp_image,
        timestamp_ms
    )

    # =================================================
    # 单帧原始状态
    # =================================================

    if not face_ok:

        if person_ok:

            # 人在但没脸：低头/背对摄像头
            raw_status = STATE_DISTRACT

        else:

            raw_status = STATE_AWAY

    elif eye_closed:

        raw_status = STATE_SLEEP

    elif hand_near_face:

        # 手贴脸：玩手机/打电话/托腮/吃东西
        raw_status = STATE_DISTRACT

    elif head_ok:

        raw_status = STATE_STUDYING

    else:

        raw_status = STATE_DISTRACT

    # =================================================
    # 单帧行为评分
    # =================================================

    score = frame_score(
        face_ok,
        head_ok,
        hand_count,
        raw_status,
        eye_closed,
        person_ok,
        head_score,
        hand_confidence,
        hand_near_face
    )

    return {

        "face_ok": face_ok,

        "head_ok": head_ok,

        "head_score": head_score,

        "yaw": yaw,

        "pitch": pitch,

        "roll_deg": roll_deg,

        "eye_closed": eye_closed,

        "person_ok": person_ok,

        "hand_count": hand_count,

        "hand_confidence": hand_confidence,

        "hand_near_face": hand_near_face,

        "raw_status": raw_status,

        "score": score

    }


# =========================================================
# 15. 单帧行为评分
#
# 满分100
#
# 人脸：35
# 头部方向：45（按头姿连续评分加权）
# 手部：10（按检测置信度加权，手贴脸则扣分）
#
# 手部不是必须项，因此没有手不会直接判定为分心
# =========================================================

SCORE_FACE = 35

SCORE_HEAD = 45

SCORE_HAND = 10

SCORE_HAND_FALLBACK = 8

SCORE_HAND_NEAR_FACE_FACTOR = 0.70

SCORE_DISTRACT_FACTOR = 0.45

SCORE_SLEEP_FACTOR = 0.20

SCORE_DETECTING_FACTOR = 0.80


def frame_score(
    face_ok,
    head_ok,
    hand_count,
    raw_status,
    eye_closed=False,
    person_ok=True,
    head_score=None,
    hand_confidence=1.0,
    hand_near_face=False
):

    # -------------------------------------------------
    # 离开 / 无人
    # -------------------------------------------------

    if (
        not person_ok
        or
        raw_status == STATE_AWAY
    ):

        return 0.0

    # 没传头姿软评分时，按二值头姿兜底
    if head_score is None:

        head_score = (
            1.0
            if head_ok
            else 0.0
        )

    score = 0.0

    # -------------------------------------------------
    # 人脸
    # -------------------------------------------------

    if face_ok:

        score += SCORE_FACE

    # -------------------------------------------------
    # 头部方向（软评分，越靠边界越低）
    # -------------------------------------------------

    if face_ok:

        score += (
            SCORE_HEAD
            *
            _clamp01(head_score)
        )

    # -------------------------------------------------
    # 手部
    #
    # 手贴脸（玩手机/打电话/托腮）不加分反而打折；
    # 普通手部（打字/写字）按置信度加分
    # -------------------------------------------------

    if (
        hand_count > 0
        and
        not hand_near_face
    ):

        score += (
            SCORE_HAND
            *
            _clamp01(hand_confidence)
        )

    elif (
        face_ok
        and
        head_ok
        and
        not hand_near_face
    ):

        # 没检测到手，但人在正常学习
        # 不扣除全部手部评分
        score += SCORE_HAND_FALLBACK

    if hand_near_face:

        score *= SCORE_HAND_NEAR_FACE_FACTOR

    # -------------------------------------------------
    # 状态打折
    # -------------------------------------------------

    if raw_status == STATE_DISTRACT:

        score *= SCORE_DISTRACT_FACTOR

    elif raw_status == STATE_SLEEP:

        score *= SCORE_SLEEP_FACTOR

    elif raw_status == STATE_DETECTING:

        score *= SCORE_DETECTING_FACTOR

    # -------------------------------------------------
    # 限制范围
    # -------------------------------------------------

    score = max(
        0,
        min(
            100,
            score
        )
    )

    return score


# =========================================================
# 16. 状态机 + 迟滞
#
# 用"持续时长"做状态切换：
# 进入坏状态需要更长时间，恢复好状态需要更短时间，
# 避免单帧抖动导致状态闪烁。
#
# 信号类型：
#   good   有脸 + 头正 + 睁眼 + 手没贴脸
#   bad    有脸 + 头偏/低头/仰头/歪头 或 手贴脸
#   sleep  有脸 + 双眼闭合
#   back   无脸但人在（低头/背对）
#   none   无脸且无人
# =========================================================

class StudyStateMachine:

    """
    默认参数（秒）：

    away_confirm
        持续无人多久判"离开"

    distract_confirm
        持续头偏/低头/背对/手贴脸多久判"注意分心"

    study_confirm
        持续正脸多久判定/恢复"正在学习"

    sleep_confirm
        持续闭眼多久判"疑似睡觉"
    """

    def __init__(
        self,
        away_confirm=3.0,
        distract_confirm=5.0,
        study_confirm=2.0,
        sleep_confirm=3.0
    ):

        self.away_confirm = (
            away_confirm
        )

        self.distract_confirm = (
            distract_confirm
        )

        self.study_confirm = (
            study_confirm
        )

        self.sleep_confirm = (
            sleep_confirm
        )

        self.state = STATE_DETECTING

        # 当前信号类型
        self._signal = None

        # 当前信号开始时间
        self._signal_since = (
            time.time()
        )

    def _classify(
        self,
        face_ok,
        head_ok,
        eye_closed,
        person_ok,
        hand_near_face
    ):

        if not face_ok:

            if person_ok:

                return "back"

            return "none"

        if eye_closed:

            return "sleep"

        if hand_near_face or not head_ok:

            return "bad"

        return "good"

    def update(
        self,
        face_ok,
        head_ok,
        eye_closed=False,
        person_ok=True,
        hand_near_face=False,
        now=None
    ):

        """
        输入一帧原始信号，返回平滑后的稳定状态。
        """

        if now is None:

            now = time.time()

        sig = self._classify(
            face_ok,
            head_ok,
            eye_closed,
            person_ok,
            hand_near_face
        )

        # 信号类型变化时重新计时
        if sig != self._signal:

            self._signal = sig

            self._signal_since = now

        held = (
            now
            -
            self._signal_since
        )

        s = self.state

        # ---------------------------------------------
        # 检测中
        # ---------------------------------------------

        if s == STATE_DETECTING:

            if (
                sig == "none"
                and
                held >= self.away_confirm
            ):

                s = STATE_AWAY

            elif (
                sig == "sleep"
                and
                held >= self.sleep_confirm
            ):

                s = STATE_SLEEP

            elif (
                sig in ("bad", "back")
                and
                held >= self.distract_confirm
            ):

                s = STATE_DISTRACT

            elif (
                sig == "good"
                and
                held >= self.study_confirm
            ):

                s = STATE_STUDYING

        # ---------------------------------------------
        # 正在学习
        #
        # 学习中短暂转头/丢脸/眨眼/抬手不降级，
        # 只有持续足够久才切换
        # ---------------------------------------------

        elif s == STATE_STUDYING:

            if (
                sig == "none"
                and
                held >= self.away_confirm
            ):

                s = STATE_AWAY

            elif (
                sig == "sleep"
                and
                held >= self.sleep_confirm
            ):

                s = STATE_SLEEP

            elif (
                sig in ("bad", "back")
                and
                held >= self.distract_confirm
            ):

                s = STATE_DISTRACT

        # ---------------------------------------------
        # 注意分心
        # ---------------------------------------------

        elif s == STATE_DISTRACT:

            if (
                sig == "good"
                and
                held >= self.study_confirm
            ):

                s = STATE_STUDYING

            elif (
                sig == "sleep"
                and
                held >= self.sleep_confirm
            ):

                s = STATE_SLEEP

            elif (
                sig == "none"
                and
                held >= self.away_confirm
            ):

                s = STATE_AWAY

        # ---------------------------------------------
        # 疑似睡觉
        # ---------------------------------------------

        elif s == STATE_SLEEP:

            if (
                sig == "good"
                and
                held >= self.study_confirm
            ):

                s = STATE_STUDYING

            elif (
                sig in ("bad", "back")
                and
                held >= self.study_confirm
            ):

                s = STATE_DISTRACT

            elif (
                sig == "none"
                and
                held >= self.away_confirm
            ):

                s = STATE_AWAY

        # ---------------------------------------------
        # 离开
        #
        # 人脸/人重新出现时先回到检测中，重新计时确认
        # ---------------------------------------------

        elif s == STATE_AWAY:

            if sig != "none":

                s = STATE_DETECTING

                self._signal = None

                self._signal_since = now

        self.state = s

        return s

    def reset(self):

        """开始新一场学习时重置"""

        self.state = STATE_DETECTING

        self._signal = None

        self._signal_since = (
            time.time()
        )
