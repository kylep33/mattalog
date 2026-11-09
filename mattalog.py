#!/usr/bin/env python3
"""Copy files to named destinations: mattalog [--autoname] DEST PATH [PATH...]"""
import argparse
import copy
import curses
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
import xml.etree.ElementTree as ET

CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "mattalog", "config.json")
DEFAULTS = {
    "destinations": {
        "books": "kile@mattbot:/srv/data/books/ingest",  # calibre-web-automated imports from here
        "shows": "kile@mattbot:/srv/data/media/shows",
        "movies": "kile@mattbot:/srv/data/media/movies",
        "photos": "kile@mattbot:/srv/data/media/photos",
    },
    "servers": {"mattbot": ["mattbot", "192.168.1.88"]},  # addresses tried in order
}
HINTS = {
    "Permission denied": "host is up, but it rejected your SSH key (try ssh-copy-id)",
    "Connection refused": "host is up, but SSH isn't accepting connections",
    "timed out": "no answer: host is off, or you're not on its network",
    "No route": "no network path to the host: check your network",
    "Network is unreachable": "this machine has no usable network",
    "Could not resolve": "name doesn't resolve: check Tailscale or the spelling",
    "Host key verification": "host key is unknown or changed: connect once by hand with ssh",
}


def load():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    except (OSError, ValueError) as e:
        sys.exit(f"mattalog: can't read {CONFIG}: {e}")


def save(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG)


# --- autoname ---

def safe_filename(stem, ext):
    # Metadata is untrusted: it must never be able to add path components.
    stem = re.sub(r"\s+", " ", re.sub(r"[/\\\x00-\x1f]", " ", stem)).strip(" .-")
    return stem.encode()[:250 - len(ext.encode())].decode(errors="ignore").rstrip() + ext if stem else ""


def short_title(title):
    return re.split(r"\s*[:_]\s", title.strip(), maxsplit=1)[0]


def first_author(author):
    author = re.split(r"\s*[;&]\s*", author.strip())[0]
    parts = [p.strip() for p in author.split(",")]
    if len(parts) == 2 and " " not in parts[0]:  # "Herbert, Frank"
        return f"{parts[1]} {parts[0]}"
    return parts[0]


def epub_meta(path):
    try:
        with zipfile.ZipFile(path) as z:
            rootfile = next(e for e in ET.fromstring(z.read("META-INF/container.xml")).iter() if e.tag.endswith("rootfile"))
            found = {}
            for e in ET.fromstring(z.read(rootfile.attrib["full-path"])).iter():
                for field in ("title", "creator"):
                    if e.tag.endswith("}" + field) and e.text and e.text.strip():
                        found.setdefault(field, e.text)
            return found.get("title"), found.get("creator")
    except (OSError, KeyError, StopIteration, ET.ParseError, zipfile.BadZipFile):
        return None, None


def annas_meta(stem):
    # Anna's Archive: "Title -- Author -- Publisher, Year -- ISBN -- hash -- Anna's Archive"
    parts = stem.split(" -- ")
    return (parts[0], parts[1]) if len(parts) >= 3 else (None, None)


def autoname(path):
    """Return a cleaner filename, or the original name when nothing better is known."""
    base = os.path.basename(path)
    stem, ext = os.path.splitext(base)
    title, author = epub_meta(path) if ext.lower() == ".epub" else (None, None)
    if not title:
        title, author = annas_meta(stem)
    if not title:
        return base
    label = short_title(title) + (f" - {first_author(author)}" if author else "")
    return safe_filename(label, ext) or base


# --- transfer ---

def split_target(target):
    """'user@host:path' -> (user, host, path); a local path -> (None, None, path)."""
    m = re.fullmatch(r"(?:([^@/:]+)@)?([^/:]+):(.*)", target)
    if not m:
        return None, None, os.path.abspath(os.path.expanduser(target))
    # rsync and ssh both start in the home directory, and -s disables ~ expansion.
    path = re.sub(r"^~/?", "", m[3]).rstrip("/") if m[3] != "/" else "/"
    return m[1], m[2], path or "."


def ssh_args(addr):
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4", addr]


def connect(user, host, path, servers):
    """Return a working ssh login with the remote folder ensured, or exit explaining why not."""
    failures = []
    for addr in servers.get(host, [host]):
        login = f"{user}@{addr}" if user else addr
        r = subprocess.run(ssh_args(login) + ["mkdir", "-p", "--", shlex.quote(path)], capture_output=True, text=True)
        if r.returncode == 0:
            return login
        if r.returncode != 255:  # ssh itself worked; the remote mkdir didn't
            sys.exit(f"mattalog: can't create {path} on {host}: {r.stderr.strip()}")
        err = r.stderr.strip()
        failures.append((addr, next((h for k, h in HINTS.items() if k in err), err or "ssh failed")))
    print(f"mattalog: {host} unreachable", file=sys.stderr)
    for addr, why in failures:
        print(f"  {addr}: {why}", file=sys.stderr)
    if shutil.which("tailscale") and subprocess.run(["tailscale", "status"], capture_output=True).returncode != 0:
        print("  tailscale: down on this machine (try: sudo tailscale up)", file=sys.stderr)
    sys.exit(2)


def send(src, base, name):
    # Partials stay out of the real filename, so --ignore-existing can't mistake one for a finished file.
    target = f"{base}/{name}" if name else f"{base}/"
    return subprocess.run(["rsync", "-a", "-s", "--ignore-existing", "--partial-dir=.mattalog-partial",
                           "--info=progress2,skip1", "--", src, target]).returncode


