#!/usr/bin/env bash

# Nexus ARC Interactive Installation Script (Bash Version)
# Note: Python is still highly recommended because 'nexus-bot' requires it anyway.

set -e

# --- Helpers ---
clear_screen() {
    clear
}

prompt_choice() {
    local question="$1"
    shift
    local choices=("$@")
    local val=""

    while true; do
        echo -e "\n${question}"
        for i in "${!choices[@]}"; do
            echo "  $((i+1))) ${choices[$i]}"
        done

        read -p $'\nSelect an option: ' val
        if [[ "$val" =~ ^[0-9]+$ ]] && [ "$val" -ge 1 ] && [ "$val" -le "${#choices[@]}" ]; then
            echo "$val"
            return
        fi
        echo "Please enter a valid number between 1 and ${#choices[@]}."
    done
}

prompt_multi_choice() {
    local question="$1"
    shift
    local choices=("$@")
    local val=""
    
    while true; do
        echo -e "\n${question}"
        for i in "${!choices[@]}"; do
            echo "  $((i+1))) ${choices[$i]}"
        done

        read -p $'\nSelect options separated by commas (e.g. 1,3) or press Enter to skip: ' val
        if [ -z "$val" ]; then
            echo ""
            return
        fi

        # Basic validation (checking if all comma separated items are numbers in range)
        local valid=1
        local selected=()
        IFS=',' read -ra ADDR <<< "$val"
        for i in "${ADDR[@]}"; do
            local num=$(echo "$i" | tr -d ' ')
            if [[ "$num" =~ ^[0-9]+$ ]] && [ "$num" -ge 1 ] && [ "$num" -le "${#choices[@]}" ]; then
                selected+=("$num")
            else
                valid=0
                break
            fi
        done

        if [ "$valid" -eq 1 ]; then
            echo "${selected[@]}"
            return
        fi
        echo "Please enter valid comma-separated numbers."
    done
}

prompt_string() {
    local question="$1"
    local default="$2"
    local val=""
    
    if [ -n "$default" ]; then
        read -p "${question} [${default}]: " val
        if [ -z "$val" ]; then
            echo "$default"
            return
        fi
    else
        read -p "${question}: " val
    fi
    echo "$val"
}

# --- Main execution ---
clear_screen

echo "======================================="
echo " 🚀 Welcome to Nexus ARC Installation (Bash) 🚀"
echo "======================================="

# Current working directory resolving is tough if curled. Assume run locally or falling back to pwd. 
BOT_DIR=$(pwd)
ENV_FILE="${BOT_DIR}/.env"

if [ -f "$ENV_FILE" ]; then
    replace=$(prompt_choice "An existing .env file was found. Overwrite?" "No, keep it" "Yes, overwrite")
    if [ "$replace" == "1" ]; then
        echo "Installation aborted to preserve .env."
        exit 0
    fi
fi

echo -e "\n--- 1. Deployment Mode ---"
mode_choice=$(prompt_choice "Which storage mode do you want to use?" "Lite (Filesystem only)" "Enterprise (PostgreSQL + Redis)")
is_enterprise=0
if [ "$mode_choice" == "2" ]; then
    is_enterprise=1
fi

setup_db=0
use_docker=0
if [ "$is_enterprise" -eq 1 ]; then
    echo -e "\n--- 2. Infrastructure Setup ---"
    infra_choice=$(prompt_choice "How do you want to run PostgreSQL and Redis?" "Docker Compose (Sandboxed)" "System packages (e.g. brew or apt)" "I already have them running")
    if [ "$infra_choice" == "1" ]; then
        use_docker=1
        setup_db=1
    elif [ "$infra_choice" == "2" ]; then
        setup_db=1
    fi
fi

echo -e "\n--- 3. Credentials & Keys ---"
telegram_token=$(prompt_string "Enter your Telegram Bot Token" "")
telegram_users=$(prompt_string "Enter your Telegram User ID (comma-separated)" "")

