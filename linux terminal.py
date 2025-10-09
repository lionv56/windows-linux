#!/usr/bin/env python3
# Linux Terminal v3.9-gdrive – Virtual Linux-like shell for Windows (LinuxFS root)
# - Behoudt ALLE functies (ls/cd/cp/mv/rm/grep/apt/dpkg/.sh/.py/.bat, metadata, migratie, help)
# - Portable Git installatie ALLEEN via Google Drive ZIP met JOUW gdown-snippet
#   (we dempen alleen het consolegeluid en printen eigen nette progress)
# - Na download: unzip naar LinuxFS\tools\git, fix 1-level dieper structuur, zoek git.exe
# - .sh draait via Git Bash (van portable Git), python3-shim wordt toegevoegd
# - Git + SSH config in virtuele HOME
# - APT/DPKG simulatie
# - Mooie, rustige output + duidelijke banners

import os, sys, shlex, stat, json, shutil, subprocess, tarfile, lzma, gzip, urllib.request, zipfile, time, math
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from datetime import datetime
from io import BytesIO, StringIO

# ===================== CONFIG =====================
# Google Drive-bestanden (maak de links publiek!)
GDRIVE_FILE_URL   = "https://drive.google.com/file/d/17NFMgGpHWQoRq7Z_MgrXdwSjB47VeBq0/view?usp=sharing"  # PortableGit ZIP
MSYS_ADDONS_URL   = "https://drive.google.com/file/d/1idRCsEzYFaraOOTAgfQp0i8vjmRnZ0oM/view?usp=sharing"  # MSYS add-ons ZIP (optioneel)

AUTO_DOWNLOAD_TOOLS = True
SHOW_TIMINGS        = True
REQUIRED_PIP_PACKAGES = ["colorama"] + (["pyreadline3"] if os.name == "nt" else [])
# ==================================================

# ---------- kleur ----------
try:
    import colorama
    colorama.just_fix_windows_console()
    USE_COLOR = sys.stdout.isatty()
except Exception:
    USE_COLOR = sys.stdout.isatty() and os.name != "nt"

def c(s: str) -> str: return s if USE_COLOR else ""
C_RESET, C_GREEN, C_BLUE, C_CYAN, C_YELLOW, C_RED = "\033[0m","\033[92m","\033[94m","\033[96m","\033[93m","\033[91m"

# ---------- paden & state ----------
SCRIPT_DIR = Path(__file__).resolve().parent

SYSTEM_DIR_NAME = "LinuxFS"
SYSTEM_ROOT  = SCRIPT_DIR / SYSTEM_DIR_NAME
INIT_MARKER  = SYSTEM_ROOT / ".linux_initialized"
META_FILE    = SYSTEM_ROOT / ".linux_meta.json"
PKG_DB_FILE  = SYSTEM_ROOT / ".linux_packages.json"
APT_REGISTRY = SYSTEM_ROOT / "etc" / "linux_apt_registry.json"

TOOLS_DIR    = SYSTEM_ROOT / "tools"
GIT_HOME     = TOOLS_DIR / "git"
CACHE_DIR    = SYSTEM_ROOT / "var" / "cache" / "downloads"

BRAND, VERSION, HOSTNAME = "Linux Terminal", "v1.0-gdrive", "linux"
USER = os.getenv("USER") or os.getenv("USERNAME") or "user"
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
        path.parent.mkdir(parents=True, exist_ok=True)
        json.dump(data, open(path, "w", encoding="utf-8"), indent=2)
    except Exception:
        pass

META   = json_load(META_FILE, {})
PKG_DB = json_load(PKG_DB_FILE, {"installed": {}})

def meta_key(p: Path) -> str:
    try: return str(p.relative_to(SYSTEM_ROOT).as_posix())
    except Exception: return ""

def meta_get(p: Path) -> dict: return META.get(meta_key(p), {})
def meta_set(p: Path, **kwargs):
    k = meta_key(p)
    if not k: return
    rec = META.get(k, {}); rec.update(kwargs); META[k] = rec
    json_save(META_FILE, META)
def pkg_db_save(): json_save(PKG_DB_FILE, PKG_DB)

# ---------- migratie KRNL → LinuxFS ----------
def migrate_from_krnl_if_needed():
    old_root = SCRIPT_DIR / "KRNL_System"
    if not old_root.exists(): return
    # als LinuxFS al bestaat, doen we niets (idempotent)
    if SYSTEM_ROOT.exists(): return
    try:
        shutil.move(str(old_root), str(SYSTEM_ROOT))
    except Exception:
        shutil.copytree(old_root, SYSTEM_ROOT, dirs_exist_ok=True)
        shutil.rmtree(old_root, ignore_errors=True)
    # rename markers/database
    for src, dst in [
        (SYSTEM_ROOT / ".krnl_initialized", INIT_MARKER),
        (SYSTEM_ROOT / ".krnl_meta.json",   META_FILE),
        (SYSTEM_ROOT / ".krnl_packages.json", PKG_DB_FILE),
    ]:
        if src.exists() and not dst.exists():
            try: src.rename(dst)
            except Exception: pass
    # registry
    krnl_reg = SYSTEM_ROOT / "etc" / "krnl_apt_registry.json"
    if krnl_reg.exists() and not APT_REGISTRY.exists():
        (SYSTEM_ROOT / "etc").mkdir(parents=True, exist_ok=True)
        try: krnl_reg.rename(APT_REGISTRY)
        except Exception:
            shutil.move(str(krnl_reg), str(APT_REGISTRY))

# ---------- init / ensure structure ----------
def ensure_structure():
    """Idempotent: maak/herstelt alle basispaden & files, onafhankelijk van eerste run."""
    for d in [f"home/{USER}","root","usr/bin","usr/lib","usr/share","etc","var/log","var/tmp","var/cache/downloads","bin","tmp","tools"]:
        (SYSTEM_ROOT / d).mkdir(parents=True, exist_ok=True)
    motd = SYSTEM_ROOT / "etc" / "motd.txt"
    if not motd.exists():
        motd.write_text(f"Welcome to {BRAND} {VERSION}!\nType 'help' for commands.\n", encoding="utf-8")
    if not APT_REGISTRY.exists(): json_save(APT_REGISTRY, {"packages": {}})
    if not META_FILE.exists(): json_save(META_FILE, {})
    if not PKG_DB_FILE.exists(): json_save(PKG_DB_FILE, {"installed": {}})
    if not INIT_MARKER.exists(): INIT_MARKER.write_text("initialized\n")

# ---------- deps ----------
def pip_install(package: str) -> bool:
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", package])
        return True
    except Exception:
        return False

def ensure_pip_deps():
    missing=[]
    for pkg in REQUIRED_PIP_PACKAGES:
        try: __import__(pkg.split("==")[0].split(">=")[0])
        except Exception: missing.append(pkg)
    if not missing: return
    print(f"{c(C_YELLOW)}Installing Python packages: {', '.join(missing)} ...{c(C_RESET)}")
    ok=True
    for p in missing: ok = pip_install(p) and ok
    if not ok: print(f"{c(C_RED)}Warning:{c(C_RESET)} Some Python packages failed; continuing...")

# ---------- nette progress utils ----------
def _fmt_s(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s}s"

def _fmt_bytes(n: int) -> str:
    if n < 1024: return f"{n}B"
    units = ["KB","MB","GB","TB"]
    x = float(n); i=0
    while x>=1024 and i < len(units)-1:
        x/=1024; i+=1
    return f"{x:.1f}{units[i]}"

def _print_inline(msg: str):
    sys.stdout.write("\r"+msg)
    sys.stdout.flush()

def _println(msg=""):
    sys.stdout.write(msg+"\n")
    sys.stdout.flush()

# ---------- gdown (Drive) ----------
def ensure_gdown() -> bool:
    try:
        import gdown  # noqa: F401
        return True
    except Exception:
        return pip_install("gdown")

