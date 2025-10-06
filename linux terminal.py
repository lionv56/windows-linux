#!/usr/bin/env python3
# Linux Terminal v3.9-gdrive – Virtual Linux-like shell for Windows (LinuxFS root)
# - Behoudt ALLE functies (ls/cd/cp/mv/rm/grep/apt/dpkg/.sh/.py/.bat, metadata, migratie, help)
# - Portable Git installatie ALLEEN via Google Drive ZIP met JOUW gdown-snippet (géén GitHub fallback)
# - Na download: unzip naar LinuxFS\tools\git, fix 1-level dieper structuur, zoek git.exe
# - .sh draait via Git Bash (van portable Git), python3-shim wordt toegevoegd
# - Git + SSH config in virtuele HOME
# - APT/DPKG simulatie
# - Mooie help-output

import os, sys, shlex, stat, json, shutil, subprocess, tarfile, lzma, gzip, urllib.request, http.cookiejar, zipfile
from pathlib import Path
from datetime import datetime
from io import BytesIO

# ===================== CONFIG =====================
# JOUW Google Drive-bestand (ZIP van PortableGit) – dit is de URL uit je bericht
GDRIVE_FILE_URL = "https://drive.google.com/file/d/17NFMgGpHWQoRq7Z_MgrXdwSjB47VeBq0/view?usp=sharing"
AUTO_DOWNLOAD_TOOLS = True  # laat dit True om automatisch te downloaden als Git ontbreekt
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
GIT_HOME     = TOOLS_DIR / "git"                      # hier komt PortableGit
CACHE_DIR    = SYSTEM_ROOT / "var" / "cache" / "downloads"

BRAND, VERSION, HOSTNAME = "Linux Terminal", "v3.9-gdrive", "linux"
USER = os.getenv("USER") or os.getenv("USERNAME") or "user"
IS_ROOT = False

# ---------- JSON helpers ----------
def json_load(path: Path, default):
    try:
        if path.exists(): return json.load(open(path, "r", encoding="utf-8"))
    except Exception: pass
    return default

def json_save(path: Path, data):
    try: json.dump(data, open(path, "w", encoding="utf-8"), indent=2)
    except Exception: pass

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
    if not old_root.exists() or SYSTEM_ROOT.exists(): return
    try:
        shutil.move(str(old_root), str(SYSTEM_ROOT))
    except Exception:
        shutil.copytree(old_root, SYSTEM_ROOT, dirs_exist_ok=True)
        shutil.rmtree(old_root, ignore_errors=True)

    for src, dst in [
        (SYSTEM_ROOT / ".krnl_initialized", INIT_MARKER),
        (SYSTEM_ROOT / ".krnl_meta.json",   META_FILE),
        (SYSTEM_ROOT / ".krnl_packages.json", PKG_DB_FILE),
    ]:
        if src.exists() and not dst.exists():
            try: src.rename(dst)
            except Exception: pass

    krnl_reg = SYSTEM_ROOT / "etc" / "krnl_apt_registry.json"
    if krnl_reg.exists() and not APT_REGISTRY.exists():
        (SYSTEM_ROOT / "etc").mkdir(parents=True, exist_ok=True)
        try: krnl_reg.rename(APT_REGISTRY)
        except Exception: shutil.move(str(krnl_reg), str(APT_REGISTRY))

# ---------- init ----------
def init_system():
    for d in [f"home/{USER}","root","usr/bin","usr/lib","usr/share","etc","var/log","var/tmp","var/cache/downloads","bin","tmp","tools"]:
        (SYSTEM_ROOT / d).mkdir(parents=True, exist_ok=True)
    motd = SYSTEM_ROOT / "etc" / "motd.txt"
    if not motd.exists():
        motd.write_text(f"Welcome to {BRAND} {VERSION}!\nType 'help' for commands.\n", encoding="utf-8")
    if not APT_REGISTRY.exists(): json_save(APT_REGISTRY, {"packages": {}})
    if not INIT_MARKER.exists(): INIT_MARKER.write_text("initialized\n")
    if not META_FILE.exists(): json_save(META_FILE, {})
    if not PKG_DB_FILE.exists(): json_save(PKG_DB_FILE, {"installed": {}})

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

