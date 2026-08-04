import urllib.request, json, time
import sys

task_id = sys.argv[1] if len(sys.argv) > 1 else None
if not task_id:
    print("Usage: python check_task.py <task_id>")
    sys.exit(1)

# Wait for completion
for i in range(6):
    time.sleep(5)
    resp = json.loads(urllib.request.urlopen(f'http://localhost:8001/api/v1/tasks/{task_id}').read())
    status = resp['task']['status']
    phase = resp['task']['phase']
    tokens = resp['task']['total_tokens']
    print(f"[{i*5}s] phase={phase} status={status} tokens={tokens}")
    if status in ('done', 'error', 'failed'):
        break

files = resp.get('files', [])
print(f"\n生成文件数: {len(files)}")
for f in files:
    path = f.get('path', '?')
    content = f.get('content', '')
    print(f"  {path}: {len(content)} 字符")
    if content:
        preview = content[:200].replace('\n', '\\n')
        print(f"    预览: {preview}...")
    else:
        print(f"    ⚠️ 空文件！")

r = resp.get('review')
if r:
    print(f"\n审查: verdict={r['verdict']} summary={r['summary'][:100]}")
    if r.get('issues'):
        for issue in r['issues'][:3]:
            print(f"  [{issue['severity']}] {issue['file_path']}: {issue['message'][:80]}")
else:
    print("\n审查: N/A")