def download_git_zip_via_your_snippet(url: str, label: str) -> Path | None:
    """
    JOUW snippet blijft exact (quiet=False), maar we dempen stdout/stderr en tonen eigen nette progress:
      # pip install gdown
      import gdown
      gdown.download(url=url, output=None, quiet=False, use_cookies=False, fuzzy=True)
    """
    if not ensure_gdown():
        _println(f"{c(C_RED)}Error:{c(C_RESET)} kon gdown niet installeren.")
        return None

    # print nette "Downloading …"
    start = time.time()
    _print_inline(f"Downloading {label} …")

    # === Exacte snippet, maar met stdout/stderr gedempt ===
    import gdown  # noqa: E402
    buf_out, buf_err = StringIO(), StringIO()
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        downloaded_path = gdown.download(url=url, output=None, quiet=False, use_cookies=False, fuzzy=True)
    # =====================================================

    elapsed = time.time() - start
    if not downloaded_path:
        _println(f"\rDownloading {label} … {c(C_RED)}failed{c(C_RESET)}")
        return None

    src = Path(downloaded_path).resolve()
    if not src.exists():
        _println(f"\rDownloading {label} … {c(C_RED)}file missing{c(C_RESET)}")
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # Zorg voor voorspelbare bestandsnaam
    dest = CACHE_DIR / (label if label.lower().endswith(".zip") else (src.name))
    if dest.exists():
        try: dest.unlink()
        except Exception: pass
    try:
        shutil.move(str(src), str(dest))
    except Exception:
        shutil.copy2(src, dest)
        try: src.unlink()
        except Exception: pass

    _println(f"\rDownloading {label} … downloaded in {_fmt_s(elapsed)}")
    return dest

