/* LaTeX autocompletion for CodeMirror's show-hint addon.
 * - "\" + letters  -> common commands
 * - \begin{        -> environments (inserts the matching \end{})
 * - \ref{ / \cite{ -> labels and BibTeX keys collected from the project
 */
(function () {
  const COMMANDS = [
    "section{}", "subsection{}", "subsubsection{}", "paragraph{}", "chapter{}", "part{}",
    "section*{}", "subsection*{}",
    "textbf{}", "textit{}", "emph{}", "underline{}", "texttt{}", "textsc{}", "textsf{}", "textrm{}",
    "label{}", "ref{}", "eqref{}", "autoref{}", "cref{}", "Cref{}", "pageref{}",
    "cite{}", "citep{}", "citet{}", "citeauthor{}", "citeyear{}", "nocite{}",
    "footnote{}", "url{}", "href{}{}", "caption{}", "centering",
    "includegraphics[width=\\linewidth]{}", "input{}", "include{}", "graphicspath{{figures/}}",
    "usepackage{}", "documentclass{}", "title{}", "author{}", "date{}", "maketitle", "thanks{}",
    "tableofcontents", "listoffigures", "listoftables", "appendix",
    "bibliography{}", "bibliographystyle{}", "addbibresource{}", "printbibliography",
    "newcommand{}{}", "renewcommand{}{}", "newenvironment{}{}{}", "DeclareMathOperator{}{}",
    "item", "hline", "toprule", "midrule", "bottomrule", "cline{}", "multicolumn{}{}{}", "multirow{}{}{}",
    "newline", "newpage", "clearpage", "noindent", "vspace{}", "hspace{}", "smallskip", "medskip", "bigskip",
    "linewidth", "textwidth", "columnwidth",
    "frac{}{}", "dfrac{}{}", "sqrt{}", "sum", "prod", "int", "oint", "lim", "infty", "partial", "nabla",
    "left(", "right)", "cdot", "cdots", "ldots", "dots", "times", "leq", "geq", "neq", "approx", "equiv",
    "in", "notin", "subset", "subseteq", "cup", "cap", "forall", "exists", "rightarrow", "Rightarrow",
    "leftarrow", "Leftarrow", "leftrightarrow", "mapsto", "to",
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta", "theta", "lambda",
    "mu", "nu", "xi", "pi", "rho", "sigma", "tau", "phi", "varphi", "chi", "psi", "omega",
    "Gamma", "Delta", "Theta", "Lambda", "Pi", "Sigma", "Phi", "Psi", "Omega",
    "mathbb{}", "mathcal{}", "mathbf{}", "mathrm{}", "mathit{}", "boldsymbol{}", "operatorname{}",
    "hat{}", "bar{}", "tilde{}", "vec{}", "dot{}", "overline{}", "underbrace{}", "overbrace{}",
    "text{}", "quad", "qquad", "today", "LaTeX", "TeX",
  ];
  const ENVIRONMENTS = [
    "figure", "figure*", "table", "table*", "tabular", "tabularx", "equation", "equation*",
    "align", "align*", "gather", "multline", "split", "cases", "matrix", "pmatrix", "bmatrix",
    "itemize", "enumerate", "description", "abstract", "center", "flushleft", "flushright",
    "minipage", "quote", "quotation", "verbatim", "lstlisting", "algorithm", "algorithmic",
    "theorem", "lemma", "proof", "definition", "corollary", "proposition", "remark", "example",
    "frame", "block", "columns", "column", "document", "thebibliography", "subfigure", "tikzpicture",
  ];
  const SNIPPETS = {
    figure: "\\begin{figure}[ht]\n  \\centering\n  \\includegraphics[width=0.8\\linewidth]{}\n  \\caption{}\n  \\label{fig:}\n\\end{figure}",
    table: "\\begin{table}[ht]\n  \\centering\n  \\caption{}\n  \\label{tab:}\n  \\begin{tabular}{lcc}\n    \\toprule\n     &  &  \\\\\n    \\midrule\n     &  &  \\\\\n    \\bottomrule\n  \\end{tabular}\n\\end{table}",
    itemize: "\\begin{itemize}\n  \\item \n\\end{itemize}",
    enumerate: "\\begin{enumerate}\n  \\item \n\\end{enumerate}",
  };

  window.LatexHints = { keys: { labels: [], cites: [] } };

  function placeCursorInFirstBraces(cm, from, text) {
    const idx = text.indexOf("{}");
    const idx2 = text.indexOf("[]");
    let off = idx >= 0 ? idx + 1 : (idx2 >= 0 ? idx2 + 1 : text.length);
    // For multi-line snippets compute the line/ch position.
    const before = text.slice(0, off).split("\n");
    const line = from.line + before.length - 1;
    const ch = (before.length === 1 ? from.ch : 0) + before[before.length - 1].length;
    cm.setCursor({ line, ch });
  }

  function hint(cm) {
    const cur = cm.getCursor();
    const line = cm.getLine(cur.line);
    const left = line.slice(0, cur.ch);

    // \ref{..., \cite{a,b...
    let m = left.match(/\\(ref|eqref|autoref|cref|Cref|pageref|cite\w*|nocite)\*?(?:\[[^\]]*\])*\{([^}]*)$/);
    if (m) {
      const isCite = m[1].startsWith("cite") || m[1] === "nocite";
      const typed = m[2].split(",").pop().trimStart();
      const pool = isCite ? LatexHints.keys.cites : LatexHints.keys.labels;
      const list = pool.filter((k) => k.toLowerCase().includes(typed.toLowerCase()));
      return { list, from: CodeMirror.Pos(cur.line, cur.ch - typed.length), to: cur };
    }
    // \begin{...
    m = left.match(/\\begin\{([A-Za-z*]*)$/);
    if (m) {
      const typed = m[1];
      const startCh = cur.ch - typed.length - "\\begin{".length;
      const list = ENVIRONMENTS.filter((e) => e.startsWith(typed)).map((env) => {
        const indent = line.match(/^\s*/)[0];
        const snippet = SNIPPETS[env] ||
          `\\begin{${env}}\n${indent}  \n${indent}\\end{${env}}`;
        return {
          text: SNIPPETS[env] ? snippet.split("\n").join("\n" + indent) : snippet,
          displayText: env,
          hint(cm2, data, completion) {
            const from = CodeMirror.Pos(cur.line, startCh);
            // Swallow the "}" that auto-close-brackets inserted after the cursor.
            let to = data.to;
            if (cm2.getRange(to, CodeMirror.Pos(to.line, to.ch + 1)) === "}") to = CodeMirror.Pos(to.line, to.ch + 1);
            cm2.replaceRange(completion.text, from, to);
            if (SNIPPETS[env]) {
              placeCursorInFirstBraces(cm2, from, completion.text);
            } else {
              cm2.setCursor({ line: cur.line + 1, ch: indent.length + 2 });
            }
          },
        };
      });
      return { list, from: CodeMirror.Pos(cur.line, startCh), to: cur };
    }
    // \command
    m = left.match(/\\([A-Za-z]*)$/);
    if (m) {
      const typed = m[1];
      const from = CodeMirror.Pos(cur.line, cur.ch - typed.length);
      const list = COMMANDS.filter((c) => c.startsWith(typed)).map((c) => ({
        text: c,
        displayText: "\\" + c,
        hint(cm2, data, completion) {
          cm2.replaceRange(completion.text, from, data.to);
          placeCursorInFirstBraces(cm2, from, completion.text);
          if (/^(ref|eqref|autoref|cref|Cref|pageref|cite\w*)\{\}$/.test(c)) {
            setTimeout(() => cm2.showHint({ hint: LatexHints.hint, completeSingle: false }), 10);
          }
        },
      }));
      return { list, from, to: cur };
    }
    return null;
  }

  LatexHints.hint = hint;
})();
