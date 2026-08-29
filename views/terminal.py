"""
终端模块

提供一个「开始」门控 + WebSocket 的 PTY 终端。
终端基于 tmux：断开重连可恢复同一个持久会话，方便用 tmux 管理。
未配置自动登录用户时，tmux 会话中运行的是用户默认 shell，
可自行输入用户名 / 密码进行认证。
"""

import fcntl
import json
import os
import pty
import signal
import struct
import termios
import threading
import time

from flask import Blueprint
from flask import render_template

try:
    from flask_sock import Sock

    terminal_sock = Sock()
except ImportError:  # 未安装 flask-sock 时，仅禁用终端 WebSocket，不影响其他页面
    terminal_sock = None

terminal_bp = Blueprint("terminal", __name__)

# tmux 持久会话名称
TMUX_SESSION = "ply"


@terminal_bp.route("/terminal")
def index():
    return render_template("terminal.html")


def _resize_pty(fd, rows, cols):
    """设置伪终端（PTY）的窗口大小"""
    try:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
    except (ValueError, OSError):
        pass


def _spawn_terminal():
    """在一个全新 PTY 中启动 tmux 客户端，附加到持久会话。

    - 会话不存在时用 `tmux new-session -A` 自动创建（运行用户默认 shell）。
    - 客户端（WebSocket）断开后 tmux 会话仍会保留，重新进入时可恢复到
      上次的状态，实现「基于 tmux」的持久终端。
    - 环境中没有 tmux 时，回退为直接打开一个登录 shell。
    """
    pid, fd = pty.fork()
    if pid == 0:
        # 子进程：作为会话首进程，绑定控制终端
        os.environ["TERM"] = "xterm-256color"
        os.environ.setdefault("LANG", "en_US.UTF-8")
        shell = os.environ.get("SHELL", "/bin/bash")
        try:
            # -A：会话已存在则直接附加，否则创建并附加
            os.execlp("tmux", "tmux", "new-session", "-A", "-s", TMUX_SESSION)
        except FileNotFoundError:
            os.execvp(shell, [shell, "-l"])
    # 父进程：返回子进程 PID 与 master fd
    return pid, fd


def _terminate_child(pid):
    """结束 ptys 子进程（tmux 客户端），避免清理线程被 waitpid 卡住。

    先发 SIGHUP，短暂等待；若仍在运行则用 SIGKILL 强制结束。
    """
    try:
        os.killpg(pid, signal.SIGHUP)
    except (ProcessLookupError, OSError):
        return
    # 最多等待约 2 秒
    for _ in range(20):
        try:
            wpid, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if wpid == pid:
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
    try:
        os.waitpid(pid, 0)
    except OSError:
        pass


def _handle_terminal_ws(ws):
    """WebSocket 终端。

    - 后台线程：读取 PTY 输出，转发给浏览器。
    - 主循环：接收浏览器输入，写回 PTY；同时处理 resize 控制消息。
    """
    pid, fd = _spawn_terminal()
    _resize_pty(fd, 24, 80)

    stopped = threading.Event()

    def pump_output():
        """后台线程：读取 PTY 输出并发送给浏览器"""
        try:
            while not stopped.is_set():
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    break
                if not data:  # 子进程退出，流结束
                    break
                ws.send(data.decode("utf-8", errors="replace"))
        except Exception:
            pass
        finally:
            stopped.set()

    reader = threading.Thread(target=pump_output, daemon=True)
    reader.start()

    try:
        while not stopped.is_set():
            try:
                message = ws.receive(timeout=0.2)
            except Exception:
                break
            if message is None:
                continue

            # 处理前端发来的 resize 控制消息（JSON）
            if isinstance(message, str):
                stripped = message.strip()
                if stripped.startswith("{"):
                    try:
                        payload = json.loads(stripped)
                    except json.JSONDecodeError:
                        payload = None
                    if isinstance(payload, dict) and payload.get("type") == "resize":
                        rows = int(payload.get("rows", 24))
                        cols = int(payload.get("cols", 80))
                        _resize_pty(fd, rows, cols)
                        continue
                os.write(fd, message.encode("utf-8"))
            else:
                os.write(fd, message)
    except Exception:
        pass
    finally:
        stopped.set()
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        _terminate_child(pid)


if terminal_sock is not None:
    terminal_sock.route("/terminal/ws")(_handle_terminal_ws)
