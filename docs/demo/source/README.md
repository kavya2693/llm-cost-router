# Video and audit report sources

Everything needed to rebuild the silent walkthrough (`docs/demo/demo.mp4`) and the audit report
(`docs/demo/AUDIT_REPORT.*`) from this repository alone. Paths below are relative to the repo root.

## What is here

| Path | What it is |
|---|---|
| `index.html` | Root of the video: theme (colours, type), and the timing of every slide |
| `compositions/bg.html`, `compositions/chrome.html` | Background grid, and the persistent project label, slide counter and progress bar |
| `compositions/s01-context.html` | Slide 01, the industry and four everyday problems |
| `compositions/s02-hook.html` | Slide 02, the project in one sentence |
| `compositions/explainer.html` | Slides 03-04, the white "how it works" chapter (edit its `CONFIG` block) |
| `compositions/s03-architecture.html` | Slide 05, the architecture diagram (offline tuning, online cascade, evaluation replay) |
| `compositions/s04-routes.html` | Slide 06, the 120 held-out requests as routed, and completion vs cost for every arm (data injected by `tools/inject_sheet.py`) |
| `compositions/value.html` | Slide 07, the routing ledger and ops-desk scene (data injected by `tools/inject_sheet.py`) |
| `compositions/s05-results.html`, `s06-twist.html`, `s07-end.html` | Slides 08-10, results, lesson and fix (what failed and the next step), end card |
| `assets/routing_log.json` | The tuned router's per-request log on the held-out split, exported by `tools/export_routing_log.py` |
| `audit/facts.yaml` | Every figure (F-xx), assumption (A-xx), slide text and check expression used by the report |
| `tools/build_audit_report.py` | Builds the slide stills, re-checks every figure against the repo outputs, writes MD, DOCX and PDF |
| `tools/export_routing_log.py` | Re-runs the repo's own `calibrate()` and `Router` over the committed response cache and exports the log as JSON |
| `tools/inject_sheet.py` | Inlines that log (and `outputs/results.json`) into `s04-routes.html` and `value.html` (renders cannot fetch files) |
| `package.json`, `hyperframes.json`, `meta.json` | HyperFrames project files (CLI pinned to 0.8.156) |

Fonts (Inter, JetBrains Mono, Montserrat) are referenced by name and embedded by the HyperFrames
renderer; all three are SIL Open Font License. The repo has no plots, so there is no `darkmap.py`
step: slide 06 is drawn from the numbers in `outputs/results.json` and `outputs/per_case.csv`.

## Requirements

- Node 22 and `npx` (HyperFrames 0.8.156 is fetched by `npx`), plus Chrome/Chromium for rendering.
- `ffmpeg` for compressing the video and extracting slide stills.
- LibreOffice (`soffice`) to convert the report to PDF.
- The repo's own environment (`uv sync`) for `make run` and `export_routing_log.py`. No model server
  is needed: both replay the committed response cache in `outputs/responses/`.
- For `build_audit_report.py` and `inject_sheet.py` (any Python 3.11+, not added to the repo's
  `pyproject.toml`): `pip install pyyaml pandas python-docx pillow numpy matplotlib`.

## Rebuild the numbers, the video and the report

```bash
# 1. repo outputs (replays the cache, under a second)
make run

# 2. inputs for the video
.venv/bin/python docs/demo/source/tools/export_routing_log.py docs/demo/source/assets/routing_log.json
python3 docs/demo/source/tools/inject_sheet.py docs/demo/source/assets/routing_log.json outputs/results.json \
  docs/demo/source/compositions --rows ext-023,cla-021,sum-026,rea-040,rea-021,cla-035

# 3. render (87 s, 1920x1080, 30 fps, silent) and compress
cd docs/demo/source
npx --yes hyperframes@0.8.156 check
npx --yes hyperframes@0.8.156 render -f 30 -q looks -o renders/llm-cost-router-master.mp4
cd -
ffmpeg -y -i docs/demo/source/renders/llm-cost-router-master.mp4 -c:v libx264 -preset slow -crf 25 \
  -pix_fmt yuv420p -movflags +faststart -an docs/demo/demo.mp4

# GIFs: the end-user value slide, and the architecture data flow
VF="fps=12,scale=800:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
ffmpeg -y -ss 53.6 -t 12 -i docs/demo/source/renders/llm-cost-router-master.mp4 -vf "$VF" -loop 0 docs/demo/demo.gif
ffmpeg -y -ss 32.8 -t 8 -i docs/demo/source/renders/llm-cost-router-master.mp4 -vf "$VF" -loop 0 docs/demo/architecture.gif

# 4. audit report: slide stills, automated checks, AUDIT_REPORT.md/.docx/.pdf
python3 docs/demo/source/tools/build_audit_report.py docs/demo/source/audit/facts.yaml
```

`build_audit_report.py` exits with code 1 if any figure on screen no longer matches the repo outputs.
`make models` (about 46 minutes of CPU inference through Ollama) refreshes the response cache; CPU
inference is not bit-for-bit deterministic, so after it every step above must be re-run and the
report's change-impact matrix lists which figures and slides each input affects.

The `renders/` folder (about 12 MB) is not committed; step 3 recreates it.