# ---------- unzip met progress ----------
def unzip_with_progress(src_zip: Path, dest_dir: Path, label: str) -> bool:
    try:
        with zipfile.ZipFile(src_zip, "r") as z:
            infos = z.infolist()
            total = sum(i.file_size for i in infos)
            done = 0
            start = time.time()
            # Schone doelmap is elders al geregeld; hier alleen uitpakken
            _print_inline(f"Extracting {label} … 0% (0B/{_fmt_bytes(total)})")
            for i in infos:
                # maak dir
                out_path = dest_dir / i.filename
                if i.is_dir():
                    out_path.mkdir(parents=True, exist_ok=True)
                    continue
                out_path.parent.mkdir(parents=True, exist_ok=True)
                # extract met stream voor progress
                with z.open(i, "r") as src, open(out_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                done += i.file_size
                pct = 0 if total==0 else int(done*100/total)
                _print_inline(f"Extracting {label} … {pct}% ({_fmt_bytes(done)}/{_fmt_bytes(total)})")
            elapsed = time.time() - start
            _println(f"\rExtracting {label} … done in {_fmt_s(elapsed)}")
        return True
    except zipfile.BadZipFile:
        _println(f"\rExtracting {label} … {c(C_RED)}bad zip{c(C_RESET)}")
        return False
    except Exception as e:
        _println(f"\rExtracting {label} … {c(C_RED)}error:{c(C_RESET)} {e}")
        return False

# ---------- Portable Git detect/install (ALLEEN Drive ZIP) ----------
def find_portable_git_bash_in(git_root: Path) -> str | None:
    for cnd in [git_root/"usr/bin/bash.exe", git_root/"bin/bash.exe"]:
        if cnd.exists(): return str(cnd)
    return None

def find_git_exe() -> str | None:
    for p in [GIT_HOME/"cmd/git.exe", GIT_HOME/"mingw64/bin/git.exe", GIT_HOME/"mingw32/bin/git.exe", GIT_HOME/"bin/git.exe"]:
        if p.exists(): return str(p)
    return shutil.which("git")

def ensure_portable_git_via_drive_pretty() -> bool:
    """Installeer Portable Git ALLEEN via GDrive ZIP; toon nette progress."""
    if find_git_exe():
        return True
    if not AUTO_DOWNLOAD_TOOLS:
        return False

    zip_path = download_git_zip_via_your_snippet(GDRIVE_FILE_URL, "PortableGit.zip")
    if not zip_path:
        return False

    # Leeg doelmap
    GIT_HOME.mkdir(parents=True, exist_ok=True)
    for child in list(GIT_HOME.iterdir()):
        try:
            if child.is_dir(): shutil.rmtree(child)
            else: child.unlink()
        except Exception: pass

    if not unzip_with_progress(zip_path, GIT_HOME, "PortableGit.zip"):
        return False

    # fix 1-level dieper
    if not find_git_exe():
        subs = [p for p in GIT_HOME.iterdir() if p.is_dir()]
        if len(subs) == 1:
            inner = subs[0]
            try:
                for item in list(inner.iterdir()):
                    shutil.move(str(item), str(GIT_HOME / item.name))
                inner.rmdir()
            except Exception as e:
                _println(f"{c(C_YELLOW)}Note:{c(C_RESET)} structure fix failed: {e}")

    ok = bool(find_git_exe())
    if not ok:
        _println(f"{c(C_RED)}Portable Git not found after extract — is the ZIP correct?{c(C_RESET)}")
    return ok

# ---------- SSH helpers ----------
def find_ssh_bins() -> tuple[str|None,str|None]:
    ssh = shutil.which("ssh"); keygen = shutil.which("ssh-keygen")
    if ssh and keygen: return ssh, keygen
    ssh_c = GIT_HOME/"usr/bin/ssh.exe"; keygen_c = GIT_HOME/"usr/bin/ssh-keygen.exe"
    return (str(ssh_c) if ssh_c.exists() else None, str(keygen_c) if keygen_c.exists() else None)

def ensure_git_config_and_ssh(virtual_home: Path):
    env_over={}
    home = virtual_home; home.mkdir(parents=True, exist_ok=True)
    env_over["HOME"] = str(home)
    git_exe = find_git_exe()
    if git_exe:
        def git_get(key):
            try:
                out = subprocess.check_output([git_exe,"config","--global","--get",key],
                                              text=True, stderr=subprocess.DEVNULL, env={**os.environ, **env_over})
                return out.strip()
            except subprocess.CalledProcessError:
                return ""
        if not git_get("user.name"):
            subprocess.run([git_exe,"config","--global","user.name",USER], env={**os.environ, **env_over})
        if not git_get("user.email"):
            subprocess.run([git_exe,"config","--global","user.email",f"{USER}@local"], env={**os.environ, **env_over})
    ssh_dir = home/".ssh"; ssh_dir.mkdir(parents=True, exist_ok=True)
    priv, pub = ssh_dir/"id_ed25519", ssh_dir/"id_ed25519.pub"
    ssh, keygen = find_ssh_bins()
    if (not priv.exists() or not pub.exists()) and keygen:
        try: subprocess.run([keygen,"-t","ed25519","-N","","-f",str(priv)], check=False, env={**os.environ, **env_over})
        except Exception: pass
    if ssh and priv.exists():
        env_over["GIT_SSH_COMMAND"] = f"\"{ssh}\" -i \"{priv}\" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
    git_path_dir = Path(git_exe).parent if git_exe else None
    if git_path_dir:
        env_over["PATH"] = os.pathsep.join([str(git_path_dir), os.environ.get("PATH","")])
    return env_over

# ---------- path helpers ----------
def resolve_path(cwd: Path, arg: str) -> Path:
    if not arg: return cwd
    if arg == "/": return SYSTEM_ROOT
    if arg.startswith("/"): return (SYSTEM_ROOT / arg.lstrip("/")).resolve()
    if arg.startswith("~"): return (SYSTEM_ROOT/"home"/USER/(arg[2:] if arg.startswith("~/") else "")).resolve()
    return (cwd/arg).resolve()

def prompt(cwd: Path) -> str:
    symbol = "#" if IS_ROOT else "$"
    rel = cwd.relative_to(SYSTEM_ROOT)
    disp = "/" if not rel.parts else "/" + "/".join(rel.parts)
    return f"{c(C_GREEN)}{USER}@{HOSTNAME}{c(C_RESET)}:{c(C_BLUE)}{disp}{c(C_RESET)}{symbol} "

# ---------- ls helpers ----------
def mode_to_str(mode:int)->str:
    is_dir="d" if stat.S_ISDIR(mode) else "-"
    bits=[(stat.S_IRUSR,"r"),(stat.S_IWUSR,"w"),(stat.S_IXUSR,"x"),
          (stat.S_IRGRP,"r"),(stat.S_IWGRP,"w"),(stat.S_IXGRP,"x"),
          (stat.S_IROTH,"r"),(stat.S_IWOTH,"w"),(stat.S_IXOTH,"x")]
    return is_dir + "".join(ch if (mode & b) else "-" for b,ch in bits)

def human_size(n:int)->str:
    size=float(n)
    for u in ["B","K","M","G","T","P"]:
        if size<1024.0: return (f"{int(size)}{u}" if u=="B" else f"{size:.0f}{u}")
        size/=1024.0
    return f"{int(size)}E"

def print_ls_entry(p:Path,long=False,human=False):
    name=p.name+("/" if p.is_dir() else "")
    if long:
        try:
            st=p.stat(); perms=mode_to_str(st.st_mode); nlink=1; meta=meta_get(p)
            owner=meta.get("owner",USER); group=meta.get("group",USER)
            size_str=f"{human_size(st.st_size):>6}" if human else f"{st.st_size:>6}"
            mtime=datetime.fromtimestamp(st.st_mtime).strftime("%b %d %H:%M")
            line=f"{perms} {nlink:>2} {owner:>8} {group:>8} {size_str} {mtime} {name}"
        except Exception:
            line=name
        print(f"{c(C_BLUE)}{line}{c(C_RESET)}" if p.is_dir() and USE_COLOR else line)
    else:
        print(f"{c(C_BLUE)}{name}{c(C_RESET)}" if p.is_dir() and USE_COLOR else name, end="  ")

def cmd_ls(cwd:Path,args:list):
    show_all=long=human=False; paths=[]
    for a in args:
        if a.startswith("-"):
            if "a" in a: show_all=True
            if "l" in a: long=True
            if "h" in a: human=True
        else: paths.append(a)
    targets=[cwd] if not paths else [resolve_path(cwd,p) for p in paths]
    multi=len(targets)>1
    for t in targets:
        if not t.exists(): print(f"ls: cannot access '{t}': No such file or directory"); continue
        if t.is_file():
            print_ls_entry(t,long,human)
            if not long: print()
            continue
        if multi:
            rel=t.relative_to(SYSTEM_ROOT) if t!=SYSTEM_ROOT else Path("/")
            print(f"{rel}:")
        try:
            entries=sorted(t.iterdir(), key=lambda x:x.name.lower()); out=0
            for e in entries:
                if not show_all and e.name.startswith("."): continue
                print_ls_entry(e,long,human); out+=1
            if not long and out: print()
        except PermissionError: print("ls: permission denied")

# ---------- core commands ----------
def cmd_rm(cwd:Path,args:list):
    force=recursive=False; targets=[]
    for a in args:
        if a.startswith("-"):
            if "f" in a: force=True
            if "r" in a: recursive=True
        else: targets.append(a)
    for t in targets:
        p=resolve_path(cwd,t)
        if not p.exists():
            if not force: print(f"rm: cannot remove '{t}': No such file or directory")
            continue
        try:
            if p.is_dir():
                if recursive: shutil.rmtree(p, ignore_errors=force)
                else: p.rmdir()
            else: p.unlink(missing_ok=True)
            k=meta_key(p)
            if k in META: del META[k]; json_save(META_FILE,META)
        except Exception as e:
            if not force: print(f"rm: cannot remove '{t}': {e}")

def cmd_cp(cwd:Path,args:list):
    recursive=False; rest=[]
    for a in args:
        if a.startswith("-"):
            if "r" in a: recursive=True
        else: rest.append(a)
    if len(rest)<2: print("Usage: cp [-r] <src>... <dst>"); return
    *srcs,dst=rest; dst_p=resolve_path(cwd,dst)
    try:
        if len(srcs)>1:
            dst_p.mkdir(parents=True, exist_ok=True)
            for s in srcs:
                sp=resolve_path(cwd,s)
                if not sp.exists(): print(f"cp: cannot stat '{s}': No such file"); continue
                if sp.is_dir():
                    if not recursive: print(f"cp: -r not specified; omitting directory '{s}'"); continue
                    shutil.copytree(sp,dst_p/sp.name,dirs_exist_ok=True)
                else: shutil.copy2(sp,dst_p/sp.name)
        else:
            sp=resolve_path(cwd,srcs[0])
            if not sp.exists(): print(f"cp: cannot stat '{srcs[0]}': No such file"); return
            if sp.is_dir():
                if not recursive: print(f"cp: -r not specified; omitting directory '{srcs[0]}'"); return
                shutil.copytree(sp,dst_p,dirs_exist_ok=True)
            else:
                dst_p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(sp,dst_p)
    except Exception as e: print(f"cp: {e}")

def cmd_mv(cwd:Path,args:list):
    if len(args)<2: print("Usage: mv <src>... <dst>"); return
    *srcs,dst=args; dst_p=resolve_path(cwd,dst)
    try:
        if len(srcs)>1:
            dst_p.mkdir(parents=True, exist_ok=True)
            for s in srcs:
                sp=resolve_path(cwd,s)
                if not sp.exists(): print(f"mv: cannot stat '{s}': No such file"); continue
                sp.rename(dst_p/sp.name)
        else:
            sp=resolve_path(cwd,srcs[0])
            if not sp.exists(): print(f"mv: cannot stat '{srcs[0]}': No such file"); return
            dst_p.parent.mkdir(parents=True, exist_ok=True)
            sp.rename(dst_p)
    except Exception as e: print(f"mv: {e}")

def cmd_echo(cwd:Path,args:list):
    if not args: print(); return
    if ">>" in args or ">" in args:
        op=max((i for i,a in enumerate(args) if a in (">",">>")), default=-1)
        if op==-1 or op==len(args)-1: print("shell: redirection parse error"); return
        text=" ".join(args[:op]); fname=args[op+1]
        fpath=resolve_path(cwd,fname); fpath.parent.mkdir(parents=True, exist_ok=True)
        mode="a" if args[op]==">>" else "w"
        with open(fpath,mode,encoding="utf-8") as f: f.write(text+("\n" if not text.endswith("\n") else ""))
    else: print(" ".join(args))

def cmd_grep(cwd:Path,args:list):
    ignore=rec=False; rest=[]
    for a in args:
        if a.startswith("-"):
            if "i" in a: ignore=True
            if "r" in a: rec=True
        else: rest.append(a)
    if not rest: print("Usage: grep [options] PATTERN [FILE...]"); return
    pattern,*files=rest
    def match(s:str)->bool: return (pattern.lower() in s.lower()) if ignore else (pattern in s)
    results=[]
    try:
        if rec:
            roots=[resolve_path(cwd,f) for f in files] if files else [cwd]
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
                p=resolve_path(cwd,f)
                if p.exists() and p.is_file():
                    try:
                        for i,line in enumerate(p.read_text(errors="ignore").splitlines(),1):
                            if match(line): results.append(f"{p.relative_to(SYSTEM_ROOT)}:{i}:{line}")
                    except Exception: pass
                else: print(f"grep: {f}: No such file or directory")
        if results: print("\n".join(results))
    except Exception as e: print(f"grep: {e}")

def cmd_chmod(cwd:Path,args:list):
    if len(args)<2: print("Usage: chmod MODE FILE..."); return
    mode=args[0]
    for n in args[1:]:
        p=resolve_path(cwd,n)
        if not p.exists(): print(f"chmod: cannot access '{n}': No such file or directory"); continue
        meta_set(p,mode=mode)

def cmd_chown(cwd:Path,args:list):
    if len(args)<2: print("Usage: chown OWNER[:GROUP] FILE..."); return
    og=args[0]; owner,group=(og.split(":",1)+[og])[:2] if ":" in og else (og,og)
    for n in args[1:]:
        p=resolve_path(cwd,n)
        if not p.exists(): print(f"chown: cannot access '{n}': No such file or directory"); continue
        meta_set(p,owner=owner,group=group)

def cmd_rmdir(cwd:Path,args:list):
    if not args: print("Usage: rmdir DIR..."); return
    for a in args:
        p=resolve_path(cwd,a)
        try: p.rmdir()
        except Exception as e: print(f"rmdir: failed to remove '{a}': {e}")

def cmd_which(args:list):
    if not args: print("which: missing operand"); return
    for n in args:
        path=shutil.which(n); print(path if path else f"{n} not found")

# ---------- APT/DPKG (simulation) ----------
def http_download(url:str)->bytes:
    with urllib.request.urlopen(url) as resp: return resp.read()

def ar_list_members(data:bytes):
    assert data[:8]==b"!<arch>\n","Not an ar archive"
    i=8
    while i+60<=len(data):
        header=data[i:i+60]; i+=60
        name=header[:16].decode("utf-8","ignore").strip()
        size=int(header[48:58].decode().strip())
        body=data[i:i+size]; i+=size
        if i%2==1: i+=1
        yield name,body

def deb_extract_data_tar(deb_bytes:bytes):
    data_member=None
    for name,body in ar_list_members(deb_bytes):
        if name.startswith("data.tar"): data_member=(name,body); break
    if not data_member: raise ValueError("No data.tar.* in .deb")
    name,body=data_member
    if name.endswith(".xz"):   tarf=tarfile.open(fileobj=BytesIO(lzma.decompress(body)),mode="r:")
    elif name.endswith(".gz"): tarf=tarfile.open(fileobj=BytesIO(gzip.decompress(body)),mode="r:")
    else:                      tarf=tarfile.open(fileobj=BytesIO(body),mode="r:")
    return tarf

def dpkg_install_deb(cwd:Path, deb_path:Path, pkg_name_hint:str=None):
    data=deb_path.read_bytes(); tarf=deb_extract_data_tar(data); installed=[]
    try:
        for m in tarf.getmembers():
            if not (m.isfile() or m.isdir()): continue
            rel=Path(m.name.lstrip("./")); dest=(SYSTEM_ROOT/rel).resolve()
            if SYSTEM_ROOT not in dest.parents and dest!=SYSTEM_ROOT: continue
            if m.isdir(): dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tarf.extractfile(m) as src, open(dest,"wb") as out: shutil.copyfileobj(src,out)
            installed.append(str(rel.as_posix()))
    finally: tarf.close()
    pkg_name=pkg_name_hint or deb_path.stem.split("_")[0]
    PKG_DB["installed"].setdefault(pkg_name,{"files":[]}); PKG_DB["installed"][pkg_name]["files"].extend(installed)
    pkg_db_save()
    print(f"Selecting previously unselected package {pkg_name}.")
    print(f"({deb_path.name}) unpacked.")
    print(f"{pkg_name} installed (simulated).")
    print("⚠️  Note: native Linux binaries from .deb do not run on Windows. Scripts/resources do.")

def apt_install(cwd:Path,pkg_name:str):
    reg=json_load(APT_REGISTRY,{"packages":{}}).get("packages",{})
    meta=reg.get(pkg_name)
    if not meta or "url" not in meta:
        print(f"E: Unable to locate package {pkg_name}")
        print(f"Tip: add the package + .deb URL in {APT_REGISTRY}")
        return
    url=meta["url"]; print(f"Downloading {url} ...")
    try: deb_bytes=http_download(url)
    except Exception as e: print(f"E: download error: {e}"); return
    tmp=SYSTEM_ROOT/"var"/"tmp"/f"{pkg_name}.deb"; tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(deb_bytes); dpkg_install_deb(cwd,tmp,pkg_name)

def dpkg_remove(pkg:str):
    rec=PKG_DB["installed"].get(pkg)
    if not rec: print(f"dpkg: warning: {pkg} is not installed"); return
    files=rec.get("files",[])
    for rel in sorted(files, key=lambda x: len(x.split("/")), reverse=True):
        p=(SYSTEM_ROOT/rel).resolve()
        try:
            if p.is_file(): p.unlink(missing_ok=True)
            elif p.is_dir():
                try: p.rmdir()
                except OSError: pass
        except Exception: pass
    del PKG_DB["installed"][pkg]; pkg_db_save(); print(f"Removed {pkg} (simulated).")

# ---------- Helpers Bash/MSYS ----------
ALIASES = {
    "pip3":"pip",
    "python3":"python",
    "apt-get":"apt",
    # Typo-fixes
    "ifcofig":"ifconfig",
    "ap-get":"apt",
    "aptget":"apt",
    "gti":"git",
}

def _path_to_msys(p: Path) -> str:
    p = p.resolve()
    s = str(p).replace("\\", "/")
    if len(s)>=2 and s[1]==":":
        drive=s[0].lower(); rest=s[2:]
        rest=rest[1:] if rest.startswith("/") else rest
        return f"/{drive}/{rest}"
    return s

def _bash_quote(s: str) -> str: return "'" + s.replace("'", "'\"'\"'") + "'"

def find_bash()->str|None:
    b=find_portable_git_bash_in(GIT_HOME)
    if b: return b
    p=shutil.which("bash")
    if p: return p
    for c in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe",
              r"C:\Program Files (x86)\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\usr\bin\bash.exe"):
        if os.path.exists(c): return c
    for env_name in ("BASH_HOME","GIT_HOME"):
        base=os.getenv(env_name)
        if base:
            for sub in ("bin\\bash.exe","usr\\bin\\bash.exe","bash.exe"):
                test=os.path.join(base,sub)
                if os.path.exists(test): return test
    return None

