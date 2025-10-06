#!/usr/bin/env python3
# Linux Terminal v3.7 – Virtual Linux-like shell for Windows (LinuxFS root)
# - Auto-setup: pip deps + auto-download Portable Git Bash if missing
# - Auto-detect Python + python3 shell-shim (bash script) for Git Bash
# - .sh runner (Git Bash), .py runner (native Python), .bat/.cmd runner
# - apt/dpkg SIMULATION (scripts/resources only), metadata & ownership simulation
# - Migrates automatically from old KRNL_System layout to LinuxFS

import os
import sys
import shlex
import stat
import json
import shutil
import subprocess
import tarfile
import lzma
import gzip
import urllib.request
import zipfile
from pathlib import Path
from datetime import datetime
from io import BytesIO

# ===================== CONFIG =====================
# Portable Git (self-extracting 7z) – update URL if needed:
PORTABLE_GIT_URL = (
    "https://github.com/git-for-windows/git/releases/download/v2.45.2.windows.1/"
    "PortableGit-2.45.2-64-bit.7z.exe"
)
AUTO_DOWNLOAD_TOOLS = True  # zet op False om auto-download te voorkomen
REQUIRED_PIP_PACKAGES = ["colorama"] + (["pyreadline3"] if os.name == "nt" else [])
# ==================================================

# ---------- color ----------
try:
    import colorama
    colorama.just_fix_windows_console()
    USE_COLOR = sys.stdout.isatty()
except Exception:
    USE_COLOR = sys.stdout.isatty() and os.name != "nt"

def c(s: str) -> str:
    return s if USE_COLOR else ""

C_RESET  = "\033[0m"
C_GREEN  = "\033[92m"
C_BLUE   = "\033[94m"
C_CYAN   = "\033[96m"
C_YELLOW = "\033[93m"
C_RED    = "\033[91m"

# ---------- paths & state ----------
SCRIPT_DIR   = Path(__file__).resolve().parent

# New Linux naming
SYSTEM_DIR_NAME = "LinuxFS"
SYSTEM_ROOT  = SCRIPT_DIR / SYSTEM_DIR_NAME
INIT_MARKER  = SYSTEM_ROOT / ".linux_initialized"
META_FILE    = SYSTEM_ROOT / ".linux_meta.json"
PKG_DB_FILE  = SYSTEM_ROOT / ".linux_packages.json"
APT_REGISTRY = SYSTEM_ROOT / "etc" / "linux_apt_registry.json"

TOOLS_DIR    = SYSTEM_ROOT / "tools"
GIT_HOME     = TOOLS_DIR / "git"     # waar PortableGit komt te staan
CACHE_DIR    = SYSTEM_ROOT / "var" / "cache" / "downloads"

# Branding
BRAND    = "Linux Terminal"
VERSION  = "v3.7"
HOSTNAME = "linux"

USER    = os.getenv("USER") or os.getenv("USERNAME") or "user"
IS_ROOT = False

# ---------- JSON helpers ----------
def json_load(path: Path, default):
    try:
        if path.exists():
            return json.load(open(path, "r", encoding="utf-8"))
    except Exception:
        pass
    return default

def json_save(path: Path, data):
    try:
        json.dump(data, open(path, "w", encoding="utf-8"), indent=2)
    except Exception:
        pass

META   = json_load(META_FILE, {})
PKG_DB = json_load(PKG_DB_FILE, {"installed": {}})

def meta_key(p: Path) -> str:
    try:
        return str(p.relative_to(SYSTEM_ROOT).as_posix())
    except Exception:
        return ""

def meta_get(p: Path) -> dict:
    return META.get(meta_key(p), {})

def meta_set(p: Path, **kwargs):
    k = meta_key(p)
    if not k: return
    rec = META.get(k, {})
    rec.update(kwargs)
    META[k] = rec
    json_save(META_FILE, META)

def pkg_db_save():
    json_save(PKG_DB_FILE, PKG_DB)

# ---------- migration from KRNL_System ----------
def migrate_from_krnl_if_needed():
    old_root = SCRIPT_DIR / "KRNL_System"
    if not old_root.exists() or SYSTEM_ROOT.exists():
        return
    try:
        shutil.move(str(old_root), str(SYSTEM_ROOT))
    except Exception:
        shutil.copytree(old_root, SYSTEM_ROOT, dirs_exist_ok=True)
        try:
            shutil.rmtree(old_root, ignore_errors=True)
        except Exception:
            pass

    # rename markers/metadata
    krnl_init = SYSTEM_ROOT / ".krnl_initialized"
    if krnl_init.exists():
        try: krnl_init.rename(INIT_MARKER)
        except Exception: pass

    krnl_meta = SYSTEM_ROOT / ".krnl_meta.json"
    if krnl_meta.exists() and not META_FILE.exists():
        try: krnl_meta.rename(META_FILE)
        except Exception: pass

    krnl_pkgs = SYSTEM_ROOT / ".krnl_packages.json"
    if krnl_pkgs.exists() and not PKG_DB_FILE.exists():
        try: krnl_pkgs.rename(PKG_DB_FILE)
        except Exception: pass

    krnl_reg = SYSTEM_ROOT / "etc" / "krnl_apt_registry.json"
    if krnl_reg.exists() and not APT_REGISTRY.exists():
        try:
            krnl_reg.rename(APT_REGISTRY)
        except Exception:
            (SYSTEM_ROOT / "etc").mkdir(parents=True, exist_ok=True)
            shutil.move(str(krnl_reg), str(APT_REGISTRY))