# ---------- gdown (Drive) ----------
def ensure_gdown() -> bool:
    try:
        import gdown  # noqa: F401
        return True
    except Exception:
        print("gdown niet gevonden — installeren...")
        return pip_install("gdown")

def download_git_zip_via_your_snippet(url: str) -> Path | None:
    """
    GEBRUIKT PRECIES JOUW 3 REGELS:
      # pip install gdown
      import gdown
      gdown.download(url=url, output=None, quiet=False, use_cookies=False, fuzzy=True)
    """
    if not ensure_gdown():
        print("Kon gdown niet installeren.")
        return None

    # ==== JOUW CODE ONGEWIJZIGD ====
    # pip install gdown
    import gdown  # noqa: E402
    print("Downloaden via jouw gdown-snippet ...")
    downloaded_path = gdown.download(url=url, output=None, quiet=False, use_cookies=False, fuzzy=True)
    # ==== EINDE JOUW CODE ====

    if not downloaded_path:
        print("Download faalde (geen bestand ontvangen).")
        return None

    src = Path(downloaded_path).resolve()
    if not src.exists():
        print("Download lijkt geslaagd maar bestand ontbreekt.")
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    dest = CACHE_DIR / src.name
    try:
        if dest.exists():
            dest.unlink()
        shutil.move(str(src), str(dest))
    except Exception:
        shutil.copy2(src, dest)
        try: src.unlink()
        except Exception: pass

    print(f"ZIP gedownload: {dest}")
    return dest

# ---------- Portable Git detect/install (ALLEEN Drive ZIP) ----------
def find_portable_git_bash_in(git_root: Path) -> str | None:
    for cnd in [git_root/"usr/bin/bash.exe", git_root/"bin/bash.exe"]:
        if cnd.exists(): return str(cnd)
    return None

def find_git_exe() -> str | None:
    # 1) portable
    for p in [GIT_HOME/"cmd/git.exe", GIT_HOME/"mingw64/bin/git.exe", GIT_HOME/"mingw32/bin/git.exe", GIT_HOME/"bin/git.exe"]:
        if p.exists(): return str(p)
    # 2) systeem
    sys_git = shutil.which("git")
    return sys_git

def unzip_to(src_zip: Path, dest_dir: Path) -> bool:
    try:
        with zipfile.ZipFile(src_zip, "r") as z:
            z.extractall(dest_dir)
        return True
    except zipfile.BadZipFile:
        print("Uitpakken mislukt: geen geldig ZIP-bestand.")
        return False
    except Exception as e:
        print(f"Uitpakken mislukt: {e}")
        return False

def ensure_portable_git_via_drive() -> bool:
    """
    Installeer Portable Git ALLEEN via de aangeleverde Google Drive ZIP (geen fallback).
    """
    # Al aanwezig?
    if find_git_exe():
        return True

    if not AUTO_DOWNLOAD_TOOLS:
        return False

    print(f"{c(C_YELLOW)}Geen Git gevonden. Download PortableGit ZIP via Google Drive...{c(C_RESET)}")
    zip_path = download_git_zip_via_your_snippet(GDRIVE_FILE_URL)
    if not zip_path:
        return False

    # Leeg doelmap (schone extractie)
    GIT_HOME.mkdir(parents=True, exist_ok=True)
    for child in list(GIT_HOME.iterdir()):
        try:
            if child.is_dir(): shutil.rmtree(child)
            else: child.unlink()
        except Exception: pass

    # Uitpakken
    if not unzip_to(zip_path, GIT_HOME):
        return False

    # Controleer structuur; fix 1 niveau dieper
    if not find_git_exe():
        subs = [p for p in GIT_HOME.iterdir() if p.is_dir()]
        if len(subs) == 1:
            inner = subs[0]
            try:
                for item in inner.iterdir():
                    shutil.move(str(item), str(GIT_HOME / item.name))
                inner.rmdir()
            except Exception as e:
                print(f"Kon submap niet herstructureren: {e}")

    ok = bool(find_git_exe())
    if ok:
        print(f"{c(C_GREEN)}Portable Git geïnstalleerd in: {GIT_HOME}{c(C_RESET)}")
    else:
        print(f"{c(C_RED)}Na uitpakken geen git.exe gevonden. Bevat de ZIP echt PortableGit?{c(C_RESET)}")
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
        except Exception: line=name
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
            print_ls_entry(t,long,human); 
            if not long: print(); 
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