def run_in_bash(full_line: str, cwd: Path):
    if AUTO_DOWNLOAD_TOOLS and not find_portable_git_bash_in(GIT_HOME):
        ensure_portable_git_via_drive_pretty()
    bash = find_bash()
    if not bash:
        print("bash: not found (PortableGit ZIP nodig via Google Drive)."); return
    msys_cwd = _path_to_msys(cwd)
    msys_shim = _path_to_msys(SYSTEM_ROOT/"usr"/"bin")
    bash_cmd = f"export PATH={_bash_quote(msys_shim)}:\"$PATH\"; cd {_bash_quote(msys_cwd)} && {full_line}"
    env = os.environ.copy()
    # Verwijder WindowsApps ruis (breekt soms python)
    env["PATH"] = os.pathsep.join([p for p in env.get("PATH","").split(os.pathsep) if "WindowsApps" not in p])
    try:
        subprocess.Popen([bash, "-lc", bash_cmd], cwd=str(cwd), env=env).wait()
    except Exception as e:
        print(f"bash passthrough error: {e}")

def list_all_bash_commands() -> list[str]:
    if AUTO_DOWNLOAD_TOOLS and not find_portable_git_bash_in(GIT_HOME):
        ensure_portable_git_via_drive_pretty()
    bash = find_bash()
    if not bash: return []
    try:
        msys_shim = _path_to_msys(SYSTEM_ROOT/"usr"/"bin")
        out = subprocess.check_output(
            [bash, "-lc", f"export PATH={_bash_quote(msys_shim)}:\"$PATH\"; compgen -c | sort -u"],
            text=True, stderr=subprocess.DEVNULL
        )
        return [line.strip() for line in out.splitlines() if line.strip()]
    except Exception:
        return []

# ---------- Windows-equivalent wrappers ----------
def cmd_ip(args:list, cwd:Path):
    sub = args[0] if args else "a"
    if sub in ("a","addr","address"):  subprocess.Popen(["ipconfig","/all"], cwd=str(cwd)).wait()
    elif sub in ("r","route"):         subprocess.Popen(["route","PRINT"], cwd=str(cwd)).wait()
    elif sub in ("link",):             subprocess.Popen(["netsh","interface","show","interface"], cwd=str(cwd)).wait()
    else: print("ip: subcommand niet ondersteund in Windows-simulatie (gebruik: ip a|r|link)")

def cmd_systemctl(args:list, cwd:Path):
    if not args:
        print("systemctl: status <naam> | start|stop <naam> | list-units  (Windows-simulatie)"); return
    sub = args[0]; rest=args[1:]
    if sub == "status" and rest: subprocess.Popen(["powershell","-NoProfile","-Command", f"Get-Service -Name {rest[0]} | Format-List *"], cwd=str(cwd)).wait()
    elif sub in ("start","stop") and rest: subprocess.Popen(["sc", sub, rest[0]], cwd=str(cwd)).wait()
    elif sub == "list-units": subprocess.Popen(["powershell","-NoProfile","-Command","Get-Service | Sort-Object Status,Name | Format-Table -Auto"], cwd=str(cwd)).wait()
    else: print(f"systemctl: '{sub}' niet ondersteund op Windows (geen systemd).")

def cmd_mount(args:list, cwd:Path):
    subprocess.Popen(["powershell","-NoProfile","-Command",
                      "Get-Volume | Select DriveLetter,FileSystemLabel,FileSystem,Size,SizeRemaining | Format-Table -Auto"],
                     cwd=str(cwd)).wait()

