# Vanta Launcher 🚀

A lightweight, non-premium Minecraft launcher built with Python 3 and PyQt6. Pick any nickname, select your version, and jump straight into the game. No login required, no hassle.

---

## ✨ Features

* **Instant Play:** Non-premium access – just enter a username, pick a version, and play.
* **Mods Manager & Modrinth Search:** Search, install, and manage Fabric mods directly inside the UI. View active mods and delete them on a per-version basis.
* **Performance Mode:** Instant Fabric engine installation and automatic injection of essential optimization mods (Sodium, Lithium, FerriteCore, and EntityCulling) to boost in-game performance.
* **Discord Rich Presence (RPC):** Dynamically displays your playing status (e.g., active Minecraft version) on your Discord profile using `pypresence` (fully toggleable in the settings drawer).
* **Dynamic Player Avatar:** Fetches and displays a real-time skin preview of the player's head (via the Minotar API) as you type your username.
* **Isolated Version Instances:** Keeps game settings, worlds, logs, and mods strictly isolated per-version inside `.Vanta/instances/<version>` to prevent mod conflicts and configuration issues.
* **Custom RAM Allocation:** A dedicated settings drawer featuring a slider to easily allocate between 2 GB to 16 GB of memory for your game instances.
* **Automated Java Management:** Automatically detects missing system Java environments and offers to download a portable, user-space JVM runtime (`jre-legacy`) without requiring administrator privileges.
* **Smart Offline & Fallback Mode:** Seamlessly detects network failures. If the Mojang API is unreachable, it automatically populates the selection grid with cached/installed versions and boots directly from local storage.
* **Asynchronous Operations:** Fully multi-threaded architecture (`QThread`) ensures the UI never freezes during version fetching, asset downloading, mod searching, or game launching.
* **Fluid Custom Animations:** Sleek borderless window interface featuring smooth window transitions, sliding custom panels, and OS-specific hooks for correct window and taskbar minimization.

---

## 🛠️ Technical Stack & Requirements

* **OS Support:** Windows and Linux (features cross-platform path resolution and safe OS-specific execution wrappers).
* **Framework:** PyQt6
* **Core Libraries:**
  * `minecraft-launcher-lib` (Core Minecraft utility)
  * `pypresence` (Discord Rich Presence integration - optional)
  * `requests` (API requests & Modrinth downloads)
* **Python Version:** Python 3.10 or higher recommended

---

## 📸 Screenshots

| Empty Launcher | Profile Preview | Version Selection |
| :---: | :---: | :---: |
| <img src="TU_WSTAW_LINK_DO_ZDJECIA_1" width="240" alt="Vanta Launcher Empty UI" /> | <img src="TU_WSTAW_LINK_DO_ZDJECIA_2" width="240" alt="Vanta Launcher User Profile" /> | <img src="TU_WSTAW_LINK_DO_ZDJECIA_3" width="240" alt="Vanta Launcher Version List" /> |

| Installing Assets | Settings Drawer | Mods Manager |
| :---: | :---: | :---: |
| <img src="TU_WSTAW_LINK_DO_ZDJECIA_4" width="240" alt="Vanta Launcher Installing" /> | <img src="TU_WSTAW_LINK_DO_ZDJECIA_5" width="240" alt="Vanta Launcher Settings" /> | <img src="TU_WSTAW_LINK_DO_ZDJECIA_6" width="240" alt="Vanta Launcher Mods Manager" /> |

---

## 📂 Architecture Overview

The launcher utilizes dedicated asynchronous workers to isolate heavy IO and network processes from the main GUI thread:
* `VersionFetchWorker`: Non-blocking Mojang API queries for official release versions with local cache fallbacks.
* `JavaDownloadWorker`: Manages portable JVM installations with continuous progress callbacks mapped directly onto the primary UI action button.
* `LaunchWorker`: Verifies client assets, bootstraps the Fabric loader, manages default performance mod packages, and handles headless child process spawning.
* `AvatarLoaderWorker`: Asynchronously fetches 3D head previews based on the typed username using the Minotar web service.
* `ModSearchWorker`: Queries the Modrinth Search API for user-requested Fabric mods using debounced search inputs.
* `ModInstallWorker`: Asynchronously retrieves target mod files, locates primary compatibility releases, and downloads them safely into the version's instance folder.

---

## 🚀 Quick Start

### For Users (Executable)
1. Head over to the **[Releases](https://github.com/salo-yek/Vanta/releases)** page.
2. Download the latest `Vanta Launcher.exe` (Windows) or download the source to run on Linux.
3. Run the application and start playing!

### For Developers (Running from Source)

1. **Clone the repository:**
```bash
git clone https://github.com/salo-yek/Vanta.git
cd Vanta
```

2. **Install dependencies:**
```bash
pip install -r requirements.txt
```

3. **Asset preparation:**
Ensure an `icons/` folder exists within your root directory containing `icon.ico` for correct taskbar and window icon mapping.

4. **Launch the application:**
```bash
python main.py
```
