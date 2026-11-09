import os
import sys
import time
import random
import threading
import ctypes
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

# ---------------------------------------------------------------------------
# WinDivert wrapper via pydivert
# ---------------------------------------------------------------------------
try:
    import pydivert
    HAS_PYDIVERT = True
except ImportError:
    HAS_PYDIVERT = False

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------
lock = threading.Lock()
running = False
current_scenario = None
scenario_params = {}
divert_thread = None
stop_event = threading.Event()
active_handle = None  # hold reference to WinDivert handle for forced close
stats = {"intercepted": 0, "dropped": 0, "delayed": 0, "sent": 0, "phase": ""}
phase_info = ""


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def safe_recv(w):
    """Receive a packet, return None on error (e.g. handle closed)."""
    try:
        return w.recv()
    except Exception:
        return None


def safe_send(w, pkt):
    try:
        w.send(pkt)
        return True
    except Exception:
        return False


def flush_lag_queue(w, lag_queue):
    """Send all queued packets that are overdue, return updated queue."""
    now = time.time()
    sent = 0
    remaining = []
    for send_time, pkt in lag_queue:
        if now >= send_time:
            if safe_send(w, pkt):
                sent += 1
        else:
            remaining.append((send_time, pkt))
    return remaining, sent


# ---------------------------------------------------------------------------
# Worker: weak network
# ---------------------------------------------------------------------------
def weak_network_worker(params):
    global stats, phase_info, active_handle
    filter_expr = params.get("filter", "true")
    lag_ms = int(params.get("lag_time", 300))
    drop_pct = float(params.get("drop_chance", 10.0))
    bw_limit = int(params.get("bandwidth_limit", 0))

    stats = {"intercepted": 0, "dropped": 0, "delayed": 0, "sent": 0, "phase": ""}
    phase_info = f"弱网模拟: 延迟{lag_ms}ms / 丢包{drop_pct}%"
    if bw_limit > 0:
        phase_info += f" / 带宽{bw_limit}KB/s"

    lag_queue = []
    bytes_this_second = 0
    last_second = time.time()

    try:
        w = pydivert.WinDivert(filter_expr)
        w.open()
        active_handle = w

        while not stop_event.is_set():
            lag_queue, sent_count = flush_lag_queue(w, lag_queue)
            stats["sent"] += sent_count

            now = time.time()
            if now - last_second >= 1.0:
                bytes_this_second = 0
                last_second = now

            pkt = safe_recv(w)
            if pkt is None:
                if stop_event.is_set():
                    break
                time.sleep(0.001)
                continue

            stats["intercepted"] += 1

            if random.random() * 100 < drop_pct:
                stats["dropped"] += 1
                continue

            if bw_limit > 0:
                if bytes_this_second + len(pkt.raw) > bw_limit * 1024:
                    stats["dropped"] += 1
                    continue
                bytes_this_second += len(pkt.raw)

            if lag_ms > 0:
                jitter = random.uniform(0.8, 1.2)
                delay = (lag_ms * jitter) / 1000.0
                lag_queue.append((now + delay, pkt))
                stats["delayed"] += 1
            else:
                if safe_send(w, pkt):
                    stats["sent"] += 1

        # flush remaining
        for _, pkt in lag_queue:
            safe_send(w, pkt)
        w.close()
    except Exception as e:
        phase_info = f"错误: {e}"
    finally:
        active_handle = None


# ---------------------------------------------------------------------------
# Worker: network switch
# ---------------------------------------------------------------------------
def network_switch_worker(params):
    global stats, phase_info, active_handle
    filter_expr = params.get("filter", "true")
    disconnect_sec = int(params.get("disconnect_duration", 3))
    reconnect_lag = int(params.get("reconnect_lag", 200))
    reconnect_drop = float(params.get("reconnect_drop", 5.0))

    stats = {"intercepted": 0, "dropped": 0, "delayed": 0, "sent": 0, "phase": ""}

    # Phase 1: full drop
    phase_info = f"网络切换 - 断开中 ({disconnect_sec}秒)"
    stats["phase"] = "disconnect"
    start = time.time()

    try:
        w = pydivert.WinDivert(filter_expr)
        w.open()
        active_handle = w

        while not stop_event.is_set():
            elapsed = time.time() - start
            if elapsed >= disconnect_sec:
                break
            remaining = disconnect_sec - elapsed
            phase_info = f"网络切换 - 断开中 (剩余 {remaining:.0f}秒)"

            pkt = safe_recv(w)
            if pkt is None:
                if stop_event.is_set():
                    break
                time.sleep(0.001)
                continue
            stats["intercepted"] += 1
            stats["dropped"] += 1
            # drop: don't send

        w.close()
    except Exception as e:
        phase_info = f"错误: {e}"
        active_handle = None
        return

    if stop_event.is_set():
        active_handle = None
        return

    # Phase 2: reconnect with lag + drop
    phase_info = f"网络切换 - 已重连 (延迟{reconnect_lag}ms, 丢包{reconnect_drop}%)"
    stats["phase"] = "reconnected"
    lag_queue = []

    try:
        w = pydivert.WinDivert(filter_expr)
        w.open()
        active_handle = w

        while not stop_event.is_set():
            lag_queue, sent_count = flush_lag_queue(w, lag_queue)
            stats["sent"] += sent_count

            pkt = safe_recv(w)
            if pkt is None:
                if stop_event.is_set():
                    break
                time.sleep(0.001)
                continue

            stats["intercepted"] += 1

            if random.random() * 100 < reconnect_drop:
                stats["dropped"] += 1
                continue

            if reconnect_lag > 0:
                jitter = random.uniform(0.8, 1.2)
                delay = (reconnect_lag * jitter) / 1000.0
                lag_queue.append((time.time() + delay, pkt))
                stats["delayed"] += 1
            else:
                if safe_send(w, pkt):
                    stats["sent"] += 1

        for _, pkt in lag_queue:
            safe_send(w, pkt)
        w.close()
    except Exception as e:
        phase_info = f"错误: {e}"
    finally:
        active_handle = None


