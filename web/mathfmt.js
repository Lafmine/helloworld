// Порт zhukogpt/mathfmt.py: LaTeX-формулы из ответов ($17 \times 3$) → обычный текст (17 × 3),
// плюс маленький безопасный Markdown-рендер для ответа (HTML из ответа модели всегда экранируется).

const SYMBOLS = {
  "\\times": "×", "\\cdot": "·", "\\div": "÷", "\\pm": "±", "\\mp": "∓",
  "\\le": "≤", "\\leq": "≤", "\\ge": "≥", "\\geq": "≥", "\\ne": "≠", "\\neq": "≠",
  "\\approx": "≈", "\\equiv": "≡", "\\infty": "∞", "\\to": "→", "\\rightarrow": "→",
  "\\Rightarrow": "⇒", "\\leftarrow": "←", "\\Leftrightarrow": "⇔", "\\degree": "°",
  "\\circ": "°", "\\angle": "∠", "\\perp": "⊥", "\\parallel": "∥", "\\triangle": "△",
  "\\in": "∈", "\\notin": "∉", "\\cup": "∪", "\\cap": "∩", "\\subset": "⊂",
  "\\emptyset": "∅", "\\forall": "∀", "\\exists": "∃", "\\neg": "¬", "\\land": "∧",
  "\\lor": "∨", "\\sum": "∑", "\\prod": "∏", "\\int": "∫", "\\partial": "∂",
  "\\alpha": "α", "\\beta": "β", "\\gamma": "γ", "\\delta": "δ", "\\Delta": "Δ",
  "\\epsilon": "ε", "\\varepsilon": "ε", "\\theta": "θ", "\\lambda": "λ", "\\mu": "μ",
  "\\pi": "π", "\\rho": "ρ", "\\sigma": "σ", "\\Sigma": "Σ", "\\tau": "τ", "\\phi": "φ",
  "\\varphi": "φ", "\\omega": "ω", "\\Omega": "Ω", "\\%": "%", "\\,": " ", "\\;": " ",
  "\\:": " ", "\\!": "", "\\quad": "  ", "\\qquad": "    ", "\\ldots": "…", "\\dots": "…",
  "\\cdots": "⋯", "\\{": "{", "\\}": "}", "\\ ": " ", "\\$": "$",
};
const SUP = { "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸",
  "9": "⁹", "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾", "n": "ⁿ" };
const SUB = { "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆", "7": "₇", "8": "₈",
  "9": "₉", "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎" };
const ARG = "\\{((?:[^{}]|\\{[^{}]*\\})*)\\}";
const SYMBOL_KEYS = Object.keys(SYMBOLS).sort((a, b) => b.length - a.length);
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function script(text, table) {
  let out = "";
  for (const ch of text) {
    if (ch === " ") { out += ch; continue; }
    if (!(ch in table)) return null;  // не нашли символ — оставим ^( ) / _( ), чтобы не потерять смысл
    out += table[ch];
  }
  return out;
}

function wrap(part) {
  part = part.trim();
  return /^[\p{L}\p{N}_.,√²³]+$/u.test(part) ? part : `(${part})`;
}