# ---------- SHIMS ----------
SHIM_SCRIPTS = {
    "ifconfig": """#!/usr/bin/env bash
# compat: ifconfig → ip/Win ipconfig
if command -v ip >/dev/null 2>&1; then
  exec ip addr show "$@"
else
  exec /c/Windows/System32/ipconfig.exe "$@"
fi
""",
    "netstat": """#!/usr/bin/env bash
# compat: netstat → ss/netstat.exe
if command -v ss >/dev/null 2>&1; then
  exec ss "$@"
else
  exec /c/Windows/System32/netstat.exe "$@"
fi
""",
    "route": """#!/usr/bin/env bash
# compat: route → route.exe
if [ $# -eq 0 ]; then
  exec /c/Windows/System32/route.exe PRINT
else
  exec /c/Windows/System32/route.exe "$@"
fi
""",
    "service": """#!/usr/bin/env bash
# compat: service → sc / Get-Service
cmd="$1"; svc="$2"
case "$cmd" in
  status) exec powershell -NoProfile -Command "Get-Service -Name $svc | Format-List *" ;;
  start|stop) exec /c/Windows/System32/sc.exe "$cmd" "$svc" ;;
  restart) /c/Windows/System32/sc.exe stop "$svc"; exec /c/Windows/System32/sc.exe start "$svc" ;;
  *) echo "usage: service <status|start|stop|restart> <name>"; exit 1 ;;
esac
""",
    "lsb_release": """#!/usr/bin/env bash
# compat: fake distro details
uname_s=$(uname -s); uname_r=$(uname -r)
echo -e "Distributor ID:\\tMSYS"
echo -e "Description:\\t${uname_s} (Git Bash/MSYS2 on Windows)"
echo -e "Release:\\t${uname_r}"
echo -e "Codename:\\twin"
"""
}

def ensure_shims():
    shim_dir = SYSTEM_ROOT/"usr"/"bin"; shim_dir.mkdir(parents=True, exist_ok=True)
    for name, content in SHIM_SCRIPTS.items():
        p = shim_dir / name
        try:
            p.write_text(content, encoding="utf-8"); os.chmod(p, 0o755)
        except Exception as e:
            print(f"Kon shim {name} niet schrijven: {e}")

def ensure_python3_shim():
    """Zet een 'python3' shim in /usr/bin voor gebruik binnen Bash."""
    pyexe = sys.executable or shutil.which("python3") or shutil.which("python")
    if not pyexe: return
    shim_dir = SYSTEM_ROOT/"usr"/"bin"; shim_dir.mkdir(parents=True, exist_ok=True)
    p = shim_dir/"python3"
    try:
        p.write_text('#!/usr/bin/env bash\n' + f'"{_path_to_msys(Path(pyexe))}" "$@"\n', encoding="utf-8")
        os.chmod(p, 0o755)
    except Exception as e:
        print(f"Kon python3 shim niet schrijven: {e}")

# ===================== HELP DATA =====================
COMMAND_SECTIONS = [
    ("Ontdek wat je hebt", [
        ("help", "overzicht; help <cmd>/<sectie>/<all-commands>"),
        ("type -a", "toon hoe Bash een commando zou uitvoeren"),
        ("whatis", "éénregelige man-pagina beschrijving"),
        ("apropos", "zoek in man-pagina’s"),
        ("man", "handleiding van een commando"),
        ("compgen -c", "alle commando’s in Bash"),
        ("alias", "lijst/maak aliassen"),
        ("which", "pad naar commando"),
        ("whereis", "zoek binaire/man/src"),
        ("busybox --list", "alle applets (indien aanwezig)"),
    ]),
    ("Navigatie & bestanden", [
        ("pwd","toon huidige directory"),
        ("ls","lijst bestanden (-alh)"),
        ("cd","wissel map"),
        ("tree","boomstructuur"),
        ("mkdir","maak map(pen)"),
        ("rmdir","verwijder lege map"),
        ("touch","maak/raakte bestand"),
        ("cp","kopieer (-r)"),
        ("mv","verplaats/hernoem"),
        ("rm","verwijder (-rf)"),
        ("ln","hardlink maken"),
        ("ln -s","symlink maken"),
        ("stat","bestandstatistieken"),
        ("file","type herleiden"),
        ("du","schijfruimte per pad"),
        ("df","schijfruimte volumes"),
    ]),
    ("Inhoud bekijken", [
        ("cat","concateneer/print"),
        ("tac","achterstevoren print"),
        ("nl","regelnummering"),
        ("less","pager"),
        ("more","pager (eenvoudig)"),
        ("head","eerste N regels"),
        ("tail","laatste N regels; -f volgen"),
        ("wc","tel regels/woorden/bytes"),
    ]),
    ("Zoeken", [
        ("grep","zoek in tekst (-irnE)"),
        ("egrep","grep -E (compat)"),
        ("find","zoek bestanden/filters"),
        ("locate","zoek via database"),
        ("updatedb","update locate-db"),
        ("xargs","bouw commandoreeksen"),
    ]),
    ("Tekstbewerking & data", [
        ("sed","stream editor"),
        ("awk","text processing"),
        ("cut","kolommen afknippen"),
        ("tr","translate/delete chars"),
        ("sort","sorteer"),
        ("uniq","unieke regels"),
        ("paste","kolommen plakken"),
        ("join","join op sleutel"),
        ("split","files opsplitsen"),
        ("csplit","splits met patroon"),
        ("fmt","hard wrap"),
        ("pr","print/kolommen"),
        ("expand","tabs → spaties"),
        ("unexpand","spaties → tabs"),
        ("diff","verschillen (unified)"),
        ("sdiff","side-by-side diff"),
        ("cmp","bytevergelijk"),
        ("comm","compare gesorteerd"),
        ("patch","pas diff toe"),
        ("iconv","tekenset conversie"),
        ("dos2unix","CRLF → LF"),
        ("unix2dos","LF → CRLF"),
    ]),
    ("Archieven & compressie", [
        ("tar","archiveren/uitpakken"),
        ("gzip","compressie"),
        ("gunzip","decompressie"),
        ("bzip2","compressie"),
        ("bunzip2","decompressie"),
        ("xz","compressie"),
        ("unxz","decompressie"),
        ("zstd","Zstandard"),
        ("zip","zip archief"),
        ("unzip","unzip"),
        ("7z","7-Zip/p7zip"),
    ]),
    ("Rechten & eigendom", [
        ("chmod","mode (symbolisch/oktaal)"),
        ("chown","eigenaar wijzigen"),
        ("chgrp","groep wijzigen"),
        ("umask","standaardrechten"),
        ("getfacl","ACL tonen"),
        ("setfacl","ACL zetten"),
    ]),
    ("Processen, jobs & signalen", [
        ("ps","proceslijst"),
        ("top","interactieve processen"),
        ("pgrep","pid op naam"),
        ("pkill","kill op naam"),
        ("kill","stuur signaal"),
        ("killall","kill alle met naam"),
        ("jobs","background jobs"),
        ("bg","job naar achtergrond"),
        ("fg","job naar voorgrond"),
        ("disown","loskoppelen"),
        ("nohup","doordraaien &"),
        ("setsid","nieuw sessie"),
        ("nice","prioriteit starten"),
        ("renice","prioriteit wijzigen"),
        ("time","tijd meet"),
        ("strace","syscall trace"),
        ("ltrace","lib call trace"),
    ]),
    ("Systeeminfo & hardware", [
        ("uname","kernel/OS info"),
        ("hostnamectl","hostname/os"),
        ("lsb_release","distributie-info"),
        ("uptime","sinds opstart"),
        ("date","datum/tijd"),
        ("free","geheugen"),
        ("vmstat","virtueel geheugen"),
        ("lscpu","CPU details"),
        ("lsblk","block devices"),
        ("blkid","UUIDs/FS-typen"),
        ("lspci","PCI"),
        ("lsusb","USB"),
        ("dmidecode","hardware DMI"),
        ("dmesg","kernel messages"),
        ("journalctl","systemd logs"),
    ]),
    ("Pakketbeheer (families)", [
        ("apt","Deb/Ubuntu pakkettool"),
        ("dpkg","Debian package layer"),
        ("dnf","RHEL/Fedora pakkettool"),
        ("yum","RHEL (ouder)"),
        ("rpm","RPM package layer"),
        ("zypper","openSUSE"),
        ("pacman","Arch Linux"),
        ("snap","universeel"),
        ("flatpak","universeel"),
        ("conda","Python env/pkg"),
    ]),
    ("Services & boot", [
        ("systemctl","systemd services"),
        ("service","SysV compat"),
        ("journalctl -u","servicelog"),
    ]),
    ("Gebruikers & groepen", [
        ("id","UID/GIDs"),
        ("who","wie ingelogd"),
        ("w","wie + load"),
        ("last","login history"),
        ("useradd","gebruiker toevoegen"),
        ("passwd","wachtwoord set"),
        ("usermod","user wijzigen"),
        ("userdel","user verwijderen"),
        ("groupadd","groep toevoegen"),
        ("gpasswd","group passwd"),
        ("su","switch user"),
        ("sudo","as root uitvoeren"),
        ("visudo","sudoers veilig"),
    ]),
    ("Netwerk", [
        ("ip","adres/route/link"),
        ("ifconfig","compat: interfaces"),
        ("ip link","link-status"),
        ("ss","sockets (modern)"),
        ("netstat","sockets (klassiek)"),
        ("ping","reachability"),
        ("traceroute","pad naar host"),
        ("tracepath","alt traceroute"),
        ("dig","DNS query"),
        ("host","DNS lookup"),
        ("nslookup","DNS klassiek"),
        ("curl","HTTP(S)/FTP client"),
        ("wget","downloader"),
        ("ftp","FTP client"),
        ("lftp","geavanceerde FTP"),
        ("nft","nftables"),
        ("iptables","oude firewall"),
        ("ufw","simpel firewall"),
    ]),
    ("SSH & remote", [
        ("ssh","remote shell"),
        ("ssh-keygen","sleutelpaar"),
        ("ssh-agent","agent"),
        ("ssh-add","sleutel in agent"),
        ("scp","secure copy"),
        ("sftp","FTP over SSH"),
        ("rsync","sync/backup"),
    ]),
    ("Schijven & filesystems", [
        ("fdisk","partities MBR"),
        ("parted","partities GPT"),
        ("mkfs.*","filesystem maken"),
        ("fsck.*","fs check"),
        ("tune2fs","ext tweaks"),
        ("mount","mount filesystem"),
        ("umount","unmount"),
        ("/etc/fstab","fstab (config)"),
    ]),
    ("Tijd, locale & planning", [
        ("date","toon/zet tijd"),
        ("timedatectl","tijdzones/NTP"),
        ("hwclock","hardware clock"),
        ("crontab","periodieke jobs"),
        ("systemctl list-timers","timers"),
        ("at","eenmalige job"),
    ]),
    ("Monitoring & performance", [
        ("top","CPU/mem processen"),
        ("htop","mooie top"),
        ("iostat","disk IO stats"),
        ("iotop","IO per proces"),
        ("dstat","combinatiestat"),
        ("nmon","monitor alles"),
        ("iftop","netwerk per host"),
        ("nload","netwerksnelheid"),
        ("free","geheugen"),
        ("vmstat","virtueel geheugen"),
        ("sar","historische stats"),
    ]),
    ("Beveiliging & crypto", [
        ("sudo","privilege escalation"),
        ("passwd","wachtwoord"),
        ("openssl","certs/hashes"),
        ("gpg","PGP crypto"),
        ("chroot","root wisselen"),
    ]),
    ("Containers & virtualisatie", [
        ("docker","containers"),
        ("docker compose","multi-container"),
        ("podman","rootless containers"),
        ("kubectl","Kubernetes"),
        ("qemu-system-*","virt machines"),
        ("vboxmanage","VirtualBox"),
    ]),
    ("Ontwikkeltools", [
        ("gcc/g++/clang","compilers"),
        ("make/cmake/ninja","buildsystemen"),
        ("pkg-config","pkg metadata"),
        ("gdb","debugger"),
        ("valgrind","mem/profiler"),
        ("objdump/readelf","binaire inspectie"),
        ("ldd","lib afhankelijkheden"),
        ("git","versiebeheer"),
        ("python3/pip","scripting"),
        ("node/npm/yarn","js toolchain"),
        ("shellcheck","bash lint"),
    ]),
]

