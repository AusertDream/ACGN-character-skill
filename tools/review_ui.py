"""
Review UI - Local HTML-based review interface for flagged dialogue events.

Generates a self-contained HTML page for reviewing events with review_required=True,
and applies corrections back to the JSONL file.
"""

from pathlib import Path
import json
import argparse

from tools.text_output import convert_jsonl_to_text


class ReviewServer:
    def __init__(self, jsonl_path: Path, crops_dir: Path, output_dir: Path):
        self.jsonl_path = jsonl_path
        self.crops_dir = crops_dir
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _load_events(self):
        events = []
        with open(self.jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def generate_review_html(self) -> Path:
        """Generate static HTML review page. Returns path to HTML file."""
        events = self._load_events()
        flagged = [e for e in events if e.get("review_required")]
        crops_rel = self.crops_dir.resolve().as_posix()
        cards_html = ""
        for ev in flagged:
            eid = ev.get("event_id", "")
            start, end = ev.get("start_ms", 0), ev.get("end_ms", 0)
            speaker = ev.get("speaker") or ""
            text = ev.get("text", "")
            conf = ev.get("confidence", 0)
            frame_file = ev.get("frame_file") or ""
            roi_file = ev.get("roi_crop_file") or ""
            candidates = ev.get("ocr_candidates") or []
            cand_html = ""
            if candidates:
                items = "".join(
                    f"<li>[{c.get('engine','?')}] {c.get('text','')} (置信度: {c.get('confidence',0):.2f})</li>"
                    for c in candidates
                )
                cand_html = f'<div class="field"><span class="label">OCR 候选:</span><ul>{items}</ul></div>'
            frame_html = ""
            if frame_file:
                frame_src = f"{crops_rel}/{frame_file}"
                frame_html = f'<div class="field"><span class="label">帧图像:</span><br><img src="{frame_src}" class="thumb"></div>'
            roi_html = ""
            if roi_file:
                roi_src = f"{crops_rel}/{roi_file}"
                roi_html = f'<div class="field"><span class="label">对话裁切:</span><br><img src="{roi_src}" class="thumb"></div>'
            name_file = ev.get("name_crop_file") or ""
            name_html = ""
            if name_file:
                name_src = f"{crops_rel}/{name_file}"
                name_html = f'<div class="field"><span class="label">名称裁切:</span><br><img src="{name_src}" class="thumb"></div>'
            selection_reason = ev.get("selection_reason") or ""
            reason_html = ""
            if selection_reason:
                reason_html = f'<div class="field"><span class="label">选取理由:</span> {selection_reason}</div>'
            cards_html += f"""
<div class="card" data-eid="{eid}">
  <div class="header">{eid} | {start}ms - {end}ms | 置信度: {conf:.2f}</div>
  <div class="field"><span class="label">说话人:</span>
    <input type="text" class="sp" value="{speaker}"></div>
  <div class="field"><span class="label">文本:</span>
    <textarea class="tx">{text}</textarea></div>
  {frame_html}{roi_html}{name_html}{cand_html}{reason_html}
  <div class="actions">
    <button class="btn accept" onclick="mark(this,'accept')">接受</button>
    <button class="btn flag" onclick="mark(this,'flag')">标记</button>
  </div>
</div>"""

        html = f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><title>对话审核</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:#f5f5f5;padding:20px}}
h1{{text-align:center;margin-bottom:20px}}
.card{{background:#fff;border:1px solid #ddd;border-radius:8px;padding:16px;margin-bottom:16px}}
.card.accepted{{border-left:4px solid #4caf50}}
.card.flagged{{border-left:4px solid #ff9800}}
.header{{font-weight:bold;margin-bottom:8px;color:#333}}
.field{{margin:6px 0}}.label{{font-weight:600;color:#555}}
input.sp{{width:200px;padding:4px 8px;border:1px solid #ccc;border-radius:4px}}
textarea.tx{{width:100%;min-height:60px;padding:4px 8px;border:1px solid #ccc;border-radius:4px;resize:vertical}}
.thumb{{max-width:480px;max-height:200px;margin-top:4px;border:1px solid #eee;border-radius:4px}}
ul{{margin:4px 0 4px 20px}}
.actions{{margin-top:8px;display:flex;gap:8px}}
.btn{{padding:6px 16px;border:none;border-radius:4px;cursor:pointer;font-size:14px}}
.btn.accept{{background:#4caf50;color:#fff}}.btn.flag{{background:#ff9800;color:#fff}}
.btn:hover{{opacity:0.85}}
#toolbar{{text-align:center;margin-bottom:20px}}
#toolbar button{{padding:8px 24px;font-size:15px;border:none;border-radius:4px;background:#1976d2;color:#fff;cursor:pointer}}
#toolbar button:hover{{opacity:0.85}}
.summary{{text-align:center;color:#666;margin-bottom:12px}}
</style></head><body>
<h1>对话事件审核</h1>
<div class="summary">共 {len(flagged)} 条待审核事件</div>
<div id="toolbar"><button onclick="exportCorrections()">导出修正结果</button></div>
{cards_html}
<script>
function mark(btn, action) {{
  var card = btn.closest('.card');
  card.classList.remove('accepted','flagged');
  card.classList.add(action === 'accept' ? 'accepted' : 'flagged');
  card.dataset.action = action;
}}
function exportCorrections() {{
  var cards = document.querySelectorAll('.card');
  var results = [];
  cards.forEach(function(c) {{
    results.push({{
      event_id: c.dataset.eid,
      speaker: c.querySelector('.sp').value,
      text: c.querySelector('.tx').value,
      accepted: c.dataset.action === 'accept'
    }});
  }});
  var blob = new Blob([JSON.stringify(results, null, 2)], {{type:'application/json'}});
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'corrections.json';
  a.click();
}}
</script></body></html>"""

        out_path = self.output_dir / "review.html"
        out_path.write_text(html, encoding="utf-8")
        return out_path

    def apply_corrections(self, corrections_json: Path):
        """Apply corrections from review and re-export JSONL/TXT."""
        with open(corrections_json, "r", encoding="utf-8") as f:
            corrections = json.load(f)
        corr_map = {c["event_id"]: c for c in corrections}

        events = self._load_events()
        for ev in events:
            eid = ev.get("event_id")
            if eid in corr_map:
                c = corr_map[eid]
                ev["speaker"] = c["speaker"]
                ev["text"] = c["text"]
                if c.get("accepted"):
                    ev["review_required"] = False

        with open(self.jsonl_path, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")

        txt_path = self.jsonl_path.with_suffix(".txt")
        convert_jsonl_to_text(self.jsonl_path, txt_path, include_review_flagged=False)
        review_txt_path = self.jsonl_path.with_name(self.jsonl_path.stem + "_review.txt")
        convert_jsonl_to_text(self.jsonl_path, review_txt_path, include_review_flagged=True)
        print(f"[review] Clean transcript: {txt_path}")
        print(f"[review] Review transcript: {review_txt_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="对话事件审核工具")
    parser.add_argument("jsonl_path", type=Path, help="JSONL 文件路径")
    parser.add_argument("--corrections", type=Path, default=None, help="修正 JSON 文件路径")
    parser.add_argument("--output-dir", type=Path, default=None, help="输出目录")
    args = parser.parse_args()

    jsonl = args.jsonl_path.resolve()
    crops = jsonl.parent / "crops"
    out_dir = (args.output_dir or jsonl.parent).resolve()

    server = ReviewServer(jsonl, crops, out_dir)

    if args.corrections:
        server.apply_corrections(args.corrections.resolve())
        print(f"已应用修正并重新导出: {jsonl}")
    else:
        html_path = server.generate_review_html()
        print(f"审核页面已生成: {html_path}")
