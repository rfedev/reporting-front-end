-- PSD Query 01: Extract PSD Transactions
CREATE OR REPLACE TABLE `psd_reporting.stage_transactions` AS
SELECT
    t.transaction_id,
    t.account_number,
    t.amount,
    t.currency,
    t.trans_date
FROM `banking_core.transactions` AS t
WHERE t.trans_date BETWEEN '{startDate}' AND '{endDate}';
