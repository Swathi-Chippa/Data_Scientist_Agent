"""Generate docs/architecture.drawio.svg with dual Draw.io XML and SVG rendering."""

import html
import os
import xml.etree.ElementTree as ET

os.makedirs("docs", exist_ok=True)

# 1. Define nodes
nodes = [
    {
        "id": "USER",
        "label": "Business Stakeholder / Analyst",
        "type": "stadium",
        "fill": "#e0f2fe",
        "stroke": "#0284c7",
        "font": "#0369a1",
        "x": 420, "y": 40, "w": 260, "h": 50,
    },
    {
        "id": "API",
        "label": "FastAPI / SSE Stream &amp; CLI REPL",
        "type": "rect",
        "fill": "#e0f2fe",
        "stroke": "#0284c7",
        "font": "#0369a1",
        "x": 425, "y": 140, "w": 250, "h": 55,
    },
    {
        "id": "GUARD",
        "label": "1. Ingress Guard<br>(SQLi, DDL/DML Filter)",
        "type": "rhombus",
        "fill": "#fee2e2",
        "stroke": "#ef4444",
        "font": "#991b1b",
        "x": 460, "y": 245, "w": 180, "h": 100,
    },
    {
        "id": "REJECT",
        "label": "Security Rejection",
        "type": "stadium",
        "fill": "#fee2e2",
        "stroke": "#ef4444",
        "font": "#991b1b",
        "x": 120, "y": 270, "w": 180, "h": 50,
    },
    {
        "id": "ROUTER",
        "label": "2. 3-Signal Intent Router<br>(Rules + Catalog Classifier + Model)",
        "type": "rect",
        "fill": "#e0f2fe",
        "stroke": "#0284c7",
        "font": "#0369a1",
        "x": 415, "y": 395, "w": 270, "h": 65,
    },
    {
        "id": "RESOLVER",
        "label": "3. Metric Resolver",
        "type": "rhombus",
        "fill": "#dcfce7",
        "stroke": "#22c55e",
        "font": "#166534",
        "x": 200, "y": 515, "w": 160, "h": 90,
    },
    {
        "id": "PERCEPTOR",
        "label": "4. Schema Perceptor &amp; Value Linker",
        "type": "rect",
        "fill": "#fef3c7",
        "stroke": "#f59e0b",
        "font": "#92400e",
        "x": 440, "y": 530, "w": 250, "h": 60,
    },
    {
        "id": "REFUSAL",
        "label": "Refusal (ABS-1)",
        "type": "stadium",
        "fill": "#fee2e2",
        "stroke": "#ef4444",
        "font": "#991b1b",
        "x": 780, "y": 535, "w": 180, "h": 50,
    },
    {
        "id": "COMPILER",
        "label": "Deterministic SQL Compiler<br>(Strict Jinja2 AST; Zero-LLM)",
        "type": "rect",
        "fill": "#dcfce7",
        "stroke": "#22c55e",
        "font": "#166534",
        "x": 150, "y": 660, "w": 250, "h": 65,
    },
    {
        "id": "GENERATOR",
        "label": "4-Stage SQL Generator<br>(CTE Skeletons + Catalog Joins)",
        "type": "rect",
        "fill": "#fef3c7",
        "stroke": "#f59e0b",
        "font": "#92400e",
        "x": 440, "y": 660, "w": 250, "h": 65,
    },
    {
        "id": "VALIDATOR",
        "label": "5. AST Validator<br>(Dual-Mode: Enterprise vs Benchmark)",
        "type": "rhombus",
        "fill": "#fee2e2",
        "stroke": "#ef4444",
        "font": "#991b1b",
        "x": 445, "y": 775, "w": 220, "h": 110,
    },
    {
        "id": "REPAIRER",
        "label": "Self-Repair Reflector<br>(Max 2 Tries; Frozen Schema)",
        "type": "rect",
        "fill": "#fef3c7",
        "stroke": "#f59e0b",
        "font": "#92400e",
        "x": 760, "y": 795, "w": 230, "h": 65,
    },
    {
        "id": "CLARIFY",
        "label": "Clarification Envelope",
        "type": "stadium",
        "fill": "#fee2e2",
        "stroke": "#ef4444",
        "font": "#991b1b",
        "x": 785, "y": 915, "w": 180, "h": 50,
    },
    {
        "id": "GATEWAY",
        "label": "6. Read-Only Gateway<br>(SQLite / Pool, 10s Budget)",
        "type": "cylinder",
        "fill": "#f3e8ff",
        "stroke": "#a855f7",
        "font": "#6b21a8",
        "x": 445, "y": 935, "w": 220, "h": 70,
    },
    {
        "id": "RECON",
        "label": "7. Result Reconciler<br>(Grain, Nulls, Fan-out, Registry)",
        "type": "rhombus",
        "fill": "#fee2e2",
        "stroke": "#ef4444",
        "font": "#991b1b",
        "x": 450, "y": 1055, "w": 210, "h": 100,
    },
    {
        "id": "LEDGER",
        "label": "8. Evidence Ledger<br>(Numbered Claims C1..Cn)",
        "type": "cylinder",
        "fill": "#ecfdf5",
        "stroke": "#10b981",
        "font": "#065f46",
        "x": 190, "y": 1215, "w": 220, "h": 70,
    },
    {
        "id": "ANALYSTS",
        "label": "9. Statistical Analysts (Pure Python)<br>• Anomaly: STL / ETS + PELT + FDR<br>• Driver: Price/Volume/Mix + DiD + Bootstrap<br>• Risk: Chronological Churn Model",
        "type": "rect",
        "fill": "#ffedd5",
        "stroke": "#f97316",
        "font": "#9a3412",
        "x": 490, "y": 1195, "w": 330, "h": 110,
    },
    {
        "id": "SYNTH",
        "label": "10. Narrative Synthesizer<br>(Findings, Interpretations, Actions)",
        "type": "rect",
        "fill": "#ecfdf5",
        "stroke": "#10b981",
        "font": "#065f46",
        "x": 180, "y": 1360, "w": 240, "h": 65,
    },
    {
        "id": "FACT",
        "label": "11. Deterministic Fact Checker<br>(Regex Numeric Audit &amp; Causal Filter)",
        "type": "rhombus",
        "fill": "#ecfdf5",
        "stroke": "#10b981",
        "font": "#065f46",
        "x": 210, "y": 1475, "w": 200, "h": 100,
    },
    {
        "id": "PURGE",
        "label": "Purge Claim / Template Fallback",
        "type": "rect",
        "fill": "#fee2e2",
        "stroke": "#ef4444",
        "font": "#991b1b",
        "x": 510, "y": 1495, "w": 240, "h": 60,
    },
    {
        "id": "ENVELOPE",
        "label": "AnswerEnvelope &amp; Trace DAG",
        "type": "stadium",
        "fill": "#ecfdf5",
        "stroke": "#10b981",
        "font": "#065f46",
        "x": 360, "y": 1640, "w": 260, "h": 55,
    },
]

