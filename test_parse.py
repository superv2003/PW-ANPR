from paddleocr import PaddleOCR
import numpy as np
import cv2

ocr = PaddleOCR(use_angle_cls=True, lang='en')
img = np.ones((100, 200, 3), dtype=np.uint8) * 128
cv2.putText(img, "MH12AB1234", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
result = ocr.ocr(img)
print(result)