COMMAND_DOCS = {
    "ls": {
        "desc": "Lijst directory-inhoud.",
        "usage": "ls [-a] [-l] [-h] [PAD...]",
        "opts": [
            "-a  → toon verborgen bestanden",
            "-l  → long listing (rechten, eigenaar, grootte, datum)",
            "-h  → human-readable groottes (met -l)",
        ],
        "examples": ["ls -alh", "ls /etc /var/log"]
    },
    "cd": {
        "desc": "Wissel van werkdirectory.",
        "usage": "cd [PAD]",
        "opts": ["zonder argument → $HOME", "-  → vorige directory"],
        "examples": ["cd ~/project", "cd -"]
    },
    "grep": {
        "desc": "Zoek regels in bestanden die overeenkomen met een patroon.",
        "usage": "grep [-i] [-r] [-n] [-E] PATROON [FILE...]",
        "opts": [
            "-i  → case-insensitive",
            "-r  → recursief door mappen",
            "-n  → toon regelnummers",
            "-E  → uitgebreid regex (egrep)"
        ],
        "examples": ["grep -rin 'ERROR' .", "grep -E 'foo|bar' file.txt"]
    },
    "tar": {
        "desc": "Maak of pak archieven uit.",
        "usage": "tar -xf ARCHIEF | tar -czf ARCHIEF.tar.gz PAD...",
        "opts": ["-x  → extract","-c  → create","-z  → gzip","-J  → xz","-f  → archiefbestand"],
        "examples": ["tar -xzf pkg.tar.gz", "tar -czf backup.tgz ~/project"]
    },
    "chmod": {"desc":"Zet rechten (octaal of symbolisch).","usage":"chmod MODE BESTAND...","opts":["u/g/o + r/w/x"],"examples":["chmod 755 script.sh"]},
    "chown": {"desc":"Wijzig eigenaar en (optioneel) groep.","usage":"chown EIGENAAR[:GROEP] BESTAND...","opts":[],"examples":["chown root:root /etc/file"]},
    "ip": {
        "desc":"Netwerkbeheer (adressen, routes, links).",
        "usage":"ip a|addr|r|route|link ...",
        "opts":["ip a  → interfaces/adressen","ip r  → routing table","ip link  → link status"],
        "examples":["ip a","ip r"]
    },
    "ifconfig": {
        "desc":"Klassieke interface-weergave (compat shim).",
        "usage":"ifconfig",
        "opts":["Shim vertaalt naar 'ip addr' of 'ipconfig.exe' (Windows)"],
        "examples":["ifconfig"]
    },
    "systemctl": {
        "desc":"Beheer systemd-services (Windows: shim naar sc/Get-Service).",
        "usage":"systemctl status|start|stop <service>",
        "opts":["status / start / stop / list-units"],
        "examples":["systemctl status ssh","systemctl list-units --type=service"]
    },
    "curl": {"desc":"Client voor HTTP(S)/FTP.","usage":"curl [opties] URL","opts":["-L (redirects)","-I (HEAD)","-O (bestandsnaam)"],"examples":["curl -LO https://example.com/file.tgz"]},
    "ssh":  {"desc":"Remote shell via SSH.","usage":"ssh [-J jumphost] [-L local:host:port] user@host","opts":["-J ProxyJump","-L/-R portforward"],"examples":["ssh -J bastion user@db"]},
    "zip":  {"desc":"Maak ZIP-archief.","usage":"zip -r archief.zip PAD/","opts":["-r recursief","-9 max compressie"],"examples":["zip -r site.zip ./dist"]},
    "unzip":{"desc":"Pak ZIP uit.","usage":"unzip archief.zip -d doel/","opts":[],"examples":["unzip tools.zip -d /usr/local/"]},
    "zstd": {"desc":"Zstandard compressor.","usage":"zstd [-T0] FILE","opts":["-T0 → alle cores"],"examples":["zstd -T0 bigfile"]},
}

