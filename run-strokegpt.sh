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
