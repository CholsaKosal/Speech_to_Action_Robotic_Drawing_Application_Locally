# Speech to Action Robotic Drawing Application

## Introduction to the project

The "Speech to Action Robotic Drawing" application enables users to control a GOFA CRB 15000 robot arm using natural voice commands to draw images. It features a desktop application with a JavaScript/TypeScript frontend (built with Electron) for a rich user interface and a Python backend to handle core logic including speech recognition, natural language understanding via a local LLM, image processing, and robot control. This document provides guidance for developers on setting up and contributing to the project.
---

## Showcase (Update Later)

*(This section will be updated with screenshots, GIFs, or videos demonstrating the application's capabilities once available.)*

---

## Set up and Run (User Guide - Update Later)

*(This section will detail how an end-user can set up and run the packaged application once it's ready for distribution. This is different from the developer setup below.)*

---

# Developer Notes

This section provides step-by-step instructions for developers to set up the project on a new machine, contribute to the codebase, and run/test the application.

**Current Development Goal: Streaming STT and LLM Output**
To enhance the conversational feel of the application, a key development goal is to implement streaming Speech-to-Text (STT) and streaming Large Language Model (LLM) output. This means processing audio as it's received to get faster partial transcriptions, and sending the LLM's response token-by-token to the frontend. This will allow the application to start providing feedback and even begin Text-to-Speech (TTS) output more immediately, reducing perceived latency and making interactions feel more natural and human-like.

## 1. Set up Git: Clone and Configure for a New PC

These steps assume you have Git installed on your new PC. If not, download and install it from [https://git-scm.com/](https://git-scm.com/).

### 1.1. Clone the Repository
Open your preferred terminal (Git Bash, Command Prompt, PowerShell, or a Linux/macOS terminal).
```bash
# Navigate to the directory where you want to store the project
cd path/to/your/development/folder

# Clone the repository using HTTPS (recommended for simplicity)
git clone [https://github.com/CholsaKosal/Speech_to_Action_Robotic_Drawing_Application.git](https://github.com/CholsaKosal/Speech_to_Action_Robotic_Drawing_Application.git)

# Or clone using SSH (if you have SSH keys set up with GitHub)
# git clone git@github.com:CholsaKosal/Speech_to_Action_Robotic_Drawing_Application.git

# Navigate into the cloned project directory
cd Speech_to_Action_Robotic_Drawing_Application
```

### 1.2. Configure Your Git Identity

Git needs to know who you are to associate your commits correctly. If this is a new machine or Git hasn't been configured globally:

```bash
git config --global user.name "Your Name"
git config --global user.email "your_email@example.com"
```

Replace `"Your Name"` and `"your_email@example.com"` with your actual Git/GitHub username and email.

### 1.3. Check Remote Configuration

Verify that the remote `origin` is correctly pointing to the GitHub repository:

```bash
git remote -v
```

You should see output similar to:

```
origin  [https://github.com/CholsaKosal/Speech_to_Action_Robotic_Drawing_Application.git](https://github.com/CholsaKosal/Speech_to_Action_Robotic_Drawing_Application.git) (fetch)
origin  [https://github.com/CholsaKosal/Speech_to_Action_Robotic_Drawing_Application.git](https://github.com/CholsaKosal/Speech_to_Action_Robotic_Drawing_Application.git) (push)
```

### 1.4. Pushing Changes

After making commits, you can push your changes to the `master` branch (or any other branch you are working on):

```bash
git push origin master
```

## 2. Set up Developing Environment

This project has a Python backend and a JavaScript/TypeScript frontend (using Electron with Vite).

### 2.1. Check Desktop Specifications

Before proceeding, ensure your desktop has adequate resources. Run the following script in **Windows Command Prompt (`cmd`)** to gather system information. This script will also create a `dxdiag_output.txt` file in the current directory with more detailed graphics information.
```cmd
@echo off
echo --- Checking System Overview (OS, CPU, RAM) ---
systeminfo | findstr /B /C:"OS Name" /C:"OS Version" /C:"System Manufacturer" /C:"System Model" /C:"Processor(s)" /C:"Total Physical Memory" /C:"Available Physical Memory"
echo.
echo --- CPU Detailed Information ---
wmic cpu get name, numberofcores, numberoflogicalprocessors, maxclockspeed
echo.
echo --- GPU (Graphics Card) Information ---
wmic path win32_videocontroller get name, adapterram, driverversion, VideoModeDescription
echo.
echo --- NVIDIA GPU Detailed Information (if NVIDIA card and drivers are installed) ---
echo Attempting to run nvidia-smi... If this command fails, it likely means you don't have an NVIDIA GPU or the NVIDIA drivers are not installed correctly in the system PATH.
nvidia-smi
echo.

echo --- Disk Drive Space Information (Size and FreeSpace are in Bytes) ---
wmic logicaldisk get caption, description, drivetype, freespace, size, volumename
echo.
echo --- Generating DirectX Diagnostic Report (this may take a moment) ---
dxdiag /t dxdiag_output.txt
echo.
echo A detailed DirectX diagnostic report has been saved to the file "dxdiag_output.txt"
echo in your current directory.
echo Please open "dxdiag_output.txt" with a text editor to view detailed graphics card VRAM.
echo Look under "Display Devices" in that file for VRAM information (e.g., "Display Memory" or "Dedicated Memory").
@echo on
```

**Minimum Recommended Specs (for smoother development & running AI models):**

  * **CPU:** Modern multi-core (e.g., Intel Core i5/i7 8th gen+, AMD Ryzen 5/7 3000 series+)
  * **RAM:** 16GB (32GB+ recommended for larger LLMs)
  * **GPU:** NVIDIA GeForce RTX series with at least 6-8GB VRAM (more is better for LLM offloading). AMD GPUs can work but may require more setup for AI acceleration.
  * **Disk:** SSD with at least 50-100GB free space.

### 2.2. Python Backend Setup

1.  **Install Python:** If not already installed, download and install Python (version 3.9+ recommended) from [https://www.python.org/](https://www.python.org/). Ensure Python and Pip are added to your system's PATH during installation.

2.  **Install FFmpeg (Required for Whisper):**
    * Download FFmpeg from [https://ffmpeg.org/download.html](https://ffmpeg.org/download.html) (e.g., "release essentials" from gyan.dev for Windows).
    * Extract it to a folder (e.g., `C:\ffmpeg`).
    * Add the `bin` subdirectory (e.g., `C:\ffmpeg\bin`) to your system's PATH environment variable.
    * Verify by opening a *new* terminal and typing `ffmpeg -version`.

3.  **Install Microsoft C++ Build Tools (Required for `llama-cpp-python` on Windows):**
    * Go to the Visual Studio downloads page: [https://visualstudio.microsoft.com/downloads/](https://visualstudio.microsoft.com/downloads/)
    * Download "Build Tools for Visual Studio".
    * Run the installer and select the "**Desktop development with C++**" workload. This installs the necessary C++ compilers (MSVC), CMake, and `nmake`.
    * Restart your terminal after installation.

4.  **Navigate to the backend directory:**
    ```bash
    cd backend
    ```
5.  **Create and activate a virtual environment:**
    ```bash
    python -m venv venv
    # On Windows cmd:
    venv\Scripts\activate
    # On PowerShell:
    # .\venv\Scripts\Activate.ps1
    # On Git Bash / Linux / macOS:
    # source venv/bin/activate
    ```
    Your terminal prompt should now be prefixed with `(venv)`.
6.  **Install Python dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
    *(Note: `requirements.txt` should be kept up-to-date with all necessary backend libraries like Flask, opencv-python, Pillow, qrcode, numpy, `openai-whisper`, `llama-cpp-python`, etc.)*
7.  **Download AI Models:**
    * **Whisper models** are typically downloaded automatically by the `openai-whisper` library on first use (e.g., the "base" model).
    * **LLM model (e.g., Phi-3 Mini Instruct GGUF):**
        * Download the desired GGUF model file (e.g., a Q4\_K\_M quantized version of Phi-3 Mini Instruct) from Hugging Face Hub.
        * Create a directory `backend/models/`.
        * Place the downloaded `.gguf` file into `backend/models/`.
        * Ensure the `LLM_MODEL_FILENAME` in `backend/config.py` matches the name of this file.
        * Note: The `backend/models/` directory should be in your `.gitignore`.

8.  The `backend` directory should contain subdirectories like `models` (for AI models), `qr_uploads` (for images uploaded via QR code), and `audio_tmp` (for temporary audio files). These are typically ignored by Git but needed for runtime.

### 2.3. JavaScript/TypeScript Frontend Setup (Electron with Vite)

The frontend is located in `frontend/s2a-drawing-ui/`.
1.  **Install Node.js and npm:** If not already installed, download and install Node.js (which includes npm) from [https://nodejs.org/](https://nodejs.org/) (LTS version recommended).
2.  **Navigate to the frontend project directory:**
    ```bash
    cd frontend/s2a-drawing-ui
    ```
3.  **Install Node.js dependencies:**
    ```bash
    npm install
    ```
    This will install packages listed in `package.json`, including Electron, Vite, React, TypeScript, etc.
      * If you encounter warnings about deprecated packages or vulnerabilities, you can try:
        ```bash
        npm audit fix
        ```
        Be cautious with `npm audit fix --force` as it might introduce breaking changes.

## 3. Run and Test Application (Steps and Scripts)

### 3.0. Network Configuration for QR Code Image Upload (Current Method)
Important: For the QR code image upload feature (from phone to PC) to work with the current setup, your PC and your phone must be on the same local network, and that network must allow direct device-to-device communication. Guest networks or networks with "Client Isolation" / "AP Isolation" enabled will likely not work.
Using a Mobile Hotspot (Recommended & Tested):
Enable the mobile hotspot feature on your phone.
Connect your development PC to this mobile hotspot Wi-Fi network.
When the Python backend server starts, it will attempt to generate a QR code URL using the PC's IP address on this hotspot network (e.g., 192.168.43.x).
Scanning the QR code with the phone (which is the hotspot provider) will then allow it to connect to the PC.
Using a Private Wi-Fi Network:
If using a home/private Wi-Fi router, ensure both devices are connected to it.
Crucially, ensure that "AP Isolation," "Client Isolation," or similar features (which prevent connected devices from communicating with each other) are disabled on your router.
This direct local network approach is for the current development phase. Future updates might explore other methods for image uploads.

### 3.1. Running the Python Backend

1.  Ensure your Python virtual environment is activated in the `backend` directory:
    ```bash
    # (If not already in backend/)
    cd path/to/project/backend
    # (If venv not active)
    # Windows cmd:
    venv\Scripts\activate
    # PowerShell:
    # .\venv\Scripts\Activate.ps1
    # Git Bash / Linux / macOS:
    # source venv/bin/activate
    ```
2.  Run the main backend orchestrator script:
    ```bash
    python main_orchestrator.py
    ```
    This should start any necessary servers (e.g., Flask-SocketIO for WebSockets, QR code uploads, and AI model loading). Monitor the terminal for logs and status messages.

### 3.2. Running the Electron Frontend (Development Mode)

1.  Open a **new terminal** window/tab.
2.  Navigate to the frontend project directory:
    ```bash
    cd path/to/project/frontend/s2a-drawing-ui
    ```
3.  Start the Vite development server and Electron application:
    ```bash
    npm run dev
    ```
    This command (defined in `package.json`) typically launches the Vite dev server for the renderer process (UI) and starts the Electron main process, opening the application window.

### 3.3. Generating `code_base.txt` (for sharing/review)

This script helps generate a snapshot of the current codebase, excluding large or unnecessary directories.
1.  **Environment:** Use **WSL (Windows Subsystem for Linux)** or **Git Bash** on Windows, or a standard terminal on Linux/macOS.
2.  **Ensure `tree` command is available:**
      * In WSL, if `tree` is not found but available via Snap:
        ```bash
        export PATH=$PATH:/snap/bin # For current session
        # For permanent fix, add to ~/.bashrc: export PATH="$PATH:/snap/bin"
        ```
3.  **Navigate to the project root directory (`Speech_to_Action_Robotic_Drawing_Application`).**
4.  **Make the script executable (if not already):**
    ```bash
    chmod +x generate_code_base.sh
    ```
5.  **Run the script:**
    ```bash
    # Clean up old files first (optional, script also does this)
    # rm -f code_base.txt temp_all_contents.txt filter_rules.sed
    ./generate_code_base.sh
    ```
    This will create `code_base.txt` in the project root. Review `exclude_patterns.conf` to ensure it correctly lists directories/files to exclude from this output.

### 3.4. Testing Robot Communication

1.  Ensure your GOFA CRB 15000 robot controller is powered on and connected to the same network as your development PC.
2.  Verify the robot controller's IP address and port match the settings in your backend's `config.py`.
3.  Use the application's UI or voice commands to initiate actions that involve robot communication.
4.  Monitor backend logs for connection status and command exchange.
5.  If using RobotStudio for simulation, ensure it's running and configured to listen for socket connections from your application.

## 4. Other Necessary Information for Development

  * **Branching Strategy:** (Define your team's branching strategy, e.g., feature branches, develop branch, master/main for releases). For solo development, working on `master` or a `develop` branch is common.
  * **Coding Standards & Linting:**
      * **Python:** Consider using tools like Black for code formatting and Flake8 or Pylint for linting.
      * **TypeScript/JavaScript:** The frontend project (created with `electron-vite`) likely includes ESLint and Prettier configurations. Adhere to these.
  * **API Documentation (Frontend-Backend):** As the WebSocket/HTTP API between the frontend and backend evolves, document the message formats, endpoints, and expected data structures.
  * **LLM and STT Model Management:**
      * Models are stored locally (e.g., in `backend/models/`, which is gitignored).
      * Document which specific models and quantization levels are being used (e.g., Whisper 'base', Phi-3 Mini Instruct Q4\_K\_M GGUF).
  * **Dependencies:** Keep `backend/requirements.txt` and `frontend/s2a-drawing-ui/package.json` up-to-date.
  * **Troubleshooting:**
      * Check backend logs for Python errors.
      * Use browser developer tools in Electron (usually `Ctrl+Shift+I` or via the View menu) to debug frontend JavaScript/TypeScript and inspect network requests.
      * Monitor system resource usage (CPU, RAM, VRAM) using Task Manager (Windows) or equivalent tools.
---
