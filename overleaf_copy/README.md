# Overleaf Copy — a local LaTeX editor for paper writing

An Overleaf-style editor that runs entirely on your own machine. Edit `.tex` / `.bib`
files in the browser, upload figures, and compile to PDF with your locally
installed TeX distribution. No account, no internet connection, no compile-time limits.

```
┌──────────┬───────────────────────────┬─────────────────────────┐
│ Files    │  main.tex  intro.tex  ×   │  PDF | Logs & errors    │
│ figures/ │ 1 \documentclass{article} │                         │
│ sections/│ 2 \usepackage{graphicx}   │    (live PDF preview)   │
│ main.tex │ 3 ...                     │                         │
│ refs.bib │                           │                         │
│ Outline  │                           │                         │
└──────────┴───────────────────────────┴─────────────────────────┘
```

## Features

- **Projects dashboard**: create, rename, duplicate, delete, and download projects as `.zip`.
- **Templates**: generic research paper (with BibTeX), IEEE conference (`IEEEtran`),
  Beamer presentation, and a blank article. Add your own by dropping a folder into
  `project_templates/`.
- **Import from Overleaf**: in Overleaf use *Menu → Download → Source*, then use
  *Import .zip* here.
- **Editor** (CodeMirror, bundled for offline use):
  - LaTeX syntax highlighting, bracket matching, auto-closing of `{}`, `[]` and `$$`.
  - Autocomplete for commands (`\`), environments (`\begin{` inserts figure and table
    snippets), `\ref{`/`\eqref{` labels and `\cite{` keys taken from your `.bib` files.
  - Multiple tabs, autosave, find/replace, document outline, light and dark themes.
- **Compile** with pdfLaTeX, XeLaTeX or LuaLaTeX via `latexmk`, which runs BibTeX/Biber
  and reruns as needed. Without `latexmk` there's a built-in fallback pipeline.
  - Errors and warnings are parsed from the log. Click one to jump to the file and line;
    the editor also marks those lines.
  - The PDF preview jumps to the page you're editing (SyncTeX).
  - Optional **auto-compile** after you stop typing.
- **File management**: new files and folders, drag and drop to move, upload images by
  drag and drop from your desktop, rename, delete, and preview images and PDFs.
- **Word count** (uses `texcount` if installed).

## Requirements

1. **Python 3.9+**
2. **A TeX distribution** that provides `pdflatex` (ideally also `latexmk`):
   - Windows: [MiKTeX](https://miktex.org/) or [TeX Live](https://tug.org/texlive/)
   - macOS: [MacTeX](https://tug.org/mactex/)
   - Linux: `sudo apt install texlive-full latexmk`, or `texlive-latex-extra texlive-publishers
     texlive-bibtex-extra biber latexmk` for a smaller install

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

Options:

| Flag | Default | Meaning |
|---|---|---|
| `--port 8080` | `5000` | Port to serve on |
| `--projects-dir ~/papers` | `./projects` | Where projects are stored (also `OVERLEAF_COPY_PROJECTS` env var) |
| `--host 0.0.0.0` | `127.0.0.1` | Expose on your LAN. **Only do this on a trusted network; there's no login.** |
| `--no-browser` | | Don't open a browser tab automatically |

`OVERLEAF_COPY_TIMEOUT` sets the maximum compile time in seconds (default 180).

## Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl/Cmd + S` or `Ctrl/Cmd + Enter` | Save and recompile |
| `Ctrl + Space` | Show autocomplete |
| `Ctrl/Cmd + B` / `I` | Bold / italic (`\textbf{}` / `\textit{}`) |
| `Ctrl/Cmd + /` | Toggle `%` comment |
| `Ctrl/Cmd + F`, `Shift + Ctrl/Cmd + F` | Find, replace |
| `Alt + G` | Jump to line |

## How it works

```
overleaf_copy/
├── app.py                # Flask server: file API, compile (latexmk), log parsing, SyncTeX
├── static/               # Single-page UI (vanilla JS + bundled CodeMirror 5)
│   ├── index.html
│   ├── app.js
│   ├── latex-hints.js    # autocomplete
│   └── style.css
├── project_templates/    # Starting points for new projects
└── projects/             # Your papers (created on first run; git-ignored)
    └── my-paper/
        ├── main.tex
        ├── .overleaf_copy.json   # main file + engine settings
        └── .build/               # aux files, log and PDF output
```

Each project is just a normal folder, so you can also open it in any other editor,
put it under git, or sync it with Dropbox or OneDrive. Build artefacts stay in `.build/`
so your source folder stays clean.

**Version history**: Overleaf's history feature isn't replicated. Use git instead: run
`git init` inside a project folder, or remove `projects/` from `.gitignore` to track
papers in this repository.

## Security notes

- The server only listens on `127.0.0.1` by default, so only your computer can reach it.
- LaTeX runs with shell-escape disabled (TeX's default restricted mode).
- All file paths are checked so they can't escape the project folder, and zip imports
  are protected against path traversal.

## Licence

CodeMirror 5 is bundled under the MIT licence (`static/vendor/codemirror/LICENSE`).
