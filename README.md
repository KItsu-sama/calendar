v0.1

Command	            Purpose
--help	            Display main CLI help.
today	            Show the schedule for the reference date.
week	            Show the schedule for the Monday–Sunday week containing the reference date.
gui or --gui	    Launch the desktop GUI.
--add	            Add an activity to the calendar.
--remove	        Remove an activity and its associated rule.
--change	            Modify an existing activity's settings.
--move	            Move one occurrence of an activity to another weekday.
--cancel	            Cancel one occurrence of an activity.
--exception	        Create a one-off cancellation, move, or time modification.
--schedule	        Display scheduled activities for a date or date range.
--free	            Calculate unoccupied intervals. These are not necessarily genuinely available time.
--conflicts	        Report overlapping scheduled activities, optionally for a proposed activity.
--preview	        Preview adding an activity and see whether it conflicts.
--generate-prompt	Generate an AI prompt for a full schedule review or a conflict-resolution request.
--batch or batch	Import activities from a batch schedule file.
--reset	            Clear the selected calendar file after explicitly specifying --save.

v0.2

Command	            Purpose
--review	            Rebuild derived sections and show what changed since the last run.
--watch	            Rerun --review whenever a source file is saved.
--export-commands FILE	Decompile current state to commands (FILE or stdout).
--profile FILE	        profile.json (protected time, buffers).
--relations FILE	        relations.json (prep/review rules).
--no-snapshot	        Do not update the change snapshot.