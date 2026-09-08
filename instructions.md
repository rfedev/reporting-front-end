# Reporting Front End

## Description:
I want to create a basic desktop python application. Use a .venv with python 3.13. I'd like to stick to using as few packages as possible. Stick to only native python packages and very popular packages. Make it clear what packages are required as I need to confirm they are available to me on my work laptop (which has a limited pip repo).
Create a requirements.txt file for all the package dependancies.

Use a proper architecture for the Presentation Layer, Application / Controller Layer and Persistence Layer.

For the Process flow editor, create a maintainable, decoupled architecture for the desktop application using PySide6, NodeGraphQt, and SQLAlchemy separates the user interface, application state management, and persistence layer.

## Working directory:
The working directory folder shows the application where the 'reports' are. Each top level folder in this directory is the name of a report.

## Main Interface:
The main interface will be simple, with a dropdown box called 'Reports'. This will hold a list of all the 'Reports' that are available.

When a report is selected, the following controls will become available in their own sections:
* A dropdown of all 'Process Flows' within the currently selected report. There will be an 'Edit Process Flow' and 'Run Process Flow' button in this section (run button currently not implemented and will be greyed out).
* A dropdown of all 'Queries' within the currently selected report. There will be an 'Edit Query' and 'Run Query' button in this section (run button currently not implemented and will be greyed out)


The default for the 'Process Flows' dropdown is 'All Queries'. When 'All Queries' is selected, all queries will show in the 'Queries' dropdown box.
When a process flow is selected, only the queries used within that process flow are shown.


## Queries
The 'queries' are all the .sql files withing the ./<selected-report-name>/code/ folder.
Clicking the 'Edit' button for a query will open the .sql file in it's OS default application.
Queries can have 'Query Parameters'. 

When clicking the 'Run Query' button a dialog window will appear showing all the parameters available for the query and their default values. The default values can be changed. There will be a date picker next to each parameter to allow picking a date. The parameters will not be constrained to a date format. If a date is picked, it will be entered in the format YYYY-MM-DD.

## Query Parameters
Query parameters are key/value pairs.
'Query Parameters' names will be any text in the query .sql file that is wraped in curly-brackets eg {parameter-name}.
The 'Query Parameters' will have default values saved at a query and process flow level.
The available for a query will be cached and updated if the query file is changed. 
In a process flow, only unique 'Query Parameters' will be used: eg if there are 4 queries in the process flow and they all have the query parameter {repDate}, only one 'repDate' parameter needs set (and all queries will use it if appropriate).


## Tables
Tables are the input and output tables used in a query, taken from the bigQuery sql.
They will be shown as inputs to and from the query nodes in the 'Process Flow Editor'.

## Process Flow
The 'Process Flows' are stored at a report level.
The process flow contains a run order of 'queries' which can branch off and converge etc and can be edited from the 'Process Flow Editor'. The queries can have parameters which will be held as an array of key/value pairs.
Clicking the 'Edit' button for a process flow will open up the 'Process Flow Editor'.
The structure of the process flow is to be determined.
The process flow will also store default values for all the 'Query Parameters' of all the queries within it. 




## Process Flow Editor
The 'Process Flow Editor' is an interface with tables, and query nodes that can be connected together with directional noodles to create a process flow.
Input and output tables to query nodes are created automatically based on the query sql. They will be represented by a box with the table names in a vertical list of text.

Main pannel: A canvas for the tables, and query nodes. The query nodes can be connected together with noodles to create a run order. They will have connections on the left and top of the node box for input connections, and connections on the right and bottom for output connections. 

There should be a method for removing nodes fromt the canvas. 
There should be a method for removing connection noodles.
Nodes and table lists can be clicked and dragged around the canvas.
Double clicking on a query node will open it with the OS default application.



Left pannel: 
Can be expanded and collapsed.
For creating and managing queries for this report: (Add, remove, rename). Removal will confirmed with a yes/no confirm box.
Queries can be clicked and dragged into the main pannel to add it as a node.

Right pannel: currently unused.





## Settings:
In the bottom left of the main interface, there is a cog button for settings. This will open a settings dialog window.
There will be a 'Working Directory' text box and folder picker to select the working directory for the app.
A tickbox for setting auto-scan on/off. If this is off, a manual sync button will be shown on the main interface that will initiate a scan for the 'reports', 'process flows' and 'queries'.


## Functions
There should be a versitile function for scanning and retreiving 'reports', 'process flows' and 'queries'. It should cache the results for the app to use in the dropdowns (so the user doesn't need to wait on a scan everytime a dropdown is opened.). Ideally, the app will monitor the 'reports' folders and 'process flows' and 'queries' files and update periodically. I'd like a clean, efficient and industry standard way of doing this that isn't resource intensive. There will be a setting in the settings dialog window to turn auto-scanning on/off.

There will be a function to scan a .sql file for 'Query Parameters'.

There will be a function to determine the input and output tables from a query. The queries are using bigQuery sql and therefore the input tables will be taken from the 'FROM' statement, and the output tables from the 'CREATE TABLE' statement.
