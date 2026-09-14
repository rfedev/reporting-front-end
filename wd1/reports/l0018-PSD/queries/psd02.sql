-- PSD Query 02: Generate PSD Summary
CREATE OR REPLACE TABLE `psd_reporting.summary_report` AS
SELECT
    st.currency,
    COUNT(st.transaction_id) AS total_count,
    SUM(st.amount) AS total_volume
FROM `psd_reporting.stage_transactions` AS st
WHERE st.amount > 0
GROUP BY st.currency;
