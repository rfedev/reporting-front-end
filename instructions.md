

Make the dropdown lists selections persist throughout use of the app. The selection should be remembered, even on reloading the app. 
Remove the 'All Queries' from the process flow menu. If there are no process flow created yet for a report, have a default 'Process Flow 01' created.

Have a way to add and remove reports, process flows and queries in the main page. process flows and queries will have run buttons. Use icons rather than text in the buttons (replace existing buttons that have this functionality). Have tooltips when mousing over the buttons. Have them inline with the report, process flow, query dropdown selectors.

In the settings menu, add a text box for the users default 'Workbench Dataset'. If this has not been set yet, open a dialog window when opening the app with a text box to enter the workbench dataset. It will have the message 'Please enter your team workbench dataset address:'. The text box will be pre-populated with 'iw-gid-prd-01-c683.gid_art_yourworkbenchID'.


When running a query, automatically pass and use the bigquery projectid based off the first 'from' statement table address of the query being run (eg all tables will have this name format 'project-id.dataset_id.table_id').

Allow csv out and output table to exits in one query if there is a create table and select only statements.


Add an 'Import csv' button to the toolbar. Clicking it will add a new type of 'Import Query'. Double clicking it will bring up a dialog window with a text box for the location of the csv file with a file picker button, and a 'Headers' tickbox (all in a horizontal row). Underneath, on a new row, a text box for the output table to create (eg 'project-id.dataset_id.table_id'). The 'Headers' tickbox will determine if the file has headers for use in the load function. Field types should be automatically detected.
There will be at the bottom of the list: '+ Add csv' button to add a new import, to allow multiple csv files. All imports other than the first one, will have a delete button (trash icon).
The 'import csv' box created in the process flow editor will be purple (like the output csv box) but have the header Import csv. It should act in the same way as a normal query node in regards to creating the ouput table box for all the new tables it's created, and they will function in the same way as normal output table boxes.




Add the 'Auto Layout' buttons and 'Fit graph' and 'Full table Address' button to a menu button in the toolbar. The button will have the text 'Appearance'

Often, when saving, loading, running or doing other actions, the layout of the process flow boxes will change. I want them all to keep a persistant position and zoom level.
Sometimes links connecting query nodes are broken when clicking the 'Auto Layout' buttons or running a process. Make sure these links persist.

Make sure there is a robust process for scanning the various node files to determine what input/output tables should be shown and what connections should be made. 


Allow running single or multiple queries in each query node. Make sure it's possible to have both an output table and a output csv table from one query node.


Allow deselection of everything in the process flow editor by clicking once on empty space.

The create table queries are not working (they are giving an error and the table is not created in the google cloud repo)

Allow double clicking the queries in the left panel list of the process flow editor to open them.
