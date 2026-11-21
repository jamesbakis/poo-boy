"""LAN networking for Poo Boy.

The host runs the real game. Clients send their key presses to the host, and the
host sends the whole game state back to every client each frame. Messages are
newline-separated JSON over TCP. Hosts announce themselves with a UDP broadcast
once a second so clients on the same network can find them.
"""

import json
import os
import socket
import threading
import time

TCP_PORT = 50555
DISCOVERY_PORT = 50556
GAME_ID = "poo-boy-lan-1"


def encode(obj):
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode()


def read_lines(sock, on_msg):
    """Read newline-separated JSON messages until the socket closes."""
    buf = b""
    while True:
        try:
            data = sock.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            return
        if not data:
            return
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                on_msg(msg)


def local_ip():
    """This machine's address on the local network (no packets are sent)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class Host:
    """Accepts players, collects their inputs and broadcasts game state."""

    def __init__(self, max_players):
        self.max_players = max_players
        self.lock = threading.Lock()
        self.clients = {}  # pid -> socket (None while the welcome message is being sent)
        self.inputs = {}   # pid -> latest input message
        self.joined, self.left = [], []
        self.accepting = True
        self.running = True
        self.name = socket.gethostname()
        self.ip = local_ip()

        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":  # on Windows this would let two hosts share the port
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("", TCP_PORT))
        self.server.listen()

        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.last_beacon = 0.0

        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self):
        while self.running:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.settimeout(1.0)
            with self.lock:
                free = [pid for pid in range(1, self.max_players) if pid not in self.clients]
                pid = free[0] if (self.accepting and free) else None
                if pid is not None:
                    self.clients[pid] = None  # reserve the slot
            if pid is None:
                try:
                    conn.sendall(encode({"t": "full", "why": "That game is full or has already started."}))
                except OSError:
                    pass
                conn.close()
                continue
            try:
                conn.sendall(encode({"t": "hi", "pid": pid}))
            except OSError:
                with self.lock:
                    self.clients.pop(pid, None)
                conn.close()
                continue
            with self.lock:
                self.clients[pid] = conn
                self.joined.append(pid)
            threading.Thread(target=self._reader, args=(pid, conn), daemon=True).start()

    def _reader(self, pid, conn):
        def on_msg(msg):
            if msg.get("t") == "in":
                with self.lock:
                    self.inputs[pid] = msg
        read_lines(conn, on_msg)
        self._drop(pid)

    def _drop(self, pid):
        with self.lock:
            sock = self.clients.pop(pid, None)
            self.inputs.pop(pid, None)
            if sock is not None:
                self.left.append(pid)
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def poll(self):
        """Returns (pids that joined, pids that left, latest input per pid)."""
        with self.lock:
            joined, left = self.joined, self.left
            self.joined, self.left = [], []
            return joined, left, dict(self.inputs)

    def broadcast(self, obj):
        data = encode(obj)
        with self.lock:
            socks = [(pid, s) for pid, s in self.clients.items() if s is not None]
        for pid, s in socks:
            try:
                s.sendall(data)
            except OSError:
                self._drop(pid)

    def beacon(self, players):
        """Announce this game on the local network, at most once a second."""
        now = time.time()
        if now - self.last_beacon < 1.0:
            return
        self.last_beacon = now
        data = json.dumps({"game": GAME_ID, "name": self.name, "port": TCP_PORT, "players": players,
                           "max": self.max_players, "open": self.accepting}).encode()
        targets = {"255.255.255.255", "127.0.0.1"}
        if self.ip.count(".") == 3 and not self.ip.startswith("127."):
            targets.add(self.ip.rsplit(".", 1)[0] + ".255")  # common /24 subnet broadcast
        for addr in targets:
            try:
                self.udp.sendto(data, (addr, DISCOVERY_PORT))
            except OSError:
                pass

    def close(self):
        self.running = False
        for s in [self.server, self.udp] + [s for s in self.clients.values() if s is not None]:
            try:
                s.close()
            except OSError:
                pass


class Client:
    """Connection from a joining player to the host."""

    def __init__(self, ip, port=TCP_PORT):
        self.sock = socket.create_connection((ip, port), timeout=3)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(1.0)
        self.lock = threading.Lock()
        self.pid = None
        self.snapshot = None
        self.events = []
        self.error = None
        self.connected = True
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        read_lines(self.sock, self._on_msg)
        self.connected = False

    def _on_msg(self, msg):
        t = msg.get("t")
        if t == "hi":
            self.pid = msg.get("pid")
        elif t == "full":
            self.error = msg.get("why", "Couldn't join that game.")
        elif t == "s":
            with self.lock:
                self.snapshot = msg  # only the newest state matters...
                self.events.extend(msg.get("ev", []))  # ...but don't lose any events

    def take(self):
        with self.lock:
            snap, events = self.snapshot, self.events
            self.snapshot, self.events = None, []
            return snap, events

    def send(self, obj):
        try:
            self.sock.sendall(encode(obj))
        except OSError:
            self.connected = False

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Discovery:
    """Listens for host announcements."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", DISCOVERY_PORT))
        self.sock.setblocking(False)
        self.found = {}

    def hosts(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(2048)
            except OSError:  # nothing waiting (or a Windows ICMP reset)
                break
            try:
                info = json.loads(data)
            except ValueError:
                continue
            if not isinstance(info, dict) or info.get("game") != GAME_ID:
                continue
            key = (info.get("name"), info.get("port"))
            old = self.found.get(key)
            info["ip"] = addr[0]
            # a host on this same machine is heard on both loopback and the LAN; prefer the LAN address
            if old and info["ip"].startswith("127.") and not old["ip"].startswith("127."):
                info["ip"] = old["ip"]
            info["seen"] = time.time()
            self.found[key] = info
        now = time.time()
        self.found = {k: v for k, v in self.found.items() if now - v["seen"] < 3.5}
        return sorted(self.found.values(), key=lambda h: str(h.get("name")))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
