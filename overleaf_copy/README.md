# Overleaf Copy — a local LaTeX editor for paper writing

An Overleaf-style editor that runs entirely on your own machine. Edit `.tex` / `.bib`
files in the browser (or its own desktop window), and compile with your local TeX
installation. There's no account and no compile-time limit, and it works offline.
Only the optional DOI/arXiv lookup, the public grammar service and the AI assistant
go online, and only when you use them.

```
┌──────────┬───────────────────────────┬─────────────────────────┐
│📁🕘✓☑🔍📚💬│  main.tex  intro.tex  ×   │  PDF | Logs & errors    │
│ figures/ │ 1 \documentclass{article} │  − Fit width ＋ ▲ 1/8 ▼ 🌙│
│ sections/│ 2 \usepackage{graphicx}   │    (PDF.js preview —    │
│ main.tex │ 3 ...                     │   double-click to jump  │
│ refs.bib │                           │    to the source)       │
│ Outline  │                           │                         │
└──────────┴───────────────────────────┴─────────────────────────┘
```

## Features

### Writing & editing
- **Editor** (CodeMirror, bundled for offline use): LaTeX highlighting, bracket matching and auto-closing, tabs,
  autosave, find/replace (`Ctrl+F`), outline, and light/dark themes.
- **Autocomplete**: commands after `\`, environments after `\begin{` (with figure/table snippets),
  `\ref{…}` labels and `\cite{…}` keys from your project.
- **Editor settings** (Tools → Editor settings): font, size, line height, **Vim / Emacs / Sublime key bindings**,
  wrapping, line numbers, auto-close, spell-check language, auto-compile delay.
- **Spell checking, offline**: words are checked in the browser with Hunspell dictionaries (en-US and en-GB included).
  LaTeX commands, maths, labels and comments are skipped. Right-click a word for suggestions or *Add to dictionary*.
  Add more languages by dropping `xx_YY.aff`/`.dic` into `static/vendor/dictionaries/xx_YY/`.
- **Grammar & style** (✓ Proofread panel): uses [LanguageTool](https://languagetool.org). Your prose is checked with
  the LaTeX markup understood, so suggestions land on the right words and you can apply them with one click.
  See [Grammar checking](#grammar-checking) for setup.
- **Equation preview**: put the cursor inside `$…$`, `\[…\]`, `equation`, `align`, … to see it rendered (KaTeX).
  Your `\newcommand` macros are used.
- **Paste images**: paste a screenshot or plot (or drag an image onto the editor). It's saved to `figures/`, and
  a complete `figure` environment is inserted, adding `\usepackage{graphicx}` if needed.
- **Table generator** (Tools → Insert table): paste cells from Excel/Google Sheets, CSV or a Markdown table to get
  a `booktabs` table with a caption and label. Pasting spreadsheet cells straight into the editor offers to convert them.
- **Search & replace across all files** (🔍 panel, `Ctrl+Shift+H`): supports regex, match case and whole word.
  A version is saved before *Replace all*, so you can undo it.
- **Notes to self** (💬 panel): click the narrow gutter left of the line numbers (or press `Ctrl+Alt+M`) to attach
  a note to a line. Notes are stored outside your `.tex` files, so they never reach the PDF. They move with
  their line as you edit.
- **✨ AI writing assistant** (optional, `Ctrl+Shift+A`): rephrase, make concise, make more academic, fix grammar,
  simplify, improve the abstract, or give your own instruction. Also *Ask AI to explain* on compile errors. Only your
  selected text (and optionally its paragraph) is sent, only when you click. You review every suggestion before
  it replaces anything. See [AI assistant](#ai-assistant).

### Compiling & the PDF
- **Compile** with pdfLaTeX, XeLaTeX or LuaLaTeX via `latexmk`, which runs BibTeX/Biber and reruns as needed.
  `Ctrl+S` saves and compiles. You can also turn on **auto-compile**.
- **PDF viewer** (PDF.js): keeps your scroll position when you recompile, with zoom (remembered), page navigation
  and a **dark PDF** mode.
  - **SyncTeX both ways**: *→ PDF* highlights the cursor's location in the PDF. **Double-click the PDF** to jump to
    the matching source line.
- **Errors in plain English**: each error and warning links to its file and line, with an explanation and the
  likely fix (💡). Errors are also marked in the editor.
- **To-do list** (☑ panel): collects `% TODO`/`FIXME` comments, `\todo{}` notes, literal `??` and undefined
  references in one clickable list.

### References
- **📚 References panel**: browse and search your `.bib` entries, then click one to cite it at the cursor
  (it's added to an existing `\cite{…}` if you're inside one).
- **Add by DOI or arXiv ID**: paste `10.1145/…`, `2101.00001` or a doi.org/arxiv.org link to fetch clean BibTeX.
  This needs internet. Pasting BibTeX directly also works offline.
- **Bibliography check**: finds cited-but-missing keys, duplicate keys, likely duplicate entries (same title/DOI),
  missing required fields, unused entries, and acronyms that need `{braces}`.

### Versions, submission & backup
- **🕘 Version history**: versions are saved automatically when you compile (at most one every 2 minutes) and every 5 minutes while editing,
  and you can save **named versions** ("Submitted to ICML"). You can compare any two versions file by file, then
  **restore one file or the whole project**. A restore saves the current state first, so it can be undone.
- **Changes PDF (latexdiff)**: one click makes a PDF marking everything added and deleted since an earlier
  version, which journals ask for when you resubmit.
- **Submission check**: compiles, then checks for errors, undefined references and duplicate labels, the
  **page limit**, **non-embedded / Type 3 fonts**, text in the margins, leftover TODOs, **double-blind anonymity**
  (author block, e-mails, acknowledgements, self-references, your names, PDF metadata), awkward file names and
  sizes, and that the `.bbl` exists.
- **Export for arXiv**: makes a zip with the compiled `.bbl`, only the files LaTeX actually used, and comments
  stripped. It adds `00README.json` when you use XeLaTeX or LuaLaTeX.
- **Backup** (Tools → Backup settings): one click, or automatically after compiling, to
  - a folder such as Dropbox, OneDrive or a USB drive, as dated `.zip` files (older ones are pruned), and/or
  - **git**: commit, and optionally push to a remote such as a private GitHub repository.
- **Writing stats & goals**: words per section, words written per day (30-day chart), a daily goal,
  a target length, and a **deadline countdown** in the top bar.

### Projects
- Dashboard: create, rename, duplicate, delete, download `.zip`, and **import a zip** (e.g. Overleaf → Menu →
  Download → Source).
- **Templates**: research paper, IEEE conference, ACM (`acmart`), Elsevier (`elsarticle`), Springer LNCS,
  arXiv preprint, thesis/dissertation, response-to-reviewers letter, Beamer slides, and blank.
  Add your own by putting a folder in `project_templates/` (an optional `TEMPLATE.txt` holds the description).

## Requirements

1. **Python 3.9+**
2. **A TeX distribution**:
   - Windows: [MiKTeX](https://miktex.org/) or [TeX Live](https://tug.org/texlive/)
   - macOS: [MacTeX](https://tug.org/mactex/)
   - Linux: `sudo apt install texlive-full latexmk`
     - For a smaller install: `texlive-latex-extra texlive-publishers texlive-fonts-extra texlive-bibtex-extra biber latexmk latexdiff`

   Optional extras that ship with full TeX/poppler installs: `latexdiff` (changes PDF), `texcount` (accurate word count),
   `synctex` (PDF ↔ source), and `pdffonts`/`pdfinfo` from poppler-utils (font and page checks).
3. Optional: `git` (backups), and an Anthropic API key (AI assistant).

## Run it

macOS / Linux:

```bash
cd overleaf_copy
./run.sh
```

Windows: double-click `run.bat`.

Or do it manually:

```bash
pip install -r requirements.txt
python app.py
```

Your browser opens at <http://127.0.0.1:5000>.

| Flag | Default | Meaning |
|---|---|---|
| `--port 8080` | `5000` | Port to serve on |
| `--projects-dir ~/papers` | `./projects` | Where projects are stored (also `OVERLEAF_COPY_PROJECTS`) |
| `--host 0.0.0.0` | `127.0.0.1` | Expose on your LAN. **Only on a trusted network; there's no login.** |
| `--no-browser` | | Don't open a browser tab |

`OVERLEAF_COPY_TIMEOUT` sets the maximum compile time in seconds (default 180).

### Desktop app

To run it in its own window instead of a browser tab:

```bash
pip install -r requirements.txt -r requirements-desktop.txt
python desktop.py
```

To build a double-clickable app with [PyInstaller](https://pyinstaller.org). On Windows, use `;` instead of `:` in `--add-data`:

```bash
pip install pyinstaller
pyinstaller --name "Overleaf Copy" --windowed --noconfirm \
  --add-data "static:static" --add-data "project_templates:project_templates" \
  --hidden-import features.history --hidden-import features.proofing --hidden-import features.submission \
  --hidden-import features.search --hidden-import features.references --hidden-import features.stats \
  --hidden-import features.notes --hidden-import features.backup --hidden-import features.ai \
  desktop.py
```

The packaged app stores projects in `~/Documents/OverleafCopy`. It still uses your installed TeX distribution.

### Grammar checking

LanguageTool runs as a small local server, which keeps your text on your machine:

```bash
docker run -d -p 8081:8010 erikvl87/languagetool
```

You can also download LanguageTool and run `java -cp languagetool-server.jar org.languagetool.server.HTTPServer --port 8081`.

If you're happy to send text over the internet instead, choose *Public api.languagetool.org* in Tools → Editor settings.

### AI assistant

```bash
pip install -r requirements-ai.txt
```

Then add an Anthropic API key in Tools → *AI assistant settings*. Alternatively, set `ANTHROPIC_API_KEY` before starting the app.
The key is stored in `~/.overleaf_copy/config.json` (owner-only permissions), never in your projects.

It uses Claude Opus 5.5 by default, with adjustable effort. **Server-side fallbacks are enabled**: if Claude declines
a request, the API retries it on Anthropic's recommended fallback model instead of returning nothing.
You can change the model in settings.

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl/Cmd + S` or `Ctrl/Cmd + Enter` | Save and recompile |
| `Ctrl + Space` | Autocomplete |
| `Ctrl/Cmd + B` / `I` | Bold / italic |
| `Ctrl/Cmd + /` | Toggle `%` comment |
| `Ctrl/Cmd + F`, `Shift+Ctrl/Cmd+F`, `Alt+G` | Find, replace, go to line |
| `Ctrl/Cmd + Shift + H` | Search & replace in all files |
| `Ctrl/Cmd + Alt + M` | Note on the current line |
| `Ctrl/Cmd + Shift + A` | AI writing assistant |
| Double-click in PDF | Jump to the source line |

## How it works

```
overleaf_copy/
├── app.py                # Flask app: projects, files, compile, PDF, SyncTeX
├── core.py               # shared helpers: paths, settings, latexmk, log parsing
├── desktop.py            # native-window launcher (pywebview)
├── features/             # one module per feature (Flask blueprints)
│   ├── history.py        #   versions, diffs, restore, latexdiff
│   ├── proofing.py       #   dictionaries list, LanguageTool proxy
│   ├── submission.py     #   checklist, arXiv export, to-do scanner
│   ├── search.py         #   project-wide search & replace
│   ├── references.py     #   .bib parser, linter, DOI/arXiv lookup
│   ├── stats.py          #   word counts, goals, deadline
│   ├── notes.py          #   line notes
│   ├── backup.py         #   folder / git backups
│   └── ai.py             #   Claude writing assistant
├── static/               # single-page UI (vanilla JS); static/features/*.js mirror the modules above
│   └── vendor/           # CodeMirror 5, PDF.js, KaTeX, Typo.js, Hunspell dictionaries (all bundled)
├── project_templates/
└── projects/             # your papers (git-ignored)
    └── my-paper/
        ├── main.tex …
        ├── .overleaf_copy.json   # main file, engine, goals, backup settings, word history
        ├── .overleaf_notes.json  # line notes
        ├── .history/             # version snapshots (de-duplicated, compressed)
        └── .build/               # aux files, log and PDF output
```

Each project is a normal folder, so you can open it in any other editor or sync it however you like.

## Security & privacy notes

- The server only listens on `127.0.0.1` by default, so only your computer can reach it.
- LaTeX runs with shell-escape disabled (TeX's default restricted mode).
- File paths are checked so they can't escape the project folder, and zip imports are protected against path traversal.
- Network use happens only when you click a DOI/arXiv lookup, the public grammar service or an AI action.
  Each of these says so in its dialog.
- Git backups include your line notes (`.overleaf_notes.json`). If you push to a remote, make it private.

## Licences of bundled components

CodeMirror 5 (MIT), PDF.js (Apache-2.0), KaTeX (MIT), Typo.js (BSD-3-Clause), and the Hunspell en-US/en-GB
dictionaries (see `static/vendor/dictionaries/*/LICENSE`). Licence files are in `static/vendor/`.
