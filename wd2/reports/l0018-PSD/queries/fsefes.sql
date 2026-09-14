-- Query: fsefes
# ouput: table_02.csv
SELECT 1;

# imported csv link-to-cloud.test_dataset.test_import_csv
# ouput: table_03.csv
select * from `link-to-cloud.test_dataset.test_import_csv`;

# imported csv link-to-cloud.test_dataset.test_query
create table test as select * from `link-to-cloud.test_dataset.test_query`;
