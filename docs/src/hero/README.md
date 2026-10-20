# Hero video source

The hero loop is a real-time Three.js scene rendered frame by frame in headless Chromium, then encoded with ffmpeg.

```sh
npm i three@0.170.0 playwright@1.48.2
# 270 frames at 1080p: 240 for the 8 s loop plus 30 past it for the seam cross-fade
node render.mjs frames 1920 1080 0 270
python3 xfade.py                      # writes final/ (240 frames, seamless)
Q=cam=macro node render.mjs macro 1920 1080 49 50   # close-up still
```

Encode (from this folder, `A=../../assets`):

```sh
VF="vignette=PI/5"
ffmpeg -framerate 30 -i final/f%04d.png -vf "$VF,format=yuv420p" -c:v libx264 -preset slow -crf 22 -movflags +faststart -an $A/hero-1080.mp4
ffmpeg -framerate 30 -i final/f%04d.png -vf "$VF,format=yuv420p" -c:v libvpx-vp9 -crf 33 -b:v 0 -row-mt 1 -an $A/hero-1080.webm
ffmpeg -framerate 30 -i final/f%04d.png -vf "$VF,scale=1280:720:flags=lanczos,format=yuv420p" -c:v libx264 -preset slow -crf 24 -movflags +faststart -an $A/hero-720.mp4
```

`render.mjs` expects Playwright's headless shell; set its `executablePath` for your machine. `og.html` is the social card template (screenshot at 1200×630).
