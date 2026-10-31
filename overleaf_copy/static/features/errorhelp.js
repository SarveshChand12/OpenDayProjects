/* Plain-English explanations for common LaTeX errors and warnings, shown under each log entry. */
(() => {
  const OC = window.OC;
  const HELP = [
    [/Undefined control sequence/, "LaTeX doesn't know a command on this line.",
      "Check the spelling of the command (e.g. \\textbf, not \\textbold). If it comes from a package, add \\usepackage{…} to the preamble (e.g. \\todo needs todonotes, \\url needs url or hyperref)."],
    [/File `([^']+)' not found/, "A file that LaTeX needs is missing.",
      "If it ends in .sty or .cls, the package isn't installed — install it with your TeX distribution's package manager (tlmgr install <name> / MiKTeX Console). If it's your own file, check the name and folder (paths are case-sensitive)."],
    [/Missing \$ inserted/, "Maths was used outside maths mode (or text inside maths).",
      "Wrap symbols like _ ^ \\alpha in $…$. To write a literal underscore in text use \\_ ."],
    [/Missing \} inserted|Extra \}, or forgotten|Too many \}'s/, "The braces { } don't balance.",
      "Count the { and } around this line — one is missing or extra. The editor highlights matching brackets when the cursor is next to one."],
    [/Runaway argument|File ended while scanning/, "A command's argument never ends — usually a missing }.",
      "Look above this line for an opening { that was never closed."],
    [/Environment (\S+) undefined/, "Unknown environment in \\begin{…}.",
      "Check the spelling, or load the package that defines it (e.g. align → amsmath, algorithm → algorithm2e or algorithm)."],
    [/\\begin\{(\S+)\} on input line \d+ ended by \\end\{(\S+)\}|ended by \\end/, "A \\begin{…} doesn't match its \\end{…}.",
      "Environments must close in the reverse order they were opened; check the names match."],
    [/Misplaced alignment tab character &/, "An & appears outside a table or align environment.",
      "To print an ampersand write \\& ."],
    [/There's no line here to end/, "A \\\\ line break appears where there's no line to break (e.g. after a blank line).",
      "Remove the \\\\ or use a blank line to start a new paragraph instead."],
    [/Paragraph ended before (\S+) was complete/, "A blank line appears inside a command's argument.",
      "Remove the blank line, or check for a missing }."],
    [/Double superscript|Double subscript/, "Two ^ or _ in a row in maths (x^2^3).",
      "Group them with braces: x^{2^3} or {x^2}^3."],
    [/Illegal unit of measure/, "A length is missing its unit.",
      "Write 0.5\\linewidth or 3cm, not 0.5 or 3."],
    [/Citation `([^']+)'.* undefined/, "A \\cite key isn't in your bibliography.",
      "Check the key matches an entry in your .bib file exactly (case-sensitive), then recompile. Use Tools → Check bibliography to list all mismatches."],
    [/Reference `([^']+)'.* undefined/, "A \\ref points to a \\label that doesn't exist.",
      "Check the label name; recompile once more if you just added it (LaTeX needs two passes)."],
    [/There were undefined references/, "Some references or citations couldn't be resolved.",
      "See the individual warnings; they appear as ?? in the PDF."],
    [/Label `([^']+)' multiply defined/, "The same \\label is used twice.", "Give each figure/section/equation a unique label."],
    [/Overfull \\hbox/, "A line is too wide and sticks into the margin.",
      "Rephrase, allow hyphenation of a long word (e.g. data\\-base), use \\url for URLs, or make a figure/table narrower."],
    [/Underfull \\hbox/, "A line is too loosely spaced (usually harmless).", "Often caused by \\\\ after a paragraph or by narrow columns. Safe to ignore unless it looks bad."],
    [/Float too large for page/, "A figure or table is taller than the page.", "Reduce its height (e.g. width=0.8\\linewidth) or split it."],
    [/Too many unprocessed floats/, "Too many figures/tables are waiting to be placed.",
      "Add \\clearpage somewhere, use [htbp] placement, or load the placeins package and use \\FloatBarrier."],
    [/Font shape .* undefined|Font .* not found/, "A requested font isn't available.", "Install the font package or choose another font; XeLaTeX/LuaLaTeX can use system fonts with fontspec."],
    [/Unicode character .* not set up for use with LaTeX|Invalid UTF-8/, "A special character (e.g. from copy-paste) isn't supported by pdfLaTeX.",
      "Replace it with a LaTeX command (– → --, “ ” → `` ''), or switch the engine to XeLaTeX/LuaLaTeX."],
    [/I can't write on file|Permission denied/, "LaTeX couldn't write an output file.", "Close the PDF in other programs (Windows locks open files) and recompile, or use ⟲ to clean the build."],
    [/Emergency stop|Fatal error occurred, no output PDF/, "LaTeX stopped early because of the error above.", "Fix the first error in the list — later ones are often consequences of it."],
    [/LaTeX Error: Can be used only in preamble/, "A command that belongs before \\begin{document} is used after it.", "Move it (e.g. \\usepackage) into the preamble."],
    [/Option clash for package (\S+)/, "A package is loaded twice with different options.", "Load it once, or use \\PassOptionsToPackage{…}{pkg} before \\documentclass."],
    [/No \\author given/, "\\maketitle was used without \\author.", "Add \\author{…} to the preamble (or \\author{} for none)."],
  ];

  function explain(message) {
    for (const [re, what, fix] of HELP) if (re.test(message)) return { what, fix };
    return null;
  }

  OC.on("compiled", () => {
    const els = document.querySelectorAll("#issueList .issue");
    OC.state.issues.forEach((iss, i) => {
      const el = els[i];
      if (!el || el.querySelector(".help")) return;
      const h = explain(iss.message);
      if (!h) return;
      const d = OC.el(`<div class="help"><b>💡 ${OC.escapeHtml(h.what)}</b><br>${OC.escapeHtml(h.fix)}</div>`);
      el.appendChild(d);
    });
  });
  OC.errorHelp = { explain };
})();
