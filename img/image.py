from PIL import Image
import numpy as np

ROI = (44, 500, 74, 532)  # x1, y1, x2, y2 -> nuova ROI centrata sull'icona

for name in ["icona_inattiva.png", "immagine_android.png"]:
    img = np.array(Image.open(name).convert("RGB"))
    x1, y1, x2, y2 = ROI
    roi = img[y1:y2, x1:x2]
    r, g, b = roi.mean(axis=(0,1))
    print(name, f"#{int(r):02x}{int(g):02x}{int(b):02x}")