vcs_choice=$(prompt_choice "Which VCS platform will you be using primarily?" "GitHub" "GitLab")
github_token=""
gitlab_token=""
gitlab_url=""

if [ "$vcs_choice" == "1" ]; then
    github_token=$(prompt_string "Enter your GitHub Personal Access Token" "")
else
    gitlab_token=$(prompt_string "Enter your GitLab Personal Access Token" "")
    gitlab_url=$(prompt_string "Enter your GitLab Base URL" "https://gitlab.com")
fi

HOME_DIR=~
base_dir=$(prompt_string "Enter your workspaces base directory" "${HOME_DIR}/git")

# Write .env content
echo "Writing .env file..."
cat <<EOF > "$ENV_FILE"
# ================================
# BOT TOKENS & IDENTITY
# ================================
TELEGRAM_TOKEN=${telegram_token}
TELEGRAM_ALLOWED_USER_IDS=${telegram_users}
TASK_CONFIRMATION_MODE=smart

# ================================
# PROJECT & PATHS
# ================================
BASE_DIR=${base_dir}
PROJECT_CONFIG_PATH=config/project_config.yaml
NEXUS_RUNTIME_DIR=./data
LOGS_DIR=./logs

# ================================
# GIT PLATFORMS
# ================================
EOF

if [ -n "$github_token" ]; then
    echo "GITHUB_TOKEN=$github_token" >> "$ENV_FILE"
elif [ -n "$gitlab_token" ]; then
    echo "GITLAB_TOKEN=$gitlab_token" >> "$ENV_FILE"
    echo "GITLAB_BASE_URL=$gitlab_url" >> "$ENV_FILE"
fi

echo -e "\n# ================================" >> "$ENV_FILE"
echo "# INFRASTRUCTURE / STORAGE" >> "$ENV_FILE"
echo "# ================================" >> "$ENV_FILE"

if [ "$is_enterprise" -eq 1 ]; then
    echo "NEXUS_STORAGE_BACKEND=postgres" >> "$ENV_FILE"
    echo "NEXUS_HOST_STATE_BACKEND=postgres" >> "$ENV_FILE"
    if [ "$use_docker" -eq 1 ]; then
        echo "NEXUS_STORAGE_DSN=postgresql://nexus:nexus@127.0.0.1:5432/nexus" >> "$ENV_FILE"
        echo "REDIS_URL=redis://localhost:6379/0" >> "$ENV_FILE"
        echo "DEPLOY_TYPE=compose" >> "$ENV_FILE"
    else
        pg_dsn=$(prompt_string "Enter PostgreSQL DSN" "postgresql://nexus:nexus@127.0.0.1:5432/nexus")
        redis_url=$(prompt_string "Enter Redis URL" "redis://localhost:6379/0")
        echo "NEXUS_STORAGE_DSN=${pg_dsn}" >> "$ENV_FILE"
        echo "REDIS_URL=${redis_url}" >> "$ENV_FILE"
        echo "DEPLOY_TYPE=systemd" >> "$ENV_FILE"
    fi
else
    echo "NEXUS_STORAGE_BACKEND=filesystem" >> "$ENV_FILE"
    echo "# REDIS_URL=" >> "$ENV_FILE"
    echo "DEPLOY_TYPE=standalone" >> "$ENV_FILE"
fi

# Project Config Scaffold
CONFIG_DIR="${BOT_DIR}/config"
mkdir -p "$CONFIG_DIR"
PROJECT_CONFIG="${CONFIG_DIR}/project_config.yaml"
if [ ! -f "$PROJECT_CONFIG" ]; then
    echo "Creating basic project_config.yaml..."
    cat <<EOF > "$PROJECT_CONFIG"
projects:
  example:
    workspace: example-workspace
EOF
fi

# 4. Agent CLI Tools
echo -e "\n--- 4. Agent CLI Tools ---"
echo "Which CLI tools do you want to install? Note: Copilot and Gemini require 'npm'."
cli_choices=("GitHub CLI" "GitLab CLI" "Copilot CLI" "Gemini CLI" "Ollama")
selected_clis=$(prompt_multi_choice "Select tools to install" "${cli_choices[@]}")

