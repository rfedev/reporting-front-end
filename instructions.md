# Instructions

## Process flow editor:

When creating a new report, it is created in the working directory. When it's created, also make folders 'queries', 'outputs' and 'inputs' inside the report folder.

The drag query from from left panel now works for queries that exist when the process flow editor is opened. However, if a query has just been added via the '+ Add' button in the left panel (without exiting the process flow editor), when dragging it in, it puts it in the wrong place.

When a query node has a select statement, it creates an ouput csv box. 

The delete key is no longer working. Revert it to how it used to work before the last change that intoduced deleting the tables in the side panel. I no longer want it to delete tables from the side pannel, only selected querie nodes on the canvas.

Introduce a right click menu for the left hand panel when right clicking on a query. Put 'Add', 'Delete' and 'Rename' as options in the menu to delete or rename the query or add a new one. Use the existing functionality to process these requests.

Make the default state of the 'Full Table Address' toggle be un-checked for new process flows. The state will be remembered if changed.

When a query node creates an ouput csv table, it will show the filename.csv. If the query node creates multiple ouput csv files, list them all in the Ouput CSV box. The default file names will be table_01.csv, table_02.csv etc. Make sure to increment the numbers if a table in the report already exists with that name. eg is table_05.csv already exists, start at table_06.csv.

On the 'Output CSV Filenames' dialog, change the text boxes to be labled 'Query01', 'Query02' etc.
The files are always saved to the 'outputs' folder of the report. Remove the abitily to pass a full path to store it elsewhere. Remove the file picker buttons. If the .csv extention is missing when 'update' button is clicked, add it in automatically.

If the line directly before the select statement to create an ouptut csv, has the comment: # ouput: tablename.csv
Use this 'tablename.csv' as the default name for the ouput csv. Be able to handle with/without spaces and the word 'ouput' is not case-sensitive.
Syncronise this comment between the query and the ouput table box. If the comment doesn't exist, add a new line above the select statement and add it in.



