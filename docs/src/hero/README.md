# Hero video source

The hero loop is a vector drawing (`blueprint-scene.html`) rendered frame by frame in headless Chromium, then encoded with ffmpeg. It is 12 seconds: three gate lamps light, then the ratchet clicks forward one tooth, four times. The wheel is symmetric under a one-tooth turn, so the last frame leads straight back into the first.

```sh
npm i playwright@1.48.2
node render.mjs blueprint-scene.html frames        # 360 frames at 1920x1080
A=../../assets
ffmpeg -framerate 30 -i frames/f%04d.png -vf format=yuv420p -c:v libx264 -preset slow -crf 20 -tune animation -movflags +faststart -an $A/hero-1080.mp4
ffmpeg -framerate 30 -i frames/f%04d.png -vf format=yuv420p -c:v libvpx-vp9 -crf 32 -b:v 0 -row-mt 1 -an $A/hero-1080.webm
ffmpeg -framerate 30 -i frames/f%04d.png -vf "scale=1280:720:flags=lanczos,format=yuv420p" -c:v libx264 -preset slow -crf 22 -tune animation -movflags +faststart -an $A/hero-720.mp4
ffmpeg -i frames/f0000.png -q:v 3 $A/hero-poster.jpg
```

`render.mjs` expects Playwright's headless shell; set its `executablePath` for your machine. `og.html` is the social card template (screenshot at 1200×630).