# ---------- exec helpers ----------
ALIASES={"pip3":"pip","python3":"python","apt-get":"apt"}

def run_host_process(argv, cwd:Path, env:dict|None=None):
    try: p=subprocess.Popen(argv, cwd=str(cwd), env=env); p.wait()
    except Exception as e: print(f"{argv[0]}: {e}")

def win_to_msys_path(win_path:str)->str:
    p=win_path.replace("\\","/")
    if len(p)>1 and p[1]==":": drive=p[0].lower(); rest=p[2:]
    rest=rest[1:] if rest.startswith("/") else rest
    return f"/{drive}/{rest}"

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

def _try_run_get_exe(cmdlist:list[str])->str|None:
    try:
        out=subprocess.check_output(cmdlist,stderr=subprocess.STDOUT,text=True)
        exe=out.strip().splitlines()[-1].strip().strip('"')
        return exe if exe and os.path.exists(exe) else None
    except Exception: return None

def find_python()->str|None:
    if sys.executable and os.path.exists(sys.executable): return sys.executable
    exe=_try_run_get_exe(["py","-3","-c","import sys; print(sys.executable)"]) or _try_run_get_exe(["py","-c","import sys; print(sys.executable)"])
    if exe: return exe
    for n in ("python3","python","python3.exe","python.exe"):
        p=shutil.which(n)
        if p and os.path.exists(p): return p
    user=os.getenv("USERNAME") or os.getenv("USER") or ""
    candidates=[]
    for base in [fr"C:\Users\{user}\AppData\Local\Programs\Python", r"C:\Program Files", r"C:\Program Files (x86)"]:
        p=Path(base)
        if p.exists():
            for sub in p.rglob("python.exe"): candidates.append(str(sub))
    for cnd in candidates:
        if os.path.exists(cnd): return cnd
    return None

# ---------- help ----------
def _pad_cols(rows,gap=2):
    if not rows: return []
    cols=max(len(r) for r in rows); widths=[0]*cols
    for r in rows:
        for i,cell in enumerate(r): widths[i]=max(widths[i], len(cell))
    out=[]
    for r in rows:
        line=""
        for i,cell in enumerate(r):
            line += cell.ljust(widths[i]+gap) if i<cols-1 else cell
        out.append(line.rstrip())
    return out

def print_help():
    root=str(SYSTEM_ROOT).replace("\\","/"); reg=str(APT_REGISTRY).replace("\\","/")
    sections=[]
    sections += [[f"{BRAND} {VERSION} — commands overview"], [f"Root: {root}"], [f"Registry: {reg}"], [""]]
    sections += [["— Navigatie & weergave —"]]
    sections += _pad_cols([["pwd","toon huidige directory"],
                           ["ls [-a] [-l] [-h] [pad]","lijst inhoud"],
                           ["cd [dir]","ga naar map (zonder arg → ~/)"],
                           ["tree","boomweergave submappen"]])
    sections += [[""],["— Bestanden & zoeken —"]]
    sections += _pad_cols([["mkdir <dir>","maak map"],
                           ["touch <file>","lege file / update mtime"],
                           ["cat <file>","print bestand"],
                           ["echo TEXT [> file|>> file]","print/append"],
                           ["rm [-r] [-f] <pad>","verwijder"],
                           ["cp [-r] <src>... <dst>","kopie"],
                           ["mv <src>... <dst>","verplaats/hernoem"],
                           ["grep [-i] [-r] PATTERN [FILE...]","zoek tekst"]])
    sections += [[""],["— Systeem & tools —"]]
    sections += _pad_cols([["chmod MODE FILE...","sim: modus in metadata"],
                           ["chown OWNER[:GROUP] FILE...","sim: eigenaar/groep"],
                           ["which <cmd>","pad naar host-commando"],
                           ["whoami","gebruikersnaam"],
                           ["clear","scherm leeg"],
                           ["exit","afsluiten"]])
    sections += [[""],["— Pakketbeheer & tools —"]]
    sections += _pad_cols([["apt install/remove","SIM: .deb resources"],
                           ["dpkg -i/-r","SIM: install/remove"],
                           ["sudo <cmd>","SIM: geen echte privileges"],
                           ["git ...","Echt: Portable/System Git + SSH"],
                           ["python/py/pip/pip3","Echt: host tools"]])
    sections += [[""],["— Voorbeelden —"]]
    sections += _pad_cols([["git clone https://github.com/user/repo",""],
                           ["./script.sh","via Git Bash (python3-shim)"]])
    sections += [[""],["⚠️  Linux-binaries uit .deb draaien niet op Windows; scripts/bronbestanden wel."],
                 ["   Git + SSH + Python worden automatisch gevonden/ingesteld."]]
    for line in sections: print(line[0] if isinstance(line,list) else line)

