#!/bin/bash

# StrokeGPT Linux Installation Script
# Installs dependencies and sets up the application for Linux

set -e  # Exit on any error

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root
if [[ $EUID -eq 0 ]]; then
   print_error "This script should not be run as root"
   exit 1
fi

print_status "Starting StrokeGPT Linux installation..."

# Check Python version
print_status "Checking Python installation..."
if ! command -v python3 &> /dev/null; then
    print_error "Python 3 is not installed. Please install Python 3.8 or later:"
    echo "  Ubuntu/Debian: sudo apt update && sudo apt install python3 python3-pip"
    echo "  Fedora: sudo dnf install python3 python3-pip"
    echo "  Arch: sudo pacman -S python python-pip"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
print_success "Python $PYTHON_VERSION found"

# Check pip
if ! command -v pip3 &> /dev/null && ! python3 -m pip --version &> /dev/null; then
    print_error "pip is not installed. Please install pip:"
    echo "  Ubuntu/Debian: sudo apt install python3-pip"
    echo "  Fedora: sudo dnf install python3-pip"
    echo "  Arch: sudo pacman -S python-pip"
    exit 1
fi

# Install Ollama
print_status "Checking Ollama installation..."
if ! command -v ollama &> /dev/null; then
    print_status "Installing Ollama..."
    curl -fsSL https://ollama.ai/install.sh | sh
else
    print_success "Ollama already installed"
fi

# Check if ollama service is running (try system service first, then user)
print_status "Ensuring Ollama service is running..."
if systemctl is-active --quiet ollama 2>/dev/null; then
    print_success "Ollama system service is running"
elif systemctl --user is-active --quiet ollama 2>/dev/null; then
    print_success "Ollama user service is running"
else
    # Try to start system service first
    if systemctl list-unit-files ollama.service &>/dev/null; then
        print_status "Starting Ollama system service..."
        sudo systemctl start ollama
    else
        print_status "Starting Ollama manually..."
        ollama serve &
        OLLAMA_PID=$!
        sleep 3
    fi
fi

# Wait for Ollama to be ready
print_status "Waiting for Ollama to be ready..."
timeout 30 bash -c 'until curl -s http://localhost:11434/api/tags >/dev/null 2>&1; do sleep 1; done'

if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
    print_error "Ollama is not responding. Please check if it's running:"
    echo "  systemctl --user status ollama"
    exit 1
fi

print_success "Ollama is running"

# Install the AI model
print_status "Checking if llama3:8b-instruct-q4_K_M model is installed..."
if ! ollama list | grep -q "llama3:8b-instruct-q4_K_M"; then
    print_status "Downloading AI model (this may take several minutes)..."
    ollama pull llama3:8b-instruct-q4_K_M
    print_success "AI model downloaded"
else
    print_success "AI model already installed"
fi

# Create virtual environment and install dependencies
print_status "Creating virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    print_success "Virtual environment created"
else
    print_success "Virtual environment already exists"
fi

print_status "Installing Python dependencies in virtual environment..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
print_success "Python dependencies installed"

# Create launch script
print_status "Creating launch script..."
cat > run-strokegpt.sh << 'EOF'
#!/bin/bash

# StrokeGPT Launch Script

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if Ollama is running
if ! curl -s http://localhost:11434/api/tags >/dev/null 2>&1; then
    print_status "Starting Ollama..."
    if command -v systemctl &> /dev/null; then
        # Try system service first, then user service
        if systemctl list-unit-files ollama.service &>/dev/null; then
            sudo systemctl start ollama
        elif systemctl --user list-unit-files ollama.service &>/dev/null; then
            systemctl --user start ollama
        else
            print_status "Starting Ollama manually..."
            ollama serve &
        fi
        sleep 3
    else
        print_error "Ollama is not running. Please start it manually:"
        echo "  ollama serve"
        exit 1
    fi
fi

# Verify model is available
if ! ollama list | grep -q "llama3:8b-instruct-q4_K_M"; then
    print_error "AI model not found. Please run the installation script again."
    exit 1
fi

print_success "Starting StrokeGPT..."
print_status "Open your browser to: http://127.0.0.1:5000"
print_status "Press Ctrl+C to stop the application"

# Activate virtual environment and start the application
source venv/bin/activate
python app.py
EOF

chmod +x run-strokegpt.sh

# Create desktop entry (optional)
if command -v xdg-desktop-menu &> /dev/null; then
    print_status "Creating desktop entry..."
    INSTALL_DIR=$(pwd)
    cat > strokegpt.desktop << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=StrokeGPT
Comment=AI-powered Handy controller
Exec=${INSTALL_DIR}/run-strokegpt.sh
Icon=applications-games
Terminal=true
Categories=Game;
StartupNotify=true
Path=${INSTALL_DIR}
EOF
    
    xdg-desktop-menu install strokegpt.desktop 2>/dev/null || true
    rm strokegpt.desktop
fi

print_success "Installation complete!"
echo
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${GREEN}StrokeGPT is now installed and ready to use!${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo
echo "To start the application:"
echo -e "  ${BLUE}./run-strokegpt.sh${NC}"
echo
echo "Then open your browser to:"
echo -e "  ${BLUE}http://127.0.0.1:5000${NC}"
echo
echo "Requirements:"
echo "  • Your Handy connection key from handyfeeling.com"
echo "  • (Optional) ElevenLabs API key for voice features"
echo
print_warning "Note: The AI model runs locally and may use significant CPU/RAM"
print_warning "Ensure your Handy device is connected to the internet"
echo