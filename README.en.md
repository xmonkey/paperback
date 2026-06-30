# Paperback

> [中文](README.md) | [English](README.en.md)

A paper-based dictation tool for Anki: pull today's due cards → generate a printable worksheet → write from memory on paper → grade (manual or photo OCR) → write ease back to Anki's scheduler.

Turn "swiping cards on screen" into "write from memory on paper + feed results back" — reinforce memory through more effortful active recall.

## Features

- **Works with any note type**: uses Anki's already-rendered front/back directly — Basic / Basic (and reversed) / Cloze / custom templates all supported
- **Printable worksheet**: left = prompt, right = blank; A4 layout; font size / orientation / single-or-double column adjustable, **remembered per deck**
- **Two grading modes**:
  - Manual: keyboard 1–4, `Space` to toggle answer (recommended)
  - Photo OCR (⚠️ in testing, not recommended): snap the worksheet on phone → GLM vision recognizes handwriting → suggests ease → you confirm
- **All config in the web UI**: OCR provider/key/model configured on `/settings`, stored locally, **takes effect immediately, no restart**
- **Reuses Anki scheduling**: doesn't reinvent the algorithm — dictation results translate into 4-button ease fed to SM-2
- **Local-only tool**: listens on 127.0.0.1, no auth, data in `~/.paperback/`

## Screenshots

**Home** — pick deck + count, recent sessions at a glance
![Home](docs/screenshots/index.png)

**Worksheet** — print and write from memory, layout adjustable (font / orientation / columns)
![Worksheet](docs/screenshots/worksheet.png)

**Worksheet (print output)** — what you actually get on paper / as PDF
![Worksheet print output](docs/screenshots/worksheet_print.png)

**Manual grading** — keyboard 1–4, `Space` to toggle answer
![Grading](docs/screenshots/grade.png)

## Requirements