node_map = {n["id"]: n for n in nodes}

# 2. Define edges
edges = [
    ("USER", "API", "Natural Language Query"),
    ("API", "GUARD", ""),
    ("GUARD", "REJECT", "Blocked"),
    ("GUARD", "ROUTER", "Sanitized"),
    ("ROUTER", "RESOLVER", "Lookup / KPI"),
    ("ROUTER", "PERCEPTOR", "Ad-Hoc SQL"),
    ("ROUTER", "REFUSAL", "Out-of-Catalog"),
    ("RESOLVER", "COMPILER", "Governed KPI"),
    ("RESOLVER", "PERCEPTOR", "Ungoverned"),
    ("PERCEPTOR", "GENERATOR", ""),
    ("GENERATOR", "VALIDATOR", ""),
    ("COMPILER", "VALIDATOR", ""),
    ("VALIDATOR", "REPAIRER", "Syntax / AST Error"),
    ("REPAIRER", "VALIDATOR", "Retry"),
    ("REPAIRER", "CLARIFY", "Exhausted"),
    ("VALIDATOR", "GATEWAY", "Approved"),
    ("GATEWAY", "RECON", ""),
    ("RECON", "LEDGER", "Lookup Output"),
    ("RECON", "ANALYSTS", "Diagnostic Intent"),
    ("ANALYSTS", "LEDGER", ""),
    ("LEDGER", "SYNTH", ""),
    ("SYNTH", "FACT", ""),
    ("FACT", "ENVELOPE", "Verified"),
    ("FACT", "PURGE", "Mismatch Detected"),
    ("PURGE", "ENVELOPE", ""),
]

