# Instructions

## Process flow editor:
For the process flow editor title, have the window title be 'Process Flow - <report-name> - <process-flow-name> 

Introduce a right click menu for the left hand panel when right clicking on a query. Put 'Add', 'Delete' and 'Rename' as options in the menu to delete or rename the query or add a new one. Use the existing functionality to process these requests.


Add a expandable/collapsable list to the top left section of the canvas.
Populate it with a list of the parameters used in the process flow and allow the user to enter values for them. If a parameter name has the word 'date' (not case-sensitive) then add a drop down list with the following options:
pick date
prev week
prev month
prev quarter
prev year
prev half year


'pick date' is the default but the last selected option will be remembered.
if 'pick date' is selected, add a date picker to allow the user to pick a date which will be in the format YYYY-MM-DD

This will replace the parameter dialog that appears when running from the process flow editor.

Update the parameter dialog that is used when running a process flow from the main window. Update it to have the same functionality for date parameters.

There should not be a minimum width of the left panel. It's size should be remembered when reopening process flows.



