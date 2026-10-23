"""Show a bounded Windows notification and click-through screen-edge highlight.
Invoke when the user needs to read the current chat. No recurring/background job.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument('message')
parser.add_argument('--title', default='에이전트 - 채팅 확인')
parser.add_argument('--seconds', type=int, default=12)
parser.add_argument('--color', choices=('cyan', 'red'), default='cyan')
parser.add_argument('--while-active', dest='while_active')
parser.add_argument('--lease-token', dest='lease_token')
parser.add_argument('--chat-title', help='Chat name shown when hovering over the blue edge; auto-detected in Codex')
args = parser.parse_args()
if not 3 <= args.seconds <= 30:
    parser.error('--seconds must be 3..30')
if args.while_active and (args.color != 'red' or not args.lease_token):
    parser.error('--while-active requires --color red and --lease-token')
if args.color == 'cyan' and not args.chat_title:
    thread = os.environ.get('CODEX_THREAD_ID')
    if thread:
        from usage_attribution import TitleCache
        home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
        try:
            name = TitleCache(home).get([thread], datetime.datetime.now().timestamp()).get(thread)
            if name and name != thread[:8]:
                args.chat_title = name
        except (OSError, ValueError):
            pass
    if not args.chat_title:
        args.chat_title = args.title
root = Path(__file__).resolve().parent
out = root/'logs'
out.mkdir(exist_ok=True)
request = out/(datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.json')
request.write_text(json.dumps(vars(args), ensure_ascii=False), encoding='utf-8')
shell = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
log = request.with_suffix('.log')
with log.open('wb') as output:
    process = subprocess.Popen(
        [str(shell), '-NoProfile', '-STA', '-File', str(Path(__file__).with_name('show-attention.ps1')),
         '-RequestPath', str(request)],
        stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW)
print(json.dumps({'pid': process.pid, 'request': str(request), 'status': str(request)+'.status.json'}, ensure_ascii=False))
