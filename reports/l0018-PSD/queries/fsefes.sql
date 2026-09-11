-- Query: fsefes
SELECT 1;

# imported csv link-to-cloud.test_dataset.test_import_csv
select * from `link-to-cloud.test_dataset.test_import_csv`;

# imported csv link-to-cloud.test_dataset.test_query
create table test as select * from `link-to-cloud.test_dataset.test_query`;
