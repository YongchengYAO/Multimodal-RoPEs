"""Execute a notebook and render it to PDF without LaTeX, pandoc or a browser.

    python export_pdf.py reproduce_figure1.ipynb        # -> reproduce_figure1.pdf

Needs: nbformat, nbclient, ipykernel (to run the notebook), fpdf2, markdown, Pillow,
matplotlib (only for its bundled DejaVu fonts). The .ipynb on disk is not modified.
"""
import base64
import io
import re
import sys
from pathlib import Path

import markdown
import matplotlib
import nbformat
from fpdf import FPDF
from fpdf.fonts import FontFace
from nbclient import NotebookClient
from PIL import Image

NB = Path(sys.argv[1]).resolve()
OUT = NB.with_suffix(".pdf")
FONT_DIR = Path(matplotlib.__file__).parent / "mpl-data" / "fonts" / "ttf"

# ---------------------------------------------------------------- 1. execute in memory
nb = nbformat.read(NB, as_version=4)
NotebookClient(nb, timeout=600, kernel_name="python3",
               resources={"metadata": {"path": str(NB.parent)}}).execute()
errors = [o for c in nb.cells if c.cell_type == "code" for o in c.outputs if o.output_type == "error"]
if errors:
    sys.exit(f"notebook raised {errors[0].ename}: {errors[0].evalue}")

# ---------------------------------------------------------------- 2. PDF setup
class NotebookPDF(FPDF):
    def footer(self):
        self.set_y(-12)
        self.set_font("DejaVuSans", size=8)
        self.set_text_color(130)
        self.cell(0, 6, f"{self.page_no()} / {{nb}}", align="C")
        self.set_text_color(0)


pdf = NotebookPDF(orientation="P", unit="mm", format="A4")
pdf.alias_nb_pages()
pdf.set_margins(15, 15, 15)
pdf.set_auto_page_break(auto=True, margin=18)
for family, stem in (("DejaVuSans", "DejaVuSans"), ("DejaVuSansMono", "DejaVuSansMono")):
    for style, suffix in (("", ""), ("B", "-Bold"), ("I", "-Oblique"), ("BI", "-BoldOblique")):
        pdf.add_font(family, style, str(FONT_DIR / f"{stem}{suffix}.ttf"))

TAG_STYLES = {
    "h1": FontFace(size_pt=17, emphasis="BOLD"),
    "h2": FontFace(size_pt=13.5, emphasis="BOLD"),
    "h3": FontFace(size_pt=11.5, emphasis="BOLD"),
    "code": FontFace(family="DejaVuSansMono", size_pt=8.5, color=(50, 50, 50)),
    "pre": FontFace(family="DejaVuSansMono", size_pt=8),
    "a": FontFace(color=(30, 90, 180)),
}
MONO_PT, MONO_LH = 7.6, 3.6

# ---------------------------------------------------------------- 3. markdown helpers
MATH_SUBS = [(r"\\theta", "θ"), (r"\\text\{([^}]*)\}", r"\1"), (r"\^\{([^}]*)\}", r"^(\1)"), (r"\\,", " ")]


def demath(text, italic=True):
    """Turn the notebook's inline $...$ snippets into plain (italic) text."""
    def repl(m):
        s = m.group(1)
        for pat, rep in MATH_SUBS:
            s = re.sub(pat, rep, s)
        return f"<i>{s}</i>" if italic else s
    return re.sub(r"\$([^$\n]+)\$", repl, text)


def split_tables(text):
    """Yield ('md', chunk) and ('table', rows) segments; rows are lists of cell strings."""
    buf, rows = [], []
    for line in text.splitlines():
        if line.strip().startswith("|"):
            if buf:                                   # flush the prose that precedes the table
                yield "md", "\n".join(buf)
                buf = []
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not all(re.fullmatch(r":?-{2,}:?", c) for c in cells):   # skip the |---| row
                rows.append(cells)
        else:
            if rows:
                yield "table", rows
                rows = []
            buf.append(line)
    if rows:
        yield "table", rows
    if buf:
        yield "md", "\n".join(buf)


def write_table(rows):
    ncol = max(len(r) for r in rows)
    plain = lambda c: re.sub(r"\*\*(.+?)\*\*", r"\1", demath(c.replace("`", ""), italic=False))
    rows = [[plain(c) for c in r] + [""] * (ncol - len(r)) for r in rows]
    pdf.set_font("DejaVuSans", size=8.5)
    # column width = widest cell (measured), capped so long prose columns wrap instead of identifiers
    widths = [min(max(pdf.get_string_width(r[j]) for r in rows), 50) + 3 for j in range(ncol)]
    with pdf.table(col_widths=widths, text_align="LEFT", line_height=4.2, padding=1.2,
                   headings_style=FontFace(emphasis="BOLD", fill_color=(235, 235, 235)),
                   borders_layout="HORIZONTAL_LINES") as table:
        for r in rows:
            row = table.row()
            for c in r:
                row.cell(c)
    pdf.ln(3)


def write_markdown(text):
    for kind, chunk in split_tables(text):
        if kind == "table":
            write_table(chunk)
            continue
        body = markdown.markdown(demath(chunk), extensions=["fenced_code"])
        pdf.set_font("DejaVuSans", size=10)
        pdf.write_html(body, font_family="DejaVuSans", tag_styles=TAG_STYLES, li_prefix_color=(60, 60, 60))
        pdf.ln(2)


# ---------------------------------------------------------------- 4. code / outputs
def write_mono(text, fill=False, color=20):
    pdf.set_font("DejaVuSansMono", size=MONO_PT)
    pdf.set_fill_color(244, 244, 244)
    pdf.set_text_color(color)
    pdf.multi_cell(0, MONO_LH, text.rstrip("\n") + "\n", fill=fill, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)
    pdf.ln(1.5)


def place_image(png):
    w_px, h_px = Image.open(io.BytesIO(png)).size
    w = pdf.epw
    h = w * h_px / w_px
    max_h = pdf.h - pdf.t_margin - pdf.b_margin - 6
    if h > max_h:
        h, w = max_h, max_h * w_px / h_px
    if pdf.get_y() + h > pdf.page_break_trigger:
        pdf.add_page()
    pdf.image(io.BytesIO(png), x=pdf.l_margin + (pdf.epw - w) / 2, w=w, h=h)
    pdf.ln(3)


def write_outputs(outputs):
    for out in outputs:
        if out.output_type == "stream":
            write_mono(out.text, color=70)
        elif out.output_type in ("display_data", "execute_result"):
            if "image/png" in out.data:
                place_image(base64.b64decode(out.data["image/png"]))
            elif "application/vnd.plotly.v1+json" in out.data:
                write_mono("[interactive Plotly figure: see figure1_position_design_interactive.html]", color=70)
            elif "text/plain" in out.data:
                write_mono(out.data["text/plain"], color=70)


# ---------------------------------------------------------------- 5. assemble
first_md = next((c.source for c in nb.cells if c.cell_type == "markdown"), "")
title = re.sub(r"[*_`]", "", first_md.splitlines()[0].lstrip("# ")) if first_md else NB.stem
pdf.set_title(title)
pdf.add_page()
for cell in nb.cells:
    if cell.cell_type == "markdown":
        write_markdown(cell.source)
    elif cell.cell_type == "code":
        write_mono(cell.source, fill=True)
        write_outputs(cell.outputs)
pdf.output(str(OUT))
print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {pdf.page_no()} pages)")