def catalog(dest, paths, rename):
    cfg = load()
    destinations = cfg["destinations"]
    target = destinations.get(dest) or destinations.get(dest + "s")
    if not target:
        sys.exit(f"mattalog: unknown destination '{dest}' (have: {', '.join(destinations)})")
    # abspath drops tab-completed trailing slashes, which rsync reads as "contents only".
    sources = [os.path.abspath(p) for p in paths]
    missing = [p for p in sources if not os.path.exists(p)]
    if missing:
        sys.exit("mattalog: not found: " + ", ".join(missing))

    user, host, path = split_target(target)
    if host:
        login = connect(user, host, path, cfg.get("servers", {}))
        base = f"{login}:{path}"
    else:
        os.makedirs(path, exist_ok=True)
        base = path

    failed = 0
    for src in sources:
        name = None
        if os.path.isfile(src):
            name = autoname(src) if rename else os.path.basename(src)
        if name and name != os.path.basename(src):
            print(f"{os.path.basename(src)} -> {name}")
        if send(src, base, name) != 0:
            print(f"mattalog: failed: {src}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


# --- settings ---

def ask(win, label, value=""):
    """One-line editor on the bottom row. Returns None on Esc."""
    curses.curs_set(1)
    try:
        while True:
            h, w = win.getmaxyx()
            shown = f"{label}: {value}"[-(w - 1):]
            win.move(h - 1, 0)
            win.clrtoeol()
            win.addnstr(h - 1, 0, shown, w - 1, curses.A_BOLD)
            c = win.get_wch()
            if c in ("\n", "\r", curses.KEY_ENTER):
                return value.strip()
            if c == "\x1b":
                return None
            if c in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                value = value[:-1]
            elif isinstance(c, str) and c.isprintable():
                value += c
    finally:
        curses.curs_set(0)


def rows_for(cfg):
    rows = [("head", "Destinations", "")]
    rows += [("dest", k, v) for k, v in cfg["destinations"].items()]
    rows += [("new-dest", "+ new destination", ""), ("head", "", ""), ("head", "Servers (addresses tried in order)", "")]
    rows += [("server", k, " ".join(v)) for k, v in cfg["servers"].items()]
    rows += [("new-server", "+ new server", "")]
    return rows


def valid_name(name):
    return bool(re.fullmatch(r"\w[\w.-]*", name or ""))


def activate(win, cfg, kind, key, value):
    """Add or edit the row. Returns a status message."""
    section = "destinations" if kind in ("dest", "new-dest") else "servers"
    example = "user@host:/path, or a local path" if section == "destinations" else "addresses, space separated"
    if kind.startswith("new"):
        key = ask(win, "Name")
        if key is None:
            return ""
        if not valid_name(key):
            return "Names can use letters, numbers, . - _ only"
        value = ""
    new = ask(win, f"{key} ({example})", value)
    if not new:
        return "" if new is None else "Not saved: value was empty"
    cfg[section][key] = new if section == "destinations" else new.split()
    save(cfg)
    return f"Saved {key}"


def settings_ui(win):
    cfg = load()
    cfg.setdefault("destinations", {})
    cfg.setdefault("servers", {})
    curses.curs_set(0)
    curses.mousemask(curses.ALL_MOUSE_EVENTS)
    curses.mouseinterval(0)
    win.keypad(True)
    selected, status = 1, ""
    while True:
        rows = rows_for(cfg)
        pickable = [i for i, r in enumerate(rows) if r[0] != "head"]
        if selected not in pickable:
            selected = min(pickable, key=lambda i: abs(i - selected))
        win.erase()
        h, w = win.getmaxyx()
        win.addnstr(0, 0, "mattalog settings", w - 1, curses.A_BOLD)
        for i, (kind, key, value) in enumerate(rows[:max(h - 5, 0)]):
            attr = curses.A_REVERSE if i == selected else (curses.A_BOLD if kind == "head" else 0)
            win.addnstr(i + 2, 0, f"  {key:<14} {value}" if value else f"  {key}", w - 1, attr)
        win.addnstr(h - 2, 0, status or "click/Enter: edit   d: delete   q: quit", w - 1, curses.A_DIM)
        win.refresh()

        c = win.getch()
        status, go = "", False
        if c in (ord("q"), 27):
            return
        if c in (curses.KEY_UP, ord("k")):
            selected = max((i for i in pickable if i < selected), default=selected)
        elif c in (curses.KEY_DOWN, ord("j")):
            selected = min((i for i in pickable if i > selected), default=selected)
        elif c in (curses.KEY_ENTER, 10, 13):
            go = True
        elif c == curses.KEY_MOUSE:
            try:
                _, _, y, _, state = curses.getmouse()
            except curses.error:
                continue
            if state & (curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED) and y - 2 in pickable:
                selected, go = y - 2, True
        elif c == ord("d") and rows[selected][0] in ("dest", "server"):
            kind, key, _ = rows[selected]
            if ask(win, f"Delete {key}? (y/n)") in ("y", "Y"):
                del cfg["destinations" if kind == "dest" else "servers"][key]
                save(cfg)
                status = f"Deleted {key}"
        if go:
            status = activate(win, cfg, *rows[selected])


def main():
    parser = argparse.ArgumentParser(prog="mattalog", usage="mattalog [--autoname] DEST PATH [PATH ...]\n       mattalog --settings")
    parser.add_argument("--settings", action="store_true", help="edit destinations and servers")
    parser.add_argument("--autoname", action="store_true", help="rename files from EPUB metadata or Anna's Archive filenames")
    parser.add_argument("args", nargs="*", help=argparse.SUPPRESS)
    opts = parser.parse_intermixed_args()
    if opts.settings:
        os.environ.setdefault("ESCDELAY", "25")
        curses.wrapper(settings_ui)
        return 0
    if len(opts.args) < 2:
        parser.print_usage(sys.stderr)
        return 2
    return catalog(opts.args[0], opts.args[1:], opts.autoname)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