1. **Anki Desktop** running
2. **[AnkiConnect](https://ankiweb.net/shared/info/2055492159)** plugin installed (default port 8765)
3. Python 3.10+ (fetched automatically by uv)

## Quick start

```bash
uv sync
uv run paperback          # listens on http://127.0.0.1:8000
```

Open <http://127.0.0.1:8000> in your browser.

## Workflow

1. **Home**: pick a deck + count (optionally check "Filter dictation cards" to exclude cards whose back is a Chinese gloss) → Generate. Deck / filter / count are remembered; recent sessions listed for resuming.
2. **Session page**:
   - Print "📄 Worksheet", write from memory on paper (top bar adjusts font/orientation/columns, remembered per deck)
   - Print "Answer key" if needed
3. **Grade**:
   - **Manual** (recommended): "Start grading" → `Space` to reveal answer → `1`–`4` to score (`Enter` defaults to **1 Again**, `Backspace` for previous)
   - **Photo OCR** (⚠️ testing, not recommended): "📸 Photo grading" → snap on phone → GLM recognizes + suggests ease → confirm (configure first at `/settings`; see [PHOTO_GRADING.md](PHOTO_GRADING.md))
4. Ease is written back to Anki in real time; network hiccups buffer to a pending queue, one-click retry once recovered.

## Supported card types

Uses AnkiConnect's rendered `question`/`answer` directly — **any note type works**:

| Type | Supported | Notes |
|---|---|---|
| Basic / Basic (and reversed card) | ✅ | Both directions correct |
| Cloze | ✅ | Custom blanks: underline on worksheet, highlight on answer key |
| Custom templates (vocab decks, etc.) | ✅ | As long as Anki can render front/back |
| Image Occlusion and image-based | ✅ technically | Whether occlusion suits dictation is up to you |

> Cards whose front renders empty are skipped at generation with a count notice. Card images are base64-embedded so they display in a standalone browser.

## Grade buttons

| Key | Rating | Anki ease |
|---|---|---|
| `1` | Again | 1 |
| `2` | Hard | 2 |
| `3` | Good | 3 |
| `4` | Easy | 4 |

Default = **1 Again** (strict mode: unless you actively confirm, treated as "didn't know").

## Photo grading (OCR, ⚠️ in testing, not recommended)

> ⚠️ Still in testing, **not recommended for daily use**. Manual grading is more reliable.

Snap the worksheet → GLM recognizes handwriting + suggests ease → confirm → write back (configure at `/settings`).

Details, model comparison, configuration and privacy in **[PHOTO_GRADING.md](PHOTO_GRADING.md)** (Chinese).

## Security

- Listens only on `127.0.0.1`, not exposed to network
- Before grading, verifies Anki's current profile/deck matches the session's; blocks on mismatch (prevents writing to the wrong profile)
- Session files written atomically + in-process lock, prevents multi-tab concurrent overwrites

## Configuration

OCR goes through the `/settings` web page (recommended). Others via env vars:

| Variable | Default | Notes |
|---|---|---|
| `PAPERBACK_ANKI_URL` | `http://localhost:8765` | AnkiConnect URL |
| `PAPERBACK_DATA_DIR` | `~/.paperback/sessions` | Session storage dir |
| `PAPERBACK_OCR_API_KEY` | (none) | OCR: vision LLM key (recommend `/settings` page) |
| `PAPERBACK_OCR_BASE_URL` | `https://open.bigmodel.cn/api/paas/v4` | OpenAI-compatible endpoint (default GLM) |
| `PAPERBACK_OCR_MODEL` | `glm-5v-turbo` | Vision model name |
| `PAPERBACK_OCR_MAX_IMAGE_PX` | `2000` | Long-edge compression threshold before upload |
| `PAPERBACK_OCR_RETAIN_DAYS` | `365` | Original photo retention days (auto-cleaned on startup) |

## Recovery after restart

Code / deps / session data all live on disk. Two steps to restart:

1. Start **Anki Desktop**, open your profile (make sure AnkiConnect is on 8765)
2. Start **Paperback**:
   ```bash
   uv run paperback          # http://127.0.0.1:8000
   ```

## Tests

```bash
uv run pytest          # 59 passed
```

## FAQ

**"Cannot connect to AnkiConnect" on the home page?**
- Make sure Anki Desktop is running and the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) plugin is installed (default port 8765)
- Make sure a profile is actually open (not sitting at Anki's profile picker)

**"No due cards" when generating?**
- Paperback only pulls **today's due** cards (`is:due`: due reviews + today's new + learning steps)
- If there genuinely aren't any today, it'll be empty — try another deck or come back tomorrow
- If "Filter dictation cards" is checked it may filter everything out (excludes cards with Chinese gloss on the back) — try unchecking it

**Photo grading doesn't work / where to get an OCR key?**
- Register at open.bigmodel.cn (GLM Zhipu) → create an API key
- Fill the key at "⚙ OCR Settings" on the home page (no shell `export` needed)
- ⚠️ OCR is a testing feature, **not recommended for daily use** — manual grading is more reliable

**Card images show blank / broken?**
- Paperback base64-embeds Anki media images, they should display
- If still blank, the media file itself is likely missing in Anki (use Anki's "Check Media" tool)
- Remote images (`http(s)://`) need network

**Print layout is off?**
- Adjust font / orientation / columns via the worksheet top bar (remembered per deck)
- Set paper / margins in the browser print dialog (`Ctrl+P` / `Cmd+P`)

**Grading defaults to "didn't know"?**
- By design — default is **1 Again** (strict mode); you must actively press `3` for Good. Forces active recall, avoids accidental passes.

**Is data lost after reboot?**
- No. Sessions persist in `~/.paperback/sessions/`. Restart Anki + `uv run paperback` to resume.

**Does it work with AnkiDroid / AnkiMobile / AnkiWeb?**
- No. Paperback uses AnkiConnect (HTTP), which only the **desktop Anki** has as a plugin.

## Tech stack

Python 3.10+ · FastAPI · Jinja2 · vanilla JS · Pillow (OCR preprocessing) · uv. No frontend framework, no build step.

## Docs

- [SPEC.md](SPEC.md) — main product spec (Chinese)
- [SPEC_OCR.md](SPEC_OCR.md) — photo grading (incl. model comparison; Chinese)
- [SPEC_LAYOUT.md](SPEC_LAYOUT.md) — layout options (Chinese)
- [SPEC_ADDON.md](SPEC_ADDON.md) — Anki add-on plan (on hold; Chinese)

## License

MIT
