"""
终端模块
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
from flask_login import current_user

from flask_sock import Sock

# 全局 Sock 对象，专门给终端 WebSocket 使用
terminal_sock = Sock()

terminal_bp = Blueprint("terminal", __name__)

# tmux 持久会话名称：所有终端连接都附加到同一个名为 "ply" 的 tmux 会话
TMUX_SESSION = "ply"


@terminal_bp.route("/terminal")
def index():
    return render_template("terminal.html")


def _resize_pty(fd, rows, cols):
    """设置伪终端（PTY）的窗口大小。

    终端程序（如 vim、top）会根据这里的 rows/cols 决定如何排版。
    浏览器端窗口改变时，会把新的行列数通过 WebSocket 发过来，再调用本函数更新。
    """
    try:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
    except (ValueError, OSError):
        # 窗口尺寸非法或 ioctl 失败时静默忽略，不影响主流程
        pass


def _spawn_terminal():
    """在一个全新 PTY 中启动 tmux 客户端，附加到持久会话。

    会话不存在时用 `tmux new-session -A` 自动创建（运行用户默认 shell）。
    客户端（WebSocket）断开后 tmux 会话仍会保留，重新进入时可恢复到上次的状态，实现「基于 tmux」的持久终端。
    环境中没有 tmux 时，回退为直接打开一个登录 shell。
    """
    pid, fd = pty.fork()
    # pty.fork() 会复制当前进程：
    #   - 子进程（pid == 0）：控制终端已和伪终端从设备相连，准备运行命令
    #   - 父进程（pid > 0）：持有主设备的文件描述符 fd，用于读写终端数据
    if pid == 0:
        # ===== 子进程：执行 tmux 或 shell =====
        # 设置基本参数
        os.environ["TERM"] = "xterm-256color"
        os.environ.setdefault("LANG", "en_US.UTF-8")
        shell = os.environ.get("SHELL", "/bin/bash")
        try:
            # os.execlp 会用 tmux 替换当前子进程镜像
            os.execlp("tmux", "tmux", "new-session", "-A", "-s", TMUX_SESSION)
        except FileNotFoundError:
            # 系统里没有安装 tmux：回退为直接打开一个登录 shell
            # "-l" 表示登录 shell（会加载 .profile / .bash_profile 等登录环境）
            os.execvp(shell, [shell, "-l"])
    # ===== 父进程：返回子进程 PID 和 master fd =====
    # fd 用来读写伪终端；pid 用来在结束时清理 tmux 子进程
    return pid, fd


def _terminate_child(pid):
    """结束 ptys 子进程（tmux 客户端），避免清理线程被 waitpid 卡住。

    先发 SIGHUP，短暂等待；若仍在运行则用 SIGKILL 强制结束。
    """
    try:
        # 向整个进程组发送 SIGHUP（挂断信号），请求 tmux 子进程正常退出
        os.killpg(pid, signal.SIGHUP)
    except (ProcessLookupError, OSError):
        # 进程组已经不存在，直接返回
        return
    # 等待最多约 2 秒（20 次 × 0.1 秒），给 tmux 时间自己退出
    for _ in range(20):
        try:
            # WNOHANG：非阻塞地检查子进程是否已退出；没退出时返回 (0, 0)
            wpid, _ = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if wpid == pid:
            # 已正常退出，无需再处理
            return
        time.sleep(0.1)
    try:
        # 2 秒后仍未退出：用 SIGKILL 强制结束
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass
    try:
        # 强制结束后再回收一次子进程，避免产生僵尸进程
        os.waitpid(pid, 0)
    except OSError:
        pass


def _handle_terminal_ws(ws):
    """WebSocket 终端。

    后台线程：读取 PTY 输出，转发给浏览器。
    主循环：接收浏览器输入，写回 PTY；同时处理 resize 控制消息。
    """
    # 防御性校验：即使 before_request 被绕过，未登录用户也不给建立终端连接
    if not current_user.is_authenticated:
        try:
            ws.close(message="未登录")
        except Exception:
            pass
        return

    pid, fd = _spawn_terminal()
    # 先给 PTY 设置一个默认的 24 行 × 80 列
    _resize_pty(fd, 24, 80)

    # 线程停止标记：告诉“输出泵”线程何时该退出
    stopped = threading.Event()

    def pump_output():
        """后台线程：读取 PTY 输出并发送给浏览器。

        因为 os.read 是阻塞调用，如果放在主循环里，会收不到浏览器输入，
        所以单独用一个线程持续读终端输出，通过 WebSocket 实时推给前端。
        """
        try:
            while not stopped.is_set():
                try:
                    # 从伪终端主设备读最多 4096 字节（终端里有输出时才会返回）
                    data = os.read(fd, 4096)
                except OSError:
                    break  # 读到错误（如 fd 已关闭），结束线程
                if not data:  # 返回空字节说明子进程退出、流结束
                    break
                # 把字节解码成文本发送给浏览器；遇到无法解码的字节用替换符替代
                ws.send(data.decode("utf-8", errors="replace"))
        except Exception:
            pass
        finally:
            # 无论正常结束还是异常，都置位“停止标记”
            stopped.set()

    # 创建并启动读取线程（daemon=True：主线程结束时它会自动被回收）
    reader = threading.Thread(target=pump_output, daemon=True)
    reader.start()

    try:
        # 主循环：不断接收浏览器发来的输入并写回 PTY
        while not stopped.is_set():
            try:
                # timeout=0.2：每隔 0.2 秒检查一次“停止标记”，
                # 这样崩溃/断开时能及时退出循环
                message = ws.receive(timeout=0.2)
            except Exception:
                break  # 连接已关闭或出错
            if message is None:
                continue  # 超时收到 None，继续循环

            # 判断是否是前端发来的 resize 控制消息
            if isinstance(message, str):
                stripped = message.strip()
                if stripped.startswith("{"):
                    # 以 { 开头的字符串可能是 JSON 控制消息，尝试解析
                    try:
                        payload = json.loads(stripped)
                    except json.JSONDecodeError:
                        payload = None
                    # 只有类型为 resize 时才当作控制消息处理
                    if isinstance(payload, dict) and payload.get("type") == "resize":
                        rows = int(payload.get("rows", 24))
                        cols = int(payload.get("cols", 80))
                        _resize_pty(fd, rows, cols)
                        continue  # 消费掉这条控制消息，不写入终端
                # 普通文本：编码为 UTF-8 后写入 PTY，等价于用户敲键盘
                os.write(fd, message.encode("utf-8"))
            else:
                # 二进制消息：直接写入 PTY
                os.write(fd, message)
    except Exception:
        pass
    finally:
        # 清理阶段：停止读取线程、关闭 fd、结束 tmux 子进程
        stopped.set()
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        # 结束 tmux 子进程（tmux 服务本身保留，会话可重新连回）
        _terminate_child(pid)


if terminal_sock is not None:
    terminal_sock.route("/terminal/ws")(_handle_terminal_ws)