# 3. Build Draw.io XML (<mxfile>)
mx_cells = [
    '<mxCell id="0"/>',
    '<mxCell id="1" parent="0"/>',
]

for n in nodes:
    nid = n["id"]
    lbl = html.escape(n["label"]).replace("&lt;br&gt;", "&#xa;")
    style = f"whiteSpace=wrap;html=1;fillColor={n['fill']};strokeColor={n['stroke']};fontColor={n['font']};strokeWidth=1.5;fontStyle=1;"
    if n["type"] == "stadium":
        style += "rounded=1;arcSize=50;"
    elif n["type"] == "rect":
        style += "rounded=1;arcSize=10;"
    elif n["type"] == "rhombus":
        style += "shape=rhombus;"
    elif n["type"] == "cylinder":
        style += "shape=cylinder3;boundedLbl=1;backgroundOutline=1;size=15;"

    cell = (
        f'<mxCell id="{nid}" value="{lbl}" style="{style}" vertex="1" parent="1">\n'
        f'  <mxGeometry x="{n["x"]}" y="{n["y"]}" width="{n["w"]}" height="{n["h"]}" as="geometry"/>\n'
        f'</mxCell>'
    )
    mx_cells.append(cell)

for idx, (src, tgt, lbl) in enumerate(edges, start=100):
    edge_lbl = html.escape(lbl)
    style = "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#64748b;strokeWidth=1.5;fontSize=11;fontColor=#475569;"
    cell = (
        f'<mxCell id="e_{idx}" value="{edge_lbl}" style="{style}" edge="1" parent="1" source="{src}" target="{tgt}">\n'
        f'  <mxGeometry relative="1" as="geometry"/>\n'
        f'</mxCell>'
    )
    mx_cells.append(cell)

mxfile_xml = (
    '<mxfile host="app.diagrams.net" modified="2026-10-26T00:00:00.000Z" agent="DS-Agent" version="21.0.0" type="device">\n'
    '  <diagram id="ds-agent-arch" name="DS-Agent Architecture">\n'
    '    <mxGraphModel dx="1200" dy="1800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1100" pageHeight="1800" math="0" shadow="0">\n'
    '      <root>\n'
    + "\n".join("        " + c for c in mx_cells) + "\n"
    '      </root>\n'
    '    </mxGraphModel>\n'
    '  </diagram>\n'
    '</mxfile>'
)

# 4. Generate Visual SVG Elements
svg_elements = []

def center(n):
    return n["x"] + n["w"] / 2.0, n["y"] + n["h"] / 2.0

# Add arrow markers
defs = """
  <defs>
    <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
      <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#64748b"/>
    </marker>
    <filter id="shadow" x="-5%" y="-5%" width="115%" height="115%">
      <feDropShadow dx="2" dy="3" stdDeviation="3" flood-color="#0f172a" flood-opacity="0.08"/>
    </filter>
  </defs>
"""

# Draw edges
for idx, (src_id, tgt_id, lbl) in enumerate(edges):
    src = node_map[src_id]
    tgt = node_map[tgt_id]
    sx, sy = center(src)
    tx, ty = center(tgt)

    # Calculate connection points
    if abs(sx - tx) < 30: # Vertical connection
        if ty > sy:
            p1 = (sx, src["y"] + src["h"])
            p2 = (tx, tgt["y"])
        else:
            p1 = (sx, src["y"])
            p2 = (tx, tgt["y"] + tgt["h"])
        d = f"M {p1[0]} {p1[1]} L {p2[0]} {p2[1]}"
        mid_x, mid_y = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    elif abs(sy - ty) < 40: # Horizontal connection
        if tx > sx:
            p1 = (src["x"] + src["w"], sy)
            p2 = (tgt["x"], ty)
        else:
            p1 = (src["x"], sy)
            p2 = (tgt["x"] + tgt["w"], ty)
        d = f"M {p1[0]} {p1[1]} L {p2[0]} {p2[1]}"
        mid_x, mid_y = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    else: # Orthogonal elbow connection
        if ty > sy:
            p1 = (sx, src["y"] + src["h"])
            p2 = (tx, tgt["y"])
            corner_y = (p1[1] + p2[1]) / 2
            d = f"M {p1[0]} {p1[1]} L {p1[0]} {corner_y} L {p2[0]} {corner_y} L {p2[0]} {p2[1]}"
            mid_x, mid_y = (p1[0] + p2[0]) / 2, corner_y
        else:
            p1 = (src["x"] + src["w"], sy)
            p2 = (tgt["x"] + tgt["w"], ty)
            mid_x = max(p1[0], p2[0]) + 40
            d = f"M {p1[0]} {p1[1]} L {mid_x} {p1[1]} L {mid_x} {p2[1]} L {p2[0]} {p2[1]}"
            mid_y = (p1[1] + p2[1]) / 2

    edge_svg = f'<path d="{d}" fill="none" stroke="#64748b" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" marker-end="url(#arrow)"/>'
    svg_elements.append(edge_svg)

    if lbl:
        lbl_w = len(lbl) * 6.5 + 14
        lbl_rect = f'<rect x="{mid_x - lbl_w/2}" y="{mid_y - 10}" width="{lbl_w}" height="20" rx="4" fill="#ffffff" stroke="#cbd5e1" stroke-width="1"/>'
        lbl_txt = f'<text x="{mid_x}" y="{mid_y + 4}" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif" font-size="11" font-weight="600" fill="#475569">{html.escape(lbl)}</text>'
        svg_elements.append(lbl_rect)
        svg_elements.append(lbl_txt)

