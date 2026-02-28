import re

# ==============================================================================
# Camera & Frame Grabbing Configuration
# ==============================================================================
RTSP_TIMEOUT_SEC = 3.0          # Maximum time to wait for connection or frame grab
FRAMES_TO_GRAB = 3              # Number of consecutive frames to capture per trigger
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
FALLBACK_MIN_AREA_PERCENT = 0.005 # Minimum area relative to image size

# ==============================================================================
# OCR Configuration
# ==============================================================================
PADDLE_CONF_THRESHOLD = 0.60    # Minimum confidence to accept PaddleOCR result
PADDLE_MIN_HEIGHT = 64          # Min height of OCR ROI (upscales if smaller)
OCR_LANG = 'en'                 # English is standard for Indian plates

# ==============================================================================
# Post-Processing & Validation Configuration
# ==============================================================================
# Weights for final confidence calculation
WEIGHT_DETECTION = 0.4
WEIGHT_OCR = 0.6

# Boost if multiple frames yield the same plate string
MULTI_FRAME_MATCH_BOOST = 0.10

# Text Replacement Rules (OCR correction for common Indian plate mistakes)
OCR_CORRECTIONS = {
    'O': '0', # In digit positions
    'I': '1',
    'L': '1',
    'B': '8',
    'S': '5',
    'G': '6',
    'Z': '2',
    'Q': '0',
    'A': '4'  # Less common, but happens
}

# Regex Patterns for Indian Plates
# Standard: MH12AB1234
PATTERN_STANDARD = re.compile(r'^[A-Z]{2}[0-9]{2}[A-Z]{1,2}[0-9]{4}$')
# BH Series: 22BH1234AA
PATTERN_BH_SERIES = re.compile(r'^[0-9]{2}BH[0-9]{4}[A-Z]{2}$')
# Old formats sometimes have 1 letter or fewer digits, but strictly following requirements:
# Government plates / other variations exist, but these are the primary validation targets.

VALID_STATE_CODES = {
    'AN', 'AP', 'AR', 'AS', 'BR', 'CG', 'CH', 'DD', 'DL', 'DN', 
    'GA', 'GJ', 'HP', 'HR', 'JH', 'JK', 'KA', 'KL', 'LA', 'LD', 
    'MH', 'ML', 'MN', 'MP', 'MZ', 'NL', 'OD', 'PB', 'PY', 'RJ', 
    'SK', 'TN', 'TS', 'UK', 'UP', 'WB'
}
