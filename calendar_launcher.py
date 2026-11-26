
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "main.py"


def run_calendar(*args):
    """Run the main Calendar application."""
    command = [sys.executable, str(MAIN), *args]

    try:
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode != 0:
            print(f"\nCalendar exited with code {result.returncode}.")
        return result.returncode
    except Exception as error:
        print(f"\nFailed to start Calendar: {error}")
        return 1


def run_cli(debug=False):
    print()
    print("Enter CLI command (e.g., --schedule --from today):")

    user_input = input(" > ").strip()
    if not user_input:
        return

    try:
        args = shlex.split(user_input)
    except ValueError as error:
        print(f"Invalid command: {error}")
        return

    if debug:
        args.insert(0, "--debug")

    run_calendar(*args)
    input("\nPress Enter to continue...")


def show_menu():
    while True:
        print("\n==============================")
        print("         Calendar Tools")
        print("==============================")
        print("1. Open Calendar GUI")
        print("2. Run CLI Command (Normal)")
        print("3. Run CLI Command (Debug Mode)")
        print("4. Show CLI help")
        print("5. Open Timetable Editor (use this)")
        print("0. Exit")
        print()

        choice = input("Choose an option: ").strip()

        if choice == "1":
            run_calendar("gui")

        elif choice == "2":
            run_cli()

        elif choice == "3":
            run_cli(debug=True)

        elif choice == "4":
            run_calendar("--help")
            input("\nPress Enter to continue...")

        elif choice == "5":
            run_calendar("group-schedule")

        elif choice == "0":
            print("Goodbye.")
            break

        else:
            print("Invalid choice.")


def main():
    if not MAIN.exists():
        print(f"Error: main.py was not found at {MAIN}")
        return 1

    # Support direct commands, e.g.:
    # python calendar_launcher.py gui
    if len(sys.argv) > 1:
        return run_calendar(*sys.argv[1:])

    try:
        show_menu()
    except KeyboardInterrupt:
        print("\nExiting Calendar.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
