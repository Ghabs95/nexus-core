#!/usr/bin/env python3
import os
import sys
import subprocess
from pathlib import Path
import shutil

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def prompt_choice(question: str, choices: list[str]) -> int:
    while True:
        print(f"\n{question}")
        for i, choice in enumerate(choices, 1):
            print(f"  {i}) {choice}")
        try:
            val = int(input("\nSelect an option: "))
            if 1 <= val <= len(choices):
                return val
            print(f"Please enter a number between 1 and {len(choices)}.")
        except ValueError:
            print("Please enter a valid number.")

def prompt_multi_choice(question: str, choices: list[str]) -> list[int]:
    while True:
        print(f"\n{question}")
        for i, choice in enumerate(choices, 1):
            print(f"  {i}) {choice}")
        try:
            val = input("\nSelect options separated by commas (e.g. 1, 3) or press Enter to skip: ").strip()
            if not val:
                return []
            
            selected = [int(x.strip()) for x in val.split(',')]
            if all(1 <= x <= len(choices) for x in selected):
                return selected
            print(f"Please enter valid numbers between 1 and {len(choices)}.")
        except ValueError:
            print("Please enter valid comma-separated numbers.")
    return []

def prompt_string(question: str, default: str = "") -> str:
    prompt = f"{question} [{default}]: " if default else f"{question}: "
    val = input(prompt).strip()
    return val if val else default

def run_command(cmd: list[str], shell: bool = False):
    print(f"\nRunning: {' '.join(cmd) if not shell else cmd}")
    try:
        subprocess.run(cmd, shell=shell, check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] Command failed: {e}")
        sys.exit(1)

