"""Convert the official bot prompt (docs/vani_buyer_prompt.docx) to a Markdown template.

    docs/vani_buyer_prompt.docx  ->  docs/vani_buyer_prompt.md

The .docx marks template variables with invisible U+FEFF characters:
    "\\ufeff product_name\\ufeff"   value inserted at runtime   ->  {{ product_name }}
    "\\ufeff@ call_outcome\\ufeff"  variable the bot itself sets ->  @call_outcome
Jinja blocks ({% if ... %}) are already plain text. The result is a Jinja template that
pipeline/bot_prompt.py renders per call for fix testing, and that prompt fixes edit.

Usage: .venv/bin/python pipeline/convert_prompt.py
"""
import re
import zipfile
from xml.etree import ElementTree as ET

from common import ROOT, setup_logging

SRC = ROOT / "docs" / "vani_buyer_prompt.docx"
DST = ROOT / "docs" / "vani_buyer_prompt.md"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
VAR = re.compile(r"﻿\s*(@)?\s*([A-Za-z_]\w*)\s*﻿")

log = setup_logging()


def para_text(p):
    t = "".join(n.text or "" for n in p.iter(W + "t"))
    t = VAR.sub(lambda m: f"@{m.group(2)}" if m.group(1) else f"{{{{ {m.group(2)} }}}}", t)
    return re.sub(r"[﻿​]", "", t).strip()


def main():
    body = ET.fromstring(zipfile.ZipFile(SRC).read("word/document.xml")).find(W + "body")
    out = []
    for el in body:
        if el.tag != W + "p":
            continue
        t = para_text(el)
        if not t:
            continue
        style = el.find(f"{W}pPr/{W}pStyle")
        style = style.get(W + "val") if style is not None else ""
        m = re.match(r"Heading(\d)", style)
        if m:
            out.append("#" * (int(m.group(1)) + 1) + " " + t)
        elif el.find(f"{W}pPr/{W}numPr") is not None:
            out.append("- " + t)
        elif len(t) < 90 and t.endswith(":") and "{%" not in t:
            out.append("## " + t.rstrip(":"))
        else:
            out.append(t)
    header = ("# Sarvam Prompt - Buyer Side VANI\n\n"
              "_Converted from vani_buyer_prompt.docx by pipeline/convert_prompt.py. This is a Jinja template: "
              "double-brace placeholders are filled in at runtime; `@name` is a variable the bot sets._\n\n")
    DST.write_text(header + "\n\n".join(out) + "\n", encoding="utf-8")
    text = DST.read_text(encoding="utf-8")
    log.info("wrote %s: %d paragraphs, %d {{ }} placeholders, %d @variables, %d {%% %%} blocks",
             DST.relative_to(ROOT), len(out), len(re.findall(r"\{\{", text)), len(re.findall(r"@\w+", text)),
             len(re.findall(r"\{%", text)))


if __name__ == "__main__":
    main()
