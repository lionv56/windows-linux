Linux Terminal v3.9-gdrive (Windows – Portable)

Een lichtgewicht, “Linux-achtige” terminal die draait op Windows zonder WSL of admin-rechten. Maakt een gesandboxte root (LinuxFS/) naast het script, vertaalt paden, en kan shell-/Python-/batch-scripts uitvoeren. Inclusief auto-setup: pip-deps en Portable Git via Google Drive (gdown).

✨ Features

Virtuele Linux-root: LinuxFS/ met structuur als /usr, /etc, /home/<user>, enz.

Linux-achtige commands: ls, cd, pwd, mkdir, touch, cat, echo, rm, cp, mv, grep, tree, which, chmod/chown (simulatie), sudo (simulatie).

Scripts uitvoeren

./script.sh via Git Bash (systeem of portable).

script.py via gedetecteerde Python (of py-launcher).

.bat/.cmd direct via Windows.

Auto-setup

Installeert Python-packages (colorama, op Windows ook pyreadline3).

Portable Git: wordt automatisch gedownload als ZIP via jouw Google-Drive link met gdown, uitgepakt naar LinuxFS\tools\git (geen admin nodig).

Maakt automatisch een python3 shim in /usr/bin zodat bash-scripts met python3 werken.

Echte Git + SSH out-of-the-box

Stelt git config --global user.name/user.email in als die nog leeg zijn.

Bootstrap van ~/.ssh (maakt id_ed25519) en zet GIT_SSH_COMMAND zodat Portable Git meteen kan clonen/pushen.

Git gebruikt een virtuele HOME (LinuxFS/home/<user>) voor consistent gedrag.

APT/DPKG simulatie: kan .deb-archieven uitpakken (scripts/resources), maar geen native Linux-binaries draaien op Windows.

Migratie: herkent en migreert oude KRNL_System/ → LinuxFS/.

Mooie help: secties, voorbeelden en kolommen.

🚀 Quick start

Start het programma (python "linux terminal.py").

Bij eerste run:

Pip-deps worden geïnstalleerd.

Portable Git ZIP wordt via Google Drive (gdown) gedownload en uitgepakt naar LinuxFS\tools\git.

In de shell:

git --version

git clone <repo>

./script.sh of python script.py

Let op: zorg dat je Drive-bestand openbaar is of met iedereen met de link deelbaar (zodat gdown kan downloaden).

🧩 Vereisten

Windows 10/11

Python 3.10+ (aanbevolen 3.12)

Internet alleen nodig voor:

eerste auto-install van pip-deps;

download van Portable Git via Google Drive;

echte git clone/pip install acties.

🔧 Troubleshooting

“git: not found”
Controleer of de Drive-link correct is en een ZIP levert; na download hoort Git te staan in LinuxFS\tools\git\....

Python package mist (bv. rich)
Installeer in dezelfde interpreter als die de shell gebruikt:
python -m pip install -r requirements.txt of python -m pip install rich

./linux.sh gebruikt het verkeerde Python
In de shell: which python om te zien welke Python wordt gebruikt. De shim zorgt dat python3 in Git Bash naar jouw Windows-Python wi
