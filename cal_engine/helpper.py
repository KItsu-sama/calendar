# it ask for what you want to do and then it will give you the option to choose from a list of options.
# so you don't have to remember the commands and options. It will also give you a description of what each command does.

from __future__ import annotations

import subprocess
import sys


class Help:
    def __init__(self) -> None:
        self.python = sys.executable
        self.args: list[str] = []

    def _run(self, args: list[str]) -> int:
        result = subprocess.run([self.python, "main.py", *args])
        return result.returncode

    def assist(self, error: str | None = None) -> int:
        """Interactive helper for CLI parse/usage errors."""
        if error:
            print(f"Error: {error}", file=sys.stderr)
            print(file=sys.stderr)

        print("What do you want to do?")
        print("1. Show command help")
        print("2. View today's schedule")
        print("3. View this week's schedule")
        print("4. creating an activity")
        print("5. changing/removing activities")
        print("6. Exit")

        try:
            choice = input("Enter the number of your choice: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 2

        if choice == "1":
            return self._run(["-h"])
        if choice == "2":
            return self._run(["today"])
        if choice == "3":
            return self._run(["week"])
        if choice == "4":
            return self.create_activity()
        if choice == "5":
            print("Examples:")
            print("  python main.py --load data/calendar.json --change school --time 07:30-12:00")
            print("  python main.py --load data/calendar.json --move math --to sat")
            print("  python main.py --load data/calendar.json --remove lunch")
            return 0
        if choice == "6":
            return 0

        print("Invalid choice.")
        return 2

    def help(self) -> int:
        """Backward-compatible entry point."""
        return self.assist()

    def create_activity(self) -> int:
        """Interactive helper for creating an activity."""
        print("Activity commands must start with a primary flag:")
        print("  --fixed, --todo, --optional, --temp-fixed, --exception")
        print("What type of activity do you want to create?")
        print("1. Fixed activity (scheduled at specific times)")
        print("2. Todo activity (flexible duration, with deadline)")
        print("3. Optional activity (low priority, flexible scheduling)")
        print("4. Temporary fixed activity (one-time fixed schedule)")
        print("5. Exception (cancel or move an occurrence)")
        print("6. Exit")

        try:
            choice = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 2

        if choice == "1":
            return self.build_fixed()
        if choice == "2":
            return self.build_todo()
        if choice == "3":
            return self.build_optional()
        if choice == "4":
            return self.build_temp_fixed()
        if choice == "5":
            return self.build_exception()
        if choice == "6":
            return 0

        print("Invalid choice.")
        return 2

    def build_fixed(self) -> int:
        """Build a --fixed activity command."""
        self.args = ["--fixed"]
        name = input("Enter activity name: ").strip()
        if not name:
            print("Name required.")
            return 2
        self.args.append(name)

        print("Recurrence:")
        print("1. Daily (--daily)")
        print("2. Weekly on specific days (--days mon wed fri)")
        print("3. Once (no recurrence)")
        rec = input("> ").strip()
        if rec == "1":
            self.args.append("--daily")
            except_days = input("Except days (e.g. sat sun), or press Enter to skip: ").strip()
            if except_days:
                self.args.extend(["--except", *except_days.split()])
        elif rec == "2":
            days = input("Enter days (e.g. mon wed fri): ").strip()
            if days:
                self.args.extend(["--days", *days.split()])
            until = input("Until date (D/M, e.g. 31/12), or press Enter: ").strip()
            if until:
                self.args.extend(["--until", until])
        elif rec == "3":
            pass
        else:
            print("Invalid choice.")
            return 2

        time_range = input("Time range (HH:MM-HH:MM, required): ").strip()
        if not time_range:
            print("--time is required for --fixed.")
            return 2
        self.args.extend(["--time", time_range])

        print("Optional modifiers:")
        if input("Changeable? (y/n): ").strip().lower() == "y":
            self.args.append("--changeable")
        if input("Flexible? (y/n): ").strip().lower() == "y":
            self.args.append("--flexible")
        move_to = input("Preferred move-to day (e.g. sat), or press Enter: ").strip()
        if move_to:
            self.args.extend(["--move-to", move_to])
        priority = input("Priority (HIGH/NORMAL/LOW), or press Enter: ").strip().upper()
        if priority in {"HIGH", "NORMAL", "LOW"}:
            self.args.extend(["--priority", priority])

        return self._run(self.args)

    def build_todo(self) -> int:
        """Build a --todo activity command."""
        self.args = ["--todo"]
        name = input("Enter activity name: ").strip()
        if not name:
            print("Name required.")
            return 2
        self.args.append(name)

        print("Duration:")
        print("1. Exact duration (--duration)")
        print("2. Min/max range (--min/--max)")
        dur = input("> ").strip()
        if dur == "1":
            duration = input("Duration (e.g. 1h30m, 90m): ").strip()
            if duration:
                self.args.extend(["--duration", duration])
        elif dur == "2":
            min_dur = input("Min duration (e.g. 30m): ").strip()
            if min_dur:
                self.args.extend(["--min", min_dur])
            max_dur = input("Max duration (e.g. 2h): ").strip()
            if max_dur:
                self.args.extend(["--max", max_dur])
        else:
            print("Invalid choice.")
            return 2

        print("Due date (required):")
        due_day = input("Day (mon/tue/wed/thu/fri/sat/sun): ").strip()
        due_time = input("Time (HH:MM): ").strip()
        if due_day and due_time:
            self.args.extend(["--due", due_day, due_time])
        else:
            print("--due is required for --todo.")
            return 2

        between = input("Allowed time window (--between HH:MM-HH:MM), or press Enter: ").strip()
        if between:
            self.args.extend(["--between", between])

        priority = input("Priority (HIGH/NORMAL/LOW), or press Enter: ").strip().upper()
        if priority in {"HIGH", "NORMAL", "LOW"}:
            self.args.extend(["--priority", priority])

        return self._run(self.args)

    def build_optional(self) -> int:
        """Build an --optional activity command."""
        self.args = ["--optional"]
        name = input("Enter activity name: ").strip()
        if not name:
            print("Name required.")
            return 2
        self.args.append(name)

        print("Recurrence:")
        print("1. Daily (--daily)")
        print("2. Weekly on specific days (--days mon wed fri)")
        print("3. Once (no recurrence)")
        rec = input("> ").strip()
        if rec == "1":
            self.args.append("--daily")
            except_days = input("Except days (e.g. sat sun), or press Enter: ").strip()
            if except_days:
                self.args.extend(["--except", *except_days.split()])
        elif rec == "2":
            days = input("Enter days (e.g. mon wed fri): ").strip()
            if days:
                self.args.extend(["--days", *days.split()])
            until = input("Until date (D/M, e.g. 31/12), or press Enter: ").strip()
            if until:
                self.args.extend(["--until", until])
        elif rec == "3":
            pass
        else:
            print("Invalid choice.")
            return 2

        time_range = input("Time range (HH:MM-HH:MM), or press Enter: ").strip()
        if time_range:
            self.args.extend(["--time", time_range])

        print("Optional duration constraints:")
        print("1. Exact duration (--duration)")
        print("2. Min/max range (--min/--max)")
        dur = input("> ").strip()
        if dur == "1":
            duration = input("Duration (e.g. 1h30m, 90m): ").strip()
            if duration:
                self.args.extend(["--duration", duration])
        elif dur == "2":
            min_dur = input("Min duration (e.g. 30m): ").strip()
            if min_dur:
                self.args.extend(["--min", min_dur])
            max_dur = input("Max duration (e.g. 2h): ").strip()
            if max_dur:
                self.args.extend(["--max", max_dur])

        between = input("Allowed time window (--between HH:MM-HH:MM), or press Enter: ").strip()
        if between:
            self.args.extend(["--between", between])

        due_day = input("Due day (mon/tue/...), or press Enter: ").strip()
        due_time = input("Due time (HH:MM), or press Enter: ").strip()
        if due_day and due_time:
            self.args.extend(["--due", due_day, due_time])

        return self._run(self.args)

    def build_temp_fixed(self) -> int:
        """Build a --temp-fixed activity command."""
        self.args = ["--temp-fixed"]
        name = input("Enter activity name: ").strip()
        if not name:
            print("Name required.")
            return 2
        self.args.append(name)

        date_str = input("Date (D/M, e.g. 28/9, required): ").strip()
        if not date_str:
            print("--date is required for --temp-fixed.")
            return 2
        self.args.extend(["--date", date_str])

        time_range = input("Time range (HH:MM-HH:MM, required): ").strip()
        if not time_range:
            print("--time is required for --temp-fixed.")
            return 2
        self.args.extend(["--time", time_range])

        except_days = input("Except days (e.g. sat sun), or press Enter: ").strip()
        if except_days:
            self.args.extend(["--except", *except_days.split()])

        print("Optional modifiers:")
        if input("Changeable? (y/n): ").strip().lower() == "y":
            self.args.append("--changeable")
        if input("Flexible? (y/n): ").strip().lower() == "y":
            self.args.append("--flexible")
        move_to = input("Preferred move-to day (e.g. sat), or press Enter: ").strip()
        if move_to:
            self.args.extend(["--move-to", move_to])
        priority = input("Priority (HIGH/NORMAL/LOW), or press Enter: ").strip().upper()
        if priority in {"HIGH", "NORMAL", "LOW"}:
            self.args.extend(["--priority", priority])

        return self._run(self.args)

    def build_exception(self) -> int:
        """Build an --exception activity command."""
        self.args = ["--exception"]
        name = input("Enter activity name: ").strip()
        if not name:
            print("Name required.")
            return 2
        self.args.append(name)

        date_str = input("Date of occurrence (D/M, e.g. 28/9, required): ").strip()
        if not date_str:
            print("--date is required for --exception.")
            return 2
        self.args.extend(["--date", date_str])

        print("Action:")
        print("1. Cancel this occurrence (--cancel)")
        print("2. Move to another day (--move)")
        action = input("> ").strip()
        if action == "1":
            self.args.append("--cancel")
        elif action == "2":
            move_day = input("Move to day (mon/tue/wed/thu/fri/sat/sun): ").strip()
            if not move_day:
                print("--move requires a day.")
                return 2
            self.args.extend(["--move", move_day])
            time_range = input("New time range (HH:MM-HH:MM), or press Enter: ").strip()
            if time_range:
                self.args.extend(["--time", time_range])
        else:
            print("Invalid choice.")
            return 2

        return self._run(self.args)