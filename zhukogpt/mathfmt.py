"""Превращает LaTeX-формулы из ответов моделей ($17 \\times 3$) в обычный текст (17 × 3)."""
import re

_SYMBOLS = {
    r"\times": "×", r"\cdot": "·", r"\div": "÷", r"\pm": "±", r"\mp": "∓",
    r"\le": "≤", r"\leq": "≤", r"\ge": "≥", r"\geq": "≥", r"\ne": "≠", r"\neq": "≠",
    r"\approx": "≈", r"\equiv": "≡", r"\infty": "∞", r"\to": "→", r"\rightarrow": "→",
    r"\Rightarrow": "⇒", r"\leftarrow": "←", r"\Leftrightarrow": "⇔", r"\degree": "°",
    r"\circ": "°", r"\angle": "∠", r"\perp": "⊥", r"\parallel": "∥", r"\triangle": "△",
    r"\in": "∈", r"\notin": "∉", r"\cup": "∪", r"\cap": "∩", r"\subset": "⊂",
    r"\emptyset": "∅", r"\forall": "∀", r"\exists": "∃", r"\neg": "¬", r"\land": "∧",
    r"\lor": "∨", r"\sum": "∑", r"\prod": "∏", r"\int": "∫", r"\partial": "∂",
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ", r"\Delta": "Δ",
    r"\epsilon": "ε", r"\varepsilon": "ε", r"\theta": "θ", r"\lambda": "λ", r"\mu": "μ",
    r"\pi": "π", r"\rho": "ρ", r"\sigma": "σ", r"\Sigma": "Σ", r"\tau": "τ", r"\phi": "φ",
    r"\varphi": "φ", r"\omega": "ω", r"\Omega": "Ω", r"\%": "%", r"\,": " ", r"\;": " ",
    r"\:": " ", r"\!": "", r"\quad": "  ", r"\qquad": "    ", r"\ldots": "…", r"\dots": "…",
    r"\cdots": "⋯", r"\{": "{", r"\}": "}", r"\ ": " ", r"\$": "$",
}
_SUPERSCRIPT = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
_SUBSCRIPT = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")

# Внутри скобок одного уровня — для \frac{..}{..}, \sqrt{..} и т. п.
_ARG = r"\{((?:[^{}]|\{[^{}]*\})*)\}"


def _script(text, table):
    converted = text.translate(table)
    # Если хотя бы один символ не нашёлся в таблице — оставляем через ^( ) / _( ), чтобы не потерять смысл.
    return converted if all(c != o or c in " " for c, o in zip(converted, text)) else None


def _latex_to_text(expr: str) -> str:
    s = expr
    s = re.sub(r"\^\s*\{?\s*\\circ\s*\}?", "°", s)  # 90^\circ → 90°
    for _ in range(3):  # вложенные \frac{\sqrt{..}}{..}: корни раньше дробей
        s = re.sub(r"\\sqrt\[([^\]]+)\]" + _ARG, lambda m: f"{m[1]}√({m[2]})", s)
        s = re.sub(r"\\sqrt" + _ARG, lambda m: f"√{_wrap(m[1])}", s)
        s = re.sub(r"\\[dt]?frac" + _ARG + _ARG, lambda m: f"{_wrap(m[1])}/{_wrap(m[2])}", s)
        s = re.sub(r"\\(?:text|textbf|textit|mathrm|mathbf|operatorname|boxed|mbox)" + _ARG, r"\1", s)
        s = re.sub(r"\\(?:overline|bar)" + _ARG, r"\1̄", s)
        s = re.sub(r"\\vec" + _ARG, r"\1⃗", s)
    s = re.sub(r"\\(?:left|right|big|Big|bigg|Bigg)\s*([()\[\]|.]|\\[{}])",
               lambda m: "" if m[1] == "." else m[1].lstrip("\\"), s)
    s = re.sub(r"\\(?:sin|cos|tan|cot|log|ln|lg|lim|max|min|exp)\b", lambda m: m[0][1:], s)
    for cmd in sorted(_SYMBOLS, key=len, reverse=True):  # длинные раньше: \leq до \le
        s = re.sub(re.escape(cmd) + r"(?![A-Za-z])", lambda _m, v=_SYMBOLS[cmd]: v, s)

    def sup(m):
        body = m[1] if m[1] is not None else m[2]
        return _script(body, _SUPERSCRIPT) or (f"^{body}" if len(body) == 1 else f"^({body})")

    def sub(m):
        body = m[1] if m[1] is not None else m[2]
        return _script(body, _SUBSCRIPT) or (f"_{body}" if len(body) == 1 else f"_({body})")

    s = re.sub(r"\^\{([^{}]*)\}|\^([0-9A-Za-z+\-])", sup, s)
    s = re.sub(r"_\{([^{}]*)\}|_([0-9])", sub, s)
    s = s.replace("{", "").replace("}", "")
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def _wrap(part: str) -> str:
    """Скобки вокруг сложного числителя/знаменателя: (a+b)/2, но 1/2."""
    part = part.strip()
    return part if re.fullmatch(r"[\w.,√²³]+", part) else f"({part})"


def latex_to_plain(text: str) -> str:
    """Находит формулы ($…$, $$…$$, \\(…\\), \\[…\\]) и переводит их в обычный текст."""
    if "$" not in text and "\\" not in text:
        return text
    text = re.sub(r"\$\$(.+?)\$\$", lambda m: _latex_to_text(m[1]), text, flags=re.S)
    text = re.sub(r"\\\[(.+?)\\\]", lambda m: _latex_to_text(m[1]), text, flags=re.S)
    text = re.sub(r"\\\((.+?)\\\)", lambda m: _latex_to_text(m[1]), text, flags=re.S)
    # $…$ в одной строке; «$5 и $10» (цены) не трогаем: после открывающего $ нет пробела,
    # перед закрывающим нет пробела, а после него не цифра.
    text = re.sub(r"(?<![\\$])\$(?=\S)([^$\n]+?)(?<=\S)\$(?!\d)", lambda m: _latex_to_text(m[1]), text)
    return text


def keep_line_breaks(text: str) -> str:
    """В Markdown одиночный перенос склеивает строки — делаем его настоящим переносом («  » в конце).

    Иначе «76 + 1 = 77» и «74 + 1 = 75» на соседних строках сливаются в одну. Код в ``` не трогаем.
    """
    lines = text.split("\n")
    in_code = False
    for i, line in enumerate(lines[:-1]):
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if not in_code and line.strip() and lines[i + 1].strip():
            lines[i] = line.rstrip() + "  "
    return "\n".join(lines)