def main():
    clear_screen()
    print("=======================================")
    print(" 🚀 Welcome to Nexus ARC Installation 🚀")
    print("=======================================\n")
    
    bot_dir = Path(__file__).resolve().parent
    env_file = bot_dir / ".env"
    
    if env_file.exists():
        replace = prompt_choice("An existing .env file was found. Overwrite?", ["No, keep it", "Yes, overwrite"])
        if replace == 1:
            print("Installation aborted to preserve .env.")
            return

    # 1. Deployment Mode
    print("\n--- 1. Deployment Mode ---")
    mode_choice = prompt_choice(
        "Which storage mode do you want to use?",
        [
            "Lite (Filesystem only - no external dependencies, great for local testing)",
            "Enterprise (PostgreSQL + Redis - persistent queue, chat memory, deduplication)"
        ]
    )
    is_enterprise = (mode_choice == 2)

    # 2. Infrastructure
    setup_db = False
    use_docker = False
    
    if is_enterprise:
        print("\n--- 2. Infrastructure Setup ---")
        infra_choice = prompt_choice(
            "How do you want to run PostgreSQL and Redis?",
            [
                "Docker Compose (Sandboxed, highly recommended)",
                "System packages (e.g. brew or apt)",
                "I already have them running (Skip installation)"
            ]
        )
        if infra_choice == 1:
            use_docker = True
            setup_db = True
        elif infra_choice == 2:
            setup_db = True

    # 3. Environment Variables
    print("\n--- 3. Credentials & Keys ---")
    telegram_token = prompt_string("Enter your Telegram Bot Token")
    telegram_users = prompt_string("Enter your Telegram User ID (or comma-separated IDs)")
    
    vcs_choice = prompt_choice(
        "Which VCS platform will you be using primarily?",
        ["GitHub", "GitLab"]
    )
    
    github_token = ""
    gitlab_token = ""
    gitlab_url = ""
    
    if vcs_choice == 1:
        github_token = prompt_string("Enter your GitHub Personal Access Token")
    else:
        gitlab_token = prompt_string("Enter your GitLab Personal Access Token (glpat-...)", "")
        gitlab_url = prompt_string("Enter your GitLab Base URL", "https://gitlab.com")

    base_dir = prompt_string("Enter your workspaces base directory (where your git clones live)", str(Path.home() / "git"))
    
    # Generate .env content
    env_content = f"""# ================================
# BOT TOKENS & IDENTITY
# ================================
TELEGRAM_TOKEN={telegram_token}
TELEGRAM_ALLOWED_USER_IDS={telegram_users}
TASK_CONFIRMATION_MODE=smart

# ================================
# PROJECT & PATHS
# ================================
BASE_DIR={base_dir}
PROJECT_CONFIG_PATH=config/project_config.yaml
NEXUS_RUNTIME_DIR=./data
LOGS_DIR=./logs

# ================================
# GIT PLATFORMS
# ================================
"""
    if github_token:
        env_content += f"GITHUB_TOKEN={github_token}\n"
    elif gitlab_token:
        env_content += f"GITLAB_TOKEN={gitlab_token}\nGITLAB_BASE_URL={gitlab_url}\n"

    # Storage section
    env_content += "\n# ================================\n# INFRASTRUCTURE / STORAGE\n# ================================\n"
    if is_enterprise:
        env_content += "NEXUS_STORAGE_BACKEND=postgres\n"
        env_content += "NEXUS_HOST_STATE_BACKEND=postgres\n"
        if use_docker:
            env_content += "NEXUS_STORAGE_DSN=postgresql://nexus:nexus@127.0.0.1:5432/nexus\n"
            env_content += "REDIS_URL=redis://localhost:6379/0\n"
            env_content += "DEPLOY_TYPE=compose\n"
        else:
            pg_dsn = prompt_string("Enter PostgreSQL DSN", "postgresql://nexus:nexus@127.0.0.1:5432/nexus")
            redis_url = prompt_string("Enter Redis URL", "redis://localhost:6379/0")
            env_content += f"NEXUS_STORAGE_DSN={pg_dsn}\n"
            env_content += f"REDIS_URL={redis_url}\n"
            env_content += "DEPLOY_TYPE=systemd\n"
    else:
        env_content += "NEXUS_STORAGE_BACKEND=filesystem\n"
        env_content += "# REDIS_URL=\n"
        env_content += "DEPLOY_TYPE=standalone\n"

    # Write .env
    print("\nWriting .env file...")
    env_file.write_text(env_content)
    
    # Copy project config if it doesn't exist
    config_dir = bot_dir / "config"
    config_dir.mkdir(exist_ok=True)
    project_config = config_dir / "project_config.yaml"
    
    if not project_config.exists():
        example_config = bot_dir.parent / "project_config.yaml"
        if example_config.exists():
            print("Copying example project_config.yaml...")
            shutil.copy2(example_config, project_config)
        else:
            print("Creating basic project_config.yaml...")
            project_config.write_text("projects:\n  example:\n    workspace: example-workspace\n")

    # 4. CLI Tools Setup
    print("\n--- 4. Agent CLI Tools ---")
    print("Which CLI tools do you want to automatically install?")
    print("Note: Copilot and Gemini require 'npm' to be installed.")
    
    cli_options = [
        "GitHub CLI (gh)",
        "GitLab CLI (glab)",
        "GitHub Copilot CLI",
        "Google Gemini CLI",
        "Ollama"
    ]
    selected_clis = prompt_multi_choice("Select tools to install", cli_options)
    
    if selected_clis:
        print("\n--- Installing Agent CLI Tools ---")
        
        install_gh = 1 in selected_clis
        install_glab = 2 in selected_clis
        install_copilot = 3 in selected_clis
        install_gemini = 4 in selected_clis
        install_ollama = 5 in selected_clis

        # NPM-based installs
        npm_packages = []
        if install_copilot: npm_packages.append("@github/copilot")
        if install_gemini: npm_packages.append("@google/gemini-cli")
        
        if npm_packages:
            if not shutil.which("npm"):
                print("⚠️ 'npm' is not installed. Skipping Copilot/Gemini CLI installation.")
            else:
                run_command(["npm", "install", "-g"] + npm_packages, shell=sys.platform == "nt")
                print(f"✅ NPM packages installed: {' '.join(npm_packages)}")

        # System-based installs
        if sys.platform == "darwin":
            brew_packages = []
            if install_gh: brew_packages.append("gh")
            if install_glab: brew_packages.append("glab")
            if install_ollama: brew_packages.append("ollama")
            
            if brew_packages:
                if shutil.which("brew"):
                    run_command(["brew", "install"] + brew_packages)
                    print(f"✅ Installed via Homebrew: {' '.join(brew_packages)}")
                else:
                    print(f"⚠️ Homebrew not found. Skipping installation of: {' '.join(brew_packages)}")
        elif sys.platform.startswith("linux"):
            if install_gh:
                if shutil.which("apt"):
                    print("Installing GitHub CLI via apt...")
                    run_command(["curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | sudo dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg"], shell=True)
                    run_command(["sudo chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg"], shell=True)
                    run_command(["echo \"deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main\" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null"], shell=True)
                    run_command(["sudo apt update && sudo apt install -y gh"], shell=True)
                    print("✅ GitHub CLI installed.")
                else:
                    print("⚠️ Only 'apt' is supported for automated Linux install of 'gh'.")
            
            if install_glab:
                print("Installing GitLab CLI...")
                run_command(["curl -sL https://j.mp/glab-cli | sudo sh"], shell=True)
                print("✅ GitLab CLI installed.")
                
            if install_ollama:
                print("Installing Ollama...")
                run_command(["curl -fsSL https://ollama.com/install.sh | sh"], shell=True)
                print("✅ Ollama installed.")

    # Perform Infra Setup if requested
    if setup_db:
        print("\n--- 5. Installing Infrastructure Components ---")
        if use_docker:
            if not shutil.which("docker"):
                print("[ERROR] Docker is not installed. Please install Docker and run `docker compose up -d` manually.")
            else:
                compose_file = bot_dir / "docker-compose.yml"
                if compose_file.exists():
                    try:
                        run_command(["docker", "compose", "up", "-d"])
                        print("✅ Docker components started successfully.")
                    except SystemExit:
                        print("⚠️ Failed to start Docker Compose. Please check the logs.")
                else:
                    print("⚠️ docker-compose.yml not found in this directory. Skipping.")
        else:
            if sys.platform == "darwin":
                if not shutil.which("brew"):
                    print("[ERROR] Homebrew not found. Skipping system package installation.")
                else:
                    run_command(["brew", "install", "postgresql@15", "redis"])
                    run_command(["brew", "services", "start", "postgresql@15"])
                    run_command(["brew", "services", "start", "redis"])
                    print("✅ PostgreSQL and Redis installed and started via Homebrew.")
                    print("⚠️ Note: You may need to create the 'nexus' database user manually:")
                    print("   createuser -s postgres")
                    print("   psql -U postgres -c \\"CREATE USER nexus WITH PASSWORD 'nexus';\\"")
                    print("   psql -U postgres -c \\"CREATE DATABASE nexus OWNER nexus;\\"")
            elif sys.platform.startswith("linux"):
                if shutil.which("apt"):
                    print("This requires sudo access to install PostgreSQL and Redis.")
                    run_command(["sudo", "apt", "update"])
                    run_command(["sudo", "apt", "install", "-y", "postgresql", "redis-server"])
                    run_command(["sudo", "systemctl", "enable", "--now", "redis-server"])
                    run_command(["sudo", "-u", "postgres", "createuser", "nexus", "--pwprompt"]) # Will block for prompt
                    run_command(["sudo", "-u", "postgres", "createdb", "nexus", "--owner=nexus"])
                    print("✅ PostgreSQL and Redis installed via apt.")
                else:
                    print("⚠️ Only 'apt' is supported for automated Linux installs right now. Please install postgres and redis manually.")

    print("\n=======================================")
    print(" 🎉 Installation Complete! 🎉")
    print("=======================================")
    print("\nNext steps:")
    print(" 1. Review the generated .env file")
    print(" 2. Review config/project_config.yaml")
    print(" 3. Make sure to pip install the package if you haven't: pip install -e .")
    print(" 4. Start the bot with: nexus-bot\n")

if __name__ == "__main__":
    main()
