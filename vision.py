import json, os, subprocess, sys, base64
from pathlib import Path
from time import time

LOCALAPPDATA = os.environ.get('LOCALAPPDATA', '')
MODLENS_SCRIPT = Path(LOCALAPPDATA) / '.dsh' / 'profiles' / 'desktop' / 'node_modules' / '@liustack' / 'modlens' / 'dist' / 'main.js'

def _run_modlens(img_path_or_url):
    try:
        cmd = ['node', str(MODLENS_SCRIPT), '-i', img_path_or_url]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, shell=False)
    except Exception as e:
        return {'error': str(e)}
    try:
        res = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return {'error': 'decode failed', 'stderr': proc.stderr[:500]}
    return res

def describe_image(img_path_or_url, kind="screen", local_ai_fallback=None):
    # modlens first
    txt = ''
    res = _run_modlens(img_path_or_url)
    if isinstance(res, dict):
        txt = res.get('summary') or (res.get('result') or {}).get('summary', '')
        ocr = ((res.get('ocr') or {}).get('full_text') or '')
        if ocr:
            txt = (txt + '\nOCR:\n' + ocr[:400]).strip()
    if txt:
        return txt[:1200]
    # fallback local
    if local_ai_fallback is not None and hasattr(local_ai_fallback, "describe_image"):
        try:
            with open(img_path_or_url, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            t = local_ai_fallback.describe_image(b64, kind)
            if t:
                return t[:1200]
        except Exception as e:
            return f"Failed to describe image locally (vision: {e})"
    return 'No vision provider configured. Run: modlens doctor or install a vision provider (Gemini key / Antigravity / local vision model).'

def provide_vision_context(img_path_or_url, kind="screen", local_ai_fallback=None):
    return f'[IMAGE DESCRIPTION modlens]: {describe_image(img_path_or_url, kind=kind, local_ai_fallback=local_ai_fallback)}'

if len(sys.argv) > 1 and sys.argv[1] == '--self-test':
    print(describe_image(sys.argv[2]))
    sys.exit(0)