# ---------- dispatcher ----------
def run_command(line:str, cwd:Path, git_env_cache:dict|None=None)->tuple[Path,dict|None]:
    if not line.strip(): return cwd, git_env_cache
    tokens=shlex.split(line); cmd,*args=tokens

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
        for a in args: resolve_path(cwd,a).mkdir(parents=True, exist_ok=True)
        return cwd, git_env_cache
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
            print(f"{indent}{Path(root).name}/"); [print(f"{indent}  {f}") for f in files]
        return cwd, git_env_cache
    elif cmd=="help": print_help(); return cwd, git_env_cache
    elif cmd=="rm": cmd_rm(cwd,args); return cwd, git_env_cache
    elif cmd=="cp": cmd_cp(cwd,args); return cwd, git_env_cache
    elif cmd=="mv": cmd_mv(cwd,args); return cwd, git_env_cache
    elif cmd=="grep": cmd_grep(cwd,args); return cwd, git_env_cache
    elif cmd=="chmod": cmd_chmod(cwd,args); return cwd, git_env_cache
    elif cmd=="chown": cmd_chown(cwd,args); return cwd, git_env_cache
    elif cmd=="which": cmd_which(args); return cwd, git_env_cache

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
                # GARANDEER Portable Git via Drive (geen fallback)
                if AUTO_DOWNLOAD_TOOLS and not find_portable_git_bash_in(GIT_HOME):
                    ensure_portable_git_via_drive()
                bash=find_bash()
                if not bash:
                    print("bash: not found. Installeer PortableGit ZIP via Google Drive.")
                    return cwd, git_env_cache
                pyexe=find_python(); msys_script=win_to_msys_path(host)
                env=os.environ.copy()
                env["PATH"]=os.pathsep.join([p for p in env.get("PATH","").split(os.pathsep) if "WindowsApps" not in p])
                # python3 shim
                shim_dir=SYSTEM_ROOT/"usr"/"bin"; shim_dir.mkdir(parents=True, exist_ok=True)
                shim_path=shim_dir/"python3"
                if pyexe:
                    shim_path.write_text('#!/usr/bin/env bash\n' + f'"{win_to_msys_path(pyexe)}" "$@"\n', encoding="utf-8")
                    try: os.chmod(shim_path,0o755)
                    except Exception: pass
                msys_shim=win_to_msys_path(str(shim_dir))
                if pyexe:
                    py_dir=win_to_msys_path(str(Path(pyexe).parent))
                    py_scripts=win_to_msys_path(str(Path(pyexe).parent/"Scripts"))
                    bash_cmd=f"export PATH='{msys_shim}':'{py_dir}':'{py_scripts}':\"$PATH\"; exec \"{msys_script}\""
                else:
                    bash_cmd=f"export PATH='{msys_shim}':\"$PATH\"; exec \"{msys_script}\""
                run_host_process([bash,"-lc",bash_cmd], cwd, env=env)
                return cwd, git_env_cache
            # .py
            if host.lower().endswith(".py"):
                ensure_pip_deps(); pyexe=find_python()
                if pyexe: run_host_process([pyexe,host], cwd)
                else: print("python: not found.")
                return cwd, git_env_cache
            # .bat/.cmd
            if host.lower().endswith(".bat") or host.lower().endswith(".cmd"):
                run_host_process([host], cwd); return cwd, git_env_cache
            # generic
            run_host_process([host], cwd); return cwd, git_env_cache
        else:
            print(f"{cmd}: No such file"); return cwd, git_env_cache

    # git (echt) – forceer eerst Drive-install als niet aanwezig
    if cmd=="git":
        if not find_git_exe() and AUTO_DOWNLOAD_TOOLS:
            ensure_portable_git_via_drive()
        git_exe=find_git_exe()
        if not git_exe:
            print("git: not found. Drive-ZIP installatie mislukt of niet publiek.")
            return cwd, git_env_cache
        if git_env_cache is None:
            git_env_cache=ensure_git_config_and_ssh(SYSTEM_ROOT/"home"/USER)
        env=os.environ.copy(); env.update(git_env_cache or {})
        try:
            p=subprocess.Popen([git_exe]+args, cwd=str(cwd), env=env); p.wait()
        except Exception as e: print(f"git error: {e}")
        return cwd, git_env_cache

    # fallback host tools
    real_cmd=shutil.which(ALIASES.get(cmd,cmd))
    if real_cmd:
        ensure_pip_deps()
        mapped=[real_cmd]+args
        try: p=subprocess.Popen(mapped, cwd=str(cwd)); p.wait()
        except Exception as e: print(f"{cmd}: {e}")
    else:
        print(f"{cmd}: command not found")
    return cwd, git_env_cache