function latexToText(expr) {
  let s = expr.replace(/\^\s*\{?\s*\\circ\s*\}?/g, "°");
  for (let i = 0; i < 3; i++) {  // вложенные \frac{\sqrt{..}}{..}: корни раньше дробей
    s = s.replace(new RegExp("\\\\sqrt\\[([^\\]]+)\\]" + ARG, "g"), (_, n, b) => `${n}√(${b})`);
    s = s.replace(new RegExp("\\\\sqrt" + ARG, "g"), (_, b) => `√${wrap(b)}`);
    s = s.replace(new RegExp("\\\\[dt]?frac" + ARG + ARG, "g"), (_, a, b) => `${wrap(a)}/${wrap(b)}`);
    s = s.replace(new RegExp("\\\\(?:text|textbf|textit|mathrm|mathbf|operatorname|boxed|mbox)" + ARG, "g"), "$1");
    s = s.replace(new RegExp("\\\\(?:overline|bar)" + ARG, "g"), "$1̄");
    s = s.replace(new RegExp("\\\\vec" + ARG, "g"), "$1⃗");
  }
  s = s.replace(/\\(?:left|right|big|Big|bigg|Bigg)\s*([()[\]|.]|\\[{}])/g,
    (_, b) => (b === "." ? "" : b.replace(/^\\/, "")));
  s = s.replace(/\\(sin|cos|tan|cot|log|ln|lg|lim|max|min|exp)\b/g, "$1");
  for (const cmd of SYMBOL_KEYS) {
    s = s.replace(new RegExp(escapeRe(cmd) + "(?![A-Za-z])", "g"), () => SYMBOLS[cmd]);
  }
  s = s.replace(/\^\{([^{}]*)\}|\^([0-9A-Za-z+\-])/g, (_, a, b) => {
    const body = a ?? b;
    return script(body, SUP) ?? (body.length === 1 ? `^${body}` : `^(${body})`);
  });
  s = s.replace(/_\{([^{}]*)\}|_([0-9])/g, (_, a, b) => {
    const body = a ?? b;
    return script(body, SUB) ?? (body.length === 1 ? `_${body}` : `_(${body})`);
  });
  return s.replace(/[{}]/g, "").replace(/[ \t]{2,}/g, " ").trim();
}

export function latexToPlain(text) {
  if (!text.includes("$") && !text.includes("\\")) return text;
  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (_, e) => latexToText(e));
  text = text.replace(/\\\[([\s\S]+?)\\\]/g, (_, e) => latexToText(e));
  text = text.replace(/\\\(([\s\S]+?)\\\)/g, (_, e) => latexToText(e));
  // $…$ в одной строке; «$5 и $10» (цены) не трогаем.
  text = text.replace(/(?<![\\$])\$(?=\S)([^$\n]+?)(?<=\S)\$(?!\d)/g, (_, e) => latexToText(e));
  return text;
}

const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function inline(text) {
  // Код сначала вынимаем, чтобы звёздочки внутри него не стали жирным.
  const codes = [];
  let s = escapeHtml(text).replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  s = s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/__(.+?)__/g, "<b>$1</b>");
  s = s.replace(/(^|[^*\w])\*(?!\s)(.+?)(?<!\s)\*(?!\w)/g, "$1<i>$2</i>");
  s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`);
  return s;
}

/** Markdown ответа → HTML: заголовки, списки, цитаты, код, жирный/курсив; каждая строка — своя строка. */
export function renderMarkdown(text) {
  const out = [];
  let list = null;  // "ul" | "ol"
  let code = null;
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const line of latexToPlain(text).replace(/\r/g, "").split("\n")) {
    if (code !== null) {
      if (line.trim().startsWith("```")) { out.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`); code = null; }
      else code.push(line);
      continue;
    }
    if (line.trim().startsWith("```")) { closeList(); code = []; continue; }
    let m;
    if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\s*(\d+)[.)]\s+(.*)$/))) {
      if (list !== "ol") { closeList(); out.push(`<ol start="${m[1]}">`); list = "ol"; }
      out.push(`<li>${inline(m[2])}</li>`);
    } else if ((m = line.match(/^\s*#{1,6}\s+(.*)$/))) {
      closeList(); out.push(`<h3>${inline(m[1])}</h3>`);
    } else if ((m = line.match(/^\s*>\s?(.*)$/))) {
      closeList(); out.push(`<blockquote>${inline(m[1])}</blockquote>`);
    } else if (!line.trim()) {
      closeList(); out.push('<div class="gap"></div>');
    } else {
      closeList(); out.push(`<p>${inline(line)}</p>`);
    }
  }
  if (code !== null) out.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
  closeList();
  return out.join("");
}
