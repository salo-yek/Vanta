# Vanta Launcher 🚀

A lightweight, no-premium Minecraft launcher built with Python and PyQt6. Pick any nickname, select your version, and jump straight into the game. No login required, no hassle.

---

## ✨ Features

* **Instant Play:** No-premium access – just enter a username, pick a version, and play.
* **Modern UI:** Clean, modern interface inspired by dark-mode design standards.
* **Smooth Animations:** Features fluid window transitions, fade-ins, and clean taskbar minimization.
* **Smart Offline Mode:** Automatically detects network issues and allows launching cached/installed versions offline.
* **Persistent Settings:** Remembers your last used username and Minecraft version for quicker launches.

## 🛠️ Requirements & Technical Stack

* **OS:** Windows (Optimized with native Windows API interactions for animations)
* **Framework:** PyQt6
* **Core Library:** `minecraft-launcher-lib`
* **Java:** OpenJDK 17 or 21 recommended (must be added to your system's PATH)

## 🚀 Quick Start

### For Users (Executable)
1. Head over to the **[Releases](https://github.com/salo-yek/Vanta/releases)** page.
2. Download `Vanta.exe`.
3. Run the executable and start playing!

### For Developers (Running from Source)
Run the following commands in your terminal to clone the repo, install all required dependencies, and launch the application:

```bash
git clone [https://github.com/salo-yek/Vanta.git](https://github.com/salo-yek/Vanta.git)
cd Vanta
pip install -r requirements.txt
python main.py

## 📝 License

This project is open-source. Feel free to fork and build upon it!
