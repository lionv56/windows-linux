Linux Terminal v3.7 (Windows – Portable)

Een lichtgewicht, “Linux-achtige” terminal die draait op Windows zonder WSL of admin-rechten.
Maakt een gesandboxte root (LinuxFS/) naast het script, vertaalt paden, en kan shell-/Python-/batch-scripts uitvoeren. Inclusief auto-setup: pip-deps en (optioneel) Portable Git Bash.

✨ Features

Virtuele Linux-root: LinuxFS/ met /usr, /etc, /home/<user>, etc.

Linux-achtige commando’s: ls, cd, pwd, mkdir, touch, cat, echo, rm, cp, mv, grep, tree, which, chmod/chown (simulatie), sudo (simulatie).

Scripts uitvoeren:

./script.sh via Git Bash (systeem of portable).

script.py via gedetecteerde Python (of py launcher).

.bat/.cmd direct via Windows.

Auto-setup:

Installeert Python-packages (colorama, pyreadline3 op Windows).

Optioneel: downloadt en uitpakt Portable Git lokaal (geen admin nodig).

Maakt automatisch een python3 shim in /usr/bin zodat bash-scripts met python3 werken.

APT/DPKG simulatie: kan .deb-archieven uitpakken (scripts/resources), maar geen native Linux-binaries draaien op Windows.

Migrate: herkent en migreert oude KRNL_System → LinuxFS.

📦 Benodigdheden

Windows 10/11

Python 3.11+ (geadviseerd 3.12)

Internet alleen nodig voor:

eerste auto-install van pip-deps;

(optioneel) download van Portable Git;

echte git clone acties