# ---------- init ----------
def init_system():
    for d in [
        f"home/{USER}", "root",
        "usr/bin", "usr/lib", "usr/share",
        "etc", "var/log", "var/tmp", "var/cache/downloads",
        "bin", "tmp", "tools"
    ]:
        (SYSTEM_ROOT / d).mkdir(parents=True, exist_ok=True)

    motd = SYSTEM_ROOT / "etc" / "motd.txt"
    if not motd.exists():
        motd.write_text(
            f"Welcome to {BRAND} {VERSION}!\nType 'help' for commands.\n",
            encoding="utf-8"
        )

    if not APT_REGISTRY.exists():
        json_save(APT_REGISTRY, {"packages": {}})

    if not INIT_MARKER.exists():
        INIT_MARKER.write_text("initialized\n")
    if not META_FILE.exists():
        json_save(META_FILE, {})
    if not PKG_DB_FILE.exists():
        json_save(PKG_DB_FILE, {"installed": {}})

# ---------- dependency bootstrap ----------
def pip_install(package: str):
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", package])
        return True
    except Exception:
        return False

def ensure_pip_deps():
    missing = []
    for pkg in REQUIRED_PIP_PACKAGES:
        try:
            __import__(pkg.split("==")[0].split(">=")[0])
        except Exception:
            missing.append(pkg)
    if not missing:
        return
    print(f"{c(C_YELLOW)}Installing Python packages: {', '.join(missing)} ...{c(C_RESET)}")
    ok_all = True
    for pkg in missing:
        ok = pip_install(pkg)
        ok_all = ok_all and ok
    if not ok_all:
        print(f"{c(C_RED)}Warning:{c(C_RESET)} Some Python packages failed to install. Continue anyway...")

