-- Query: q3
# output: table_02.csv
create or replace table `table3` as
    SELECT 1 as a
    from `table1` as t1
        left join `table2` t2 on (t1.a = t2.a);
