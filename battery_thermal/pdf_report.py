"""
pdf_report.py
=============
Renders the simulation report as a downloadable PDF in an IEEE
conference-paper style: centered title block, two-column body, Roman-
numeral section headings, numbered figures with captions, a results
table, and a references list -- with the transient-response charts
embedded as figures rather than left as an on-screen-only Plotly graph.

Chart rendering uses matplotlib (not Plotly/kaleido): kaleido bundles a
~130 MB headless-Chromium binary, which is a poor fit for a serverless
function with a 10 s timeout and tight deployment size limits. matplotlib
in "Agg" (non-interactive) mode has no such cost and is the standard
choice for headless chart-to-image rendering in a Flask/Dash backend.
"""

import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, Paragraph, Image,
    Table, TableStyle, FrameBreak, NextPageTemplate, KeepTogether,
)
import report as reportgen

# --------------------------------------------------------------------------
# IEEE-ish typography
# --------------------------------------------------------------------------
FONT = "Times-Roman"
FONT_BOLD = "Times-Bold"
FONT_ITALIC = "Times-Italic"

styles = {
    "title": ParagraphStyle("title", fontName=FONT, fontSize=18, leading=22,
                             alignment=TA_CENTER, spaceAfter=6),
    "authors": ParagraphStyle("authors", fontName=FONT, fontSize=11, leading=14,
                               alignment=TA_CENTER, spaceAfter=2),
    "affil": ParagraphStyle("affil", fontName=FONT_ITALIC, fontSize=10, leading=12,
                             alignment=TA_CENTER, spaceAfter=14),
    "abstract_head": ParagraphStyle("abstract_head", fontName=FONT_BOLD, fontSize=9,
                                     leading=11, spaceAfter=2),
    "abstract": ParagraphStyle("abstract", fontName=FONT, fontSize=9, leading=11,
                                alignment=TA_JUSTIFY, spaceAfter=10),
    "keywords": ParagraphStyle("keywords", fontName=FONT, fontSize=9, leading=11,
                                alignment=TA_JUSTIFY, spaceAfter=12),
    "h1": ParagraphStyle("h1", fontName=FONT_BOLD, fontSize=10, leading=13,
                          alignment=TA_CENTER, spaceBefore=10, spaceAfter=6,
                          letterSpacing=1),
    "body": ParagraphStyle("body", fontName=FONT, fontSize=9, leading=12,
                            alignment=TA_JUSTIFY, spaceAfter=6, firstLineIndent=12),
    "caption": ParagraphStyle("caption", fontName=FONT, fontSize=8, leading=10,
                               alignment=TA_CENTER, spaceBefore=4, spaceAfter=10),
    "tablecell": ParagraphStyle("tablecell", fontName=FONT, fontSize=7.5, leading=9),
    "tablehead": ParagraphStyle("tablehead", fontName=FONT_BOLD, fontSize=7.5, leading=9,
                                 textColor=colors.white),
    "ref": ParagraphStyle("ref", fontName=FONT, fontSize=8, leading=10,
                           spaceAfter=3),
}

REFERENCES = [
    "D. Bernardi, E. Pawlikowski, and J. Newman, \"A General Energy Balance for "
    "Battery Systems,\" J. Electrochem. Soc., vol. 132, no. 1, pp. 5-12, 1985.",
    "F. P. Incropera, D. P. DeWitt, T. L. Bergman, and A. S. Lavine, "
    "Fundamentals of Heat and Mass Transfer, 7th ed. Hoboken, NJ: Wiley, 2011.",
    "G. Karimi and X. Li, \"Thermal management of lithium-ion batteries for "
    "electric vehicles,\" Int. J. Energy Res., vol. 37, no. 1, pp. 13-24, 2013.",
]


