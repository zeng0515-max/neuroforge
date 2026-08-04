import sqlite3
conn = sqlite3.connect('data/app.db')
# Find task id
task_row = conn.execute("SELECT id FROM task WHERE task_id=?", ['4004bc56ed3f']).fetchone()
if task_row:
    task_db_id = task_row[0]
    print(f"Task DB ID: {task_db_id}")
    files = conn.execute("SELECT id, path, content, line_count FROM file WHERE task_id=?", [task_db_id]).fetchall()
    for f in files:
        print(f"  id={f[0]} path={f[1]} content_len={len(f[2] or '')} lines={f[3]}")
        if f[2]:
            print(f"    预览: {f[2][:200]}")
        else:
            print(f"    ⚠️ 空内容！")
else:
    print("Task not found")