# -------- HELP RENDERING --------
def bash_has(cmd: str) -> bool:
    bash = find_bash()
    if not bash: return False
    try:
        msys_shim = _path_to_msys(SYSTEM_ROOT/"usr"/"bin")
        out = subprocess.run(
            [bash, "-lc", f"export PATH={_bash_quote(msys_shim)}:\"$PATH\"; command -v {shlex.quote(cmd)} >/dev/null 2>&1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return out.returncode == 0
    except Exception:
        return False

def status_icon(cmd: str) -> str:
    if cmd in SHIM_SCRIPTS: return "≈"
    if bash_has(cmd) or shutil.which(cmd): return "✓"
    return "·"

def _pad_cols(rows, gap=2):
    if not rows: return []
    widths=[0]*max(len(r) for r in rows)
    for r in rows:
        for i,cell in enumerate(r):
            widths[i]=max(widths[i], len(cell))
    out=[]
    for r in rows:
        line=""
        for i,cell in enumerate(r):
            pad = widths[i]-len(cell)
            line += cell + (" "*(pad+(gap if i<len(r)-1 else 0)))
        out.append(line.rstrip())
    return out

def render_section(title: str, items: list[tuple[str,str]]):
    print(f"{c(C_CYAN)}— {title} —{c(C_RESET)}")
    rows=[]
    for name, desc in items:
        icon = status_icon(name.split()[0])
        rows.append([f"{icon} {name}", desc])
    for line in _pad_cols(rows): print(line)
    print()

def render_help_overview():
    root=str(SYSTEM_ROOT).replace("\\","/"); reg=str(APT_REGISTRY).replace("\\","/")
    print(f"{BRAND} {VERSION} — commands overview")
    print(f"Root: {root}")
    print(f"Registry: {reg}\n")
    print(f"Tip: gebruik {c(C_GREEN)}help <command>{c(C_RESET)} voor details, {c(C_GREEN)}help <categorie>{c(C_RESET)} voor een sectie, of {c(C_GREEN)}help all-commands{c(C_RESET)} voor alles.\n")

def render_all_commands():
    render_help_overview()
    for title, items in COMMAND_SECTIONS:
        render_section(title, items)
    documented = {n.split()[0] for _,items in COMMAND_SECTIONS for (n,_) in items}
    documented |= set(SHIM_SCRIPTS.keys())
    available = set(list_all_bash_commands())
    extra = sorted([x for x in available if x not in documented])
    if extra:
        print(f"{c(C_CYAN)}— Extra (gedetecteerd via Bash) —{c(C_RESET)}")
        cols=4; grid=[extra[i:i+cols] for i in range(0,len(extra),cols)]
        rows=[[f"✓ {x}" for x in row] for row in grid]
        for line in _pad_cols(rows): print(line)
        print()

def render_category(cat: str):
    cat_l = cat.strip().lower()
    for title, items in COMMAND_SECTIONS:
        if cat_l in (title.lower(), title.lower().split(" (")[0]):
            render_section(title, items); return True
    return False

def render_command_card(cmd: str):
    name = cmd.strip()
    doc = COMMAND_DOCS.get(name) or COMMAND_DOCS.get(name.split()[0])
    icon = status_icon(name.split()[0])
    print(f"{c(C_CYAN)}{name}{c(C_RESET)}  [{icon} {'shim' if name in SHIM_SCRIPTS else 'beschikbaar' if icon=='✓' else 'niet gevonden'}]\n")
    if not doc:
        print("Beschrijving : Geen specifieke kaart — probeer 'man' of '--help'.")
        print(f"Gebruik       : {name} [--help]")
        print("Opties        : —")
        print("Voorbeelden   :")
        print(f"  {name} --help\n")
        return
    print(f"Beschrijving : {doc.get('desc','—')}")
    print(f"Gebruik       : {doc.get('usage', name+' [--help]')}")
    opts = doc.get("opts", [])
    print("Opties        : " + (opts[0] if opts else "—"))
    for o in opts[1:]:
        print(" " * 15 + o)
    ex = doc.get("examples", [])
    print("Voorbeelden   : " + (ex[0] if ex else "—"))
    for e in ex[1:]:
        print(" " * 15 + e)
    print()

def cmd_help(args:list):
    if not args:
        render_help_overview()
        # Compacte start: 3 populaire secties tonen
        for title in ["Navigatie & bestanden", "Zoeken", "Netwerk"]:
            for t, items in COMMAND_SECTIONS:
                if t == title: render_section(t, items); break
        print(f"Statusiconen: ✓ gevonden  · niet gevonden  ≈ shim")
        return
    key = " ".join(args).strip()
    if key in ("all","all-commands","lijst","list","--full"):
        render_all_commands(); print(f"Statusiconen: ✓ gevonden  · niet gevonden  ≈ shim"); return
    if render_category(key): return
    render_command_card(key)

# ---------- MSYS add-ons (optioneel) ----------
def install_msys_addons_pretty() -> bool:
    if not MSYS_ADDONS_URL: return False
    zip_path = download_git_zip_via_your_snippet(MSYS_ADDONS_URL, "MSYS2-packages-master.zip")
    if not zip_path: return False
    # Uitpakken in PortableGit-root zodat usr/bin, mingw64/bin etc. worden samengevoegd
    ok = unzip_with_progress(zip_path, GIT_HOME, "MSYS2-packages-master.zip")
    return ok

# ---------- dispatcher ----------
def run_command(line:str, cwd:Path, git_env_cache:dict|None=None)->tuple[Path,dict|None]:
    if not line.strip(): return cwd, git_env_cache
    tokens=shlex.split(line)
    cmd,*args=tokens

    # Aliassen/typos
    cmd = ALIASES.get(cmd, cmd)

    # built-ins
    if cmd=="exit": print("Bye!"); sys.exit(0)
    elif cmd=="pwd":
        rel=cwd.relative_to(SYSTEM_ROOT); print("/" if not rel.parts else "/" + "/".join(rel.parts)); return cwd, git_env_cache
    elif cmd=="ls": cmd_ls(cwd,args); return cwd, git_env_cache
    elif cmd=="cd":
        dest=resolve_path(cwd,args[0]) if args else (SYSTEM_ROOT/"home"/USER)
        if dest.exists() and dest.is_dir() and (SYSTEM_ROOT in dest.parents or dest==SYSTEM_ROOT): return dest, git_env_cache
        print(f"cd: {args[0] if args else ''}: No such directory"); return cwd, git_env_cache
    elif cmd=="mkdir":
        for a in args: resolve_path(cwd,a).mkdir(parents=True, exist_ok=True); return cwd, git_env_cache
    elif cmd=="rmdir": cmd_rmdir(cwd,args); return cwd, git_env_cache
    elif cmd=="touch":
        for a in args:
            p=resolve_path(cwd,a); p.parent.mkdir(parents=True, exist_ok=True); p.touch(exist_ok=True)
        return cwd, git_env_cache
    elif cmd=="cat":
        if not args: print("cat: missing file operand")
        else:
            for a in args:
                p=resolve_path(cwd,a)
                if p.exists() and p.is_file(): print(p.read_text(errors="ignore"), end="")
                else: print(f"cat: {a}: No such file")
        return cwd, git_env_cache
    elif cmd=="echo": cmd_echo(cwd,args); return cwd, git_env_cache
    elif cmd=="whoami": print(USER); return cwd, git_env_cache
    elif cmd=="clear": os.system("cls" if os.name=="nt" else "clear"); return cwd, git_env_cache
    elif cmd=="tree":
        for root,dirs,files in os.walk(cwd):
            rel=Path(root).relative_to(cwd); indent="  "*len(rel.parts)
            print(f"{indent}{Path(root).name}/")
            for f in files: print(f"{indent}  {f}")
        return cwd, git_env_cache
    elif cmd=="help": cmd_help(args); return cwd, git_env_cache
    elif cmd=="rm": cmd_rm(cwd,args); return cwd, git_env_cache
    elif cmd=="cp": cmd_cp(cwd,args); return cwd, git_env_cache
    elif cmd=="mv": cmd_mv(cwd,args); return cwd, git_env_cache
    elif cmd=="grep": cmd_grep(cwd,args); return cwd, git_env_cache
    elif cmd=="chmod": cmd_chmod(cwd,args); return cwd, git_env_cache
    elif cmd=="chown": cmd_chown(cwd,args); return cwd, git_env_cache
    elif cmd=="which": cmd_which(args); return cwd, git_env_cache
    elif cmd=="diag":
        if args and args[0]=="path":
            print("Windows PATH:"); print(os.environ.get("PATH",""))
            bash = find_bash()
            if bash:
                msys_shim = _path_to_msys(SYSTEM_ROOT/"usr"/"bin")
                out = subprocess.check_output([bash,"-lc",f"export PATH={_bash_quote(msys_shim)}:\"$PATH\"; printf '%s' \"$PATH\""], text=True)
                print("\nBash PATH:"); print(out)
        else:
            print("diag path  — toon Windows & Bash PATH")
        return cwd, git_env_cache

    # Linux wrappers
    if cmd=="ip": cmd_ip(args, cwd); return cwd, git_env_cache
    if cmd=="systemctl": cmd_systemctl(args, cwd); return cwd, git_env_cache
    if cmd=="mount": cmd_mount(args, cwd); return cwd, git_env_cache

    # sudo (sim)
    if cmd=="sudo":
        if not args: print("sudo: usage: sudo <command> [args...]"); return cwd, git_env_cache
        print("[sudo simulated] running:", " ".join(args))
        return run_command(" ".join(args), cwd, git_env_cache)

    # apt / apt-get (sim)
    if cmd in ("apt","apt-get"):
        if not args: print("Usage: apt install <pkg> | apt remove <pkg>"); return cwd, git_env_cache
        sub=args[0]; rest=args[1:]
        if sub in ("install","i"):
            if not rest: print("apt: missing package name"); return cwd, git_env_cache
            apt_install(cwd,rest[0]); return cwd, git_env_cache
        elif sub in ("remove","purge","r"):
            if not rest: print("apt: missing package name"); return cwd, git_env_cache
            dpkg_remove(rest[0]); return cwd, git_env_cache
        else:
            print("Supported: apt install <pkg>, apt remove <pkg> (simulation)"); return cwd, git_env_cache

    # dpkg (sim)
    if cmd=="dpkg":
        if not args: print("Usage: dpkg -i FILE.deb | dpkg -r <pkg>"); return cwd, git_env_cache
        if args[0]=="-i":
            if len(args)<2: print("dpkg: missing .deb filename"); return cwd, git_env_cache
            deb=resolve_path(cwd,args[1])
            if not deb.exists(): print(f"dpkg: {args[1]}: No such file"); return cwd, git_env_cache
            try: dpkg_install_deb(cwd,deb)
            except Exception as e: print(f"dpkg: error installing: {e}")
            return cwd, git_env_cache
        elif args[0]=="-r":
            if len(args)<2: print("dpkg: missing package name"); return cwd, git_env_cache
            dpkg_remove(args[1]); return cwd, git_env_cache
        else:
            print("Supported: dpkg -i FILE.deb, dpkg -r <pkg>  (simulation)"); return cwd, git_env_cache

    # --- path execution / scripts ---
    if "/" in cmd or "\\" in cmd or cmd.startswith("."):
        target=resolve_path(cwd,cmd)
        if target.exists() and target.is_file():
            host=str(target)
            # .sh → Git Bash
            if host.lower().endswith(".sh"):
                ensure_pip_deps()
                if AUTO_DOWNLOAD_TOOLS and not find_portable_git_bash_in(GIT_HOME):
                    ensure_portable_git_via_drive_pretty()
                bash=find_bash()
                if not bash:
                    print("bash: not found. Installeer PortableGit ZIP via Google Drive.")
                    return cwd, git_env_cache
                msys_script=_path_to_msys(Path(host))
                shim_dir=SYSTEM_ROOT/"usr"/"bin"; shim_dir.mkdir(parents=True, exist_ok=True)
                msys_shim=_path_to_msys(shim_dir)
                bash_cmd=f"export PATH={_bash_quote(msys_shim)}:\"$PATH\"; exec \"{msys_script}\""
                env=os.environ.copy()
                env["PATH"]=os.pathsep.join([p for p in env.get("PATH","").split(os.pathsep) if "WindowsApps" not in p])
                subprocess.Popen([bash,"-lc",bash_cmd], cwd=str(cwd), env=env).wait()
                return cwd, git_env_cache
            # .py
            if host.lower().endswith(".py"):
                ensure_pip_deps()
                pyexe=sys.executable or shutil.which("python") or shutil.which("python3")
                if pyexe: subprocess.Popen([pyexe,host], cwd=str(cwd)).wait()
                else: print("python: not found.")
                return cwd, git_env_cache
            # .bat/.cmd
            if host.lower().endswith((".bat",".cmd")):
                subprocess.Popen([host], cwd=str(cwd)).wait(); return cwd, git_env_cache
            # generic
            subprocess.Popen([host], cwd=str(cwd)).wait(); return cwd, git_env_cache
        else:
            print(f"{cmd}: No such file"); return cwd, git_env_cache

    # git (echt)
    if cmd=="git":
        if not find_git_exe() and AUTO_DOWNLOAD_TOOLS:
            ensure_portable_git_via_drive_pretty()
        git_exe=find_git_exe()
        if not git_exe:
            print("git: not found. Drive-ZIP installatie mislukt of niet publiek.")
            return cwd, git_env_cache
        if git_env_cache is None:
            git_env_cache=ensure_git_config_and_ssh(SYSTEM_ROOT/"home"/USER)
        env=os.environ.copy(); env.update(git_env_cache or {})
        subprocess.Popen([git_exe]+args, cwd=str(cwd), env=env).wait()
        return cwd, git_env_cache

    # === Bash passthrough voor ALLES ===
    if find_bash():
        run_in_bash(line, cwd)
        return cwd, git_env_cache

    # fallback host tools
    real_cmd=shutil.which(ALIASES.get(cmd,cmd))
    if real_cmd:
        ensure_pip_deps()
        subprocess.Popen([real_cmd]+args, cwd=str(cwd)).wait()
    else:
        print(f"{cmd}: command not found")
    return cwd, git_env_cache

# ---------- banners & clear ----------
def print_banner_initial():
    print(f"{c(C_CYAN)}{BRAND} {VERSION} – Initializing virtual Linux system...{c(C_RESET)}\n")

def print_banner_final():
    print(f"{c(C_CYAN)}{BRAND} {VERSION} – Virtual system mounted{c(C_RESET)}\n")

def do_clear():
    try:
        os.system("cls" if os.name=="nt" else "clear")
    except Exception:
        # fallback: veel nieuwe regels
        print("\n"*80)

# ---------- bootstrap real git ----------
def validate_git_available_or_exit():
    git_exe=find_git_exe()
    if not git_exe:
        zips=[p.name for p in CACHE_DIR.glob("*.zip")]
        print("Fout: kon geen echte Git installeren/vinden.")
        print("Diag:\n  Looked for git here:")
        print(f"    - system PATH: {shutil.which('git')}")
        print(f"    - {GIT_HOME/'cmd/git.exe'}")
        print(f"    - {GIT_HOME/'mingw64/bin/git.exe'}")
        print(f"    - {GIT_HOME/'mingw32/bin/git.exe'}")
        print("  ZIP(s) in cache:", zips)
        print("Tip: zorg dat je Drive-link publiek is en dat het bestand een PortableGit ZIP is.")
        sys.exit(1)
    # valideer zonder te printen
    try:
        subprocess.check_output([git_exe,"--version"],text=True,stderr=subprocess.STDOUT)
    except Exception as e:
        print(f"Fout bij het uitvoeren van Git: {e}"); sys.exit(1)

# ---------- main ----------
def main():
    migrate_from_krnl_if_needed()

    # Bepaal of dit de eerste run is VOOR we structuren forceren
    was_initialized = INIT_MARKER.exists()

    # Zorg ALTIJD voor mappenstructuur (idempotent)
    ensure_structure()

    first_boot = not was_initialized

    if first_boot:
        # Eerst de init-banner tonen (jouw wens)
        print_banner_initial()

    ensure_pip_deps()

    # Portable Git via Google Drive + nette progress
    installed_anything = False
    if AUTO_DOWNLOAD_TOOLS:
        if not find_git_exe():
            if ensure_portable_git_via_drive_pretty():
                installed_anything = True
        # Bash zal meegaan met PortableGit; geen aparte install

    # (optioneel) MSYS add-ons ZIP
    if MSYS_ADDONS_URL and find_git_exe():
        # alleen bij eerste keer of als usr/bin nog niet bestaat
        addons_indicator = GIT_HOME / "usr" / "bin"
        if first_boot or not addons_indicator.exists():
            ok = install_msys_addons_pretty()
            installed_anything = installed_anything or ok

    # shims
    ensure_shims()
    ensure_python3_shim()

    # Git/SSH env voorbereiden
    _ = ensure_git_config_and_ssh(SYSTEM_ROOT/"home"/USER)

    # valideer Git zonder spam
    validate_git_available_or_exit()

    # Als we iets hebben gedownload/uitgepakt of het is eerste boot → scherm "refresh"
    if first_boot or installed_anything:
        do_clear()

    # Altijd de definitieve banner tonen na (eventuele) refresh
    print_banner_final()

    cwd=SYSTEM_ROOT/"home"/USER; cwd.mkdir(parents=True, exist_ok=True)
    print(f"Mounted virtual system at {SYSTEM_ROOT}")
    print("Home directory:", cwd)
    print("Type 'help', 'help all-commands' of 'help <cmd>' voor details.\n")

    git_env_cache=None
    while True:
        try:
            line=input(prompt(cwd))
            cwd,git_env_cache=run_command(line,cwd,git_env_cache)
        except KeyboardInterrupt: print("^C")
        except EOFError: print(); break
        except Exception as e: print(f"Shell error: {e}")

if __name__=="__main__":
    main()
