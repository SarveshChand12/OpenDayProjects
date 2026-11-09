"""Convert a Word .docx file to LaTeX with only the Python standard library.

Handles: title/author, headings (numbering like "1." or "IV." stripped), abstract,
bold/italic/underline/strike/super-/subscript/small caps, bulleted & numbered lists
(nested), tables (merged cells, header rule), images with captions, footnotes,
hyperlinks, page breaks, Word equations (OMML -> LaTeX maths), and a numbered
reference list (-> thebibliography, with "[1]"-style citations turned into \\cite).

Usage:
    result = convert_docx(path_or_bytes, docclass="article")
    result.tex      -> the .tex source
    result.media    -> {"figures/image1.png": bytes, ...}
    result.warnings -> human-readable notes about things that need checking
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "v": "urn:schemas-microsoft-com:vml",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "dc": "http://purl.org/dc/elements/1.1/",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}
W = "{%s}" % NS["w"]
M = "{%s}" % NS["m"]
R = "{%s}" % NS["r"]


def q(tag: str) -> str:
    """'w:p' -> '{namespace}p'"""
    prefix, local = tag.split(":")
    return "{%s}%s" % (NS[prefix], local)


def wval(el, tag: str, default=None):
    """Value of <tag w:val="..."> child, e.g. wval(rPr, 'w:b')."""
    if el is None:
        return default
    c = el.find(tag, NS)
    if c is None:
        return default
    return c.get(W + "val", "true")


def on(el, tag: str) -> bool:
    v = wval(el, tag)
    return v is not None and v not in ("0", "false", "none")


# --------------------------------------------------------------------------- #
# Text escaping
# --------------------------------------------------------------------------- #
TEXT_MAP = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$", "&": r"\&", "#": r"\#",
    "%": r"\%", "_": r"\_", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
    "\u201c": "``", "\u201d": "''", "\u2018": "`", "\u2019": "'", "\u2013": "--", "\u2014": "---",
    "\u2026": r"\ldots{}", "\u00a0": "~", "\u2009": r"\,", "\u200b": "", "\u00ad": r"\-",
    "\u2022": r"\textbullet{}", "\u00b0": r"\textdegree{}", "\u00b1": r"$\pm$", "\u00d7": r"$\times$",
    "\u2264": r"$\leq$", "\u2265": r"$\geq$", "\u2260": r"$\neq$", "\u2248": r"$\approx$",
    "\u2192": r"$\rightarrow$", "\u2190": r"$\leftarrow$", "\u2194": r"$\leftrightarrow$",
    "\u21d2": r"$\Rightarrow$", "\u221e": r"$\infty$", "\u2208": r"$\in$", "\u2211": r"$\sum$",
    "\u00b5": r"$\mu$", "\u2032": r"$'$", "\u20ac": r"\texteuro{}", "\u00a9": r"\textcopyright{}",
    "\u00ae": r"\textregistered{}", "\u2122": r"\texttrademark{}", "\u00a7": r"\S{}", "\u00b6": r"\P{}",
    "\u2212": "--", "\u2010": "-", "\u2011": "-", "\u00ab": r"\guillemotleft{}", "\u00bb": r"\guillemotright{}",
}
GREEK = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ϵ": "epsilon", "ζ": "zeta",
    "η": "eta", "θ": "theta", "ϑ": "vartheta", "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu",
    "ν": "nu", "ξ": "xi", "π": "pi", "ρ": "rho", "σ": "sigma", "ς": "varsigma", "τ": "tau",
    "υ": "upsilon", "φ": "phi", "ϕ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
    "Γ": "Gamma", "Δ": "Delta", "Θ": "Theta", "Λ": "Lambda", "Ξ": "Xi", "Π": "Pi", "Σ": "Sigma",
    "Υ": "Upsilon", "Φ": "Phi", "Ψ": "Psi", "Ω": "Omega",
}
for ch, name in GREEK.items():
    TEXT_MAP.setdefault(ch, f"$\\{name}$")


def esc(text: str) -> str:
    return "".join(TEXT_MAP.get(c, c) for c in text)


# Unicode -> LaTeX inside maths
MATH_MAP = {
    "≤": r"\leq ", "≥": r"\geq ", "≠": r"\neq ", "≈": r"\approx ", "≡": r"\equiv ", "∼": r"\sim ",
    "±": r"\pm ", "∓": r"\mp ", "×": r"\times ", "÷": r"\div ", "·": r"\cdot ", "⋅": r"\cdot ", "∗": "*",
    "∞": r"\infty ", "∂": r"\partial ", "∇": r"\nabla ", "∈": r"\in ", "∉": r"\notin ", "⊂": r"\subset ",
    "⊆": r"\subseteq ", "⊃": r"\supset ", "⊇": r"\supseteq ", "∪": r"\cup ", "∩": r"\cap ", "∅": r"\emptyset ",
    "∀": r"\forall ", "∃": r"\exists ", "¬": r"\neg ", "∧": r"\wedge ", "∨": r"\vee ", "→": r"\rightarrow ",
    "←": r"\leftarrow ", "↔": r"\leftrightarrow ", "⇒": r"\Rightarrow ", "⇐": r"\Leftarrow ",
    "⇔": r"\Leftrightarrow ", "↦": r"\mapsto ", "∑": r"\sum ", "∏": r"\prod ", "∫": r"\int ",
    "∬": r"\iint ", "∮": r"\oint ", "√": r"\surd ", "∝": r"\propto ", "…": r"\ldots ", "⋯": r"\cdots ",
    "′": "'", "″": "''", "−": "-", "ℝ": r"\mathbb{R}", "ℕ": r"\mathbb{N}", "ℤ": r"\mathbb{Z}",
    "ℚ": r"\mathbb{Q}", "ℂ": r"\mathbb{C}", "ℓ": r"\ell ", "°": r"^{\circ}", "∘": r"\circ ",
    "⟨": r"\langle ", "⟩": r"\rangle ", "‖": r"\| ", "∣": r"\mid ", "⊥": r"\perp ", "∥": r"\parallel ",
    "{": r"\{", "}": r"\}", "%": r"\%", "#": r"\#", "&": r"\&", "_": r"\_", "$": r"\$", "\u00a0": " ",
    "⊕": r"\oplus ", "⊗": r"\otimes ", "≪": r"\ll ", "≫": r"\gg ", "≜": r"\triangleq ", "≔": ":=",
    "ˆ": r"\hat{}", "ħ": r"\hbar ",
}
for ch, name in GREEK.items():
    MATH_MAP[ch] = f"\\{name} "
NARY = {"∑": r"\sum", "∏": r"\prod", "∫": r"\int", "∬": r"\iint", "∭": r"\iiint", "∮": r"\oint",
        "⋃": r"\bigcup", "⋂": r"\bigcap", "∐": r"\coprod", "⋁": r"\bigvee", "⋀": r"\bigwedge"}
ACCENTS = {"̂": r"\hat", "̃": r"\tilde", "̄": r"\bar", "̇": r"\dot", "̈": r"\ddot", "⃗": r"\vec",
           "̌": r"\check", "́": r"\acute", "̀": r"\grave", "̆": r"\breve", "⃡": r"\overleftrightarrow"}
FUNCS = {"sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "exp", "lim", "max", "min", "sup",
         "inf", "det", "arg", "sinh", "cosh", "tanh", "arcsin", "arccos", "arctan", "gcd", "Pr", "deg", "dim", "ker"}


def math_text(t: str) -> str:
    return "".join(MATH_MAP.get(c, c) for c in t)


# --------------------------------------------------------------------------- #
# OMML (Word equations) -> LaTeX
# --------------------------------------------------------------------------- #
def omml(el) -> str:
    """Convert an <m:oMath> (or any OMML element) to LaTeX math."""
    if el is None:
        return ""
    tag = el.tag
    kids = list(el)

    def sub(name):
        return omml(el.find(f"m:{name}", NS))

    def chrval(name, default=""):
        pr = el.find(f"m:{name}Pr", NS)
        c = pr.find("m:chr", NS) if pr is not None else None
        return c.get(M + "val") if c is not None and c.get(M + "val") is not None else default

    if tag in (M + "oMath", M + "e", M + "num", M + "den", M + "sub", M + "sup", M + "deg",
               M + "fName", M + "lim", M + "oMathPara"):
        if tag == M + "oMathPara":
            return r" \\ ".join(omml(k) for k in el.findall("m:oMath", NS))
        return "".join(omml(k) for k in kids)
    if tag == M + "r":
        text = "".join(t.text or "" for t in el.findall("m:t", NS))
        rpr = el.find("m:rPr", NS)
        if rpr is not None and rpr.find("m:nor", NS) is not None:
            return r"\text{" + esc(text) + "}"
        sty = rpr.find("m:sty", NS) if rpr is not None else None
        if text.strip() in FUNCS:
            return "\\" + text.strip() + " "
        conv = math_text(text)
        if sty is not None and sty.get(M + "val") == "p" and re.fullmatch(r"[A-Za-z]{2,}", text):
            return r"\mathrm{" + text + "}"
        if sty is not None and sty.get(M + "val") == "b":
            return r"\mathbf{" + conv + "}"
        scr = rpr.find("m:scr", NS) if rpr is not None else None
        font = {"script": r"\mathcal", "double-struck": r"\mathbb", "fraktur": r"\mathfrak",
                "sans-serif": r"\mathsf", "monospace": r"\mathtt"}.get(scr.get(M + "val") if scr is not None else "")
        if font:
            return font + "{" + conv + "}"
        return conv
    if tag == M + "f":
        pr = el.find("m:fPr/m:type", NS)
        kind = pr.get(M + "val") if pr is not None else "bar"
        if kind == "lin":
            return f"{sub('num')}/{sub('den')}"
        if kind == "noBar":
            return r"\genfrac{}{}{0pt}{}{" + sub("num") + "}{" + sub("den") + "}"
        return r"\frac{" + sub("num") + "}{" + sub("den") + "}"
    if tag == M + "sSup":
        return "{" + sub("e") + "}^{" + sub("sup") + "}"
    if tag == M + "sSub":
        return "{" + sub("e") + "}_{" + sub("sub") + "}"
    if tag == M + "sSubSup":
        return "{" + sub("e") + "}_{" + sub("sub") + "}^{" + sub("sup") + "}"
    if tag == M + "sPre":
        return "{}_{" + sub("sub") + "}^{" + sub("sup") + "}" + sub("e")
    if tag == M + "rad":
        deg = sub("deg")
        hide = el.find("m:radPr/m:degHide", NS)
        if deg and (hide is None or hide.get(M + "val") in ("0", "off")):
            return r"\sqrt[" + deg + "]{" + sub("e") + "}"
        return r"\sqrt{" + sub("e") + "}"
    if tag == M + "nary":
        op = NARY.get(chrval("nary", "∫"), r"\int")
        lo, hi = sub("sub"), sub("sup")
        s = op + (f"_{{{lo}}}" if lo else "") + (f"^{{{hi}}}" if hi else "")
        return s + " " + sub("e")
    if tag == M + "d":
        pr = el.find("m:dPr", NS)
        beg, end, sep = "(", ")", "|"
        if pr is not None:
            for name, default in (("begChr", "("), ("endChr", ")"), ("sepChr", "|")):
                c = pr.find(f"m:{name}", NS)
                if c is not None:
                    v = c.get(M + "val", "")
                    if name == "begChr":
                        beg = v
                    elif name == "endChr":
                        end = v
                    else:
                        sep = v
        delim = {"(": "(", ")": ")", "[": "[", "]": "]", "{": r"\{", "}": r"\}", "|": "|", "‖": r"\|",
                 "⟨": r"\langle", "⟩": r"\rangle", "⌊": r"\lfloor", "⌋": r"\rfloor", "⌈": r"\lceil",
                 "⌉": r"\rceil", "": "."}
        inner = (" " + MATH_MAP.get(sep, sep).strip() + " ").join(omml(e) for e in el.findall("m:e", NS))
        return r"\left" + delim.get(beg, beg) + " " + inner + r" \right" + delim.get(end, end)
    if tag == M + "acc":
        acc = ACCENTS.get(chrval("acc", "̂"), r"\hat")
        return acc + "{" + sub("e") + "}"
    if tag == M + "bar":
        pos = el.find("m:barPr/m:pos", NS)
        return (r"\underline{" if pos is not None and pos.get(M + "val") == "bot" else r"\overline{") + sub("e") + "}"
    if tag == M + "groupChr":
        ch = chrval("groupChr", "⏟")
        pos = el.find("m:groupChrPr/m:pos", NS)
        top = pos is not None and pos.get(M + "val") == "top"
        if ch in ("⏟", "⏞"):
            return (r"\overbrace{" if ch == "⏞" else r"\underbrace{") + sub("e") + "}"
        return (r"\overset{" if top else r"\underset{") + MATH_MAP.get(ch, ch) + "}{" + sub("e") + "}"
    if tag == M + "func":
        name = sub("fName").strip()
        if re.fullmatch(r"[A-Za-z]+", name):
            name = ("\\" + name) if name in FUNCS else r"\operatorname{" + name + "}"
        return name + " " + sub("e")
    if tag == M + "limLow":
        return "{" + sub("e") + "}_{" + sub("lim") + "}"
    if tag == M + "limUpp":
        return "{" + sub("e") + "}^{" + sub("lim") + "}"
    if tag == M + "m":
        rows = [" & ".join(omml(e) for e in mr.findall("m:e", NS)) for mr in el.findall("m:mr", NS)]
        return r"\begin{matrix} " + r" \\ ".join(rows) + r" \end{matrix}"
    if tag == M + "eqArr":
        rows = [omml(e) for e in el.findall("m:e", NS)]
        return r"\begin{aligned} " + r" \\ ".join(rows) + r" \end{aligned}"
    if tag in (M + "box", M + "borderBox", M + "phant"):
        return sub("e")
    if tag.endswith("Pr") or tag == M + "ctrlPr":
        return ""
    # Unknown wrapper: convert children
    return "".join(omml(k) for k in kids)


def tidy_math(s: str) -> str:
    s = re.sub(r"[ ]{2,}", " ", s).strip()
    s = re.sub(r"\{(\\?[A-Za-z0-9])\}(?=[_^])", r"\1", s)   # {x}^{2} -> x^{2}
    return s


# --------------------------------------------------------------------------- #
# Converter
# --------------------------------------------------------------------------- #
@dataclass
class Result:
    tex: str = ""
    media: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    title: str = ""
    stats: dict = field(default_factory=dict)


HEADING_CMDS = ["section", "subsection", "subsubsection", "paragraph", "subparagraph"]
NUMBER_PREFIX = re.compile(r"^\s*((\d+(\.\d+)*\.?)|([IVXLC]+\.)|([A-Z]\.)|(\d+\)))\s+")
CAPTION_PREFIX = re.compile(r"^\s*(fig(ure)?|table|tab)\.?\s*([0-9IVX]+|[A-Z])?\s*[:.\-–—]?\s*", re.I)
REF_HEADINGS = re.compile(r"^\s*(references|bibliography|works cited|literature cited|reference list)\s*$", re.I)
ABSTRACT_RE = re.compile(r"^\s*(abstract)\s*[:.\-–—]?\s*", re.I)
KEYWORDS_RE = re.compile(r"^\s*(keywords|key words|index terms)\s*[:.\-–—]\s*", re.I)
BIB_ITEM_RE = re.compile(r"^\s*\[(\d+)\]\s*")


def strip_label(text: str, label: str) -> str:
    """Remove a leading label such as 'Abstract---' even when it is wrapped in \\textbf{...}."""
    m = re.match(r"^((?:\\(?:textbf|emph|textsc|underline|textit)\{)*)\s*(?:" + label +
                 r")\s*(?:---|--|[:.\-])*\s*", text, re.I)
    if not m:
        return text
    opened = m.group(1).count("{")
    rest = text[m.end():]
    closing = re.match(r"^(\s*\}){%d}" % opened, rest) if opened else None
    if opened and closing:
        return rest[closing.end():].lstrip(" .:-")    # the label was the whole formatted run
    return (m.group(1) + rest) if opened else rest  # formatting continues into the text


AFFIL_RE = re.compile(r"universit|school|department|dept\.|institute|college|faculty|laborator|centre|center|"
                      r"@|\\href|company|inc\.|ltd|gmbh|corporation", re.I)


class DocxConverter:
    def __init__(self, data: bytes, docclass: str = "article"):
        self.z = zipfile.ZipFile(io.BytesIO(data))
        self.docclass = docclass
        self.res = Result()
        self.styles = self._load_styles()
        self.numbering = self._load_numbering()
        self.rels = self._load_rels("word/_rels/document.xml.rels")
        self.footnotes = self._load_notes("word/footnotes.xml", "footnote")
        self.endnotes = self._load_notes("word/endnotes.xml", "endnote")
        self.used_media: dict[str, str] = {}
        self.fig_count = 0
        self.packages = {"graphicx", "amsmath", "amssymb", "booktabs", "hyperref"}
        self.pending_caption: tuple[str, str] | None = None
        self.has_equations = False

    # ------------------------------------------------------------- loaders
    def _xml(self, name):
        try:
            return ET.fromstring(self.z.read(name))
        except KeyError:
            return None

    def _load_styles(self):
        styles = {}
        root = self._xml("word/styles.xml")
        if root is None:
            return styles
        for s in root.findall("w:style", NS):
            sid = s.get(W + "styleId")
            name_el = s.find("w:name", NS)
            based = s.find("w:basedOn", NS)
            outline = s.find("w:pPr/w:outlineLvl", NS)
            styles[sid] = {
                "name": (name_el.get(W + "val") if name_el is not None else sid or "").lower(),
                "based": based.get(W + "val") if based is not None else None,
                "outline": int(outline.get(W + "val")) if outline is not None else None,
                "rpr": s.find("w:rPr", NS),
                "numpr": s.find("w:pPr/w:numPr", NS),
            }
        return styles

    def style_name(self, sid):
        seen = 0
        while sid and seen < 10:
            st = self.styles.get(sid)
            if not st:
                return (sid or "").lower()
            if re.match(r"(heading \d|title|subtitle|caption|list|quote|intense quote|abstract|author|"
                        r"bibliography|footnote|toc|code|html preformatted)", st["name"]):
                return st["name"]
            sid = st["based"]
            seen += 1
        return self.styles.get(sid, {}).get("name", "") if sid else ""

    def heading_level(self, sid, ppr):
        name = self.style_name(sid)
        m = re.match(r"heading (\d)", name)
        if m:
            return int(m.group(1))
        lvl = ppr.find("w:outlineLvl", NS) if ppr is not None else None
        if lvl is not None and lvl.get(W + "val") not in (None, "9"):
            return int(lvl.get(W + "val")) + 1
        st = self.styles.get(sid)
        if st and st["outline"] is not None and st["outline"] < 9:
            return st["outline"] + 1
        return None

    def _load_numbering(self):
        root = self._xml("word/numbering.xml")
        result = {}
        if root is None:
            return result
        abstract = {}
        for an in root.findall("w:abstractNum", NS):
            levels = {}
            for lvl in an.findall("w:lvl", NS):
                fmt = lvl.find("w:numFmt", NS)
                levels[int(lvl.get(W + "ilvl"))] = fmt.get(W + "val") if fmt is not None else "decimal"
            abstract[an.get(W + "abstractNumId")] = levels
        for num in root.findall("w:num", NS):
            aid = num.find("w:abstractNumId", NS)
            if aid is not None:
                result[num.get(W + "numId")] = abstract.get(aid.get(W + "val"), {})
        return result

    def _load_rels(self, name):
        root = self._xml(name)
        rels = {}
        if root is None:
            return rels
        for r in root.findall("rel:Relationship", NS):
            rels[r.get("Id")] = (r.get("Target"), r.get("TargetMode"), r.get("Type", ""))
        return rels

    def _load_notes(self, name, kind):
        root = self._xml(name)
        notes = {}
        if root is None:
            return notes
        for n in root.findall(f"w:{kind}", NS):
            if n.get(W + "type") in ("separator", "continuationSeparator", "continuationNotice"):
                continue
            notes[n.get(W + "id")] = n
        return notes

    def core_props(self):
        root = self._xml("docProps/core.xml")
        if root is None:
            return {}
        out = {}
        for key, tag in (("title", "dc:title"), ("author", "dc:creator")):
            el = root.find(tag, NS)
            if el is not None and (el.text or "").strip():
                out[key] = el.text.strip()
        return out

    # ------------------------------------------------------------- inline
    def runs_to_tex(self, p, in_math_ok=True) -> str:
        """Convert the inline content of a paragraph (or hyperlink, etc.)."""
        pieces: list[tuple[str, tuple]] = []   # (text, formatting tuple) for merging
        out: list[str] = []

        def flush():
            if not pieces:
                return
            # Merge consecutive pieces with the same formatting
            merged = []
            for text, fmt in pieces:
                if merged and merged[-1][1] == fmt:
                    merged[-1] = (merged[-1][0] + text, fmt)
                else:
                    merged.append((text, fmt))
            for text, fmt in merged:
                out.append(self.wrap(text, fmt))
            pieces.clear()

        fld_depth = [0]
        fld_show = [True]

        def walk(el):
            for child in el:
                tag = child.tag
                if tag == W + "r":
                    run(child)
                elif tag == W + "hyperlink":
                    flush()
                    rid = child.get(R + "id")
                    anchor = child.get(W + "anchor")
                    inner = self.runs_to_tex(child)
                    if rid and rid in self.rels:
                        url = self.rels[rid][0]
                        out.append(r"\href{" + url.replace("%", r"\%").replace("#", r"\#") + "}{" + inner + "}")
                    elif anchor:
                        out.append(inner)
                    else:
                        out.append(inner)
                elif tag in (W + "smartTag", W + "customXml", W + "sdt", W + "sdtContent", W + "ins",
                             W + "fldSimple", q("mc:AlternateContent"), q("mc:Choice")):
                    walk(child)
                elif tag == W + "del":
                    continue
                elif tag == M + "oMath":
                    flush()
                    self.has_equations = True
                    out.append("$" + tidy_math(omml(child)) + "$")
                elif tag == M + "oMathPara":
                    flush()
                    self.has_equations = True
                    out.append("\x00DISPLAY\x00" + tidy_math(omml(child)) + "\x00/DISPLAY\x00")

        def run(r):
            rpr = r.find("w:rPr", NS)
            fmt = self.run_format(rpr, r)
            for child in r:
                tag = child.tag
                if tag == W + "fldChar":
                    t = child.get(W + "fldCharType")
                    if t == "begin":
                        fld_depth[0] += 1
                        fld_show[0] = False
                    elif t == "separate":
                        fld_show[0] = True
                    elif t == "end":
                        fld_depth[0] = max(0, fld_depth[0] - 1)
                        fld_show[0] = True
                    continue
                if tag == W + "instrText":
                    continue
                if not fld_show[0]:
                    continue
                if tag == W + "t":
                    pieces.append((child.text or "", fmt))
                elif tag == W + "tab":
                    pieces.append(("\t", fmt))
                elif tag in (W + "br", W + "cr"):
                    flush()
                    out.append("\n\\clearpage\n" if child.get(W + "type") == "page" else "\\\\\n")
                elif tag == W + "noBreakHyphen":
                    pieces.append(("-", fmt))
                elif tag == W + "sym":
                    pieces.append(("", fmt))
                elif tag in (W + "footnoteReference", W + "endnoteReference"):
                    flush()
                    notes = self.footnotes if tag == W + "footnoteReference" else self.endnotes
                    note = notes.get(child.get(W + "id"))
                    if note is not None:
                        body = " ".join(self.runs_to_tex(np) for np in note.findall("w:p", NS)).strip()
                        out.append(r"\footnote{" + body + "}")
                elif tag in (W + "drawing", W + "pict", W + "object"):
                    flush()
                    img = self.image(child)
                    if img:
                        out.append(img)
                elif tag == q("mc:AlternateContent"):
                    choice = child.find("mc:Choice", NS)
                    if choice is not None:
                        for d in choice:
                            if d.tag == W + "drawing":
                                flush()
                                img = self.image(d)
                                if img:
                                    out.append(img)
                elif tag == M + "oMath":
                    flush()
                    out.append("$" + tidy_math(omml(child)) + "$")

        walk(p)
        flush()
        text = "".join(out)
        text = text.replace("\t", r"\quad ")
        return text

    def run_format(self, rpr, r):
        if rpr is None:
            return ()
        fmt = []
        if on(rpr, "w:b"):
            fmt.append("b")
        if on(rpr, "w:i"):
            fmt.append("i")
        u = wval(rpr, "w:u")
        if u and u not in ("none", "0"):
            fmt.append("u")
        if on(rpr, "w:strike") or on(rpr, "w:dstrike"):
            fmt.append("s")
        if on(rpr, "w:smallCaps"):
            fmt.append("sc")
        va = wval(rpr, "w:vertAlign")
        if va == "superscript":
            fmt.append("sup")
        elif va == "subscript":
            fmt.append("sub")
        font = rpr.find("w:rFonts", NS)
        if font is not None and re.search(r"courier|consolas|mono|menlo", font.get(W + "ascii", "") or "", re.I):
            fmt.append("tt")
        return tuple(fmt)

    def wrap(self, text: str, fmt: tuple) -> str:
        if not text:
            return ""
        caps = text
        s = esc(caps)
        if not s.strip():
            return s
        lead = s[: len(s) - len(s.lstrip())]
        trail = s[len(s.rstrip()):]
        core = s.strip()
        for f in fmt:
            if f == "b":
                core = r"\textbf{" + core + "}"
            elif f == "i":
                core = r"\emph{" + core + "}"
            elif f == "u":
                core = r"\underline{" + core + "}"
            elif f == "s":
                self.packages.add("ulem")
                core = r"\sout{" + core + "}"
            elif f == "sc":
                core = r"\textsc{" + core + "}"
            elif f == "sup":
                core = r"\textsuperscript{" + core + "}"
            elif f == "sub":
                core = r"\textsubscript{" + core + "}"
            elif f == "tt":
                core = r"\texttt{" + core + "}"
        return lead + core + trail

    # ------------------------------------------------------------- images
    def image(self, el) -> str:
        blip = el.find(".//a:blip", NS)
        rid = None
        if blip is not None:
            rid = blip.get(R + "embed") or blip.get(R + "link")
        else:
            vimg = el.find(".//v:imagedata", NS)
            if vimg is not None:
                rid = vimg.get(R + "id")
        if not rid or rid not in self.rels:
            if el.find(".//a:graphicData", NS) is not None and blip is None:
                self.res.warnings.append("A chart, SmartArt or text box couldn't be converted — "
                                         "export it from Word as an image and insert it.")
                return "\n% [Chart / SmartArt / drawing from Word omitted — insert it as an image]\n"
            return ""
        target = self.rels[rid][0]
        src = posixpath.normpath(posixpath.join("word", target)) if not target.startswith("/") else target.lstrip("/")
        name = posixpath.basename(src)
        dest = self.used_media.get(src)
        if dest is None:
            try:
                data = self.z.read(src)
            except KeyError:
                self.res.warnings.append(f"Image {name} is linked, not embedded, and couldn't be included.")
                return ""
            stem, ext = posixpath.splitext(name)
            ext = ext.lower()
            dest = f"figures/{stem}{ext}"
            self.res.media[dest] = data
            self.used_media[src] = dest
            if ext in (".emf", ".wmf", ".tif", ".tiff", ".bmp", ".svg"):
                self.res.warnings.append(f"{dest}: {ext[1:].upper()} images can't be used by pdfLaTeX — "
                                         "convert it to PNG or PDF and update the \\includegraphics line.")
        width = ""
        ext_el = el.find(".//wp:extent", NS)
        if ext_el is not None and ext_el.get("cx"):
            inches = int(ext_el.get("cx")) / 914400
            frac = min(1.0, inches / 6.3)
            width = f"width={frac:.2f}\\linewidth"
        return "\x00IMG\x00" + (width or "width=0.8\\linewidth") + "\x00" + dest + "\x00/IMG\x00"

    # ------------------------------------------------------------- tables
    def table(self, tbl) -> str:
        grid = [int(g.get(W + "w", "0") or 0) for g in tbl.findall("w:tblGrid/w:gridCol", NS)]
        rows = []
        for tr in tbl.findall("w:tr", NS):
            cells = []
            for tc in tr.findall("w:tc", NS):
                tcpr = tc.find("w:tcPr", NS)
                span = int(wval(tcpr, "w:gridSpan", "1") or 1)
                vmerge = tcpr.find("w:vMerge", NS) if tcpr is not None else None
                continued = vmerge is not None and vmerge.get(W + "val") in (None, "continue")
                paras = []
                for child in tc:
                    if child.tag == W + "p":
                        paras.append(self.runs_to_tex(child).replace("\x00DISPLAY\x00", "$").replace("\x00/DISPLAY\x00", "$"))
                    elif child.tag == W + "tbl":
                        paras.append("[nested table]")
                        self.res.warnings.append("A table inside a table was simplified.")
                text = r" \newline ".join(p.strip() for p in paras if p.strip())
                text = re.sub(r"\x00IMG\x00(.*?)\x00(.*?)\x00/IMG\x00", r"\\includegraphics[width=\\linewidth]{\2}", text)
                cells.append({"text": "" if continued else text, "span": span})
            rows.append(cells)
        if not rows:
            return ""
        ncols = max(sum(c["span"] for c in r) for r in rows)
        if len(grid) != ncols:
            grid = [1] * ncols
        total = sum(grid) or 1
        # Column types: short content -> l/c/r, long content -> p{} from Word's column widths
        col_len = [0] * ncols
        numeric = [True] * ncols
        for ri, r in enumerate(rows):
            ci = 0
            for c in r:
                if c["span"] == 1 and ci < ncols:
                    col_len[ci] = max(col_len[ci], len(re.sub(r"\\[a-z]+\{|\}", "", c["text"])))
                    if ri > 0 and c["text"] and not re.fullmatch(r"[-+$]?\(?[\d.,]+%?\)?\\?%?", c["text"].strip()):
                        numeric[ci] = False
                ci += c["span"]
        spec = []
        long_cols = sum(1 for L in col_len if L > 25)
        for i in range(ncols):
            if col_len[i] > 25 or (long_cols and sum(col_len) > 80):
                spec.append("p{%.2f\\linewidth}" % max(0.08, 0.95 * grid[i] / total - 0.02))
            else:
                spec.append("r" if numeric[i] and i > 0 else ("l" if i == 0 else "c"))
        lines = ["\\begin{tabular}{" + "".join(spec) + "}", "\\toprule"]
        for ri, r in enumerate(rows):
            parts = []
            for c in r:
                t = c["text"]
                if c["span"] > 1:
                    t = "\\multicolumn{%d}{c}{%s}" % (c["span"], t)
                parts.append(t)
            lines.append("  " + " & ".join(parts) + r" \\")
            if ri == 0 and len(rows) > 1:
                lines.append("\\midrule")
        lines.append("\\bottomrule")
        lines.append("\\end{tabular}")
        return "\n".join(lines)

    # ------------------------------------------------------------- blocks
    def list_info(self, ppr, sid):
        numpr = ppr.find("w:numPr", NS) if ppr is not None else None
        if numpr is None:
            st = self.styles.get(sid)
            numpr = st["numpr"] if st else None
        if numpr is None:
            name = self.style_name(sid)
            m = re.match(r"list (bullet|number)(?: (\d))?", name)
            if m:   # "List Bullet 2" = second level
                return (int(m.group(2)) - 1 if m.group(2) else 0), ("itemize" if m.group(1) == "bullet" else "enumerate")
            return None
        num_id = wval(numpr, "w:numId")
        if num_id in (None, "0"):
            return None
        ilvl = int(wval(numpr, "w:ilvl", "0") or 0)
        m = re.match(r"list (?:bullet|number|continue)? ?(\d)$", self.style_name(sid))
        if m and numpr.find("w:ilvl", NS) is None:
            ilvl = int(m.group(1)) - 1       # "List Bullet 2" style: level comes from the style name
        levels = self.numbering.get(num_id, {})
        fmt = levels.get(ilvl, levels.get(0, "bullet"))
        return ilvl, ("itemize" if fmt in ("bullet", "none") else "enumerate")

    def convert(self) -> Result:
        doc = self._xml("word/document.xml")
        if doc is None:
            raise ValueError("This doesn't look like a Word .docx file (word/document.xml is missing).")
        body = doc.find("w:body", NS)
        props = self.core_props()
        title, subtitle, authors = "", "", []
        abstract: list[str] = []
        keywords = ""
        out: list[str] = []
        bib: list[tuple[str, str]] = []
        list_stack: list[tuple[str, int]] = []
        mode = "body"          # body | abstract | refs | front
        seen_heading = False
        pending_table_caption = None
        last_block = None       # ("figure"|"table", index in out)

        def close_lists(to_level=-1):
            while list_stack and list_stack[-1][1] > to_level:
                env, _ = list_stack.pop()
                out.append("\\end{%s}" % env)

        blocks = list(body)
        for idx, el in enumerate(blocks):
            if el.tag == W + "sdt":
                content = el.find("w:sdtContent", NS)
                if content is not None:
                    blocks[idx + 1:idx + 1] = list(content)
                continue
            if el.tag == W + "tbl":
                close_lists()
                tex = self.table(el)
                cap = pending_table_caption
                pending_table_caption = None
                if mode == "refs":
                    mode = "body"
                out.append("\x00TABLE\x00" + (cap or "") + "\x00" + tex + "\x00/TABLE\x00")
                last_block = ("table", len(out) - 1)
                continue
            if el.tag != W + "p":
                continue
            ppr = el.find("w:pPr", NS)
            sid = wval(ppr, "w:pStyle")
            sname = self.style_name(sid)
            text = self.runs_to_tex(el)
            plain = re.sub(r"\\[a-zA-Z]+\{|[{}]|\x00[^\x00]*\x00", "", text).strip()
            level = self.heading_level(sid, ppr)
            has_img = "\x00IMG\x00" in text

            # Title / subtitle
            if sname == "title":
                title = (title + " " + text).strip()
                mode = "front"
                continue
            if sname == "subtitle":
                subtitle = (subtitle + " " + text).strip()
                continue
            if sname in ("author", "authors"):
                authors.append(text)
                continue

            # Captions ("Caption" style, or "Figure 3: ..." / "Table 2. ..." paragraphs)
            is_caption = sname == "caption" or (bool(CAPTION_PREFIX.match(plain)) and len(plain) < 400
                                                 and re.match(r"^\s*(fig(ure)?|table|tab)\.?\s*[0-9IVX]+", plain, re.I))
            if is_caption and not has_img and plain:
                cap = CAPTION_PREFIX.sub("", text, count=1).strip() if CAPTION_PREFIX.match(plain) else text.strip()
                raw_style = (self.styles.get(sid, {}).get("name") or "").lower()
                nxt = next((b for b in blocks[idx + 1:] if not (b.tag == W + "p" and not "".join(
                    t.text or "" for t in b.iter(W + "t")).strip() and b.find(".//a:blip", NS) is None)), None)
                if re.match(r"^\s*tab", plain, re.I) or "table" in raw_style:
                    is_table = True
                elif re.match(r"^\s*fig", plain, re.I) or "image" in raw_style or "figure" in raw_style:
                    is_table = False
                elif nxt is not None and nxt.tag == W + "tbl":
                    is_table = True
                else:
                    is_table = bool(last_block and last_block[0] == "table")
                if last_block and last_block[0] == ("table" if is_table else "figure") and \
                        "\x00\x00" in out[last_block[1]]:
                    out[last_block[1]] = out[last_block[1]].replace("\x00\x00", "\x00" + cap + "\x00", 1)
                    last_block = None
                elif is_table:
                    pending_table_caption = cap
                else:
                    self.pending_caption = cap
                continue

            # Headings
            if level and plain:
                close_lists()
                seen_heading = True
                if REF_HEADINGS.match(plain):
                    mode = "refs"
                    out.append("\x00REFS\x00")
                    continue
                if ABSTRACT_RE.fullmatch(plain + ":") or plain.lower() == "abstract":
                    mode = "abstract"
                    continue
                mode = "body"
                clean = NUMBER_PREFIX.sub("", text, count=1).strip()
                clean = re.sub(r"^\\(textbf|emph)\{(.*)\}$", r"\2", clean)   # heading styles are bold already
                if plain.isupper() and len(plain) > 3:
                    clean = clean.title()     # "INTRODUCTION" -> "Introduction"
                star = "*" if re.match(r"^\s*(acknowledg|appendix)", plain, re.I) else ""
                cmd = HEADING_CMDS[min(level, len(HEADING_CMDS)) - 1]
                out.append(f"\n\\{cmd}{star}{{{clean.strip()}}}")
                continue

            # Plain-paragraph "References" heading (common in Word papers without styles)
            if REF_HEADINGS.match(plain):
                close_lists()
                mode = "refs"
                out.append("\x00REFS\x00")
                continue

            if mode == "front" and not plain:
                continue
            if mode == "front" and not seen_heading and not ABSTRACT_RE.match(plain):
                # Lines between the title and the first heading: authors / affiliations
                if len(plain) < 200 and not has_img and len(authors) < 8:
                    authors.append(text)
                    continue
                mode = "body"

            # Abstract ("Abstract—..." paragraph, or after an Abstract heading)
            m = ABSTRACT_RE.match(plain)
            if m and len(plain) > 40 and not abstract:
                abstract.append(strip_label(text, "abstract").strip())
                mode = "body"
                continue
            if mode == "abstract":
                if plain:
                    abstract.append(text)
                continue
            km = KEYWORDS_RE.match(plain)
            if km and not keywords and len(plain) < 400:
                keywords = strip_label(text, "keywords|key words|index terms").strip()
                continue

            # Reference list entries
            if mode == "refs":
                if not plain:
                    continue
                bm = BIB_ITEM_RE.match(plain)
                entry = BIB_ITEM_RE.sub("", text, count=1) if bm else text
                num = bm.group(1) if bm else str(len(bib) + 1)
                bib.append((num, entry.strip()))
                continue

            # Images (on their own line -> figure)
            if has_img:
                close_lists()
                for im in re.finditer(r"\x00IMG\x00(.*?)\x00(.*?)\x00/IMG\x00", text):
                    cap = self.pending_caption or ""
                    self.pending_caption = None
                    out.append("\x00FIG\x00" + im.group(1) + "\x00" + im.group(2) + "\x00" + cap + "\x00/FIG\x00")
                    last_block = ("figure", len(out) - 1)
                rest = re.sub(r"\x00IMG\x00.*?\x00/IMG\x00", "", text).strip()
                if rest:
                    out.append(rest)
                continue

            # Lists
            li = self.list_info(ppr, sid)
            if li and plain:
                ilvl, env = li
                # Close deeper lists, and a same-level list of the other kind.
                while list_stack and (list_stack[-1][1] > ilvl or
                                      (list_stack[-1][1] == ilvl and list_stack[-1][0] != env)):
                    e, _ = list_stack.pop()
                    out.append("\\end{%s}" % e)
                # Open one new (possibly nested) list; skipped Word levels collapse into one.
                if not list_stack or list_stack[-1][1] < ilvl:
                    list_stack.append((env, ilvl))
                    out.append("\\begin{%s}" % env)
                out.append("  \\item " + text.strip())
                continue
            close_lists()

            if not plain and "\x00DISPLAY" not in text:
                continue
            if "\x00DISPLAY\x00" in text:
                text = re.sub(r"\x00DISPLAY\x00(.*?)\x00/DISPLAY\x00",
                              lambda mm: "\n\\begin{equation}\n  " + mm.group(1) + "\n\\end{equation}\n", text, flags=re.S)
            quote = sname in ("quote", "intense quote")
            code = sname in ("code", "html preformatted")
            if code:
                out.append("\\begin{verbatim}\n" + plain + "\n\\end{verbatim}")
            elif quote:
                out.append("\\begin{quote}\n" + text.strip() + "\n\\end{quote}")
            else:
                out.append(text.strip())
            last_block = None if last_block and last_block[0] == "figure" else last_block
        close_lists()

        # ---------------------------------------------------------- assemble
        body_tex = "\n\n".join(out)
        body_tex = re.sub(r"\x00FIG\x00(.*?)\x00(.*?)\x00(.*?)\x00/FIG\x00",
                          lambda mm: self.figure(mm.group(1), mm.group(2), mm.group(3)), body_tex, flags=re.S)
        body_tex = re.sub(r"\x00TABLE\x00(.*?)\x00(.*?)\x00/TABLE\x00",
                          lambda mm: self.table_float(mm.group(1), mm.group(2)), body_tex, flags=re.S)
        body_tex = re.sub(r"\x00IMG\x00(.*?)\x00(.*?)\x00/IMG\x00", r"\\includegraphics[\1]{\2}", body_tex)
        body_tex = body_tex.replace("\x00\x00", "")

        bib_tex = ""
        if bib:
            keys = {num: f"ref{num}" for num, _ in bib}
            items = "\n".join(f"\\bibitem{{{keys[num]}}} {entry}" for num, entry in bib)
            width = str(len(bib))
            bib_tex = "\\begin{thebibliography}{%s}\n%s\n\\end{thebibliography}" % ("9" * len(width), items)
            body_tex = self.link_citations(body_tex, keys)
            self.res.stats["references"] = len(bib)
        body_tex = body_tex.replace("\x00REFS\x00", bib_tex if bib_tex else "")
        body_tex = re.sub(r"\n{3,}", "\n\n", body_tex).strip()
        list_line = r"(?:\\begin\{(?:itemize|enumerate)\}|\s*\\item[^\n]*|\\end\{(?:itemize|enumerate)\})"
        prev = None
        while prev != body_tex:
            prev = body_tex
            body_tex = re.sub(r"(" + list_line + r")\n\n(?=" + list_line + r")", r"\1\n", body_tex)

        title = title or props.get("title", "")
        if not authors and props.get("author"):
            authors = [esc(props["author"])]
        self.res.title = re.sub(r"\\[a-z]+\{|[{}]", "", title)
        return self.assemble(title, subtitle, authors, abstract, keywords, body_tex)

    def figure(self, width, path, caption):
        self.fig_count += 1
        label = re.sub(r"[^a-z0-9]+", "-", posixpath.splitext(posixpath.basename(path))[0].lower())
        cap = caption.strip() or ""
        lines = ["\\begin{figure}[htbp]", "  \\centering", f"  \\includegraphics[{width}]{{{path}}}"]
        lines.append(f"  \\caption{{{cap}}}" if cap else "  % \\caption{Add a caption}")
        lines.append(f"  \\label{{fig:{label}}}")
        lines.append("\\end{figure}")
        return "\n".join(lines)

    def table_float(self, caption, tabular):
        n = self.res.stats.get("tables", 0) + 1
        self.res.stats["tables"] = n
        lines = ["\\begin{table}[htbp]", "  \\centering"]
        lines.append(f"  \\caption{{{caption.strip()}}}" if caption.strip() else "  % \\caption{Add a caption}")
        lines.append(f"  \\label{{tab:table{n}}}")
        lines.append("  " + tabular.replace("\n", "\n  "))
        lines.append("\\end{table}")
        return "\n".join(lines)

    @staticmethod
    def link_citations(tex: str, keys: dict) -> str:
        """[1], [2, 3], [4]–[6], [1-3] -> \\cite{ref1}, \\cite{ref2,ref3}, ..."""
        def expand(spec: str):
            nums = []
            for part in re.split(r"\s*,\s*", spec):
                rng = re.fullmatch(r"(\d+)\s*(?:-|--|–|—)\s*(\d+)", part)
                if rng:
                    a, b = int(rng.group(1)), int(rng.group(2))
                    if 0 < b - a < 50:
                        nums.extend(str(i) for i in range(a, b + 1))
                        continue
                if part.isdigit():
                    nums.append(part)
                else:
                    return None
            return nums if nums and all(n in keys for n in nums) else None

        def repl(m):
            spec = m.group(1)
            if m.group(2):    # [4]--[6]
                spec = f"{m.group(1)}-{m.group(2)}"
            nums = expand(spec.replace("--", "-"))
            if not nums:
                return m.group(0)
            return "~\\cite{" + ",".join(keys[n] for n in nums) + "}"

        tex = re.sub(r"\s?\[(\d+(?:\s*(?:,|-|--|–)\s*\d+)*)\](?:\s*(?:--|–|-)\s*\[(\d+)\])?", repl, tex)
        return tex.replace(" ~\\cite", "~\\cite")

    def assemble(self, title, subtitle, authors, abstract, keywords, body) -> Result:
        ieee = self.docclass == "ieee"
        if "\\sout{" in body:
            self.packages.add("ulem")
        pre = []
        if ieee:
            pre.append("\\documentclass[conference]{IEEEtran}")
        elif self.docclass == "report":
            pre.append("\\documentclass[11pt,a4paper]{report}")
        else:
            pre.append("\\documentclass[11pt,a4paper]{article}")
        pre.append("\\usepackage[utf8]{inputenc}")
        pre.append("\\usepackage[T1]{fontenc}")
        if not ieee:
            pre.append("\\usepackage[margin=2.5cm]{geometry}")
        for pkg in ("amsmath", "amssymb", "graphicx", "booktabs", "array"):
            pre.append(f"\\usepackage{{{pkg}}}")
        if "ulem" in self.packages:
            pre.append("\\usepackage[normalem]{ulem}")
        pre.append("\\usepackage{textcomp}")
        pre.append("\\usepackage[hidelinks]{hyperref}")
        pre.append("")
        pre.append("% Converted from a Word document by Overleaf Copy. Check figures, tables and equations.")
        t = title + (("\\\\\n\\large " + subtitle) if subtitle else "")
        pre.append(f"\\title{{{t or 'Untitled'}}}")
        if ieee:
            blocks, cur = [], None
            for a in authors:
                plain_a = re.sub(r"\\[a-z]+\{|[{}]", "", a)
                if cur is not None and AFFIL_RE.search(plain_a):
                    cur["aff"].append(a)
                else:
                    cur = {"name": a, "aff": []}
                    blocks.append(cur)
            pre.append("\\author{" + "\n\\and\n".join(
                "\\IEEEauthorblockN{" + b["name"] + "}" +
                ("\n\\IEEEauthorblockA{" + " \\\\\n".join(b["aff"]) + "}" if b["aff"] else "")
                for b in blocks) + "}" if authors else "\\author{}")
        else:
            pre.append("\\author{" + " \\\\\n".join(authors) + "}" if authors else "\\author{}")
            pre.append("\\date{}")
        doc = ["", "\\begin{document}", "\\maketitle", ""]
        if abstract:
            doc += ["\\begin{abstract}", "\n\n".join(a.strip() for a in abstract), "\\end{abstract}", ""]
        if keywords:
            if ieee:
                doc += ["\\begin{IEEEkeywords}", keywords, "\\end{IEEEkeywords}", ""]
            else:
                doc += ["\\noindent\\textbf{Keywords:} " + keywords, ""]
        doc += [body, "", "\\end{document}", ""]
        self.res.tex = "\n".join(pre + doc)
        if self.has_equations:
            self.res.warnings.append("Equations were converted from Word's format — check them against the original.")
        self.res.stats["figures"] = self.fig_count
        self.res.stats["images"] = len(self.res.media)
        return self.res


def convert_docx(source, docclass: str = "article") -> Result:
    data = source if isinstance(source, (bytes, bytearray)) else open(source, "rb").read()
    try:
        conv = DocxConverter(bytes(data), docclass)
    except zipfile.BadZipFile as e:
        raise ValueError("That file isn't a valid .docx (Word 2007 or later). Old .doc files need to be "
                         "saved as .docx in Word first.") from e
    return conv.convert()


if __name__ == "__main__":   # python docx2tex.py paper.docx > paper.tex
    import sys
    r = convert_docx(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "article")
    print(r.tex)
    for w in r.warnings:
        print("% WARNING:", w, file=sys.stderr)