install_gh=0
install_glab=0
install_copilot=0
install_gemini=0
install_ollama=0

for s in $selected_clis; do
    case $s in
        1) install_gh=1 ;;
        2) install_glab=1 ;;
        3) install_copilot=1 ;;
        4) install_gemini=1 ;;
        5) install_ollama=1 ;;
    esac
done

if [ -n "$selected_clis" ]; then
    echo -e "\n--- Installing Agent CLI Tools ---"
    
    if [ "$install_copilot" -eq 1 ] || [ "$install_gemini" -eq 1 ]; then
        if ! command -v npm &> /dev/null; then
            echo "⚠️ 'npm' is not installed. Skipping Copilot/Gemini installation."
        else
            npm_packages=""
            [ "$install_copilot" -eq 1 ] && npm_packages="$npm_packages @github/copilot"
            [ "$install_gemini" -eq 1 ] && npm_packages="$npm_packages @google/gemini-cli"
            npm install -g $npm_packages
            echo "✅ NPM packages installed."
        fi
    fi

    OS=$(uname -s)
    if [ "$OS" == "Darwin" ]; then
        brew_packages=""
        [ "$install_gh" -eq 1 ] && brew_packages="$brew_packages gh"
        [ "$install_glab" -eq 1 ] && brew_packages="$brew_packages glab"
        [ "$install_ollama" -eq 1 ] && brew_packages="$brew_packages ollama"

        if [ -n "$brew_packages" ]; then
            if ! command -v brew &> /dev/null; then
                echo "⚠️ Homebrew not found. Skipping $brew_packages"
            else
                brew install $brew_packages
                echo "✅ Installed via brew: $brew_packages"
            fi
        fi
    elif [ "$OS" == "Linux" ]; then
        if [ "$install_gh" -eq 1 ]; then
            if command -v apt &> /dev/null; then
                curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
                sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
                echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null
                sudo apt update && sudo apt install -y gh
                echo "✅ Installed gh"
            else
                echo "⚠️ apt not found, skipping gh install"
            fi
        fi
        
        if [ "$install_glab" -eq 1 ]; then
            curl -sL https://j.mp/glab-cli | sudo sh
            echo "✅ Installed glab"
        fi

        if [ "$install_ollama" -eq 1 ]; then
            curl -fsSL https://ollama.com/install.sh | sh
            echo "✅ Installed ollama"
        fi
    fi
fi

# 5. Infrastructure
if [ "$setup_db" -eq 1 ]; then
    echo -e "\n--- 5. Installing Infrastructure Components ---"
    if [ "$use_docker" -eq 1 ]; then
        if ! command -v docker &> /dev/null; then
            echo "[ERROR] Docker not found."
        else
            if [ -f "docker-compose.yml" ]; then
                docker compose up -d
                echo "✅ Docker components started."
            else
                echo "⚠️ docker-compose.yml not found in $(pwd)."
            fi
        fi
    else
        OS=$(uname -s)
        if [ "$OS" == "Darwin" ]; then
            if command -v brew &> /dev/null; then
                brew install postgresql@15 redis
                brew services start postgresql@15
                brew services start redis
                echo "✅ DBs installed via Brew."
            fi
        elif [ "$OS" == "Linux" ]; then
            if command -v apt &> /dev/null; then
                sudo apt update && sudo apt install -y postgresql redis-server
                sudo systemctl enable --now redis-server
                sudo -u postgres createuser nexus --pwprompt
                sudo -u postgres createdb nexus --owner=nexus
                echo "✅ DBs installed via APT."
            fi
        fi
    fi
fi

echo -e "\n======================================="
echo " 🎉 Installation Complete! 🎉"
echo "======================================="
echo -e "\nNext steps:"
echo " 1. Review the generated .env file"
echo " 2. Review config/project_config.yaml"
echo " 3. pip install -e ."
echo " 4. Run nexus-bot"