# Draw nodes
for n in nodes:
    x, y, w, h = n["x"], n["y"], n["w"], n["h"]
    fill, stroke, font = n["fill"], n["stroke"], n["font"]
    ntype = n["type"]

    if ntype == "stadium":
        rx = h / 2
        shape = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" ry="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="1.8" filter="url(#shadow)"/>'
    elif ntype == "rect":
        shape = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" ry="8" fill="{fill}" stroke="{stroke}" stroke-width="1.8" filter="url(#shadow)"/>'
    elif ntype == "rhombus":
        pts = f"{x + w/2},{y} {x + w},{y + h/2} {x + w/2},{y + h} {x},{y + h/2}"
        shape = f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" stroke-width="1.8" filter="url(#shadow)"/>'
    elif ntype == "cylinder":
        # Draw 3D cylinder
        top_h = 14
        shape = (
            f'<path d="M {x} {y + top_h} A {w/2} {top_h} 0 0 0 {x + w} {y + top_h} L {x + w} {y + h - top_h} A {w/2} {top_h} 0 0 1 {x} {y + h - top_h} Z" fill="{fill}" stroke="{stroke}" stroke-width="1.8" filter="url(#shadow)"/>'
            f'<ellipse cx="{x + w/2}" cy="{y + top_h}" rx="{w/2}" ry="{top_h}" fill="{fill}" stroke="{stroke}" stroke-width="1.8"/>'
            f'<path d="M {x} {y + h - top_h} A {w/2} {top_h} 0 0 0 {x + w} {y + h - top_h}" fill="none" stroke="{stroke}" stroke-width="1.8"/>'
        )

    svg_elements.append(shape)

    # Render label text
    lines = n["label"].split("<br>")
    line_h = 16
    start_y = y + (h - len(lines) * line_h) / 2 + 12
    for l_idx, line in enumerate(lines):
        clean_line = html.unescape(line)
        # Check if line is bullet point
        is_bullet = clean_line.startswith("•")
        font_weight = "500" if is_bullet else "700"
        font_size = "11.5" if is_bullet else "12.5"
        text_anchor = "start" if is_bullet else "middle"
        text_x = x + 16 if is_bullet else x + w/2

        txt = (
            f'<text x="{text_x}" y="{start_y + l_idx * line_h}" text-anchor="{text_anchor}" '
            f'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif" font-size="{font_size}" '
            f'font-weight="{font_weight}" fill="{font}">{html.escape(clean_line)}</text>'
        )
        svg_elements.append(txt)

# Encode content attribute for Draw.io
encoded_content = html.escape(mxfile_xml, quote=True)

final_svg = f"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1" width="1080px" height="1760px" viewBox="0 0 1080 1760" content="{encoded_content}">
{defs}
  <rect width="100%" height="100%" fill="#f8fafc"/>
  <g transform="translate(10, 10)">
{"".join("    " + el + "\n" for el in svg_elements)}  </g>
</svg>
"""

with open("docs/architecture.drawio.svg", "w", encoding="utf-8") as f:
    f.write(final_svg)

print("Created docs/architecture.drawio.svg successfully. Size:", len(final_svg))