def http_download_to(path: Path, url: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as resp, open(path, "wb") as f:
        shutil.copyfileobj(resp, f)

def find_portable_git_bash_in(git_root: Path) -> str | None:
    candidates = [
        git_root / "usr" / "bin" / "bash.exe",
        git_root / "bin" / "bash.exe",
    ]
    for cnd in candidates:
        if cnd.exists():
            return str(cnd)
    return None

def ensure_git_bash() -> str | None:
    # 1) Already present in tools?
    bash = find_portable_git_bash_in(GIT_HOME)
    if bash:
        return bash

    # 2) System Git?
    sys_bash = shutil.which("bash")
    if sys_bash:
        return sys_bash

    if not AUTO_DOWNLOAD_TOOLS:
        print(f"{c(C_YELLOW)}Git Bash not found. AUTO_DOWNLOAD_TOOLS is disabled.{c(C_RESET)}")
        return None

    # 3) Download PortableGit and extract (self-extracting .7z.exe)
    try:
        print(f"{c(C_YELLOW)}Downloading Portable Git Bash...{c(C_RESET)}")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        sfx_path = CACHE_DIR / "PortableGit.7z.exe"
        if not sfx_path.exists():
            http_download_to(sfx_path, PORTABLE_GIT_URL)
        # Extract to GIT_HOME using SFX flags: -y (assume yes) -o (output dir)
        GIT_HOME.mkdir(parents=True, exist_ok=True)
        # Use quotes in case of spaces
        proc = subprocess.run([str(sfx_path), "-y", f"-o{str(GIT_HOME)}"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            print(f"{c(C_RED)}PortableGit extraction failed{c(C_RESET)}\n{proc.stderr}")
            return None
        bash = find_portable_git_bash_in(GIT_HOME)
        if bash:
            print(f"{c(C_GREEN)}Portable Git Bash installed to {GIT_HOME}{c(C_RESET)}")
            return bash
    except Exception as e:
        print(f"{c(C_RED)}Failed to auto-install Git Bash:{c(C_RESET)} {e}")
        return None
    return None

# ---------- path helpers ----------
def resolve_path(cwd: Path, arg: str) -> Path:
    if not arg: return cwd
    if arg == "/": return SYSTEM_ROOT
    if arg.startswith("/"):
        return (SYSTEM_ROOT / arg.lstrip("/")).resolve()
    if arg.startswith("~"):
        tail = arg[2:] if arg.startswith("~/") else ""
        return (SYSTEM_ROOT / "home" / USER / tail).resolve()
    return (cwd / arg).resolve()

def prompt(cwd: Path) -> str:
    symbol = "#" if IS_ROOT else "$"
    rel = cwd.relative_to(SYSTEM_ROOT)
    disp = "/" if not rel.parts else "/" + "/".join(rel.parts)
    return f"{c(C_GREEN)}{USER}@{HOSTNAME}{c(C_RESET)}:{c(C_BLUE)}{disp}{c(C_RESET)}{symbol} "

# ---------- ls ----------
def mode_to_str(mode: int) -> str:
    is_dir = "d" if stat.S_ISDIR(mode) else "-"
    bits = [
        (stat.S_IRUSR, "r"), (stat.S_IWUSR, "w"), (stat.S_IXUSR, "x"),
        (stat.S_IRGRP, "r"), (stat.S_IWGRP, "w"), (stat.S_IXGRP, "x"),
        (stat.S_IROTH, "r"), (stat.S_IWOTH, "w"), (stat.S_IXOTH, "x"),
    ]
    return is_dir + "".join(ch if (mode & b) else "-" for b, ch in bits)

def human_size(n: int) -> str:
    size = float(n)
    for unit in ["B", "K", "M", "G", "T", "P"]:
        if size < 1024.0:
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.0f}{unit}"
        size /= 1024.0
    return f"{int(size)}E"

def print_ls_entry(p: Path, long=False, human=False):
    name = p.name + ("/" if p.is_dir() else "")
    if long:
        try:
            st = p.stat()
            perms = mode_to_str(st.st_mode)
            nlink = 1
            meta = meta_get(p)
            owner = meta.get("owner", USER)
            group = meta.get("group", USER)
            size = st.st_size
            size_str = f"{human_size(size):>6}" if human else f"{size:>6}"
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%b %d %H:%M")
            line = f"{perms} {nlink:>2} {owner:>8} {group:>8} {size_str} {mtime} {name}"
        except Exception:
            line = name
        if p.is_dir() and USE_COLOR:
            print(f"{c(C_BLUE)}{line}{c(C_RESET)}")
        else:
            print(line)
    else:
        if p.is_dir() and USE_COLOR:
            print(f"{c(C_BLUE)}{name}{c(C_RESET)}", end="  ")
        else:
            print(name, end="  ")

def cmd_ls(cwd: Path, args: list):
    show_all = False; long = False; human = False; paths = []
    for a in args:
        if a.startswith("-"):
            if "a" in a: show_all = True
            if "l" in a: long = True
            if "h" in a: human = True
        else:
            paths.append(a)
    targets = [cwd] if not paths else [resolve_path(cwd, p) for p in paths]
    multi = len(targets) > 1
    for t in targets:
        if not t or not t.exists():
            print(f"ls: cannot access '{t}': No such file or directory"); continue
        if t.is_file():
            print_ls_entry(t, long=long, human=human)
            if not long: print()
            continue
        if multi:
            rel = t.relative_to(SYSTEM_ROOT) if t != SYSTEM_ROOT else Path("/")
            print(f"{rel}:")
        try:
            entries = sorted(t.iterdir(), key=lambda x: x.name.lower())
            out_count = 0
            for e in entries:
                if not show_all and e.name.startswith("."): continue
                print_ls_entry(e, long=long, human=human); out_count += 1
            if not long and out_count: print()
        except PermissionError:
            print("ls: permission denied")

# ---------- core cmds ----------
def cmd_rm(cwd: Path, args: list):
    force = False; recursive = False; targets = []
    for a in args:
        if a.startswith("-"):
            if "f" in a: force = True
            if "r" in a: recursive = True
        else:
            targets.append(a)
    for t in targets:
        p = resolve_path(cwd, t)
        if not p.exists():
            if not force: print(f"rm: cannot remove '{t}': No such file or directory")
            continue
        try:
            if p.is_dir():
                if recursive: shutil.rmtree(p, ignore_errors=force)
                else: p.rmdir()
            else:
                p.unlink(missing_ok=True)
            k = meta_key(p)
            if k in META: del META[k]; json_save(META_FILE, META)
        except Exception as e:
            if not force: print(f"rm: cannot remove '{t}': {e}")

def cmd_cp(cwd: Path, args: list):
    recursive = False; rest = []
    for a in args:
        if a.startswith("-"):
            if "r" in a: recursive = True
        else:
            rest.append(a)
    if len(rest) < 2:
        print("Usage: cp [-r] <src>... <dst>"); return
    *srcs, dst = rest
    dst_p = resolve_path(cwd, dst)
    try:
        if len(srcs) > 1:
            dst_p.mkdir(parents=True, exist_ok=True)
            for s in srcs:
                sp = resolve_path(cwd, s)
                if not sp.exists(): print(f"cp: cannot stat '{s}': No such file or directory"); continue
                if sp.is_dir():
                    if not recursive: print(f"cp: -r not specified; omitting directory '{s}'"); continue
                    shutil.copytree(sp, dst_p / sp.name, dirs_exist_ok=True)
                else:
                    shutil.copy2(sp, dst_p / sp.name)
        else:
            sp = resolve_path(cwd, srcs[0])
            if not sp.exists(): print(f"cp: cannot stat '{srcs[0]}': No such file or directory"); return
            if sp.is_dir():
                if not recursive: print(f"cp: -r not specified; omitting directory '{srcs[0]}'"); return
                shutil.copytree(sp, dst_p, dirs_exist_ok=True)
            else:
                dst_p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sp, dst_p)
    except Exception as e:
        print(f"cp: {e}")

def cmd_mv(cwd: Path, args: list):
    if len(args) < 2: print("Usage: mv <src>... <dst>"); return
    *srcs, dst = args
    dst_p = resolve_path(cwd, dst)
    try:
        if len(srcs) > 1:
            dst_p.mkdir(parents=True, exist_ok=True)
            for s in srcs:
                sp = resolve_path(cwd, s)
                if not sp.exists(): print(f"mv: cannot stat '{s}': No such file or directory"); continue
                sp.rename(dst_p / sp.name)
        else:
            sp = resolve_path(cwd, srcs[0])
            if not sp.exists(): print(f"mv: cannot stat '{srcs[0]}': No such file or directory"); return
            dst_p.parent.mkdir(parents=True, exist_ok=True)
            sp.rename(dst_p)
    except Exception as e:
        print(f"mv: {e}")

def cmd_echo(cwd: Path, args: list):
    if not args: print(); return
    if ">>" in args or ">" in args:
        op_idx = max((i for i,a in enumerate(args) if a in (">",">>")), default=-1)
        if op_idx == -1 or op_idx == len(args)-1: print("shell: redirection parse error"); return
        text = " ".join(args[:op_idx]); fname = args[op_idx+1]
        fpath = resolve_path(cwd, fname); fpath.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if args[op_idx] == ">>" else "w"
        with open(fpath, mode, encoding="utf-8") as f:
            f.write(text + ("\n" if not text.endswith("\n") else ""))
    else:
        print(" ".join(args))

def cmd_grep(cwd: Path, args: list):
    ignore = False; rec = False; rest = []
    for a in args:
        if a.startswith("-"):
            if "i" in a: ignore = True
            if "r" in a: rec = True
        else:
            rest.append(a)
    if not rest: print("Usage: grep [options] PATTERN [FILE...]"); return
    pattern, *files = rest
    def match(line: str) -> bool: return (pattern.lower() in line.lower()) if ignore else (pattern in line)
    results = []
    try:
        if rec:
            roots = [resolve_path(cwd, f) for f in files] if files else [cwd]
            for r in roots:
                if not r.exists(): continue
                for p in r.rglob("*"):
                    if p.is_file():
                        try:
                            for i,line in enumerate(p.read_text(errors="ignore").splitlines(),1):
                                if match(line): results.append(f"{p.relative_to(SYSTEM_ROOT)}:{i}:{line}")
                        except Exception: pass
        else:
            if not files: print("grep: no file specified"); return
            for f in files:
                p = resolve_path(cwd, f)
                if p.exists() and p.is_file():
                    try:
                        for i,line in enumerate(p.read_text(errors="ignore").splitlines(),1):
                            if match(line): results.append(f"{p.relative_to(SYSTEM_ROOT)}:{i}:{line}")
                    except Exception: pass
                else:
                    print(f"grep: {f}: No such file or directory")
        if results: print("\n".join(results))
    except Exception as e:
        print(f"grep: {e}")

def cmd_chmod(cwd: Path, args: list):
    if len(args) < 2: print("Usage: chmod MODE FILE..."); return
    mode = args[0]
    for name in args[1:]:
        p = resolve_path(cwd, name)
        if not p.exists(): print(f"chmod: cannot access '{name}': No such file or directory"); continue
        meta_set(p, mode=mode)

def cmd_chown(cwd: Path, args: list):
    if len(args) < 2: print("Usage: chown OWNER[:GROUP] FILE..."); return
    owner_group = args[0]
    owner, group = (owner_group.split(":", 1) + [owner_group])[:2] if ":" in owner_group else (owner_group, owner_group)
    for name in args[1:]:
        p = resolve_path(cwd, name)
        if not p.exists(): print(f"chown: cannot access '{name}': No such file or directory"); continue
        meta_set(p, owner=owner, group=group)

def cmd_which(args: list):
    if not args: print("which: missing operand"); return
    for name in args:
        path = shutil.which(name)
        print(path if path else f"{name} not found")

# ---------- APT/DPKG (simulation) ----------
def http_download(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:
        return resp.read()

def ar_list_members(data: bytes):
    assert data[:8] == b"!<arch>\n", "Not an ar archive"
    i = 8
    while i + 60 <= len(data):
        header = data[i:i+60]; i += 60
        name = header[:16].decode("utf-8", "ignore").strip()
        size = int(header[48:58].decode().strip())
        body = data[i:i+size]; i += size
        if i % 2 == 1: i += 1
        yield name, body

def deb_extract_data_tar(deb_bytes: bytes):
    data_member = None
    for name, body in ar_list_members(deb_bytes):
        if name.startswith("data.tar"):
            data_member = (name, body); break
    if not data_member: raise ValueError("No data.tar.* in .deb")
    name, body = data_member
    if name.endswith(".xz"):
        tarf = tarfile.open(fileobj=BytesIO(lzma.decompress(body)), mode="r:")
    elif name.endswith(".gz"):
        tarf = tarfile.open(fileobj=BytesIO(gzip.decompress(body)), mode="r:")
    else:
        tarf = tarfile.open(fileobj=BytesIO(body), mode="r:")
    return tarf

def dpkg_install_deb(cwd: Path, deb_path: Path, pkg_name_hint: str = None):
    data = deb_path.read_bytes()
    tarf = deb_extract_data_tar(data)
    installed_files = []
    try:
        for m in tarf.getmembers():
            if not (m.isfile() or m.isdir()): continue
            rel = Path(m.name.lstrip("./"))
            dest = (SYSTEM_ROOT / rel).resolve()
            if SYSTEM_ROOT not in dest.parents and dest != SYSTEM_ROOT: continue
            if m.isdir(): dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tarf.extractfile(m) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
            installed_files.append(str(rel.as_posix()))
    finally:
        tarf.close()

    pkg_name = pkg_name_hint or deb_path.stem.split("_")[0]
    PKG_DB["installed"].setdefault(pkg_name, {"files": []})
    PKG_DB["installed"][pkg_name]["files"].extend(installed_files)
    pkg_db_save()
    print(f"Selecting previously unselected package {pkg_name}.")
    print(f"({deb_path.name}) unpacked.")
    print(f"{pkg_name} installed (simulated).")
    print("⚠️  Note: native Linux binaries from .deb do not run on Windows. Scripts/resources do.")

def apt_install(cwd: Path, pkg_name: str):
    reg = json_load(APT_REGISTRY, {"packages": {}}).get("packages", {})
    meta = reg.get(pkg_name)
    if not meta or "url" not in meta:
        print(f"E: Unable to locate package {pkg_name}")
        print(f"Tip: add the package + .deb URL in {APT_REGISTRY}")
        return
    url = meta["url"]
    print(f"Downloading {url} ...")
    try:
        deb_bytes = http_download(url)
    except Exception as e:
        print(f"E: download error: {e}")
        return
    tmp_deb = SYSTEM_ROOT / "var" / "tmp" / f"{pkg_name}.deb"
    tmp_deb.parent.mkdir(parents=True, exist_ok=True)
    tmp_deb.write_bytes(deb_bytes)
    dpkg_install_deb(cwd, tmp_deb, pkg_name)

def dpkg_remove(pkg_name: str):
    rec = PKG_DB["installed"].get(pkg_name)
    if not rec:
        print(f"dpkg: warning: {pkg_name} is not installed"); return
    files = rec.get("files", [])
    for rel in sorted(files, key=lambda x: len(x.split("/")), reverse=True):
        p = (SYSTEM_ROOT / rel).resolve()
        try:
            if p.is_file(): p.unlink(missing_ok=True)
            elif p.is_dir():
                try: p.rmdir()
                except OSError: pass
        except Exception: pass
    del PKG_DB["installed"][pkg_name]
    pkg_db_save()
    print(f"Removed {pkg_name} (simulated).")

# ---------- extern/helpers ----------
def to_host_path(p: Path) -> str:
    return str(p)

def map_args_virtual_to_host(cwd: Path, args: list) -> list:
    mapped = []
    for a in args:
        if a.startswith("-") or "://" in a:
            mapped.append(a); continue
        vp = resolve_path(cwd, a)
        mapped.append(to_host_path(vp) if vp.exists() else a)
    return mapped

ALIASES = {"pip3": "pip", "python3": "python", "apt-get": "apt"}

def run_host_process(argv, cwd: Path, env: dict | None = None):
    try:
        p = subprocess.Popen(argv, cwd=str(cwd), env=env)
        p.wait()
    except Exception as e:
        print(f"{argv[0]}: {e}")

def win_to_msys_path(win_path: str) -> str:
    p = win_path.replace("\\", "/")
    if len(p) > 1 and p[1] == ":":
        drive = p[0].lower(); rest = p[2:]
        if rest.startswith("/"): rest = rest[1:]
        return f"/{drive}/{rest}"
    if p.startswith("//"):  # UNC
        return "/unc" + p
    return p

def find_bash() -> str | None:
    # Prefer Portable Git in tools
    bash = find_portable_git_bash_in(GIT_HOME)
    if bash:
        return bash
    # Fallback to system
    p = shutil.which("bash")
    if p: return p
    # Try common installs
    for c in (r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files\Git\usr\bin\bash.exe",
              r"C:\Program Files (x86)\Git\bin\bash.exe",
              r"C:\Program Files (x86)\Git\usr\bin\bash.exe"):
        if os.path.exists(c): return c
    for env_name in ("BASH_HOME", "GIT_HOME"):
        base = os.getenv(env_name)
        if base:
            for sub in ("bin\\bash.exe", "usr\\bin\\bash.exe", "bash.exe"):
                test = os.path.join(base, sub)
                if os.path.exists(test): return test
    return None

def _try_run_get_exe(cmdlist: list[str]) -> str | None:
    try:
        out = subprocess.check_output(cmdlist, stderr=subprocess.STDOUT, text=True)
        exe = out.strip().splitlines()[-1].strip().strip('"')
        return exe if exe and os.path.exists(exe) else None
    except Exception:
        return None

def find_python() -> str | None:
    # 1) current interpreter
    if sys.executable and os.path.exists(sys.executable):
        return sys.executable

    # 2) Windows py launcher
    exe = _try_run_get_exe(["py", "-3", "-c", "import sys; print(sys.executable)"])
    if exe: return exe
    exe = _try_run_get_exe(["py", "-c", "import sys; print(sys.executable)"])
    if exe: return exe

    # 3) PATH search
    for name in ("python3", "python", "python3.exe", "python.exe"):
        p = shutil.which(name)
        if p and os.path.exists(p): return p

    # 4) common install paths
    candidates = []
    user = os.getenv("USERNAME") or os.getenv("USER") or ""
    for base in [
        fr"C:\Users\{user}\AppData\Local\Programs\Python",
        r"C:\Program Files\Python311",
        r"C:\Program Files\Python312",
        r"C:\Program Files\Python313",
        r"C:\Program Files\Python314",
        r"C:\Program Files (x86)\Python311",
        r"C:\Program Files (x86)\Python312",
    ]:
        p = Path(base)
        if p.exists():
            if p.name.lower().startswith("python3"):
                exe = p / "python.exe"
                if exe.exists(): candidates.append(str(exe))
            else:
                for sub in p.glob("Python3*/python.exe"):
                    candidates.append(str(sub))
                for sub in p.glob("Python*/python.exe"):
                    candidates.append(str(sub))
    for c in candidates:
        if os.path.exists(c): return c

    return None

# ---------- dispatcher ----------
def run_command(line: str, cwd: Path) -> Path:
    if not line.strip(): return cwd
    tokens = shlex.split(line)
    cmd, *args = tokens

    # built-ins
    if cmd == "exit": print("Bye!"); sys.exit(0)
    elif cmd == "pwd":
        rel = cwd.relative_to(SYSTEM_ROOT)
        print("/" if not rel.parts else "/" + "/".join(rel.parts)); return cwd
    elif cmd == "ls":   cmd_ls(cwd, args); return cwd
    elif cmd == "cd":
        dest = resolve_path(cwd, args[0]) if args else (SYSTEM_ROOT / "home" / USER)
        if dest.exists() and dest.is_dir() and (SYSTEM_ROOT in dest.parents or dest == SYSTEM_ROOT): return dest
        print(f"cd: {args[0] if args else ''}: No such directory"); return cwd
    elif cmd == "mkdir":
        for a in args: resolve_path(cwd, a).mkdir(parents=True, exist_ok=True); return cwd
    elif cmd == "touch":
        for a in args:
            p = resolve_path(cwd, a); p.parent.mkdir(parents=True, exist_ok=True); p.touch(exist_ok=True)
        return cwd
    elif cmd == "cat":
        if not args: print("cat: missing file operand")
        else:
            for a in args:
                p = resolve_path(cwd, a)
                if p.exists() and p.is_file(): print(p.read_text(), end="")
                else: print(f"cat: {a}: No such file")
        return cwd
    elif cmd == "echo": cmd_echo(cwd, args); return cwd
    elif cmd == "whoami": print(USER); return cwd
    elif cmd == "clear": os.system("cls" if os.name == "nt" else "clear"); return cwd
    elif cmd == "tree":
        for root, dirs, files in os.walk(cwd):
            rel = Path(root).relative_to(cwd); indent = "  " * len(rel.parts)
            print(f"{indent}{Path(root).name}/"); [print(f"{indent}  {f}") for f in files]
        return cwd
    elif cmd == "help":
        print("Available commands:")
        print("  ls [-a] [-l] [-h] [path]")
        print("  cd [dir]     pwd")
        print("  mkdir <dir>  touch <file>  cat <file>")
        print("  echo TEXT [> file | >> file]")
        print("  rm [-r] [-f] <path>    cp [-r] <src>... <dst>    mv <src>... <dst>")
        print("  grep [-i] [-r] PATTERN [FILE...]")
        print("  chmod MODE FILE...   (sim)   chown OWNER[:GROUP] FILE...   (sim)")
        print("  which <cmd>   whoami   clear   tree   help   exit")
        print("  apt install <pkg>    apt remove <pkg>    dpkg -i FILE.deb    dpkg -r <pkg>   (SIMULATION)")
        print(f"Registry: {APT_REGISTRY} (add packages + .deb URL here)")
        print("⚠️  Linux binaries from .deb will not run on Windows; scripts/resources do.")
        return cwd
    elif cmd == "rm": cmd_rm(cwd, args); return cwd
    elif cmd == "cp": cmd_cp(cwd, args); return cwd
    elif cmd == "mv": cmd_mv(cwd, args); return cwd
    elif cmd == "grep": cmd_grep(cwd, args); return cwd
    elif cmd == "chmod": cmd_chmod(cwd, args); return cwd
    elif cmd == "chown": cmd_chown(cwd, args); return cwd
    elif cmd == "which": cmd_which(args); return cwd

    # sudo (sim)
    if cmd == "sudo":
        if not args: print("sudo: usage: sudo <command> [args...]"); return cwd
        print("[sudo simulated] running:", " ".join(args))
        return run_command(" ".join(args), cwd)

    # apt / apt-get (sim)
    if cmd in ("apt", "apt-get"):
        if not args: print("Usage: apt install <pkg> | apt remove <pkg>"); return cwd
        sub = args[0]; rest = args[1:]
        if sub in ("install", "i"):
            if not rest: print("apt: missing package name"); return cwd
            apt_install(cwd, rest[0]); return cwd
        elif sub in ("remove", "purge", "r"):
            if not rest: print("apt: missing package name"); return cwd
            dpkg_remove(rest[0]); return cwd
        else:
            print("Supported: apt install <pkg>, apt remove <pkg> (simulation)"); return cwd

    # dpkg (sim)
    if cmd == "dpkg":
        if not args: print("Usage: dpkg -i FILE.deb | dpkg -r <pkg>"); return cwd
        if args[0] == "-i":
            if len(args) < 2: print("dpkg: missing .deb filename"); return cwd
            deb = resolve_path(cwd, args[1])
            if not deb.exists(): print(f"dpkg: {args[1]}: No such file"); return cwd
            try:
                dpkg_install_deb(cwd, deb)
            except Exception as e:
                print(f"dpkg: error installing: {e}")
            return cwd
        elif args[0] == "-r":
            if len(args) < 2: print("dpkg: missing package name"); return cwd
            dpkg_remove(args[1]); return cwd
        else:
            print("Supported: dpkg -i FILE.deb, dpkg -r <pkg>  (simulation)"); return cwd

    # --- path execution / scripts ---
    if "/" in cmd or "\\" in cmd or cmd.startswith("."):
        target = resolve_path(cwd, cmd)
        if target.exists() and target.is_file():
            host = str(target)

            # .sh → run in Git Bash with python auto-detect + SHELL SHIM + PATH prep
            if host.lower().endswith(".sh"):
                # ensure deps
                ensure_pip_deps()
                bash = find_bash()
                if not bash:
                    if AUTO_DOWNLOAD_TOOLS:
                        bash = ensure_git_bash()
                    if not bash:
                        print("bash: not found and auto-install failed. Install Git for Windows (Portable or full).")
                        return cwd

                pyexe = find_python()
                msys_script = win_to_msys_path(host)

                # clean PATH (remove WindowsApps aliases)
                env = os.environ.copy()
                env["PATH"] = os.pathsep.join([p for p in env.get("PATH","").split(os.pathsep) if "WindowsApps" not in p])

                # ALWAYS write a shell 'python3' shim in /usr/bin
                shim_dir = SYSTEM_ROOT / "usr" / "bin"
                shim_dir.mkdir(parents=True, exist_ok=True)
                shim_path = shim_dir / "python3"
                if pyexe:
                    shim_path.write_text(
                        '#!/usr/bin/env bash\n'
                        f'"{win_to_msys_path(pyexe)}" "$@"\n',
                        encoding="utf-8"
                    )
                    try: os.chmod(shim_path, 0o755)
                    except Exception: pass

                msys_shim_dir = win_to_msys_path(str(shim_dir))
                if pyexe:
                    py_dir = win_to_msys_path(str(Path(pyexe).parent))
                    py_scripts = win_to_msys_path(str(Path(pyexe).parent / "Scripts"))
                    bash_cmd = f"export PATH='{msys_shim_dir}':'{py_dir}':'{py_scripts}':\"$PATH\"; exec \"{msys_script}\""
                else:
                    bash_cmd = f"export PATH='{msys_shim_dir}':\"$PATH\"; exec \"{msys_script}\""

                run_host_process([bash, "-lc", bash_cmd], cwd, env=env)
                return cwd

            # .py → run with detected Python
            if host.lower().endswith(".py"):
                ensure_pip_deps()
                pyexe = find_python()
                if pyexe:
                    run_host_process([pyexe, host], cwd)
                else:
                    print("python: not found. Install Python or enable the 'py' launcher.")
                return cwd

            # .bat/.cmd → run directly
            if host.lower().endswith(".bat") or host.lower().endswith(".cmd"):
                run_host_process([host], cwd); return cwd

            # generic: try to run
            run_host_process([host], cwd); return cwd
        else:
            print(f"{cmd}: No such file"); return cwd

    # git
    if cmd == "git":
        ensure_pip_deps()
        real = shutil.which("git")
        if not real:
            # try Portable Git if auto-downloaded
            bash_path = ensure_git_bash() if AUTO_DOWNLOAD_TOOLS else None
            # git.exe is alongside PortableGit's `mingw64/bin/git.exe` (or cmd/git.exe)
            possible = [
                GIT_HOME / "cmd" / "git.exe",
                GIT_HOME / "mingw64" / "bin" / "git.exe",
                GIT_HOME / "mingw32" / "bin" / "git.exe",
            ]
            for p in possible:
                if p.exists():
                    real = str(p); break

        if real:
            host_args = map_args_virtual_to_host(cwd, args)
            try:
                p = subprocess.Popen([real] + host_args, cwd=str(cwd))
                p.wait()
            except Exception as e:
                print(f"git error: {e}")
        else:
            if args[:1] == ["clone"] and len(args) >= 2:
                url = args[1]
                name = url.rstrip("/").split("/")[-1]
                if name.endswith(".git"): name = name[:-4]
                target = cwd / name
                target.mkdir(parents=True, exist_ok=True)
                (target / "README.txt").write_text(
                    f"Dummy clone of {url}\n(No real git available)\n", encoding="utf-8"
                )
                print(f"Cloning into '{name}'...\nDone (dummy). Install Git or enable AUTO_DOWNLOAD_TOOLS.")
            else:
                print("git: command not found (install Git or enable AUTO_DOWNLOAD_TOOLS).")
        return cwd

    # fallback host tools
    real_cmd = shutil.which(ALIASES.get(cmd, cmd))
    if real_cmd:
        ensure_pip_deps()
        mapped = map_args_virtual_to_host(cwd, [ALIASES.get(cmd, cmd)] + args)
        try:
            p = subprocess.Popen(mapped, cwd=str(cwd))
            p.wait()
        except Exception as e:
            print(f"{cmd}: {e}")
    else:
        print(f"{cmd}: command not found")
    return cwd

# ---------- main ----------
def main():
    migrate_from_krnl_if_needed()

    first = False
    if not SYSTEM_ROOT.exists() or not INIT_MARKER.exists():
        first = True
        init_system()

    # Ensure core deps up-front so first run is smooth
    ensure_pip_deps()
    if AUTO_DOWNLOAD_TOOLS and not find_bash():
        ensure_git_bash()  # best effort; als het faalt, wordt later nog gemeld

    header = "Initializing virtual Linux system..." if first else "Virtual system mounted"
    print(f"{c(C_CYAN)}{BRAND} {VERSION} – {header}{c(C_RESET)}\n")
    cwd = SYSTEM_ROOT / "home" / USER
    cwd.mkdir(parents=True, exist_ok=True)
    print(f"Mounted virtual system at {SYSTEM_ROOT}")
    print("Home directory:", cwd)
    print("Type 'help' for available commands.\n")

    while True:
        try:
            line = input(prompt(cwd))
            cwd = run_command(line, cwd)
        except KeyboardInterrupt:
            print("^C")
        except EOFError:
            print()
            break
        except Exception as e:
            print(f"Shell error: {e}")

if __name__ == "__main__":
    main()