# ---------- bootstrap real git ----------
def ensure_real_git_or_exit(first_run: bool):
    # Alleen via Drive-zip (geen GitHub fallback)
    if AUTO_DOWNLOAD_TOOLS:
        ensure_portable_git_via_drive()
    git_exe=find_git_exe()
    if not git_exe:
        zips=[p.name for p in CACHE_DIR.glob("*.zip")]
        print("Fout: kon geen echte Git installeren/vinden.")
        print("Diag:")
        print("  Looked for git here:")
        print(f"    - system PATH: {shutil.which('git')}")
        print(f"    - {GIT_HOME/'cmd/git.exe'}")
        print(f"    - {GIT_HOME/'mingw64/bin/git.exe'}")
        print(f"    - {GIT_HOME/'mingw32/bin/git.exe'}")
        print("  ZIP(s) in cache:", zips)
        print("Tip: zorg dat je Drive-link publiek is en dat het bestand een PortableGit ZIP is.")
        sys.exit(1)
    try:
        out=subprocess.check_output([git_exe,"--version"],text=True,stderr=subprocess.STDOUT)
        print(out.strip())
    except Exception as e:
        print(f"Fout bij het uitvoeren van Git: {e}"); sys.exit(1)

# ---------- main ----------
def main():
    migrate_from_krnl_if_needed()
    first=False
    if not SYSTEM_ROOT.exists() or not INIT_MARKER.exists():
        first=True; init_system()
    ensure_pip_deps()
    ensure_real_git_or_exit(first_run=first)
    # Zorg dat Bash beschikbaar is (via PortableGit)
    if AUTO_DOWNLOAD_TOOLS and not find_bash():
        ensure_portable_git_via_drive()

    header="Initializing virtual Linux system..." if first else "Virtual system mounted"
    print(f"{c(C_CYAN)}{BRAND} {VERSION} – {header}{c(C_RESET)}\n")
    cwd=SYSTEM_ROOT/"home"/USER; cwd.mkdir(parents=True, exist_ok=True)
    print(f"Mounted virtual system at {SYSTEM_ROOT}")
    print("Home directory:", cwd)
    print("Type 'help' for available commands.\n")

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
