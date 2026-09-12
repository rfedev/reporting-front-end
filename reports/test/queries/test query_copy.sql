-- Query: test
#comment
# output: sel1.csv
SELECT '{repdate}' as reporting_date, 1 as num;

CREATE OR REPLACE TABLE `my_project.my_dataset.my_table` (
    transaction_id INT64,
    transaction_date DATE,
    amount NUMERIC,
    store_id STRING
);

# output: sel2.csv
select * from `link-to-cloud.test_dataset.test-import-202608`;

# output: sel3.csv
select * from `link-to-cloud.test_dataset.test-import`;


