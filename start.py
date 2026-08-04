"""多智能体协同代码生成与审查系统 - 一键启动脚本。

功能：
1. 检查 / 创建虚拟环境并安装依赖
2. 检查 .env 配置文件
3. 初始化向量知识库（失败不阻断）
4. 启动 FastAPI 服务（同时托管前端 + 后端 API）
5. 自动打开默认浏览器到前端页面

使用：
    python start.py              默认启动 + 自动开浏览器
    python start.py --reload     开发模式（代码变动自动重载）
    python start.py --no-browser 仅启动服务，不自动打开浏览器
    python start.py --restart    强制结束旧进程并重启（端口被占时用）

架构说明：
    单进程模型，FastAPI 同时托管：
      - 前端静态页：http://localhost:8001/       (会 301 跳转到 /web/)
      - 后端 API：  http://localhost:8001/api/v1/
      - Swagger：   http://localhost:8001/docs
    无需独立前端 dev server，启动即用。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

# ------------------------------------------------------------------ #
#  路径常量
# ------------------------------------------------------------------ #
PROJECT_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
VENV_PIP = PROJECT_ROOT / ".venv" / "Scripts" / "pip.exe"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
INIT_SCRIPT = PROJECT_ROOT / "scripts" / "init_vector_db.py"

# 服务配置
HOST = "0.0.0.0"
PORT = 8001
FRONTEND_URL = f"http://localhost:{PORT}/"
API_URL = f"http://localhost:{PORT}/api/v1/"
DOCS_URL = f"http://localhost:{PORT}/docs"


def _step(idx: str, msg: str) -> None:
    """打印步骤信息。"""
    print(f"[{idx}] {msg}")


def _ok(msg: str) -> None:
    print(f"    ✓ {msg}")


def _err(msg: str) -> None:
    print(f"    ✗ {msg}")


def _run(cmd: list[str], **kwargs) -> int:
    """运行子命令，实时输出。"""
    return subprocess.call(cmd, cwd=str(PROJECT_ROOT), **kwargs)


# ------------------------------------------------------------------ #
#  步骤 1：虚拟环境
# ------------------------------------------------------------------ #
def ensure_venv() -> str:
    """检查虚拟环境，不存在则创建并装依赖。返回 python 可执行路径。"""
    _step("1/5", "检查虚拟环境 ...")

    if VENV_PYTHON.exists():
        _ok("虚拟环境已就绪")
        return str(VENV_PYTHON)

    _err("未找到虚拟环境，正在创建 .venv ...")
    rc = _run([sys.executable, "-m", "venv", ".venv"])
    if rc != 0:
        _err("创建虚拟环境失败，请确认已安装 Python 3.10+")
        sys.exit(1)

    _ok("正在升级 pip ...")
    _run([str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip", "-q"])

    _ok("正在安装依赖（首次较慢，请耐心等待）...")
    rc = _run([str(VENV_PIP), "install", "-r", str(REQUIREMENTS), "-q"])
    if rc != 0:
        _err("依赖安装失败，请手动执行: .venv\\Scripts\\pip install -r requirements.txt")
        sys.exit(1)

    _ok("依赖安装完成")
    return str(VENV_PYTHON)


# ------------------------------------------------------------------ #
#  步骤 2：配置文件
# ------------------------------------------------------------------ #
def ensure_env() -> None:
    """检查 .env，不存在则从模板复制。"""
    _step("2/5", "检查配置文件 ...")

    if ENV_FILE.exists():
        _ok(".env 已就绪")
        return

    if ENV_EXAMPLE.exists():
        import shutil
        shutil.copy(str(ENV_EXAMPLE), str(ENV_FILE))
        _ok("已从模板生成 .env")
        print()
        print("    请编辑 .env 填入真实的 LLM_API_KEY 后重新运行本脚本。")
        print()
        # 尝试用默认编辑器打开
        try:
            os.startfile(str(ENV_FILE))  # type: ignore[attr-defined]
        except Exception:
            pass
        sys.exit(0)
    else:
        _err("未找到 .env.example 模板，请手动创建 .env")
        sys.exit(1)


# ------------------------------------------------------------------ #
#  步骤 3：向量库
# ------------------------------------------------------------------ #
def ensure_rag(python: str) -> None:
    """初始化向量知识库（失败不阻断启动）。"""
    _step("3/5", "检查向量知识库 ...")

    check_code = (
        "import sys; sys.path.insert(0,'.'); "
        "from app.tools.rag_service import rag; "
        "print(rag.count())"
    )
    rc = _run([python, "-c", check_code],
              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if rc == 0:
        _ok("RAG 已就绪")
        return

    _ok("RAG 未初始化，正在灌入种子数据 ...")
    if INIT_SCRIPT.exists():
        _run([python, str(INIT_SCRIPT)])
    else:
        _err("未找到 scripts/init_vector_db.py，跳过")


# ------------------------------------------------------------------ #
#  步骤 4：端口检查 + 自动清理
# ------------------------------------------------------------------ #
def _get_listening_pid(port: int) -> int | None:
    """返回占用指定端口 LISTENING 状态的进程 PID，未找到返回 None。"""
    try:
        import re
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, encoding="gbk", errors="ignore"
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            if parts[0].upper() != "TCP":
                continue
            # 本地地址末尾是 :port，形如 0.0.0.0:8001 或 127.0.0.1:8001
            local = parts[1]
            if not local.endswith(f":{port}"):
                continue
            state = parts[3] if len(parts) >= 5 else ""
            if state != "LISTENING":
                continue
            pid_str = parts[-1]
            if pid_str.isdigit():
                return int(pid_str)
    except Exception:
        pass
    return None


def _process_name(pid: int) -> str:
    """返回进程名（尽力而为，失败返回空字符串）。"""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="gbk", errors="ignore"
        )
        # CSV 格式: "python.exe","12345",...
        # tasklist 无匹配时会输出提示信息（中/英文），需排除
        no_match_markers = ("没有运行的任务匹配指定标准", "no tasks are running")
        for line in result.stdout.splitlines():
            stripped = line.strip('"').strip()
            if not stripped:
                continue
            if any(m in stripped.lower() for m in no_match_markers):
                continue
            parts = stripped.split('","')
            if parts:
                return parts[0]
    except Exception:
        pass
    return ""


def _kill_pid_tree(pid: int) -> bool:
    """强制结束进程及其所有子进程（树式 kill）。

    使用 taskkill /T /F 递归结束整个进程树，解决 uvicorn --reload
    模式下 reloader 父进程与 worker 子进程的残留问题。
    """
    rc = subprocess.call(
        ["taskkill", "/T", "/F", "/PID", str(pid)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return rc == 0


def _is_process_alive(pid: int) -> bool:
    """判断指定 PID 的进程是否仍在运行。

    用于区分"进程存活需 kill"与"进程已死但内核 socket 残留（zombie）"。
    """
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="gbk", errors="ignore"
        )
        no_match_markers = ("没有运行的任务匹配指定标准", "no tasks are running")
        for line in result.stdout.splitlines():
            stripped = line.strip().strip('"').strip()
            if not stripped:
                continue
            if any(m in stripped.lower() for m in no_match_markers):
                continue
            return True  # 找到有效进程行
        return False
    except Exception:
        return False


def _wait_port_free(port: int, timeout: float = 15.0, interval: float = 0.5) -> bool:
    """轮询等待端口真正释放（含 Windows 内核 socket zombie 回收）。

    kill 进程后端口释放是异步的，单次检查可能误判；zombie socket
    （进程已死但 LISTENING 状态未回收）通常 2-30s 内由内核释放。
    """
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.3)
        try:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return True
        finally:
            sock.close()
        time.sleep(interval)
    return False


def _find_orphan_uvicorn_pids(port: int) -> list[int]:
    """找出持有端口的孤立 uvicorn worker PID。

    uvicorn --reload 用 multiprocessing.spawn 创建 worker，父 reloader 被杀后
    worker 会继承 socket fd 继续服务，但 netstat 报告的 PID 是已死的父进程，
    导致常规 kill 定位失败。本函数扫描所有 python 进程，返回命令行匹配
    uvicorn/app.main/multiprocessing.spawn 的 PID（排除当前进程）。
    """
    pids: list[int] = []
    ps_script = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | "
        "Where-Object { $_.CommandLine -match 'uvicorn|app\\.main|multiprocessing\\.spawn' } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=5
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                pid = int(line)
                if pid != os.getpid():
                    pids.append(pid)
    except Exception:
        pass
    return pids


def force_release_port(port: int) -> bool:
    """强制释放端口：树式 kill 占用进程 + 轮询等待内核 socket 回收。

    用于 --restart 场景，彻底清理 uvicorn --reload 父子进程树与 zombie socket。
    """
    _step("4/5", f"强制释放端口 {port} ...")
    current_pid = os.getpid()

    killed_any = False
    for attempt in range(5):
        pid = _get_listening_pid(port)
        if pid is None:
            break
        if pid == current_pid:
            _err(f"当前进程正占用端口 {port}，无法自我重启")
            return False

        # 进程已死但 socket 残留（zombie），无需 kill，直接等待内核回收
        if not _is_process_alive(pid):
            _ok(f"PID {pid} 已退出，socket 残留中，等待内核回收 ...")
            killed_any = True
            break

        if _kill_pid_tree(pid):
            _ok(f"已结束进程树 PID {pid}")
            killed_any = True
        else:
            _err(f"无法结束进程 {pid}，尝试下一次轮询")
        time.sleep(0.5)

    if not killed_any and _get_listening_pid(port) is None:
        _ok(f"端口 {port} 已释放")
        return True

    # 轮询等待端口真正释放（含 zombie socket 回收）
    if _wait_port_free(port, timeout=10.0, interval=0.5):
        _ok(f"端口 {port} 已释放")
        return True

    # zombie socket 未释放，可能是孤立的 uvicorn worker 持有 fd，扫描清理
    orphans = _find_orphan_uvicorn_pids(port)
    if orphans:
        _ok(f"发现 {len(orphans)} 个孤立 uvicorn worker，正在清理 ...")
        for opid in orphans:
            _kill_pid_tree(opid)
        if _wait_port_free(port, timeout=10.0, interval=0.5):
            _ok(f"端口 {port} 已释放")
            return True

    _err(f"端口 {port} 仍被占用（进程已结束，内核 socket 回收超时）")
    print(f"        可改用其他端口：python start.py --port {port+1}")
    return False


def check_port() -> bool:
    """检查端口是否被占用。
    - 空闲 → 返回 True
    - zombie socket（进程已死但内核 socket 残留）→ 轮询等待回收后返回 True
    - 被本项目的 python/uvicorn 占用 → 树式 kill 后轮询等待释放，返回 True
    - 被非本项目进程占用或清理失败 → 返回 False
    """
    _step("4/5", f"检查端口 {PORT} ...")
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        if sock.connect_ex(("127.0.0.1", PORT)) != 0:
            _ok(f"端口 {PORT} 空闲")
            return True

        # 端口被占用，尝试识别占用进程
        pid = _get_listening_pid(PORT)
        if pid is None:
            _err(f"端口 {PORT} 已被占用，无法识别进程 PID")
            print(f"        可尝试强制重启：python start.py --restart")
            print(f"        或改用其他端口：python start.py --port {PORT+1}")
            return False

        # 进程已死但 socket 残留（zombie），等待内核自动回收
        if not _is_process_alive(pid):
            _ok(f"PID {pid} 已退出，socket 残留中，等待内核回收 ...")
            if _wait_port_free(PORT, timeout=8.0, interval=0.5):
                _ok(f"端口 {PORT} 已释放")
                return True
            # zombie 未释放，可能是孤立的 uvicorn worker 持有 fd，扫描清理
            orphans = _find_orphan_uvicorn_pids(PORT)
            if orphans:
                _ok(f"发现 {len(orphans)} 个孤立 uvicorn worker，正在清理 ...")
                for opid in orphans:
                    _kill_pid_tree(opid)
                if _wait_port_free(PORT, timeout=10.0, interval=0.5):
                    _ok(f"端口 {PORT} 已释放")
                    return True
            _err(f"内核 socket 回收超时")
            print(f"        可改用其他端口：python start.py --port {PORT+1}")
            return False

        proc_name = _process_name(pid)
        is_known = any(n in proc_name.lower() for n in ("python", "uvicorn"))
        label = f"{proc_name} (PID {pid})" if proc_name else f"PID {pid}"

        if is_known:
            _err(f"端口 {PORT} 被旧的 {label} 占用，正在树式清理 ...")
            if _kill_pid_tree(pid):
                _ok(f"已结束进程树 PID {pid}")
                # 轮询等待端口真正释放（替代单次 sleep）
                if _wait_port_free(PORT, timeout=15.0, interval=0.5):
                    _ok(f"端口 {PORT} 已释放")
                    return True
                _err(f"进程已结束，但端口释放超时（可能 socket zombie）")
                print(f"        稍等几秒后重试，或改用其他端口：")
                print(f"            python start.py --port {PORT+1}")
                return False
            else:
                _err(f"无法结束进程 {pid}（可能需要管理员权限）")
                print(f"        可尝试强制重启：python start.py --restart")
                print(f"        或手动执行：taskkill /T /F /PID {pid}")
                print(f"        或改用其他端口：python start.py --port {PORT+1}")
                return False

        # 非 Python/uvicorn 进程占用，不给用户乱杀
        _err(f"端口 {PORT} 被非本项目进程占用：{label}")
        print(f"        可尝试强制重启（会结束所有占用该端口的进程）：")
        print(f"            python start.py --restart")
        print(f"        或改用其他端口：")
        print(f"            python start.py --port {PORT+1}")
        return False
    finally:
        sock.close()


# ------------------------------------------------------------------ #
#  步骤 5：启动服务 + 自动开浏览器
# ------------------------------------------------------------------ #
def open_browser_later(delay_seconds: float = 2.0) -> None:
    """延迟打开浏览器，给 uvicorn 启动留时间。"""
    def _open():
        time.sleep(delay_seconds)
        try:
            webbrowser.open(FRONTEND_URL)
            _ok(f"已打开浏览器：{FRONTEND_URL}")
        except Exception as e:
            _err(f"自动打开浏览器失败：{e}，请手动访问 {FRONTEND_URL}")

    t = threading.Thread(target=_open, daemon=True)
    t.start()


def start_server(python: str, reload: bool = False, open_browser: bool = True, port: int = PORT) -> None:
    """启动 uvicorn 服务（同时托管前端 + 后端 API）。"""
    _step("5/5", "启动服务 ...")
    print()
    print("=" * 64)
    print("  多智能体协同代码生成与审查系统 · NeuroForge")
    print("=" * 64)
    print()
    print("  🌐  前端控制台:  " + FRONTEND_URL.replace(f":{port}/", f":{port}/"))
    print("  🔌  后端 API:    " + API_URL.replace(f":{port}/", f":{port}/"))
    print("  📚  Swagger 文档:" + DOCS_URL.replace(f":{port}/", f":{port}/"))
    print("  ❤️  健康检查:    " + API_URL + "health")
    print()
    print("  按 Ctrl+C 停止服务")
    print("=" * 64)
    print()

    if open_browser:
        _ok(f"服务启动后 {2.0}s 将自动打开浏览器 ...")
        open_browser_later(delay_seconds=2.0)

    cmd = [python, "-m", "uvicorn", "app.main:app",
           "--host", HOST, "--port", str(port)]
    if reload:
        cmd.append("--reload")

    try:
        _run(cmd)
    except KeyboardInterrupt:
        print()
        _ok("服务已停止")


# ------------------------------------------------------------------ #
#  主入口
# ------------------------------------------------------------------ #
def main() -> None:
    global PORT, FRONTEND_URL, API_URL, DOCS_URL

    print()
    print("=" * 64)
    print("  多智能体协同代码生成与审查系统 · 一键启动")
    print("=" * 64)
    print()

    reload = "--reload" in sys.argv
    no_browser = "--no-browser" in sys.argv
    restart = "--restart" in sys.argv

    # 支持自定义端口：--port 8002
    custom_port = PORT
    if "--port" in sys.argv:
        try:
            idx = sys.argv.index("--port")
            custom_port = int(sys.argv[idx + 1])
        except (IndexError, ValueError):
            _err("--port 参数无效，使用默认端口 8001")

    PORT = custom_port
    FRONTEND_URL = f"http://localhost:{PORT}/"
    API_URL = f"http://localhost:{PORT}/api/v1/"
    DOCS_URL = f"http://localhost:{PORT}/docs"

    python = ensure_venv()
    ensure_env()
    ensure_rag(python)

    if restart:
        if not force_release_port(PORT):
            sys.exit(1)
    elif not check_port():
        sys.exit(1)

    start_server(python, reload=reload, open_browser=not no_browser, port=PORT)


if __name__ == "__main__":
    main()