# --------------------------------------------------------------------------
# Chart rendering (matplotlib, dark-free / print-friendly palette)
# --------------------------------------------------------------------------
def _render_transient_chart(results, ctx):
    modes_metrics = reportgen.compute_mode_metrics(results, ctx)
    fig, axes = plt.subplots(3, 1, figsize=(6.6, 5.4), sharex=True, dpi=200)

    for mm in modes_metrics:
        d = results[mm["mode"]]
        axes[0].plot(d["times"], d["tmax"], label=mm["label"], color=mm["color"], linewidth=1.4)
        axes[1].plot(d["times"], d["tavg"], color=mm["color"], linewidth=1.4)
        axes[2].plot(d["times"], [s * 100 for s in d["soc"]], color=mm["color"], linewidth=1.4)

    axes[0].axhline(ctx["max_cell_temp"], color="#cc0000", linestyle="--", linewidth=1,
                     label=f"max cell temp limit ({ctx['max_cell_temp']:.0f}°C)")
    axes[0].set_ylabel("T$_{max}$ (°C)", fontsize=8)
    axes[1].set_ylabel("T$_{avg}$ (°C)", fontsize=8)
    axes[2].set_ylabel("SoC (%)", fontsize=8)
    axes[2].set_xlabel("Time (s)", fontsize=8)

    for ax in axes:
        ax.tick_params(labelsize=7)
        ax.grid(True, linewidth=0.4, alpha=0.5)
        for spine in ax.spines.values():
            spine.set_linewidth(0.6)

    axes[0].legend(fontsize=6.5, loc="upper right", ncol=2, frameon=False)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _render_bar_chart(results, ctx):
    modes_metrics = reportgen.compute_mode_metrics(results, ctx)
    fig, ax = plt.subplots(figsize=(6.6, 2.6), dpi=200)

    labels = [mm["label"] for mm in modes_metrics]
    peaks = [mm["t_max"] for mm in modes_metrics]
    bar_colors = [mm["color"] for mm in modes_metrics]
    bars = ax.bar(labels, peaks, color=bar_colors, width=0.5)
    ax.axhline(ctx["max_cell_temp"], color="#cc0000", linestyle="--", linewidth=1)
    ax.set_ylabel("Peak cell temperature (°C)", fontsize=8)
    ax.tick_params(labelsize=7.5)
    ax.grid(True, axis="y", linewidth=0.4, alpha=0.5)
    for spine in ax.spines.values():
        spine.set_linewidth(0.6)
    for b, v in zip(bars, peaks):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.1f}", ha="center", va="bottom", fontsize=7)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


# --------------------------------------------------------------------------
# Document assembly
# --------------------------------------------------------------------------
def _two_col_doc(buf):
    page_w, page_h = letter
    margin = 0.55 * inch
    gutter = 0.28 * inch
    title_block_h = 1.8 * inch  # reserved height for the title/authors/affiliation block
    col_w = (page_w - 2 * margin - gutter) / 2
    # left/right columns must start exactly where the title block ends, or their
    # top edge overlaps into the title block's reserved area
    col_h = page_h - 2 * margin - title_block_h

    doc = BaseDocTemplate(buf, pagesize=letter,
                           leftMargin=margin, rightMargin=margin,
                           topMargin=margin, bottomMargin=margin,
                           title="CellFire Battery Thermal Simulation Report")

    full_frame = Frame(margin, page_h - margin - title_block_h, page_w - 2 * margin, title_block_h,
                        id="full", showBoundary=0)
    left_frame = Frame(margin, margin, col_w, col_h, id="left", showBoundary=0)
    right_frame = Frame(margin + col_w + gutter, margin, col_w, col_h, id="right", showBoundary=0)

    left_frame_p2 = Frame(margin, margin, col_w, page_h - 2 * margin, id="left2", showBoundary=0)
    right_frame_p2 = Frame(margin + col_w + gutter, margin, col_w, page_h - 2 * margin,
                            id="right2", showBoundary=0)

    doc.addPageTemplates([
        PageTemplate(id="First", frames=[full_frame, left_frame, right_frame]),
        PageTemplate(id="Later", frames=[left_frame_p2, right_frame_p2]),
    ])
    return doc