# ---------------------------------------------------------------------------
# Worker: disconnect & reconnect
# ---------------------------------------------------------------------------
def disconnect_reconnect_worker(params):
    global stats, phase_info, running, current_scenario, active_handle
    filter_expr = params.get("filter", "true")
    disconnect_sec = int(params.get("disconnect_duration", 5))

    stats = {"intercepted": 0, "dropped": 0, "delayed": 0, "sent": 0, "phase": ""}
    stats["phase"] = "disconnected"
    start = time.time()

    try:
        w = pydivert.WinDivert(filter_expr)
        w.open()
        active_handle = w

        while not stop_event.is_set():
            elapsed = time.time() - start
            remaining = disconnect_sec - elapsed
            if remaining <= 0:
                break
            phase_info = f"断网中 (剩余 {remaining:.0f}秒)"

            pkt = safe_recv(w)
            if pkt is None:
                if stop_event.is_set():
                    break
                time.sleep(0.001)
                continue
            stats["intercepted"] += 1
            stats["dropped"] += 1

        w.close()
    except Exception as e:
        phase_info = f"错误: {e}"
    finally:
        active_handle = None
        with lock:
            running = False
            current_scenario = None
        if not stop_event.is_set():
            phase_info = "断网结束 - 网络已恢复"
            stats["phase"] = "restored"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/check")
def api_check():
    return jsonify({
        "pydivert": HAS_PYDIVERT,
        "admin": is_admin(),
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    global running, current_scenario, scenario_params, divert_thread

    if not HAS_PYDIVERT:
        return jsonify({"success": False, "message": "pydivert 未安装，请运行: pip install pydivert"})

    if not is_admin():
        return jsonify({"success": False, "message": "需要管理员权限运行！请右键以管理员身份运行命令行"})

    data = request.json
    scenario = data.get("scenario")
    params = data.get("params", {})

    with lock:
        if running:
            return jsonify({"success": False, "message": "已有场景运行中，请先停止"})
        stop_event.clear()
        running = True
        current_scenario = scenario
        scenario_params = params

    workers = {
        "weak": weak_network_worker,
        "switch": network_switch_worker,
        "disconnect": disconnect_reconnect_worker,
    }

    worker = workers.get(scenario)
    if not worker:
        with lock:
            running = False
            current_scenario = None
        return jsonify({"success": False, "message": "未知场景"})

    divert_thread = threading.Thread(target=worker, args=(params,), daemon=True)
    divert_thread.start()

    names = {"weak": "弱网模拟", "switch": "网络切换", "disconnect": "断网重连"}
    return jsonify({"success": True, "message": f"{names.get(scenario, scenario)} 已启动"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    global running, current_scenario, phase_info, active_handle

    with lock:
        if not running:
            return jsonify({"success": True, "message": "当前无运行中的场景"})
        stop_event.set()

    # force close the WinDivert handle to unblock recv()
    h = active_handle
    if h:
        try:
            h.close()
        except Exception:
            pass

    if divert_thread:
        divert_thread.join(timeout=5)

    with lock:
        running = False
        current_scenario = None
    phase_info = "已停止"

    return jsonify({"success": True, "message": "已停止"})


@app.route("/api/status")
def api_status():
    return jsonify({
        "running": running,
        "scenario": current_scenario,
        "stats": stats,
        "phase": phase_info,
    })


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if not is_admin():
        print("=" * 60)
        print("  WARNING: 未以管理员身份运行!")
        print("  WinDivert 需要管理员权限才能拦截数据包")
        print("  请右键命令行 -> 以管理员身份运行")
        print("=" * 60)

    if not HAS_PYDIVERT:
        print("=" * 60)
        print("  WARNING: pydivert 未安装!")
        print("  请运行: pip install pydivert")
        print("=" * 60)

    print()
    print("  弱网模拟工具启动中...")
    print("  打开浏览器访问: http://127.0.0.1:5000")
    print()
    app.run(host="127.0.0.1", port=5000, debug=False)
