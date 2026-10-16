# The narrated demo

This folder builds Eagleview's narrated demo video with [Remotion](https://www.remotion.dev),
from footage that already exists: the raw clips and run logs that
`scripts/evidence/demo_video.sh` keeps in `runs/demo_video/` (mock bank only). Every cursor
position, click, typed value, control change and line of AI reasoning on screen comes from a run
log. The chat window is drawn for the video and says so ("illustration"); the question and the
answer it shows come from the real agent run.

**Narration is AI-generated.** It is spoken by a macOS voice (`say`), one audio file per beat,
and the subtitles are the same words.

## Preview and render

You need Node 20 or later, `ffmpeg`, and the raw clips in `runs/demo_video/` (record any that are
missing with `scripts/evidence/demo_video.sh --record-video --no-build`; only the discovery and
agent segments call the model).

```bash
cd video && npm ci
```

```bash
cd video && npm run prepare-media
```

```bash
cd video && npm run studio
```

```bash
cd video && npm run render
```

- `prepare-media` runs `video/prepare.py`: it converts the clips, extracts the stills, speaks the
  narration and writes `public/media/story.json`, the timeline the composition reads. Nothing in
  `public/media/` is committed.
- **The sync check** runs inside `prepare-media` and stops it on any mismatch (`video/sync.py`).
  Each click or keypress is timed from the footage itself, at the first frame of its effect; the
  footage holds the frame just before it while the ripple plays. For every overlaid action the
  check confirms that the held frame is stable and followed by the action's effect, matches the
  action's own logged "before" screenshot better than its "after" one, has the target element
  (not blank space) under the ripple, sits inside the camera's view, and is what the timeline
  shows at that moment.
- `sync-sheet` writes `runs/demo_video/sync_sheet.png` from the render: one frame per click or
  keypress, at its ripple, labelled with the action.
- `studio` opens Remotion Studio (http://localhost:3000) to scrub through the `Demo` composition.
- `render` writes `runs/demo_video/draft.mp4`.

To change a narration line, edit it in `prepare.py` and run `prepare-media` again: each beat's
length follows its audio, and footage holds its last frame while the narration finishes.

## Layout

| File | What |
|---|---|
| `prepare.py` | run logs and clips in, `public/media/` out |
| `src/Root.tsx`, `src/Demo.tsx` | the composition: scenes, narration, subtitles |
| `sync.py`, `sync_sheet.py` | each action's on-screen moment, the one timeline, the sync check; the sync sheet |
| `src/Footage.tsx` | footage with jump cuts and held frames, the camera, cursor, ripples, labels, the "driving" token |
| `src/scenes.tsx` | one component per beat |
| `src/Chat.tsx`, `src/Subtitles.tsx` | the assistant's chat window; two-line subtitles |
| `src/theme.ts` | one colour per actor: violet for the AI, teal for the recipe, orange for a person |
