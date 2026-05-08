#!/usr/bin/env python3
"""
Web-based ROI annotator — annotate all videos in one session.

Start:  python -m tools.roi_annotator [--port 8765]
Then open http://localhost:8765 in a browser.

Left panel shows all video/config pairs. Click to switch.
Draw dialog box (blue) then name box (red). Save writes back to YAML.
"""
import argparse, io, json, random, sys
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

import av, yaml
from PIL import Image

DATA_DIR = Path("/data2/training_data")
CONFIG_DIR = Path("tools/configs")

# ── video/config discovery ──
VIDEO_PATTERNS = {
    "ep18side": "崩坏三舰长线全剧情合集第十八节主要支线",
    "ep01":     "崩坏三舰长线全剧情合集，第一节",
    "ep18p1":   "崩坏三舰长线全剧情合集第十八节第一部分",
    "ep18p2":   "崩坏三舰长线全剧情合集第十八节第二部分",
    "ep18p3":   "崩坏三舰长线全剧情合集第十八节第三部分",
    "ep17":     "崩坏三舰长线全剧情合集第十七节",
    "ep19":     "崩坏三舰长线全剧情合集第十九节",
}

def discover_videos():
    items = []
    for key, pattern in VIDEO_PATTERNS.items():
        mp4s = sorted(DATA_DIR.glob(pattern + "*.mp4"))
        cfg = CONFIG_DIR / f"yuexia_{key}_roi.yaml"
        if mp4s:
            items.append({"key": key, "video": str(mp4s[0]), "config": str(cfg)})
    return items

VIDEOS = discover_videos()

# ── per-video state ──
STATE = {
    "key": None,
    "video_path": None,
    "config_path": None,
    "config": {},
    "container": None,
    "total_frames": 1000,
}

def switch_video(key):
    item = next((v for v in VIDEOS if v["key"] == key), None)
    if not item:
        return False
    if STATE.get("container"):
        try: STATE["container"].close()
        except: pass
    container = av.open(item["video"])
    stream = container.streams.video[0]
    cfg_path = Path(item["config"])
    cfg = yaml.safe_load(open(cfg_path)) if cfg_path.exists() else {
        "work_id": key, "name": key,
        "dialog_box": {"x": 0.10, "y": 0.80, "w": 0.70, "h": 0.12},
        "name_box":   {"x": 0.10, "y": 0.70, "w": 0.12, "h": 0.06},
    }
    STATE.update({"key": key, "video_path": item["video"], "config_path": cfg_path,
                  "config": cfg, "container": container,
                  "total_frames": stream.frames or 1000})
    return True

def get_random_frame():
    container = STATE.get("container")
    if not container:
        return None
    stream = container.streams.video[0]
    duration = stream.duration
    time_base = stream.time_base
    if duration and time_base:
        max_ts = int(duration)
    else:
        max_ts = int(60 * 30 / time_base) if time_base else 100000
    for _ in range(20):
        target_ts = random.randint(0, max(1, max_ts - 1))
        try:
            container.seek(target_ts, stream=stream)
        except Exception:
            continue
        try:
            for frame in container.decode(video=0):
                buf = io.BytesIO()
                frame.to_image().save(buf, format="JPEG", quality=85)
                return buf.getvalue()
        except Exception:
            continue
    # Fallback: seek to beginning and grab first frame
    try:
        container.seek(0, stream=stream)
        for frame in container.decode(video=0):
            buf = io.BytesIO()
            frame.to_image().save(buf, format="JPEG", quality=85)
            return buf.getvalue()
    except Exception:
        pass
    return None

# ── HTTP handler ──
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode())
        elif self.path.startswith("/frame"):
            img = get_random_frame()
            if not img: self.send_error(500); return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(img)
        elif self.path == "/videos":
            self._json([{"key": v["key"], "active": v["key"] == STATE["key"]} for v in VIDEOS])
        elif self.path == "/config":
            cfg = STATE["config"]
            self._json({"key": STATE["key"], "dialog_box": cfg.get("dialog_box", {}),
                        "name_box": cfg.get("name_box", {})})
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(length))
        if self.path == "/switch":
            ok = switch_video(data["key"])
            self._json({"ok": ok, "key": STATE["key"],
                        "dialog_box": STATE["config"].get("dialog_box", {}),
                        "name_box": STATE["config"].get("name_box", {})})
        elif self.path == "/save":
            for field in ("dialog_box", "name_box"):
                if field in data and data[field]:
                    b = data[field]
                    STATE["config"][field] = {k: round(b[k], 4) for k in "xywh"}
            cfg_path = STATE["config_path"]
            if cfg_path:
                cfg_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cfg_path, "w") as f:
                    yaml.dump(STATE["config"], f, allow_unicode=True,
                              default_flow_style=False, sort_keys=False)
            self._json({"ok": True, "dialog_box": STATE["config"].get("dialog_box"),
                        "name_box": STATE["config"].get("name_box")})
        else:
            self.send_error(404)

