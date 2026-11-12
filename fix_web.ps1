@'
"""
E:/Jericho/Private/anyjson/web_server.py
Веб-сервер и агент для anyjson + llama.cpp.
Запуск откуда угодно: py E:/Jericho/Private/anyjson/web_server.py
"""
import io
import sys
import os
import re
import json
import time
import threading
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Generator
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request
import urllib.error

_SCRIPT_DIR = Path(__file__).resolve().parent
_CORE_DIR = _SCRIPT_DIR / "_core"
for _p in (str(_CORE_DIR), str(_SCRIPT_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    os.chdir(_SCRIPT_DIR)
except Exception:
    pass
import asyncio
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
except Exception:
    pass
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("AnyJSON-Web")

_original_argv = sys.argv[:]
if len(sys.argv) < 2:
    sys.argv = [sys.argv[0], "WebAgent"]
try:
    from _config import BASE_DIR, AGENT_NAME, TOOLS_DIR, PUBLIC_TOOLS_DIR, WIKI_TEXTS_DIR, _WIKIGET_PATH
    from _actions import cmd_parser as _cmd_parser
    from _actions._tools_core import init_tools as _init_tools
    logger.info(f"Ядро anyjson загружено из: {_SCRIPT_DIR}")
finally:
    sys.argv = _original_argv

class AgentState:
    def __init__(self):
        self.history = []
        self.is_processing = False
        self.lock = threading.Lock()
        self.system_prompt = f"Привет, {AGENT_NAME}! Я выполняю JSON. Формат: текст + ```text\n{{\"команда\": {{\"параметр\": \"значение\"}}}}\n``` или heredoc <<<...>>>. Команды через |||."
    def add_entry(self, role, content, meta=None):
        with self.lock:
            self.history.append({"timestamp": time.strftime("%H:%M:%S"), "role": role, "content": content, "meta": meta or {}})
            if len(self.history) > 100: self.history = self.history[-100:]

state = AgentState()
_llm_messages = [{"role": "system", "content": state.system_prompt}]
_tools_initialized = False

def init_agent_tools():
    global _tools_initialized
    if _tools_initialized: return
    try:
        _init_tools(BASE_DIR, AGENT_NAME, wiki_texts_dir=WIKI_TEXTS_DIR, tools_dir=TOOLS_DIR, public_tools_dir=PUBLIC_TOOLS_DIR, wikiget_path=_WIKIGET_PATH)
        _tools_initialized = True
    except Exception as e:
        logger.error(f"Ошибка инструментов: {e}"); raise

def extract_json_from_text(text):
    if not text: return None
    m = re.search(r"```(?:json|text)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try: json.loads(m.group(1).strip()); return m.group(1).strip()
        except: pass
    s = text.find('{')
    if s != -1:
        b, i, e = 0, False, False
        for k in range(s, len(text)):
            c = text[k]
            if e: e = False; continue
            if c == '\\' and i: e = True; continue
            if c == '"' and not e: i = not i; continue
            if not i:
                if c == '{': b += 1
                elif c == '}':
                    b -= 1
                    if b == 0:
                        try: json.loads(text[s:k+1]); return text[s:k+1]
                        except: return None
    return None

def execute_anyjson_command(json_str):
    cap, old = io.StringIO(), sys.stdout
    try:
        sys.stdout = cap; _cmd_parser.process_command_input(json_str)
    except Exception as ex: return f"[ОШИБКА] {ex}"
    finally: sys.stdout = old
    return cap.getvalue().strip() or "[Нет вывода]"

def query_llama_stream(ctx):
    url = "http://127.0.0.1:8080/v1/chat/completions"
    data = json.dumps({"messages": ctx, "temperature": 0.7, "max_tokens": 2048, "stream": True}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            buf = ""
            while True:
                ch = resp.read(1)
                if not ch: break
                try: buf += ch.decode('utf-8')
                except: continue
                while "\n" in buf:
                    ln, buf = buf.split("\n", 1)
                    ln = ln.strip()
                    if not ln.startswith("data: "): continue
                    ds = ln[6:]
                    if ds == "[DONE]": return
                    try:
                        obj = json.loads(ds)
                        c = obj.get("choices", [{}])[0].get("delta", {}).get("content", "")
                        if c: yield c
                    except: continue
    except Exception as ex:
        yield f"\n[ОШИБКА LLM] {ex}"

def process_user_message_stream(user_text):
    init_agent_tools()
    _llm_messages.append({"role": "user", "content": user_text})
    state.add_entry("user", user_text)
    full = ""
    for _ in range(10):
        for tok in query_llama_stream(_llm_messages):
            full += tok; yield {"type": "token", "content": tok}
        if not full: yield {"type": "error", "content": "Пустой ответ"}; return
        state.add_entry("llm", full)
        _llm_messages.append({"role": "assistant", "content": full})
        cmd = extract_json_from_text(full)
        if not cmd: yield {"type": "done", "content": full}; return
        yield {"type": "tool_start", "content": cmd[:100]}
        state.is_processing = True
        try: res = execute_anyjson_command(cmd)
        finally: state.is_processing = False
        state.add_entry("tool_result", res)
        yield {"type": "tool_result", "content": res}
        _llm_messages.append({"role": "user", "content": f"Результат:\n{res}\n\nПродолжай."})
        full = ""
    yield {"type": "done", "content": full or "[Лимит]"}

HTML = """<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><title>AnyJSON</title>
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--bd:#30363d;--tx:#c9d1d9;--tx2:#8b949e;--ac:#58a6ff;--ub:#1f6feb;--lb:#238636;--tb:#9e6a03;--eb:#da3633}
*{box-sizing:border-box;margin:0;padding:0}body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--tx);height:100vh;display:flex;flex-direction:column;overflow:hidden}
.hd{background:var(--bg2);border-bottom:1px solid var(--bd);padding:16px 24px;display:flex;align-items:center;gap:12px;font-weight:600}
.dot{width:8px;height:8px;border-radius:50%;background:var(--tx2);transition:.3s}.dot.on{background:#238636;box-shadow:0 0 8px #238636;animation:p 2s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.5}}
.chat{flex:1;overflow-y:auto;padding:24px;display:flex;flex-direction:column;gap:16px}
.msg{display:flex;gap:12px;max-width:85%;animation:s .3s ease-out}.msg.u{align-self:flex-end;flex-direction:row-reverse}.msg.l{align-self:flex-start}.msg.t{align-self:flex-start;max-width:90%}.msg.e{align-self:center;max-width:90%}
@keyframes s{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:translateY(0)}}
.av{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0}
.msg.u .av{background:var(--ub)}.msg.l .av{background:var(--lb)}.msg.t .av{background:var(--tb)}
.bb{padding:12px 16px;border-radius:12px;line-height:1.5;font-size:14px;word-wrap:break-word;white-space:pre-wrap}
.msg.u .bb{background:#1f6feb22;border:1px solid var(--ub);border-bottom-right-radius:4px}
.msg.l .bb{background:#23863622;border:1px solid var(--lb);border-bottom-left-radius:4px}
.msg.t .bb{background:#9e6a0322;border:1px solid var(--tb);font-family:monospace;font-size:13px}
.msg.e .bb{background:#da363322;border:1px solid var(--eb);color:#ff7b72}
.ti{display:inline-flex;gap:4px;padding:4px 0}.ti span{width:6px;height:6px;background:var(--tx2);border-radius:50%;animation:b 1.4s infinite ease-in-out both}
.ti span:nth-child(1){animation-delay:-.32s}.ti span:nth-child(2){animation-delay:-.16s}
@keyframes b{0%,80%,100%{transform:scale(0)}40%{transform:scale(1)}}
.in{background:var(--bg2);border-top:1px solid var(--bd);padding:16px 24px;display:flex;gap:12px;align-items:flex-end}
#i{flex:1;background:var(--bg3);border:1px solid var(--bd);border-radius:8px;padding:12px 16px;color:var(--tx);font-family:inherit;font-size:14px;resize:none;min-height:48px;max-height:200px;outline:none}
#i:focus{border-color:var(--ac)}#b{background:var(--ac);color:#fff;border:none;border-radius:8px;padding:12px 24px;font-weight:600;cursor:pointer;height:48px}
#b:hover:not(:disabled){background:#1f6feb}#b:disabled{background:var(--bg3);color:var(--tx2);cursor:not-allowed}
</style></head><body>
<div class="hd"><div class="dot" id="d"></div>AnyJSON Web Agent</div>
<div class="chat" id="c"></div>
<div class="in"><textarea id="i" placeholder="Сообщение... (Enter - отправить)" rows="1"></textarea><button id="b">Отправить</button></div>
<script>
const c=document.getElementById('c'),i=document.getElementById('i'),b=document.getElementById('b'),d=document.getElementById('d');
i.addEventListener('input',function(){this.style.height='auto';this.style.height=Math.min(this.scrollHeight,200)+'px'});
function add(r,t,s){const v=document.createElement('div');v.className='msg '+r;const a=document.createElement('div');a.className='av';a.textContent=r==='u'?'👤':r==='l'?'🤖':r==='t'?'⚙️':'⚠️';const bb=document.createElement('div');bb.className='bb';bb.textContent=t;if(s&&r==='l'){const ti=document.createElement('div');ti.className='ti';ti.innerHTML='<span></span><span></span><span></span>';bb.appendChild(ti)}v.appendChild(a);v.appendChild(bb);c.appendChild(v);c.scrollTop=c.scrollHeight;return bb}
function upd(bb,t,rm){if(rm){const ti=bb.querySelector('.ti');if(ti)ti.remove()}const ti=bb.querySelector('.ti');if(ti)bb.insertBefore(document.createTextNode(t),ti);else bb.textContent=t;c.scrollTop=c.scrollHeight}
async function send(){const t=i.value.trim();if(!t)return;add('u',t);i.value='';i.style.height='auto';i.disabled=b.disabled=true;d.classList.add('on');const lb=add('l','',true);let f='';
try{const r=await fetch('/api/chat/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:t})});const rd=r.body.getReader();const dc=new TextDecoder();let bf='';
while(true){const{done,value}=await rd.read();if(done)break;bf+=dc.decode(value,{stream:true});const ls=bf.split('\n');bf=ls.pop();
for(const l of ls){if(!l.startsWith('data: '))continue;const ds=l.slice(6);if(ds==='[DONE]')continue;try{const e=JSON.parse(ds);if(e.type==='token'){f+=e.content;upd(lb,f)}else if(e.type==='tool_start')add('t','⚙️ '+e.content);else if(e.type==='tool_result')add('t','✅ '+e.content);else if(e.type==='error')add('e',e.content);else if(e.type==='done')upd(lb,f,true)}catch(e){}}}
upd(lb,f,true)}catch(e){add('e','Ошибка: '+e.message);upd(lb,f||'[Ошибка]',true)}finally{i.disabled=b.disabled=false;i.focus();d.classList.remove('on')}}
b.addEventListener('click',send);i.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send()}});i.focus();
</script></body></html>"""

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path=="/":
            self.send_response(200);self.send_header("Content-type","text/html; charset=utf-8");self.end_headers();self.wfile.write(HTML.encode('utf-8'))
        elif self.path=="/api/state":
            self.send_response(200);self.send_header("Content-type","application/json; charset=utf-8");self.end_headers()
            with state.lock:self.wfile.write(json.dumps({"is_processing":state.is_processing,"history":state.history},ensure_ascii=False).encode('utf-8'))
        else:self.send_error(404)
    def do_POST(self):
        if self.path!="/api/chat/stream":self.send_error(404);return
        cl=int(self.headers.get('Content-Length',0));body=self.rfile.read(cl).decode('utf-8')
        try:data=json.loads(body);ut=data.get("message","").strip()
        except:ut=body.strip()
        if not ut:self.send_response(400);self.end_headers();return
        self.send_response(200);self.send_header("Content-Type","text/event-stream; charset=utf-8");self.send_header("Cache-Control","no-cache");self.send_header("Connection","keep-alive");self.end_headers()
        try:
            for ev in process_user_message_stream(ut):
                ln=f"data: {json.dumps(ev, ensure_ascii=True)}\n\n";self.wfile.write(ln.encode('utf-8'));self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n");self.wfile.flush()
        except Exception as ex:
            self.wfile.write(f"data: {json.dumps({'type':'error','content':str(ex)}, ensure_ascii=True)}\n\n".encode('utf-8'));self.wfile.flush()
    def log_message(self,*a):pass

def main():
    try:init_agent_tools()
    except Exception as e:logger.critical(f"Инструменты: {e}");input("Enter...");sys.exit(1)
    srv=HTTPServer(("0.0.0.0",8081),H);logger.info("http://localhost:8081")
    try:srv.serve_forever()
    except KeyboardInterrupt:srv.shutdown()

if __name__=="__main__":main()
'@ | Set-Content -Path "E:\Jericho\Private\anyjson\web_server.py" -Encoding UTF8