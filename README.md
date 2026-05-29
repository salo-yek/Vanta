# Vanta Launcher 🚀

A lightweight, non-premium Minecraft launcher built with Python 3 and PyQt6. Pick any nickname, select your version, and jump straight into the game. No login required, no hassle.

---

## ✨ Features

* **Instant Play:** Non-premium access – just enter a username, pick a version, and play.
* **Asynchronous Operations:** Fully multi-threaded architecture (`QThread`) ensures the UI never freezes during version fetching, asset downloading, or game launching.
* **Automated Java Management:** Automatically detects missing local Java environments and offers to download a portable, user-space JVM runtime (`jre-legacy`) without requiring admin privileges.
* **Smart Offline & Fallback Mode:** Seamlessly detects network failures. If the Mojang API is unreachable, it automatically populates the selection grid with cached/installed versions and boots directly from local storage.
* **Fluid Custom Animations:** Sleek borderless window interface featuring smooth window transitions, custom physics-based fade-ins, and native Windows API hooks for custom taskbar minimization.
* **Persistent Settings:** Remembers your last used username and Minecraft version across sessions for immediate launching.

## 🛠️ Technical Stack & Requirements

* **OS:** Windows (Optimized with native Windows API interactions via `ctypes` for animations and proper Taskbar grouping using `AppUserModelID`).
* **Framework:** PyQt6
* **Core Library:** `minecraft-launcher-lib`
* **Python Version:** Python 3.10 or higher recommended

---

## 📸 Screenshots

| Empty Launcher | Profile Preview | Version Selection | Downloading Assets |
| :---: | :---: | :---: | :---: |
| <img src="https://github.com/user-attachments/assets/518519f8-00d0-4368-862b-ec866f628456" width="220" alt="Vanta Launcher Empty UI" /> | <img src="https://github.com/user-attachments/assets/f4062168-dce9-4840-a2b5-ee974d53d726" width="220" alt="Vanta Launcher User Profile" /> | <img src="https://github.com/user-attachments/assets/f388e763-2ec8-465c-8518-f7cefbc07aee" width="220" alt="Vanta Launcher Version List" /> | <img src="https://github.com/user-attachments/assets/396f5983-b7cf-4723-82e4-2043afa248c7" width="220" alt="Vanta Launcher Downloading Assets" /> |

---

## 📂 Architecture Overview

The launcher uses dedicated asynchronous workers to isolate heavy processes from the main GUI thread:
* `VersionFetchWorker`: Non-blocking Mojang API queries for official release versions.
* `JavaDownloadWorker`: Manages portable JVM installation with continuous progress callbacks mapped directly onto the primary UI action button.
* `LaunchWorker`: Verification of Minecraft client assets, configuration of the data directory structure, and headless child process spawning via `subprocess.Popen`.

---

## 🚀 Quick Start

### For Users (Executable)
1. Head over to the **[Releases](https://github.com/salo-yek/Vanta/releases)** page.
2. Download the latest `Vanta Launcher.exe`.
3. Run the executable and start playing!

### For Developers (Running from Source)

1. **Clone the repository:**
```bash
git clone [https://github.com/salo-yek/Vanta.git](https://github.com/salo-yek/Vanta.git)
cd Vanta
```
2. **Install dependencies:**
   pip install -r requirements.txt
3. **Asset preparation:**
   Ensure an icons/ folder exists within your root directory containing icon.ico for correct application mapping.
4. **Launch the application:**
   python main.py