def _table(headers, rows, col_widths):
    data = [[Paragraph(h, styles["tablehead"]) for h in headers]]
    for r in rows:
        data.append([Paragraph(str(c), styles["tablecell"]) for c in r])
    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#26313f")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#888888")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
    ]))
    return t


def build_pdf(vehicle_id, battery_key, c_rate, t_amb_c, lx_cm, ly_cm, results, design=None):
    """Returns PDF bytes for the current simulation run, IEEE-conference-paper styled."""
    ctx = reportgen.resolve_context(vehicle_id, battery_key, design)
    vehicle = ctx["vehicle"]
    modes_metrics = reportgen.compute_mode_metrics(results, ctx)

    vehicle_name = vehicle["name"] if vehicle else "Generic EV Platform"
    title = f"Thermal Management Analysis of a {ctx['chemistry']} Li-Ion Battery Pack for {vehicle_name}"

    buf = io.BytesIO()
    doc = _two_col_doc(buf)
    story = []

    # ---- Title block (full width) -----------------------------------
    story.append(Paragraph(title, styles["title"]))
    story.append(Paragraph("CellFire &ndash; EV Battery Thermal Management Simulator", styles["authors"]))
    story.append(Paragraph("BMEE402L, Heat and Mass Transfer &mdash; VIT Vellore", styles["affil"]))
    story.append(FrameBreak())

    # ---- Abstract + keywords (left column, page 1) --------------------
    best = min(modes_metrics, key=lambda mm: mm["t_max"])
    worst = max(modes_metrics, key=lambda mm: mm["t_max"])
    abstract = (
        f"This report presents a three-dimensional finite-difference (FDM) transient thermal "
        f"analysis of a representative {ctx['chemistry']} lithium-ion cell sized for the "
        f"{vehicle_name} platform, discharged at {c_rate:g}C from {ctx['soc_max']:.0f}% to "
        f"{ctx['soc_min']:.0f}% state of charge at {t_amb_c:g}&deg;C ambient temperature. "
        f"{len(modes_metrics)} cooling strategies were evaluated"
        + (f", with {worst['label']} producing the highest peak cell temperature "
           f"({worst['t_max']:.1f}&deg;C) and {best['label']} the lowest ({best['t_max']:.1f}&deg;C)"
           if best is not worst else f", reaching a peak cell temperature of {best['t_max']:.1f}&deg;C")
        + f". Results are compared against a design limit of {ctx['max_cell_temp']:.0f}&deg;C to "
        f"assess thermal margin under the specified operating envelope."
    )
    story.append(Paragraph("Abstract", styles["abstract_head"]))
    story.append(Paragraph(abstract, styles["abstract"]))
    story.append(Paragraph(
        f"<b>Index Terms</b>&mdash;lithium-ion battery, thermal management, finite difference "
        f"method, electric vehicle, {ctx['chemistry']}, cooling strategy",
        styles["keywords"]))

    # ---- I. Introduction ------------------------------------------------
    story.append(Paragraph("I. Introduction", styles["h1"]))
    story.append(Paragraph(
        f"Electric vehicle (EV) battery packs generate heat through Joule (I&sup2;R) losses during "
        f"charge and discharge. Left unmanaged, this heat raises cell temperature, accelerates "
        f"capacity fade, and in extreme cases can trigger thermal runaway. This work models the "
        f"transient temperature field of a single representative cell using an explicit "
        f"three-dimensional finite-difference scheme, and compares {len(modes_metrics)} candidate "
        f"cooling strategies against the design targets summarized in Table I.",
        styles["body"]))

    # ---- II. System Specification (table) --------------------------------
    story.append(Paragraph("II. System Specification", styles["h1"]))
    spec_rows = [
        ["Chemistry", ctx["chemistry"]],
        ["Cell format", ctx["cell_format"]],
        ["Rated capacity", f"{ctx['rated_ah']:.1f} Ah"],
        ["Nominal / cutoff voltage", f"{ctx['nominal_v']:.2f} V / {ctx['cutoff_low_v']:.2f}"
                                       f"–{ctx['cutoff_high_v']:.2f} V"],
        ["Pack topology", f"{ctx['series']}S{ctx['parallel']}P ({ctx['series']*ctx['parallel']} cells)"],
        ["Pack voltage / energy", f"{ctx['pack_voltage']:.0f} V / {ctx['pack_energy_kwh']:.1f} kWh"],
        ["Cell footprint", f"{lx_cm:g} × {ly_cm:g} × 0.6 cm"],
        ["Discharge rate", f"{c_rate:g} C"],
        ["Ambient temperature", f"{t_amb_c:g} °C"],
        ["SoC operating window", f"{ctx['soc_min']:.0f}–{ctx['soc_max']:.0f} %"],
        ["Max cell temperature limit", f"{ctx['max_cell_temp']:.0f} °C"],
        ["Max cell-to-cell dT limit", f"{ctx['max_delta_t']:.0f} °C"],
        ["Thermal runaway onset (assumed)", f"{ctx['runaway_temp']:.0f} °C"],
    ]
    if vehicle:
        spec_rows = [
            ["Target range", f"{vehicle.get('range_km', '--')} km"],
            ["Kerb mass", f"{vehicle.get('mass_kg', '--')} kg"],
            ["Peak motor power", f"{vehicle.get('motor_kw', '--')} kW"],
            ["DC fast-charge (10-80%)", f"{vehicle.get('fast_charge_min', '--')} min"],
        ] + spec_rows
    story.append(_table(["Parameter", "Value"], spec_rows, [1.35 * inch, 1.35 * inch]))
    story.append(Paragraph("TABLE I. Design specification used for this simulation run.", styles["caption"]))

    # ---- III. Methodology -------------------------------------------------
    # Note: body text uses ReportLab's base Times fonts, which only cover the
    # WinAnsi/CP1252 code page -- Greek letters and math operators (rho, del,
    # nabla, the dot-accent on q) fall outside it and render as blank boxes.
    # Chart images (matplotlib) don't have this limitation and use proper
    # math notation instead; here the governing equation is spelled out in
    # words to stay safely inside the supported character set.
    story.append(Paragraph("III. Methodology", styles["h1"]))
    story.append(Paragraph(
        "The governing equation is the transient heat-diffusion equation -- density times specific "
        "heat times the local time derivative of temperature equals thermal conductivity times the "
        "Laplacian of temperature, plus a volumetric heat generation term -- solved on a "
        "15&times;10&times;5 structured grid using an explicit forward-time, central-space scheme "
        "with ghost-node Robin boundary conditions on all six faces to represent convective cooling "
        "(h = 5, 35, and 500 W/m&sup2;K for natural, forced-air, and liquid cooling respectively). "
        "The volumetric heat generation term follows Bernardi's energy balance [1]: heat generation "
        "rate equals I&sup2;R(SoC,T) divided by cell volume, with R(SoC) drawn from a "
        "cubic-interpolated Li-NMC resistance curve and a linear temperature correction. Time "
        "integration uses an internally subdivided step chosen to satisfy the explicit stability "
        "criterion at every reporting interval.",
        styles["body"]))

    # ---- IV. Results --------------------------------------------------
    story.append(NextPageTemplate("Later"))
    story.append(Paragraph("IV. Results", styles["h1"]))

    chart1_buf = _render_transient_chart(results, ctx)
    story.append(KeepTogether([
        Image(chart1_buf, width=3.15 * inch, height=3.15 * 5.4 / 6.6 * inch),
        Paragraph("Fig. 1. Peak temperature, average temperature, and state of charge versus "
                  "time for each cooling strategy. Dashed line marks the max-cell-temperature limit.",
                  styles["caption"]),
    ]))

    chart2_buf = _render_bar_chart(results, ctx)
    story.append(KeepTogether([
        Image(chart2_buf, width=3.15 * inch, height=3.15 * 2.6 / 6.6 * inch),
        Paragraph("Fig. 2. Peak cell temperature reached by cooling strategy, against the design limit.",
                  styles["caption"]),
    ]))

    result_rows = []
    for mm in modes_metrics:
        result_rows.append([
            mm["label"], f"{mm['t_max']:.1f}", f"{mm['peak_delta_t']:.1f}",
            "Pass" if mm["t_max"] <= ctx["max_cell_temp"] else "Fail",
            "Pass" if mm["delta_t_ok"] else "Fail",
            reportgen.fmt_time(mm["t_to_target"]),
        ])
    story.append(_table(
        ["Mode", "T_peak\n(°C)", "dT_peak\n(°C)", "Temp\nlimit", "dT\nlimit", "Time to\ntarget SoC"],
        result_rows, [0.68 * inch, 0.42 * inch, 0.42 * inch, 0.35 * inch, 0.35 * inch, 0.68 * inch],
    ))
    story.append(Paragraph("TABLE II. Per-cooling-mode results against the safety limits in Table I.",
                            styles["caption"]))

    # Deliberate column break: the figures/tables above rarely fill the left
    # column exactly, and without this the discussion below would just
    # continue in whatever space is left rather than starting a fresh (and
    # visually balanced) right-hand column.
    story.append(FrameBreak())

    # ---- V. Discussion -------------------------------------------------
    story.append(Paragraph("V. Discussion", styles["h1"]))
    if best is not worst:
        spread = worst["t_max"] - best["t_max"]
        disc = (
            f"{worst['label']} produced the highest peak cell temperature "
            f"({worst['t_max']:.1f}&deg;C), while {best['label']} kept the pack "
            f"{spread:.1f}&deg;C cooler. This follows directly from the convective heat-transfer "
            f"coefficient h in Newton's law of cooling: a higher h drives a smaller steady-state "
            f"temperature rise for the same internal heat generation. "
        )
    else:
        disc = f"The evaluated strategy reached a peak cell temperature of {best['t_max']:.1f}&deg;C. "
    disc += (
        "Because thermal conductivity is high relative to the cell's thin cross-section, the "
        "within-cell temperature spread stays small compared to the overall temperature rise "
        "for the parameters simulated here; consequently the largest lever on peak temperature "
        "is cooling-mode selection and discharge C-rate, not internal conduction. Resistance "
        "growth with SoC depletion also means heating is not uniform in time, peaking toward the "
        "start and end of the discharge window rather than mid-cycle."
    )
    story.append(Paragraph(disc, styles["body"]))

    # ---- VI. Conclusion -------------------------------------------------
    story.append(Paragraph("VI. Conclusion", styles["h1"]))
    all_pass = all(mm["t_max"] <= ctx["max_cell_temp"] and mm["delta_t_ok"] for mm in modes_metrics)
    concl = (
        "All evaluated cooling strategies satisfy the specified thermal design limits for this "
        "operating scenario."
        if all_pass else
        "At least one evaluated cooling strategy exceeds the specified thermal design limits for "
        "this operating scenario and would require a lower C-rate, better cooling, or relaxed "
        "limits before this design could be considered acceptable."
    )
    story.append(Paragraph(concl + " This is an educational thermal model (BMEE402L, VIT Vellore) "
                            "and does not replace validated OEM battery-management or safety "
                            "engineering.", styles["body"]))

    # ---- References -------------------------------------------------
    story.append(Paragraph("References", styles["h1"]))
    for i, ref in enumerate(REFERENCES, start=1):
        story.append(Paragraph(f"[{i}]&nbsp;&nbsp;{ref}", styles["ref"]))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()