HTML = """<!DOCTYPE html>
<html lang="zh"><head>
<meta charset="utf-8">
<title>ROI Annotator</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#1a1a2e;color:#eee;font:13px monospace;display:flex;height:100vh}
#sidebar{width:260px;background:#16213e;display:flex;flex-direction:column;overflow:hidden}
#video-list{flex:1;overflow-y:auto;padding:8px}
.vitem{padding:8px 10px;cursor:pointer;border-radius:4px;margin-bottom:4px;border:1px solid #333}
.vitem:hover{background:#0f3460}
.vitem.active{background:#0f3460;border-color:#4a9eff}
.vitem .key{font-weight:bold;color:#4a9eff}
.vitem .status{font-size:11px;color:#888;margin-top:2px}
#controls{padding:10px;border-top:1px solid #333;display:flex;flex-direction:column;gap:6px}
#coords{font-size:11px;color:#888;padding:6px 10px;border-top:1px solid #333}
#main{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:8px}
canvas{max-width:100%;max-height:90vh;cursor:crosshair}
button{padding:7px 12px;border:none;border-radius:4px;cursor:pointer;font:12px monospace;width:100%}
.btn-blue{background:#0f3460;color:#eee}.btn-red{background:#533483;color:#eee}
.btn-green{background:#1b6b3a;color:#eee}.btn-gray{background:#333;color:#eee}
button:hover{opacity:.85}
#status{padding:6px 10px;font-size:11px;color:#aaa;border-top:1px solid #333}
.legend{display:flex;gap:12px;padding:6px 10px;font-size:11px;border-top:1px solid #333}
.dot{display:inline-block;width:10px;height:10px;margin-right:4px;vertical-align:middle}
</style></head>
<body>
<div id="sidebar">
  <div style="padding:10px;font-size:14px;font-weight:bold;border-bottom:1px solid #333">ROI Annotator</div>
  <div class="legend">
    <span><span class="dot" style="background:rgba(0,100,255,0.7)"></span>Dialog</span>
    <span><span class="dot" style="background:rgba(255,50,50,0.7)"></span>Name</span>
  </div>
  <div id="video-list"></div>
  <div id="coords">—</div>
  <div id="controls">
    <button class="btn-blue" onclick="refreshFrame()">🔄 Refresh Frame (R)</button>
    <div style="display:flex;gap:4px">
      <button class="btn-blue" onclick="setMode('dialog')" style="flex:1">Dialog (D)</button>
      <button class="btn-red" onclick="setMode('name')" style="flex:1">Name (N)</button>
    </div>
    <button class="btn-gray" onclick="clearBoxes()">✕ Clear All</button>
    <button class="btn-green" onclick="saveConfig()">💾 Save (Ctrl+S)</button>
  </div>
  <div id="status">Select a video to start</div>
</div>
<div id="main"><canvas id="canvas"></canvas></div>

<script>
let img=new Image(),imgW=0,imgH=0,dialogBox=null,nameBox=null;
let drawing=null,dragBox=null,startX=0,startY=0,mode='dialog',pendingConfig=null;
const canvas=document.getElementById('canvas'),ctx=canvas.getContext('2d');

function setMode(m){mode=m;document.getElementById('status').textContent='Mode: '+m+' | Click+drag to draw';}

function loadVideos(){
  fetch('/videos').then(r=>r.json()).then(vs=>{
    const el=document.getElementById('video-list');
    el.innerHTML=vs.map(v=>`<div class="vitem${v.active?' active':''}" onclick="switchVideo('${v.key}')">
      <div class="key">${v.key}</div></div>`).join('');
  });
}

function switchVideo(key){
  document.getElementById('status').textContent='Loading '+key+'...';
  fetch('/switch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key})})
    .then(r=>r.json()).then(d=>{
      if(!d.ok){document.getElementById('status').textContent='Failed to load '+key;return;}
      pendingConfig=d;
      loadVideos();
      refreshFrame();
      document.getElementById('status').textContent='Loaded: '+key;
    });
}

function loadBoxesFromConfig(cfg){
  function toPixels(b){if(!b||!b.x)return null;return{x:b.x*imgW,y:b.y*imgH,w:b.w*imgW,h:b.h*imgH};}
  dialogBox=toPixels(cfg.dialog_box);
  nameBox=toPixels(cfg.name_box);
}

function refreshFrame(){
  fetch('/frame?'+Date.now()).then(r=>{
    if(!r.ok) throw new Error('frame fetch failed');
    return r.blob();
  }).then(blob=>{
    const url=URL.createObjectURL(blob);
    img.onload=()=>{
      imgW=img.naturalWidth;imgH=img.naturalHeight;
      canvas.width=imgW;canvas.height=imgH;
      if(pendingConfig){
        loadBoxesFromConfig(pendingConfig);
        pendingConfig=null;
      } else {
        fetch('/config').then(r=>r.json()).then(cfg=>{loadBoxesFromConfig(cfg);drawBoxes();});
        return;
      }
      drawBoxes();
      URL.revokeObjectURL(url);
    };
    img.src=url;
  }).catch(e=>{
    document.getElementById('status').textContent='Error loading frame: '+e.message;
  });
}

function drawBoxes(){
  if(!imgW)return;
  ctx.drawImage(img,0,0);
  if(dialogBox)drawRect(dialogBox,'rgba(0,100,255,0.3)','#4a9eff',3);
  if(nameBox)drawRect(nameBox,'rgba(255,50,50,0.3)','#ff5555',3);
  updateCoords();
}

function drawRect(b,fill,stroke,lw){
  ctx.fillStyle=fill;ctx.fillRect(b.x,b.y,b.w,b.h);
  ctx.strokeStyle=stroke;ctx.lineWidth=lw;ctx.strokeRect(b.x,b.y,b.w,b.h);
}

function updateCoords(){
  const n=v=>v?v.toFixed(4):'—';
  const d=dialogBox,nb=nameBox;
  document.getElementById('coords').innerHTML=
    `<b>dialog:</b> x=${n(d&&d.x/imgW)} y=${n(d&&d.y/imgH)} w=${n(d&&d.w/imgW)} h=${n(d&&d.h/imgH)}<br>`+
    `<b>name:</b>   x=${n(nb&&nb.x/imgW)} y=${n(nb&&nb.y/imgH)} w=${n(nb&&nb.w/imgW)} h=${n(nb&&nb.h/imgH)}`;
}

function clearBoxes(){dialogBox=null;nameBox=null;ctx.drawImage(img,0,0);updateCoords();}

function saveConfig(){
  function norm(b){if(!b)return null;return{x:b.x/imgW,y:b.y/imgH,w:b.w/imgW,h:b.h/imgH};}
  fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({dialog_box:norm(dialogBox),name_box:norm(nameBox)})})
    .then(r=>r.json()).then(d=>{
      document.getElementById('status').textContent='✓ Saved!';
    });
}

// Mouse
function getPos(e){
  const r=canvas.getBoundingClientRect();
  return{x:(e.clientX-r.left)*imgW/r.width,y:(e.clientY-r.top)*imgH/r.height};
}
function inBox(p,b){return b&&p.x>=b.x&&p.x<=b.x+b.w&&p.y>=b.y&&p.y<=b.y+b.h;}

canvas.addEventListener('mousedown',e=>{
  const p=getPos(e);
  if(inBox(p,nameBox)){dragBox='name';startX=p.x-nameBox.x;startY=p.y-nameBox.y;return;}
  if(inBox(p,dialogBox)){dragBox='dialog';startX=p.x-dialogBox.x;startY=p.y-dialogBox.y;return;}
  drawing={mode,x:p.x,y:p.y,w:0,h:0};startX=p.x;startY=p.y;
});
canvas.addEventListener('mousemove',e=>{
  const p=getPos(e);
  if(dragBox){
    const b=dragBox==='dialog'?dialogBox:nameBox;
    b.x=p.x-startX;b.y=p.y-startY;drawBoxes();return;
  }
  if(drawing){
    drawing.w=p.x-startX;drawing.h=p.y-startY;
    ctx.drawImage(img,0,0);drawBoxes();
    const x=drawing.w>=0?drawing.x:drawing.x+drawing.w;
    const y=drawing.h>=0?drawing.y:drawing.y+drawing.h;
    drawRect({x,y,w:Math.abs(drawing.w),h:Math.abs(drawing.h)},
      drawing.mode==='dialog'?'rgba(0,100,255,0.2)':'rgba(255,50,50,0.2)',
      drawing.mode==='dialog'?'#4a9eff':'#ff5555',2);
  }
});
canvas.addEventListener('mouseup',()=>{
  if(dragBox){dragBox=null;updateCoords();return;}
  if(drawing){
    const w=Math.abs(drawing.w),h=Math.abs(drawing.h);
    if(w>5&&h>5){
      const b={x:Math.round(drawing.w>=0?drawing.x:drawing.x+drawing.w),
               y:Math.round(drawing.h>=0?drawing.y:drawing.y+drawing.h),
               w:Math.round(w),h:Math.round(h)};
      if(drawing.mode==='dialog'){dialogBox=b;mode='name';}
      else{nameBox=b;mode='dialog';}
    }
    drawing=null;drawBoxes();
  }
});

document.addEventListener('keydown',e=>{
  if(e.key==='r')refreshFrame();
  else if(e.key==='d')setMode('dialog');
  else if(e.key==='n')setMode('name');
  else if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();saveConfig();}
});

loadVideos();
</script>
</body></html>"""

def main():
    parser = argparse.ArgumentParser(description="Web-based ROI annotator for all videos")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if not VIDEOS:
        print("No videos found in", DATA_DIR, file=sys.stderr)
        sys.exit(1)

    # Pre-load first video
    switch_video(VIDEOS[0]["key"])

    print(f"Found {len(VIDEOS)} videos: {[v['key'] for v in VIDEOS]}")
    print(f"\n  http://localhost:{args.port}")
    print(f"  R=refresh  D=dialog mode  N=name mode  Ctrl+S=save\n")

    server = HTTPServer(("0.0.0.0", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")

if __name__ == "__main__":
    main()
