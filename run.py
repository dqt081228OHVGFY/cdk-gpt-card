from pathlib import Path
import subprocess


if __name__ == "__main__":
    subprocess.run([str(Path(__file__).with_name("start-background.bat"))], check=False)
