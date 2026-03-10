import re

# ==============================================================================
# Camera & Frame Grabbing Configuration
# ==============================================================================
# NOTE: RTSP_TIMEOUT_SEC is no longer used for frame grabbing because CameraManager
# keeps a persistent connection open. It is retained here only for any legacy
# health-check logic that may reference it.
RTSP_TIMEOUT_SEC = 5.0

# Stale-frame threshold: if the background reader hasn't delivered a new frame
# within this many seconds, get_frame() returns CAMERA_STALE_FRAME.
CAMERA_STALE_FRAME_SEC = 10.0

# How long to wait for the very first frame after adding a camera at startup.
CAMERA_READY_TIMEOUT_SEC = 15.0

FRAMES_TO_GRAB = 1              # Number of frames returned per capture trigger
MAX_IMAGE_WIDTH = 1280          # Max width for processing (maintains aspect ratio)

# ==============================================================================
# Debugging Configuration
# ==============================================================================
DEBUG_SAVE_IMAGES = True        # Save grabbed frames and ROIs to disk
DEBUG_OUT_DIR = "debug_output/" # Directory to save the debug images

# ==============================================================================
# Plate Detection Configuration (YOLO & Fallback)
# ==============================================================================
YOLO_MODEL_PATH = "lpr_engine/models/indian_plate_detector.onnx"
YOLO_CONF_THRESHOLD = 0.10      # Aggressively low to catch standalone plates (Regex handles false positives)
YOLO_INPUT_SIZE = (416, 416)    # The selected Indian plate model uses 416x416

# Fallback Contour Detection
FALLBACK_MIN_ASPECT_RATIO = 2.0
FALLBACK_MAX_ASPECT_RATIO = 6.0
FALLBACK_MIN_AREA_PERCENT = 0.005  # Minimum area relative to image size

# ==============================================================================
# OCR Configuration
# ==============================================================================
PADDLE_CONF_THRESHOLD = 0.60    # Minimum confidence to accept PaddleOCR result
PADDLE_MIN_HEIGHT = 64          # Min height of OCR ROI (upscales if smaller)
OCR_LANG = 'en'                 # English is standard for Indian plates

# ==============================================================================
# Post-Processing & Validation Configuration
# ==============================================================================
WEIGHT_DETECTION = 0.4
WEIGHT_OCR = 0.6

MULTI_FRAME_MATCH_BOOST = 0.10

# Text Replacement Rules (OCR correction for common Indian plate mistakes)
OCR_CORRECTIONS = {
    'O': '0',
    'I': '1',
    'L': '1',
    'B': '8',
    'S': '5',
    'G': '6',
    'Z': '2',
    'Q': '0',
    'A': '4'
}

# Regex Patterns for Indian Plates
PATTERN_STANDARD = re.compile(r'^[A-Z]{2}[0-9]{2}[A-Z]{1,2}[0-9]{4}$')
PATTERN_BH_SERIES = re.compile(r'^[0-9]{2}BH[0-9]{4}[A-Z]{2}$')

VALID_STATE_CODES = {
    'AN', 'AP', 'AR', 'AS', 'BR', 'CG', 'CH', 'DD', 'DL', 'DN',
    'GA', 'GJ', 'HP', 'HR', 'JH', 'JK', 'KA', 'KL', 'LA', 'LD',
    'MH', 'ML', 'MN', 'MP', 'MZ', 'NL', 'OD', 'PB', 'PY', 'RJ',
    'SK', 'TN', 'TS', 'UK', 'UP', 'WB'
}
