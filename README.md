# VisionGesture — Python 3.13 + MediaPipe Tasks

This version uses `mediapipe.tasks.python.vision.HandLandmarker`, not the removed
`mp.solutions.hands` API.

## 1. Install dependencies

Open PowerShell in this project folder:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 2. Download the hand landmark model

Download the official MediaPipe Hand Landmarker model from:

https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task

Save the file as **`hand_landmarker.task`** in the same folder as `visiongesture.py`.

Expected folder:

```text
VisionGesture_Python313/
  visiongesture.py
  requirements.txt
  README.md
  hand_landmarker.task
```

## 3. Run

```powershell
python visiongesture.py
```

Press `Q` in the camera window to quit.

## Gestures

- Index finger only: move cursor.
- Thumb + index pinch: left click.
- Index + middle fingers: scroll.
- Open palm: set system volume from thumb-index distance.
- Closed fist: press Space (play/pause in many media players).

## Notes

- This is a prototype. Lighting, camera position, and hand orientation affect recognition.
- The fist gesture sends Space to the currently focused application. Test with a media player first.
- PyAutoGUI failsafe is enabled; move the cursor to the top-left corner to trigger it.
- On Windows, volume control uses `pycaw`. On macOS, it uses `osascript`. On Linux, it uses `pactl`.
- If volume control is unavailable, mouse, scroll, and media gestures may still work.
