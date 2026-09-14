# Instructions



## Multiple working directories
Be able to add multiple working directories in the settings window. Each working directory can be given an alias. 
When creating a new report in the main window, in the 'Add Report' dialog where the user specifies the report name, add a dropdown with the working directory aliases to choose which working directory the report wil be created in. 
Wehen selecting a report in the main window, the dropdown list will show all reports from all working directories.
Make sure that all downstream effects of this change are handled. If you have any queries about how to handle it, let me know.



## Process flow editor:

### Imports
When importing a csv, make the 'browse for CSV file..' button is square.
Rename the 'Import csv' to 'Import Files' as I want it to be able to import csvs and xlsx files.


### Excel file import
Also, add the ability to select xlsx files for import too. Implement the functionality to import xlsx files into a project dataset if an xlsx file is selected.

### Manage schemas
Implement a method to either use an auto-schema, or manually set one using
Currently the schema is auto-identified. Add an 'Auto-Schema' tickbox for this (below the 'Headers' tickbox). If either the 'Auto-Schema' of 'Headers' tick boxes are not ticked, there will be a button appear (icon is a right facing arrow with the tooltip 'Schema details'). This button will open up a dialog window for editing the headers and field types. It will have 2 columns: 'Field Name' and 'Field Type'. If the 'headers' tickbox is ticked, the field names will be populated by the first row of the file.


