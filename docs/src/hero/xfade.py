# Output frame j (0..239). For the first N frames, blend the continuation past the loop (240+j)
# into the start (j), so frame 239 -> 0 is the same step as 239 -> 240.
import os, shutil
from PIL import Image
N, LOOP = 30, 240
os.makedirs('final', exist_ok=True)
for j in range(LOOP):
    dst = f'final/f{j:04d}.png'
    if j >= N:
        shutil.copyfile(f'frames/f{j:04d}.png', dst); continue
    a = Image.open(f'frames/f{LOOP + j:04d}.png').convert('RGB')
    b = Image.open(f'frames/f{j:04d}.png').convert('RGB')
    w = j / (N - 1); w = w * w * (3 - 2 * w)
    Image.blend(a, b, w).save(dst)
print('done')
