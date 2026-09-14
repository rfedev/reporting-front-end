-- Query: test
#comment
# output: sel1.csv
SELECT '{repdate}' as reporting_date, 1 as num;



# output: table_02.csv
# create or replace table `link-to-cloud.test_dataset.tablename` as (select * from `link-to-cloud.test_dataset.test-import`);

# output: sel2.csv
select * from `link-to-cloud.test_dataset.test-import-202608`;


# output: sel3.csv
select * from `link-to-cloud.test_dataset.test-import`;


